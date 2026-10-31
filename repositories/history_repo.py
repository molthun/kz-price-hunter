"""
HistoryRepository (S2-E29, KZ Price Hunter 2.0).

Repository layer for offer price history and isolated product pricing series:
  (product_id, seller_id, channel_id, condition)
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from domain.models import Condition, OfferPriceHistory, utc_now_iso
from domain.price_history_v2 import ProductPriceHistoryReport, build_price_history_series
from repositories.base import BaseRepository
from repositories.product_repo import ProductRepository

logger = logging.getLogger("kz_price_hunter.repositories.history")


class HistoryRepository(BaseRepository):
    """
    Unified repository for time-series price history and multi-channel price observations.
    """

    def __init__(self, conn):
        super().__init__(conn)
        self.product_repo = ProductRepository(conn)

    def record_point(
        self,
        offer_id: str,
        price: float,
        old_price: Optional[float] = None,
        observed_at: Optional[str] = None,
        product_id: Optional[str] = None,
        seller_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        condition: Optional[str] = None,
        city: Optional[str] = None,
    ) -> None:
        """
        Records a single price observation point for an offer with persistent dimension snapshots (SP30-01).
        If dimensions are not explicitly provided, captures them from the offer at recording time.
        Preserves previously recorded snapshots on re-record.
        """
        obs = observed_at or utc_now_iso()
        sql = """
        INSERT INTO offer_price_history (
            offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city
        )
        SELECT
            ?,
            ?,
            ?,
            ?,
            COALESCE(?, o.product_id),
            COALESCE(?, o.seller_id),
            COALESCE(?, o.channel_id),
            COALESCE(?, o.condition),
            COALESCE(?, o.city)
        FROM (SELECT 1) LEFT JOIN offers o ON o.id = ?
        ON CONFLICT(offer_id, observed_at) DO UPDATE SET
            price = excluded.price,
            old_price = excluded.old_price,
            product_id = COALESCE(offer_price_history.product_id, excluded.product_id),
            seller_id = COALESCE(offer_price_history.seller_id, excluded.seller_id),
            channel_id = COALESCE(offer_price_history.channel_id, excluded.channel_id),
            condition = COALESCE(offer_price_history.condition, excluded.condition),
            city = COALESCE(offer_price_history.city, excluded.city)
        """
        self.execute(
            sql,
            (
                offer_id,
                float(price),
                old_price,
                obs,
                product_id,
                seller_id,
                channel_id,
                condition,
                city,
                offer_id,
            ),
        )

    def record_history_model(self, model: OfferPriceHistory) -> None:
        """Records an OfferPriceHistory domain dataclass with all preserved dimensions."""
        self.record_point(
            offer_id=model.offer_id,
            price=model.price,
            old_price=model.old_price,
            observed_at=model.observed_at,
            product_id=getattr(model, "product_id", None),
            seller_id=getattr(model, "seller_id", None),
            channel_id=getattr(model, "channel_id", None),
            condition=getattr(model, "condition", None),
            city=getattr(model, "city", None),
        )

    def record_batch(self, points: List[Dict[str, Any]]) -> int:
        """
        Records a batch of price points preserving offer dimension snapshots (SP30-01).
        Each point dict must have: offer_id, price, and optional old_price, observed_at,
        and optional explicit dimensions (product_id, seller_id, channel_id, condition, city).
        """
        if not points:
            return 0
        now_ts = utc_now_iso()
        rows = [
            (
                p["offer_id"],
                float(p["price"]),
                p.get("old_price"),
                p.get("observed_at") or now_ts,
                p.get("product_id"),
                p.get("seller_id"),
                p.get("channel_id"),
                p.get("condition"),
                p.get("city"),
                p["offer_id"],
            )
            for p in points
        ]
        sql = """
        INSERT INTO offer_price_history (
            offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city
        )
        SELECT
            ?,
            ?,
            ?,
            ?,
            COALESCE(?, o.product_id),
            COALESCE(?, o.seller_id),
            COALESCE(?, o.channel_id),
            COALESCE(?, o.condition),
            COALESCE(?, o.city)
        FROM (SELECT 1) LEFT JOIN offers o ON o.id = ?
        ON CONFLICT(offer_id, observed_at) DO UPDATE SET
            price = excluded.price,
            old_price = excluded.old_price,
            product_id = COALESCE(offer_price_history.product_id, excluded.product_id),
            seller_id = COALESCE(offer_price_history.seller_id, excluded.seller_id),
            channel_id = COALESCE(offer_price_history.channel_id, excluded.channel_id),
            condition = COALESCE(offer_price_history.condition, excluded.condition),
            city = COALESCE(offer_price_history.city, excluded.city)
        """
        cur = self.executemany(sql, rows)
        return cur.rowcount

    def get_offer_price_history(
        self,
        offer_id: str,
        limit: int = 100,
    ) -> List[OfferPriceHistory]:
        """Returns chronological price observations for a single offer."""
        sql = """
        SELECT offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city
        FROM offer_price_history
        WHERE offer_id = ?
        ORDER BY observed_at ASC
        LIMIT ?
        """
        rows = self.fetchall(sql, (offer_id, limit))
        return [OfferPriceHistory.from_row(r) for r in rows]

    def delete_history_for_offer(self, offer_id: str) -> int:
        """Deletes all price history observations for an offer."""
        sql = "DELETE FROM offer_price_history WHERE offer_id = ?"
        cur = self.execute(sql, (offer_id,))
        return cur.rowcount

    def get_product_price_history(
        self,
        product_id: str,
        condition: Optional[str] = None,
        seller_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        city: Optional[str] = None,
        limit_per_series: int = 90,
    ) -> Optional[ProductPriceHistoryReport]:
        """
        Retrieves historical price lines for a canonical product, grouped cleanly
        into isolated series (product_id, seller_id, channel_id, condition).
        """
        product = self.product_repo.get_by_id(product_id)
        if not product:
            return None

        eff_limit = max(1, min(int(limit_per_series or 90), 500))
        where_extra = ""
        params: list = [product_id]

        if seller_id:
            where_extra += " AND {seller_id} = ?"
            params.append(seller_id)
        if channel_id:
            where_extra += " AND {channel_id} = ?"
            params.append(channel_id)
        if channel_type:
            where_extra += " AND LOWER(COALESCE(c.channel_type, '')) = ?"
            params.append(str(channel_type).strip().lower())
        if condition:
            where_extra += " AND UPPER(COALESCE({condition}, 'NEW')) = ?"
            params.append(Condition.normalize(condition, default=Condition.NEW))
        if city and city != "Казахстан":
            where_extra += " AND ({city} = ? OR {city} = 'Казахстан')"
            params.append(city)

        snapshot = {
            col: f"COALESCE(oph.{col}, o.{col})"
            for col in ("product_id", "seller_id", "channel_id", "condition", "city")
        }
        current = {col: f"o.{col}" for col in snapshot}

        h = snapshot
        sql = f"""
        SELECT
            {h['product_id']} AS product_id,
            o.id AS offer_id,
            {h['seller_id']} AS seller_id,
            s.name AS seller_name,
            {h['channel_id']} AS channel_id,
            c.channel_type,
            c.name AS channel_name,
            {h['condition']} AS condition,
            {h['city']} AS city,
            o.currency,
            oph.price,
            oph.old_price,
            oph.observed_at
        FROM offer_price_history oph
        JOIN offers o ON o.id = oph.offer_id
        LEFT JOIN sellers s ON s.id = {h['seller_id']}
        LEFT JOIN channels c ON c.id = {h['channel_id']}
        WHERE {h['product_id']} = ?{where_extra.format(**h)}
        ORDER BY oph.observed_at ASC
        """
        rows = [dict(r) for r in self.fetchall(sql, params)]

        # Fallback to current offer observations if historical snapshots are not yet recorded
        if not rows:
            fallback_sql = f"""
            SELECT
                o.product_id,
                o.id AS offer_id,
                o.seller_id,
                s.name AS seller_name,
                o.channel_id,
                c.channel_type,
                c.name AS channel_name,
                o.condition,
                o.city,
                o.currency,
                o.price,
                o.old_price,
                o.observed_at
            FROM offers o
            LEFT JOIN sellers s ON s.id = o.seller_id
            LEFT JOIN channels c ON c.id = o.channel_id
            WHERE o.product_id = ? AND o.is_active = 1{where_extra.format(**current)}
            """
            rows = [dict(r) for r in self.fetchall(fallback_sql, params)]

        return build_price_history_series(
            rows,
            product_id=product.id,
            title=product.title,
            limit_per_series=eff_limit,
        )

    def get_history_stats(self) -> Dict[str, Any]:
        """Returns summary metadata about recorded price history."""
        sql = """
        SELECT
            COUNT(*) as total_points,
            COUNT(DISTINCT offer_id) as unique_offers,
            MIN(observed_at) as earliest_observation,
            MAX(observed_at) as latest_observation
        FROM offer_price_history
        """
        row = self.fetchone(sql) or {}
        return {
            "total_points": int(row.get("total_points") or 0),
            "unique_offers": int(row.get("unique_offers") or 0),
            "earliest_observation": row.get("earliest_observation"),
            "latest_observation": row.get("latest_observation"),
        }
