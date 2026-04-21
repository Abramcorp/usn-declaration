"""
Bank operations management endpoints for Russian tax declaration system.
Handles classification, filtering, and custom rules for operations.
"""

from typing import List, Optional
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.database import get_db
from app.models import (
    Project, BankOperation, ClassificationRule, AuditLog
)

router = APIRouter(prefix="/api/operations", tags=["operations"])


# Pydantic models
from pydantic import BaseModel


class BankOperationResponse(BaseModel):
    """Response model for bank operation."""
    id: int
    operation_date: date
    posting_date: Optional[date]
    amount: Decimal
    direction: str
    purpose: str
    counterparty: Optional[str]
    counterparty_inn: Optional[str]
    counterparty_account: Optional[str]
    document_number: Optional[str]
    classification: str
    classification_rule: Optional[str]
    classification_confidence: Optional[float]
    included_in_tax_base: bool
    exclusion_reason: Optional[str]
    user_comment: Optional[str]
    is_manually_classified: bool

    class Config:
        from_attributes = True


class ClassificationUpdateRequest(BaseModel):
    """Request model for manual classification update."""
    classification: str  # "income", "not_income", "disputed", "manually_set"
    comment: Optional[str] = None


class BatchClassificationRequest(BaseModel):
    """Request model for batch classification update."""
    operation_ids: List[int]
    classification: str
    comment: Optional[str] = None


class ClassificationRuleRequest(BaseModel):
    """Request model for creating classification rule."""
    rule_type: str  # "keyword_income", "keyword_exclude", "counterparty_income", "counterparty_exclude"
    pattern: str  # Keyword or counterparty name
    description: Optional[str] = None


class ClassificationRuleResponse(BaseModel):
    """Response model for classification rule."""
    id: int
    project_id: Optional[int]
    rule_type: str
    pattern: str
    description: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True


class OperationStatsResponse(BaseModel):
    """Response model for operation statistics."""
    total_operations: int
    total_income: Decimal
    total_excluded: Decimal
    total_disputed: Decimal
    income_count: int
    excluded_count: int
    disputed_count: int
    income_amount_sum: Decimal
    excluded_amount_sum: Decimal
    disputed_amount_sum: Decimal


def _log_audit(
    db: Session,
    project_id: int,
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
):
    """Helper function to log audit events."""
    log_entry = AuditLog(
        project_id=project_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(log_entry)
    db.commit()


@router.get("/{project_id}", response_model=List[BankOperationResponse])
def list_operations(
    project_id: int,
    classification: Optional[str] = None,
    year: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    min_amount: Optional[Decimal] = None,
    max_amount: Optional[Decimal] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    List bank operations with optional filtering.

    Args:
        project_id: ID of the project
        classification: Filter by classification ("income", "not_income", "disputed")
        date_from: Filter operations from date (inclusive)
        date_to: Filter operations to date (inclusive)
        min_amount: Minimum operation amount
        max_amount: Maximum operation amount
        search: Search in purpose and counterparty fields
        skip: Pagination - number of records to skip
        limit: Pagination - max records to return

    Returns:
        List of bank operations matching filters.

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

    # Build query
    query = db.query(BankOperation).filter(BankOperation.project_id == project_id)

    # Apply year filter (tax period)
    if year:
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        query = query.filter(
            BankOperation.operation_date >= year_start,
            BankOperation.operation_date <= year_end,
        )

    # Apply filters
    if classification:
        query = query.filter(BankOperation.classification == classification)

    if date_from:
        query = query.filter(BankOperation.operation_date >= date_from)

    if date_to:
        query = query.filter(BankOperation.operation_date <= date_to)

    if min_amount is not None:
        query = query.filter(BankOperation.amount >= min_amount)

    if max_amount is not None:
        query = query.filter(BankOperation.amount <= max_amount)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (BankOperation.purpose.ilike(search_term)) |
            (BankOperation.counterparty.ilike(search_term))
        )

    # Sort by date descending
    query = query.order_by(BankOperation.operation_date.desc())

    # Apply pagination
    operations = query.offset(skip).limit(limit).all()

    return operations


@router.get("/{project_id}/stats", response_model=OperationStatsResponse)
def get_operation_stats(project_id: int, db: Session = Depends(get_db)):
    """
    Get summary statistics for all operations in a project.

    Args:
        project_id: ID of the project

    Returns:
        Operation statistics including counts and amounts by classification.

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

    # Total count
    total = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id
    ).scalar() or 0

    # Count by classification
    income_count = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "income",
    ).scalar() or 0

    excluded_count = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "not_income",
    ).scalar() or 0

    disputed_count = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "disputed",
    ).scalar() or 0

    # Sum by classification
    income_sum = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "income",
        BankOperation.direction == "income",
    ).scalar() or Decimal("0")

    excluded_sum = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "not_income",
        BankOperation.direction == "income",
    ).scalar() or Decimal("0")

    disputed_sum = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "disputed",
        BankOperation.direction == "income",
    ).scalar() or Decimal("0")

    return OperationStatsResponse(
        total_operations=total,
        total_income=income_sum,
        total_excluded=excluded_sum,
        total_disputed=disputed_sum,
        income_count=income_count,
        excluded_count=excluded_count,
        disputed_count=disputed_count,
        income_amount_sum=income_sum,
        excluded_amount_sum=excluded_sum,
        disputed_amount_sum=disputed_sum,
    )


@router.put("/classify/{operation_id}")
def update_operation_classification(
    operation_id: int,
    request: ClassificationUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Manually reclassify a bank operation.

    Args:
        operation_id: ID of the operation
        request: New classification and optional comment

    Returns:
        Updated operation.

    Raises:
        HTTPException: If operation not found.
    """
    operation = db.query(BankOperation).filter(BankOperation.id == operation_id).first()

    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Операция с ID {operation_id} не найдена"
        )

    old_classification = operation.classification

    # Update classification
    operation.classification = request.classification
    operation.is_manually_classified = True
    operation.user_comment = request.comment
    operation.included_in_tax_base = (request.classification == "income")

    db.commit()
    db.refresh(operation)

    # Log the change
    _log_audit(
        db,
        operation.project_id,
        "reclassify",
        "bank_operation",
        operation_id,
        old_value=old_classification,
        new_value=request.classification,
    )

    return operation


