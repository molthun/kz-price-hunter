#!/usr/bin/env python3
"""
Исправляет категории товаров, сохранённые старым поиском синонимов по подстроке
("Электромясорубки" -> "Мясо и птица"). По умолчанию только показывает изменения.
"""
import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog_classifier import CatalogClassifier
from domain.category import MASTER_TAXONOMY, TaxonomyMapper
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
    "Машинки для стрижки, триммеры": "Игрушки и конструкторы",
    "Детский мир: 🚗 Автокресла": "Дом и сад",
    "Французский Дом: 💆\u200d♀️ Уход за волосами": "Кондиционеры",
    "Сковороды, сотейники": "Техника для кухни",
    "Тостеры, гриль": "Электроника",
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
        # norm:<сырая категория>:<название>; сама категория может содержать двоеточие после магазина
        raw = key[5:].rsplit(":", 1)[0] if (key or "").startswith("norm:") else offer_category
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


def _root(mapper: TaxonomyMapper, category) -> str:
    while category.parent_id:
        category = mapper.get_by_id(category.parent_id)
    return category.id


def fix_cross_root(db_path: str, apply: bool) -> Counter:
    """Товар лежит в другом корневом разделе, чем говорит раздел магазина: SSD в шинах, гель для стирки в красоте.

    Решения локальной модели не трогаются. Разделы магазина со слишком общим названием пропускаются:
    в «Запчастях» бывают и детали пылесосов, а в «Играх» геймпады.
    """
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    mapper = TaxonomyMapper()
    classifier = CatalogClassifier()
    by_name = {c.name: c for c in MASTER_TAXONOMY}
    moved: Counter = Counter()
    updates = []
    rows = conn.execute(
        """SELECT id, title, category, canonical_key FROM canonical_products
           WHERE id NOT IN (SELECT product_id FROM category_ai_verdicts WHERE applied = 1)"""
    ).fetchall()
    for product_id, title, stored, key in rows:
        current = by_name.get(stored)
        if not current:
            continue
        if (key or "").startswith("norm:"):
            raw = key[5:].rsplit(":", 1)[0]
            if re.search(r"запчаст|\bигры\b", raw, re.I):
                continue
            target = mapper.map_category(raw)
        elif stored in ("Шины и диски", "Клавиатуры и мыши"):  # ключ без раздела магазина: решает правило по названию
            result = classifier.classify_rules(title)
            target = mapper.get_by_id(result.category_id) if result else None
            raw = "(название)"
        else:
            continue
        # «Клавиатуры и мыши» раньше принимали корпуса, роутеры и принтеры, это ошибка и внутри одного корня
        moved_out = current.id == "peripherals" and target is not None and target.id != "peripherals"
        if target and (moved_out or _root(mapper, target) != _root(mapper, current)):
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
    parser.add_argument("--cross-root", action="store_true", help="Искать товары в чужом корневом разделе")
    args = parser.parse_args()
    result = fix_cross_root(args.db, args.apply) if args.cross_root else fix(args.db, args.apply)
    for (raw, old, new), count in result.most_common():
        print(f"{count:6d}  {raw}: {old} -> {new}")
    print(f"Всего: {sum(result.values())}, записано: {'да' if args.apply else 'нет'}")
