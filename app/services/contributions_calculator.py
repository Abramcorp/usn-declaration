"""
Калькулятор страховых взносов ИП на УСН 6%.

Рассчитывает:
  1. Фиксированные взносы ИП за себя (ст. 430 НК РФ)
  2. 1% с доходов свыше 300 000₽
  3. Взносы за работников по тарифу МСП (ст. 427 НК РФ)
     - 30% на часть зарплаты ≤ МРОТ
     - 15% на часть зарплаты > МРОТ
  4. Учёт предельной базы (единый тариф с 2023)

Все суммы округляются до целых рублей (п. 6 ст. 52 НК РФ).
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional

from app.services.utils import round_rub as _round_rub


# ─── Константы по годам ─────────────────────────────────────────

# Фиксированные взносы ИП за себя (совокупный фиксированный платёж)
FIXED_IP = {
    2021: 40874, 2022: 43211, 2023: 45842,
    2024: 49500, 2025: 53658, 2026: 57390, 2027: 61154,
}

# Максимум 1% сверх 300к (по п. 1.2 ст. 430 НК РФ: 8 × взнос_на_ОПС − взнос_на_ОПС)
# ВАЖНО: считается от взноса на ОПС, а не от совокупного платежа!
MAX_1PCT = {
    2021: 227136,   # 8 × 32448 - 32448
    2022: 245906,   # 8 × 34445 - 34445 (фактический max по НК)
    2023: 257061,   # по п. 1.2 ст. 430 НК (переходный, утверждён в законе)
    2024: 277571,   # утверждено
    2025: 300888,   # утверждено
    2026: 321818,   # утверждено (ОПС 45924, max = 7 × 45974 ≈ 321818)
    2027: 342923,   # утверждено
}

# МРОТ по годам (с 1 января)
MROT = {
    2023: 16242, 2024: 19242, 2025: 22440, 2026: 25000, 2027: 27000,
}

# Предельная база для единого тарифа (с 2023 — единая для ОПС+ВНиМ)
PREDEL_BAZA = {
    2023: 1917000, 2024: 2225000, 2025: 2759000, 2026: 3200000,
}

# Единый тариф с 2023:
#   до предельной базы: 30% (для МСП: 30% на ≤МРОТ, 15% на >МРОТ)
#   сверх предельной базы: 15.1% (для МСП: 15.1% на ≤МРОТ, 0% на >МРОТ... нет)
# Упрощённая схема МСП с 2023:
#   ≤ МРОТ: 30% до предела, 15.1% сверх предела
#   > МРОТ: 15% до предела, 0% сверх предела... нет, не так
#
# ТОЧНАЯ ФОРМУЛА МСП (п. 2.4 ст. 427 НК РФ):
#   Часть зарплаты ≤ МРОТ → обычный единый тариф (30% / 15.1%)
#   Часть зарплаты > МРОТ → пониженный тариф (15% / 0%)
#   Предельная база применяется к ОБЩЕЙ сумме выплат работнику

# Ставки
RATE_STANDARD = Decimal("0.30")         # 30% до предельной базы
RATE_ABOVE_LIMIT = Decimal("0.151")     # 15.1% сверх предельной базы
RATE_MSP_LOW = Decimal("0.15")          # 15% МСП на часть > МРОТ (до предела)
# Сверх предельной базы для МСП: 0% на часть > МРОТ
RATE_MSP_ABOVE_LIMIT = Decimal("0")

# Тариф НС и ПЗ (несчастные случаи) — по умолчанию 1 класс = 0.2%
RATE_NS = Decimal("0.002")


def compute_employee_contributions(
    avg_salary: float,
    num_employees: int,
    year: int,
    tariff: str = "msp",  # "msp" или "standard"
    ns_rate: float = 0.2,  # ставка НС в процентах (0.2% по умолчанию)
) -> Dict[str, Any]:
    """
    Рассчитать взносы за работников поквартально (нарастающим итогом).

    Args:
        avg_salary: средняя зарплата одного работника в месяц
        num_employees: количество работников
        year: налоговый год
        tariff: "msp" (МСП) или "standard" (30%)
        ns_rate: ставка взносов на травматизм, % (по умолчанию 0.2)

    Returns:
        {
            "monthly_detail": [...],  # помесячная расшифровка
            "quarterly": {"q1": ..., "q2": ..., "q3": ..., "q4": ...},
            "cumulative": {"q1": ..., "half_year": ..., "nine_months": ..., "year": ...},
            "total_year": int,
            "params": {...}
        }
    """
    salary = Decimal(str(avg_salary))
    n = num_employees
    mrot = Decimal(str(MROT.get(year, 22440)))
    predel = Decimal(str(PREDEL_BAZA.get(year, 2759000)))
    ns = Decimal(str(ns_rate)) / Decimal("100")
    is_msp = (tariff == "msp")

    monthly_detail = []
    # Накопленная база по каждому работнику (предполагаем одинаковую зарплату)
    cumulative_base_per_worker = Decimal("0")

    quarterly_totals = {"q1": 0, "q2": 0, "q3": 0, "q4": 0}
    quarter_names = ["q1", "q1", "q1", "q2", "q2", "q2", "q3", "q3", "q3", "q4", "q4", "q4"]
    month_names = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                   "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

    for month_idx in range(12):
        prev_base = cumulative_base_per_worker
        cumulative_base_per_worker += salary

        # Определяем, какая часть зарплаты попадает до и после предельной базы
        if prev_base >= predel:
            # Вся зарплата сверх предельной базы
            below_limit = Decimal("0")
            above_limit = salary
        elif cumulative_base_per_worker > predel:
            # Частично до предела, частично сверх
            below_limit = predel - prev_base
            above_limit = cumulative_base_per_worker - predel
        else:
            # Вся зарплата в пределах базы
            below_limit = salary
            above_limit = Decimal("0")

        if is_msp:
            # МСП: разделяем на ≤МРОТ и >МРОТ
            mrot_part = min(salary, mrot)  # часть ≤ МРОТ
            above_mrot = max(Decimal("0"), salary - mrot)  # часть > МРОТ

            # Часть ≤ МРОТ — обычный тариф
            if prev_base >= predel:
                # Всё сверх предела: МРОТ-часть по 15.1%, выше-МРОТ по 0%
                contrib_mrot = _round_rub(mrot_part * RATE_ABOVE_LIMIT * n)
                contrib_above_mrot = 0
            elif prev_base + mrot_part > predel:
                # МРОТ-часть частично до предела, частично сверх
                mrot_below = predel - prev_base
                mrot_above = mrot_part - mrot_below
                contrib_mrot = _round_rub(
                    (mrot_below * RATE_STANDARD + mrot_above * RATE_ABOVE_LIMIT) * n
                )
                # Выше-МРОТ часть вся сверх предела → 0%
                contrib_above_mrot = 0
            else:
                # МРОТ-часть целиком до предела: 30%
                contrib_mrot = _round_rub(mrot_part * RATE_STANDARD * n)
                # Выше-МРОТ часть
                remaining_to_limit = predel - prev_base - mrot_part
                if above_mrot <= remaining_to_limit:
                    # Вся выше-МРОТ до предела: 15%
                    contrib_above_mrot = _round_rub(above_mrot * RATE_MSP_LOW * n)
                elif remaining_to_limit > 0:
                    # Частично до предела: 15%, остальное: 0%
                    contrib_above_mrot = _round_rub(remaining_to_limit * RATE_MSP_LOW * n)
                else:
                    # Выше-МРОТ вся сверх предела: 0%
                    contrib_above_mrot = 0

            contrib_main = contrib_mrot + contrib_above_mrot
        else:
            # Стандартный тариф 30%
            contrib_below = _round_rub(below_limit * RATE_STANDARD * n)
            contrib_above = _round_rub(above_limit * RATE_ABOVE_LIMIT * n)
            contrib_main = contrib_below + contrib_above

        # НС и ПЗ (не зависит от предельной базы, начисляется на всю зарплату)
        contrib_ns = _round_rub(salary * ns * n)

        total_month = contrib_main + contrib_ns
        q = quarter_names[month_idx]
        quarterly_totals[q] += total_month

        monthly_detail.append({
            "month": month_names[month_idx],
            "salary": int(salary),
            "cumulative_base": int(cumulative_base_per_worker),
            "above_limit": cumulative_base_per_worker > predel,
            "contributions": total_month,
            "contrib_main": contrib_main,
            "contrib_ns": contrib_ns,
        })

    # Нарастающий итог для декларации
    cumulative = {
        "q1":          quarterly_totals["q1"],
        "half_year":   quarterly_totals["q1"] + quarterly_totals["q2"],
        "nine_months": quarterly_totals["q1"] + quarterly_totals["q2"] + quarterly_totals["q3"],
        "year":        sum(quarterly_totals.values()),
    }

    return {
        "monthly_detail": monthly_detail,
        "quarterly": quarterly_totals,
        "cumulative": cumulative,
        "total_year": sum(quarterly_totals.values()),
        "params": {
            "avg_salary": int(salary),
            "num_employees": n,
            "tariff": tariff,
            "mrot": int(mrot),
            "predel_baza": int(predel),
            "ns_rate": float(ns),
            "year": year,
        },
    }


def compute_total_contributions(
    year: int,
    year_income: float,
    has_employees: bool = False,
    avg_salary: float = 0,
    num_employees: int = 0,
    tariff: str = "msp",
    ns_rate: float = 0.2,
) -> Dict[str, Any]:
    """
    Рассчитать ВСЕ страховые взносы (ИП за себя + за работников)
    нарастающим итогом по периодам декларации.

    Returns:
        {
            "ip_fixed": int,           # фикс. взносы ИП
            "ip_1pct": int,            # 1% сверх 300к
            "employee_total": int,     # взносы за работников (год)
            "employee_cumulative": {...},  # нарастающий итог за работников
            "total_cumulative": {...},     # ИТОГО нарастающим итогом (для строк 140-143)
            "contributions_430": {...},    # для строк 150, 160, 161, 162
        }
    """
    fixed = FIXED_IP.get(year, 53658)

    # 1% с доходов свыше 300к (п. 1.2 ст. 430 НК РФ)
    extra_1pct_raw = max(0, (float(year_income) - 300000) * 0.01)
    max_extra = MAX_1PCT.get(year, 300888)  # макс по справочнику (от ОПС, не от совокупного!)
    extra_1pct = min(extra_1pct_raw, max_extra)
    ip_1pct = _round_rub(extra_1pct)

    # ИП фикс. часть: равномерно по кварталам
    ip_q = Decimal(str(fixed)) / 4
    ip_cumulative = {
        "q1":          _round_rub(ip_q),
        "half_year":   _round_rub(ip_q * 2),
        "nine_months": _round_rub(ip_q * 3),
        "year":        fixed + ip_1pct,  # в годовой итог включаем 1%
    }

    # Взносы за работников
    emp_cumulative = {"q1": 0, "half_year": 0, "nine_months": 0, "year": 0}
    emp_detail = None
    if has_employees and num_employees > 0 and avg_salary > 0:
        emp = compute_employee_contributions(
            avg_salary=avg_salary,
            num_employees=num_employees,
            year=year,
            tariff=tariff,
            ns_rate=ns_rate,
        )
        emp_cumulative = emp["cumulative"]
        emp_detail = emp

    # Итого нарастающим итогом (для строк 140-143 декларации)
    total_cumulative = {
        "q1":          ip_cumulative["q1"] + emp_cumulative["q1"],
        "half_year":   ip_cumulative["half_year"] + emp_cumulative["half_year"],
        "nine_months": ip_cumulative["nine_months"] + emp_cumulative["nine_months"],
        "year":        ip_cumulative["year"] + emp_cumulative["year"],
    }

    # Для строк 150-162 декларации
    contributions_430 = {
        "line_150": fixed,       # фикс. часть (совокупный фикс. платёж)
        "line_160": ip_1pct,     # 1% сверх 300к
        "line_161": ip_1pct,     # 1% за текущий год
        "line_162": 0,           # 1% за предыдущий год
    }

    return {
        "ip_fixed": fixed,
        "ip_1pct": ip_1pct,
        "ip_cumulative": ip_cumulative,
        "employee_total": emp_cumulative["year"],
        "employee_cumulative": emp_cumulative,
        "employee_detail": emp_detail,
        "total_cumulative": total_cumulative,
        "contributions_430": contributions_430,
    }
