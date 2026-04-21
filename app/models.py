"""
SQLAlchemy ORM models for Russian tax declaration system (ИП на УСН 6%).
Represents entities involved in tax calculation and reporting for simplified tax system.
"""
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Numeric, Index
from sqlalchemy.orm import relationship

from app.database import Base


# ============================================================================
# Project / Налогоплательщик - Main tax payer entity
# ============================================================================
class Project(Base):
    """
    Represents a tax payer (индивидуальный предприниматель) and their tax period.
    Serves as the primary entity that all other records relate to.
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)

    # Basic taxpayer information
    inn = Column(String(12), nullable=False, index=True)  # ИНН
    fio = Column(String(255), nullable=False)  # ФИО (Full name)
    tax_period_year = Column(Integer, nullable=False, index=True)  # Налоговый период (год)

    # Tax system settings
    tax_rate = Column(Float, default=6.0)  # Налоговая ставка (6% for УСН 6%)
    oktmo = Column(String(8), nullable=True)  # ОКТМО (tax code)
    ifns_code = Column(String(4), nullable=True)  # Код ИФНС (tax authority code)

    # Employment information
    has_employees = Column(Boolean, default=False)  # Есть ли работники
    employee_start_quarter = Column(Integer, nullable=True)  # Квартал начала работы с сотрудниками (1-4)

    # System configuration
    uses_ens = Column(Boolean, default=True)  # Использует ЕНС (single tax notification system)
    contribution_input_mode = Column(String(20), default="total")  # "total" or "detailed"

    # Status
    status = Column(String(50), default="draft")  # "draft", "submitted", "completed", etc.

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    bank_operations = relationship("BankOperation", back_populates="project", cascade="all, delete-orphan")
    ofd_receipts = relationship("OfdReceipt", back_populates="project", cascade="all, delete-orphan")
    insurance_contributions = relationship("InsuranceContribution", back_populates="project", cascade="all, delete-orphan")
    tax_calculations = relationship("TaxCalculation", back_populates="project", cascade="all, delete-orphan")
    classification_rules = relationship("ClassificationRule", back_populates="project", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Project(inn={self.inn}, fio={self.fio}, year={self.tax_period_year})>"


# ============================================================================
# BankOperation / Банковская операция - Bank transactions
# ============================================================================
class BankOperation(Base):
    """
    Represents a bank transaction that needs to be classified as income or expense.
    Critical for determining tax base in УСН 6% system.
    """
    __tablename__ = "bank_operations"
    __table_args__ = (
        Index("ix_bank_operations_project_date", "project_id", "operation_date"),
        Index("ix_bank_operations_project_classification", "project_id", "classification"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    # Transaction dates
    operation_date = Column(Date, nullable=False, index=True)  # Дата операции
    posting_date = Column(Date, nullable=True)  # Дата проведения

    # Amount and direction
    amount = Column(Numeric(15, 2), nullable=False)  # Сумма
    direction = Column(String(20), nullable=False)  # "income" или "expense"

    # Transaction details
    purpose = Column(String(500), nullable=False)  # Назначение платежа
    counterparty = Column(String(255), nullable=True)  # Контрагент
    counterparty_inn = Column(String(12), nullable=True)  # ИНН контрагента
    counterparty_account = Column(String(20), nullable=True)  # Счёт контрагента
    document_number = Column(String(50), nullable=True)  # Номер документа

    # Classification
    classification = Column(String(50), default="disputed")  # "income", "not_income", "disputed", "manually_set"
    classification_rule = Column(String(255), nullable=True)  # Какое правило сработало
    classification_confidence = Column(Float, nullable=True)  # Уверенность классификации (0-1)

    # Tax base inclusion
    included_in_tax_base = Column(Boolean, default=False)  # Включена в налоговую базу
    exclusion_reason = Column(String(255), nullable=True)  # Причина исключения из базы

    # User notes
    user_comment = Column(String(500), nullable=True)  # Комментарий пользователя
    is_manually_classified = Column(Boolean, default=False)  # Вручную классифицировано

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="bank_operations")

    def __repr__(self):
        return f"<BankOperation(id={self.id}, date={self.operation_date}, amount={self.amount}, direction={self.direction})>"


# ============================================================================
# OfdReceipt / Чек ОФД - OFD (Online Cash Register) receipts
# ============================================================================
class OfdReceipt(Base):
    """
    Represents a receipt from an online cash register (ОФД).
    Used to supplement or verify bank operation income data.
    """
    __tablename__ = "ofd_receipts"
    __table_args__ = (
        Index("ix_ofd_receipts_project_date", "project_id", "receipt_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    # Receipt information
    receipt_date = Column(DateTime, nullable=False)  # Дата и время чека
    amount = Column(Numeric(15, 2), nullable=False)  # Сумма чека

    # Payment and operation type
    payment_type = Column(String(20), nullable=False)  # "cash", "card", "mixed"
    operation_type = Column(String(20), nullable=False)  # "sale" или "refund"

    # Cash register details
    kkt_number = Column(String(40), nullable=True)  # Номер ККТ (кассового аппарата)
    receipt_number = Column(String(50), nullable=True)  # Номер чека
    fn = Column(String(40), nullable=True)  # Фискальный накопитель (Fiscal Drive)
    fd = Column(String(40), nullable=True)  # Фискальный документ (Fiscal Document number)
    fpd = Column(String(40), nullable=True)  # Фискальный признак документа (Fiscal attribute)

    # Point of sale
    point_of_sale = Column(String(255), nullable=True)  # Место продажи

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="ofd_receipts")

    def __repr__(self):
        return f"<OfdReceipt(id={self.id}, date={self.receipt_date}, amount={self.amount})>"


# ============================================================================
# InsuranceContribution / Страховые взносы - Tax contributions
# ============================================================================
class InsuranceContribution(Base):
    """
    Represents insurance contributions (страховые взносы).
    In УСН 6%, these can reduce the tax liability up to 100%.
    """
    __tablename__ = "insurance_contributions"
    __table_args__ = (
        Index("ix_insurance_contributions_project_date", "project_id", "payment_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    # Contribution details
    contribution_type = Column(String(50), nullable=False)  # "fixed_ip", "one_percent", "employee_insurance", "total"
    amount = Column(Numeric(15, 2), nullable=False)  # Сумма взносов
    payment_date = Column(Date, nullable=True)  # Дата оплаты
    document_number = Column(String(50), nullable=True)  # Номер документа оплаты

    # Status
    status = Column(String(50), default="accepted")  # "accepted", "rejected", "disputed"
    comment = Column(String(500), nullable=True)  # Комментарий

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="insurance_contributions")

    def __repr__(self):
        return f"<InsuranceContribution(id={self.id}, type={self.contribution_type}, amount={self.amount})>"


# ============================================================================
# TaxCalculation / Расчёт налога - Tax calculations by period
# ============================================================================
class TaxCalculation(Base):
    """
    Represents tax calculation results for a specific period.
    Stores intermediate calculations and final tax due.
    """
    __tablename__ = "tax_calculations"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    # Period
    period = Column(String(20), nullable=False)  # "q1", "half_year", "nine_months", "year"

    # Income
    income_cumulative = Column(Numeric(15, 2), nullable=False)  # Накопленный доход за период

    # Tax calculation
    tax_calculated = Column(Numeric(15, 2), nullable=False)  # Рассчитанный налог (доход * ставка)

    # Contributions
    contributions_applied = Column(Numeric(15, 2), nullable=False)  # Применённые взносы
    contribution_limit = Column(Numeric(15, 2), nullable=False)  # Лимит взносов (до 100% налога)

    # Final tax
    tax_after_reduction = Column(Numeric(15, 2), nullable=False)  # Налог после снижения взносами
    advance_paid = Column(Numeric(15, 2), default=0)  # Авансовые платежи
    tax_due = Column(Numeric(15, 2), nullable=False)  # Налог к уплате

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="tax_calculations")

    def __repr__(self):
        return f"<TaxCalculation(id={self.id}, period={self.period}, tax_due={self.tax_due})>"


# ============================================================================
# ClassificationRule / Правило классификации - Auto-classification rules
# ============================================================================
class ClassificationRule(Base):
    """
    Represents rules for automatic classification of bank operations.
    Can be global (project_id=NULL) or project-specific.
    """
    __tablename__ = "classification_rules"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)  # NULL = global rule

    # Rule definition
    rule_type = Column(String(50), nullable=False)  # "keyword_income", "keyword_exclude", "counterparty_income", "counterparty_exclude"
    pattern = Column(String(255), nullable=False)  # Шаблон (ключевое слово или имя)
    description = Column(String(500), nullable=True)  # Описание правила

    # Status
    is_active = Column(Boolean, default=True)  # Активно ли правило

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="classification_rules")

    def __repr__(self):
        return f"<ClassificationRule(id={self.id}, type={self.rule_type}, pattern={self.pattern})>"


# ============================================================================
# AuditLog / Журнал аудита - Audit trail for all changes
# ============================================================================
class AuditLog(Base):
    """
    Audit log for tracking changes to important entities.
    Provides a complete history of modifications.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_project_date", "project_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    # Action information
    action = Column(String(50), nullable=False)  # "create", "update", "delete", "reclassify"
    entity_type = Column(String(50), nullable=False)  # "project", "bank_operation", "tax_calculation", etc.
    entity_id = Column(Integer, nullable=True)  # ID of the affected entity

    # Change tracking
    old_value = Column(String(500), nullable=True)  # Старое значение
    new_value = Column(String(500), nullable=True)  # Новое значение

    # User information
    user_comment = Column(String(500), nullable=True)  # Комментарий пользователя

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog(id={self.id}, action={self.action}, entity_type={self.entity_type})>"
