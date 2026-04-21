"""
Project CRUD endpoints for Russian tax declaration system.
Manages individual tax payer (ИП) projects and their basic information.
"""

from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Project, BankOperation, AuditLog

router = APIRouter(prefix="/api/projects", tags=["projects"])


# Pydantic models for requests/responses
from pydantic import BaseModel


class ProjectCreate(BaseModel):
    """Request model for creating a project."""
    inn: str
    fio: str
    tax_period_year: int
    has_employees: bool = False
    employee_start_quarter: Optional[int] = None
    uses_ens: bool = True
    contribution_input_mode: str = "total"
    oktmo: Optional[str] = None
    ifns_code: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "inn": "123456789012",
                "fio": "Иванов Иван Иванович",
                "tax_period_year": 2024,
                "has_employees": False,
                "uses_ens": True,
                "contribution_input_mode": "total",
                "oktmo": "12345678",
                "ifns_code": "1234"
            }
        }


class ProjectUpdate(BaseModel):
    """Request model for updating a project."""
    inn: Optional[str] = None
    fio: Optional[str] = None
    tax_period_year: Optional[int] = None
    has_employees: Optional[bool] = None
    employee_start_quarter: Optional[int] = None
    uses_ens: Optional[bool] = None
    contribution_input_mode: Optional[str] = None
    oktmo: Optional[str] = None
    ifns_code: Optional[str] = None


class ProjectStatusUpdate(BaseModel):
    """Request model for updating project status."""
    status: str  # "draft", "submitted", "completed", etc.

    class Config:
        json_schema_extra = {"example": {"status": "submitted"}}


class ProjectResponse(BaseModel):
    """Response model for project data."""
    id: int
    inn: str
    fio: str
    tax_period_year: int
    status: str
    has_employees: bool
    employee_start_quarter: Optional[int]
    uses_ens: bool
    contribution_input_mode: str
    oktmo: Optional[str]
    ifns_code: Optional[str]
    tax_rate: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectStatsResponse(BaseModel):
    """Response model for project with statistics."""
    project: ProjectResponse
    operations_count: int
    income_count: int
    excluded_count: int
    disputed_count: int
    total_income: Decimal
    total_excluded: Decimal


def _log_audit(
    db: Session,
    project_id: int,
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    comment: Optional[str] = None,
):
    """Helper function to log audit events."""
    log_entry = AuditLog(
        project_id=project_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        user_comment=comment,
    )
    db.add(log_entry)
    db.commit()


