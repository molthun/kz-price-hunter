#!/usr/bin/env python3
"""
Исправляет категории товаров, сохранённые старым поиском синонимов по подстроке
("Электромясорубки" -> "Мясо и птица"). По умолчанию только показывает изменения.
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.category import TaxonomyMapper
from repositories.schema_v2 import rebuild_canonical_products_fts

# Сырая категория магазина -> куда её ошибочно относил старый поиск
WRONG_TARGETS = {
    "Электромясорубки": "Мясо и птица",
    "Кофемолки": "Напитки и соки",
    "Йогуртницы": "Молочные продукты",
    "Кухонные комбайны и машины": "Шины и диски",
    "Шлифовальные машины": "Шины и диски",
    "Tgrad: 🧺 Сушильные машины": "Шины и диски",
    "Детский мир: 🛴 Детский транспорт": "Спорт и отдых",
    "Автоматика для ворот Doorhan": "Автотовары",
    "Europharma: 🧴 Дермакосметика": "Декоративная косметика",
}


def fix(db_path: str, apply: bool) -> Counter:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    mapper = TaxonomyMapper()
    moved: Counter = Counter()
    updates = []
    rows = conn.execute(
        """
        SELECT cp.id, cp.category, cp.canonical_key,
               (SELECT p.category FROM products p WHERE p.canonical_key = cp.canonical_key LIMIT 1)
        FROM canonical_products cp
        """
    ).fetchall()
    for product_id, stored, key, offer_category in rows:
        raw = key.split(":")[1] if (key or "").startswith("norm:") and key.count(":") >= 2 else offer_category
        # Ключ содержит сырую категорию, в которой сам может быть префикс магазина с двоеточием
        if WRONG_TARGETS.get(offer_category) == stored:
            raw = offer_category
        if not raw:
            continue
        # Старая ошибка, либо сырая категория, которая раньше не сопоставлялась вовсе
        if WRONG_TARGETS.get(raw) != stored and raw != stored:
            continue
        target = mapper.map_category(raw)
        if target and target.name != stored:
            updates.append((target.name, product_id))
            moved[(raw, stored, target.name)] += 1

    if apply and updates:
        conn.executemany("UPDATE canonical_products SET category = ? WHERE id = ?", updates)
        rebuild_canonical_products_fts(conn)
        conn.commit()
    conn.close()
    return moved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/app/data/prices.db")
    parser.add_argument("--apply", action="store_true", help="Записать изменения")
    args = parser.parse_args()
    result = fix(args.db, args.apply)
    for (raw, old, new), count in result.most_common():
        print(f"{count:6d}  {raw}: {old} -> {new}")
    print(f"Всего: {sum(result.values())}, записано: {'да' if args.apply else 'нет'}")
