"""Единое правило «предложение можно купить» для поиска, витрины продавца и наблюдений (C08, R07–R09).

Предложение purchasable, если одновременно:
  * цена положительна;
  * наличие — in_stock или preorder;
  * срок (TTL) не истёк: истёкшим считается now >= expires_at;
  * само предложение, его канал и продавец активны (если признак известен);
  * наблюдение не устарело (Stale по data_quality.freshness от observed_at, иначе updated_at).

SQL-фильтры кандидатов строятся из тех же порогов (sql_purchasable_conditions), чтобы отбор до
LIMIT и итоговая карточка не расходились.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import data_quality

PURCHASABLE_AVAILABILITY = ("in_stock", "preorder")


def utc_now(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Момент проверки в UTC; наивное время считается UTC."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def offer_expired(expires_at: Any, now: Optional[datetime.datetime] = None) -> bool:
    """Истёк ли TTL. Битое значение — отсутствие данных, а не «просрочено»."""
    if not expires_at:
        return False
    try:
        exp = datetime.datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=datetime.timezone.utc)
    return utc_now(now) >= exp


def _inactive(value: Any) -> bool:
    return value is not None and not bool(value)


def offer_purchasable(row: Dict[str, Any], now: Optional[datetime.datetime] = None) -> bool:
    moment = utc_now(now)
    try:
        price = float(row.get("price") if row.get("price") is not None else row.get("current_price") or 0)
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    if str(row.get("availability") or "in_stock").lower() not in PURCHASABLE_AVAILABILITY:
        return False
    if offer_expired(row.get("expires_at"), now=moment):
        return False
    if any(_inactive(row.get(k)) for k in ("is_active", "channel_is_active", "seller_is_active")):
        return False
    observed = row.get("observed_at") or row.get("updated_at")
    if observed and data_quality.freshness(observed, now=moment)["freshness"] == data_quality.STALE:
        return False
    return True


def sql_purchasable_conditions(now: Optional[datetime.datetime] = None, offer: str = "o") -> Tuple[List[str], list]:
    """Те же условия в SQL для отбора кандидатов до LIMIT (алиас предложения — offer)."""
    moment = utc_now(now).isoformat()
    conditions = [
        f"{offer}.price > 0",
        f"LOWER(COALESCE({offer}.availability, 'in_stock')) IN ('in_stock', 'preorder')",
        # expired ⇔ now >= expires_at, значит допустимо только expires_at > now (R09)
        # Нечитаемое время, как и в Python, не делает предложение недоступным
        f"(julianday({offer}.expires_at) IS NULL OR julianday({offer}.expires_at) > julianday(?))",
        f"(julianday({offer}.observed_at) IS NULL OR julianday({offer}.observed_at) >= julianday(?) - ?)",
    ]
    return conditions, [moment, moment, data_quality.AGING_HOURS / 24.0]
