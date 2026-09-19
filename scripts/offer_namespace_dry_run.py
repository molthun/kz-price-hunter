"""Пробный прогон миграции 5 (R-H01) на копии базы: печатает план, ничего не меняет.

    python scripts/offer_namespace_dry_run.py path/to/copy/prices.db

Открывает базу только на чтение (mode=ro). Запускать на копии рабочей базы.
"""
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(path: str) -> int:
    from database import offer_namespace_plan
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    version = conn.execute("SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()
    plan = offer_namespace_plan(conn)
    print(f"schema_version: {version[0] if version else 0}")
    print(f"переименований: {len(plan['renames'])}, конфликтов (новый id занят): {len(plan['conflicts'])}, "
          f"смешанных записей (история сбрасывается): {len(plan['collided'])}")
    for shop, count in Counter(shop for _, _, shop in plan["renames"] + plan["conflicts"]).most_common():
        print(f"  {shop}: {count}")
    for old, new, shop in (plan["renames"] + plan["conflicts"])[:10]:
        print(f"  пример: {old} → {new} ({shop})")
    for pid in plan["collided"][:20]:
        shops = [r[0] for r in conn.execute("SELECT DISTINCT shop_key FROM product_sources WHERE product_id = ?", (pid,))]
        print(f"  смешано: {pid} ← {', '.join(shops)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
