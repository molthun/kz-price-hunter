#!/usr/bin/env python3
"""
Wave 7 Catalog Enrichment Script (v6.28.30).
Deep enrichment of the top categories:
- Ноутбуки (model-code screen_size, cpu, gpu, RAM/SSD)
- Мониторы (HTML entities &#34;, single quotes, model-code diagonals)
- Смартфоны (unitless (4/128), (6/128), 8/256, 2GB/32GB, standalone 32GB)
- Телевизоры (type facet for TVs, Projectors, Projection Screens, Mounts, Tuners)
- Корпуса (Компьютерный корпус ..., Панель LCD, без Б/П)
- Кондиционеры (Midea/AVA/ALMACOM/Atlantiq/CHIGO split systems without 'Кондиционер' word)
- Игровые приставки (retro Dendy/Sega consoles, game compilations, Moza Racing wheels)
- Одежда и обувь / Женская одежда (brand-prefixed bags, backpacks, tracksuits, parkas, vests, blouses, wallets)
- Игрушки для малышей / Игрушки и конструкторы (inflatable toys, playsets, tractors, doll furniture, magnetic mosaics)
- Сад и огород / Дом и сад (garden hoses, sprinklers, electric trimmers, LED strips/panels)
- Красота и здоровье (floor scales)
- Инструменты и ремонт (tweezers, cutting/grinding discs, network punch-down/stripper tools, welding masks)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from catalog_classifier import CatalogClassifier
from domain.attribute_indexing import index_product_facts
from domain.category import MASTER_TAXONOMY
from repositories.category_repo import CategoryRepository

NAME_TO_ID = {c.name: c.id for c in MASTER_TAXONOMY}


def _move_product(conn: sqlite3.Connection, pid: str, target_cat: str, now_iso: str) -> None:
    cat_id = NAME_TO_ID.get(target_cat, "electronics")
    conn.execute(
        "UPDATE product_category_tags SET category_id = ?, source = 'rules' WHERE product_id = ?",
        (cat_id, pid),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO category_ai_verdicts
            (product_id, old_category, new_category, confidence, model, applied, decided_at)
        VALUES (?, NULL, ?, 0.98, 'feed', 1, ?)
        """,
        (pid, target_cat, now_iso),
    )
    conn.execute(
        "UPDATE canonical_products SET category = ? WHERE id = ?",
        (target_cat, pid),
    )


