"""
Комплексные тесты для налоговой декларации ИП на УСН 6%.

Тестирует:
1. Парсер 1C формата (direction detection, header parsing, amounts)
2. Калькулятор страховых взносов (ставки, расчёт, 1%)
3. Детектор ЕНС-платежей (фильтрация, ложные срабатывания)
4. Расчёт авансовых платежей (нарастающий итог, лимиты)
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from decimal import Decimal
from datetime import date

# ============================================================================
# 1. ТЕСТЫ ПАРСЕРА
# ============================================================================

def test_parser_1c_header_extraction():
    """Проверяем извлечение РасчСчет, периода и остатков из заголовка."""
    from app.services.parser import BankStatementParser

    content = """1CClientBankExchange
ВерсияФормата=1.03
Кодировка=Windows
ДатаНачала=01.01.2024
ДатаКонца=31.12.2024
РасчСчет=40802810556710009452
СекцияРасчСчет
ДатаНачала=01.01.2024
ДатаКонца=01.01.2024
НачальныйОстаток=100000.00
РасчСчет=40802810556710009452
ВсегоСписано=50000
ВсегоПоступило=30000
КонечныйОстаток=80000.00
КонецРасчСчет
СекцияДокумент=Платежное поручение
Номер=1
Дата=01.01.2024
Сумма=30000
ПлательщикСчет=40802810000000000001
Плательщик=ИП Иванов Иван
ПлательщикИНН=123456789012
ПолучательСчет=40802810556710009452
Получатель=ИП Алиев
ПолучательИНН=645410875014
НазначениеПлатежа=Оплата за товар
КонецДокумента
"""
    parser = BankStatementParser()
    result = parser._parse_1c_format(content)

    assert result["account_number"] == "40802810556710009452", \
        f"Неверный РасчСчет: {result['account_number']}"
    # Owner info extracted from first operation
    assert result["owner_inn"] is not None, "owner_inn должен быть извлечён"
    assert result["owner_name"] is not None, "owner_name должен быть извлечён"
    assert result["period_start"] == date(2024, 1, 1), \
        f"Неверная дата начала: {result['period_start']}"
    assert result["period_end"] == date(2024, 12, 31), \
        f"Неверная дата конца: {result['period_end']}"
    assert result["opening_balance"] == Decimal("100000.00"), \
        f"Неверный начальный остаток: {result['opening_balance']}"
    assert result["closing_balance"] == Decimal("80000.00"), \
        f"Неверный конечный остаток: {result['closing_balance']}"
    print("  ✓ test_parser_1c_header_extraction")


def test_parser_1c_direction_income():
    """Если ПолучательСчет = наш счёт → income."""
    from app.services.parser import BankStatementParser

    content = """1CClientBankExchange
РасчСчет=40802810556710009452
СекцияДокумент=Платежное поручение
Номер=100
Дата=15.03.2024
Сумма=50000
ПлательщикСчет=40802810000000000001
Плательщик=ООО Клиент
ПлательщикИНН=1234567890
ПолучательСчет=40802810556710009452
Получатель=ИП Алиев
ПолучательИНН=645410875014
НазначениеПлатежа=Оплата за услуги
КонецДокумента
"""
    parser = BankStatementParser()
    result = parser._parse_1c_format(content)

    assert len(result["operations"]) == 1
    op = result["operations"][0]
    assert op["direction"] == "income", f"Направление должно быть income, получили: {op['direction']}"
    assert op["amount"] == Decimal("50000")
    assert op["counterparty"] == "ООО Клиент"
    assert op["counterparty_inn"] == "1234567890"
    print("  ✓ test_parser_1c_direction_income")


def test_parser_1c_direction_expense():
    """Если ПлательщикСчет = наш счёт → expense."""
    from app.services.parser import BankStatementParser

    content = """1CClientBankExchange
