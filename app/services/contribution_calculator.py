"""
Автоматический расчёт страховых взносов и авансовых платежей для ИП на УСН 6%.

Логика:
1. Фиксированные взносы ИП — сумма определяется по году (справочник)
2. 1% свыше 300 000 ₽ — рассчитывается от дохода
3. Авансовые платежи — рассчитываются поквартально нарастающим итогом
4. Обнаружение платежей на ЕНС — по ключевым словам в назначении платежа
"""

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple
from datetime import date


# ============================================================================
# СПРАВОЧНИК СТАВОК СТРАХОВЫХ ВЗНОСОВ ПО ГОДАМ
# ============================================================================
CONTRIBUTION_RATES = {
    2023: {
        "fixed_total": Decimal("45842"),      # Фиксированные взносы (ОПС + ОМС)
        "max_1pct": Decimal("257061"),         # Максимум 1% сверх 300т
        "income_threshold": Decimal("300000"), # Порог для 1%
    },
    2024: {
        "fixed_total": Decimal("49500"),
        "max_1pct": Decimal("277571"),
        "income_threshold": Decimal("300000"),
    },
    2025: {
        "fixed_total": Decimal("53658"),
        "max_1pct": Decimal("300888"),
        "income_threshold": Decimal("300000"),
    },
    2026: {
        "fixed_total": Decimal("57390"),       # Утверждено: ОПС 45924 + ОМС 11466
        "max_1pct": Decimal("321818"),         # Макс. сумма 1% сверх 300т
        "income_threshold": Decimal("300000"),
    },
    2027: {
        "fixed_total": Decimal("61154"),       # Утверждено
        "max_1pct": Decimal("342923"),         # Макс. сумма 1% сверх 300т
        "income_threshold": Decimal("300000"),
    },
}

# Значения по умолчанию для неизвестного года
DEFAULT_RATES = {
    "fixed_total": Decimal("49500"),
    "max_1pct": Decimal("277571"),
    "income_threshold": Decimal("300000"),
}


# ============================================================================
# КЛЮЧЕВЫЕ СЛОВА ДЛЯ ОБНАРУЖЕНИЯ ПЛАТЕЖЕЙ НА ЕНС / ФНС
# ============================================================================
ENS_KEYWORDS = [
    "енс",
    "единый налоговый счет",
    "единый налоговый счёт",
    "пополнение енс",
    "единый налоговый платеж",
    "единый налоговый платёж",
]

TAX_AUTHORITY_KEYWORDS = [
    "уфк",
    "управление федерального казначейства",
    "федеральное казначейство",
    "ифнс",
    "инспекция фнс",
    "межрайонная ифнс",
    "налоговая инспекция",
    "фнс россии",
]

TAX_PAYMENT_KEYWORDS = [
    "страховые взносы",
    "страховой взнос",
    "фиксированный взнос",
    "фиксированные взносы",
    "авансовый платеж",
    "авансовый платёж",
    "налог усн",
    "упрощённая система",
    "упрощенная система",
    "единый налог",
    "взнос опс",
    "взнос омс",
    "пенсионное страхование",
    "медицинское страхование",
    "1 процент свыше",
    "1% свыше",
    "доп. взнос",
    "дополнительный взнос",
]

# ИНН налоговых органов (наиболее частые)
TAX_AUTHORITY_INNS = [
    "7727406020",  # Казначейство России (ФНС России) — ЕНС
]

# ИНН, которые НЕ являются налоговыми (банки, операторы эквайринга)
# Сбербанк (7707083893) — эквайринг, комиссии, премии — НЕ налоговый орган
KNOWN_BANK_INNS = {
    "7707083893",  # ПАО Сбербанк
    "7710140679",  # ПАО Банк ВТБ
    "7744000302",  # АО Альфа-Банк
    "7710353606",  # АО Тинькофф Банк
    "7750005725",  # ПАО Совкомбанк
}

# Ключевые слова операций, которые НИКОГДА не являются налоговыми платежами
NOT_TAX_KEYWORDS = [
    "эквайринг",
    "мерчант",
    "зачисление средств по операциям",
    "комиссия банка",
    "комиссия за",
    "комиссия в другие банки",
    "комиссионные",
    "обслуживание счета",
    "обслуживание счёта",
    "премия",
    "поощрительные выплаты",
    "реестру",
    "инкассация",
    "кэшбэк",
    "кешбэк",
    "cashback",
    "проценты по",
    "проценты на остаток",
    "торговая выручка",
]


def get_rates(year: int) -> Dict:
    """Получить ставки взносов для указанного года."""
    return CONTRIBUTION_RATES.get(year, DEFAULT_RATES)


def calculate_fixed_contributions(year: int) -> Decimal:
    """Рассчитать фиксированные взносы ИП за год."""
    rates = get_rates(year)
    return rates["fixed_total"]


