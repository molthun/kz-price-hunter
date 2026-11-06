"""
CategoryRepository for Category Architecture (Search Platform 2.0, Stage 17).

Provides SQLite persistence, hierarchical queries, seeding and offer aggregation.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from domain.category import Category, MASTER_TAXONOMY, TaxonomyMapper


class CategoryRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, category: Category) -> Category:
        self.conn.execute(
            """
            INSERT INTO categories (
                id, parent_id, name, icon, aliases_json, attributes_schema_json,
                search_priority, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_id = excluded.parent_id,
                name = excluded.name,
                icon = excluded.icon,
                aliases_json = excluded.aliases_json,
                attributes_schema_json = excluded.attributes_schema_json,
                search_priority = excluded.search_priority,
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                category.id,
                category.parent_id,
                category.name,
                category.icon,
                json.dumps(category.aliases, ensure_ascii=False),
                json.dumps(category.attributes_schema, ensure_ascii=False),
                category.search_priority,
                1 if category.is_active else 0,
                category.created_at,
                category.updated_at,
            ),
        )
        return category

    def get_by_id(self, category_id: str) -> Optional[Category]:
        cur = self.conn.execute(
            """
            SELECT id, parent_id, name, icon, aliases_json, attributes_schema_json,
                   search_priority, is_active, created_at, updated_at
            FROM categories WHERE id = ?
            """,
            (category_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return Category.from_row(dict(zip(cols, row)))

    def list_all(self, active_only: bool = True) -> List[Category]:
        sql = """
            SELECT id, parent_id, name, icon, aliases_json, attributes_schema_json,
                   search_priority, is_active, created_at, updated_at
            FROM categories
        """
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY search_priority DESC, name ASC"

        cur = self.conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [Category.from_row(dict(zip(cols, r))) for r in cur.fetchall()]

    def list_roots(self, active_only: bool = True) -> List[Category]:
        sql = """
            SELECT id, parent_id, name, icon, aliases_json, attributes_schema_json,
                   search_priority, is_active, created_at, updated_at
            FROM categories
            WHERE parent_id IS NULL
        """
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY search_priority DESC, name ASC"

        cur = self.conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [Category.from_row(dict(zip(cols, r))) for r in cur.fetchall()]

    def list_children(self, parent_id: str, active_only: bool = True) -> List[Category]:
        sql = """
            SELECT id, parent_id, name, icon, aliases_json, attributes_schema_json,
                   search_priority, is_active, created_at, updated_at
            FROM categories
            WHERE parent_id = ?
        """
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY search_priority DESC, name ASC"

        cur = self.conn.execute(sql, (parent_id,))
        cols = [d[0] for d in cur.description]
        return [Category.from_row(dict(zip(cols, r))) for r in cur.fetchall()]

    def seed_defaults_if_empty(self) -> int:
        """Засевает мастер-таксономию, если таблица categories пуста."""
        count = self.conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if count > 0:
            return 0
        inserted = 0
        for cat in MASTER_TAXONOMY:
            self.save(cat)
            inserted += 1
        return inserted

    def sync_master_taxonomy(self) -> int:
        """Синхронизирует или дополняет таблицу categories категориями из актуальной MASTER_TAXONOMY."""
        stored = {c.id: c for c in self.list_all(active_only=False)}
        synced = 0
        for cat in MASTER_TAXONOMY:
            old = stored.get(cat.id)
            if (old and old.aliases == cat.aliases and old.name == cat.name
                    and old.parent_id == cat.parent_id
                    and old.attributes_schema == cat.attributes_schema):
                continue
            if old:
                # Preserve reviewed dynamic filter definitions across taxonomy sync/restarts, while taking new schema updates.
                import copy
                cat = copy.deepcopy(cat)
                cat.attributes_schema = {**old.attributes_schema, **cat.attributes_schema}
            self.save(cat)
            synced += 1
        return synced

    def get_mapper(self) -> TaxonomyMapper:
        """Создает инстанс TaxonomyMapper на основе сохраненных в БД категорий."""
        cats = self.list_all(active_only=True)
        return TaxonomyMapper(cats if cats else MASTER_TAXONOMY)

    def count_products_by_category(self, city: Optional[str] = None) -> Dict[str, int]:
        """
        Подсчитывает число уникальных канонических товаров по категориям.
        Если указан конкретный город, учитываются только товары с активными предложениями в этом городе.
        """
        if city and city not in ("Все", "Казахстан"):
            sql = """
                SELECT p.category, COUNT(DISTINCT p.id)
                FROM canonical_products p
                JOIN offers o ON o.product_id = p.id
                WHERE o.is_active = 1 AND p.category IS NOT NULL AND p.category != '' AND o.city = ?
                GROUP BY p.category
            """
            rows = self.conn.execute(sql, [city]).fetchall()
        else:
            sql = """
                SELECT p.category, COUNT(DISTINCT p.id)
                FROM canonical_products p
                WHERE p.category IS NOT NULL AND p.category != ''
                GROUP BY p.category
            """
            rows = self.conn.execute(sql).fetchall()
        mapper = self.get_mapper()
        canonical_counts: Dict[str, int] = {}
        for r in rows:
            raw_cat = r[0]
            cnt = int(r[1])
            # Сохраняем сырой счетчик для прямой совместимости
            canonical_counts[raw_cat] = canonical_counts.get(raw_cat, 0) + cnt
            # И мапим в мастер-таксономию (id, name, parent_id)
            mapped = mapper.map_category(raw_cat)
            if mapped:
                if mapped.id != raw_cat:
                    canonical_counts[mapped.id] = canonical_counts.get(mapped.id, 0) + cnt
                if mapped.name != raw_cat and mapped.name != mapped.id:
                    canonical_counts[mapped.name] = canonical_counts.get(mapped.name, 0) + cnt
                if mapped.parent_id and mapped.parent_id != raw_cat:
                    canonical_counts[mapped.parent_id] = canonical_counts.get(mapped.parent_id, 0) + cnt
        return canonical_counts
