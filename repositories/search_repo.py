"""
Search Repository for KZ Price Hunter 2.0 (Unified Product Index).
Executes high-speed FTS5 product lookups and multi-channel offer retrieval.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional

from domain.models import CanonicalProduct
from repositories.base import BaseRepository

logger = logging.getLogger("kz_price_hunter.repositories.search")

TOKEN_SYNONYMS: Dict[str, List[str]] = {
    "айфон": ["iphone"],
    "iphone": ["айфон"],
    "про": ["pro"],
    "pro": ["про"],
    "макс": ["max"],
    "max": ["макс"],
    "плюс": ["plus"],
    "plus": ["плюс"],
    "ультра": ["ultra"],
    "ultra": ["ультра"],
    "мини": ["mini"],
    "mini": ["мини"],
    "самсунг": ["samsung"],
    "samsung": ["самсунг"],
    "сяоми": ["xiaomi"],
    "xiaomi": ["сяоми"],
    "редми": ["redmi"],
    "redmi": ["редми"],
    "поко": ["poco"],
    "poco": ["поко"],
    "асус": ["asus"],
    "asus": ["асус"],
    "макбук": ["macbook"],
    "macbook": ["макбук"],
    "айпад": ["ipad"],
    "ipad": ["айпад"],
    "сони": ["sony"],
    "sony": ["сони"],
}

STORAGE_TOKENS = {"64", "128", "256", "512", "1024"}


class SearchRepository(BaseRepository):
    """
    Coordinates FTS5 index searches over Canonical Products and collects active offers.
    """

    @classmethod
    def _build_fts_query(cls, text: str) -> str:
        """
        Builds a safe, robust FTS5 expression from input text with synonym expansion.
        Numbers are quoted strictly without wildcard; words use prefix matching.
        """
        clean = re.sub(r'[^\w\-\s]', ' ', text).strip()
        tokens = [t.strip().lower() for t in clean.split() if t.strip()]
        if not tokens:
            return ""

        parts = []
        for t in tokens:
            if t in TOKEN_SYNONYMS:
                alts = [t] + TOKEN_SYNONYMS[t]
                alts_expr = " OR ".join([f'"{alt}"*' for alt in alts])
                parts.append(f"({alts_expr})")
            elif t in STORAGE_TOKENS:
                parts.append(f'("{t}" OR "{t}gb"* OR "{t}гб"*)')
            elif t.isdigit():
                parts.append(f'"{t}"')
            elif len(t) >= 2:
                parts.append(f'"{t}"*')
            else:
                parts.append(f'"{t}"')

        return " AND ".join(parts)

    def search_products(
        self,
        search_text: str,
        brand: Optional[str] = None,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        channel_type: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: str = "relevance",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        1. Finds matching canonical products via FTS5 or LIKE fallback.
           Applies EXISTS clause on active offers so LIMIT is not wasted on products with no matching offers.
        2. Retrieves active offers for these products satisfying city/condition/price constraints.
        3. Returns aggregated structure grouped by product.
        """
        products_map: Dict[str, Dict[str, Any]] = {}
        ordered_product_ids: List[str] = []

        # -------------------------------------------------------------------
        # Build Offer Criteria for Candidate Filtering (C-E11-04)
        # -------------------------------------------------------------------
        exists_conditions = [
            "o.is_active = 1",
            "s.is_active = 1",
            "c.is_active = 1",
        ]
        exists_params: list = []

        if city and city != "Все":
            exists_conditions.append("(o.city = ? OR o.city LIKE ? OR o.city = 'Все' OR o.city LIKE '%Казахстан%')")
            exists_params.extend([city, f"%{city}%"])

        if condition:
            from domain.models import Condition
            norm_cond = Condition.normalize(condition)
            if norm_cond != Condition.UNKNOWN:
                exists_conditions.append("o.condition = ?")
                exists_params.append(norm_cond)

        if channel_type:
            exists_conditions.append("c.channel_type = ?")
            exists_params.append(channel_type)

        if min_price is not None and min_price > 0:
            exists_conditions.append("o.price >= ?")
            exists_params.append(min_price)

        if max_price is not None and max_price > 0:
            exists_conditions.append("o.price <= ?")
            exists_params.append(max_price)

        exists_sql = f"""
        AND EXISTS (
            SELECT 1 FROM offers o
            JOIN sellers s ON o.seller_id = s.id
            JOIN channels c ON o.channel_id = c.id
            WHERE o.product_id = p.id AND {" AND ".join(exists_conditions)}
        )
        """

        # A05: Сортировка до LIMIT
        order_by_sql = "rank ASC"
        order_by_like = "p.id ASC"
        order_params: list = []
        if sort_by == "price_asc":
            sub = f"""(
                SELECT MIN(o.price) FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                WHERE o.product_id = p.id AND {" AND ".join(exists_conditions)}
            ) ASC"""
            order_by_sql = sub
            order_by_like = sub
            order_params = list(exists_params)
        elif sort_by == "price_desc":
            sub = f"""(
                SELECT MAX(o.price) FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                WHERE o.product_id = p.id AND {" AND ".join(exists_conditions)}
            ) DESC"""
            order_by_sql = sub
            order_by_like = sub
            order_params = list(exists_params)

        # -------------------------------------------------------------------
        # Step 1: Match Canonical Products
        # -------------------------------------------------------------------
        fts_query = self._build_fts_query(search_text) if search_text else ""
        used_fts = False

        if fts_query:
            try:
                sql_fts = f"""
                SELECT p.*, bm25(canonical_products_fts) as rank
                FROM canonical_products_fts f
                JOIN canonical_products p ON f.rowid = p.rowid
                WHERE canonical_products_fts MATCH ?
                  {exists_sql}
                ORDER BY {order_by_sql}
                LIMIT ?
                """
                fts_params = [fts_query] + exists_params + order_params + [limit]
                rows = self.fetchall(sql_fts, fts_params)
                if rows:
                    used_fts = True
                    for r in rows:
                        pid = r["id"]
                        if pid not in products_map:
                            products_map[pid] = {
                                "product": CanonicalProduct.from_row(r),
                                "bm25_score": float(r["rank"]),
                                "offers": [],
                            }
                            ordered_product_ids.append(pid)
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 query failed, falling back to LIKE: {e}")
                used_fts = False

        # Fallback to LIKE matching if FTS is unavailable or gave 0 results
        if not used_fts or not products_map:
            tokens = [t.lower() for t in search_text.split() if t.strip()]
            where_clauses = ["1=1"]
            like_params: list = []

            for t in tokens:
                sub_tokens = [t]
                if t in TOKEN_SYNONYMS:
                    sub_tokens.extend(TOKEN_SYNONYMS[t])

                token_or = " OR ".join([
                    "(LOWER(title) LIKE ? OR LOWER(COALESCE(brand, '')) LIKE ? OR LOWER(COALESCE(model, '')) LIKE ? OR LOWER(category) LIKE ?)"
                    for _ in sub_tokens
                ])
                where_clauses.append(f"({token_or})")
                for st in sub_tokens:
                    pat = f"%{st}%"
                    like_params.extend([pat, pat, pat, pat])

            if brand:
                where_clauses.append("LOWER(COALESCE(brand, '')) = ?")
                like_params.append(brand.lower())

            sql_like = f"""
            SELECT p.* FROM canonical_products p
            WHERE {" AND ".join(where_clauses)}
              {exists_sql}
            ORDER BY {order_by_like}
            LIMIT ?
            """
            like_full_params = like_params + exists_params + order_params + [limit]
            rows = self.fetchall(sql_like, like_full_params)
            for r in rows:
                pid = r["id"]
                if pid not in products_map:
                    products_map[pid] = {
                        "product": CanonicalProduct.from_row(r),
                        "bm25_score": 1.0,
                        "offers": [],
                    }
                    ordered_product_ids.append(pid)

        if not ordered_product_ids:
            return []

        # -------------------------------------------------------------------
        # Step 2: Retrieve Matching Offers for Found Products
        # -------------------------------------------------------------------
        placeholders = ",".join(["?"] * len(ordered_product_ids))
        offer_select_conditions = [f"o.product_id IN ({placeholders})"] + exists_conditions
        offer_params: list = list(ordered_product_ids) + exists_params

        sql_offers = f"""
        SELECT o.*,
               s.name as seller_name,
               s.slug as seller_slug,
               s.rating as seller_rating,
               c.channel_type,
               c.name as channel_name
        FROM offers o
        JOIN sellers s ON o.seller_id = s.id
        JOIN channels c ON o.channel_id = c.id
        WHERE {" AND ".join(offer_select_conditions)}
        ORDER BY o.price ASC
        """
        offer_rows = self.fetchall(sql_offers, offer_params)

        for orow in offer_rows:
            od = dict(orow)
            pid = od["product_id"]
            if pid in products_map:
                products_map[pid]["offers"].append(od)

        return [products_map[pid] for pid in ordered_product_ids if products_map[pid]["offers"]]