def calculate_one_percent(year: int, total_income: Decimal) -> Decimal:
    """
    Рассчитать 1% свыше 300 000 ₽.

    Args:
        year: Налоговый год
        total_income: Общий доход за год

    Returns:
        Сумма 1% взноса (0 если доход <= 300 000)
    """
    rates = get_rates(year)
    threshold = rates["income_threshold"]
    max_1pct = rates["max_1pct"]

    if total_income <= threshold:
        return Decimal("0")

    one_pct = (total_income - threshold) * Decimal("0.01")
    # Округляем до копеек
    one_pct = one_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Не больше максимума
    return min(one_pct, max_1pct)


def calculate_total_ip_contributions(year: int, total_income: Decimal) -> Dict:
    """
    Рассчитать все взносы ИП за себя.

    Returns:
        {
            "fixed": Decimal,
            "one_percent": Decimal,
            "total": Decimal,
            "rates": dict с параметрами года
        }
    """
    fixed = calculate_fixed_contributions(year)
    one_pct = calculate_one_percent(year, total_income)
    return {
        "fixed": fixed,
        "one_percent": one_pct,
        "total": fixed + one_pct,
        "rates": get_rates(year),
    }


def detect_ens_payments(operations: List[Dict]) -> List[Dict]:
    """
    Обнаружить платежи на ЕНС/ФНС в списке банковских операций.

    ВАЖНО: налоговые платежи — это ТОЛЬКО списания (direction='expense').
    Поступления (эквайринг, премии, проценты) НЕ являются налоговыми платежами.
    Банковские комиссии тоже НЕ являются налоговыми платежами.

    Args:
        operations: Список операций (словари с полями purpose, counterparty,
                    counterparty_inn, amount, operation_date, direction, id)

    Returns:
        Список обнаруженных платежей с предварительной категоризацией
    """
    detected = []

    for op in operations:
        # ============================================================
        # ФИЛЬТР 1: Только списания (расходные операции)
        # Налоговые платежи — это всегда деньги УХОДЯЩИЕ со счёта ИП
        # ============================================================
        if op.get("direction") != "expense":
            continue

        purpose = (op.get("purpose") or "").lower()
        counterparty = (op.get("counterparty") or "").lower()
        counterparty_inn = (op.get("counterparty_inn") or "").strip()

        # ============================================================
        # ФИЛЬТР 2: Исключить заведомо НЕ-налоговые операции
        # Эквайринг, банковские комиссии, премии и т.п.
        # ============================================================
        is_not_tax = False
        for kw in NOT_TAX_KEYWORDS:
            if kw in purpose:
                is_not_tax = True
                break
        if is_not_tax:
            continue

        # Если контрагент — банк (не казначейство), скорее всего это комиссия
        if counterparty_inn in KNOWN_BANK_INNS:
            continue

        # ============================================================
        # ДЕТЕКЦИЯ: проверяем ИНН, ключевые слова
        # ============================================================
        is_ens = False
        match_reason = ""
        confidence = 0.0

        # Проверка по ИНН контрагента (Казначейство)
        if counterparty_inn in TAX_AUTHORITY_INNS:
            is_ens = True
            match_reason = f"ИНН контрагента ({counterparty_inn})"
            confidence = 0.95

        # Проверка по ключевым словам ЕНС в назначении
        if not is_ens:
            for kw in ENS_KEYWORDS:
                if kw in purpose:
                    is_ens = True
                    match_reason = f"Назначение содержит '{kw}'"
                    confidence = 0.90
                    break

        # Проверка по контрагенту (УФК, ФНС)
        if not is_ens:
            for kw in TAX_AUTHORITY_KEYWORDS:
                if kw in counterparty or kw in purpose:
                    is_ens = True
                    match_reason = f"Контрагент/назначение содержит '{kw}'"
                    confidence = 0.85
                    break

        # Проверка по ключевым словам налоговых платежей
        if not is_ens:
            for kw in TAX_PAYMENT_KEYWORDS:
                if kw in purpose:
                    is_ens = True
                    match_reason = f"Назначение содержит '{kw}'"
                    confidence = 0.75
                    break

        if is_ens:
            # Попытка автоматически определить категорию
            amount = Decimal(str(op.get("amount", 0)))
            category = _guess_payment_category(purpose, amount)

            detected.append({
                "operation_id": op.get("id"),
                "date": str(op.get("operation_date", "")),
                "amount": str(op.get("amount", 0)),
                "purpose": op.get("purpose", ""),
                "counterparty": op.get("counterparty", ""),
                "detected_category": category,
                "confidence": confidence,
                "match_reason": match_reason,
            })

    return detected


