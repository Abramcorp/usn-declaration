"""
Data import endpoints for Russian tax declaration system.
Handles import of bank statements and OFD receipts.
"""

import os
import shutil
from datetime import datetime
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, BankOperation, OfdReceipt, AuditLog
from app.services.parser import BankStatementParser
from app.services.classifier import OperationClassifier
from app.services.ofd_parser import parse_ofd_xlsx

router = APIRouter(prefix="/api/import", tags=["import"])


class ImportSummary:
    """Summary of import operation."""
    def __init__(self):
        self.total_parsed = 0
        self.total_saved = 0
        self.income_count = 0
        self.not_income_count = 0
        self.disputed_count = 0
        self.income_amount = Decimal("0")
        self.not_income_amount = Decimal("0")
        self.disputed_amount = Decimal("0")
        self.errors = []


def _ensure_uploads_directory():
    """Ensure uploads directory exists."""
    uploads_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    return uploads_dir


def _log_audit(
    db: Session,
    project_id: int,
    action: str,
    entity_type: str,
    new_value: Optional[str] = None,
):
    """Helper function to log audit events."""
    log_entry = AuditLog(
        project_id=project_id,
        action=action,
        entity_type=entity_type,
        new_value=new_value,
    )
    db.add(log_entry)
    db.commit()