def run_enrichment(db_path: str = "prices.db") -> None:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    now_iso = datetime.now(timezone.utc).isoformat()
    clf = CatalogClassifier()

    # 1. Sync Master Taxonomy schemas (updated tv, baby_toys, toys schemas)
    cat_repo = CategoryRepository(conn)
    synced_cats = cat_repo.sync_master_taxonomy()
    conn.commit()
    print(f"[1/5] Synced {synced_cats} categories from MASTER_TAXONOMY.")

    # 2. Clean up misplaced items in Furniture, Monitors, Laptops
    moved_count = 0

    # 2a. Ship 19" server rack shelves/fan panels/organizers in Мебель -> Сетевое оборудование
    rows_ship = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE category = 'Мебель'
          AND (
              title LIKE 'Полка Ship %'
              OR title LIKE 'Вентиляторная панель Ship %'
              OR title LIKE 'Кабельный органайзер Ship %'
              OR title LIKE '%в шкаф 19%'
              OR title LIKE '%для шкафов 19%'
          )
        """
    ).fetchall()
    for r in rows_ship:
        _move_product(conn, r["id"], "Сетевое оборудование", now_iso)
        moved_count += 1

    # 2b. Monitor stands, selfie monitors, screen wipes in Мониторы -> Электроника / Бытовая химия и хозтовары
    rows_mon_misc = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE category = 'Мониторы'
          AND (
              title LIKE 'Подставка для монитора%'
              OR title LIKE 'Кронштейн для монитора%'
              OR title LIKE 'Крепление для монитора%'
              OR title LIKE 'Селфи-монитор%'
              OR title LIKE 'Салфетки для %монитор%'
          )
        """
    ).fetchall()
    for r in rows_mon_misc:
        t_low = (r["title"] or "").lower()
        target = "Бытовая химия и хозтовары" if "салфетк" in t_low else "Электроника"
        _move_product(conn, r["id"], target, now_iso)
        moved_count += 1

    # 2c. Laptop bags in Ноутбуки -> Чехлы и бамперы
    rows_lap_bags = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE category = 'Ноутбуки'
          AND (
              title LIKE 'Сумка для ноутбука%'
              OR title LIKE 'Рюкзак для ноутбука%'
              OR title LIKE 'Чехол для ноутбука%'
              OR title LIKE 'Подставка для ноутбука%'
          )
        """
    ).fetchall()
    for r in rows_lap_bags:
        t_low = (r["title"] or "").lower()
        target = "Электроника" if "подставк" in t_low else "Чехлы и бамперы"
        _move_product(conn, r["id"], target, now_iso)
        moved_count += 1

    # 2d. Hair conditioners & Fabric softeners in Кондиционеры -> Уход за волосами / Бытовая химия и хозтовары
    rows_ac_chem = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE category = 'Кондиционеры'
          AND (
              title LIKE '% мл%'
              OR title LIKE '%смягчител%'
              OR title LIKE '%белья%'
              OR title LIKE '%волос%'
              OR title LIKE '%Vernel%'
              OR title LIKE '%Kerasys%'
              OR title LIKE '%Elastine%'
              OR title LIKE '%Gliss%'
              OR title LIKE '%BioMio%'
              OR title LIKE '% л'
              OR title LIKE '% л,%'
          )
        """
    ).fetchall()
    for r in rows_ac_chem:
        t_low = (r["title"] or "").lower()
        if any(w in t_low for w in ("смягчител", "белья", "vernel", "biomio", "lenor", "abc ", " 1,44 л", " 0,96 л", " 2 л", " 4 л")):
            _move_product(conn, r["id"], "Бытовая химия и хозтовары", now_iso)
        else:
            _move_product(conn, r["id"], "Уход за волосами", now_iso)
        moved_count += 1

    # 2e. Funko POP & collectible figurines in Игровые приставки -> Игрушки и конструкторы
    rows_console_figs = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE category = 'Игровые приставки'
          AND (
              title LIKE '%Фигурк%'
              OR title LIKE '%фигурк%'
              OR title LIKE '%Bitty POP%'
              OR title LIKE '%Брелок%'
              OR title LIKE '%брелок%'
          )
        """
    ).fetchall()
    for r in rows_console_figs:
        _move_product(conn, r["id"], "Игрушки и конструкторы", now_iso)
        moved_count += 1

    conn.commit()
    print(f"[2/5] Moved {moved_count} misplaced items to their canonical categories.")

    # 3. Backfill brands across Wave 7 categories where brand is missing or generic
    wave7_categories = (
        "Ноутбуки",
        "Мониторы",
        "Смартфоны",
        "Телевизоры",
        "Корпуса",
        "Кондиционеры",
        "Игровые приставки",
        "Одежда и обувь",
        "Женская одежда",
        "Мужская одежда",
        "Обувь",
        "Игрушки для малышей",
        "Игрушки и конструкторы",
        "Сад и огород",
        "Дом и сад",
        "Красота и здоровье",
        "Инструменты и ремонт",
        "Сетевое оборудование",
    )
    placeholders = ",".join("?" for _ in wave7_categories)
    rows_no_brand = conn.execute(
        f"""
        SELECT id, title, brand FROM canonical_products
        WHERE category IN ({placeholders})
          AND (brand IS NULL OR TRIM(brand) = '' OR LOWER(TRIM(brand)) IN ('generic', 'no name', 'noname', 'нет бренда', 'без бренда'))
        """,
        wave7_categories,
    ).fetchall()

    brand_updated = 0
    for r in rows_no_brand:
        title = r["title"] or ""
        b = clf.extract_brand(title)
        if not b and re.search(r"\bkari\b", title, re.I):
            b = "Kari"
        if b:
            conn.execute(
                "UPDATE canonical_products SET brand = ? WHERE id = ?",
                (b, r["id"]),
            )
            brand_updated += 1
    conn.commit()
    print(f"[3/5] Backfilled brand on {brand_updated} products.")

    # 4. Re-index attributes for all products in Wave 7 categories + any un-indexed products in the catalog
    facts_map = {}
    try:
        for r in conn.execute(
            "SELECT product_id, facts_json FROM product_source_evidence WHERE status = 'ready' AND facts_json != '[]'"
        ).fetchall():
            try:
                facts_map[r["product_id"]] = json.loads(r["facts_json"])
            except Exception:
                pass
    except Exception:
        pass

    target_rows = conn.execute(
        f"""
        SELECT p.id, p.title
        FROM canonical_products p
        WHERE p.category IN ({placeholders})
           OR NOT EXISTS (
               SELECT 1 FROM product_search_attributes pa
               WHERE pa.product_id = p.id AND pa.source_title = p.title
           )
        """,
        wave7_categories,
    ).fetchall()

    reindexed = 0
    for idx, r in enumerate(target_rows, start=1):
        pid = r["id"]
        title = r["title"] or ""
        if not title:
            continue
        cnt = index_product_facts(conn, pid, facts_map.get(pid, []), title)
        if cnt > 0:
            reindexed += 1
        if idx % 1000 == 0:
            conn.commit()
    conn.commit()
    conn.execute("UPDATE product_search_attributes SET value = 'Сплит-система' WHERE key = 'type' AND LOWER(value) = 'сплит-система'")
    conn.execute("UPDATE product_search_attributes SET value = 'Терморегулятор' WHERE key = 'type' AND LOWER(value) = 'терморегулятор'")
    conn.execute("UPDATE product_search_attributes SET value = 'Для девочек' WHERE key = 'gender' AND LOWER(value) IN ('девочки', 'девочка')")
    conn.execute("UPDATE product_search_attributes SET value = 'Для мальчиков' WHERE key = 'gender' AND LOWER(value) IN ('мальчики', 'мальчик')")
    conn.execute("UPDATE product_search_attributes SET value = 'Mid-Tower / ATX' WHERE key = 'form_factor' AND LOWER(value) IN ('midi-башня', 'midi-tower', 'mid-tower')")
    conn.commit()
    try:
        conn.execute("DELETE FROM local_search_intents")
        conn.commit()
    except Exception:
        pass
    print(f"[4/5] Re-indexed attributes on {reindexed}/{len(target_rows)} target products.")

    # 5. Summary report
    total_attrs = conn.execute("SELECT COUNT(*) FROM product_search_attributes").fetchone()[0]
    print(f"\n[5/5] Total rows in product_search_attributes: {total_attrs:,}")
    print("\nCategory coverage summary:")
    summary_rows = conn.execute(
        f"""
        SELECT p.category,
               COUNT(*) AS total,
               SUM(CASE WHEN p.brand IS NOT NULL AND TRIM(p.brand) != '' AND LOWER(TRIM(p.brand)) NOT IN ('generic', 'no name', 'noname') THEN 1 ELSE 0 END) AS with_brand,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM product_search_attributes pa WHERE pa.product_id = p.id AND pa.source_title = p.title) THEN 1 ELSE 0 END) AS with_attrs,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM product_search_attributes pa WHERE pa.product_id = p.id AND pa.key != 'color') THEN 1 ELSE 0 END) AS with_nc_attrs
        FROM canonical_products p
        WHERE p.category IN ({placeholders})
        GROUP BY p.category
        ORDER BY total DESC
        """,
        wave7_categories,
    ).fetchall()
    for sr in summary_rows:
        tot = sr["total"] or 1
        wb = sr["with_brand"] or 0
        wa = sr["with_attrs"] or 0
        wnc = sr["with_nc_attrs"] or 0
        print(
            f"  - {sr['category']:<28}: total={tot:5d} | brand={wb:5d} ({wb*100.0/tot:5.1f}%) | attrs={wa:5d} ({wa*100.0/tot:5.1f}%) | non-color={wnc:5d} ({wnc*100.0/tot:5.1f}%)"
        )

    conn.close()


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "prices.db"
    run_enrichment(db_file)
