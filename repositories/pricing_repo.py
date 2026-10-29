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
from domain.price_history_v2 import ProductPriceHistoryReport, build_price_history_series
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

    def get_product_price_history(
        self,
        product_id: str,
        seller_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        condition: Optional[str] = None,
        city: Optional[str] = None,
        limit_per_series: int = 90,
        limit_per_offer: Optional[int] = None,
    ) -> Optional[ProductPriceHistoryReport]:
        """
        Price History 2.0 (S2-E26): возвращает изолированные ценовые линии по
        (product_id, seller_id, channel_id, condition) из offer_price_history.
        """
        product = self.product_repo.get_by_id(product_id)
        if not product:
            return None

        eff_limit = int(limit_per_offer or limit_per_series or 90)
        where_extra = ""
        params: list = [product_id]
        # Измерения точки — на момент наблюдения (V02). Для строк, записанных до появления
        # колонок, их нет, и остаётся только текущее предложение. Префикс h. заменяется на o.
        # в запросе-подстановке по offers.
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
        snapshot = {col: f"COALESCE(oph.{col}, o.{col})" for col in
                    ("product_id", "seller_id", "channel_id", "condition", "city")}
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

        # Если таблица offer_price_history для офферов ещё пуста, подставляем текущие наблюдения из offers
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
