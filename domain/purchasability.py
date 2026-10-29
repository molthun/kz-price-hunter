"""Единое правило «предложение можно купить» для поиска, витрины продавца и наблюдений (C08, R07–R09).

Предложение purchasable, если одновременно:
  * цена положительна;
  * наличие — in_stock или preorder;
  * срок (TTL) не истёк: истёкшим считается now >= expires_at;
  * само предложение, его канал и продавец активны (если признак известен);
  * наблюдение не устарело: возраст observed_at (иначе updated_at) не больше предела для типа
    канала и состояния (domain.freshness_v2: сайт 72 ч, еда 12 ч, объявления и соцсети 7 дн.).
    Нечитаемое время — отсутствие данных, а не «устарело».

SQL-фильтры кандидатов строятся из тех же порогов (sql_purchasable_conditions), чтобы отбор до
LIMIT и итоговая карточка не расходились.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple


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
    if observed:
        from domain.freshness_v2 import _parse_iso_utc, purchasable_max_age_hours
        seen = _parse_iso_utc(observed)
        limit = purchasable_max_age_hours(row.get("channel_type"), row.get("condition"))
        if seen is not None and (moment - seen).total_seconds() / 3600.0 > limit:
            return False
    return True


def sql_purchasable_conditions(now: Optional[datetime.datetime] = None, offer: str = "o",
                               channel: Optional[str] = None) -> Tuple[List[str], list]:
    """Те же условия в SQL для отбора кандидатов до LIMIT (алиас предложения — offer, канала —
    channel; без канала порог свежести — как у строки без channel_type в Python)."""
    from domain.freshness_v2 import sql_purchasable_max_age_hours
    moment = utc_now(now).isoformat()
    max_age = sql_purchasable_max_age_hours(channel, offer)
    seen = f"COALESCE(NULLIF({offer}.observed_at, ''), {offer}.updated_at)"
    conditions = [
        f"{offer}.price > 0",
        f"LOWER(COALESCE({offer}.availability, 'in_stock')) IN ('in_stock', 'preorder')",
        # expired ⇔ now >= expires_at, значит допустимо только expires_at > now (R09)
        # Нечитаемое время, как и в Python, не делает предложение недоступным
        f"(julianday({offer}.expires_at) IS NULL OR julianday({offer}.expires_at) > julianday(?))",
        # Как в Python: пустое observed_at — берётся updated_at (F02)
        f"(julianday({seen}) IS NULL OR julianday({seen}) >= julianday(?) - {max_age} / 24.0)",
    ]
    return conditions, [moment, moment]