@router.put("/batch-classify")
def batch_classify_operations(
    request: BatchClassificationRequest,
    db: Session = Depends(get_db),
):
    """
    Batch update classifications for multiple operations.

    Args:
        request: List of operation IDs and new classification

    Returns:
        Count of updated operations.

    Raises:
        HTTPException: If any operation not found.
    """
    # Fetch all operations
    operations = db.query(BankOperation).filter(
        BankOperation.id.in_(request.operation_ids)
    ).all()

    if len(operations) != len(request.operation_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Некоторые операции не найдены"
        )

    project_id = operations[0].project_id

    # Update all
    for op in operations:
        op.classification = request.classification
        op.is_manually_classified = True
        op.user_comment = request.comment
        op.included_in_tax_base = (request.classification == "income")

    db.commit()

    # Log the batch change
    _log_audit(
        db,
        project_id,
        "batch_reclassify",
        "bank_operations",
        new_value=f"Переклассифицировано {len(operations)} операций в '{request.classification}'",
    )

    return {
        "updated_count": len(operations),
        "message": f"Успешно обновлено {len(operations)} операций"
    }


@router.post("/rules/{project_id}", response_model=ClassificationRuleResponse)
def create_classification_rule(
    project_id: int,
    request: ClassificationRuleRequest,
    db: Session = Depends(get_db),
):
    """
    Create a custom classification rule for a project.

    Args:
        project_id: ID of the project
        request: Rule definition (type, pattern, description)

    Returns:
        Created classification rule.

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

    rule = ClassificationRule(
        project_id=project_id,
        rule_type=request.rule_type,
        pattern=request.pattern,
        description=request.description,
        is_active=True,
    )

    db.add(rule)
    db.commit()
    db.refresh(rule)

    # Log the creation
    _log_audit(
        db,
        project_id,
        "create",
        "classification_rule",
        rule.id,
        new_value=f"Правило: {request.rule_type} - {request.pattern}",
    )

    return rule


@router.get("/rules/{project_id}", response_model=List[ClassificationRuleResponse])
def list_classification_rules(project_id: int, db: Session = Depends(get_db)):
    """
    List all classification rules for a project.

    Args:
        project_id: ID of the project

    Returns:
        List of classification rules.

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

    rules = db.query(ClassificationRule).filter(
        ClassificationRule.project_id == project_id
    ).all()

    return rules


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_classification_rule(rule_id: int, db: Session = Depends(get_db)):
    """
    Delete a classification rule.

    Args:
        rule_id: ID of the rule

    Raises:
        HTTPException: If rule not found.
    """
    rule = db.query(ClassificationRule).filter(ClassificationRule.id == rule_id).first()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Правило с ID {rule_id} не найдено"
        )

    project_id = rule.project_id

    db.delete(rule)
    db.commit()

    # Log the deletion
    _log_audit(
        db,
        project_id,
        "delete",
        "classification_rule",
        rule_id,
        old_value=f"{rule.rule_type} - {rule.pattern}",
    )