@router.post("/bank-statement/{project_id}")
async def import_bank_statement(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Import bank statement file, parse operations, and classify them.

    Args:
        project_id: ID of the project
        file: Bank statement file (.txt format)

    Returns:
        Import summary with counts by classification.

    Raises:
        HTTPException: If project not found or file processing fails.
    """
    # Check project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Validate file extension
    if not file.filename.endswith('.txt'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Поддерживаются только файлы .txt"
        )

    uploads_dir = _ensure_uploads_directory()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_name = f"{project_id}_{timestamp}_{file.filename}"
    file_path = os.path.join(uploads_dir, file_name)

    try:
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Parse bank statement
        parser = BankStatementParser()
        parse_result = parser.parse(file_path)

        if not parse_result or "operations" not in parse_result:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Не удалось распарсить файл банковской выписки"
            )

        operations = parse_result.get("operations", [])
        summary = ImportSummary()
        summary.total_parsed = len(operations)

        # Initialize classifier
        classifier = OperationClassifier(project_id, db)

        # Prepare operations for batch classification
        ops_to_classify = []
        for op in operations:
            ops_to_classify.append({
                "amount": Decimal(str(op.get("amount", 0))),
                "direction": op.get("direction", "income"),
                "purpose": op.get("purpose", ""),
                "counterparty": op.get("counterparty", ""),
                "counterparty_inn": op.get("counterparty_inn", ""),
            })

        # Classify in batch
        classifications = classifier.classify_batch(ops_to_classify)

        # Save operations to database
        for idx, op in enumerate(operations):
            classification_result = classifications[idx] if idx < len(classifications) else {}

            bank_op = BankOperation(
                project_id=project_id,
                operation_date=op.get("operation_date"),
                posting_date=op.get("posting_date"),
                amount=Decimal(str(op.get("amount", 0))),
                direction=op.get("direction", "income"),
                purpose=op.get("purpose", ""),
                counterparty=op.get("counterparty", ""),
                counterparty_inn=op.get("counterparty_inn", ""),
                counterparty_account=op.get("counterparty_account", ""),
                document_number=op.get("document_number", ""),
                classification=classification_result.get("classification", "disputed"),
                classification_rule=classification_result.get("rule", ""),
                classification_confidence=classification_result.get("confidence", 0.0),
                included_in_tax_base=classification_result.get("classification") == "income",
            )

            db.add(bank_op)
            summary.total_saved += 1

            # Count by classification
            op_amount = Decimal(str(op.get("amount", 0)))
            if classification_result.get("classification") == "income":
                summary.income_count += 1
                summary.income_amount += op_amount
            elif classification_result.get("classification") == "not_income":
                summary.not_income_count += 1
                summary.not_income_amount += op_amount
            else:
                summary.disputed_count += 1
                summary.disputed_amount += op_amount

        db.commit()

        # Log the import
        _log_audit(
            db, project_id, "import", "bank_statement",
            new_value=f"Загружено операций: {summary.total_saved} (Доход: {summary.income_count}, Исключено: {summary.not_income_count}, Спорные: {summary.disputed_count})"
        )

        # Calculate raw direction totals from parser (before classification)
        raw_income_count = 0
        raw_expense_count = 0
        raw_income_amount = Decimal("0")
        raw_expense_amount = Decimal("0")
        for op in operations:
            op_amount = Decimal(str(op.get("amount", 0)))
            if op.get("direction") == "income":
                raw_income_count += 1
                raw_income_amount += op_amount
            else:
                raw_expense_count += 1
                raw_expense_amount += op_amount

        return {
            "status": "success",
            "file_name": file_name,
            "total_parsed": summary.total_parsed,
            "total_saved": summary.total_saved,
            # Classification-based counts
            "income_count": summary.income_count,
            "not_income_count": summary.not_income_count,
            "disputed_count": summary.disputed_count,
            "income_amount": str(summary.income_amount),
            "not_income_amount": str(summary.not_income_amount),
            "disputed_amount": str(summary.disputed_amount),
            "total_amount": str(summary.income_amount + summary.not_income_amount + summary.disputed_amount),
            # Raw direction totals (from bank statement)
            "raw_income_count": raw_income_count,
            "raw_expense_count": raw_expense_count,
            "raw_income_amount": str(raw_income_amount),
            "raw_expense_amount": str(raw_expense_amount),
            # Owner info from parser (ИП данные из выписки)
            "owner_inn": parse_result.get("owner_inn"),
            "owner_name": parse_result.get("owner_name"),
            # Period and account info from parser
            "account_number": parse_result.get("account_number"),
            "period_start": str(parse_result.get("period_start", "")) if parse_result.get("period_start") else None,
            "period_end": str(parse_result.get("period_end", "")) if parse_result.get("period_end") else None,
            "opening_balance": str(parse_result.get("opening_balance", "")) if parse_result.get("opening_balance") else None,
            "closing_balance": str(parse_result.get("closing_balance", "")) if parse_result.get("closing_balance") else None,
            "parser_warnings": parse_result.get("warnings", []),
            "message": f"Банковская выписка успешно импортирована. Загружено {summary.total_saved} операций."
        }

    except Exception as e:
        # Clean up file on error
        if os.path.exists(file_path):
            os.remove(file_path)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обработке файла: {str(e)}"
        )


@router.post("/ofd/{project_id}")
async def import_ofd_receipts(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Import OFD (Online Cash Register) receipts file.

    Currently a stub implementation that saves raw file data.

    Args:
        project_id: ID of the project
        file: OFD file (.xlsx or .csv format)

    Returns:
        Import summary with basic information.

    Raises:
        HTTPException: If project not found or file format invalid.
    """
    # Check project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Validate file extension
    allowed_ext = ('.xlsx', '.xls', '.csv')
    if not any(file.filename.lower().endswith(ext) for ext in allowed_ext):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Поддерживаются файлы .xlsx, .xls и .csv"
        )

    uploads_dir = _ensure_uploads_directory()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_name = f"{project_id}_ofd_{timestamp}_{file.filename}"
    file_path = os.path.join(uploads_dir, file_name)

    try:
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Remove previous OFD data for this project so we can reimport cleanly
        db.query(OfdReceipt).filter(OfdReceipt.project_id == project_id).delete()

        # Parse ОФД file (xlsx, xls, csv)
        parse_result = parse_ofd_xlsx(file_path)
        if parse_result.get("errors"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="; ".join(parse_result["errors"])
            )

        receipts = parse_result.get("receipts", [])
        # Batch insert для производительности (может быть 40000+ записей)
        BATCH_SIZE = 1000
        for i in range(0, len(receipts), BATCH_SIZE):
            batch = receipts[i:i + BATCH_SIZE]
            for r in batch:
                db.add(OfdReceipt(
                    project_id=project_id,
                    receipt_date=r["receipt_date"],
                    amount=r["amount"],
                    payment_type=r["payment_type"],
                    operation_type=r["operation_type"],
                    receipt_number=r.get("receipt_number") or None,
                    kkt_number=r.get("kkt_number") or None,
                    point_of_sale=r.get("point_of_sale") or None,
                ))
            db.flush()
        db.commit()

        total_cash = parse_result["total_cash"]
        total_card = parse_result["total_card"]
        total_refund_cash = parse_result["total_refund_cash"]
        total_refund_card = parse_result["total_refund_card"]
        total_sale = total_cash + total_card
        total_refund = total_refund_cash + total_refund_card

        # Log the import
        _log_audit(
            db, project_id, "import", "ofd_file",
            new_value=(
                f"ОФД: {len(receipts)} строк, "
                f"нал {total_cash}, безнал {total_card}, возвраты {total_refund}"
            )
        )

        return {
            "status": "success",
            "file_name": file_name,
            "total_receipts": len(receipts),
            "total_cash": str(total_cash),
            "total_card": str(total_card),
            "total_sale": str(total_sale),
            "total_refund_cash": str(total_refund_cash),
            "total_refund_card": str(total_refund_card),
            "total_refund": str(total_refund),
            "period_start": str(parse_result["period_start"]) if parse_result.get("period_start") else None,
            "period_end": str(parse_result["period_end"]) if parse_result.get("period_end") else None,
            "warnings": parse_result.get("warnings", [])[:20],
            "message": (
                f"ОФД файл обработан: {len(receipts)} строк "
                f"(наличные {total_cash} ₽, эквайринг {total_card} ₽)."
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        # Clean up file on error
        if os.path.exists(file_path):
            os.remove(file_path)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обработке файла: {str(e)}"
        )
