"""
Search Repository for KZ Price Hunter 2.0 (Unified Product Index).
Executes high-speed FTS5 product lookups and multi-channel offer retrieval.
"""

from __future__ import annotations

import json
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


def _attribute_value_variants(key: str, value: str) -> list[str]:
    """Возвращает список возможных вариантов написания одного нормализованного значения для поиска."""
    variants = [value]
    if key in ('storage', 'ram', 'vram'):
        m = re.match(r'^(\d+)\s*([ГТМ]Б)$', value)
        if m:
            num, unit = m.group(1), m.group(2)
            en_unit = 'TB' if unit == 'ТБ' else 'GB' if unit == 'ГБ' else 'MB'
            variants.extend([
                f"{num} {unit.lower()}", f"{num} {unit.capitalize()}",
                f"{num} {en_unit}", f"{num} {en_unit.lower()}", f"{num} {en_unit.capitalize()}",
                f"{num}{unit}", f"{num}{unit.lower()}", f"{num}{en_unit}", f"{num}{en_unit.lower()}",
                f"{num}G", f"{num}g", f"{num}GD", f"{num}gd"
            ])
            if unit == 'ТБ':
                gb_eq = int(num) * 1000
                gb_eq_bin = int(num) * 1024
                variants.extend([str(gb_eq), str(gb_eq_bin), f"{gb_eq} ГБ", f"{gb_eq_bin} ГБ", f"{gb_eq} GB", f"{gb_eq_bin} GB"])
    elif key == 'chipset':
        variants.extend([
            re.sub(r'^(?:GeForce|Radeon)\s+', '', value),
            f"NVIDIA {value}", f"AMD {value}"
        ])
    return list(dict.fromkeys(v for v in variants if v))



# Суффиксы, которые делают модель видеокарты другой моделью (A07).
GPU_MODEL_SUFFIXES = ("Ti Super", "Ti", "Super", "XTX", "XT", "GRE")


# Пробельные символы, которые не различают модели видеокарт: пробел, табуляция, переводы строки,
# неразрывный и узкий неразрывный пробелы (R03)
_GPU_SPACE_CODES = (32, 9, 10, 13, 160, 8239)


def _gpu_compact(model: str) -> str:
    """Модель видеокарты без пробельных символов и регистра: «RTX 4070  SUPER» → «rtx4070super»."""
    return "".join(ch for ch in (model or "").lower() if ord(ch) not in _GPU_SPACE_CODES)


