"""
Domain models and logic for Condition / Second-hand market (KZ Price Hunter 2.0).
Provides strict segregation between NEW, USED, REFURBISHED, and OPEN_BOX goods.
Guarantees that used / open box / refurbished offers never contaminate the 'best new price'.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from domain.models import Condition, Offer


class ConditionFilter:
    ALL = "all"
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    OPEN_BOX = "open_box"

    SUPPORTED = {ALL, NEW, USED, REFURBISHED, OPEN_BOX}

    @classmethod
    def normalize_filter(cls, val: Optional[str]) -> str:
        if not val:
            return cls.ALL
        v = str(val).strip().lower()
        if v in cls.SUPPORTED:
            return v
        if v in {"б/у", "бу", "бывший в употреблении", "second_hand"}:
            return cls.USED
        if v in {"новое", "новый", "new"}:
            return cls.NEW
        if v in {"уценка", "витрина", "openbox", "open_box"}:
            return cls.OPEN_BOX
        if v in {"восстановленный", "refurb", "refurbished"}:
            return cls.REFURBISHED
        return cls.ALL

    @classmethod
    def matches(cls, filter_val: str, offer_condition: str) -> bool:
        """
        Check if an offer condition matches the requested filter.
        Strict isolation:
        - NEW: only Condition.NEW
        - USED: Condition.USED (or secondary market if umbrella)
        - REFURBISHED: only Condition.REFURBISHED
        - OPEN_BOX: only Condition.OPEN_BOX
        - ALL: all conditions match
        """
        f = cls.normalize_filter(filter_val)
        c = offer_condition.upper() if offer_condition else Condition.UNKNOWN

        if f == cls.ALL:
            return True
        if f == cls.NEW:
            return c == Condition.NEW
        if f == cls.USED:
            return c == Condition.USED
        if f == cls.REFURBISHED:
            return c == Condition.REFURBISHED
        if f == cls.OPEN_BOX:
            return c == Condition.OPEN_BOX
        return False


@dataclass
class ConditionBreakdown:
    """
    Price summary per condition category for a canonical product.
    Allows UI and search to present clean separate tiers:
      MacBook Air M5:
        Новые: от 589 000 ₸ (12 магазинов)
        Б/у:   от 430 000 ₸ (4 продавца)
        Уценка / Open Box: от 520 000 ₸ (2 магазина)
    """
    product_id: str
    title: str
    best_new_price: Optional[float] = None
    new_offers_count: int = 0
    best_used_price: Optional[float] = None
    used_offers_count: int = 0
    best_open_box_price: Optional[float] = None
    open_box_offers_count: int = 0
    best_refurbished_price: Optional[float] = None
    refurbished_offers_count: int = 0
    unknown_offers_count: int = 0
    total_active_offers: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_condition_breakdown(
    product_id: str,
    title: str,
    offers: List[Offer],
) -> ConditionBreakdown:
    """
    Analyzes all active offers for a canonical product and computes
    isolated, non-contaminating price tiers for each condition.
    """
    new_prices: List[float] = []
    used_prices: List[float] = []
    open_box_prices: List[float] = []
    refurb_prices: List[float] = []
    unknown_count = 0

    for off in offers:
        if not off.is_active:
            continue
        c = (off.condition or Condition.UNKNOWN).upper()
        if c == Condition.NEW:
            new_prices.append(off.price)
        elif c == Condition.USED:
            used_prices.append(off.price)
        elif c == Condition.OPEN_BOX:
            open_box_prices.append(off.price)
        elif c == Condition.REFURBISHED:
            refurb_prices.append(off.price)
        else:
            unknown_count += 1

    total = len(new_prices) + len(used_prices) + len(open_box_prices) + len(refurb_prices) + unknown_count

    return ConditionBreakdown(
        product_id=product_id,
        title=title,
        best_new_price=min(new_prices) if new_prices else None,
        new_offers_count=len(new_prices),
        best_used_price=min(used_prices) if used_prices else None,
        used_offers_count=len(used_prices),
        best_open_box_price=min(open_box_prices) if open_box_prices else None,
        open_box_offers_count=len(open_box_prices),
        best_refurbished_price=min(refurb_prices) if refurb_prices else None,
        refurbished_offers_count=len(refurb_prices),
        unknown_offers_count=unknown_count,
        total_active_offers=total,
    )


def filter_offers_by_condition(offers: List[Offer], condition_filter: str) -> List[Offer]:
    """
    Filters offers strictly by condition filter.
    Guarantees no cross-contamination between tiers.
    """
    f = ConditionFilter.normalize_filter(condition_filter)
    if f == ConditionFilter.ALL:
        return list(offers)
    return [o for o in offers if ConditionFilter.matches(f, o.condition)]
