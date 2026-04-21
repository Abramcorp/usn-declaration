"""
Общие утилиты для расчётов.
"""
from decimal import Decimal, ROUND_HALF_UP


def round_rub(val) -> int:
    """Округление до целых рублей по п. 6 ст. 52 НК РФ:
    < 50 копеек отбрасывается, >= 50 копеек округляется до рубля."""
    return int(Decimal(str(val)).quantize(Decimal("1"), ROUND_HALF_UP))