def _sql_compact(expr: str) -> str:
    """Тот же вид в SQL: LOWER и удаление тех же пробельных символов."""
    out = f"LOWER({expr})"
    for code in _GPU_SPACE_CODES:
        out = f"REPLACE({out}, char({code}), '')"
    return out


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
            elif re.fullmatch(r"[а-яё]{5,}", t):
                alts = cls._russian_word_alts(t)
                alts_expr = " OR ".join(f'"{alt}"*' for alt in alts)
                parts.append(f"({alts_expr})" if len(alts) > 1 else alts_expr)
            elif len(t) >= 2:
                parts.append(f'"{t}"*')
            else:
                parts.append(f'"{t}"')

        return " AND ".join(parts)

    @staticmethod
    def _russian_word_alts(word: str) -> List[str]:
        """
        Формы слова для префиксного поиска: "мясорубки" находит "мясорубка" и "электромясорубка",
        множественное число "плиты", "бритвы" находит "плита", "бритва".
        Окончание у коротких слов единственного числа сохраняется: "машина" не должна находить "машинка".
        """
        if len(word) >= 5 and word[-1] in "ыи":
            stem = word[:-1]
        elif len(word) >= 7 and word[-1] in "аяыиуюеоь":
            stem = word[:-1]
        else:
            stem = word
        alts = [stem]
        if not stem.startswith("электро"):
            alts.append(f"электро{stem}")
        return alts

    @staticmethod
    def _offer_filter(
        city: Optional[str] = None,
        condition: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        gift: Optional[bool] = None,
        in_stock: Optional[bool] = None,
        delivery: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        now: Optional[datetime.datetime] = None,
    ) -> tuple[List[str], list]:
        """Условия к предложению (алиасы o, s, c) — единые для выдачи, счётчика и фасетов."""
        conditions = [
            "o.is_active = 1",
            "s.is_active = 1",
            "c.is_active = 1",
        ]
        params: list = []

        if city and city not in ("Все", "Казахстан"):
            conditions.append("(o.city = ? OR o.city LIKE ? OR o.city = 'Все' OR o.city LIKE '%Казахстан%')")
            params.extend([city, f"%{city}%"])

        if condition:
            from domain.models import Condition
            norm_cond = Condition.normalize(condition)
            if norm_cond != Condition.UNKNOWN:
                conditions.append("o.condition = ?")
                params.append(norm_cond)

        if channel_type:
            conditions.append("c.channel_type = ?")
            params.append(channel_type)

        seller_kind = str(seller_type or "").strip().lower()
        if seller_kind in PRIVATE_SELLER_TYPES:
            conditions.append(PRIVATE_SELLER_SQL)
        elif seller_kind in BUSINESS_SELLER_TYPES:
            conditions.append(f"NOT {PRIVATE_SELLER_SQL}")

        if seller_id and seller_id.strip():
            s_val = seller_id.strip()
            conditions.append("(o.seller_id = ? OR s.slug = ? OR s.name = ?)")
            params.extend([s_val, s_val, s_val])

        if gift:
            conditions.append("o.has_gift = 1")
        if installment:
            conditions.append("(o.installment_available = 1 OR (o.installment_months IS NOT NULL AND o.installment_months > 0))")

        if in_stock:
            # То же правило, что у «Нет в наличии» на карточке: у устаревшей цены availability остаётся in_stock
            from domain.purchasability import sql_purchasable_conditions
            purchasable, purchasable_params = sql_purchasable_conditions(now=now, channel="c")
            conditions.extend(purchasable)
            params.extend(purchasable_params)

        if delivery:
            del_val = str(delivery).strip().lower()
            if del_val == "delivery":
                conditions.append("(o.delivery_type IS NOT NULL AND o.delivery_type != '' AND o.delivery_type != 'pickup_only')")
            elif del_val == "pickup":
                conditions.append("(o.pickup_available = 1)")

        if min_price is not None and min_price > 0:
            conditions.append("o.price >= ?")
            params.append(min_price)

        if max_price is not None and max_price > 0:
            conditions.append("o.price <= ?")
            params.append(max_price)

        return conditions, params

    def _category_filter(self, category: Optional[str]) -> tuple[str, list]:
        sql, params = self._category_filter_only(category)
        for key, value in getattr(self, 'attribute_filters', {}).items():
            variants = _attribute_value_variants(key, value)
            if len(variants) > 1:
                placeholders = ",".join(["?"] * len(variants))
                sql += (f" AND EXISTS (SELECT 1 FROM product_search_attributes pa WHERE pa.product_id=p.id"
                        f" AND pa.key=? AND pa.value IN ({placeholders}) AND pa.source_title=p.title)")
                params.append(key)
                params.extend(variants)
            else:
                sql += (" AND EXISTS (SELECT 1 FROM product_search_attributes pa WHERE pa.product_id=p.id"
                        " AND pa.key=? AND pa.value=? AND pa.source_title=p.title)")
                params.extend([key, value])
        for key, value in getattr(self, 'inferred_attribute_filters', {}).items():
            sql += (" AND (p.id IN (SELECT value FROM json_each(?)) OR EXISTS ("
                    "SELECT 1 FROM product_search_attributes pa WHERE pa.product_id=p.id"
                    " AND pa.key=? AND pa.value=? AND pa.source_title=p.title))")
            params.extend([json.dumps(getattr(self, 'verified_scope', [])), key, value])
        scope = getattr(self, 'product_scope', None)
        if scope is not None:
            sql += " AND p.id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(scope))
        return sql, params

    def _category_filter_only(self, category: Optional[str]) -> tuple[str, list]:
        """S2-E17: Фильтрация по категории с учетом иерархии мастер-таксономии."""
        if not (category and category.strip()):
            return "", []
        raw_c = category.strip()
        try:
            from repositories.category_repo import CategoryRepository
            mapper = CategoryRepository(self.conn).get_mapper()
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
            return (
                f"AND (p.category IN ({placeholders}) OR LOWER(COALESCE(p.category, '')) = ?)",
                all_matching + [raw_c.lower()],
            )
        # LOWER в SQLite не сворачивает кириллицу: значение фасета сравнивается и как есть
        return (
            "AND (p.category = ? OR LOWER(COALESCE(p.category, '')) = ? OR LOWER(COALESCE(p.category, '')) LIKE ?)",
            [raw_c, raw_c.lower(), f"%{raw_c.lower()}%"],
        )

    @staticmethod
    def _brand_filter(brand: Optional[str]) -> tuple[str, list]:
        """S2-E18: Фильтрация по бренду."""
        if not (brand and brand.strip()):
            return "", []
        raw_b = brand.strip().lower()
        from domain.query_parser import BRAND_MAPPINGS
        canon_b = BRAND_MAPPINGS.get(raw_b, raw_b).lower()
        # SP30-08: Строгое сравнение производителя.
        # Если у товара явно заполнен brand (например, Acme), упоминание Apple в названии аксессуара
        # ('Acme adapter compatible with Apple iPhone') ни в коем случае не должно попадать в фильтр brand=Apple!
        # Резервный поиск по title разрешён ТОЛЬКО когда brand у товара в БД не указан (NULL или пустой).
        return (
            "AND ("
            "  LOWER(COALESCE(p.brand, '')) = ? OR LOWER(COALESCE(p.brand, '')) = ? "
            "  OR (COALESCE(p.brand, '') = '' AND (LOWER(p.title) LIKE ? OR LOWER(p.title) LIKE ?))"
            ")",
            [raw_b, canon_b, f"%{raw_b}%", f"%{canon_b}%"],
        )

    @staticmethod
    def _model_filter(model: Optional[str]) -> tuple[str, list]:
        """Фильтрация по канонической модели товара (без цвета и характеристик)."""
        if not (model and model.strip()) or model in ("Все", "Любая", "Все модели"):
            return "", []
        raw_m = model.strip()
        from catalog_classifier import canonical_gpu_model
        canon_gpu = canonical_gpu_model(raw_m)
        if canon_gpu:
            short_m = canon_gpu.replace("GeForce ", "").replace("Radeon ", "")
            # A07/R03 (аудиты 05.11.2026): «RTX 4060» не захватывает 4060 Ti/Super, а фильтр варианта находит
            # его при любом написании. Модель и название сравниваются в одном виде — без пробельных символов
            # (пробелы, табуляция, неразрывные): «RTX 4070  SUPER», «RTX4070 Super» и «RTX 4070 Super» совпадают.
            # По названию ищем только товары без своей GPU-модели и без значимого суффикса после искомой.
            key = _gpu_compact(short_m)
            excluded = [f"%{key}{_gpu_compact(suffix)}%" for suffix in GPU_MODEL_SUFFIXES]
            if key.endswith("xt"):
                excluded.append(f"%{key}x%")
            title_sql = _sql_compact("p.title")
            model_sql = _sql_compact("COALESCE(p.model, '')")
            not_like = "".join(f" AND {title_sql} NOT LIKE ?" for _ in excluded)
            no_gpu_model = (
                "(COALESCE(p.model, '') = '' OR (UPPER(p.model) NOT LIKE '%RTX%' AND UPPER(p.model) NOT LIKE '%GTX%' "
                "AND UPPER(p.model) NOT LIKE '%RX_%' AND UPPER(p.model) NOT LIKE '%ARC %'))"
            )
            # Дешёвое предусловие перед REPLACE по каждой строке: номер модели («4060») есть в поле model.
            # Без него сравнение без пробелов по всему каталогу медленнее в разы (стенд 05.11.2026).
            number = re.search(r"\d+", key)
            model_guard = "p.model LIKE ? AND " if number else ""
            guard_args = [f"%{number.group()}%"] if number else []
            return (
                f"AND (({model_guard}{model_sql} IN (?, ?)) "
                f"OR (p.category IN ('gpus', 'Видеокарты') AND {no_gpu_model} "
                f"AND {title_sql} LIKE ?{not_like}))",
                [*guard_args, _gpu_compact(canon_gpu), key, f"%{key}%", *excluded],
            )
        return (
            "AND (p.model = ? OR LOWER(COALESCE(p.model, '')) = ?)",
            [raw_m, raw_m.lower()],
        )

    @staticmethod
    def _kind_filter(kind: Optional[str]) -> tuple[str, list]:
        """Фильтрация по типу назначения: device (устройства), accessory (аксессуары), consumable (расходники)."""
        if not (kind and kind.strip()) or kind in ("all", "Все", "любые"):
            return "", []
        raw_k = kind.strip().lower()
        if raw_k in ("device", "accessory", "consumable"):
            return "AND p.item_kind = ?", [raw_k]
        return "", []

    @staticmethod
    def _build_model_facets(raw_facets: List[Any], category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Нормализует фасеты моделей: исключает бренды/мусор и агрегирует видеокарты по каноническим названиям."""
        from catalog_classifier import canonical_gpu_model, _ALL_BRANDS_LOWER

        is_gpu_cat = category in ("gpus", "Видеокарты") if category else False
        model_counts: Dict[str, int] = {}

        for r in raw_facets:
            val = (r["value"] or "").strip()
            if not val:
                continue
            v_low = val.lower()
            if v_low in _ALL_BRANDS_LOWER or v_low in {
                "v8", "11350 04", "pc", "pro", "mini", "plus", "max", "ultra",
                "gaming", "stand", "holder", "oc", "dual", "tuf", "rog"
            }:
                continue

            if is_gpu_cat or any(k in v_low for k in ("rtx", "gtx", "radeon", "geforce", "arc")):
                canon = canonical_gpu_model(val)
                if canon:
                    val = canon
                elif any(v_low.startswith(b) for b in ("amd", "nvidia", "intel", "asus", "msi", "palit", "gigabyte")):
                    continue

            model_counts[val] = model_counts.get(val, 0) + int(r["count"])

        sorted_models = sorted(model_counts.items(), key=lambda x: (-x[1], x[0]))[:40]
        return [{"model": k, "count": v} for k, v in sorted_models]

    @staticmethod
    def _like_clauses(search_text: str) -> tuple[List[str], list]:
        """Условия LIKE-поиска по токенам запроса с синонимами."""
        tokens = [t.lower() for t in (search_text or "").split() if t.strip()]
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
        return where_clauses, like_params

    def _matching_product_ids(
        self,
        search_text: str,
        offer_conditions: List[str],
        offer_params: list,
        brand: Optional[str],
        category: Optional[str],
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> tuple[str, list]:
        """
        Подзапрос id всей подходящей выборки без LIMIT: FTS5, а при его ошибке или пустом
        результате LIKE — то же правило, что в search_products.
        """
        exists_sql = f"""
        EXISTS (
            SELECT 1 FROM offers o
            JOIN sellers s ON o.seller_id = s.id
            JOIN channels c ON o.channel_id = c.id
            WHERE o.product_id = p.id AND {" AND ".join(offer_conditions)}
        )
        """
        cat_sql, cat_params = self._category_filter(category)
        brand_sql, brand_params = self._brand_filter(brand)
        model_sql, model_params = self._model_filter(model)
        kind_sql, kind_params = self._kind_filter(kind)

        fts_query = self._build_fts_query(search_text) if search_text else ""
        if fts_query:
            fts_sql = f"""
            SELECT p.id
            FROM canonical_products_fts f
            JOIN canonical_products p ON f.rowid = p.rowid
            WHERE canonical_products_fts MATCH ?
              {cat_sql}
              {brand_sql}
              {model_sql}
              {kind_sql}
              AND {exists_sql}
            """
            fts_params = [fts_query] + cat_params + brand_params + model_params + kind_params + offer_params
            try:
                if self.fetchone(f"SELECT 1 as found FROM ({fts_sql}) LIMIT 1", fts_params):
                    return fts_sql, fts_params
            except sqlite3.OperationalError:
                pass

        where_clauses, like_params = self._like_clauses(search_text)
        like_sql = f"""
        SELECT p.id FROM canonical_products p
        WHERE {" AND ".join(where_clauses)}
          {cat_sql}
          {brand_sql}
          {model_sql}
          {kind_sql}
          AND {exists_sql}
        """
        return like_sql, like_params + cat_params + brand_params + model_params + kind_params + offer_params

    def search_products(
        self,
        search_text: str,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        kind: Optional[str] = None,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        category: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        gift: Optional[bool] = None,
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
        exists_conditions, exists_params = self._offer_filter(
            city=city, condition=condition, channel_type=channel_type, seller_type=seller_type,
            seller_id=seller_id, installment=installment, gift=gift, in_stock=in_stock, delivery=delivery,
            min_price=min_price, max_price=max_price, now=now,
        )

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
                SELECT MIN(o.price) FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                WHERE o.product_id = p.id AND {" AND ".join(order_conditions)}
            )"""
            order_by_sql = f"CASE WHEN {sub} IS NULL THEN 1 ELSE 0 END ASC, {sub} DESC, p.id ASC"
            order_by_like = order_by_sql
            order_params = (list(exists_params) + purchasable_params) * 2

        cat_filter_sql, cat_filter_params = self._category_filter(category)
        brand_filter_sql, brand_filter_params = self._brand_filter(brand)
        model_filter_sql, model_filter_params = self._model_filter(model)
        kind_filter_sql, kind_filter_params = self._kind_filter(kind)

        # Исполнения одной модели — одна карточка: в выдаче остаётся самое дешёвое из подходящих.
        # Цена считается только у товаров с семейством, остальные остаются одни в своей группе.
        family_sql = f"""
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(p.family_id, p.id)
                ORDER BY CASE WHEN p.family_id IS NULL THEN 0 ELSE COALESCE((
                    SELECT MIN(o.price) FROM offers o
                    JOIN sellers s ON o.seller_id = s.id
                    JOIN channels c ON o.channel_id = c.id
                    WHERE o.product_id = p.id AND {" AND ".join(order_conditions)}
                ), 1e18) END, p.id
            ) AS family_row,
            COUNT(*) OVER (PARTITION BY COALESCE(p.family_id, p.id)) AS family_size
        """
        family_params = list(exists_params) + purchasable_params

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
                SELECT p.* FROM (
                    SELECT p.*, {family_sql} FROM (
                        SELECT p.*, bm25(canonical_products_fts) as rank
                        FROM canonical_products_fts f
                        JOIN canonical_products p ON f.rowid = p.rowid
                        WHERE canonical_products_fts MATCH ?
                          {cat_filter_sql}
                          {brand_filter_sql}
                          {model_filter_sql}
                          {kind_filter_sql}
                          {exists_sql}
                        LIMIT -1  -- bm25 работает только до оконных функций, запрос нельзя разворачивать
                    ) p
                ) p
                WHERE p.family_row = 1
                ORDER BY {order_by_sql}
                LIMIT ? OFFSET ?
                """
                fts_params = family_params + [fts_query] + cat_filter_params + brand_filter_params + model_filter_params + kind_filter_params + exists_params + order_params + [limit_val, offset_val]
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
                                "variants_count": r["family_size"] - 1,
                            }
                            ordered_product_ids.append(pid)
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 query failed, falling back to LIKE: {e}")
                used_fts = False

        # Fallback to LIKE matching if FTS is unavailable or gave 0 results
        if not used_fts or not products_map:
            where_clauses, like_params = self._like_clauses(search_text)

            if brand_filter_sql:
                where_clauses.append(brand_filter_sql.lstrip("AND "))
                like_params.extend(brand_filter_params)

            if cat_filter_sql:
                where_clauses.append(cat_filter_sql.lstrip("AND "))
                like_params.extend(cat_filter_params)

            if model_filter_sql:
                where_clauses.append(model_filter_sql.lstrip("AND "))
                like_params.extend(model_filter_params)

            if kind_filter_sql:
                where_clauses.append(kind_filter_sql.lstrip("AND "))
                like_params.extend(kind_filter_params)

            sql_like = f"""
            SELECT p.* FROM (
                SELECT p.*, {family_sql} FROM canonical_products p
                WHERE {" AND ".join(where_clauses)}
                  {exists_sql}
            ) p
            WHERE p.family_row = 1
            ORDER BY {order_by_like}
            LIMIT ? OFFSET ?
            """
            like_full_params = family_params + like_params + exists_params + order_params + [limit_val, offset_val]
            rows = self.fetchall(sql_like, like_full_params)
            for r in rows:
                pid = r["id"]
                if pid not in products_map:
                    products_map[pid] = {
                        "product": CanonicalProduct.from_row(r),
                        "bm25_score": 1.0,
                        "offers": [],
                        "variants_count": r["family_size"] - 1,
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

        self._attach_offer_titles([o for item in products_map.values() for o in item["offers"]])

        return [products_map[pid] for pid in ordered_product_ids if products_map[pid]["offers"]]

    def count_search_products(
        self,
        search_text: str = "",
        brand: Optional[str] = None,
        model: Optional[str] = None,
        kind: Optional[str] = None,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        category: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        gift: Optional[bool] = None,
        in_stock: Optional[bool] = None,
        delivery: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> Dict[str, int]:
        """
        Calculates exact total matching canonical products and offers count across the full dataset.
        """
        conditions, params = self._offer_filter(
            city=city, condition=condition, channel_type=channel_type, seller_type=seller_type,
            seller_id=seller_id, installment=installment, gift=gift, in_stock=in_stock, delivery=delivery,
            min_price=min_price, max_price=max_price,
        )
        ids_sql, ids_params = self._matching_product_ids(search_text, conditions, params, brand, category, model, kind)
        row = self.fetchone(
            f"""SELECT COUNT(DISTINCT COALESCE(p.family_id, p.id)) as cnt FROM canonical_products p
                WHERE p.id IN ({ids_sql})""",
            ids_params,
        )
        return {
            "total_products": int(row["cnt"]) if row else 0,
        }

    def get_search_facets(
        self,
        search_text: str = "",
        city: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        kind: Optional[str] = None,
        condition: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        gift: Optional[bool] = None,
        in_stock: Optional[bool] = None,
        delivery: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Фасеты по всей выборке, а не по странице; считаются SQL до пагинации.

        Значение фасета — число товаров, которое останется, если выбрать это значение: применяются
        все фильтры, кроме фильтра самой группы. Поэтому внутри группы можно переключиться на
        соседнее значение. Условия к продавцу и городу относятся к одному и тому же предложению.
        """
        offer_filters: Dict[str, Any] = dict(
            city=city, condition=condition, channel_type=channel_type, seller_type=seller_type,
            seller_id=seller_id, installment=installment, gift=gift, in_stock=in_stock, delivery=delivery,
            min_price=min_price, max_price=max_price,
        )
        conditions, params = self._offer_filter(**offer_filters)

        def product_facet(
            column: str,
            *,
            facet_brand: Optional[str],
            facet_category: Optional[str],
            facet_model: Optional[str] = None,
            facet_kind: Optional[str] = None,
            limit: int = 50,
        ) -> List[Any]:
            ids_sql, ids_params = self._matching_product_ids(
                search_text, conditions, params, facet_brand, facet_category, facet_model, facet_kind,
            )
            return self.fetchall(
                f"""
                SELECT p.{column} as value, COUNT(DISTINCT COALESCE(p.family_id, p.id)) as count
                FROM canonical_products p
                WHERE p.id IN ({ids_sql}) AND COALESCE(p.{column}, '') != ''
                GROUP BY p.{column}
                ORDER BY count DESC, value ASC
                LIMIT {limit}
                """,
                ids_params,
            )

        def offer_facet(select: str, group: str, extra: str, limit: int, drop: str) -> List[Any]:
            facet_conditions, facet_params = self._offer_filter(**{**offer_filters, drop: None})
            ids_sql, ids_params = self._matching_product_ids(
                search_text, facet_conditions, facet_params, brand, category, model, kind,
            )
            return self.fetchall(
                f"""
                SELECT {select}, COUNT(DISTINCT COALESCE(fp.family_id, o.product_id)) as count
                FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                JOIN canonical_products fp ON fp.id = o.product_id
                WHERE {" AND ".join(facet_conditions)}
                  AND o.product_id IN ({ids_sql})
                  {extra}
                GROUP BY {group}
                ORDER BY count DESC, {group} ASC
                LIMIT {limit}
                """,
                facet_params + ids_params,
            )

        facet_cat_for_group = None
        if category and category.strip():
            raw_c = category.strip()
            try:
                from repositories.category_repo import CategoryRepository
                m = CategoryRepository(self.conn).get_mapper()
                t_cat = m.map_category(raw_c) or m.get_by_id(raw_c)
                if t_cat:
                    children = m.get_children(t_cat.id)
                    if children:
                        # Если у категории есть дочерние подкатегории, фасеты строим по ней (показываем детей)
                        facet_cat_for_group = t_cat.id
                    else:
                        # Если категория листовая, показываем сестринские подкатегории под тем же родителем
                        facet_cat_for_group = t_cat.parent_id if t_cat.parent_id else t_cat.id
            except Exception:
                facet_cat_for_group = None

        return {
            "category_facets": [
                {"name": r["value"], "count": int(r["count"])}
                for r in product_facet("category", facet_brand=brand, facet_category=facet_cat_for_group, facet_model=model, facet_kind=kind)
            ],
            "brand_facets": [
                {"brand": r["value"], "count": int(r["count"])}
                for r in product_facet("brand", facet_brand=None, facet_category=category, facet_model=model, facet_kind=kind)
            ],
            "model_facets": self._build_model_facets(
                product_facet("model", facet_brand=brand, facet_category=category, facet_model=None, facet_kind=kind, limit=100),
                category=category,
            ),
            "kind_facets": [
                {"kind": r["value"], "count": int(r["count"])}
                for r in product_facet("item_kind", facet_brand=brand, facet_category=category, facet_model=model, facet_kind=None, limit=10)
            ],
            "seller_facets": [
                {"id": r["id"], "name": r["name"], "slug": r["slug"], "count": int(r["count"])}
                for r in offer_facet("s.id, s.name, s.slug", "s.id", "", 20, "seller_id")
            ],
            "city_facets": [
                {"city": r["city"], "count": int(r["count"])}
                for r in offer_facet(
                    "o.city", "o.city",
                    "AND o.city IS NOT NULL AND o.city != '' AND o.city != 'Все'", 15, "city",
                )
            ],
        }

    def count_cards_by_category(self, city: Optional[str] = None) -> Dict[str, int]:
        """Сколько карточек покажет выдача в каждом разделе: те же условия к предложениям и то же объединение
        исполнений. Ключ — id раздела; товар считается один раз, сумму по дереву даёт вызывающий."""
        from domain.category import TaxonomyMapper
        conditions, params = self._offer_filter(city=city)
        rows = self.fetchall(
            f"""
            SELECT p.category AS category, COUNT(DISTINCT COALESCE(p.family_id, p.id)) AS cards
            FROM canonical_products p
            WHERE COALESCE(p.category, '') != '' AND EXISTS (
                SELECT 1 FROM offers o
                JOIN sellers s ON o.seller_id = s.id
                JOIN channels c ON o.channel_id = c.id
                WHERE o.product_id = p.id AND {" AND ".join(conditions)}
            )
            GROUP BY p.category
            """,
            params,
        )
        from repositories.category_repo import CategoryRepository
        mapper = CategoryRepository(self.conn).get_mapper()
        counts: Dict[str, int] = {}
        for row in rows:
            mapped = mapper.map_category(row["category"])
            if mapped:
                counts[mapped.id] = counts.get(mapped.id, 0) + int(row["cards"])
        return counts

    def attribute_facets(self, arguments, schema):
        """Dynamic fields use the same entire SQL result set, before family grouping/LIMIT."""
        from domain.query_parser import QueryParser
        from domain.attribute_indexing import ATTRIBUTE_LABELS
        parsed = QueryParser.parse(arguments.get('query', ''))
        offer_args = {k: arguments.get(k) for k in (
            'city', 'condition', 'channel_type', 'seller_type', 'seller_id', 'installment',
            'gift', 'in_stock', 'delivery', 'min_price', 'max_price')}
        for k, val in [('city', parsed.detected_city), ('condition', parsed.condition),
                       ('channel_type', parsed.channel_type), ('min_price', parsed.min_price), ('max_price', parsed.max_price)]:
            if offer_args[k] is None:
                offer_args[k] = val
        conditions, params = self._offer_filter(**offer_args)
        chosen = dict(getattr(self, 'attribute_filters', {}))

        full_schema = dict(schema) if isinstance(schema, dict) else {}
        if not full_schema or len(full_schema) < 3:
            try:
                base_sql, base_values = self._matching_product_ids(
                    parsed.clean_query, conditions, params,
                    arguments.get('brand') or parsed.detected_brand, arguments.get('category'),
                    arguments.get('model'), arguments.get('kind')
                )
                top_keys = self.fetchall(f'''
                    SELECT pa.key, COUNT(DISTINCT pa.product_id) AS p_count
                    FROM product_search_attributes pa
                    JOIN canonical_products p ON p.id = pa.product_id
                    WHERE pa.source_title = p.title AND pa.product_id IN ({base_sql})
                    GROUP BY pa.key
                    ORDER BY p_count DESC
                    LIMIT 10
                ''', base_values)
                cat_id_for_suppression = None
                if arguments.get('category'):
                    try:
                        from repositories.category_repo import CategoryRepository
                        m_sup = CategoryRepository(self.conn).get_mapper()
                        c_obj = m_sup.map_category(arguments['category']) or m_sup.get_by_id(arguments['category'])
                        if c_obj:
                            cat_id_for_suppression = c_obj.id
                    except Exception:
                        cat_id_for_suppression = None

                incompatible_keys: Set[str] = set()
                if cat_id_for_suppression:
                    # Музыка/книги атрибуты не должны появляться в электронике и комплектующих
                    if cat_id_for_suppression in ('electronics', 'pc_components', 'psu', 'pc_cases', 'cooling', 'gpus', 'cpus', 'motherboards', 'ram', 'storage', 'hdd', 'phones', 'laptops', 'tablets', 'monitors', 'tv'):
                        incompatible_keys.update({'format', 'artist', 'genre', 'author', 'publisher', 'cover_type', 'diaper_type', 'diaper_size', 'weight_range', 'pieces_count'})
                    # Детские товары атрибуты не должны появляться в электронике
                    if cat_id_for_suppression in ('electronics', 'pc_components', 'psu', 'pc_cases', 'cooling', 'gpus', 'cpus', 'motherboards'):
                        incompatible_keys.update({'cleaning_type', 'dust_collector_type', 'refrigeration_defrost', 'defrost_system', 'load_capacity', 'spin_speed'})

                for r in top_keys:
                    k = r['key']
                    if k in ATTRIBUTE_LABELS and k not in full_schema and r['p_count'] >= 1 and k not in incompatible_keys:
                        full_schema[k] = {'label': ATTRIBUTE_LABELS.get(k, k)}
            except Exception:
                pass

        # Приоритетный порядок фасетов для ключевых категорий
        cat_key = arguments.get('category') or ''
        priority_orders = {
            'diapers_hygiene': ['diaper_type', 'diaper_size', 'weight_range', 'pieces_count'],
            'phones': ['storage', 'ram', 'color', 'screen_size', '5g', 'nfc'],
            'laptops': ['cpu', 'ram', 'storage', 'screen_size', 'gpu'],
            'refrigerators': ['refrigeration_defrost', 'energy_class', 'capacity', 'noise_level', 'color'],
            'washing_machines': ['load_capacity', 'spin_speed', 'energy_class', 'program_count', 'installation_type', 'color'],
            'dishwashers': ['installation_type', 'capacity_sets', 'width_cm', 'color', 'water_consumption'],
            'vacuums': ['type', 'cleaning_type', 'dust_collector_type', 'power', 'color'],
            'kitchen_appliances': ['type', 'power', 'capacity', 'color'],
            'psu': ['power', 'form_factor', 'color'],
            'pc_cases': ['form_factor', 'color', 'material'],
            'cooling': ['type', 'socket', 'power', 'color'],
            'gpus': ['vram', 'chipset', 'interface'],
            'cpus': ['socket', 'cores', 'frequency'],
            'motherboards': ['socket', 'chipset', 'form_factor'],
            'ram': ['ram', 'memory_type', 'frequency'],
            'storage': ['storage', 'form_factor'],
            'hdd': ['storage', 'form_factor'],
            'pc_components': ['power', 'form_factor', 'socket', 'chipset', 'vram', 'ram', 'storage', 'color'],
            'vinyl': ['artist', 'format', 'genre', 'release_year'],
            'books': ['author', 'genre', 'publisher', 'cover_type', 'release_year'],
        }
        order = priority_orders.get(cat_key, [])
        if not order and parsed.attributes.get('diaper_type'):
            order = priority_orders['diapers_hygiene']

        if order:
            ordered_schema = {}
            for k in order:
                if k in full_schema:
                    ordered_schema[k] = full_schema[k]
            for k, v in full_schema.items():
                if k not in ordered_schema:
                    ordered_schema[k] = v
            full_schema = ordered_schema

        facets = []
        try:
            for key, field in list(full_schema.items())[:12]:
                self.attribute_filters = {k:v for k,v in chosen.items() if k != key}
                sql, values = self._matching_product_ids(parsed.clean_query, conditions, params,
                    arguments.get('brand') or parsed.detected_brand, arguments.get('category'),
                    arguments.get('model'), arguments.get('kind'))
                rows = self.fetchall(f'''SELECT pa.value, COUNT(DISTINCT COALESCE(p.family_id,p.id)) AS count
                    FROM product_search_attributes pa JOIN canonical_products p ON p.id=pa.product_id
                    WHERE pa.key=? AND pa.source_title=p.title AND p.id IN ({sql}) GROUP BY pa.value ORDER BY count DESC,value LIMIT 80''',
                    [key] + values)
                if rows or key in chosen:
                    from domain.attribute_indexing import normalize_attribute_value
                    # Схлопываем одинаковые варианты (16 GB / 16 Гб / 16GB) в единое каноническое значение
                    norm_groups: Dict[str, int] = {}
                    for r in rows:
                        raw_v = r['value']
                        n_v = normalize_attribute_value(key, raw_v) or raw_v
                        if n_v and n_v.casefold() not in ('да', 'нет', 'true', 'false', 'unknown'):
                            norm_groups[n_v] = norm_groups.get(n_v, 0) + int(r['count'])

                    def _facet_sort_key(item):
                        val_str, count_val = item
                        if key in ('storage', 'ram', 'vram'):
                            m_num = re.search(r'^(\d+)\s*([ГТМ]Б)$', val_str)
                            if m_num:
                                mult = 1024 * 1024 if m_num.group(2) == 'ТБ' else 1024 if m_num.group(2) == 'ГБ' else 1
                                return (-int(m_num.group(1)) * mult, -count_val)
                        return (-count_val, val_str)

                    merged_rows = [{'value': k, 'count': v} for k, v in sorted(norm_groups.items(), key=_facet_sort_key)]
                    raw_label = field.get('label') if isinstance(field, dict) else None
                    display_label = raw_label or ATTRIBUTE_LABELS.get(key, key)
                    facets.append({'key': key, 'label': display_label, 'values': merged_rows})
        finally:
            self.attribute_filters = chosen
        return facets

    def get_family_products(self, family_id: str, city: Optional[str] = None) -> List[Dict[str, Any]]:
        """Исполнения одной модели с действующими предложениями и их самой низкой ценой."""
        city_sql = ""
        params: list = [family_id]
        if city and city not in ("Все", "Казахстан"):
            city_sql = "AND (o.city = ? OR o.city LIKE ? OR o.city = 'Все' OR o.city LIKE '%Казахстан%')"
            params.extend([city, f"%{city}%"])
        rows = self.fetchall(
            f"""
            SELECT p.id, p.title, MIN(o.price) AS min_price
            FROM canonical_products p
            JOIN offers o ON o.product_id = p.id AND o.is_active = 1
            WHERE p.family_id = ? {city_sql}
            GROUP BY p.id
            """,
            params,
        )
        return [dict(r) for r in rows]

    def _attach_offer_titles(self, offers: List[Dict[str, Any]]) -> None:
        """Название у магазина лежит в старой таблице products (id предложения: off_<id товара>)."""
        ids = [o["id"][4:] for o in offers if str(o["id"]).startswith("off_")]
        if not ids:
            return
        try:
            placeholders = ",".join("?" * len(ids))
            rows = self.fetchall(f"SELECT id, title FROM products WHERE id IN ({placeholders})", ids)
        except sqlite3.OperationalError:
            return                                  # база без старой схемы: карточка работает без выбора цвета
        titles = {r["id"]: r["title"] for r in rows}
        for offer in offers:
            offer["offer_title"] = titles.get(str(offer["id"])[4:])

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

        # Цвета одного исполнения могут лежать в соседних товарах: в карточке они вместе
        product_ids = [product.id]
        if row["family_id"]:
            from domain.product_options import same_variant_ids
            siblings = self.get_family_products(row["family_id"])
            product_ids += [i for i in same_variant_ids(product.category, product.title, siblings) if i != product.id]

        conditions = [
            f"o.product_id IN ({','.join('?' * len(product_ids))})",
            "o.is_active = 1",
            "s.is_active = 1",
            "c.is_active = 1",
        ]
        params: list = list(product_ids)

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

        self._attach_offer_titles(offers)

        return {
            "product": product,
            "offers": offers,
        }
