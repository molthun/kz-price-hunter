"""
Search Repository for KZ Price Hunter 2.0 (Unified Product Index).
Executes high-speed FTS5 product lookups and multi-channel offer retrieval.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import datetime
import time
from typing import Any, Dict, List, Optional, Set

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

# Единственное правило «частный продавец» — и для отбора до LIMIT, и для метки в выдаче.
# Повреждённый metadata_json не роняет запрос (json_valid) и не делает продавца частным.
# LIKE в SQLite не сворачивает регистр кириллицы, поэтому написания перечислены явно.
_METADATA_FLAG_SQL = (
    "COALESCE(CASE WHEN json_valid(s.metadata_json) THEN json_extract(s.metadata_json, '{path}') END, 0) "
    "NOT IN (0, '', 'false')"
)
PRIVATE_SELLER_SQL = (
    "(c.channel_type IN ('classifieds', 'olx')"
    f" OR {_METADATA_FLAG_SQL.format(path='$.is_private')}"
    f" OR {_METADATA_FLAG_SQL.format(path='$.is_private_seller')}"
    " OR s.name LIKE '%частн%' OR s.name LIKE '%Частн%' OR s.name LIKE '%ЧАСТН%')"
)
PRIVATE_SELLER_TYPES = {"private", "private_seller", "classifieds"}
BUSINESS_SELLER_TYPES = {"business", "company", "retail"}


class SearchRepository(BaseRepository):
    """
    Coordinates FTS5 index searches over Canonical Products and collects active offers.
    """

    _cat_cache: Dict[str, Set[str]] = {}
    _cat_cache_time: float = 0.0

    def _get_db_categories_for_subtree(self, mapper, subtree_ids: Set[str]) -> Set[str]:
        now = time.time()
        if not SearchRepository._cat_cache or (now - SearchRepository._cat_cache_time > 300.0):
            try:
                cur = self.conn.execute(
                    "SELECT DISTINCT category FROM canonical_products WHERE category IS NOT NULL AND category != ''"
                )
                cat_map: Dict[str, Set[str]] = {}
                for row in cur.fetchall():
                    raw_cat = row[0]
                    mapped = mapper.map_category(raw_cat)
                    if mapped:
                        cat_map.setdefault(mapped.id, set()).add(raw_cat)
                        if mapped.parent_id:
                            cat_map.setdefault(mapped.parent_id, set()).add(raw_cat)
                SearchRepository._cat_cache = cat_map
                SearchRepository._cat_cache_time = now
            except Exception:
                return set()

        result: Set[str] = set()
        for cid in subtree_ids:
            result.update(SearchRepository._cat_cache.get(cid, set()))
        return result

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
        category: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        in_stock: Optional[bool] = None,
        delivery: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: str = "relevance",
        limit: int = 50,
        offset: int = 0,
        now: Optional[datetime.datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        1. Finds matching canonical products via FTS5 or LIKE fallback.
           Applies EXISTS clause on active offers so LIMIT is not wasted on products with no matching offers.
        2. Retrieves active offers for these products satisfying city/condition/price constraints.
        3. Returns aggregated structure grouped by product with stable tie-breaker and pagination.
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

        if city and city not in ("Все", "Казахстан"):
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

        seller_kind = str(seller_type or "").strip().lower()
        if seller_kind in PRIVATE_SELLER_TYPES:
            exists_conditions.append(PRIVATE_SELLER_SQL)
        elif seller_kind in BUSINESS_SELLER_TYPES:
            exists_conditions.append(f"NOT {PRIVATE_SELLER_SQL}")

        if seller_id and seller_id.strip():
            s_val = seller_id.strip()
            exists_conditions.append("(o.seller_id = ? OR s.slug = ? OR s.name = ?)")
            exists_params.extend([s_val, s_val, s_val])

        if installment:
            exists_conditions.append("(o.installment_available = 1 OR (o.installment_months IS NOT NULL AND o.installment_months > 0))")

        if in_stock:
            exists_conditions.append("(o.availability = 'in_stock')")

        if delivery:
            del_val = str(delivery).strip().lower()
            if del_val == "delivery":
                exists_conditions.append("(o.delivery_type IS NOT NULL AND o.delivery_type != '' AND o.delivery_type != 'pickup_only')")
            elif del_val == "pickup":
                exists_conditions.append("(o.pickup_available = 1)")

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

        # A05, B10, C07, R07, R09: ключ сортировки до LIMIT — цена только покупаемых предложений,
        # по тому же правилу и моменту, что итоговая карточка (domain.purchasability)
        from domain.purchasability import sql_purchasable_conditions
        purchasable_sql, purchasable_params = sql_purchasable_conditions(now, channel="c")
        order_conditions = list(exists_conditions) + purchasable_sql

        order_by_sql = "rank ASC, p.id ASC"
        order_by_like = "p.id ASC"
        order_params: list = []
        if sort_by == "price_asc":
            sub = f"""(
                SELECT MIN(o.price) FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                WHERE o.product_id = p.id AND {" AND ".join(order_conditions)}
            )"""
            order_by_sql = f"CASE WHEN {sub} IS NULL THEN 1 ELSE 0 END ASC, {sub} ASC, p.id ASC"
            order_by_like = order_by_sql
            order_params = (list(exists_params) + purchasable_params) * 2
        elif sort_by == "price_desc":
            sub = f"""(
                SELECT MAX(o.price) FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                WHERE o.product_id = p.id AND {" AND ".join(order_conditions)}
            )"""
            order_by_sql = f"CASE WHEN {sub} IS NULL THEN 1 ELSE 0 END ASC, {sub} DESC, p.id ASC"
            order_by_like = order_by_sql
            order_params = (list(exists_params) + purchasable_params) * 2

        # S2-E17: Фильтрация по категории с учетом иерархии мастер-таксономии
        cat_filter_sql = ""
        cat_filter_params: list = []
        if category and category.strip():
            raw_c = category.strip()
            try:
                from domain.category import TaxonomyMapper
                mapper = TaxonomyMapper()
                target_cat = mapper.map_category(raw_c) or mapper.get_by_id(raw_c)
            except Exception:
                target_cat = None

            if target_cat:
                subtree = mapper.get_subtree_ids(target_cat.id)
                names = {c.name for cid in subtree if (c := mapper.get_by_id(cid))}
                names.update(c.name.lower() for cid in subtree if (c := mapper.get_by_id(cid)))
                names.update(cid for cid in subtree)
                names.update(cid.lower() for cid in subtree)
                for cid in subtree:
                    c = mapper.get_by_id(cid)
                    if c:
                        names.update(c.aliases)
                        names.update(a.lower() for a in c.aliases)

                # Добавляем все сырые категории магазинов из базы данных, отображающиеся в поддерево
                db_raw_cats = self._get_db_categories_for_subtree(mapper, subtree)
                all_matching = list(names.union(db_raw_cats))
                placeholders = ",".join(["?"] * len(all_matching))
                cat_filter_sql = f"AND (p.category IN ({placeholders}) OR LOWER(COALESCE(p.category, '')) = ?)"
                cat_filter_params = all_matching + [raw_c.lower()]
            else:
                cat_filter_sql = "AND (LOWER(COALESCE(p.category, '')) = ? OR LOWER(COALESCE(p.category, '')) LIKE ?)"
                cat_filter_params = [raw_c.lower(), f"%{raw_c.lower()}%"]

        # S2-E18: Фильтрация по бренду
        brand_filter_sql = ""
        brand_filter_params: list = []
        if brand and brand.strip():
            raw_b = brand.strip().lower()
            from domain.query_parser import BRAND_MAPPINGS
            canon_b = BRAND_MAPPINGS.get(raw_b, raw_b).lower()
            # SP30-08: Строгое сравнение производителя.
            # Если у товара явно заполнен brand (например, Acme), упоминание Apple в названии аксессуара
            # ('Acme adapter compatible with Apple iPhone') ни в коем случае не должно попадать в фильтр brand=Apple!
            # Резервный поиск по title разрешён ТОЛЬКО когда brand у товара в БД не указан (NULL или пустой).
            brand_filter_sql = (
                "AND ("
                "  LOWER(COALESCE(p.brand, '')) = ? OR LOWER(COALESCE(p.brand, '')) = ? "
                "  OR (COALESCE(p.brand, '') = '' AND (LOWER(p.title) LIKE ? OR LOWER(p.title) LIKE ?))"
                ")"
            )
            brand_filter_params = [raw_b, canon_b, f"%{raw_b}%", f"%{canon_b}%"]

        # -------------------------------------------------------------------
        # Step 1: Match Canonical Products
        # -------------------------------------------------------------------
        fts_query = self._build_fts_query(search_text) if search_text else ""
        used_fts = False
        limit_val = max(1, min(limit, 100))
        offset_val = max(0, offset)

        if fts_query:
            try:
                sql_fts = f"""
                SELECT p.*, bm25(canonical_products_fts) as rank
                FROM canonical_products_fts f
                JOIN canonical_products p ON f.rowid = p.rowid
                WHERE canonical_products_fts MATCH ?
                  {cat_filter_sql}
                  {brand_filter_sql}
                  {exists_sql}
                ORDER BY {order_by_sql}
                LIMIT ? OFFSET ?
                """
                fts_params = [fts_query] + cat_filter_params + brand_filter_params + exists_params + order_params + [limit_val, offset_val]
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

            if brand_filter_sql:
                where_clauses.append(brand_filter_sql.lstrip("AND "))
                like_params.extend(brand_filter_params)

            if cat_filter_sql:
                where_clauses.append(cat_filter_sql.lstrip("AND "))
                like_params.extend(cat_filter_params)

            sql_like = f"""
            SELECT p.* FROM canonical_products p
            WHERE {" AND ".join(where_clauses)}
              {exists_sql}
            ORDER BY {order_by_like}
            LIMIT ? OFFSET ?
            """
            like_full_params = like_params + exists_params + order_params + [limit_val, offset_val]
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
               s.domain as seller_domain,
               s.phone as seller_phone,
               s.bin as seller_bin,
               s.legal_name as seller_legal_name,
               s.metadata_json as seller_metadata_json,
               {PRIVATE_SELLER_SQL} as is_private_seller,
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
            od["is_private_seller"] = bool(od.get("is_private_seller"))

            pid = od["product_id"]
            if pid in products_map:
                products_map[pid]["offers"].append(od)

        return [products_map[pid] for pid in ordered_product_ids if products_map[pid]["offers"]]

    def count_search_products(
        self,
        search_text: str = "",
        brand: Optional[str] = None,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        category: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        in_stock: Optional[bool] = None,
        delivery: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> Dict[str, int]:
        """
        Calculates exact total matching canonical products and offers count across the full dataset.
        """
        exists_conditions = [
            "o.is_active = 1",
            "s.is_active = 1",
            "c.is_active = 1",
        ]
        exists_params: list = []

        if city and city not in ("Все", "Казахстан"):
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

        seller_kind = str(seller_type or "").strip().lower()
        if seller_kind in PRIVATE_SELLER_TYPES:
            exists_conditions.append(PRIVATE_SELLER_SQL)
        elif seller_kind in BUSINESS_SELLER_TYPES:
            exists_conditions.append(f"NOT {PRIVATE_SELLER_SQL}")

        if seller_id and seller_id.strip():
            s_val = seller_id.strip()
            exists_conditions.append("(o.seller_id = ? OR s.slug = ? OR s.name = ?)")
            exists_params.extend([s_val, s_val, s_val])

        if installment:
            exists_conditions.append("(o.installment_available = 1 OR (o.installment_months IS NOT NULL AND o.installment_months > 0))")

        if in_stock:
            exists_conditions.append("(o.availability = 'in_stock')")

        if delivery:
            del_val = str(delivery).strip().lower()
            if del_val == "delivery":
                exists_conditions.append("(o.delivery_type IS NOT NULL AND o.delivery_type != '' AND o.delivery_type != 'pickup_only')")
            elif del_val == "pickup":
                exists_conditions.append("(o.pickup_available = 1)")

        if min_price is not None and min_price > 0:
            exists_conditions.append("o.price >= ?")
            exists_params.append(min_price)

        if max_price is not None and max_price > 0:
            exists_conditions.append("o.price <= ?")
            exists_params.append(max_price)

        exists_sql = f"""
        EXISTS (
            SELECT 1 FROM offers o
            JOIN sellers s ON o.seller_id = s.id
            JOIN channels c ON o.channel_id = c.id
            WHERE o.product_id = p.id AND {" AND ".join(exists_conditions)}
        )
        """

        cat_filter_sql = ""
        cat_filter_params: list = []
        if category and category.strip():
            raw_c = category.strip()
            try:
                from domain.category import TaxonomyMapper
                mapper = TaxonomyMapper()
                target_cat = mapper.map_category(raw_c) or mapper.get_by_id(raw_c)
            except Exception:
                target_cat = None

            if target_cat:
                subtree = mapper.get_subtree_ids(target_cat.id)
                names = {c.name for cid in subtree if (c := mapper.get_by_id(cid))}
                names.update(c.name.lower() for cid in subtree if (c := mapper.get_by_id(cid)))
                names.update(cid for cid in subtree)
                names.update(cid.lower() for cid in subtree)
                for cid in subtree:
                    c = mapper.get_by_id(cid)
                    if c:
                        names.update(c.aliases)
                        names.update(a.lower() for a in c.aliases)

                db_raw_cats = self._get_db_categories_for_subtree(mapper, subtree)
                all_matching = list(names.union(db_raw_cats))
                placeholders = ",".join(["?"] * len(all_matching))
                cat_filter_sql = f"AND (p.category IN ({placeholders}) OR LOWER(COALESCE(p.category, '')) = ?)"
                cat_filter_params = all_matching + [raw_c.lower()]
            else:
                cat_filter_sql = "AND (LOWER(COALESCE(p.category, '')) = ? OR LOWER(COALESCE(p.category, '')) LIKE ?)"
                cat_filter_params = [raw_c.lower(), f"%{raw_c.lower()}%"]

        brand_filter_sql = ""
        brand_filter_params: list = []
        if brand and brand.strip():
            raw_b = brand.strip().lower()
            from domain.query_parser import BRAND_MAPPINGS
            canon_b = BRAND_MAPPINGS.get(raw_b, raw_b).lower()
            brand_filter_sql = (
                "AND ("
                "  LOWER(COALESCE(p.brand, '')) = ? OR LOWER(COALESCE(p.brand, '')) = ? "
                "  OR (COALESCE(p.brand, '') = '' AND (LOWER(p.title) LIKE ? OR LOWER(p.title) LIKE ?))"
                ")"
            )
            brand_filter_params = [raw_b, canon_b, f"%{raw_b}%", f"%{canon_b}%"]

        fts_query = self._build_fts_query(search_text) if search_text else ""
        used_fts = False
        total_products = 0

        if fts_query:
            try:
                count_sql = f"""
                SELECT COUNT(DISTINCT p.id) as cnt
                FROM canonical_products_fts f
                JOIN canonical_products p ON f.rowid = p.rowid
                WHERE canonical_products_fts MATCH ?
                  {cat_filter_sql}
                  {brand_filter_sql}
                  AND {exists_sql}
                """
                fts_params = [fts_query] + cat_filter_params + brand_filter_params + exists_params
                row = self.fetchone(count_sql, fts_params)
                if row and row["cnt"] > 0:
                    used_fts = True
                    total_products = int(row["cnt"])
            except sqlite3.OperationalError:
                used_fts = False

        if not used_fts:
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

            if brand_filter_sql:
                where_clauses.append(brand_filter_sql.lstrip("AND "))
                like_params.extend(brand_filter_params)

            if cat_filter_sql:
                where_clauses.append(cat_filter_sql.lstrip("AND "))
                like_params.extend(cat_filter_params)

            where_clauses.append(exists_sql.lstrip("AND "))
            count_like = f"SELECT COUNT(DISTINCT p.id) as cnt FROM canonical_products p WHERE {' AND '.join(where_clauses)}"
            row = self.fetchone(count_like, like_params + exists_params)
            total_products = int(row["cnt"]) if row else 0

        return {
            "total_products": total_products,
        }

    def get_search_facets(
        self,
        search_text: str = "",
        city: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieves global facets for category, brand, sellers, and cities across matching catalog items.
        """
        # Collect distinct sellers and cities available
        sellers_sql = """
        SELECT s.id, s.name, s.slug, COUNT(DISTINCT o.product_id) as count
        FROM sellers s
        JOIN offers o ON o.seller_id = s.id
        WHERE s.is_active = 1 AND o.is_active = 1
        GROUP BY s.id
        ORDER BY count DESC
        LIMIT 20
        """
        seller_facets = [
            {"id": r["id"], "name": r["name"], "slug": r["slug"], "count": int(r["count"])}
            for r in self.fetchall(sellers_sql)
        ]

        cities_sql = """
        SELECT o.city, COUNT(DISTINCT o.product_id) as count
        FROM offers o
        WHERE o.is_active = 1 AND o.city IS NOT NULL AND o.city != '' AND o.city != 'Все'
        GROUP BY o.city
        ORDER BY count DESC
        LIMIT 15
        """
        city_facets = [
            {"city": r["city"], "count": int(r["count"])}
            for r in self.fetchall(cities_sql)
        ]

        return {
            "seller_facets": seller_facets,
            "city_facets": city_facets,
        }

    def get_canonical_product_detail(
        self,
        product_id: str,
        city: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves canonical product and its active offers joined with sellers and channels.
        Matches by product.id or canonical_key.
        """
        row = self.fetchone(
            "SELECT * FROM canonical_products WHERE id = ? OR canonical_key = ?",
            (product_id, product_id),
        )
        if not row:
            return None
        product = CanonicalProduct.from_row(row)

        conditions = [
            "o.product_id = ?",
            "o.is_active = 1",
            "s.is_active = 1",
            "c.is_active = 1",
        ]
        params: list = [product.id]

        if city and city not in ("Все", "Казахстан"):
            conditions.append("(o.city = ? OR o.city LIKE ? OR o.city = 'Все' OR o.city LIKE '%Казахстан%')")
            params.extend([city, f"%{city}%"])

        sql_offers = f"""
        SELECT o.*,
               s.name as seller_name,
               s.slug as seller_slug,
               s.rating as seller_rating,
               s.domain as seller_domain,
               s.phone as seller_phone,
               s.bin as seller_bin,
               s.legal_name as seller_legal_name,
               s.metadata_json as seller_metadata_json,
               {PRIVATE_SELLER_SQL} as is_private_seller,
               c.channel_type,
               c.name as channel_name
        FROM offers o
        JOIN sellers s ON o.seller_id = s.id
        JOIN channels c ON o.channel_id = c.id
        WHERE {" AND ".join(conditions)}
        ORDER BY o.price ASC
        """
        offer_rows = self.fetchall(sql_offers, params)
        offers = []
        for r in offer_rows:
            od = dict(r)
            od["is_private_seller"] = bool(od.get("is_private_seller"))
            offers.append(od)

        return {
            "product": product,
            "offers": offers,
        }