def _guess_payment_category(purpose: str, amount: Decimal = Decimal("0")) -> str:
    """
    Угадать категорию платежа по назначению и сумме.

    Логика:
    1. Сначала проверяем ключевые слова в назначении (точные маркеры)
    2. Если назначение общее («Единый налоговый платёж»), определяем по сумме:
       - >= 1 000 000 ₽ → авансовый платёж УСН (крупные квартальные платежи)
       - < 1 000 000 ₽ → взносы за сотрудников (НДФЛ + страховые ~43% от ФОТ)

    Категории:
        "fixed_contributions" — фиксированные взносы ИП за себя
        "one_percent" — 1% свыше 300т
        "employee_contributions" — взносы за сотрудников (НДФЛ, страховые)
        "tax_advance" — авансовый платеж УСН
        "ens_mixed" — ЕНС без конкретики

    Returns:
        Строка-категория
    """
    purpose_lower = purpose.lower()

    # Порог для разделения: авансы УСН обычно >= 1M, зарплатные налоги < 1M
    EMPLOYEE_AMOUNT_THRESHOLD = Decimal("1000000")

    # ---- Точные маркеры в назначении (приоритет) ----

    # Авансовый платёж УСН (только если явно указано)
    advance_markers = [
        "авансовый платеж", "авансовый платёж",
        "аванс усн", "аванс по усн",
        "налог усн",
    ]
    for m in advance_markers:
        if m in purpose_lower:
            return "tax_advance"

    # 1% свыше 300т
    one_pct_markers = [
        "1 процент", "1%", "доп. взнос", "дополнительный взнос",
        "свыше 300", "сверх 300",
    ]
    for m in one_pct_markers:
        if m in purpose_lower:
            return "one_percent"

    # Взносы за сотрудников (явные маркеры)
    employee_markers = [
        "за сотрудник", "за работник", "зарплатные взносы",
        "фсс", "социальное страхование", "ндфл",
    ]
    for m in employee_markers:
        if m in purpose_lower:
            return "employee_contributions"

    # Фиксированные взносы ИП за себя
    fixed_markers = [
        "фиксированный", "фиксированные", "страховые взносы ип",
        "взнос опс", "взнос омс", "пенсионное страхование",
        "медицинское страхование", "за себя",
    ]
    for m in fixed_markers:
        if m in purpose_lower:
            return "fixed_contributions"

    # ---- Общее назначение — определяем по сумме ----

    # «Единый налоговый платёж» / ЕНС — сумма определяет категорию
    ens_generic_markers = ["единый налоговый", "енс", "единый налог"]
    is_generic_ens = any(m in purpose_lower for m in ens_generic_markers)

    if is_generic_ens and amount > 0:
        if amount >= EMPLOYEE_AMOUNT_THRESHOLD:
            # Крупные суммы — авансовый платёж УСН
            return "tax_advance"
        else:
            # До 1M — взносы за сотрудников (НДФЛ + страховые ~43% ФОТ)
            return "employee_contributions"

    # Если есть хоть какой-то generic marker — ens_mixed
    if is_generic_ens:
        return "ens_mixed"

    return "ens_mixed"


def calculate_quarterly_income(operations: List[Dict]) -> Dict[str, Decimal]:
    """
    Рассчитать доход по кварталам из списка операций.

    Args:
        operations: Список операций (включённых в налоговую базу)

    Returns:
        {"q1": Decimal, "q2": Decimal, "q3": Decimal, "q4": Decimal}
    """
    quarterly = {
        "q1": Decimal("0"),
        "q2": Decimal("0"),
        "q3": Decimal("0"),
        "q4": Decimal("0"),
    }

    for op in operations:
        if op.get("classification") != "income":
            continue

        amount = Decimal(str(op.get("amount", 0)))
        op_date = op.get("operation_date")

        if isinstance(op_date, str):
            try:
                parts = op_date.split("-")
                month = int(parts[1])
            except (IndexError, ValueError):
                continue
        elif isinstance(op_date, date):
            month = op_date.month
        else:
            continue

        if 1 <= month <= 3:
            quarterly["q1"] += amount
        elif 4 <= month <= 6:
            quarterly["q2"] += amount
        elif 7 <= month <= 9:
            quarterly["q3"] += amount
        elif 10 <= month <= 12:
            quarterly["q4"] += amount

    return quarterly


