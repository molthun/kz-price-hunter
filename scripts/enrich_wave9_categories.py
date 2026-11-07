#!/usr/bin/env python3
"""
Wave 9 Catalog Enrichment Script (v6.28.32).
Deep enrichment and category drift prevention across:
- Category drift fix: Migrate any non-canonical categories (Распродажа, Внешние аккумуляторы, Уценка, etc.)
  to canonical MASTER_TAXONOMY categories with product_category_tags and category_ai_verdicts.
- PC Components:
  - Материнские платы (chipset, socket, memory_type, form_factor)
  - Процессоры (socket, cores, clock, architecture)
  - Видеокарты (gpu, vram, memory_type)
  - Блоки питания (power, form_factor, modular)
  - Жесткие диски HDD (storage, form_factor, rpm)
  - Компьютеры и ПК (cpu, ram, storage, gpu)
- Appliances & Power:
  - Пылесосы (type, power, dustbin, corded/cordless)
  - Техника для кухни (type, power, volume)
  - Бытовая техника (type, power)
  - Посуда и кухня (type, material)
  - ИБП и питание (power, type)
- Consumer Goods, Apparel & Accessories:
  - Одежда и обувь (type, gender, season)
  - Кабели и зарядные устройства (type, connector, power, battery)
  - Товары для малышей & Детское питание (type, age_from, pack_weight)
  - Зоотовары (type, pet_type)
  - Красота и здоровье & Декоративная косметика (type, volume)
  - Книги и канцелярия (type, author)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from catalog_classifier import CatalogClassifier
from domain.attribute_indexing import (
    EVIDENCE_RULES,
    extract_title_attributes,
    index_product_facts,
    upsert_search_attributes,
)
from domain.category import MASTER_TAXONOMY
from domain.feed_categories import section_category

CANONICAL_CATEGORY_NAMES = {c.name for c in MASTER_TAXONOMY}
NAME_TO_ID = {c.name: c.id for c in MASTER_TAXONOMY}
ID_TO_NAME = {c.id: c.name for c in MASTER_TAXONOMY}

WAVE9_TARGET_CATEGORIES = [
    # PC Components
    "Материнские платы",
    "Процессоры",
    "Видеокарты",
    "Блоки питания",
    "Жесткие диски HDD",
    "SSD накопители",
    "Охлаждение и кулеры",
    "Компьютеры и ПК",
    # Appliances & Power
    "Пылесосы",
    "Техника для кухни",
    "Бытовая техника",
    "Посуда и кухня",
    "ИБП и питание",
    # Consumer Goods & Apparel
    "Одежда и обувь",
    "Обувь",
    "Женская одежда",
    "Мужская одежда",
    "Кабели и зарядные устройства",
    "Чехлы и бамперы",
    "Защитные стекла и пленки",
    "Картриджи и чернила",
    "Товары для малышей",
    "Детские товары",
    "Детское питание",
    "Коляски и автокресла",
    "Зоотовары",
    "Товары для кошек",
    "Товары для собак",
    "Красота и здоровье",
    "Парфюмерия",
    "Декоративная косметика",
    "Книги и канцелярия",
    "Книги",
]


def _move_product(conn: sqlite3.Connection, pid: str, target_cat: str, now_iso: str, reason: str = "wave9_reclass") -> None:
    cat_id = NAME_TO_ID.get(target_cat, "electronics")
    now_ts = datetime.now(timezone.utc).timestamp()
    conn.execute(
        """
        INSERT INTO product_category_tags (product_id, category_id, source, source_signature, reason, updated_at)
        VALUES (?, ?, 'rules', 'wave9', ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            category_id = excluded.category_id,
            source = excluded.source,
            source_signature = excluded.source_signature,
            reason = excluded.reason,
            updated_at = excluded.updated_at
        """,
        (pid, cat_id, reason, now_ts),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO category_ai_verdicts
            (product_id, old_category, new_category, confidence, model, applied, decided_at)
        VALUES (?, NULL, ?, 0.99, ?, 1, ?)
        """,
        (pid, target_cat, reason, now_iso),
    )
    conn.execute(
        "UPDATE canonical_products SET category = ? WHERE id = ?",
        (target_cat, pid),
    )


