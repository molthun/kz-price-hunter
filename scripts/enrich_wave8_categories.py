#!/usr/bin/env python3
"""
Wave 8 Catalog Enrichment Script (v6.28.31).
Deep enrichment and cross-category cleanup of 15 mid-tier categories:
- Мебель (Mebel.kz collection dimensions/models, gaming chairs, Askona bases, Zeta furniture)
- Текстиль и декор (Mebel.kz rugs/textiles, curtains, mattress protectors, vases, wall clocks)
- Инструменты и ремонт (woodworking machines, impact drivers, plumbing, building mixes, profiles, tapes)
- Красота и здоровье (dermocosmetics, hygiene pads/tampons, Kari beauty accessories, medical devices)
- Декоративная косметика (face/lip/eye makeup with trailing product type, cross-category cleanup)
- Книги и канцелярия (Kazakh/Latin author books, Meloman art/hobby kits, school/office stationery)
- Дом и сад & Сад и огород (frame/inflatable pools, garden tools, storage boxes, lighting, decor)
- Продукты питания (chewing gum, dragees, syrups, crackers, bread, pickles, traditional pasta)
- Бытовая химия и хозтовары (steam mops -> vacuums, professional cleaners, odor neutralizers, laundry liquids)
- Электроника (laptop stands, styluses, docking stations, smart home hubs/sensors/sockets/switches)
- Детские товары (baby wipes, loungers, playmats, crib bumpers, kids furniture & gift boxes)
- Товары для кошек (super-premium dry/wet foods, litter boxes, scoops, flea drops, collars)
- Аудиотехника (plural 'Колонки', speakerphones, phono preamps, Hi-Fi players, wireless receivers)
- Лекарства и витамины (Europharma trailing dosage forms: № N пакетики/леденцы/суппозитории/мазь/раствор/сироп)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from catalog_classifier import CatalogClassifier
from domain.attribute_indexing import (
    EVIDENCE_RULES,
    index_product_facts,
    upsert_search_attributes,
)
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


def _infer_fallback_attrs(category: str, title: str, src_cat: str) -> Dict[str, str]:
    """Deterministic fallback attributes from source feed category & title context."""
    extra: Dict[str, str] = {}
    t_low = title.lower()
    sc_low = src_cat.lower()

    if category == "Мебель":
        if any(w in sc_low for w in ("комоды и тумбы", "шкафы и стеллажи", "шкафы и комоды")):
            extra["type"] = "Шкаф / Комод / Тумба"
        elif any(w in sc_low for w in ("рабочие столы", "обеденные столы", "столы")):
            extra["type"] = "Стол"
        elif "кровати" in sc_low:
            extra["type"] = "Кровать / Матрас"
        elif "диваны и кресла" in sc_low:
            extra["type"] = "Диван"
        elif any(w in sc_low for w in ("компьютерные кресла", "стулья и пуфы", "офисные кресла")):
            extra["type"] = "Офисное кресло / Стул"
        else:
            extra["type"] = "Мебель и гарнитуры"

        m_dim = re.search(r"\b(\d{2,3})\s*[xх]\s*(\d{2,3})\b", title, re.I)
        if m_dim:
            extra["bed_size"] = f"{m_dim.group(1)}x{m_dim.group(2)} см"

    elif category == "Текстиль и декор":
        if "ковры и текстиль" in sc_low or any(
            w in t_low for w in ("ковер", "коврик", "плед", "покрывал", "бель", "простын", "наволочк", "одеял", "подушк", "штор", "тюль", "наматрасник", "полотенц", "скатерт")
        ):
            extra["type"] = "Постельное белье, текстиль и ковры"
        else:
            extra["type"] = "Хранение, декор и хозтовары"

    elif category == "Инструменты и ремонт":
        if any(w in sc_low for w in ("стройматериалы", "лкм", "краски", "обои", "плитка", "напольные")):
            extra["type"] = "Строительные смеси, ЛКМ и отделка"
        elif "ручной инструмент" in sc_low:
            extra["type"] = "Ручной инструмент и наборы"
            extra["power_supply"] = "Ручной"
        elif any(w in sc_low for w in ("сантехника", "водоснабжение")):
            extra["type"] = "Сантехника"
        elif "электроинструмент" in sc_low:
            extra["type"] = "Оснастка и расходники"
        else:
            extra["type"] = "Ручной инструмент и наборы"

    elif category == "Красота и здоровье":
        if re.search(r"\b(тени|тушь|подводк\w*|лайнер|бров\w*|ресниц\w*)\b", t_low):
            extra["type"] = "Макияж для глаз и бровей"
        elif re.search(r"\b(помад\w*|блеск|тинт|бальзам\s+для\s+губ|стик\s+для\s+губ)\b", t_low):
            extra["type"] = "Макияж для губ (помады, блески, карандаши)"
        elif re.search(r"\b(пудр\w*|румян\w*|консилер|тональн\w*|хайлайтер|бронзер|скульптор|праймер|контуринг|кушон|фиксатор)\b", t_low):
            extra["type"] = "Макияж для лица (тон, пудра, контуринг)"
        elif re.search(r"\b(кист\w*|спонж\w*|маникюр\w*|пилочк\w*|ногт\w*|лак\s+|зеркал\w*|косметичк\w*|пинцет|щипчик\w*|кусачки|ножниц\w*)\b", t_low):
            extra["type"] = "Косметички и маникюр"
        elif re.search(r"\b(волос\w*|расческ\w*|шампун\w*|кондиционер|бальзам|маска\s+для\s+волос|краска\s+для\s+волос)\b", t_low):
            extra["type"] = "Уход, укладка и окрашивание волос"
        elif re.search(r"\b(мыло|душ\w*|ванн\w*|мочалк\w*|губк\w*|дезодорант|антиперспирант|салфетк\w*|ватн\w*|прокладк\w*|тампон\w*|интимн\w*)\b", t_low):
            extra["type"] = "Гели для душа, мыло и гигиена"
        elif re.search(r"\b(зубн\w*|полост\w*\s+рта|ирригатор|ополаскивател\w*)\b", t_low):
            extra["type"] = "Уход за полостью рта"
        elif re.search(r"\b(рук\b|тела\b|ног\b|скраб|лосьон|молочко|брить\w*|депиляц\w*|воск)\b", t_low):
            extra["type"] = "Уход за телом, руками и бритье"
        elif "оптика" in sc_low or "аптека" in sc_low or re.search(r"\b(очки|линз\w*|тонометр|термометр|ингалятор|массажер|бандаж|корсет|грелк\w*|пластыр\w*|тест)\b", t_low):
            extra["type"] = "Медтехника и массажеры"
        else:
            extra["type"] = "Уход за лицом и губами"

    elif category == "Декоративная косметика":
        if re.search(r"\b(губ\w*|помад\w*|блеск|тинт|lip)\b", t_low):
            extra["type"] = "Макияж для губ (помады, блески, карандаши)"
        elif re.search(r"\b(глаз\w*|век\b|ресниц\w*|бров\w*|тушь|тени|подводк\w*|лайнер|eye|brow|mascara)\b", t_low):
            extra["type"] = "Макияж для глаз и бровей"
        elif re.search(r"\b(ногт\w*|маникюр\w*|лак\s+|кист\w*|спонж\w*|пилочк\w*|nail|brush)\b", t_low):
            extra["type"] = "Кисти, спонжи и маникюр"
        else:
            extra["type"] = "Макияж для лица (тон, пудра, контуринг)"

    elif category == "Книги и канцелярия":
        if "творчество и хобби" in sc_low:
            extra["type"] = "Товары для творчества и рисования"
        elif "книги" in sc_low:
            extra["type"] = "Книги и учебная литература"
        else:
            extra["type"] = "Офисная и школьная канцелярия"

    elif category == "Дом и сад":
        if any(w in sc_low for w in ("освещение", "лампочки", "светильники")):
            extra["type"] = "Светильники, люстры и LED-ленты"
        elif any(w in sc_low for w in ("сад", "огород", "полив", "растения", "семена")):
            extra["type"] = "Садовый инвентарь, полив и растения"
        elif any(w in sc_low for w in ("текстиль", "ковры", "постельное", "шторы")):
            extra["type"] = "Постельное белье, текстиль и ковры"
        elif any(w in sc_low for w in ("посуда", "кухня", "сервировка")):
            extra["type"] = "Кухонная утварь и сервировка"
        else:
            extra["type"] = "Хранение, декор и хозтовары"

    elif category == "Сад и огород":
        extra["type"] = "Садовый инвентарь, полив и растения"

    elif category == "Продукты питания":
        if any(w in sc_low for w in ("сладости", "кондитерские", "выпечка", "шоколад")):
            extra["type"] = "Сладости, шоколад и выпечка"
        elif any(w in sc_low for w in ("консервы", "соленья")):
            extra["type"] = "Консервы и соленья"
        elif any(w in sc_low for w in ("соусы", "масла", "специи", "приправы")):
            extra["type"] = "Масла, соусы и специи"
        elif any(w in sc_low for w in ("снеки", "чипсы", "орехи", "сухофрукты")):
            extra["type"] = "Снеки, чипсы и орехи"
        elif "чай" in sc_low:
            extra["type"] = "Чай"
        elif "кофе" in sc_low:
            extra["type"] = "Кофе и какао"
        elif any(w in sc_low for w in ("напитки", "вода", "соки")):
            extra["type"] = "Газированные и энергетические напитки"
        elif any(w in sc_low for w in ("молочные", "сыры")):
            extra["type"] = "Кисломолочные продукты, творог и йогурты"
        elif any(w in sc_low for w in ("мясо", "птица", "колбасы", "полуфабрикаты", "рыба")):
            extra["type"] = "Мясо, птица и полуфабрикаты"
        else:
            extra["type"] = "Бакалея, макароны и крупы"

    elif category == "Бытовая химия и хозтовары":
        if "стирк" in sc_low or "бель" in sc_low:
            extra["type"] = "Средства для стирки и ухода за бельем"
        elif "посуд" in sc_low:
            extra["type"] = "Средства для мытья посуды и ПММ"
        elif "бумаг" in sc_low or "салфетк" in sc_low:
            extra["type"] = "Туалетная бумага и салфетки"
        elif "чистота" in sc_low or "бытовая химия" in sc_low:
            extra["type"] = "Чистящие средства и освежители"
        else:
            extra["type"] = "Инвентарь для уборки и хозтовары"

    elif category == "Электроника":
        if any(w in t_low for w in ("умн", "датчик", "центр управления", "шлюз", "розетк", "выключател", "реле", "сирен", "звонок", "zigbee", "tuya", "aqara", "tapo", "rubetek", "считывател")):
            extra["type"] = "Умный дом и смарт-освещение"
        elif any(w in t_low for w in ("подставк", "кронштейн", "держател", "креплен", "стойк")):
            extra["type"] = "Подставка / Держатель / Кронштейн"
        elif any(w in t_low for w in ("пульт", "тв", "tv", "антенн", "ресивер", "приставк", "проектор", "экран")):
            extra["type"] = "ТВ-аксессуары, экраны и пульты ДУ"
        else:
            extra["type"] = "Док-станция / USB-хаб / Картридер"

    elif category == "Детские товары":
        if any(w in sc_low for w in ("питание", "смеси", "каши", "пюре", "сладости")):
            extra["type"] = "Детские пюре и каши"
        elif any(w in sc_low for w in ("кормление", "бутылочки", "пустышки")):
            extra["type"] = "Бутылочки, пустышки и кормление"
        elif any(w in sc_low for w in ("коляски", "автокресла")):
            extra["type"] = "Детская коляска / Аксессуары"
        elif any(w in sc_low for w in ("комната", "мебель", "кроватки")):
            extra["type"] = "Детская мебель, кроватки и текстиль"
        elif any(w in sc_low for w in ("игрушки", "развитие")):
            extra["type"] = "Игрушки для малышей и развитие"
        else:
            extra["type"] = "Уход за малышом и купание"

    elif category == "Товары для кошек":
        extra["pet_type"] = "Для кошек"
        if any(w in t_low for w in ("наполнитель", "лоток", "туалет", "совок", "пеленк")):
            extra["type"] = "Наполнитель и гигиена"
        elif any(w in t_low for w in ("лакомств", "витамин", "трава", "паста")):
            extra["type"] = "Лакомства и витамины"
        elif any(w in t_low for w in ("влажн", "пауч", "консерв", "желе", "соус")):
            extra["type"] = "Влажный корм / Паучи"
        elif "корм" in t_low:
            extra["type"] = "Сухой корм"
        else:
            extra["type"] = "Аксессуары и уход"

    elif category == "Аудиотехника":
        if any(w in t_low for w in ("микрофон", "радиосистем", "петличн")):
            extra["type"] = "Микрофон / Радиосистема"
        elif any(w in t_low for w in ("саундбар", "soundbar", "домашний кинотеатр")):
            extra["type"] = "Саундбар / Домашний кинотеатр"
        elif any(w in t_low for w in ("портативн", "bluetooth", "караоке")):
            extra["type"] = "Портативная / Вечериночная акустика"
        else:
            extra["type"] = "Стационарная / Компьютерная акустика"

    elif category == "Лекарства и витамины":
        extra["type"] = "Витамины и аптечные средства"

    return extra


def run_enrichment(db_path: str = "prices.db") -> None:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    now_iso = datetime.now(timezone.utc).isoformat()
    clf = CatalogClassifier()

    # 1. Sync Master Taxonomy
    cat_repo = CategoryRepository(conn)
    synced_cats = cat_repo.sync_master_taxonomy()
    conn.commit()
    print(f"[1/5] Synced {synced_cats} categories from MASTER_TAXONOMY.")

    # Build source category & shop lookup for canonical products
    src_info: Dict[str, Tuple[str, str]] = {}
    for r in conn.execute(
        """
        SELECT o.product_id, MAX(COALESCE(pr.category, '')) as src_cat, MAX(COALESCE(pr.shop, '')) as src_shop
        FROM offers o
        JOIN products pr ON pr.url = o.url
        GROUP BY o.product_id
        """
    ).fetchall():
        src_info[r["product_id"]] = (r["src_cat"] or "", r["src_shop"] or "")

    # 2. Cross-category cleanup of misplaced products
    moved_count = 0

    # 2a. Французский Дом makeup & perfume in Красота и здоровье
    rows_beauty = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Красота и здоровье'"
    ).fetchall()
    for r in rows_beauty:
        pid = r["id"]
        title = r["title"] or ""
        t_low = title.lower()
        sc, _ = src_info.get(pid, ("", ""))
        sc_low = sc.lower()
        if "макияж" in sc_low:
            _move_product(conn, pid, "Декоративная косметика", now_iso)
            moved_count += 1
        elif "парфюмерия" in sc_low or re.search(r"\b(парфюм|парфюмерная\s+вода|туалетная\s+вода|одеколон)\b", t_low):
            _move_product(conn, pid, "Парфюмерия", now_iso)
            moved_count += 1
        elif "стирка и уход за бель" in sc_low or "средства для мытья посуды" in sc_low or re.search(r"\b(calgon|для\s+мытья\s+(?:детской\s+)?посуды|для\s+стирки)\b", t_low):
            _move_product(conn, pid, "Бытовая химия и хозтовары", now_iso)
            moved_count += 1
        elif "игрушка-антистресс" in t_low:
            _move_product(conn, pid, "Игрушки и конструкторы", now_iso)
            moved_count += 1
        elif "ластик " in t_low:
            _move_product(conn, pid, "Книги и канцелярия", now_iso)
            moved_count += 1

    # 2b. Haircare and hygiene pads in Декоративная косметика
    rows_makeup = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Декоративная косметика'"
    ).fetchall()
    for r in rows_makeup:
        pid = r["id"]
        t_low = (r["title"] or "").lower()
        if re.search(r"\b(для\s+волос|шампунь|бальзам\s+для\s+волос|маска\s+для\s+волос|краска\s+для\s+волос|кондиционер\s+для\s+волос)\b", t_low):
            _move_product(conn, pid, "Уход за волосами", now_iso)
            moved_count += 1
        elif re.search(r"\b(прокладк\w*|тампон\w*|ватн\w*\s+(?:диск|палочк)|гель\s+для\s+душа|жидкое\s+мыло|зубн\w*\s+(?:паст|щетк)|дезодорант|антиперспирант)\b", t_low):
            _move_product(conn, pid, "Красота и здоровье", now_iso)
            moved_count += 1

    # 2c. Steam mops (Швабра паровая) in Бытовая химия и хозтовары -> Пылесосы
    rows_steam_mop = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Бытовая химия и хозтовары' AND title LIKE '%Швабра паровая%'"
    ).fetchall()
    for r in rows_steam_mop:
        _move_product(conn, r["id"], "Пылесосы", now_iso)
        moved_count += 1

    # 2d. Toothpaste in Продукты питания -> Красота и здоровье
    rows_food_tp = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Продукты питания' AND (title LIKE '%ПАСТА ЗУБНАЯ%' OR title LIKE '%Зубная паста%' OR title LIKE '%зубная паста%')"
    ).fetchall()
    for r in rows_food_tp:
        _move_product(conn, r["id"], "Красота и здоровье", now_iso)
        moved_count += 1

    # 2e. Auto parts/chemicals & laundry detergents in Дом и сад
    rows_home = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Дом и сад'"
    ).fetchall()
    for r in rows_home:
        pid = r["id"]
        t_low = (r["title"] or "").lower()
        if re.search(r"\b(шланг\s+тормозной|тормозн\w*\s+шланг|антидождь|\bavs\s+avk|\bavs\b.*\b(?:авто|шин|стекол|кузов|салон|двигател|тормоз))\b", t_low):
            _move_product(conn, pid, "Автотовары", now_iso)
            moved_count += 1
        elif re.search(r"\b(кондиционер\s+(?:vernel|lenor|mello)|для\s+детского\s+белья|порошок\s+karcher|средство\s+для\s+стирки)\b", t_low):
            _move_product(conn, pid, "Бытовая химия и хозтовары", now_iso)
            moved_count += 1

    # 2f. Flower pots in Мебель -> Сад и огород
    rows_furn_pots = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE category = 'Мебель'
          AND (
              title LIKE 'Горшок %для цветов%'
              OR title LIKE 'Горшок прямоугольный%'
              OR title LIKE 'Горшок пластиковый%'
              OR title LIKE 'Горшок-кадка%'
              OR title LIKE 'Подставка декоративная для цветов%'
          )
        """
    ).fetchall()
    for r in rows_furn_pots:
        _move_product(conn, r["id"], "Сад и огород", now_iso)
        moved_count += 1

    # 2g. Media converters & USB adapters in Электроника
    rows_elec = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Электроника'"
    ).fetchall()
    for r in rows_elec:
        pid = r["id"]
        t_low = (r["title"] or "").lower()
        if "медиаконвертер" in t_low or "трансивер" in t_low or "коммутатор" in t_low:
            _move_product(conn, pid, "Сетевое оборудование", now_iso)
            moved_count += 1
        elif re.search(r"^\s*(?:адаптер|переходник)\s+(?:usb|type-c|hdmi|displayport|vga|dvi|rj45|lightning)\b", t_low):
            _move_product(conn, pid, "Кабели и зарядные устройства", now_iso)
            moved_count += 1

    conn.commit()
    print(f"[2/5] Moved {moved_count} misplaced items to their canonical categories.")

    # 3. Backfill missing brands on Wave 8 categories
    wave8_categories = (
        "Мебель",
        "Текстиль и декор",
        "Инструменты и ремонт",
        "Красота и здоровье",
        "Декоративная косметика",
        "Парфюмерия",
        "Уход за волосами",
        "Книги и канцелярия",
        "Книги",
        "Дом и сад",
        "Сад и огород",
        "Продукты питания",
        "Бытовая химия и хозтовары",
        "Электроника",
        "Детские товары",
        "Товары для кошек",
        "Аудиотехника",
        "Лекарства и витамины",
        "Пылесосы",
    )
    placeholders = ",".join("?" for _ in wave8_categories)
    rows_no_brand = conn.execute(
        f"""
        SELECT id, title, brand, category FROM canonical_products
        WHERE category IN ({placeholders})
          AND (brand IS NULL OR TRIM(brand) = '' OR LOWER(TRIM(brand)) IN ('generic', 'no name', 'noname', 'нет бренда', 'без бренда'))
        """,
        wave8_categories,
    ).fetchall()

    brand_updated = 0
    for r in rows_no_brand:
        pid = r["id"]
        title = r["title"] or ""
        b = clf.extract_brand(title)
        if not b:
            sc, sh = src_info.get(pid, ("", ""))
            if re.search(r"\bkari\b", title, re.I) or sc.startswith("Kari:"):
                b = "Kari"
            elif sc.startswith("Mebel.kz:") or sh == "divan.kz":
                b = "Mebel.kz"
            elif sc.startswith("Zeta:") or sh == "zeta.kz":
                b = "Zeta"
        if b:
            conn.execute("UPDATE canonical_products SET brand = ? WHERE id = ?", (b, pid))
            brand_updated += 1
    conn.commit()
    print(f"[3/5] Backfilled brand on {brand_updated} products.")

    # 4. Re-index attributes for Wave 8 categories + apply source-category rules where needed
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
        SELECT p.id, p.title, p.category
        FROM canonical_products p
        WHERE p.category IN ({placeholders})
        """,
        wave8_categories,
    ).fetchall()

    print(f"[4/5] Re-indexing attributes for {len(target_rows)} products...")
    indexed_attrs = 0
    fallback_applied = 0
    for idx, r in enumerate(target_rows, start=1):
        pid = r["id"]
        title = r["title"] or ""
        cat = r["category"] or ""
        if not title:
            continue
        indexed_attrs += index_product_facts(conn, pid, facts_map.get(pid, []), title)

        # Check if product now has at least one non-color attribute; if not, apply deterministic source-category rule
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

        if idx % 500 == 0:
            conn.commit()
    conn.commit()
    print(f"[5/5] Indexed {indexed_attrs} attributes across {len(target_rows)} products (fallback rules applied on {fallback_applied} products).")

    # Summary table
    print("\n=== Wave 8 Category Coverage Summary ===")
    for cat in wave8_categories:
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
            f"  {cat:28s}: total={tot:5d} | brand={with_brand:5d} ({with_brand*100/tot:5.1f}%) | non_color_attrs={with_nc:5d} ({with_nc*100/tot:5.1f}%)"
        )

    conn.close()


if __name__ == "__main__":
    db_arg = sys.argv[1] if len(sys.argv) > 1 else "prices.db"
    run_enrichment(db_arg)
