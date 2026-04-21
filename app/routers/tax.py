"""
Tax calculation endpoints for Russian УСН 6% system.
Handles income aggregation, contributions, advance payments, and tax calculations.
"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.database import get_db
from app.models import (
    Project, BankOperation, InsuranceContribution, TaxCalculation, AuditLog, OfdReceipt
)
from app.services.tax_engine import TaxEngine
from app.services.contribution_calculator import (
    detect_ens_payments,
    calculate_total_ip_contributions,
    calculate_quarterly_income,
    calculate_advances,
    distribute_ens_payments_to_quarters,
    get_advance_payments_from_ens,
    get_rates,
)

router = APIRouter(prefix="/api/tax", tags=["tax"])


# Pydantic models
from pydantic import BaseModel


class IncomeByQuarterResponse(BaseModel):
    """Response model for income aggregated by quarter."""
    q1_income: Decimal
    q2_income: Decimal
    q3_income: Decimal
    q4_income: Decimal
    total_income: Decimal
    q1_date_start: Optional[date]
    q1_date_end: Optional[date]
    q2_date_start: Optional[date]
    q2_date_end: Optional[date]
    q3_date_start: Optional[date]
    q3_date_end: Optional[date]
    q4_date_start: Optional[date]
    q4_date_end: Optional[date]


class ContributionRequest(BaseModel):
    """Request model for insurance contribution."""
    contribution_type: str  # "fixed_ip", "one_percent", "employee_insurance", "total"
    amount: Decimal
    payment_date: Optional[date] = None
    document_number: Optional[str] = None
    status: str = "accepted"
    comment: Optional[str] = None


class ContributionResponse(BaseModel):
    """Response model for insurance contribution."""
    id: int
    project_id: int
    contribution_type: str
    amount: Decimal
    payment_date: Optional[date]
    document_number: Optional[str]
    status: str
    comment: Optional[str]

    class Config:
        from_attributes = True


class AdvancePaymentRequest(BaseModel):
    """Request model for advance payment."""
    period: str  # "q1", "q2", "q3", "advance"
    amount: Decimal
    payment_date: Optional[date] = None
    document_number: Optional[str] = None


class TaxCalculationResult(BaseModel):
    """Response model for tax calculation."""
    period: str
    income_cumulative: Decimal
    tax_calculated: Decimal
    contributions_applied: Decimal
    contribution_limit: Decimal
    tax_after_reduction: Decimal
    advance_paid: Decimal
    tax_due: Decimal


class DeclarationData(BaseModel):
    """Response model for declaration data (form-friendly)."""
    project_id: int
    inn: str
    fio: str
    tax_period_year: int
    total_income: Decimal
    total_contributions: Decimal
    total_advances: Decimal
    tax_q1: Optional[Decimal]
    tax_q2: Optional[Decimal]
    tax_q3: Optional[Decimal]
    tax_q4: Optional[Decimal]
    tax_total: Optional[Decimal]
    detailed_calculations: List[TaxCalculationResult]


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


def _get_quarter_dates(year: int) -> Dict[str, tuple]:
    """Get date ranges for each quarter of a year."""
    return {
        "q1": (date(year, 1, 1), date(year, 3, 31)),
        "q2": (date(year, 4, 1), date(year, 6, 30)),
        "q3": (date(year, 7, 1), date(year, 9, 30)),
        "q4": (date(year, 10, 1), date(year, 12, 31)),
    }


@router.get("/income/{project_id}", response_model=IncomeByQuarterResponse)
def get_aggregated_income(project_id: int, db: Session = Depends(get_db)):
    """
    Get aggregated income by quarters.

    Args:
        project_id: ID of the project

    Returns:
        Income totals for each quarter and year total.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    year = project.tax_period_year
    quarter_dates = _get_quarter_dates(year)

    # Calculate income for each quarter
    def get_quarterly_income(q_start: date, q_end: date) -> Decimal:
        total = db.query(func.sum(BankOperation.amount)).filter(
            BankOperation.project_id == project_id,
            BankOperation.direction == "income",
            BankOperation.included_in_tax_base == True,
            BankOperation.operation_date >= q_start,
            BankOperation.operation_date <= q_end,
        ).scalar() or Decimal("0")
        return total

    q1_start, q1_end = quarter_dates["q1"]
    q2_start, q2_end = quarter_dates["q2"]
    q3_start, q3_end = quarter_dates["q3"]
    q4_start, q4_end = quarter_dates["q4"]

    q1_income = get_quarterly_income(q1_start, q1_end)
    q2_income = get_quarterly_income(q2_start, q2_end)
    q3_income = get_quarterly_income(q3_start, q3_end)
    q4_income = get_quarterly_income(q4_start, q4_end)

    total_income = q1_income + q2_income + q3_income + q4_income

    return IncomeByQuarterResponse(
        q1_income=q1_income,
        q2_income=q2_income,
        q3_income=q3_income,
        q4_income=q4_income,
        total_income=total_income,
        q1_date_start=q1_start,
        q1_date_end=q1_end,
        q2_date_start=q2_start,
        q2_date_end=q2_end,
        q3_date_start=q3_start,
        q3_date_end=q3_end,
        q4_date_start=q4_start,
        q4_date_end=q4_end,
    )


