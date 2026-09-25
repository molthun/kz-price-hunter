"""
Offer and Price History Repository for KZ Price Hunter 2.0.
"""

from __future__ import annotations

import json
from typing import List, Optional

from domain.models import Condition, Offer, OfferPriceHistory, utc_now_iso
from repositories.base import BaseRepository


class OfferRepository(BaseRepository):
    """
    CRUD and query operations for merchant offers and time-series prices.
    """

    def save_or_update(self, offer: Offer) -> Offer:
        sql = """
        INSERT INTO offers (
            id, product_id, seller_id, channel_id, external_sku,
            url, image_url, price, old_price, currency, condition,
            availability, city, payment_methods_json, installment_months,
            delivery_type, warranty, published_at, observed_at,
            is_active, raw_payload_json, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            product_id=excluded.product_id,
            seller_id=excluded.seller_id,
            channel_id=excluded.channel_id,
            external_sku=excluded.external_sku,
            url=excluded.url,
            image_url=COALESCE(excluded.image_url, offers.image_url),
            price=excluded.price,
            old_price=excluded.old_price,
            currency=excluded.currency,
            condition=excluded.condition,
            availability=excluded.availability,
            city=excluded.city,
            payment_methods_json=excluded.payment_methods_json,
            installment_months=COALESCE(excluded.installment_months, offers.installment_months),
            delivery_type=COALESCE(excluded.delivery_type, offers.delivery_type),
            warranty=COALESCE(excluded.warranty, offers.warranty),
            published_at=COALESCE(excluded.published_at, offers.published_at),
            observed_at=excluded.observed_at,
            is_active=excluded.is_active,
            raw_payload_json=excluded.raw_payload_json,
            updated_at=excluded.updated_at
        """
        now = utc_now_iso()
        params = (
            offer.id,
            offer.product_id,
            offer.seller_id,
            offer.channel_id,
            offer.external_sku,
            offer.url,
            offer.image_url,
            offer.price,
            offer.old_price,
            offer.currency,
            offer.condition,
            offer.availability,
            offer.city,
            json.dumps(offer.payment_methods, ensure_ascii=False),
            offer.installment_months,
            offer.delivery_type,
            offer.warranty,
            offer.published_at,
            offer.observed_at or now,
            1 if offer.is_active else 0,
            json.dumps(offer.raw_payload, ensure_ascii=False),
            offer.created_at or now,
            now,
        )
        self.execute(sql, params)
        offer.updated_at = now
        return offer

    def add_price_observation(
        self,
        offer_id: str,
        price: float,
        old_price: Optional[float] = None,
        observed_at: Optional[str] = None,
    ) -> OfferPriceHistory:
        obs_time = observed_at or utc_now_iso()
        sql = """
        INSERT OR REPLACE INTO offer_price_history (offer_id, price, old_price, observed_at)
        VALUES (?, ?, ?, ?)
        """
        self.execute(sql, (offer_id, price, old_price, obs_time))
        return OfferPriceHistory(
            offer_id=offer_id,
            price=price,
            old_price=old_price,
            observed_at=obs_time,
        )

    def get_by_id(self, offer_id: str) -> Optional[Offer]:
        row = self.fetchone("SELECT * FROM offers WHERE id = ?", (offer_id,))
        return Offer.from_row(row) if row else None

    def get_by_channel_sku(self, channel_id: str, external_sku: str) -> Optional[Offer]:
        row = self.fetchone(
            "SELECT * FROM offers WHERE channel_id = ? AND external_sku = ?",
            (channel_id, external_sku),
        )
        return Offer.from_row(row) if row else None

    def get_active_offers_for_product(
        self,
        product_id: str,
        city: Optional[str] = None,
        condition: Optional[str] = None,
    ) -> List[Offer]:
        sql = "SELECT * FROM offers WHERE product_id = ? AND is_active = 1"
        params: list = [product_id]
        if city and city != "Казахстан":
            sql += " AND (city = ? OR city = 'Казахстан')"
            params.append(city)
        if condition:
            sql += " AND condition = ?"
            params.append(condition)
        sql += " ORDER BY price ASC"
        rows = self.fetchall(sql, params)
        return [Offer.from_row(r) for r in rows]

    def get_price_history(self, offer_id: str, limit: int = 100) -> List[OfferPriceHistory]:
        sql = """
        SELECT * FROM (
            SELECT * FROM offer_price_history
            WHERE offer_id = ?
            ORDER BY observed_at DESC
            LIMIT ?
        ) ORDER BY observed_at ASC
        """
        rows = self.fetchall(sql, (offer_id, limit))
        return [OfferPriceHistory.from_row(r) for r in rows]

    def count_offers(self, active_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM offers"
        if active_only:
            sql += " WHERE is_active = 1"
        row = self.fetchone(sql)
        return row["COUNT(*)"] if row else 0
