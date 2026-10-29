"""
Price History 2.0 (S2-E26, KZ Price Hunter 2.0).

История цен строго разделяется по четвёрке:
  (product_id, seller_id, channel_id, condition)

Нельзя смешивать:
  * 4mobile direct 499 000 ₸
  * 4mobile Kaspi  539 000 ₸
или разные состояния (NEW против USED) в одну ценовую линию.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from domain.models import Condition


@dataclass
class PriceHistoryPoint:
    observed_at: str
    price: float
    old_price: Optional[float] = None
    offer_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PriceHistorySeries:
    series_key: str
    product_id: str
    seller_id: str
    seller_name: str
    channel_id: str
    channel_type: str
    channel_name: str
    condition: str
    city: str = "Казахстан"
    currency: str = "KZT"
    offer_id: Optional[str] = None
    points: List[PriceHistoryPoint] = field(default_factory=list)
    min_price: float = 0.0
    max_price: float = 0.0
    first_price: float = 0.0
    latest_price: float = 0.0
    change_amount: float = 0.0
    change_pct: float = 0.0

    @property
    def current_price(self) -> float:
        return self.latest_price

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["current_price"] = self.latest_price
        res["points"] = [p.to_dict() for p in self.points]
        return res


@dataclass
class ProductPriceHistoryReport:
    product_id: str
    title: str
    series: List[PriceHistorySeries] = field(default_factory=list)
    total_series: int = 0
    total_observations: int = 0

    @property
    def series_count(self) -> int:
        return self.total_series

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "total_series": self.total_series,
            "series_count": self.total_series,
            "total_observations": self.total_observations,
            "series": [s.to_dict() for s in self.series],
        }


def make_price_series_key(
    product_id: str,
    seller_id: str,
    channel_id: str,
    condition: str,
) -> str:
    norm_cond = Condition.normalize(condition, default=Condition.UNKNOWN)
    return f"{product_id}::{seller_id}::{channel_id}::{norm_cond}"


def build_price_history_series(
    rows: Iterable[Dict[str, Any]],
    *,
    product_id: str,
    title: str = "",
    limit_per_series: int = 90,
) -> ProductPriceHistoryReport:
    """Группирует точки истории цен в изолированные линии (product, seller, channel, condition)."""
    buckets: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    for row in rows:
        price = float(row.get("price") or 0.0)
        if price <= 0:
            continue
        observed_at = str(row.get("observed_at") or "").strip()
        if not observed_at:
            continue

        pid = str(row.get("product_id") or product_id).strip()
        sid = str(row.get("seller_id") or "").strip()
        cid = str(row.get("channel_id") or "").strip()
        cond = Condition.normalize(row.get("condition"), default=Condition.NEW)
        if not sid or not cid:
            continue

        key_tuple = (pid, sid, cid, cond)
        if key_tuple not in buckets:
            buckets[key_tuple] = {
                "product_id": pid,
                "seller_id": sid,
                "seller_name": str(row.get("seller_name") or sid),
                "channel_id": cid,
                "channel_type": str(row.get("channel_type") or "website"),
                "channel_name": str(row.get("channel_name") or cid),
                "condition": cond,
                "city": str(row.get("city") or "Казахстан"),
                "currency": str(row.get("currency") or "KZT"),
                "offer_id": row.get("offer_id"),
                "points_by_time": {},
            }

        old_p = float(row["old_price"]) if row.get("old_price") is not None else None
        # Одна линия может собирать несколько предложений (например, разные города): их точки
        # в один момент — разные наблюдения, а не повтор, и затирать друг друга не должны (V02)
        offer_key = str(row.get("offer_id") or "")
        buckets[key_tuple]["points_by_time"][(observed_at, offer_key)] = PriceHistoryPoint(
            observed_at=observed_at,
            price=price,
            old_price=old_p,
            offer_id=row.get("offer_id"),
        )

    series_list: List[PriceHistorySeries] = []
    total_obs = 0

    for (pid, sid, cid, cond), meta in buckets.items():
        pts = [meta["points_by_time"][k] for k in sorted(meta["points_by_time"])]
        if limit_per_series and limit_per_series > 0 and len(pts) > limit_per_series:
            pts = pts[-limit_per_series:]
        if not pts:
            continue

        prices = [p.price for p in pts]
        first_p = pts[0].price
        latest_p = pts[-1].price
        change_amt = round(latest_p - first_p, 2)
        change_pct = round((change_amt / first_p) * 100.0, 2) if first_p > 0 else 0.0

        series_list.append(
            PriceHistorySeries(
                series_key=make_price_series_key(pid, sid, cid, cond),
                product_id=pid,
                seller_id=sid,
                seller_name=meta["seller_name"],
                channel_id=cid,
                channel_type=meta["channel_type"],
                channel_name=meta["channel_name"],
                condition=cond,
                city=meta["city"],
                currency=meta["currency"],
                offer_id=meta["offer_id"],
                points=pts,
                min_price=min(prices),
                max_price=max(prices),
                first_price=first_p,
                latest_price=latest_p,
                change_amount=change_amt,
                change_pct=change_pct,
            )
        )
        total_obs += len(pts)

    # Сортируем серии: сначала NEW, затем по текущей (последней) цене по возрастанию
    series_list.sort(key=lambda s: (s.condition != Condition.NEW, s.latest_price, s.seller_name, s.channel_type))

    return ProductPriceHistoryReport(
        product_id=product_id,
        title=title,
        series=series_list,
        total_series=len(series_list),
        total_observations=total_obs,
    )