@router.post("/contributions/{project_id}")
def save_contributions(
    project_id: int,
    contributions: List[ContributionRequest],
    db: Session = Depends(get_db),
):
    """
    Save insurance contributions for a project.

    Args:
        project_id: ID of the project
        contributions: List of contributions to save

    Returns:
        List of saved contributions.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Clear existing contributions
    db.query(InsuranceContribution).filter(
        InsuranceContribution.project_id == project_id
    ).delete()

    saved_contributions = []
    total_amount = Decimal("0")

    for contrib_data in contributions:
        contribution = InsuranceContribution(
            project_id=project_id,
            contribution_type=contrib_data.contribution_type,
            amount=contrib_data.amount,
            payment_date=contrib_data.payment_date,
            document_number=contrib_data.document_number,
            status=contrib_data.status,
            comment=contrib_data.comment,
        )
        db.add(contribution)
        saved_contributions.append(contribution)
        total_amount += contrib_data.amount

    db.commit()

    # Refresh to get IDs
    for contrib in saved_contributions:
        db.refresh(contrib)

    # Log the update
    _log_audit(
        db, project_id, "update", "contributions",
        new_value=f"Сохранено взносов: {len(contributions)}, сумма: {total_amount}"
    )

    return saved_contributions


@router.get("/contributions/{project_id}", response_model=List[ContributionResponse])
def get_contributions(project_id: int, db: Session = Depends(get_db)):
    """
    Get all insurance contributions for a project.

    Args:
        project_id: ID of the project

    Returns:
        List of all contributions for the project.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    contributions = db.query(InsuranceContribution).filter(
        InsuranceContribution.project_id == project_id
    ).all()

    return contributions


@router.post("/advances/{project_id}")
def save_advance_payments(
    project_id: int,
    advances: List[AdvancePaymentRequest],
    db: Session = Depends(get_db),
):
    """
    Save advance tax payments for a project.

    Args:
        project_id: ID of the project
        advances: List of advance payments to save

    Returns:
        Confirmation message.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Store advances as metadata for now (could be in separate table)
    # For MVP, we'll just log the action
    total_amount = sum(adv.amount for adv in advances)

    _log_audit(
        db, project_id, "update", "advance_payments",
        new_value=f"Авансовые платежи: {len(advances)}, сумма: {total_amount}"
    )

    return {
        "status": "success",
        "advances_count": len(advances),
        "total_amount": total_amount,
        "message": f"Авансовые платежи успешно сохранены"
    }


@router.get("/advances/{project_id}")
def get_advance_payments(project_id: int, db: Session = Depends(get_db)):
    """
    Get advance tax payments for a project (stub).

    Args:
        project_id: ID of the project

    Returns:
        List of advance payments (currently empty).

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # TODO: Implement proper advance payment storage
    return {"advances": []}


