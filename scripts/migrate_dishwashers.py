"""
Скрипт миграции категории Посудомоечные машины (dishwashers) и сопутствующей бытовой химии.

1. Синхронизирует мастер-таксономию в SQLite (CategoryRepository.sync_master_taxonomy).
2. Выделяет посудомоечные машины в категорию 'Посудомоечные машины' (dishwashers).
3. Переносит таблетки/капсулы/соль/ополаскиватели из 'Посуда и кухня' / 'Техника для кухни' в 'Бытовая химия и хозтовары' (household_chem, consumable).
4. Корректно обновляет product_category_tags перед canonical_products с учетом триггера classification_category_guard.
5. Запускает sync_all_source_attributes для индексации атрибутов (тип установки, ширина, вместимость).
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repositories.category_repo import CategoryRepository
from domain.attribute_indexing import sync_all_source_attributes, extract_title_attributes


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_consumable_title(title: str) -> bool:
    t = title.lower()
    return bool(re.search(
        r'\b(таблетк|капсул|соль|ополаскивател|средств|очистител|гель|порошок|finish|somat|fairy|biomio|synergetic|dr\.?\s*beckmann|pril|yokosun|oiko|mr\.?joby|calvatis|eco&clean)\b',
        t
    ))


def is_toy_title(title: str) -> bool:
    t = title.lower()
    return bool(re.search(r'\b(игруш|игрушечн|детск|на\s+бат|свет/звук|b1184864)\b', t))


def is_actual_dishwasher(title: str) -> bool:
    t = title.lower()
    if is_consumable_title(t) or is_toy_title(t):
        return False
    return bool(re.search(r'\b(посудомоечн\w*|посудомойк\w*|dishwasher)\b', t))


def migrate_dishwashers(db_path: str, dry_run: bool = False):
    print(f"[{now_iso()}] Opening database: {db_path} (dry_run={dry_run})")
    conn = sqlite3.connect(db_path)

    # 1. Синхронизация мастер-таксономии
    cat_repo = CategoryRepository(conn)
    synced_cats = cat_repo.sync_master_taxonomy()
    print(f"[{now_iso()}] Synced master taxonomy categories: {synced_cats}")
    if not dry_run:
        conn.commit()

    # Check if item_kind exists in canonical_products
    cols = {r[1] for r in conn.execute('PRAGMA table_info(canonical_products)').fetchall()}
    has_item_kind = 'item_kind' in cols

    # 2. Поиск всех товаров со словом посудомо*
    rows = conn.execute(
        "SELECT id, title, category FROM canonical_products WHERE title LIKE '%посудомо%'"
    ).fetchall()
    print(f"[{now_iso()}] Found {len(rows)} products with 'посудомо'")

    dishwashers_moved = 0
    consumables_moved = 0

    for pid, title, current_cat in rows:
        if is_actual_dishwasher(title):
            target_cat_name = "Посудомоечные машины"
            target_cat_id = "dishwashers"
            target_kind = "device"
            dishwashers_moved += 1
            print(f"  [DISHWASHER] {title} -> {target_cat_name} ({target_kind}) (was: {current_cat})")
        elif is_consumable_title(title):
            target_cat_name = "Бытовая химия и хозтовары"
            target_cat_id = "household_chem"
            target_kind = "consumable"
            consumables_moved += 1
            print(f"  [CONSUMABLE] {title} -> {target_cat_name} ({target_kind}) (was: {current_cat})")
        else:
            print(f"  [SKIPPED] {title} (current: {current_cat})")
            continue

        if not dry_run:
            # 1. product_category_tags обновляем первым (с учетом триггера classification_category_guard)
            conn.execute(
                """
                INSERT INTO product_category_tags (product_id, category_id, source, updated_at)
                VALUES (?, ?, 'rules', ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    category_id = excluded.category_id,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (pid, target_cat_id, now_iso()),
            )

            # 2. canonical_products
            if has_item_kind:
                conn.execute(
                    """
                    UPDATE canonical_products
                    SET category = ?, item_kind = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (target_cat_name, target_kind, now_iso(), pid),
                )
            else:
                conn.execute(
                    """
                    UPDATE canonical_products
                    SET category = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (target_cat_name, now_iso(), pid),
                )

    if not dry_run:
        conn.commit()
        print(f"[{now_iso()}] Committed category migrations: {dishwashers_moved} dishwashers, {consumables_moved} consumables")

        # 3. Индексация атрибутов
        print(f"[{now_iso()}] Syncing attributes for updated products...")
        attrs_synced = sync_all_source_attributes(conn)
        print(f"[{now_iso()}] Synced {attrs_synced} product attributes")
        conn.commit()

    conn.close()
    print(f"[{now_iso()}] Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate dishwashers to standalone category")
    parser.add_argument("--db", default="prices.db", help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    args = parser.parse_args()
    migrate_dishwashers(args.db, dry_run=args.dry_run)