def calculate_advances(
    year: int,
    quarterly_income: Dict[str, Decimal],
    quarterly_contributions: Dict[str, Decimal],
    has_employees: bool,
) -> List[Dict]:
    """
    Рассчитать авансовые платежи по кварталам.

    Логика: нарастающий итог.
    Аванс = Налог_нараст - Взносы_нараст - Уплачено_ранее_нараст

    Args:
        year: Налоговый год
        quarterly_income: Доход по кварталам
        quarterly_contributions: Взносы оплаченные по кварталам
        has_employees: Есть ли сотрудники (влияет на лимит вычета)

    Returns:
        Список расчётов по периодам
    """
    tax_rate = Decimal("0.06")
    periods = [
        ("q1", "1 квартал", ["q1"]),
        ("half_year", "Полугодие", ["q1", "q2"]),
        ("nine_months", "9 месяцев", ["q1", "q2", "q3"]),
        ("year", "Год", ["q1", "q2", "q3", "q4"]),
    ]

    results = []
    total_advance_paid = Decimal("0")

    for period_key, period_name, quarters in periods:
        # Доход нарастающим итогом
        income_cumulative = sum(quarterly_income.get(q, Decimal("0")) for q in quarters)

        # Налог
        tax_calculated = (income_cumulative * tax_rate).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )

        # Взносы нарастающим итогом
        contributions_cumulative = sum(
            quarterly_contributions.get(q, Decimal("0")) for q in quarters
        )

        # Лимит взносов
        if has_employees:
            contribution_limit = (tax_calculated * Decimal("0.5")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        else:
            contribution_limit = tax_calculated  # до 100%

        contributions_applied = min(contributions_cumulative, contribution_limit)

        # Налог после уменьшения
        tax_after_reduction = max(Decimal("0"), tax_calculated - contributions_applied)

        # К доплате = налог_после_уменьш - уже_уплаченные_авансы
        tax_due = tax_after_reduction - total_advance_paid

        results.append({
            "period": period_key,
            "period_name": period_name,
            "income_cumulative": str(income_cumulative),
            "tax_calculated": str(tax_calculated),
            "contributions_cumulative": str(contributions_cumulative),
            "contributions_applied": str(contributions_applied),
            "contribution_limit": str(contribution_limit),
            "tax_after_reduction": str(tax_after_reduction),
            "advance_paid": str(total_advance_paid),
            "tax_due": str(tax_due),
        })

        # Аванс к уплате становится "уплаченным" для следующего периода
        if tax_due > 0:
            total_advance_paid += tax_due

    return results


def distribute_ens_payments_to_quarters(
    ens_payments: List[Dict],
) -> Dict[str, Decimal]:
    """
    Распределить категоризированные ЕНС-платежи по кварталам.
    Только платежи с категориями взносов (fixed, one_percent, employee).

    Args:
        ens_payments: Список платежей с полями date, amount, category

    Returns:
        {"q1": Decimal, "q2": Decimal, "q3": Decimal, "q4": Decimal}
    """
    contribution_categories = {
        "fixed_contributions", "one_percent", "employee_contributions", "ens_mixed"
    }

    quarterly = {
        "q1": Decimal("0"),
        "q2": Decimal("0"),
        "q3": Decimal("0"),
        "q4": Decimal("0"),
    }

    for payment in ens_payments:
        category = payment.get("category") or payment.get("detected_category", "")
        if category not in contribution_categories:
            continue

        amount = Decimal(str(payment.get("amount", 0)))
        date_str = str(payment.get("date", ""))

        try:
            # Parse date to get month
            if "-" in date_str:
                month = int(date_str.split("-")[1])
            elif "." in date_str:
                month = int(date_str.split(".")[1])
            else:
                continue
        except (IndexError, ValueError):
            continue

        if 1 <= month <= 3:
            quarterly["q1"] += amount
        elif 4 <= month <= 6:
            quarterly["q2"] += amount
        elif 7 <= month <= 9:
            quarterly["q3"] += amount
        elif 10 <= month <= 12:
            quarterly["q4"] += amount

    return quarterly


def get_advance_payments_from_ens(ens_payments: List[Dict]) -> Dict[str, Decimal]:
    """
    Извлечь фактически уплаченные авансовые платежи из ЕНС-платежей.

    Returns:
        {"q1": Decimal, "q2": Decimal, "q3": Decimal, "q4": Decimal}
    """
    quarterly = {
        "q1": Decimal("0"),
        "q2": Decimal("0"),
        "q3": Decimal("0"),
        "q4": Decimal("0"),
    }

    for payment in ens_payments:
        category = payment.get("category") or payment.get("detected_category", "")
        if category != "tax_advance":
            continue

        amount = Decimal(str(payment.get("amount", 0)))
        date_str = str(payment.get("date", ""))

        try:
            if "-" in date_str:
                month = int(date_str.split("-")[1])
            elif "." in date_str:
                month = int(date_str.split(".")[1])
            else:
                continue
        except (IndexError, ValueError):
            continue

        if 1 <= month <= 3:
            quarterly["q1"] += amount
        elif 4 <= month <= 6:
            quarterly["q2"] += amount
        elif 7 <= month <= 9:
            quarterly["q3"] += amount
        elif 10 <= month <= 12:
            quarterly["q4"] += amount

    return quarterly