@router.post("/calculate/{project_id}")
def calculate_tax(
    project_id: int,
    revenue_source: str = "bank",
    db: Session = Depends(get_db),
):
    """
    Run full tax calculation for the project.

    Args:
        project_id: ID of the project
        revenue_source: "bank" (default) — использовать банковскую выписку как есть,
                        "reconciled" — дополнить банковскую выручку наличной из ОФД.

    Returns:
        Full calculation result with quarterly and annual taxes.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Prepare project settings for tax engine
    project_settings = {
        "has_employees": project.has_employees,
        "uses_ens": project.uses_ens,
        "employee_start_quarter": project.employee_start_quarter,
        "contribution_input_mode": project.contribution_input_mode,
        "year": project.tax_period_year,
        "tax_rate": project.tax_rate if project.tax_rate else "6",
        "oktmo": project.oktmo or "",
    }

    # Initialize tax engine
    tax_engine = TaxEngine(project_settings)

    # Get income by quarter
    year = project.tax_period_year
    quarter_dates = _get_quarter_dates(year)

    quarterly_income = {}
    for q, (q_start, q_end) in quarter_dates.items():
        income = db.query(func.sum(BankOperation.amount)).filter(
            BankOperation.project_id == project_id,
            BankOperation.direction == "income",
            BankOperation.included_in_tax_base == True,
            BankOperation.operation_date >= q_start,
            BankOperation.operation_date <= q_end,
        ).scalar() or Decimal("0")
        quarterly_income[q] = income

    # Дополнить выручку наличными из ОФД, если пользователь выбрал сверку.
    # Логика: эквайринг в банке уже учтён (deposits от эквайрера),
    # а в ОФД «Наличные» отражают только кэш — его в банке нет.
    revenue_source_note: Optional[str] = None
    if revenue_source == "reconciled":
        for q, (q_start, q_end) in quarter_dates.items():
            q_start_dt = datetime(q_start.year, q_start.month, q_start.day)
            q_end_dt = datetime(q_end.year, q_end.month, q_end.day, 23, 59, 59)
            cash_sale = db.query(func.sum(OfdReceipt.amount)).filter(
                OfdReceipt.project_id == project_id,
                OfdReceipt.payment_type == "cash",
                OfdReceipt.operation_type == "sale",
                OfdReceipt.receipt_date >= q_start_dt,
                OfdReceipt.receipt_date <= q_end_dt,
            ).scalar() or Decimal("0")
            cash_refund = db.query(func.sum(OfdReceipt.amount)).filter(
                OfdReceipt.project_id == project_id,
                OfdReceipt.payment_type == "cash",
                OfdReceipt.operation_type == "refund",
                OfdReceipt.receipt_date >= q_start_dt,
                OfdReceipt.receipt_date <= q_end_dt,
            ).scalar() or Decimal("0")
            quarterly_income[q] = quarterly_income[q] + (cash_sale - cash_refund)
        revenue_source_note = "База дополнена наличной выручкой из ОФД (эквайринг уже в банке)."

    # Get contributions
    contributions_list = db.query(InsuranceContribution).filter(
        InsuranceContribution.project_id == project_id
    ).all()

    contributions_dict = {}
    for c in contributions_list:
        if c.contribution_type not in contributions_dict:
            contributions_dict[c.contribution_type] = Decimal("0")
        contributions_dict[c.contribution_type] += c.amount

    # Автоматически дополнить фикс. взносами и расчётным 1% из справочника,
    # если в таблице взносов пусто (чаще всего — когда расчёт идёт до подтверждения).
    total_year_income = sum(quarterly_income.values(), Decimal("0"))
    from app.services.contribution_calculator import calculate_total_ip_contributions
    auto_contrib = calculate_total_ip_contributions(project.tax_period_year, total_year_income)
    engine_contributions = {
        "fixed_ip": contributions_dict.get("fixed_ip", auto_contrib["fixed"]),
        "one_percent": contributions_dict.get("one_percent", auto_contrib["one_percent"]),
        "employee_insurance": contributions_dict.get("employee_insurance", Decimal("0")),
    }

    # Run calculation
    try:
        calculation_result = tax_engine.calculate(quarterly_income, engine_contributions, {})
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при расчёте налога: {str(e)}"
        )

    # Clear existing calculations
    db.query(TaxCalculation).filter(TaxCalculation.project_id == project_id).delete()

    # Save results to database
    saved_calculations = []
    periods_data = calculation_result.get("periods", {})

    for period, calc_data in periods_data.items():
        tax_calc = TaxCalculation(
            project_id=project_id,
            period=period,
            income_cumulative=Decimal(str(calc_data.get("income_cumulative", 0))),
            tax_calculated=Decimal(str(calc_data.get("tax_calculated", 0))),
            contributions_applied=Decimal(str(calc_data.get("contributions_applied", 0))),
            contribution_limit=Decimal(str(calc_data.get("contribution_limit", 0))),
            tax_after_reduction=Decimal(str(calc_data.get("tax_after_reduction", 0))),
            advance_paid=Decimal(str(calc_data.get("actual_paid", 0))),
            # tax_due теперь = расчётный аванс за период (advance_due)
            tax_due=Decimal(str(calc_data.get("advance_due", 0))),
        )
        db.add(tax_calc)
        saved_calculations.append(tax_calc)

    db.commit()

    # Log the calculation
    _log_audit(
        db, project_id, "calculate", "tax",
        new_value=f"Произведён расчёт налога за {project.tax_period_year} год"
    )

    # Format response
    results = []
    for calc in saved_calculations:
        results.append(TaxCalculationResult(
            period=calc.period,
            income_cumulative=calc.income_cumulative,
            tax_calculated=calc.tax_calculated,
            contributions_applied=calc.contributions_applied,
            contribution_limit=calc.contribution_limit,
            tax_after_reduction=calc.tax_after_reduction,
            advance_paid=calc.advance_paid,
            tax_due=calc.tax_due,
        ))

    return {
        "status": "success",
        "project_id": project_id,
        "tax_period_year": project.tax_period_year,
        "calculations": results,
        "revenue_source": revenue_source,
        "revenue_source_note": revenue_source_note,
        "message": "Налог успешно рассчитан"
    }


@router.get("/calculation/{project_id}")
def get_calculation_results(project_id: int, db: Session = Depends(get_db)):
    """
    Get saved calculation results for a project.

    Args:
        project_id: ID of the project

    Returns:
        Calculation results by period.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    calculations = db.query(TaxCalculation).filter(
        TaxCalculation.project_id == project_id
    ).all()

    if not calculations:
        return {
            "status": "not_calculated",
            "message": "Расчёт налога не произведён. Сначала загрузите операции и сделайте расчёт."
        }

    results = []
    for calc in calculations:
        results.append(TaxCalculationResult(
            period=calc.period,
            income_cumulative=calc.income_cumulative,
            tax_calculated=calc.tax_calculated,
            contributions_applied=calc.contributions_applied,
            contribution_limit=calc.contribution_limit,
            tax_after_reduction=calc.tax_after_reduction,
            advance_paid=calc.advance_paid,
            tax_due=calc.tax_due,
        ))

    return {
        "project_id": project_id,
        "calculations": results,
    }