@router.get("/", response_model=List[ProjectResponse])
def list_projects(db: Session = Depends(get_db)):
    """
    List all tax payer projects.

    Returns:
        List of all projects in the system.
    """
    projects = db.query(Project).all()
    return projects


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    project_data: ProjectCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new tax payer project (ИП).

    Args:
        project_data: Project creation data

    Returns:
        Created project with all details.

    Raises:
        HTTPException: If INN already exists for the given year.
    """
    # Check if project with same INN and year already exists
    existing = db.query(Project).filter(
        Project.inn == project_data.inn,
        Project.tax_period_year == project_data.tax_period_year
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Проект с ИНН {project_data.inn} на {project_data.tax_period_year} год уже существует"
        )

    # Create new project
    project = Project(
        inn=project_data.inn,
        fio=project_data.fio,
        tax_period_year=project_data.tax_period_year,
        has_employees=project_data.has_employees,
        employee_start_quarter=project_data.employee_start_quarter,
        uses_ens=project_data.uses_ens,
        contribution_input_mode=project_data.contribution_input_mode,
        oktmo=project_data.oktmo,
        ifns_code=project_data.ifns_code,
    )

    db.add(project)
    db.commit()
    db.refresh(project)

    # Log audit event
    _log_audit(
        db, project.id, "create", "project", project.id,
        new_value=f"Проект {project_data.fio} ({project_data.inn})"
    )

    return project


@router.get("/{project_id}", response_model=ProjectStatsResponse)
def get_project(project_id: int, db: Session = Depends(get_db)):
    """
    Get project details with operation statistics.

    Args:
        project_id: ID of the project

    Returns:
        Project details with statistics (operation counts, income totals).

    Raises:
        HTTPException: If project not found.
    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Calculate statistics
    total_ops = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id
    ).scalar() or 0

    income_ops = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "income",
    ).scalar() or 0

    excluded_ops = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "not_income",
    ).scalar() or 0

    disputed_ops = db.query(func.count(BankOperation.id)).filter(
        BankOperation.project_id == project_id,
        BankOperation.classification == "disputed",
    ).scalar() or 0

    total_income = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.direction == "income",
        BankOperation.included_in_tax_base == True,
    ).scalar() or Decimal("0")

    total_excluded = db.query(func.sum(BankOperation.amount)).filter(
        BankOperation.project_id == project_id,
        BankOperation.direction == "income",
        BankOperation.included_in_tax_base == False,
    ).scalar() or Decimal("0")

    return ProjectStatsResponse(
        project=project,
        operations_count=total_ops,
        income_count=income_ops,
        excluded_count=excluded_ops,
        disputed_count=disputed_ops,
        total_income=total_income,
        total_excluded=total_excluded,
    )


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_data: ProjectUpdate,
    db: Session = Depends(get_db)
):
    """
    Update project information.

    Args:
        project_id: ID of the project
        project_data: Fields to update

    Returns:
        Updated project details.

    Raises:
        HTTPException: If project not found.
    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    # Track changes for audit log
    changes = []

    if project_data.inn is not None and project_data.inn != project.inn:
        changes.append(f"ИНН: {project.inn} -> {project_data.inn}")
        project.inn = project_data.inn

    if project_data.tax_period_year is not None and project_data.tax_period_year != project.tax_period_year:
        changes.append(f"Год: {project.tax_period_year} -> {project_data.tax_period_year}")
        project.tax_period_year = project_data.tax_period_year

    if project_data.fio is not None and project_data.fio != project.fio:
        changes.append(f"ФИО: {project.fio} -> {project_data.fio}")
        project.fio = project_data.fio

    if project_data.has_employees is not None:
        changes.append(f"Работники: {project.has_employees} -> {project_data.has_employees}")
        project.has_employees = project_data.has_employees

    if project_data.employee_start_quarter is not None:
        changes.append(f"Квартал начала работ: {project.employee_start_quarter} -> {project_data.employee_start_quarter}")
        project.employee_start_quarter = project_data.employee_start_quarter

    if project_data.uses_ens is not None:
        changes.append(f"ЕНС: {project.uses_ens} -> {project_data.uses_ens}")
        project.uses_ens = project_data.uses_ens

    if project_data.contribution_input_mode is not None:
        changes.append(f"Режим взносов: {project.contribution_input_mode} -> {project_data.contribution_input_mode}")
        project.contribution_input_mode = project_data.contribution_input_mode

    if project_data.oktmo is not None and project_data.oktmo != project.oktmo:
        changes.append(f"ОКТМО: {project.oktmo} -> {project_data.oktmo}")
        project.oktmo = project_data.oktmo

    if project_data.ifns_code is not None and project_data.ifns_code != project.ifns_code:
        changes.append(f"Код ИФНС: {project.ifns_code} -> {project_data.ifns_code}")
        project.ifns_code = project_data.ifns_code

    if changes:
        db.commit()
        db.refresh(project)

        _log_audit(
            db, project.id, "update", "project", project.id,
            new_value="; ".join(changes)
        )

    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """
    Delete project and all related data.

    Args:
        project_id: ID of the project

    Raises:
        HTTPException: If project not found.
    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    project_name = f"{project.fio} ({project.inn})"

    # Delete cascade handled by SQLAlchemy relationships
    db.delete(project)
    db.commit()

    # Log the deletion
    _log_audit(
        db, project_id, "delete", "project", project_id,
        old_value=project_name
    )


@router.put("/{project_id}/status", response_model=ProjectResponse)
def update_project_status(
    project_id: int,
    status_data: ProjectStatusUpdate,
    db: Session = Depends(get_db)
):
    """
    Update project status (draft, submitted, completed, etc.).

    Args:
        project_id: ID of the project
        status_data: New status value

    Returns:
        Updated project with new status.

    Raises:
        HTTPException: If project not found.
    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект с ID {project_id} не найден"
        )

    old_status = project.status
    project.status = status_data.status
    db.commit()
    db.refresh(project)

    _log_audit(
        db, project.id, "update", "project", project.id,
        old_value=old_status,
        new_value=status_data.status,
        comment=f"Изменение статуса проекта"
    )

    return project
