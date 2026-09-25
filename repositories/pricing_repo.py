"""
Repository and query service for Channel-Aware Pricing (KZ Price Hunter 2.0).
Computes Cross-Channel (same seller) and Cross-Seller (market competition) price structures.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from domain.channel_pricing import (
    ChannelSavings,
    ProductChannelMatrix,
    build_channel_matrix,
)
from domain.condition import (
    ConditionBreakdown,
    ConditionFilter,
    build_condition_breakdown,
)
from domain.models import Condition, Offer, Seller
from repositories.base import BaseRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.seller_repo import SellerRepository

logger = logging.getLogger("kz_price_hunter.repositories.pricing")


class PricingRepository(BaseRepository):
    """Provides analytical and operational channel-aware price queries."""

    def __init__(self, conn):
        super().__init__(conn)
        self.offer_repo = OfferRepository(conn)
        self.product_repo = ProductRepository(conn)
        self.seller_repo = SellerRepository(conn)

    def get_product_channel_matrix(
        self,
        product_id: str,
        city: Optional[str] = None,
        condition: str = Condition.NEW,
    ) -> Optional[ProductChannelMatrix]:
        """
        Builds the complete Channel-Aware pricing matrix for a given canonical product.
        Strictly segregates condition tiers (NEW, USED, OPEN_BOX, REFURBISHED).
        """
        product = self.product_repo.get_by_id(product_id)
        if not product:
            return None

        # Fetch all active offers for the product to build the full condition breakdown
        all_offers = self.offer_repo.get_active_offers_for_product(
            product_id=product_id, city=city, condition=None
        )
        if not all_offers:
            return ProductChannelMatrix(
                product_id=product_id,
                canonical_key=product.canonical_key,
                title=product.title,
                condition=condition,
                city=city or "Казахстан",
                best_overall_price=0.0,
            )

        # Collect unique sellers
        seller_ids = {o.seller_id for o in all_offers}
        sellers_map: Dict[str, Seller] = {}
        for sid in seller_ids:
            s = self.seller_repo.get_by_id(sid)
            if s:
                sellers_map[sid] = s

        # Точные типы каналов из таблицы channels: выводить их разбором идентификатора —
        # та же связанность через форматированную строку, что уже подводила проект
        channel_types = {
            row["id"]: row["channel_type"]
            for row in self.fetchall(
                "SELECT id, channel_type FROM channels WHERE seller_id IN (%s)"
                % ",".join("?" * len(seller_ids)),
                tuple(seller_ids),
            )
        } if seller_ids else {}

        return build_channel_matrix(
            product_id=product.id,
            canonical_key=product.canonical_key,
            title=product.title,
            offers=all_offers,
            sellers_map=sellers_map,
            condition=condition,
            city=city or "Казахстан",
            channel_types=channel_types,
        )

    def get_condition_breakdown(
        self,
        product_id: str,
        city: Optional[str] = None,
    ) -> Optional[ConditionBreakdown]:
        """
        Computes price tiers per condition (New, Used, Open Box, Refurbished) for a product.
        """
        product = self.product_repo.get_by_id(product_id)
        if not product:
            return None

        offers = self.offer_repo.get_active_offers_for_product(
            product_id=product_id, city=city, condition=None
        )
        return build_condition_breakdown(
            product_id=product.id,
            title=product.title,
            offers=offers,
        )


    def find_cross_channel_savings(
        self,
        min_savings_pct: float = 3.0,
        city: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Discovers products where buying directly from a seller saves money
        compared to buying from the same seller on a marketplace.
        """
        # Find products with multiple channels for the same seller
        sql = """
        SELECT o.product_id
        FROM offers o
        WHERE o.is_active = 1
        """
        params: list = []
        if city and city != "Казахстан":
            sql += " AND (o.city = ? OR o.city = 'Казахстан')"
            params.append(city)

        sql += """
        GROUP BY o.product_id, o.seller_id
        HAVING COUNT(DISTINCT o.channel_id) > 1
        LIMIT ?
        """
        params.append(limit * 2)

        rows = self.fetchall(sql, params)
        results: List[Dict[str, Any]] = []

        seen_products = set()
        for r in rows:
            pid = r["product_id"]
            if pid in seen_products:
                continue
            seen_products.add(pid)

            matrix = self.get_product_channel_matrix(pid, city=city)
            if not matrix:
                continue

            for opp in matrix.cross_channel_opportunities:
                if opp.savings_percent >= min_savings_pct:
                    results.append({
                        "product_id": matrix.product_id,
                        "canonical_key": matrix.canonical_key,
                        "title": matrix.title,
                        "city": matrix.city,
                        "seller_id": opp.seller_id,
                        "seller_name": opp.seller_name,
                        "direct_price": opp.direct_price,
                        "marketplace_price": opp.marketplace_price,
                        "savings_amount": opp.savings_amount,
                        "savings_percent": opp.savings_percent,
                        "marketplace_channel": opp.marketplace_channel_type,
                        "installment_months": opp.installment_months,
                        "tradeoff_note": opp.tradeoff_note,
                    })

            if len(results) >= limit:
                break

        results.sort(key=lambda x: x["savings_amount"], reverse=True)
        return results[:limit]
