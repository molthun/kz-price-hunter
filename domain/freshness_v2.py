"""
Дифференцированная свежесть предложений по типу источника (S2-E25, KZ Price Hunter 2.0).

Не одна универсальная свежесть для всего каталога, а отдельные пороги по Source Type:
  * Retail API / YML: часы (FRESH ≤ 12 ч, AGING ≤ 36 ч, STALE ≤ 72 ч, EXPIRED > 72 ч)
  * Website: часы (FRESH ≤ 26 ч, AGING ≤ 72 ч, STALE > 72 ч — совместимо с P02 data_quality)
  * Instagram post: дни (FRESH ≤ 72 ч / 3 дн., AGING ≤ 168 ч / 7 дн., STALE ≤ 336 ч / 14 дн., EXPIRED > 14 дн.)
  * Story: часы (FRESH ≤ 6 ч, AGING ≤ 18 ч, STALE ≤ 24 ч, EXPIRED > 24 ч или при наступлении expires_at)
  * Used listing: дни (FRESH ≤ 72 ч / 3 дн., AGING ≤ 168 ч / 7 дн., STALE ≤ 336 ч / 14 дн., EXPIRED > 14 дн.)
  * Food: часы (FRESH ≤ 4 ч, AGING ≤ 12 ч, STALE ≤ 24 ч, EXPIRED > 24 ч)
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

from domain.models import ChannelType, Condition
from domain.purchasability import offer_expired, utc_now


class SourceFreshnessType:
    RETAIL_API = "retail_api"
    YML = "yml"
    WEBSITE = "website"
    INSTAGRAM_POST = "instagram_post"
    STORY = "story"
    USED_LISTING = "used_listing"
    FOOD = "food"

    ALL = {
        RETAIL_API,
        YML,
        WEBSITE,
        INSTAGRAM_POST,
        STORY,
        USED_LISTING,
        FOOD,
    }


class FreshnessStateV2:
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"

    ALL = {FRESH, AGING, STALE, EXPIRED, UNKNOWN}


# Пороги в часах: (fresh_max_hours, aging_max_hours, stale_max_hours_or_none)
# Если stale_max_hours задан и возраст превышает его, состояние переходит в EXPIRED.
FRESHNESS_THRESHOLDS_HOURS: Dict[str, Tuple[float, float, Optional[float]]] = {
    SourceFreshnessType.RETAIL_API: (12.0, 36.0, 72.0),
    SourceFreshnessType.YML: (12.0, 36.0, 72.0),
    SourceFreshnessType.WEBSITE: (26.0, 72.0, None),
    SourceFreshnessType.INSTAGRAM_POST: (72.0, 168.0, 336.0),
    SourceFreshnessType.STORY: (6.0, 18.0, 24.0),
    SourceFreshnessType.USED_LISTING: (72.0, 168.0, 336.0),
    SourceFreshnessType.FOOD: (4.0, 12.0, 24.0),
}


@dataclass
class OfferFreshnessResult:
    state: str
    source_type: str
    age_hours: Optional[float]
    is_purchasable_freshness: bool
    is_expired: bool
    is_stale: bool

    @property
    def legacy_freshness(self) -> str:
        """Нижний регистр для обратной совместимости с P02 (fresh / aging / stale / unknown)."""
        if self.state == FreshnessStateV2.FRESH:
            return "fresh"
        if self.state == FreshnessStateV2.AGING:
            return "aging"
        if self.state in (FreshnessStateV2.STALE, FreshnessStateV2.EXPIRED):
            return "stale"
        return "unknown"

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["legacy_freshness"] = self.legacy_freshness
        return res


def _parse_iso_utc(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
            )
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def infer_source_freshness_type(
    source_type: Optional[str] = None,
    channel_type: Optional[str] = None,
    condition: Optional[str] = None,
    content_type: Optional[str] = None,
    catalog_format: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """Определяет тип свежести источника по явной метке или контексту канала/контента/состояния."""
    if source_type:
        norm = str(source_type).strip().lower()
        if norm in SourceFreshnessType.ALL:
            return norm
        if norm in ("api", "json_api", "rest", "graphql"):
            return SourceFreshnessType.RETAIL_API
        if norm in ("yml_feed", "xml_feed", "feed"):
            return SourceFreshnessType.YML
        if norm in ("story", "instagram_story", "telegram_story"):
            return SourceFreshnessType.STORY
        if norm in ("post", "instagram", "telegram"):
            return SourceFreshnessType.INSTAGRAM_POST
        if norm in ("used", "classifieds"):
            return SourceFreshnessType.USED_LISTING
        if norm in ("food", "food_aggregator", "food_menu"):
            return SourceFreshnessType.FOOD

    if category:
        cat_low = str(category).strip().lower()
        if any(w in cat_low for w in ("еда", "пицц", "суши", "продукт", "food", "ресторан", "меню")):
            return SourceFreshnessType.FOOD

    if content_type and str(content_type).strip().upper() == "STORY":
        return SourceFreshnessType.STORY

    if catalog_format:
        cf = str(catalog_format).strip().lower()
        if cf in ("yml", "xml_feed"):
            return SourceFreshnessType.YML
        if cf in ("json_api", "graphql"):
            return SourceFreshnessType.RETAIL_API

    ct = str(channel_type or "").strip().lower()
    if ct == ChannelType.FOOD_AGGREGATOR:
        return SourceFreshnessType.FOOD
    if ct == ChannelType.CLASSIFIEDS:
        return SourceFreshnessType.USED_LISTING
    if ct in (ChannelType.INSTAGRAM, ChannelType.TELEGRAM):
        return SourceFreshnessType.INSTAGRAM_POST

    cond = Condition.normalize(condition, default=Condition.UNKNOWN)
    if cond in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX) and ct in (
        ChannelType.CLASSIFIEDS,
        ChannelType.INSTAGRAM,
        ChannelType.TELEGRAM,
        ChannelType.WHATSAPP,
    ):
        return SourceFreshnessType.USED_LISTING

    return SourceFreshnessType.WEBSITE


def evaluate_offer_freshness(
    observed_at: Any,
    *,
    source_type: Optional[str] = None,
    channel_type: Optional[str] = None,
    condition: Optional[str] = None,
    content_type: Optional[str] = None,
    catalog_format: Optional[str] = None,
    category: Optional[str] = None,
    expires_at: Any = None,
    now: Optional[datetime.datetime] = None,
) -> OfferFreshnessResult:
    """Вычисляет состояние свежести (FRESH / AGING / STALE / EXPIRED / UNKNOWN) с учётом типа источника."""
    resolved_type = infer_source_freshness_type(
        source_type=source_type,
        channel_type=channel_type,
        condition=condition,
        content_type=content_type,
        catalog_format=catalog_format,
        category=category,
    )
    moment = utc_now(now)
    seen_dt = _parse_iso_utc(observed_at)

    if offer_expired(expires_at, now=moment):
        age = round(max(0.0, (moment - seen_dt).total_seconds() / 3600.0), 1) if seen_dt else None
        return OfferFreshnessResult(
            state=FreshnessStateV2.EXPIRED,
            source_type=resolved_type,
            age_hours=age,
            is_purchasable_freshness=False,
            is_expired=True,
            is_stale=True,
        )

    if seen_dt is None:
        return OfferFreshnessResult(
            state=FreshnessStateV2.UNKNOWN,
            source_type=resolved_type,
            age_hours=None,
            is_purchasable_freshness=False,
            is_expired=False,
            is_stale=False,
        )

    hours = max(0.0, (moment - seen_dt).total_seconds() / 3600.0)
    fresh_max, aging_max, stale_max = FRESHNESS_THRESHOLDS_HOURS.get(
        resolved_type, FRESHNESS_THRESHOLDS_HOURS[SourceFreshnessType.WEBSITE]
    )

    if hours <= fresh_max:
        state = FreshnessStateV2.FRESH
    elif hours <= aging_max:
        state = FreshnessStateV2.AGING
    elif stale_max is not None and hours > stale_max:
        state = FreshnessStateV2.EXPIRED
    else:
        state = FreshnessStateV2.STALE

    is_exp = state == FreshnessStateV2.EXPIRED
    is_st = state in (FreshnessStateV2.STALE, FreshnessStateV2.EXPIRED)
    return OfferFreshnessResult(
        state=state,
        source_type=resolved_type,
        age_hours=round(hours, 1),
        is_purchasable_freshness=state in (FreshnessStateV2.FRESH, FreshnessStateV2.AGING),
        is_expired=is_exp,
        is_stale=is_st,
    )


# ---------------------------------------------------------------------------
# Правило доступности (V03 аудита 6.5.0). Поиск в SQL до LIMIT, карточки, витрина продавца,
# наблюдения, помощник, провенанс и мониторинг должны отвечать одинаково, поэтому тип источника
# для правила выводится только из того, что есть на каждом из этих путей: тип канала и состояние.
# Явная метка источника (API/YML) и категория в предложении не хранятся — пороги RETAIL_API/YML
# доступны только явным вызовам evaluate_offer_freshness и в правило доступности не входят.
# Купить можно, пока наблюдение FRESH или AGING: предельный возраст — верхняя граница AGING.
# ---------------------------------------------------------------------------

def policy_freshness_type(channel_type: Optional[str] = None, condition: Optional[str] = None) -> str:
    return infer_source_freshness_type(channel_type=channel_type, condition=condition)


def purchasable_max_age_hours(channel_type: Optional[str] = None, condition: Optional[str] = None) -> float:
    return FRESHNESS_THRESHOLDS_HOURS[policy_freshness_type(channel_type, condition)][1]


def offer_freshness(row: Dict[str, Any], now: Optional[datetime.datetime] = None) -> OfferFreshnessResult:
    """Свежесть строки предложения по правилу доступности (тип канала + состояние + TTL)."""
    return evaluate_offer_freshness(
        row.get("observed_at") or row.get("updated_at"),
        channel_type=row.get("channel_type"),
        condition=row.get("condition"),
        expires_at=row.get("expires_at"),
        now=now,
    )


def sql_purchasable_max_age_hours(channel: Optional[str] = "c", offer: str = "o") -> str:
    """Тот же предельный возраст в SQL. Ветви строятся перебором purchasable_max_age_hours по всем
    типам каналов и состояниям — отдельной таблицы порогов для SQL нет. Без алиаса канала —
    порог сайта, как для строки без channel_type в Python."""
    default = purchasable_max_age_hours(None, None)
    if not channel:
        return repr(default)
    ctype = f"LOWER(COALESCE({channel}.channel_type, ''))"
    cond = f"UPPER(COALESCE({offer}.condition, ''))"
    branches = []
    for ct in sorted(ChannelType.ALL | {ChannelType.UNKNOWN}):
        base = purchasable_max_age_hours(ct, None)
        special = {c: purchasable_max_age_hours(ct, c) for c in sorted(Condition.ALL)}
        special = {c: h for c, h in special.items() if h != base}
        if not special and base == default:
            continue
        inner = repr(base)
        if special:
            whens = " ".join(f"WHEN {cond} = '{c}' THEN {h!r}" for c, h in special.items())
            inner = f"(CASE {whens} ELSE {base!r} END)"
        branches.append(f"WHEN {ctype} = '{ct}' THEN {inner}")
    if not branches:
        return repr(default)
    return f"(CASE {' '.join(branches)} ELSE {default!r} END)"
