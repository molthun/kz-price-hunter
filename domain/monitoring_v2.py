"""
Monitoring Center 2.0 & 4-Level Observability (S2-E23, S2-E24, KZ Price Hunter 2.0).

Четыре уровня диагностики платформы:
1. Уровень продавца (`seller_level`): известные продавцы, подтверждённые каналы, кандидаты в очереди сверки.
2. Уровень источника (`source_level`): Tier 1–7 (`source_profiles`), соцсети (`social_sources`), витрины магазинов (`shop_status`).
3. Уровень канала (`channel_level`): каналы по типам и расхождения цен одного продавца между каналами (например, сайт vs маркетплейс).
4. Уровень предложения и качества данных (`offer_level`): актуальные (`fresh`), устаревшие (`stale`), истёкшие (`expired`) по типу источника (`SourceFreshnessType`), а также отсеянные предложения (`data_quality` / `Vision`).
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import data_quality
from domain.freshness_v2 import FreshnessStateV2, offer_freshness
from domain.models import utc_now_iso
from telemetry import get_correlation_ids


@dataclass
class MonitoringOverviewV2:
    """Сводка 4 уровней Monitoring Center 2.0 (S2-E23, S2-E24)."""
    generated_at: str
    correlation_ids: Dict[str, Optional[str]]
    seller_level: Dict[str, Any] = field(default_factory=dict)
    source_level: Dict[str, Any] = field(default_factory=dict)
    channel_level: Dict[str, Any] = field(default_factory=dict)
    offer_level: Dict[str, Any] = field(default_factory=dict)
    # Разделы, которые не удалось прочитать: их числа — null, а не ноль (V04)
    read_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_monitoring_overview_v2(
    conn: Any,
    now: Optional[datetime.datetime] = None,
) -> MonitoringOverviewV2:
    """
    Собирает четырёхуровневую диагностику платформы (S2-E23) по состоянию БД.
    Работает только на чтение и безопасно обрабатывает пустые или частичные таблицы.
    """
    cur = conn.cursor()
    read_errors: List[str] = []

    def _note_error(sql: str, exc: Exception) -> None:
        table = sql.split(" FROM ", 1)[-1].split()[0] if " FROM " in sql else "?"
        read_errors.append(f"{table}: {type(exc).__name__}")

    def _safe_fetchall(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            _note_error(sql, exc)
            return []

    def _safe_scalar(sql: str, params: tuple = ()) -> Optional[int]:
        """Число или None при ошибке чтения: недоступная таблица — не «ноль» (V04)."""
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return 0
            val = row[0] if not isinstance(row, dict) else next(iter(row.values()))
            return int(val or 0)
        except Exception as exc:
            _note_error(sql, exc)
            return None

    # ------------------------------------------------------------------
    # 1. Уровень продавца (Seller Level)
    # ------------------------------------------------------------------
    total_sellers = _safe_scalar("SELECT COUNT(*) FROM sellers")
    active_sellers = _safe_scalar("SELECT COUNT(*) FROM sellers WHERE is_active = 1")
    sellers_with_channels = _safe_scalar(
        "SELECT COUNT(DISTINCT seller_id) FROM channels WHERE is_active = 1"
    )
    sellers_with_verified_identity = _safe_scalar(
        "SELECT COUNT(DISTINCT seller_id) FROM seller_identities WHERE confidence >= 0.8"
    )
    pending_reviews = _safe_scalar(
        "SELECT COUNT(*) FROM seller_review_queue WHERE status = 'PENDING'"
    )

    candidate_by_status: Dict[str, int] = {}
    for row in _safe_fetchall(
        "SELECT status, COUNT(*) AS cnt FROM seller_candidates GROUP BY status"
    ):
        candidate_by_status[str(row.get("status") or "UNKNOWN")] = int(row.get("cnt") or 0)

    seller_level = {
        "total_sellers": total_sellers,
        "active_sellers": active_sellers,
        "sellers_with_confirmed_channels": sellers_with_channels,
        "sellers_with_verified_identity": sellers_with_verified_identity,
        "pending_review_queue": pending_reviews,
        "candidates_by_status": candidate_by_status,
    }

    # ------------------------------------------------------------------
    # 2. Уровень источника (Source Level)
    # ------------------------------------------------------------------
    tiers_breakdown: Dict[str, int] = {}
    formats_breakdown: Dict[str, int] = {}
    observed_profiles = 0
    total_profiles = 0

    for row in _safe_fetchall(
        "SELECT extraction_tier, catalog_format, observed, COUNT(*) AS cnt "
        "FROM source_profiles GROUP BY extraction_tier, catalog_format, observed"
    ):
        cnt = int(row.get("cnt") or 0)
        total_profiles += cnt
        if row.get("observed"):
            observed_profiles += cnt
        tier_key = f"tier_{row.get('extraction_tier') or 7}"
        tiers_breakdown[tier_key] = tiers_breakdown.get(tier_key, 0) + cnt
        fmt_key = str(row.get("catalog_format") or "unknown")
        formats_breakdown[fmt_key] = formats_breakdown.get(fmt_key, 0) + cnt

    social_sources_status: Dict[str, int] = {}
    social_sources_recent: List[Dict[str, Any]] = []
    for row in _safe_fetchall(
        "SELECT id, platform, handle, enabled, last_status, last_error, last_fetched_at "
        "FROM social_sources ORDER BY COALESCE(last_fetched_at, '') DESC LIMIT 25"
    ):
        st = str(row.get("last_status") or "NEVER")
        social_sources_status[st] = social_sources_status.get(st, 0) + 1
        social_sources_recent.append({
            "source_id": row.get("id"),
            "platform": row.get("platform"),
            "handle": row.get("handle"),
            "enabled": bool(row.get("enabled")),
            "last_status": st,
            "last_error": row.get("last_error") or "",
            "last_fetched_at": row.get("last_fetched_at"),
        })

    source_level = {
        "total_source_profiles": total_profiles,
        "observed_source_profiles": observed_profiles,
        "tiers_breakdown": tiers_breakdown,
        "formats_breakdown": formats_breakdown,
        "social_sources_by_status": social_sources_status,
        "social_sources_recent": social_sources_recent[:10],
    }

    # ------------------------------------------------------------------
    # 3. Уровень канала (Channel Level & Cross-Channel Price Discrepancies)
    # ------------------------------------------------------------------
    channels_by_type: Dict[str, int] = {}
    for row in _safe_fetchall(
        "SELECT channel_type, COUNT(*) AS cnt FROM channels WHERE is_active = 1 GROUP BY channel_type"
    ):
        channels_by_type[str(row.get("channel_type") or "unknown")] = int(row.get("cnt") or 0)

    # Расхождения цен одного и того же продавца и товара между его каналами (например, сайт vs Kaspi)
    discrepancy_rows = _safe_fetchall(
        """
        SELECT
            o.product_id,
            cp.title AS product_title,
            o.seller_id,
            s.name AS seller_name,
            MIN(o.price) AS min_channel_price,
            MAX(o.price) AS max_channel_price,
            COUNT(DISTINCT o.channel_id) AS channels_count
        FROM offers o
        JOIN sellers s ON s.id = o.seller_id
        LEFT JOIN canonical_products cp ON cp.id = o.product_id
        WHERE o.is_active = 1 AND o.price > 0
        GROUP BY o.product_id, o.seller_id, o.condition
        HAVING COUNT(DISTINCT o.channel_id) > 1 AND MAX(o.price) > MIN(o.price)
        ORDER BY (MAX(o.price) - MIN(o.price)) DESC
        LIMIT 20
        """
    )
    cross_channel_discrepancies: List[Dict[str, Any]] = []
    for d in discrepancy_rows:
        min_p = float(d.get("min_channel_price") or 0.0)
        max_p = float(d.get("max_channel_price") or 0.0)
        diff = round(max_p - min_p, 2)
        spread_pct = round((diff / min_p) * 100.0, 2) if min_p > 0 else 0.0
        cross_channel_discrepancies.append({
            "product_id": d.get("product_id"),
            "product_title": d.get("product_title") or d.get("product_id"),
            "seller_id": d.get("seller_id"),
            "seller_name": d.get("seller_name"),
            "min_channel_price": min_p,
            "max_channel_price": max_p,
            "price_diff": diff,
            "spread_pct": spread_pct,
            "channels_count": int(d.get("channels_count") or 0),
        })

    channel_level = {
        "active_channels_by_type": channels_by_type,
        "cross_channel_discrepancies_count": len(cross_channel_discrepancies),
        "cross_channel_discrepancies": cross_channel_discrepancies,
    }

    # ------------------------------------------------------------------
    # 4. Уровень предложения и качества данных (Offer & Data Quality Level)
    # ------------------------------------------------------------------
    # Все активные предложения, постранично по rowid: прежний LIMIT 5000 выдавал размер выборки
    # за общее число, а свежесть и разбивки описывали произвольный фрагмент каталога (V04)
    offer_sql = """
        SELECT
            o.rowid AS _rowid, o.id, o.price, o.condition, o.observed_at, o.updated_at,
            o.expires_at, c.channel_type, cp.title AS canonical_title
        FROM offers o
        LEFT JOIN channels c ON c.id = o.channel_id
        LEFT JOIN canonical_products cp ON cp.id = o.product_id
        WHERE o.is_active = 1 AND o.rowid > ?
        ORDER BY o.rowid
        LIMIT ?
    """
    page_size = 5000

    total_active = 0
    fresh_count = 0
    stale_count = 0
    expired_count = 0
    unknown_count = 0
    rejected_quality_count = 0
    by_source_type: Dict[str, Dict[str, int]] = {}
    offers_read_ok = True

    last_rowid = 0
    while True:
        try:
            cur.execute(offer_sql, (last_rowid, page_size))
            page = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            _note_error("SELECT FROM offers", exc)
            offers_read_ok = False
            break
        if not page:
            break
        last_rowid = page[-1]["_rowid"]
        for r in page:
            total_active += 1
            # То же правило, что у доступности в поиске (V03): тип канала, состояние, TTL
            f_res = offer_freshness(r, now=now)
            bucket = by_source_type.setdefault(
                f_res.source_type, {"fresh": 0, "stale": 0, "expired": 0, "unknown": 0, "total": 0}
            )
            bucket["total"] += 1
            if f_res.state in (FreshnessStateV2.FRESH, FreshnessStateV2.AGING):
                fresh_count += 1
                bucket["fresh"] += 1
            elif f_res.state == FreshnessStateV2.STALE:
                stale_count += 1
                bucket["stale"] += 1
            elif f_res.state == FreshnessStateV2.EXPIRED:
                expired_count += 1
                bucket["expired"] += 1
            else:
                # Время наблюдения не читается — это не «истёкшее», а неизвестное
                unknown_count += 1
                bucket["unknown"] += 1

            title = str(r.get("canonical_title") or "").strip()
            price = float(r.get("price") or 0.0)
            if price <= 0 or price > data_quality.MAX_PRICE or not title:
                rejected_quality_count += 1
        if len(page) < page_size:
            break

    inactive_offers = _safe_scalar("SELECT COUNT(*) FROM offers WHERE is_active = 0")
    active_observations = _safe_scalar("SELECT COUNT(*) FROM content_observations")

    def _if_read(value: Any) -> Any:
        return value if offers_read_ok else None

    offer_level = {
        "total_active_offers": _if_read(total_active),
        "inactive_offers": inactive_offers,
        "fresh_offers": _if_read(fresh_count),
        "stale_offers": _if_read(stale_count),
        "expired_offers": _if_read(expired_count),
        "unknown_freshness_offers": _if_read(unknown_count),
        "rejected_by_quality_rules": _if_read(rejected_quality_count),
        "by_source_freshness_type": by_source_type if offers_read_ok else None,
        "content_observations_stored": active_observations,
    }

    return MonitoringOverviewV2(
        generated_at=utc_now_iso(),
        correlation_ids=get_correlation_ids(),
        seller_level=seller_level,
        source_level=source_level,
        channel_level=channel_level,
        offer_level=offer_level,
        read_errors=read_errors,
    )