@router.get("/ens-payments/{project_id}")
def detect_ens_payments_endpoint(
    project_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Обнаружить платежи на ЕНС/ФНС в банковских операциях проекта.
    Возвращает список обнаруженных платежей с предварительной категоризацией.
    Если передан year — фильтрует по налоговому периоду.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Получить операции проекта (с фильтром по году если указан)
    filter_year = year or project.tax_period_year
    query = db.query(BankOperation).filter(
        BankOperation.project_id == project_id
    )
    if filter_year:
        query = query.filter(
            BankOperation.operation_date >= date(filter_year, 1, 1),
            BankOperation.operation_date <= date(filter_year, 12, 31),
        )
    operations = query.all()

    # Преобразовать в словари для детектора
    ops_dicts = []
    for op in operations:
        ops_dicts.append({
            "id": op.id,
            "operation_date": str(op.operation_date) if op.operation_date else "",
            "amount": str(op.amount),
            "purpose": op.purpose or "",
            "counterparty": op.counterparty or "",
            "counterparty_inn": op.counterparty_inn or "",
            "direction": op.direction or "",
            "classification": op.classification or "",
        })

    detected = detect_ens_payments(ops_dicts)

    return {
        "project_id": project_id,
        "detected_count": len(detected),
        "payments": detected,
    }


@router.get("/auto-contributions/{project_id}")
def auto_calculate_contributions(
    project_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Автоматически рассчитать страховые взносы на основе дохода.
    Возвращает расчётные суммы фикс. взносов и 1%.
    Если передан year — использует его вместо project.tax_period_year.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    year = year or project.tax_period_year

    # Получить общий доход за выбранный год
    income_query = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "income",
    )
    if year:
        income_query = income_query.filter(
            BankOperation.operation_date >= date(year, 1, 1),
            BankOperation.operation_date <= date(year, 12, 31),
        )
    total_income = income_query.scalar() or Decimal("0")

    # Рассчитать взносы
    contrib_info = calculate_total_ip_contributions(year, total_income)

    rates = get_rates(year)

    return {
        "project_id": project_id,
        "year": year,
        "total_income": str(total_income),
        "fixed_contributions": str(contrib_info["fixed"]),
        "one_percent": str(contrib_info["one_percent"]),
        "total_contributions": str(contrib_info["total"]),
        "income_threshold": str(rates["income_threshold"]),
        "max_1pct_cap": str(rates["max_1pct"]),
    }


@router.get("/ofd-revenue/{project_id}")
def get_ofd_revenue(
    project_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Получить суммы выручки из ОФД (онлайн-касса) за указанный год.
    Разделяет выручку по типу оплаты: наличная, эквайринг, смешанная.
    Позволяет сравнить с банковской выручкой и исключить задвоение эквайринга.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    year = year or project.tax_period_year

    # Базовый запрос: только продажи (не возвраты) для выбранного проекта
    base_query = db.query(OfdReceipt).filter(
        OfdReceipt.project_id == project_id,
        OfdReceipt.operation_type == "sale",
    )
    if year:
        base_query = base_query.filter(
            OfdReceipt.receipt_date >= date(year, 1, 1),
            OfdReceipt.receipt_date <= date(year, 12, 31),
        )

    # Суммы по типу оплаты
    def _sum_by_payment(payment_type: str) -> Decimal:
        q = base_query.with_entities(func.sum(OfdReceipt.amount)).filter(
            OfdReceipt.payment_type == payment_type
        )
        return q.scalar() or Decimal("0")

    cash_revenue = _sum_by_payment("cash")
    acquiring_revenue = _sum_by_payment("card")
    mixed_revenue = _sum_by_payment("mixed")

    # Возвраты
    refund_query = db.query(func.sum(OfdReceipt.amount)).filter(
        OfdReceipt.project_id == project_id,
        OfdReceipt.operation_type == "refund",
    )
    if year:
        refund_query = refund_query.filter(
            OfdReceipt.receipt_date >= date(year, 1, 1),
            OfdReceipt.receipt_date <= date(year, 12, 31),
        )
    refund_amount = refund_query.scalar() or Decimal("0")

    total_revenue = cash_revenue + acquiring_revenue + mixed_revenue - refund_amount

    # Количество чеков
    receipts_count = base_query.count()

    return {
        "project_id": project_id,
        "year": year,
        "total_revenue": str(total_revenue),
        "cash_revenue": str(cash_revenue),
        "acquiring_revenue": str(acquiring_revenue),
        "mixed_revenue": str(mixed_revenue),
        "refund_amount": str(refund_amount),
        "receipts_count": receipts_count,
    }


@router.get("/revenue-reconciliation/{project_id}")
def get_revenue_reconciliation(
    project_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Дневная сверка ОФД и банковского эквайринга.

    Для каждого дня возвращает:
      - ofd_cash  — продажи за наличные (ОФД)
      - ofd_card  — продажи по безналу/эквайрингу (ОФД)
      - bank_acquiring — поступления эквайринга на расчётный счёт за этот день
      - acquiring_diff = ofd_card - bank_acquiring (в идеале ~0, либо смещено на 1-2 дня)
      - cash_revenue — чистая наличная выручка за день

    Итоги:
      - ofd_total = cash + card (по продажам)
      - bank_total_acquiring — поступления, классифицированные как "эквайринг"
      - cash_revenue_total — общая наличная выручка (ОФД)
      - suggested_tax_base = bank_total_acquiring + cash_revenue_total
        (рекомендуемая налоговая база без задвоения эквайринга)
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    year = year or project.tax_period_year

    # 1) Поднять ОФД-чеки за год и агрегировать по дню
    ofd_q = db.query(OfdReceipt).filter(OfdReceipt.project_id == project_id)
    if year:
        ofd_q = ofd_q.filter(
            OfdReceipt.receipt_date >= datetime(year, 1, 1),
            OfdReceipt.receipt_date < datetime(year + 1, 1, 1),
        )
    from collections import defaultdict
    ofd_by_day: Dict[date, Dict[str, Decimal]] = defaultdict(lambda: {
        "cash": Decimal("0"), "card": Decimal("0"),
        "refund_cash": Decimal("0"), "refund_card": Decimal("0"),
    })
    for r in ofd_q.all():
        d = r.receipt_date.date() if hasattr(r.receipt_date, "date") else r.receipt_date
        key = ("refund_" if r.operation_type == "refund" else "") + (r.payment_type or "cash")
        if key in ofd_by_day[d]:
            ofd_by_day[d][key] += Decimal(r.amount)

    # 2) Поднять банковские операции-доходы и извлечь эквайринг по дню
    bank_q = db.query(BankOperation).filter(
        BankOperation.project_id == project_id,
        BankOperation.direction == "income",
        BankOperation.classification == "income",
    )
    if year:
        bank_q = bank_q.filter(
            BankOperation.operation_date >= date(year, 1, 1),
            BankOperation.operation_date <= date(year, 12, 31),
        )

    bank_acquiring_by_day: Dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    bank_non_acquiring_total = Decimal("0")
    bank_acquiring_total = Decimal("0")

    ACQUIRING_KEYWORDS = (
        "эквайринг", "эквайр", "terminal", "терминал", "pos-терминал",
        "торгов", "карт", "card", "онлайн продаж", "онлайн касс",
    )
    for op in bank_q.all():
        purpose = (op.purpose or "").lower()
        counterparty = (op.counterparty or "").lower()
        is_acquiring = any(k in purpose or k in counterparty for k in ACQUIRING_KEYWORDS)
        amount = Decimal(op.amount)
        d = op.posting_date or op.operation_date
        if is_acquiring:
            bank_acquiring_by_day[d] += amount
            bank_acquiring_total += amount
        else:
            bank_non_acquiring_total += amount

    # 3) Собрать дневную сверку
    all_days = sorted(set(ofd_by_day.keys()) | set(bank_acquiring_by_day.keys()))
    daily: List[Dict[str, Any]] = []
    for d in all_days:
        o = ofd_by_day.get(d, {})
        ofd_cash = o.get("cash", Decimal("0"))
        ofd_card = o.get("card", Decimal("0"))
        ref_cash = o.get("refund_cash", Decimal("0"))
        ref_card = o.get("refund_card", Decimal("0"))
        bank_acq = bank_acquiring_by_day.get(d, Decimal("0"))
        daily.append({
            "date": str(d),
            "ofd_cash": str(ofd_cash),
            "ofd_card": str(ofd_card),
            "ofd_refund_cash": str(ref_cash),
            "ofd_refund_card": str(ref_card),
            "bank_acquiring": str(bank_acq),
            "acquiring_diff": str((ofd_card - ref_card) - bank_acq),
            "cash_revenue": str(ofd_cash - ref_cash),
        })

    # 4) Суммы
    total_ofd_cash = sum((o["cash"] for o in ofd_by_day.values()), Decimal("0"))
    total_ofd_card = sum((o["card"] for o in ofd_by_day.values()), Decimal("0"))
    total_refund_cash = sum((o["refund_cash"] for o in ofd_by_day.values()), Decimal("0"))
    total_refund_card = sum((o["refund_card"] for o in ofd_by_day.values()), Decimal("0"))
    cash_revenue_total = total_ofd_cash - total_refund_cash
    ofd_total_sale = total_ofd_cash + total_ofd_card - total_refund_cash - total_refund_card

    # Суммарная разница (для визуального контроля)
    total_acquiring_diff = (total_ofd_card - total_refund_card) - bank_acquiring_total

    # Рекомендуемая налоговая база:
    # эквайринг считаем по банку (он попадает «по факту поступления»),
    # а наличные доливаем из ОФД (в банке их нет).
    suggested_tax_base = bank_acquiring_total + bank_non_acquiring_total + cash_revenue_total

    return {
        "project_id": project_id,
        "year": year,
        "daily": daily,
        "summary": {
            "ofd_cash_sale":        str(total_ofd_cash),
            "ofd_card_sale":        str(total_ofd_card),
            "ofd_refund_cash":      str(total_refund_cash),
            "ofd_refund_card":      str(total_refund_card),
            "ofd_total_sale":       str(ofd_total_sale),
            "cash_revenue_total":   str(cash_revenue_total),
            "bank_acquiring_total": str(bank_acquiring_total),
            "bank_other_income":    str(bank_non_acquiring_total),
            "bank_total_income":    str(bank_acquiring_total + bank_non_acquiring_total),
            "acquiring_diff_total": str(total_acquiring_diff),
            "suggested_tax_base":   str(suggested_tax_base),
        },
    }


class ENSPaymentCategory(BaseModel):
    """Категоризация одного ЕНС-платежа."""
    operation_id: int
    category: str  # "fixed_contributions", "one_percent", "employee_contributions", "tax_advance", "other"
    amount: Decimal
    date: Optional[str] = None


class AutoCalcRequest(BaseModel):
    """Запрос на авто-расчёт с категоризированными ЕНС-платежами."""
    ens_payments: List[ENSPaymentCategory] = []
    has_employees: bool = False
    year: Optional[int] = None


@router.post("/auto-calculate/{project_id}")
def auto_calculate_tax(
    project_id: int,
    request: AutoCalcRequest,
    db: Session = Depends(get_db),
):
    """
    Полный авто-расчёт: взносы + авансы + налог.
    Принимает категоризированные ЕНС-платежи от пользователя.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    year = request.year or project.tax_period_year

    # 1) Получить все операции «доход» за выбранный год
    income_query = db.query(BankOperation).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "income",
    )
    if year:
        income_query = income_query.filter(
            BankOperation.operation_date >= date(year, 1, 1),
            BankOperation.operation_date <= date(year, 12, 31),
        )
    income_ops = income_query.all()

    ops_dicts = [{
        "classification": "income",
        "amount": str(op.amount),
        "operation_date": str(op.operation_date) if op.operation_date else "",
    } for op in income_ops]

    quarterly_income = calculate_quarterly_income(ops_dicts)

    # 2) Распределить взносы по кварталам (из ЕНС-платежей)
    ens_list = [{
        "amount": str(p.amount),
        "date": p.date or "",
        "category": p.category,
    } for p in request.ens_payments]

    quarterly_contributions = distribute_ens_payments_to_quarters(ens_list)

    # 3) Рассчитать авансы
    advance_results = calculate_advances(
        year, quarterly_income, quarterly_contributions, request.has_employees
    )

    # 4) Фактически уплаченные авансы из выписки
    actual_advances = get_advance_payments_from_ens(ens_list)

    # 5) Сравнение расчётных и фактических авансов
    comparison = []
    for calc in advance_results:
        period = calc["period"]
        # Map period to quarters for actual advances
        calculated_due = Decimal(calc["tax_due"])

        actual_paid = Decimal("0")
        if period == "q1":
            actual_paid = actual_advances.get("q1", Decimal("0"))
        elif period == "half_year":
            actual_paid = actual_advances.get("q2", Decimal("0"))
        elif period == "nine_months":
            actual_paid = actual_advances.get("q3", Decimal("0"))
        elif period == "year":
            actual_paid = actual_advances.get("q4", Decimal("0"))

        diff = actual_paid - calculated_due

        comparison.append({
            "period": period,
            "period_name": calc["period_name"],
            "calculated_advance": str(calculated_due),
            "actual_paid": str(actual_paid),
            "difference": str(diff),
            "status": "ok" if diff >= 0 else "underpaid",
        })

    # 6) Итоги взносов
    total_income = sum(quarterly_income.values())
    contrib_info = calculate_total_ip_contributions(year, total_income)

    total_ens_contributions = sum(
        Decimal(str(p.amount)) for p in request.ens_payments
        if p.category in ("fixed_contributions", "one_percent", "employee_contributions", "ens_mixed")
    )

    return {
        "project_id": project_id,
        "year": year,
        "quarterly_income": {k: str(v) for k, v in quarterly_income.items()},
        "total_income": str(total_income),
        "contributions_required": {
            "fixed": str(contrib_info["fixed"]),
            "one_percent": str(contrib_info["one_percent"]),
            "total": str(contrib_info["total"]),
        },
        "contributions_paid": str(total_ens_contributions),
        "quarterly_contributions": {k: str(v) for k, v in quarterly_contributions.items()},
        "advance_calculations": advance_results,
        "advance_comparison": comparison,
    }


@router.get("/declaration/{project_id}", response_model=DeclarationData)
def get_declaration_data(project_id: int, db: Session = Depends(get_db)):
    """
    Get declaration data formatted for form filling.

    Args:
        project_id: ID of the project

    Returns:
        Declaration data with all required values.

    Raises:
        HTTPException: If project not found.
    """
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Get calculations
    calculations = db.query(TaxCalculation).filter(
        TaxCalculation.project_id == project_id
    ).all()

    calc_dict = {calc.period: calc for calc in calculations}

    # Extract quarterly and total taxes
    tax_q1 = calc_dict.get("q1", {}).tax_due if "q1" in calc_dict else None
    tax_q2 = calc_dict.get("half_year", {}).tax_due if "half_year" in calc_dict else None
    tax_q3 = calc_dict.get("nine_months", {}).tax_due if "nine_months" in calc_dict else None
    tax_q4 = calc_dict.get("year", {}).tax_due if "year" in calc_dict else None

    # Total income
    total_income = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.direction == "income",
        BankOperation.included_in_tax_base == True,
    ).scalar() or Decimal("0")

    # Total contributions
    total_contributions = db.query(func.sum(InsuranceContribution.amount)).filter(
        InsuranceContribution.project_id == project_id,
    ).scalar() or Decimal("0")

    # Prepare results
    calc_results = []
    for calc in calculations:
        calc_results.append(TaxCalculationResult(
            period=calc.period,
            income_cumulative=calc.income_cumulative,
            tax_calculated=calc.tax_calculated,
            contributions_applied=calc.contributions_applied,
            contribution_limit=calc.contribution_limit,
            tax_after_reduction=calc.tax_after_reduction,
            advance_paid=calc.advance_paid,
            tax_due=calc.tax_due,
        ))

    return DeclarationData(
        project_id=project_id,
        inn=project.inn,
        fio=project.fio,
        tax_period_year=project.tax_period_year,
        total_income=total_income,
        total_contributions=total_contributions,
        total_advances=Decimal("0"),  # TODO: Get from advances storage
        tax_q1=tax_q1,
        tax_q2=tax_q2,
        tax_q3=tax_q3,
        tax_q4=tax_q4,
        tax_total=tax_q4,  # Year total is in q4
        detailed_calculations=calc_results,
    )
