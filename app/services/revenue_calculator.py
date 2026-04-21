"""
Revenue calculator for ИП УСН 6%.

Deduplication rule (enforced by user):
    - From OFD we take ONLY cash (Наличные) revenue.
    - From the bank statement we take ALL income operations (acquiring card
      payments + everything else).
    This guarantees card sales are not double-counted:
       * card sales live in bank (acquiring deposits) → counted
       * card sales also in OFD as "Безналичные" → IGNORED
       * cash sales live in OFD as "Наличные" → counted
       * cash sales are never in the bank statement → nothing to ignore
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models import BankOperation, OfdReceipt, Project


def _quarter_bounds(year: int) -> Dict[str, tuple]:
    return {
        "q1":      (date(year, 1, 1),  date(year, 3, 31)),
        "q2":      (date(year, 4, 1),  date(year, 6, 30)),
        "q3":      (date(year, 7, 1),  date(year, 9, 30)),
        "q4":      (date(year, 10, 1), date(year, 12, 31)),
    }


def compute_quarterly_revenue(db: Session, project_id: int) -> Dict[str, Any]:
    """
    Compute revenue broken down by quarter, with cumulative period totals
    (q1, half_year, nine_months, year) — the four values needed for УСН declaration.

    Returns a dict like:
        {
          "bank": {"q1": ..., "q2": ..., "q3": ..., "q4": ..., "total": ...},
          "ofd_cash": {"q1": ..., "q2": ..., "q3": ..., "q4": ..., "total": ...},
          "total": {"q1": ..., "q2": ..., "q3": ..., "q4": ..., "total": ...},
          "cumulative": {
             "q1":         <доход за 1 квартал>,
             "half_year":  <доход за полугодие>,
             "nine_months":<доход за 9 месяцев>,
             "year":       <доход за год>,
          },
          "year": <int>,
          "has_ofd": <bool>,
        }
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError(f"Проект {project_id} не найден")

    year = project.tax_period_year
    bounds = _quarter_bounds(year)

    # --- Bank income (all included_in_tax_base deposits) -----------------
    bank_by_q = {}
    for q, (d0, d1) in bounds.items():
        amt = (
            db.query(func.coalesce(func.sum(BankOperation.amount), 0))
            .filter(
                BankOperation.project_id == project_id,
                BankOperation.direction == "income",
                BankOperation.included_in_tax_base.is_(True),
                BankOperation.operation_date >= d0,
                BankOperation.operation_date <= d1,
            )
            .scalar()
        )
        bank_by_q[q] = Decimal(amt or 0)
    bank_by_q["total"] = sum(bank_by_q.values(), Decimal("0"))

    # --- OFD cash net (sale - refund, cash only) -------------------------
    ofd_by_q = {}
    has_ofd = False
    total_ofd_rows = (
        db.query(func.count(OfdReceipt.id))
        .filter(OfdReceipt.project_id == project_id)
        .scalar()
        or 0
    )
    if total_ofd_rows:
        has_ofd = True

    for q, (d0, d1) in bounds.items():
        sale = (
            db.query(func.coalesce(func.sum(OfdReceipt.amount), 0))
            .filter(
                OfdReceipt.project_id == project_id,
                OfdReceipt.payment_type == "cash",
                OfdReceipt.operation_type == "sale",
                func.date(OfdReceipt.receipt_date) >= d0,
                func.date(OfdReceipt.receipt_date) <= d1,
            )
            .scalar()
        )
        refund = (
            db.query(func.coalesce(func.sum(OfdReceipt.amount), 0))
            .filter(
                OfdReceipt.project_id == project_id,
                OfdReceipt.payment_type == "cash",
                OfdReceipt.operation_type == "refund",
                func.date(OfdReceipt.receipt_date) >= d0,
                func.date(OfdReceipt.receipt_date) <= d1,
            )
            .scalar()
        )
        ofd_by_q[q] = Decimal(sale or 0) - Decimal(refund or 0)
    ofd_by_q["total"] = sum(v for k, v in ofd_by_q.items() if k != "total")

    # --- Totals (bank + ofd cash) ---------------------------------------
    total_by_q = {
        q: bank_by_q[q] + ofd_by_q[q] for q in ("q1", "q2", "q3", "q4")
    }
    total_by_q["total"] = sum(total_by_q.values(), Decimal("0"))

    # --- Cumulative (for ФНС periods) -----------------------------------
    cum = {
        "q1":          total_by_q["q1"],
        "half_year":   total_by_q["q1"] + total_by_q["q2"],
        "nine_months": total_by_q["q1"] + total_by_q["q2"] + total_by_q["q3"],
        "year":        total_by_q["total"],
    }

    return {
        "bank":        {k: float(v) for k, v in bank_by_q.items()},
        "ofd_cash":    {k: float(v) for k, v in ofd_by_q.items()},
        "total":       {k: float(v) for k, v in total_by_q.items()},
        "cumulative":  {k: float(v) for k, v in cum.items()},
        "year":        year,
        "has_ofd":     has_ofd,
    }


def _round_rub(val):
    """Округление до целых рублей по п. 6 ст. 52 НК РФ."""
    from app.services.utils import round_rub
    return round_rub(val)


def compute_tax_by_period(
    cumulative_income: Dict[str, float],
    rate: float,
    contributions: Dict[str, float],
) -> Dict[str, Dict[str, float]]:
    """
    Compute УСН 6 % tax for each period.

    contributions: dict with keys 'q1','half_year','nine_months','year' —
    paid insurance contributions accumulated to the end of the period
    (ИП + at most 50% in case of employees is applied by the caller).

    All amounts are rounded to whole rubles per п. 6 ст. 52 НК РФ.

    Returns:
        { 'q1':          {'income': .., 'tax': .., 'reduction': .., 'payable': ..},
          'half_year':   {...},
          'nine_months': {...},
          'year':        {...} }
    """
    out = {}
    for period in ("q1", "half_year", "nine_months", "year"):
        income = Decimal(str(cumulative_income.get(period, 0)))
        tax = _round_rub(income * Decimal(str(rate)) / Decimal("100"))
        # Округляем взносы до целых рублей (п. 6 ст. 52 НК РФ)
        raw_reduction = _round_rub(contributions.get(period, 0))
        reduction = min(raw_reduction, tax)  # вычет не может превышать налог
        payable = tax - reduction
        out[period] = {
            "income":    int(income),
            "tax":       int(tax),
            "reduction": int(reduction),
            "payable":   int(payable),
        }
    return out
