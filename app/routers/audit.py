"""
Audit log endpoints for Russian tax declaration system.
Provides audit trail for all changes to projects and related data.
"""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


# Pydantic models
from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    """Response model for audit log entry."""
    id: int
    project_id: int
    action: str  # "create", "update", "delete", "reclassify", "import", "calculate"
    entity_type: str  # "project", "bank_operation", "tax_calculation", etc.
    entity_id: Optional[int]
    old_value: Optional[str]
    new_value: Optional[str]
    user_comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    """Response model for paginated audit log list."""
    total_count: int
    skip: int
    limit: int
    logs: List[AuditLogResponse]


@router.get("/{project_id}", response_model=AuditLogListResponse)
def get_audit_log(
    project_id: int,
    skip: int = 0,
    limit: int = 100,
    action_filter: Optional[str] = None,
    entity_filter: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Get audit log for a project with optional filtering and pagination.

    Args:
        project_id: ID of the project
        skip: Pagination - number of records to skip
        limit: Pagination - max records to return
        action_filter: Filter by action type (e.g., "update", "create", "delete")
        entity_filter: Filter by entity type (e.g., "project", "bank_operation")

    Returns:
        Paginated list of audit log entries.

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
    query = db.query(AuditLog).filter(AuditLog.project_id == project_id)

    # Apply filters if provided
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    if entity_filter:
        query = query.filter(AuditLog.entity_type == entity_filter)

    # Get total count
    total_count = query.count()

    # Sort by date descending (most recent first)
    logs = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()

    return AuditLogListResponse(
        total_count=total_count,
        skip=skip,
        limit=limit,
        logs=logs,
    )