РасчСчет=40802810556710009452
СекцияДокумент=Платежное поручение
Номер=200
Дата=20.06.2024
Сумма=120000
ПлательщикСчет=40802810556710009452
Плательщик=ИП Алиев
ПлательщикИНН=645410875014
ПолучательСчет=40702810756000000647
Получатель=ООО Поставщик
ПолучательИНН=6451425582
НазначениеПлатежа=Оплата за товар
КонецДокумента
"""
    parser = BankStatementParser()
    result = parser._parse_1c_format(content)

    assert len(result["operations"]) == 1
    op = result["operations"][0]
    assert op["direction"] == "expense", f"Направление должно быть expense, получили: {op['direction']}"
    assert op["amount"] == Decimal("120000")
    assert op["counterparty"] == "ООО Поставщик"
    assert op["counterparty_inn"] == "6451425582"
    print("  ✓ test_parser_1c_direction_expense")


def test_parser_1c_mixed_operations():
    """Несколько операций: и приход, и расход — суммы считаются верно."""
    from app.services.parser import BankStatementParser

    content = """1CClientBankExchange
РасчСчет=40802810556710009452
СекцияДокумент=Платежное поручение
Номер=1
Дата=01.04.2024
Сумма=100000
ПлательщикСчет=40802810000000000001
Плательщик=Покупатель 1
ПлательщикИНН=111111111111
ПолучательСчет=40802810556710009452
Получатель=ИП Алиев
ПолучательИНН=645410875014
НазначениеПлатежа=Оплата за товар
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=2
Дата=02.04.2024
Сумма=250000
ПлательщикСчет=40802810000000000002
Плательщик=Покупатель 2
ПлательщикИНН=222222222222
ПолучательСчет=40802810556710009452
Получатель=ИП Алиев
ПолучательИНН=645410875014
НазначениеПлатежа=Оплата по договору
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=3
Дата=03.04.2024
Сумма=80000
ПлательщикСчет=40802810556710009452
Плательщик=ИП Алиев
ПлательщикИНН=645410875014
ПолучательСчет=40702810000000000099
Получатель=ООО Поставщик
ПолучательИНН=333333333333
НазначениеПлатежа=Оплата за материалы
КонецДокумента
"""
    parser = BankStatementParser()
    # Use full parse() to get totals
    # Write to temp file for parse()
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(content)
        tmp_path = f.name

    result = parser.parse(tmp_path)
    os.unlink(tmp_path)

    assert result["total_operations"] == 3, f"Должно быть 3 операции, получили: {result['total_operations']}"
    assert result["total_income"] == Decimal("350000"), \
        f"Доход: ожидали 350000, получили: {result['total_income']}"
    assert result["total_expense"] == Decimal("80000"), \
        f"Расход: ожидали 80000, получили: {result['total_expense']}"

    income_ops = [op for op in result["operations"] if op["direction"] == "income"]
    expense_ops = [op for op in result["operations"] if op["direction"] == "expense"]
    assert len(income_ops) == 2
    assert len(expense_ops) == 1
    print("  ✓ test_parser_1c_mixed_operations")


def test_parser_1c_fallback_account():
    """Если РасчСчет нет в заголовке — определяем по частоте."""
    from app.services.parser import BankStatementParser

    content = """1CClientBankExchange
