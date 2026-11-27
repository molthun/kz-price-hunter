"""Общие правила записи цены предложения и её истории.

Прежняя цена (old_price) — это зачёркнутая цена скидки. Она хранится только когда обе цены
конечны, текущая положительна, а прежняя строго выше текущей. Иначе прежней цены нет (NULL):
рост цены, повтор той же цены, ноль, отрицательные, нечисловые значения, NaN и бесконечность.
"""
import math
from typing import Any, Optional


def finite_price(value: Any) -> Optional[float]:
    """Положительная конечная цена или None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def valid_old_price(old_price: Any, price: Any) -> Optional[float]:
    """Прежняя цена, если это настоящая скидка относительно текущей; иначе None."""
    current = finite_price(price)
    previous = finite_price(old_price)
    if current is None or previous is None:
        return None
    return previous if previous > current else None