def _infer_fallback_attrs(category: str, title: str, src_cat: str) -> Dict[str, str]:
    """Deterministic fallback attributes from title & source feed category context."""
    extra: Dict[str, str] = {}
    t_low = title.lower()
    sc_low = src_cat.lower()

    if category == "Материнские платы":
        if re.search(r"\b(atx|micro[- ]?atx|m-atx|matx|mini[- ]?itx|itx|eatx)\b", t_low):
            m_ff = re.search(r"\b(micro[- ]?atx|m-atx|matx|mini[- ]?itx|itx|eatx|atx)\b", t_low)
            if m_ff:
                ff_map = {"atx": "ATX", "micro-atx": "Micro-ATX", "m-atx": "Micro-ATX", "matx": "Micro-ATX", "mini-itx": "Mini-ITX", "itx": "Mini-ITX", "eatx": "E-ATX"}
                extra["form_factor"] = ff_map.get(m_ff.group(1).lower(), "ATX")

    elif category == "Блоки питания":
        if "form_factor" not in extra:
            if "sfx" in t_low:
                extra["form_factor"] = "SFX"
            elif "atx" in t_low or "блок питания" in t_low:
                extra["form_factor"] = "ATX"

    elif category == "Жесткие диски HDD":
        if "form_factor" not in extra:
            if "2.5" in t_low:
                extra["form_factor"] = '2.5"'
            elif "3.5" in t_low:
                extra["form_factor"] = '3.5"'
            else:
                extra["form_factor"] = '3.5"'

    elif category == "Пылесосы":
        if re.search(r"\b(робот|robot)\b", t_low):
            extra["type"] = "Робот-пылесос"
        elif re.search(r"\b(вертикальн\w*|беспроводн\w*|аккумуляторн\w*|handstick|cordless)\b", t_low):
            extra["type"] = "Вертикальный / Беспроводной пылесос"
        elif re.search(r"\b(моющий|пароочиститель|паровая\s+швабра|стеклоочиститель)\b", t_low):
            extra["type"] = "Моющий / Пароочиститель"
        elif re.search(r"\b(автомобильн\w*|ручной\s+пылесос|для\s+авто)\b", t_low):
            extra["type"] = "Автомобильный / Ручной пылесос"
        else:
            extra["type"] = "Классический пылесос для сухой уборки"

    elif category in ("Техника для кухни", "Бытовая техника"):
        if re.search(r"\b(чайник\w*|термопот\w*|самовар)\b", t_low):
            extra["type"] = "Чайник / Термопот"
        elif re.search(r"\b(кофемашин\w*|кофеварк\w*|кофемолк\w*)\b", t_low):
            extra["type"] = "Кофемашина / Кофеварка"
        elif re.search(r"\b(блендер\w*|миксер\w*|измельчитель)\b", t_low):
            extra["type"] = "Блендер / Миксер / Измельчитель"
        elif re.search(r"\b(мясорубк\w*)\b", t_low):
            extra["type"] = "Мясорубка"
        elif re.search(r"\b(микроволнов\w*|свч)\b", t_low):
            extra["type"] = "Микроволновая печь (СВЧ)"
        elif re.search(r"\b(мультиварк\w*|пароварк\w*|скороварк\w*)\b", t_low):
            extra["type"] = "Мультиварка / Пароварка"
        elif re.search(r"\b(гриль\w*|тостер\w*|сэндвичниц\w*|вафельниц\w*|блинниц\w*)\b", t_low):
            extra["type"] = "Гриль / Тостер / Вафельница"
        elif re.search(r"\b(соковыжималк\w*)\b", t_low):
            extra["type"] = "Соковыжималка"
        elif re.search(r"\b(утюг\w*|отпаривател\w*|парогенератор\w*)\b", t_low):
            extra["type"] = "Утюг / Отпариватель"
        elif re.search(r"\b(машинка\s+для\s+стрижки|триммер\w*|электробритв\w*|эпилятор\w*)\b", t_low):
            extra["type"] = "Прибор для стрижки и бритья"
        elif re.search(r"\b(фен\w*|стайлер\w*|плойк\w*|выпрямител\w*)\b", t_low):
            extra["type"] = "Фен / Стайлер для волос"
        elif re.search(r"\b(весы\s+(?:напольн|кухонн))\b", t_low):
            extra["type"] = "Весы"
        else:
            extra["type"] = "Мелкая бытовая техника"

    elif category == "Посуда и кухня":
        if re.search(r"\b(сковород\w*|сотейник\w*|вок\b|жаровн\w*)\b", t_low):
            extra["type"] = "Сковорода / Сотейник"
        elif re.search(r"\b(кастрюл\w*|ковш\b|казан\w*|утятниц\w*)\b", t_low):
            extra["type"] = "Кастрюля / Ковш / Казан"
        elif re.search(r"\b(нож\b|ножи\b|ножниц\w*\s+кухонн|овощечистк\w*)\b", t_low):
            extra["type"] = "Кухонные ножи и ножницы"
        elif re.search(r"\b(тарелк\w*|салатник\w*|блюд\w*|миск\w*\s+кухонн)\b", t_low):
            extra["type"] = "Тарелки и салатники"
        elif re.search(r"\b(кружк\w*|чашк\w*|бокал\w*|стакан\w*|фужер\w*|стопк\w*|рюмк\w*)\b", t_low):
            extra["type"] = "Кружки, бокалы и стаканы"
        elif re.search(r"\b(термос\w*|термокружк\w*|бутылк\w*\s+для\s+воды)\b", t_low):
            extra["type"] = "Термосы и ланч-боксы"
        else:
            extra["type"] = "Кухонные принадлежности и посуда"

    elif category == "ИБП и питание":
        if re.search(r"\b(ибп|ups|источник\s+бесперебойного|smart-ups|back-ups)\b", t_low):
            extra["type"] = "ИБП (UPS)"
        elif re.search(r"\b(стабилизатор\w*)\b", t_low):
            extra["type"] = "Стабилизатор напряжения"
        elif re.search(r"\b(сетев\w*\s+фильтр\w*|удлинитель\w*)\b", t_low):
            extra["type"] = "Сетевой фильтр / Удлинитель"
        else:
            extra["type"] = "Электропитание и защита"

    return extra