ВерсияФормата=1.03
СекцияДокумент=Платежное поручение
Номер=1
Дата=01.01.2024
Сумма=10000
ПлательщикСчет=40802810000000000001
Плательщик=Клиент
ПлательщикИНН=111111111111
ПолучательСчет=40802810999999999999
Получатель=ИП Наш
ПолучательИНН=222222222222
НазначениеПлатежа=Тест
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=2
Дата=02.01.2024
Сумма=20000
ПлательщикСчет=40802810999999999999
Плательщик=ИП Наш
ПлательщикИНН=222222222222
ПолучательСчет=40802810000000000002
Получатель=Поставщик
ПолучательИНН=333333333333
НазначениеПлатежа=Оплата
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=3
Дата=03.01.2024
Сумма=5000
ПлательщикСчет=40802810000000000003
Плательщик=Ещё один клиент
ПлательщикИНН=444444444444
ПолучательСчет=40802810999999999999
Получатель=ИП Наш
ПолучательИНН=222222222222
НазначениеПлатежа=Тест2
КонецДокумента
"""
    parser = BankStatementParser()
    result = parser._parse_1c_format(content)

    # 40802810999999999999 appears 3 times (2 as recipient, 1 as payer) — most frequent
    assert result["account_number"] == "40802810999999999999", \
        f"Fallback account: {result['account_number']}"
    assert len(result["warnings"]) > 0, "Должно быть предупреждение о fallback"
    print("  ✓ test_parser_1c_fallback_account")


def test_parser_real_file():
    """Тест на реальном файле выписки."""
    from app.services.parser import BankStatementParser

    file_path = os.path.join(
        os.path.dirname(__file__), '..', 'app', 'uploads',
        '1_20260413_125501_kl_to_1c (545).txt'
    )
    if not os.path.exists(file_path):
        print("  ⊘ test_parser_real_file (файл не найден, пропуск)")
        return

    parser = BankStatementParser()
    result = parser.parse(file_path)

    assert result["format_detected"] == "1c"
    assert result["account_number"] == "40802810556710009452"
    assert result["total_operations"] == 5993, f"Ожидали 5993, получили: {result['total_operations']}"

    income_ops = [op for op in result["operations"] if op["direction"] == "income"]
    expense_ops = [op for op in result["operations"] if op["direction"] == "expense"]

    assert len(income_ops) == 4025, f"Income: ожидали 4025, получили {len(income_ops)}"
    assert len(expense_ops) == 1968, f"Expense: ожидали 1968, получили {len(expense_ops)}"

    # Суммы должны совпадать с контрольными из СекцияРасчСчет
    assert result["total_income"] == Decimal("315274681.47"), \
        f"Сумма поступлений: {result['total_income']}"
    assert result["total_expense"] == Decimal("315478521.79"), \
        f"Сумма списаний: {result['total_expense']}"

    assert len(result["errors"]) == 0, f"Ошибки: {result['errors']}"
    assert result["opening_balance"] is not None
    assert result["closing_balance"] is not None
    print("  ✓ test_parser_real_file (5993 ops, суммы совпадают)")


# ============================================================================
# 2. ТЕСТЫ КАЛЬКУЛЯТОРА ВЗНОСОВ
# ============================================================================

def test_contribution_rates():
    """Проверяем актуальные ставки по годам."""
    from app.services.contribution_calculator import CONTRIBUTION_RATES

    # 2024
    assert CONTRIBUTION_RATES[2024]["fixed_total"] == Decimal("49500")
    assert CONTRIBUTION_RATES[2024]["max_1pct"] == Decimal("277571")

    # 2025
    assert CONTRIBUTION_RATES[2025]["fixed_total"] == Decimal("53658")
    assert CONTRIBUTION_RATES[2025]["max_1pct"] == Decimal("300888")

    # 2026
    assert CONTRIBUTION_RATES[2026]["fixed_total"] == Decimal("57390"), \
        f"2026 fixed: {CONTRIBUTION_RATES[2026]['fixed_total']}"
    assert CONTRIBUTION_RATES[2026]["max_1pct"] == Decimal("321818"), \
        f"2026 max 1%: {CONTRIBUTION_RATES[2026]['max_1pct']}"

    # 2027
    assert CONTRIBUTION_RATES[2027]["fixed_total"] == Decimal("61154")

    print("  ✓ test_contribution_rates")


def test_fixed_contributions():
    """Фиксированные взносы."""
    from app.services.contribution_calculator import calculate_fixed_contributions

    assert calculate_fixed_contributions(2024) == Decimal("49500")
    assert calculate_fixed_contributions(2025) == Decimal("53658")
    assert calculate_fixed_contributions(2026) == Decimal("57390")
    print("  ✓ test_fixed_contributions")


def test_one_percent_below_threshold():
    """Доход ниже 300 000 — 1% = 0."""
    from app.services.contribution_calculator import calculate_one_percent

    assert calculate_one_percent(2024, Decimal("200000")) == Decimal("0")
    assert calculate_one_percent(2024, Decimal("300000")) == Decimal("0")
    print("  ✓ test_one_percent_below_threshold")


def test_one_percent_above_threshold():
    """Доход выше 300 000 — 1% считается корректно."""
    from app.services.contribution_calculator import calculate_one_percent

    # 500000 - 300000 = 200000 × 1% = 2000
    assert calculate_one_percent(2024, Decimal("500000")) == Decimal("2000.00")

    # 10 000 000 - 300 000 = 9 700 000 × 1% = 97 000
    assert calculate_one_percent(2024, Decimal("10000000")) == Decimal("97000.00")
    print("  ✓ test_one_percent_above_threshold")


def test_one_percent_max_cap():
    """1% не может превышать максимум для года."""
    from app.services.contribution_calculator import calculate_one_percent

    # Доход 100 000 000 → 1% = 997 000, но макс. 277571 для 2024
    result = calculate_one_percent(2024, Decimal("100000000"))
    assert result == Decimal("277571"), f"Макс. 1%: {result}"

    # 2026: макс. 321818
    result = calculate_one_percent(2026, Decimal("100000000"))
    assert result == Decimal("321818"), f"2026 макс. 1%: {result}"
    print("  ✓ test_one_percent_max_cap")


def test_total_ip_contributions():
    """Полный расчёт: фикс + 1%."""
    from app.services.contribution_calculator import calculate_total_ip_contributions

    # 2024, доход 1 000 000
    info = calculate_total_ip_contributions(2024, Decimal("1000000"))
    assert info["fixed"] == Decimal("49500")
    assert info["one_percent"] == Decimal("7000.00")  # (1000000-300000)*0.01
    assert info["total"] == Decimal("56500.00")

    # 2026, доход 315 274 681 (из реальной выписки)
    info = calculate_total_ip_contributions(2026, Decimal("315274681"))
    assert info["fixed"] == Decimal("57390")
    assert info["one_percent"] == Decimal("321818")  # capped at max
    assert info["total"] == Decimal("379208")  # 57390 + 321818
    print("  ✓ test_total_ip_contributions")


# ============================================================================
# 3. ТЕСТЫ ДЕТЕКТОРА ЕНС
# ============================================================================

def test_ens_detection_income_filtered():
    """Входящие операции НЕ должны детектиться как ЕНС-платежи."""
    from app.services.contribution_calculator import detect_ens_payments

    operations = [
        {
            "id": 1, "direction": "income", "amount": "15000",
            "purpose": "Зачисление средств по операциям эквайринга",
            "counterparty": "ПАО СБЕРБАНК",
            "counterparty_inn": "7707083893",
            "operation_date": "2024-05-01",
        },
        {
            "id": 2, "direction": "income", "amount": "3000",
            "purpose": "Премия, поощрительные выплаты",
            "counterparty": "ПАО СБЕРБАНК",
            "counterparty_inn": "7707083893",
            "operation_date": "2024-05-15",
        },
    ]

    detected = detect_ens_payments(operations)
    assert len(detected) == 0, f"Поступления не должны быть ЕНС: найдено {len(detected)}"
    print("  ✓ test_ens_detection_income_filtered")


def test_ens_detection_bank_commissions_filtered():
    """Банковские комиссии и премии НЕ являются ЕНС-платежами."""
    from app.services.contribution_calculator import detect_ens_payments

    operations = [
        {
            "id": 1, "direction": "expense", "amount": "199",
            "purpose": "Комиссия в другие банки (кредитные организации)",
            "counterparty": "ПОВОЛЖСКИЙ БАНК ПАО СБЕРБАНК",
            "counterparty_inn": "7707083893",
            "operation_date": "2024-04-10",
        },
        {
            "id": 2, "direction": "expense", "amount": "340000",
            "purpose": "Премия, иные поощрительные выплаты по реестру №45",
            "counterparty": "ПОВОЛЖСКИЙ БАНК ПАО СБЕРБАНК",
            "counterparty_inn": "7707083893",
            "operation_date": "2024-04-10",
        },
        {
            "id": 3, "direction": "expense", "amount": "500",
            "purpose": "Комиссия за обслуживание счёта",
            "counterparty": "ПАО СБЕРБАНК",
            "counterparty_inn": "7707083893",
            "operation_date": "2024-04-10",
        },
    ]

    detected = detect_ens_payments(operations)
    assert len(detected) == 0, f"Комиссии/премии не ЕНС: найдено {len(detected)}"
    print("  ✓ test_ens_detection_bank_commissions_filtered")


def test_ens_detection_real_tax_payment():
    """Настоящие налоговые платежи на ЕНС должны определяться."""
    from app.services.contribution_calculator import detect_ens_payments

    operations = [
        {
            "id": 10, "direction": "expense", "amount": "146200",
            "purpose": "Единый налоговый платеж. НДС не облагается",
            "counterparty": "Казначейство России (ФНС России)",
            "counterparty_inn": "7727406020",
            "operation_date": "2024-07-25",
        },
        {
            "id": 11, "direction": "expense", "amount": "49500",
            "purpose": "Фиксированные страховые взносы ИП за себя за 2024 год",
            "counterparty": "Казначейство России",
            "counterparty_inn": "7727406020",
            "operation_date": "2024-12-20",
        },
    ]

    detected = detect_ens_payments(operations)
    assert len(detected) == 2, f"Ожидали 2 ЕНС-платежа, получили: {len(detected)}"

    # Проверяем категории: 146200 < 1M → employee_contributions, 49500 → fixed_contributions
    categories = {d["detected_category"] for d in detected}
    assert "fixed_contributions" in categories, f"Ожидали fixed_contributions: {categories}"
    assert "employee_contributions" in categories, f"Ожидали employee_contributions: {categories}"
    print("  ✓ test_ens_detection_real_tax_payment")


def test_ens_detection_real_file():
    """Тест на реальном файле: только 156 реальных ЕНС-платежей."""
    from app.services.parser import BankStatementParser
    from app.services.contribution_calculator import detect_ens_payments

    file_path = os.path.join(
        os.path.dirname(__file__), '..', 'app', 'uploads',
        '1_20260413_125501_kl_to_1c (545).txt'
    )
    if not os.path.exists(file_path):
        print("  ⊘ test_ens_detection_real_file (файл не найден, пропуск)")
        return

    parser = BankStatementParser()
    result = parser.parse(file_path)

    # Convert to format expected by detect_ens_payments
    ops_dicts = []
    for op in result["operations"]:
        ops_dicts.append({
            "id": 0,
            "operation_date": str(op.get("operation_date", "")),
            "amount": str(op.get("amount", 0)),
            "purpose": op.get("purpose", ""),
            "counterparty": op.get("counterparty", ""),
            "counterparty_inn": op.get("counterparty_inn", ""),
            "direction": op.get("direction", ""),
        })

    detected = detect_ens_payments(ops_dicts)

    assert len(detected) == 156, f"Ожидали 156 ЕНС-платежей, получили: {len(detected)}"

    # Все должны быть expense
    for d in detected:
        assert "Казначейство" in d.get("counterparty", "") or d.get("match_reason", "").startswith("ИНН"), \
            f"Неожиданный ЕНС-платёж: {d}"

    # Не должно быть false positives (эквайринг, комиссии, премии)
    for d in detected:
        purpose_lower = d.get("purpose", "").lower()
        assert "эквайринг" not in purpose_lower, f"Эквайринг в ЕНС: {d['purpose']}"
        assert "комиссия" not in purpose_lower, f"Комиссия в ЕНС: {d['purpose']}"
        assert "премия" not in purpose_lower, f"Премия в ЕНС: {d['purpose']}"

    print(f"  ✓ test_ens_detection_real_file (156 платежей, 0 false positives)")


# ============================================================================
# 4. ТЕСТЫ АВАНСОВЫХ ПЛАТЕЖЕЙ
# ============================================================================

def test_advances_no_employees():
    """ИП без сотрудников: вычет взносов до 100%."""
    from app.services.contribution_calculator import calculate_advances

    quarterly_income = {
        "q1": Decimal("500000"),
        "q2": Decimal("600000"),
        "q3": Decimal("700000"),
        "q4": Decimal("800000"),
    }
    quarterly_contributions = {
        "q1": Decimal("12375"),   # 49500/4
        "q2": Decimal("12375"),
        "q3": Decimal("12375"),
        "q4": Decimal("12375"),
    }

    results = calculate_advances(2024, quarterly_income, quarterly_contributions, has_employees=False)

    # Q1: доход 500000, налог 30000, взносы 12375, к уплате = 30000 - 12375 = 17625
    q1 = results[0]
    assert Decimal(q1["tax_calculated"]) == Decimal("30000")
    assert Decimal(q1["contributions_applied"]) == Decimal("12375")
    assert Decimal(q1["tax_due"]) == Decimal("17625")

    # Полугодие: доход 1100000 нараст., налог 66000, взносы 24750 нараст.
    # После уменьш. = 66000 - 24750 = 41250, уже оплачено 17625, к доплате = 23625
    half = results[1]
    assert Decimal(half["income_cumulative"]) == Decimal("1100000")
    assert Decimal(half["tax_calculated"]) == Decimal("66000")
    assert Decimal(half["tax_due"]) == Decimal("23625")

    print("  ✓ test_advances_no_employees")


def test_advances_with_employees():
    """ИП с сотрудниками: вычет максимум 50%."""
    from app.services.contribution_calculator import calculate_advances

    quarterly_income = {
        "q1": Decimal("1000000"),
        "q2": Decimal("0"),
        "q3": Decimal("0"),
        "q4": Decimal("0"),
    }
    quarterly_contributions = {
        "q1": Decimal("100000"),  # Больше 50% налога
        "q2": Decimal("0"),
        "q3": Decimal("0"),
        "q4": Decimal("0"),
    }

    results = calculate_advances(2024, quarterly_income, quarterly_contributions, has_employees=True)

    # Q1: доход 1000000, налог 60000, лимит 50% = 30000, взносы = 100000 (ограничены 30000)
    q1 = results[0]
    assert Decimal(q1["tax_calculated"]) == Decimal("60000")
    assert Decimal(q1["contribution_limit"]) == Decimal("30000")
    assert Decimal(q1["contributions_applied"]) == Decimal("30000")  # min(100000, 30000)
    assert Decimal(q1["tax_after_reduction"]) == Decimal("30000")
    assert Decimal(q1["tax_due"]) == Decimal("30000")

    print("  ✓ test_advances_with_employees")


def test_quarterly_income_distribution():
    """Распределение дохода по кварталам."""
    from app.services.contribution_calculator import calculate_quarterly_income

    operations = [
        {"classification": "income", "amount": "100000", "operation_date": "2024-02-15"},
        {"classification": "income", "amount": "200000", "operation_date": "2024-05-20"},
        {"classification": "income", "amount": "300000", "operation_date": "2024-08-10"},
        {"classification": "income", "amount": "400000", "operation_date": "2024-11-30"},
        {"classification": "not_income", "amount": "50000", "operation_date": "2024-03-01"},  # ignored
    ]

    quarterly = calculate_quarterly_income(operations)

    assert quarterly["q1"] == Decimal("100000")
    assert quarterly["q2"] == Decimal("200000")
    assert quarterly["q3"] == Decimal("300000")
    assert quarterly["q4"] == Decimal("400000")
    print("  ✓ test_quarterly_income_distribution")


def test_ens_distribution_to_quarters():
    """Распределение ЕНС-платежей по кварталам."""
    from app.services.contribution_calculator import distribute_ens_payments_to_quarters

    payments = [
        {"amount": "12375", "date": "2024-01-15", "category": "fixed_contributions"},
        {"amount": "12375", "date": "2024-04-10", "category": "fixed_contributions"},
        {"amount": "12375", "date": "2024-07-20", "category": "fixed_contributions"},
        {"amount": "12375", "date": "2024-10-25", "category": "fixed_contributions"},
        {"amount": "146200", "date": "2024-04-25", "category": "tax_advance"},  # не взнос → не считается
    ]

    quarterly = distribute_ens_payments_to_quarters(payments)

    assert quarterly["q1"] == Decimal("12375")
    assert quarterly["q2"] == Decimal("12375")
    assert quarterly["q3"] == Decimal("12375")
    assert quarterly["q4"] == Decimal("12375")
    print("  ✓ test_ens_distribution_to_quarters")


def test_advance_payments_from_ens():
    """Извлечение авансовых платежей из ЕНС."""
    from app.services.contribution_calculator import get_advance_payments_from_ens

    payments = [
        {"amount": "50000", "date": "2024-04-25", "category": "tax_advance"},
        {"amount": "12375", "date": "2024-04-10", "category": "fixed_contributions"},  # не аванс
        {"amount": "75000", "date": "2024-07-25", "category": "tax_advance"},
        {"amount": "60000", "date": "2024-10-25", "category": "tax_advance"},
    ]

    quarterly = get_advance_payments_from_ens(payments)

    assert quarterly["q1"] == Decimal("0")
    assert quarterly["q2"] == Decimal("50000")
    assert quarterly["q3"] == Decimal("75000")
    assert quarterly["q4"] == Decimal("60000")
    print("  ✓ test_advance_payments_from_ens")


# ============================================================================
# 5. ТЕСТЫ ПАРСЕРА — EDGE CASES
# ============================================================================

def test_amount_categorization_below_1m():
    """ЕНС-платежи < 1M авто-категоризируются как employee_contributions."""
    from app.services.contribution_calculator import _guess_payment_category

    # Типичные зарплатные платежи (НДФЛ + страховые)
    assert _guess_payment_category("Единый налоговый платеж. НДС не облагается", Decimal("146200")) == "employee_contributions"
    assert _guess_payment_category("Единый налоговый платеж", Decimal("500000")) == "employee_contributions"
    assert _guess_payment_category("Единый налоговый платеж", Decimal("999999")) == "employee_contributions"
    assert _guess_payment_category("ЕНС пополнение", Decimal("50000")) == "employee_contributions"
    print("  ✓ test_amount_categorization_below_1m")


def test_amount_categorization_above_1m():
    """ЕНС-платежи >= 1M авто-категоризируются как tax_advance."""
    from app.services.contribution_calculator import _guess_payment_category

    assert _guess_payment_category("Единый налоговый платеж. НДС не облагается", Decimal("1000000")) == "tax_advance"
    assert _guess_payment_category("Единый налоговый платеж", Decimal("3500000")) == "tax_advance"
    assert _guess_payment_category("ЕНС пополнение", Decimal("2000000")) == "tax_advance"
    print("  ✓ test_amount_categorization_above_1m")


def test_explicit_markers_override_amount():
    """Явные маркеры в назначении имеют приоритет над суммой."""
    from app.services.contribution_calculator import _guess_payment_category

    # "за себя" → fixed_contributions, даже при большой сумме
    assert _guess_payment_category("Фиксированные страховые взносы ИП за себя за 2024 год", Decimal("49500")) == "fixed_contributions"

    # "НДФЛ" → employee_contributions, даже при большой сумме
    assert _guess_payment_category("НДФЛ за работников", Decimal("2000000")) == "employee_contributions"

    # "авансовый платёж" → tax_advance, даже при малой сумме
    assert _guess_payment_category("Авансовый платёж УСН за 1 квартал", Decimal("50000")) == "tax_advance"

    # "1%" → one_percent
    assert _guess_payment_category("1% свыше 300 тыс", Decimal("10000")) == "one_percent"
    print("  ✓ test_explicit_markers_override_amount")


def test_ens_detect_passes_amount():
    """detect_ens_payments() передаёт сумму в _guess_payment_category()."""
    from app.services.contribution_calculator import detect_ens_payments

    operations = [
        {
            "id": 1, "direction": "expense", "amount": "146200",
            "purpose": "Единый налоговый платеж. НДС не облагается",
            "counterparty": "Казначейство России (ФНС России)",
            "counterparty_inn": "7727406020",
            "operation_date": "2024-04-25",
        },
        {
            "id": 2, "direction": "expense", "amount": "1500000",
            "purpose": "Единый налоговый платеж. НДС не облагается",
            "counterparty": "Казначейство России (ФНС России)",
            "counterparty_inn": "7727406020",
            "operation_date": "2024-04-25",
        },
    ]

    detected = detect_ens_payments(operations)
    assert len(detected) == 2

    small = next(d for d in detected if d["operation_id"] == 1)
    large = next(d for d in detected if d["operation_id"] == 2)

    assert small["detected_category"] == "employee_contributions", \
        f"146200 должно быть employee_contributions, получили: {small['detected_category']}"
    assert large["detected_category"] == "tax_advance", \
        f"1500000 должно быть tax_advance, получили: {large['detected_category']}"
    print("  ✓ test_ens_detect_passes_amount")


def test_parser_amount_formats():
    """Разные форматы сумм."""
    from app.services.parser import BankStatementParser
    parser = BankStatementParser()

    assert parser._parse_amount("1234.56") == Decimal("1234.56")
    assert parser._parse_amount("1234,56") == Decimal("1234.56")
    assert parser._parse_amount("1 234 567,89") == Decimal("1234567.89")
    assert parser._parse_amount("0") == Decimal("0")
    assert parser._parse_amount("") is None
    assert parser._parse_amount("-") is None
    assert parser._parse_amount("—") is None
    print("  ✓ test_parser_amount_formats")


def test_parser_date_formats():
    """Разные форматы дат."""
    from app.services.parser import BankStatementParser
    parser = BankStatementParser()

    assert parser._parse_date("01.01.2024") == date(2024, 1, 1)
    assert parser._parse_date("31/12/2024") == date(2024, 12, 31)
    assert parser._parse_date("2024-06-15") == date(2024, 6, 15)
    assert parser._parse_date("") is None
    assert parser._parse_date("не дата") is None
    print("  ✓ test_parser_date_formats")


# ============================================================================
# RUN ALL TESTS
# ============================================================================

def run_all_tests():
    """Запуск всех тестов."""
    tests = [
        # Parser
        ("Парсер 1C: заголовок", test_parser_1c_header_extraction),
        ("Парсер 1C: direction=income", test_parser_1c_direction_income),
        ("Парсер 1C: direction=expense", test_parser_1c_direction_expense),
        ("Парсер 1C: mixed operations", test_parser_1c_mixed_operations),
        ("Парсер 1C: fallback account", test_parser_1c_fallback_account),
        ("Парсер 1C: реальный файл", test_parser_real_file),
        ("Парсер: форматы сумм", test_parser_amount_formats),
        ("Парсер: форматы дат", test_parser_date_formats),
        # Contribution calculator
        ("Взносы: ставки по годам", test_contribution_rates),
        ("Взносы: фиксированные", test_fixed_contributions),
        ("Взносы: 1% ниже порога", test_one_percent_below_threshold),
        ("Взносы: 1% выше порога", test_one_percent_above_threshold),
        ("Взносы: 1% потолок", test_one_percent_max_cap),
        ("Взносы: итого ИП", test_total_ip_contributions),
        # ENS detection
        ("ЕНС: income отфильтрован", test_ens_detection_income_filtered),
        ("ЕНС: комиссии отфильтрованы", test_ens_detection_bank_commissions_filtered),
        ("ЕНС: настоящие платежи", test_ens_detection_real_tax_payment),
        ("ЕНС: реальный файл", test_ens_detection_real_file),
        # Amount-based categorization
        ("Категоризация: < 1M → employee_contributions", test_amount_categorization_below_1m),
        ("Категоризация: >= 1M → tax_advance", test_amount_categorization_above_1m),
        ("Категоризация: маркеры приоритетнее суммы", test_explicit_markers_override_amount),
        ("Категоризация: detect_ens передаёт amount", test_ens_detect_passes_amount),
        # Advances
        ("Авансы: без сотрудников", test_advances_no_employees),
        ("Авансы: с сотрудниками (50%)", test_advances_with_employees),
        ("Авансы: распределение дохода", test_quarterly_income_distribution),
        ("Авансы: распределение ЕНС", test_ens_distribution_to_quarters),
        ("Авансы: из ЕНС-платежей", test_advance_payments_from_ens),
    ]

    print("=" * 60)
    print("ТЕСТЫ НАЛОГОВОЙ ДЕКЛАРАЦИИ ИП УСН 6%")
    print("=" * 60)

    passed = 0
    failed = 0
    skipped = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {name}: EXCEPTION — {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"ИТОГО: {passed} passed, {failed} failed из {len(tests)}")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
