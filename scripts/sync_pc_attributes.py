#!/usr/bin/env python3
"""
Sync search attributes from titles and evidence for PC hardware, monitors, and devices.
Usage:
    python3 scripts/sync_pc_attributes.py [--db PATH]
"""
import argparse
import os
import sqlite3
import sys

from domain.attribute_indexing import (
    sync_all_source_attributes,
    index_product_facts,
    ATTRIBUTE_LABELS,
)

DDL = """
CREATE TABLE IF NOT EXISTS product_search_attributes (
    product_id TEXT NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence TEXT NOT NULL,
    source_title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(product_id, key)
);
CREATE INDEX IF NOT EXISTS idx_product_search_attr ON product_search_attributes(key, value, product_id);
"""


def main():
    parser = argparse.ArgumentParser(description="Sync PC hardware and monitor attributes")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "prices.db"), help="Path to SQLite db")
    args = parser.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        print(f"База данных {db_path} не найдена.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)

    print(f"🔄 Индексация характеристик в {db_path}...")
    total = sync_all_source_attributes(conn, limit=100000)
    conn.commit()
    print(f"✅ Проиндексировано записей атрибутов: {total}")

    rows = conn.execute("""
        SELECT key, COUNT(DISTINCT product_id) as p_count, COUNT(DISTINCT value) as v_count
        FROM product_search_attributes
        GROUP BY key
        ORDER BY p_count DESC
    """).fetchall()

    print("\n📊 Статистика по характеристикам в каталоге:")
    for r in rows:
        label = ATTRIBUTE_LABELS.get(r["key"], r["key"])
        print(f"  • {label} ({r['key']}): {r['p_count']} товаров, {r['v_count']} значений")

    conn.close()


if __name__ == "__main__":
    main()
