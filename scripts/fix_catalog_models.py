#!/usr/bin/env python3
"""
scripts/fix_catalog_models.py

Миграционный скрипт очистки и канонизации моделей в каталоге:
1. Канонизирует модели всех видеокарт в canonical_products (исключает производителей,
   вендорные серии типа Dual/Ventus/OC/Windforce, объединяя карточки под чистым именем GPU).
2. Вычищает оставшиеся ноутбуки (Lenovo LOQ, ThundeRobot, ASUS TUF, HYDRA) из 'Видеокарты' в 'Ноутбуки'.
3. Перемещает случайно попавшие кулеры (DeepCool AG620, Ocypus Delta) в 'Охлаждение и кулеры'.
4. Удаляет бренды/производителей, ошибочно записанные в поле model по всей БД.
"""

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from catalog_classifier import (
    CatalogClassifier,
    canonical_gpu_model,
    _HARDWARE_VENDORS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_catalog_models")


def run_migration(db_path: str):
    logger.info(f"Подключение к базе данных: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    clf = CatalogClassifier()

    # 1. Перемещение ноутбуков и кулеров из 'Видеокарты'
    c.execute("""
        SELECT id, title, category, model FROM canonical_products
        WHERE category IN ('gpus', 'Видеокарты')
    """)
    gpu_products = c.fetchall()
    logger.info(f"Найдено {len(gpu_products)} товаров в категории Видеокарты")

    laptops_moved = 0
    coolers_moved = 0
    components_moved = 0
    models_canonicalized = 0
    models_nullified = 0

    for p in gpu_products:
        pid = p["id"]
        title = p["title"]
        curr_m = p["model"]

        # 1. Проверяем, не ноутбук ли это
        clf_res = clf.classify_rules(title)
        is_laptop = (clf_res and clf_res.category_id == "laptops") or bool(
            re.search(r"\b(ноутбук|macbook|laptop|ультрабук|mechrevo|thunderobot)\b|(?<!видеокарт\w\s)(?<!кулер\w\s)\b(?:rog\s+strix\s+scar|gaming\s+a16)\b|\b(?:14|15\.6|16|17\.3|18)\s*[\"”].*\b(?:intel|amd|core|ryzen).*\b(?:гб|gb)\b", title, re.I)
        )
        if is_laptop:
            logger.info(f"Перемещение ноутбука в 'Ноутбуки': [{pid}] {title}")
            c.execute("""
                INSERT OR REPLACE INTO product_category_tags
                (product_id, category_id, source, source_signature, updated_at)
                VALUES (?, 'laptops', 'rules', 'fix_catalog_models', strftime('%s','now'))
            """, (pid,))
            c.execute("UPDATE canonical_products SET category = 'Ноутбуки', updated_at = strftime('%s','now') WHERE id = ?", (pid,))
            c.execute("DELETE FROM product_search_attributes WHERE product_id = ? AND key IN ('vram', 'vram_gb', 'chipset', 'clock_speed', 'interface')", (pid,))
            laptops_moved += 1
            continue

        # 2. Проверяем, не кулер ли это
        is_cooler = (clf_res and clf_res.category_id in ("coolers", "cooling")) or bool(
            re.search(r"\b(кулер|систем[аы]\s+охлаждения|сжо|сво|cpu\s+cooler)\b", title, re.I) and not re.search(r"\bвидеокарт\w*\b", title, re.I)
        )
        if is_cooler:
            logger.info(f"Перемещение кулера в 'Охлаждение и кулеры': [{pid}] {title}")
            c.execute("""
                INSERT OR REPLACE INTO product_category_tags
                (product_id, category_id, source, source_signature, updated_at)
                VALUES (?, 'cooling', 'rules', 'fix_catalog_models', strftime('%s','now'))
            """, (pid,))
            c.execute("UPDATE canonical_products SET category = 'Охлаждение и кулеры', updated_at = strftime('%s','now') WHERE id = ?", (pid,))
            c.execute("DELETE FROM product_search_attributes WHERE product_id = ? AND key IN ('vram', 'vram_gb', 'chipset', 'clock_speed', 'interface')", (pid,))
            coolers_moved += 1
            continue

        # 3. Проверяем LED-контроллеры дисплеев и внешние USB-адаптеры
        is_component = bool(re.search(r"\b(novastar|huidu|приемная\s+карта|передающая\s+карта|wavlink\s+wl-)\b", title, re.I))
        if is_component:
            logger.info(f"Перемещение комплектующего в 'Комплектующие для ПК': [{pid}] {title}")
            c.execute("""
                INSERT OR REPLACE INTO product_category_tags
                (product_id, category_id, source, source_signature, updated_at)
                VALUES (?, 'pc_components', 'rules', 'fix_catalog_models', strftime('%s','now'))
            """, (pid,))
            c.execute("UPDATE canonical_products SET category = 'Комплектующие для ПК', updated_at = strftime('%s','now') WHERE id = ?", (pid,))
            c.execute("DELETE FROM product_search_attributes WHERE product_id = ? AND key IN ('vram', 'vram_gb', 'chipset', 'clock_speed', 'interface')", (pid,))
            components_moved += 1
            continue

        # 4. Канонизация GPU модели
        canon_m = canonical_gpu_model(title)
        if canon_m:
            if curr_m != canon_m:
                c.execute("UPDATE canonical_products SET model = ?, updated_at = strftime('%s','now') WHERE id = ?", (canon_m, pid))
                models_canonicalized += 1
        else:
            # Если чипсет не распознан, но текущее поле model содержит вендорный шум или SKU артикул
            if curr_m and (
                any(v in curr_m.lower() for v in ("gv-", "ne7", "ne6", "ocv", "1135", "3235", "afox", "pny", "palit", "zotac", "sapphire", "xfx", "inno3d", "maxsun", "pulse", "swift", "speedster", "gaming", "twin edge", "windforce", "aorus", "eagle", "dual", "ventus"))
                or len(curr_m) <= 2
            ):
                c.execute("UPDATE canonical_products SET model = NULL, updated_at = strftime('%s','now') WHERE id = ?", (pid,))
                models_nullified += 1

    conn.commit()
    logger.info(f"Перемещено ноутбуков: {laptops_moved}")
    logger.info(f"Перемещено кулеров: {coolers_moved}")
    logger.info(f"Перемещено комплектующих: {components_moved}")
    logger.info(f"Канонизировано моделей GPU: {models_canonicalized}")
    logger.info(f"Очищено вендорных моделей GPU в NULL: {models_nullified}")

    # 2. Очистка по всей БД от моделей, являющихся чистыми названиями производителей или мусором
    junk_models = _HARDWARE_VENDORS | {
        "v8", "11350 04", "pc", "pro", "mini", "plus", "max", "ultra",
        "gaming", "stand", "holder", "oc", "dual", "tuf", "rog", "amd", "nvidia", "intel",
        "pci e nv gv", "pci e gddr6 gv", "speedster swft 21", "gaming edition", "twin edge",
        "pulse amd radeon rx", "swift amd radeon rx", "amd radeon ai pro", "amd radeon 9070 gaming",
        "maxsun geforce gtx ms", "rtx geforce 3050 stormx", "rtx 3060windforce oc gv"
    }

    # Исключаем Dyson V8 из очистки для пылесосов
    c.execute("SELECT id, title, category, model FROM canonical_products WHERE model IS NOT NULL")
    all_models = c.fetchall()
    junk_cleared = 0

    for p in all_models:
        m = (p["model"] or "").strip().lower()
        if m in junk_models:
            # Для пылесосов Dyson V8 модель допустима
            if m == "v8" and ("dyson" in p["title"].lower() or p["category"] in ("vacuums", "Пылесосы")):
                continue
            c.execute("UPDATE canonical_products SET model = NULL WHERE id = ?", (p["id"],))
            junk_cleared += 1

    conn.commit()
    logger.info(f"Очищено ошибочных моделей-производителей по всей БД: {junk_cleared}")

    # 3. Очистка неверных чипсетов (где ошибочно записаны CPU или мусор)
    c.execute("""
        DELETE FROM product_search_attributes
        WHERE key = 'chipset' AND (
            value LIKE '%Core Ultra%' OR
            value LIKE '%Ryzen%' OR
            value LIKE '%Intel Core%' OR
            value LIKE '%275HX%' OR
            value LIKE '%290HX%' OR
            value LIKE '%i7-%' OR
            value LIKE '%i9-%' OR
            value LIKE '%i5-%' OR
            value IN ('NVIDIA', 'AMD', 'Intel', 'GeForce', 'Radeon')
        )
    """)
    chipsets_cleaned = c.rowcount
    conn.commit()
    logger.info(f"Очищено искаженных чипсетов: {chipsets_cleaned}")

    conn.close()
    logger.info("Миграция моделей каталога успешно завершена.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix catalog models and misclassified products")
    parser.add_argument("--db-path", default="prices.db", help="Path to SQLite database")
    args = parser.parse_args()

    run_migration(args.db_path)
