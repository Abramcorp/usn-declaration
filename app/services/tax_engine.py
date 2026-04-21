"""
Tax calculation engine for Russian ИП on УСН 6% (Доходы).

Версия 2.0 — исправленные формулы согласно декларации КНД 1152017.

Ключевые принципы:
- Все квартальные расчёты ведутся нарастающим итогом
- Налог = накопленный_доход × ставка
- Уменьшение налога на взносы:
    * без работников — до 100% от налога
    * с работниками — до 50% от налога
- 1% сверх 300 000 ₽ (ограниченный max_1pct для конкретного года)
- Декларация заполняется в целых рублях (округление ROUND_HALF_UP)
- Расчётные авансы (строки 020/040/070) используются для формирования
  стр. 100/110 (итог к уплате/уменьшению), а не фактически уплаченные
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Optional, List, Dict
from enum import Enum

from app.services.contribution_calculator import get_rates


def _to_rub(value) -> Decimal:
    """Округление до целого рубля (ROUND_HALF_UP) — как в декларации."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _to_kop(value) -> Decimal:
    """Округление до копеек (для промежуточных расчётов)."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ContributionMode(Enum):
    TOTAL = "total"
    DETAILED = "detailed"


class TaxEngine:
    """
    Tax calculation engine for USN 6% (Доходы).
    """

    ONE_PERCENT_THRESHOLD = Decimal("300000")
    PERIODS = ["q1", "q2", "q3", "q4"]
    CUMULATIVE_PERIODS = ["q1", "half_year", "nine_months", "year"]

    def __init__(self, project_settings: dict):
        self.tax_rate = Decimal(str(project_settings.get("tax_rate", "6.0")))
        self.has_employees = project_settings.get("has_employees", False)
        self.employee_start_quarter = project_settings.get("employee_start_quarter")
        self.uses_ens = project_settings.get("uses_ens", False)
        self.year = int(project_settings.get("year", 0)) or None

        contribution_mode = project_settings.get("contribution_input_mode", "total")
        self.contribution_mode = ContributionMode(contribution_mode)
        self._validate_settings()

    def _validate_settings(self):
        if not (Decimal("1") <= self.tax_rate <= Decimal("100")):
            raise ValueError(f"Invalid tax rate: {self.tax_rate}")
        if self.employee_start_quarter is not None:
            if not (1 <= self.employee_start_quarter <= 4):
                raise ValueError(f"Invalid employee_start_quarter: {self.employee_start_quarter}")

    # ---------------------------------------------------------------------
    # MAIN CALCULATION
    # ---------------------------------------------------------------------
    def calculate(
        self,
        income_data: dict,
        contributions: dict,
        advances_paid: Optional[dict] = None,
    ) -> dict:
        """
        Основной метод расчёта.

        Args:
            income_data: {"q1": Decimal, "q2": Decimal, "q3": Decimal, "q4": Decimal}
            contributions: взносы (см. _distribute_contributions)
            advances_paid: фактически уплаченные авансы (информация для сверки,
                            в расчёт авансов декларации НЕ входит).
        """
        advances_paid = advances_paid or {}
        income_data = {k: _to_kop(v) for k, v in income_data.items()}

        cumulative_income = self._get_cumulative_income_map(income_data)
        total_income = cumulative_income["year"]

        # 1% сверх 300 000 — по году с лимитом из справочника
        one_percent = self.calculate_one_percent(total_income)

        # Распределение взносов по периодам (по факту уплаты, либо равномерно)
        contrib_distribution = self._distribute_contributions(contributions, one_percent)

        # Расчёт по каждому периоду нарастающим итогом
        periods_result = {}
        # Расчётные авансы (по декларации), сохраняем для следующего периода
        calculated_advances = {"q1": Decimal("0"), "half_year": Decimal("0"), "nine_months": Decimal("0")}

        for idx, period in enumerate(self.CUMULATIVE_PERIODS):
            income_cum = cumulative_income[period]

            # Налог нарастающим итогом (в рублях — как в декларации)
            tax_calculated = _to_rub(income_cum * self.tax_rate / Decimal("100"))

            has_employees_in_period = self._has_employees_in_period(period)

            # Доступные взносы нарастающим итогом
            contributions_available = contrib_distribution.get(period, Decimal("0"))

            # Лимит уменьшения (50% или 100% от налога)
            reduction_limit = self._get_reduction_limit(tax_calculated, has_employees_in_period)

            # Фактически применено взносов (не больше лимита и не больше доступных)
            contributions_applied = min(contributions_available, reduction_limit)

            # Налог после уменьшения (не может быть отрицательным)
            tax_after_reduction = max(tax_calculated - contributions_applied, Decimal("0"))

            # ===== Расчётный аванс к уплате за период =====
            # Q1:        аванс = tax_after_reduction_q1                       (стр. 020)
            # Полуг.:    аванс = tax_after_reduction_hy - аванс_Q1            (стр. 040/050)
            # 9 мес.:    аванс = tax_after_reduction_9m - Q1 - HY             (стр. 070/080)
            # Год:       к уплате = tax_after_reduction_year - все авансы     (стр. 100/110)
            if period == "q1":
                advance_due = tax_after_reduction
                calculated_advances["q1"] = advance_due
            elif period == "half_year":
                # За полугодие — разница (может быть отрицательной: «к уменьшению»)
                advance_due = tax_after_reduction - calculated_advances["q1"]
                calculated_advances["half_year"] = advance_due
            elif period == "nine_months":
                advance_due = (
                    tax_after_reduction
                    - calculated_advances["q1"]
                    - calculated_advances["half_year"]
                )
                calculated_advances["nine_months"] = advance_due
            else:  # year
                advance_due = (
                    tax_after_reduction
                    - calculated_advances["q1"]
                    - calculated_advances["half_year"]
                    - calculated_advances["nine_months"]
                )

            periods_result[period] = {
                "income_period": income_data.get(period, Decimal("0")),
                "income_cumulative": income_cum,
                "tax_calculated": tax_calculated,
                "contributions_available": contributions_available,
                "contribution_limit": reduction_limit,
                "contributions_applied": contributions_applied,
                "tax_after_reduction": tax_after_reduction,
                # Расчётный аванс (для стр. 020/040/050/070/080 декларации)
                "advance_due": advance_due,
                # Фактически уплаченный (для сверки)
                "actual_paid": _to_kop(advances_paid.get(period, 0)),
                "has_employees": has_employees_in_period,
                "reduction_percent": 100 if not has_employees_in_period else 50,
            }

        # ===== Сводка =====
        year_data = periods_result["year"]
        total_advances_calc = (
            calculated_advances["q1"]
            + calculated_advances["half_year"]
            + calculated_advances["nine_months"]
        )

        # Итог года (стр. 100 / 110)
        final_line_100 = max(year_data["advance_due"], Decimal("0"))     # к уплате
        final_line_110 = max(-year_data["advance_due"], Decimal("0"))    # к уменьшению

        summary = {
            "total_income": total_income,
            "total_tax_calculated": year_data["tax_calculated"],
            "total_tax_after_reduction": year_data["tax_after_reduction"],
            "total_contributions_applied": year_data["contributions_applied"],
            "total_advances_due_calc": total_advances_calc,
            "total_actual_paid": sum(
                _to_kop(advances_paid.get(p, 0))
                for p in self.CUMULATIVE_PERIODS
            ),
            "final_tax_due": final_line_100,        # стр. 100
            "overpayment": final_line_110,          # стр. 110
            "one_percent_calculated": one_percent,
        }

        return {
            "periods": periods_result,
            "calculated_advances": calculated_advances,
            "summary": summary,
            "one_percent_calculated": one_percent,
            "warnings": self._validate_inputs(income_data, contributions, advances_paid),
        }

    # ---------------------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------------------
    def _get_cumulative_income_map(self, income_data: dict) -> Dict[str, Decimal]:
        q1 = income_data.get("q1", Decimal("0"))
        q2 = income_data.get("q2", Decimal("0"))
        q3 = income_data.get("q3", Decimal("0"))
        q4 = income_data.get("q4", Decimal("0"))
        return {
            "q1": q1,
            "half_year": q1 + q2,
            "nine_months": q1 + q2 + q3,
            "year": q1 + q2 + q3 + q4,
        }

    def _has_employees_in_period(self, period: str) -> bool:
        if not self.has_employees:
            return False
        if self.employee_start_quarter is None:
            return True
        if period == "q1":
            return self.employee_start_quarter <= 1
        if period == "half_year":
            return self.employee_start_quarter <= 2
        if period == "nine_months":
            return self.employee_start_quarter <= 3
        return True  # year

    def _get_reduction_limit(self, tax: Decimal, has_employees: bool) -> Decimal:
        if not has_employees:
            return tax
        return _to_rub(tax * Decimal("50") / Decimal("100"))

    def _distribute_contributions(
        self,
        contributions: dict,
        one_percent_calculated: Decimal,
    ) -> Dict[str, Decimal]:
        """
        Распределение накопленных взносов по периодам (нарастающим итогом).

        Поддерживаются 2 формата входа:
          1. "quarterly": {"q1": ..., "q2": ..., "q3": ..., "q4": ...} — факт.
             уплата по кварталам (приоритетный, если передан).
          2. Иначе используем фикс.взносы + 1% + взносы сотрудников — по
             правилу начисления: фикс.взносы распределяются поквартально,
             1% — относим в год (Q4), т.к. срок уплаты 01.07 следующего года.
        """
        result = {"q1": Decimal("0"), "half_year": Decimal("0"), "nine_months": Decimal("0"), "year": Decimal("0")}

        # Формат 1: явные поквартальные суммы
        quarterly = contributions.get("quarterly")
        if quarterly:
            q1 = _to_kop(quarterly.get("q1", 0))
            q2 = _to_kop(quarterly.get("q2", 0))
            q3 = _to_kop(quarterly.get("q3", 0))
            q4 = _to_kop(quarterly.get("q4", 0))
            result["q1"] = q1
            result["half_year"] = q1 + q2
            result["nine_months"] = q1 + q2 + q3
            result["year"] = q1 + q2 + q3 + q4
            return result

        # Формат 2: total (фикс + сотрудники + 1%)
        fixed_ip = _to_kop(contributions.get("fixed_ip", 0))
        # Если явно передан one_percent > 0 — берём его, иначе авто-расчёт
        explicit_1pct = _to_kop(contributions.get("one_percent", 0))
        one_percent = explicit_1pct if explicit_1pct > 0 else _to_kop(one_percent_calculated)
        employee_insurance = _to_kop(contributions.get("employee_insurance", 0))
        total_amount = _to_kop(contributions.get("total_amount", 0))

        # Если указан total_amount — распределяем равномерно
        if total_amount > 0 and fixed_ip == 0 and employee_insurance == 0:
            per_quarter = _to_kop(total_amount / Decimal("4"))
            result["q1"] = per_quarter
            result["half_year"] = per_quarter * Decimal("2")
            result["nine_months"] = per_quarter * Decimal("3")
            result["year"] = total_amount
            return result

        # Фикс. взносы по кварталам (равномерно, начисление)
        fixed_per_q = _to_kop(fixed_ip / Decimal("4"))
        employee_per_q = _to_kop(employee_insurance / Decimal("4"))
        # 1% — в годовой период (срок уплаты 01.07 следующего года,
        # но с 2023 можно учитывать за год начисления)
        result["q1"] = fixed_per_q + employee_per_q
        result["half_year"] = (fixed_per_q + employee_per_q) * Decimal("2")
        result["nine_months"] = (fixed_per_q + employee_per_q) * Decimal("3")
        result["year"] = fixed_ip + employee_insurance + one_percent
        return result

    def calculate_one_percent(self, total_income: Decimal) -> Decimal:
        """1% сверх 300 000 ₽, с лимитом для конкретного года."""
        if total_income <= self.ONE_PERCENT_THRESHOLD:
            return Decimal("0")
        amt = _to_rub((total_income - self.ONE_PERCENT_THRESHOLD) * Decimal("0.01"))
        # Лимит из справочника
        if self.year:
            rates = get_rates(self.year)
            cap = rates.get("max_1pct")
            if cap:
                amt = min(amt, _to_rub(cap))
        return amt

    def _validate_inputs(self, income_data: dict, contributions: dict, advances_paid: dict) -> List[str]:
        warnings = []
        for q, v in income_data.items():
            if v < Decimal("0"):
                warnings.append(f"Отрицательный доход за {q}: {v}")
        return warnings

    # ---------------------------------------------------------------------
    # DECLARATION DATA (КНД 1152017)
    # ---------------------------------------------------------------------
    def get_declaration_data(self, calculation_result: dict, project_settings: dict) -> dict:
        """
        Формирование данных для декларации КНД 1152017 (разделы 1.1 и 2.1.1).
        Все суммы — в целых рублях.
        """
        periods = calculation_result["periods"]
        advances = calculation_result["calculated_advances"]
        summary = calculation_result["summary"]

        # Ставка в сотых долях процента (60 = 6,0% в форме декларации)
        rate_x10 = _to_rub(self.tax_rate * Decimal("10"))

        section_2_1_1 = {
            # Код признака применения ставки (стр. 101): 1 — стандарт.
            "line_101": 1,
            # Признак налогоплательщика (стр. 102): 1 — с выплатами, 2 — без.
            "line_102": 1 if self.has_employees else 2,
            # Доходы нарастающим итогом
            "line_110": _to_rub(periods["q1"]["income_cumulative"]),
            "line_111": _to_rub(periods["half_year"]["income_cumulative"]),
            "line_112": _to_rub(periods["nine_months"]["income_cumulative"]),
            "line_113": _to_rub(periods["year"]["income_cumulative"]),
            # Ставка налога (×10): например 60 = 6,0 %
            "line_120": rate_x10,
            "line_121": rate_x10,
            "line_122": rate_x10,
            "line_123": rate_x10,
            # Сумма исчисленного налога (авансового платежа)
            "line_130": _to_rub(periods["q1"]["tax_calculated"]),
            "line_131": _to_rub(periods["half_year"]["tax_calculated"]),
            "line_132": _to_rub(periods["nine_months"]["tax_calculated"]),
            "line_133": _to_rub(periods["year"]["tax_calculated"]),
            # Сумма страховых взносов, уменьшающих налог
            "line_140": _to_rub(periods["q1"]["contributions_applied"]),
            "line_141": _to_rub(periods["half_year"]["contributions_applied"]),
            "line_142": _to_rub(periods["nine_months"]["contributions_applied"]),
            "line_143": _to_rub(periods["year"]["contributions_applied"]),
        }

        # Раздел 1.1 — авансы и итог
        q1_due = advances["q1"]
        hy_due = advances["half_year"]
        nm_due = advances["nine_months"]

        section_1_1 = {
            # ОКТМО — заполняется из проекта
            "line_010": project_settings.get("oktmo", ""),
            # Аванс Q1 (стр. 020)
            "line_020": _to_rub(max(q1_due, Decimal("0"))),
            # ОКТМО при изменении — обычно дублирует 010
            "line_030": project_settings.get("oktmo", ""),
            # Полугодие: стр. 040 (к уплате) или стр. 050 (к уменьшению)
            "line_040": _to_rub(max(hy_due, Decimal("0"))),
            "line_050": _to_rub(max(-hy_due, Decimal("0"))),
            "line_060": project_settings.get("oktmo", ""),
            # 9 месяцев: стр. 070 (к уплате) или стр. 080 (к уменьшению)
            "line_070": _to_rub(max(nm_due, Decimal("0"))),
            "line_080": _to_rub(max(-nm_due, Decimal("0"))),
            "line_090": project_settings.get("oktmo", ""),
            # Год: стр. 100 (к уплате) или стр. 110 (к уменьшению)
            "line_100": _to_rub(summary["final_tax_due"]),
            "line_110": _to_rub(summary["overpayment"]),
        }

        return {
            "section_2_1_1": section_2_1_1,
            "section_1_1": section_1_1,
            "summary": {
                "total_income": _to_rub(summary["total_income"]),
                "total_tax_calculated": _to_rub(summary["total_tax_calculated"]),
                "total_tax_after_reduction": _to_rub(summary["total_tax_after_reduction"]),
                "total_contributions_applied": _to_rub(summary["total_contributions_applied"]),
                "total_advances_due_calc": _to_rub(summary["total_advances_due_calc"]),
                "final_tax_due": _to_rub(summary["final_tax_due"]),
                "overpayment": _to_rub(summary["overpayment"]),
            },
            "one_percent_calculated": _to_rub(calculation_result["one_percent_calculated"]),
            "settings": {
                "has_employees": self.has_employees,
                "employee_start_quarter": self.employee_start_quarter,
                "uses_ens": self.uses_ens,
                "tax_rate": self.tax_rate,
                "year": self.year,
            },
        }