def run_enrichment(db_path: str = "prices.db") -> None:
    if not os.path.exists(db_path):
        print(f"Error: Database {db_path} does not exist.", flush=True)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now_iso = datetime.now(timezone.utc).isoformat()
    classifier = CatalogClassifier()

    print(f"=== Wave 9 Enrichment on {db_path} ({now_iso}) ===", flush=True)

    # Step 1: Detect and migrate non-canonical categories
    print("[1/5] Detecting non-canonical category drift...", flush=True)
    canon_placeholders = ",".join("?" for _ in CANONICAL_CATEGORY_NAMES)
    drift_rows = conn.execute(
        f"""
        SELECT p.id, p.title, p.category,
               COALESCE(src.src_shop, '') AS seller_name,
               COALESCE(src.src_cat, '') AS raw_category
        FROM canonical_products p
        LEFT JOIN (
            SELECT o.product_id, MAX(COALESCE(pr.category, '')) as src_cat, MAX(COALESCE(pr.shop, '')) as src_shop
            FROM offers o
            JOIN products pr ON pr.url = o.url
            GROUP BY o.product_id
        ) src ON src.product_id = p.id
        WHERE p.category IS NULL
           OR TRIM(p.category) = ''
           OR p.category NOT IN ({canon_placeholders})
        """,
        tuple(CANONICAL_CATEGORY_NAMES),
    ).fetchall()

    print(f"Found {len(drift_rows)} products in non-canonical or empty categories.", flush=True)
    drift_migrated = 0
    for r in drift_rows:
        pid = r["id"]
        title = r["title"] or ""
        old_cat = r["category"] or ""
        seller = r["seller_name"] or ""
        raw_cat = r["raw_category"] or ""

        # Deterministic rule matching first
        rule = classifier.classify_rules(title)
        new_cat = rule.category_name if (rule and rule.category_name in CANONICAL_CATEGORY_NAMES) else ""
        if not new_cat:
            # Fallback to section_category on raw_cat
            sc_id = section_category(raw_cat)
            if sc_id and sc_id in ID_TO_NAME:
                new_cat = ID_TO_NAME[sc_id]
            elif old_cat:
                sc_old = section_category(old_cat)
                if sc_old and sc_old in ID_TO_NAME:
                    new_cat = ID_TO_NAME[sc_old]
            if not new_cat or new_cat not in CANONICAL_CATEGORY_NAMES:
                new_cat = "Электроника"

        _move_product(conn, pid, new_cat, now_iso, reason="wave9_drift_fix")
        drift_migrated += 1

    conn.commit()
    print(f"[1/5] Completed drift migration: {drift_migrated} products re-assigned to canonical categories.", flush=True)

    # Step 2: Cross-category cleanup for PC components & consumer categories
    print("[2/5] Running cross-category precision checks...", flush=True)
    cross_cleanup = 0

    # Motherboards misfiled into Электроника / Компьютеры и ПК
    mb_candidates = conn.execute(
        """
        SELECT id, title, category FROM canonical_products
        WHERE category IN ('Электроника', 'Компьютеры и ПК')
          AND (
            LOWER(title) LIKE '%материнск%плат%'
            OR LOWER(title) LIKE '%матплат%'
            OR LOWER(title) LIKE '%системная плата%'
            OR LOWER(title) LIKE '%mainboard%'
            OR LOWER(title) LIKE '%motherboard%'
          )
        """
    ).fetchall()
    for r in mb_candidates:
        _move_product(conn, r["id"], "Материнские платы", now_iso, "wave9_mb_cleanup")
        cross_cleanup += 1

    # GPUs misfiled into Компьютеры и ПК
    gpu_candidates = conn.execute(
        """
        SELECT id, title, category FROM canonical_products
        WHERE category IN ('Электроника', 'Компьютеры и ПК')
          AND (
            LOWER(title) LIKE '%видеокарт%'
            OR LOWER(title) LIKE '%geforce rtx%'
            OR LOWER(title) LIKE '%radeon rx%'
          )
          AND NOT LOWER(title) LIKE '%системный блок%'
          AND NOT LOWER(title) LIKE '%компьютер%'
          AND NOT LOWER(title) LIKE '%пк%'
        """
    ).fetchall()
    for r in gpu_candidates:
        _move_product(conn, r["id"], "Видеокарты", now_iso, "wave9_gpu_cleanup")
        cross_cleanup += 1

    # CPUs misfiled into Электроника / Компьютеры и ПК
    cpu_candidates = conn.execute(
        """
        SELECT id, title, category FROM canonical_products
        WHERE category IN ('Электроника', 'Компьютеры и ПК')
          AND (
            LOWER(title) LIKE '%процессор amd%'
            OR LOWER(title) LIKE '%процессор intel%'
            OR LOWER(title) LIKE '%ryzen % box'
            OR LOWER(title) LIKE '%ryzen % oem'
            OR LOWER(title) LIKE '%core i% box'
            OR LOWER(title) LIKE '%core i% oem'
          )
          AND NOT LOWER(title) LIKE '%системный блок%'
          AND NOT LOWER(title) LIKE '%компьютер%'
        """
    ).fetchall()
    for r in cpu_candidates:
        _move_product(conn, r["id"], "Процессоры", now_iso, "wave9_cpu_cleanup")
        cross_cleanup += 1

    # PSUs misfiled into Компьютеры и ПК
    psu_candidates = conn.execute(
        """
        SELECT id, title, category FROM canonical_products
        WHERE category IN ('Электроника', 'Компьютеры и ПК')
          AND (
            LOWER(title) LIKE '%блок питания%'
            OR LOWER(title) LIKE '%бп %'
          )
          AND NOT LOWER(title) LIKE '%системный блок%'
          AND NOT LOWER(title) LIKE '%ноутбук%'
        """
    ).fetchall()
    for r in psu_candidates:
        _move_product(conn, r["id"], "Блоки питания", now_iso, "wave9_psu_cleanup")
        cross_cleanup += 1

    conn.commit()
    print(f"[2/5] Cross-category cleanup moved {cross_cleanup} items to exact component categories.", flush=True)

    # Step 3: Cache offer raw facts
    print("[3/5] Loading offer facts...", flush=True)
    facts_map: Dict[str, List[Tuple[str, str]]] = {}
    src_info: Dict[str, Tuple[str, str]] = {}
    try:
        for r in conn.execute(
            """
            SELECT o.product_id, MAX(COALESCE(pr.category, '')) as src_cat, MAX(COALESCE(pr.shop, '')) as src_shop
            FROM offers o
            JOIN products pr ON pr.url = o.url
            GROUP BY o.product_id
            """
        ).fetchall():
            src_info[r["product_id"]] = (r["src_cat"] or "", r["src_shop"] or "")
    except Exception as e:
        print(f"Warning loading src_info: {e}", flush=True)

    try:
        for r in conn.execute(
            "SELECT product_id, facts_json FROM product_source_evidence WHERE status = 'ready' AND facts_json != '[]'"
        ).fetchall():
            try:
                facts_map[r["product_id"]] = json.loads(r["facts_json"])
            except Exception:
                pass
    except Exception as e:
        print(f"Warning loading facts: {e}", flush=True)

    # Step 4: Index attributes for all Wave 9 categories
    placeholders = ",".join("?" for _ in WAVE9_TARGET_CATEGORIES)
    target_rows = conn.execute(
        f"""
        SELECT p.id, p.title, p.category
        FROM canonical_products p
        WHERE p.category IN ({placeholders})
        """,
        WAVE9_TARGET_CATEGORIES,
    ).fetchall()

    print(f"[4/5] Re-indexing attributes for {len(target_rows)} products across Wave 9 categories...", flush=True)
    indexed_attrs = 0
    fallback_applied = 0

    for idx, r in enumerate(target_rows, start=1):
        pid = r["id"]
        title = r["title"] or ""
        cat = r["category"] or ""
        if not title:
            continue

        indexed_attrs += index_product_facts(conn, pid, facts_map.get(pid, []), title)

        # Check if product has at least one non-color attribute
        has_nc = conn.execute(
            "SELECT 1 FROM product_search_attributes WHERE product_id = ? AND key != 'color' LIMIT 1",
            (pid,),
        ).fetchone()
        if not has_nc:
            sc, _ = src_info.get(pid, ("", ""))
            extra = _infer_fallback_attrs(cat, title, sc)
            if extra:
                items = {k: (v, EVIDENCE_RULES) for k, v in extra.items()}
                effective = upsert_search_attributes(conn, pid, items, title, now_iso)
                try:
                    conn.execute(
                        "UPDATE canonical_products SET attributes_json = json_patch(COALESCE(NULLIF(attributes_json, ''), '{}'), ?) WHERE id = ?",
                        (json.dumps(effective), pid),
                    )
                except Exception:
                    pass
                fallback_applied += 1
                indexed_attrs += len(extra)

        if idx % 1000 == 0:
            conn.commit()

    conn.commit()
    print(f"[5/5] Indexed {indexed_attrs} attributes across {len(target_rows)} products (fallback applied to {fallback_applied} products).", flush=True)

    # Summary table
    print("\n=== Wave 9 Category Coverage Summary ===", flush=True)
    for cat in WAVE9_TARGET_CATEGORIES:
        tot = conn.execute("SELECT COUNT(*) FROM canonical_products WHERE category = ?", (cat,)).fetchone()[0]
        if tot == 0:
            continue
        with_brand = conn.execute(
            "SELECT COUNT(*) FROM canonical_products WHERE category = ? AND brand IS NOT NULL AND TRIM(brand) != ''",
            (cat,),
        ).fetchone()[0]
        with_nc = conn.execute(
            """
            SELECT COUNT(DISTINCT p.id)
            FROM canonical_products p
            JOIN product_search_attributes pa ON pa.product_id = p.id
            WHERE p.category = ? AND pa.key != 'color'
            """,
            (cat,),
        ).fetchone()[0]
        print(
            f"  {cat:30s}: total={tot:5d} | brand={with_brand:5d} ({with_brand*100/tot:5.1f}%) | non_color_attrs={with_nc:5d} ({with_nc*100/tot:5.1f}%)"
        )

    conn.close()


if __name__ == "__main__":
    db_arg = sys.argv[1] if len(sys.argv) > 1 else "prices.db"
    run_enrichment(db_arg)
