#!/usr/bin/env python3
"""
Wave 6 Catalog Enrichment & Unmapped Taxonomy Cleanup Script (v6.28.29).
Enriches Software, Telephony, Bicycles/Scooters, Sport/Fitness, Auto Parts,
Protective Films, Cables/Batteries, Furniture, Tools, Books/Stationery, and
maps all remaining non-standard store categories into MASTER_TAXONOMY.
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog_classifier import CatalogClassifier
from domain.attribute_indexing import sync_all_source_attributes
from domain.category import MASTER_TAXONOMY
from repositories.category_repo import CategoryRepository


def _move_product(conn: sqlite3.Connection, pid: str, cat_id: str, cat_name: str, kind: str | None = None) -> None:
    conn.execute("UPDATE product_category_tags SET category_id = ? WHERE product_id = ?", (cat_id, pid))
    if kind:
        conn.execute("UPDATE canonical_products SET category = ?, item_kind = ? WHERE id = ?", (cat_name, kind, pid))
    else:
        conn.execute("UPDATE canonical_products SET category = ? WHERE id = ?", (cat_name, pid))


DIRECT_CATEGORY_MAP = {
    'Геодезическое оборудование': 'Инструменты и ремонт',
    'MasterOK: 🌱 Мотоблоки и культиваторы': 'Инструменты и ремонт',
    'Лемана ПРО: 🪜 Стремянки': 'Инструменты и ремонт',
    'Снегоуборочные лопаты': 'Инструменты и ремонт',
    'Шпатлевочные смеси': 'Инструменты и ремонт',
    'Штукатурки': 'Инструменты и ремонт',
    'Снегоуборочные машины': 'Инструменты и ремонт',
    'Аксессуары для нивелиров и лазерных уровней': 'Инструменты и ремонт',
    'Лемана ПРО: 🧱 Стройматериалы': 'Инструменты и ремонт',
    'Обои': 'Инструменты и ремонт',
    'Экраны для ванн': 'Инструменты и ремонт',
    'Экраны для радиаторов': 'Инструменты и ремонт',
    'MasterOK: ✨ Новинки': 'Инструменты и ремонт',
    'MasterOK: 🚜 Минитракторы': 'Инструменты и ремонт',
    'Бетономешалки': 'Инструменты и ремонт',
    'Герметики': 'Инструменты и ремонт',
    'Измерительные рулетки': 'Инструменты и ремонт',
    'Отопительные котлы': 'Инструменты и ремонт',
    'Сварочные выпрямители, инверторы': 'Инструменты и ремонт',
    'Штативы для нивелиров': 'Инструменты и ремонт',
    'Электрические шлифовальные машины': 'Инструменты и ремонт',
    'Внешние аккумуляторы': 'Кабели и зарядные устройства',
    'Гипс для лепки': 'Книги и канцелярия',
    'Ручки': 'Книги и канцелярия',
    'Лаки для живописи': 'Книги и канцелярия',
    'Меломан: 🎨 Творчество и хобби': 'Книги и канцелярия',
    'Ластики': 'Книги и канцелярия',
    'Наборы для росписи': 'Книги и канцелярия',
    'Гипсовые и деревянные фигуры для художников': 'Книги и канцелярия',
    'Скобы для степлера': 'Книги и канцелярия',
    'Игрушечный транспорт': 'Игрушки и конструкторы',
    'Брелоки и ключницы': 'Игрушки и конструкторы',
    'Гастрономия': 'Продукты питания',
    'Собственное производство': 'Продукты питания',
    'Консервы': 'Продукты питания',
    'Фрукты и овощи': 'Продукты питания',
    'Пульты для аудио и видеотехники': 'Электроника',
    'Интерактивные панели': 'Электроника',
    'Проекционные экраны': 'Электроника',
    'Аксессуары для аудио-видео техники': 'Электроника',
    'Держатели для телефонов': 'Электроника',
    'Контент': 'Фото и видео',
    'Аксессуары для туристических горелок': 'Туризм и кемпинг',
    'Аксессуары для рыбалки': 'Туризм и кемпинг',
    'Походные печи': 'Туризм и кемпинг',
    'Комплектующие для шкафов': 'Сетевое оборудование',
    'Аксессуары для волос': 'Женская одежда',
    'Аксессуары для мастера': 'Красота и здоровье',
    'Декор для дизайна ногтей': 'Декоративная косметика',
    'Зеркала косметические': 'Красота и здоровье',
    'Косметички и несессеры': 'Красота и здоровье',
    'Насадки для аппаратного маникюра': 'Красота и здоровье',
    'Очищающие и укрепляющие средства для бровей и ресниц': 'Уход за кожей',
    'Парикмахерские принадлежности': 'Красота и здоровье',
    'Пилки и пемзы': 'Красота и здоровье',
    'Принадлежности для ванны и душа': 'Красота и здоровье',
    'Ватные палочки и диски': 'Красота и здоровье',
    'Французский Дом: 🎁 Подарочные наборы': 'Красота и здоровье',
    'Кремы и сыворотки': 'Уход за кожей',
    'Средства по уходу за волосами': 'Уход за волосами',
    'Ароматизаторы салона автомобиля': 'Автотовары',
    'Автосканеры': 'Автоэлектроника',
    'Аксессуары для автомобильной аудиотехники': 'Автоэлектроника',
    'Очистители и полироли для шин и дисков автомобилей': 'Масла и автохимия',
    'Полироль для салона автомобиля': 'Масла и автохимия',
    'Бытовые товары и химия': 'Товары для малышей',
    'Витамины и БАД': 'Лекарства и витамины',
    'Лемана ПРО: 📦 Аксессуары для хранения': 'Текстиль и декор',
    'Zeta: 🧥 Вешалки': 'Текстиль и декор',
    'Коврики для ванной': 'Текстиль и декор',
    'Новогодние елки': 'Текстиль и декор',
    'Статуэтки и фигурки': 'Текстиль и декор',
    'Комфорт: 🌿 Сад, огород и дача': 'Сад и огород',
    'Комфорт: 💡 Электротовары, свет и климат': 'Бытовая техника',
    'Осушители воздуха': 'Бытовая техника',
    'Очистители и увлажнители': 'Бытовая техника',
    'Кофеварки и кофемашины': 'Техника для кухни',
    'Ножи, ножницы и наборы': 'Посуда и кухня',
    'Сервизы': 'Посуда и кухня',
    'Массажные кресла': 'Фитнес и тренажеры',
    'Беговые дорожки': 'Фитнес и тренажеры',
    'Матрасы': 'Мебель',
    'Диваны и комплекты мягкой мебели': 'Мебель',
    'Кровати': 'Мебель',
    'Кухонные гарнитуры': 'Мебель',
    'Обеденные группы': 'Мебель',
    'Шкафы для одежды': 'Мебель',
    'Samsung': 'Смартфоны',
    'Zeta: 🪣 Емкости, баки и ведра': 'Бытовая химия и хозтовары',
    'Магнум: 🧴 Средства гигиены': 'Бытовая химия и хозтовары',
    'Оргтехника': 'Принтеры и МФУ',
    'Сухие корма для собак': 'Товары для собак',
    'Термопаста': 'Охлаждение и кулеры',
}


def run_enrichment(db_path: str = 'prices.db') -> None:
    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # 0a. Sync MASTER_TAXONOMY schemas into categories table
    cat_repo = CategoryRepository(conn)
    synced_cats = cat_repo.sync_master_taxonomy()
    print(f"Synced {synced_cats} categories in master taxonomy.")

    # 0b. Purge any leaked test sellers from earlier test runs
    test_sids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM sellers WHERE name IN ('Тест Стор Алматы', 'Новый Магазин', 'Тест Магазин', 'Тест Стор Обновленный') OR domain = 'test-store.kz' OR id IN ('seller_shop-1734e375', 'seller_shop-8a086712')"
        ).fetchall()
    ]
    if test_sids:
        ph = ",".join("?" for _ in test_sids)
        for tbl in ("offer_price_history", "offers", "social_sources", "content_observations", "source_profiles", "seller_identities", "channels"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE seller_id IN ({ph})", test_sids)
            except Exception:
                pass
        conn.execute(f"DELETE FROM sellers WHERE id IN ({ph})", test_sids)
        conn.commit()

    id_to_name = {c.id: c.name for c in MASTER_TAXONOMY}
    name_to_id = {c.name: c.id for c in MASTER_TAXONOMY}
    valid_names = set(id_to_name.values()) | set(id_to_name.keys())
    clf = CatalogClassifier()

    # 0c. Clean up any legacy AI-created categories in `categories` table (e.g. ai_706f8863ee306bd0 'Геодезическое оборудование')
    extra_cats = conn.execute(
        "SELECT id, name, parent_id FROM categories WHERE id NOT IN (" + ",".join("?" for _ in id_to_name) + ")",
        list(id_to_name.keys()),
    ).fetchall()
    for ec in extra_cats:
        target_name = DIRECT_CATEGORY_MAP.get(ec["name"], "Инструменты и ремонт")
        target_id = name_to_id.get(target_name, ec["parent_id"] or "tools_repair")
        conn.execute("UPDATE product_category_tags SET category_id = ? WHERE category_id = ?", (target_id, ec["id"]))
        conn.execute("UPDATE canonical_products SET category = ? WHERE category = ?", (id_to_name[target_id], ec["name"]))
        conn.execute("DELETE FROM categories WHERE id = ?", (ec["id"],))

    # 1. Map direct non-standard categories to MASTER_TAXONOMY (updating product_category_tags first!)
    mapped_direct = 0
    for old_cat, new_cat in DIRECT_CATEGORY_MAP.items():
        new_id = name_to_id[new_cat]
        pids = [r["id"] for r in conn.execute("SELECT id FROM canonical_products WHERE category = ?", (old_cat,)).fetchall()]
        for pid in pids:
            _move_product(conn, pid, new_id, new_cat)
            mapped_direct += 1

    # 2. Classify mixed promo/sale categories item-by-item using CatalogClassifier + fallback
    promo_fallbacks = {
        'Все акции и скидки': 'Продукты питания',
        'Магнум: 🔥 Все акции и скидки': 'Продукты питания',
        'Распродажа витрины': 'Инструменты и ремонт',
        'Уценка': 'Инструменты и ремонт',
        'Распродажа': 'Электроника',
        'Sulpak: 🔥 Распродажа': 'Электроника',
    }
    mapped_promo = 0
    rows_unmapped = conn.execute(
        "SELECT id, title, category FROM canonical_products WHERE category IS NULL OR category NOT IN ("
        + ",".join("?" for _ in valid_names)
        + ")",
        list(valid_names),
    ).fetchall()
    for r in rows_unmapped:
        pid, title, cat = r["id"], r["title"], r["category"]
        target_cat = None
        if title:
            res = clf.classify_rules(title)
            if res and res.category_id in id_to_name:
                target_cat = id_to_name[res.category_id]
        if not target_cat:
            target_cat = promo_fallbacks.get(cat or '', 'Электроника')
        target_id = name_to_id.get(target_cat, 'electronics')
        _move_product(conn, pid, target_id, target_cat)
        mapped_promo += 1

    # 3. Fix misclassified items inside existing categories
    reclassified_fixes = 0
    # Office paper in 'Книги' -> 'Книги и канцелярия'
    for r in conn.execute(
        "SELECT id FROM canonical_products WHERE category IN ('Книги', 'books') AND (title LIKE 'Бумага %' OR title LIKE '%для офисной техники%')"
    ).fetchall():
        _move_product(conn, r["id"], "books_stationery", "Книги и канцелярия")
        reclassified_fixes += 1

    # Steam mops in 'Бытовая химия и хозтовары' -> 'Пылесосы'
    for r in conn.execute(
        "SELECT id FROM canonical_products WHERE category IN ('Бытовая химия и хозтовары', 'household_chem') AND (title LIKE '%Швабра паровая%' OR title LIKE '%Паровая швабра%' OR title LIKE '%Электрошвабра%')"
    ).fetchall():
        _move_product(conn, r["id"], "vacuums", "Пылесосы")
        reclassified_fixes += 1

    # Server rack shelves / patch panels in 'Мебель' -> 'Сетевое оборудование'
    for r in conn.execute(
        "SELECT id FROM canonical_products WHERE category IN ('Мебель', 'furniture') AND (title LIKE '%Ship 70%' OR title LIKE 'Patch panel%Ship%' OR title LIKE 'Комплект направляющих для стоечных серверов%')"
    ).fetchall():
        _move_product(conn, r["id"], "network", "Сетевое оборудование")
        reclassified_fixes += 1

    # Gaming chairs in 'Спорт и отдых' or 'Электроника' -> 'Мебель'
    for r in conn.execute(
        "SELECT id FROM canonical_products WHERE category IN ('Спорт и отдых', 'sport', 'Электроника', 'electronics') AND (title LIKE 'Игровое кресло%' OR title LIKE 'Кресло игровое%' OR title LIKE 'Компьютерное кресло%')"
    ).fetchall():
        _move_product(conn, r["id"], "furniture", "Мебель")
        reclassified_fixes += 1

    # Modems in 'Стационарные и IP-телефоны' -> 'Сетевое оборудование'
    for r in conn.execute(
        "SELECT id FROM canonical_products WHERE category IN ('Стационарные и IP-телефоны', 'telephony') AND (title LIKE 'Модем %' OR title LIKE '%Маршрутизатор%' OR title LIKE '%Роутер%')"
    ).fetchall():
        _move_product(conn, r["id"], "network", "Сетевое оборудование")
        reclassified_fixes += 1

    # Welding mask glass & bathroom brushes in 'Защитные стекла и пленки' -> 'Инструменты и ремонт' / 'Бытовая химия и хозтовары'
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category IN ('Защитные стекла и пленки', 'phone_glass')"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"маск\w*\s+сварщик\w*|для\s+масок\s+foxweld|для\s+сварочн\w*", t, re.I):
            _move_product(conn, r["id"], "tools_repair", "Инструменты и ремонт")
            reclassified_fixes += 1
        elif re.search(r"^\s*ерш\b|обод\s+щетки", t, re.I):
            _move_product(conn, r["id"], "household_chem", "Бытовая химия и хозтовары")
            reclassified_fixes += 1

    conn.commit()
    print(
        f"Taxonomy cleanup: {mapped_direct} direct mapped, {mapped_promo} promo classified, "
        f"{reclassified_fixes} cross-category fixes."
    )

    # 4. Backfill missing brands on canonical_products + fix false Dell/Candy/LG in Books & Stationery
    rows = conn.execute(
        """
        SELECT id, title FROM canonical_products
        WHERE title IS NOT NULL AND (
            brand IS NULL OR brand = '' OR LOWER(brand) = 'unknown'
            OR (category IN ('Книги', 'Книги и канцелярия') AND brand IN ('Dell', 'Candy', 'LG'))
        )
        """
    ).fetchall()
    brand_updates = 0
    for r in rows:
        pid, title = r["id"], r["title"]
        b = clf.extract_brand(title)
        if b in ("Dell", "Candy", "LG") and re.search(r"блокнот|холст|альбом|пластилин|пенал|пакет", title, re.I):
            b = None
        conn.execute("UPDATE canonical_products SET brand = ? WHERE id = ?", (b, pid))
        brand_updates += 1
    conn.commit()
    print(f"Updated/backfilled brand on {brand_updates} canonical products.")

    # 5. Re-index attributes across the catalog
    print("Running sync_all_source_attributes...")
    total_indexed = sync_all_source_attributes(conn, limit=150000)
    print(f"Total attributes indexed: {total_indexed}")

    # 6. Clear search intents cache so new facets take effect immediately
    try:
        conn.execute("DELETE FROM local_search_intents")
        conn.commit()
        print("Cleared local_search_intents cache.")
    except Exception:
        pass

    # 7. Print Wave 6 coverage summary
    wave6_cats = [
        'Программы', 'Стационарные и IP-телефоны', 'Книги', 'Книги и канцелярия',
        'Велосипеды и самокаты', 'Спорт и отдых', 'Фитнес и тренажеры',
        'Автотовары', 'Автоэлектроника', 'Защитные стекла и пленки',
        'Бытовая химия и хозтовары', 'Кабели и зарядные устройства',
        'Мебель', 'Инструменты и ремонт', 'Электроника'
    ]
    print("\nWave 6 Category Non-Color Attribute Coverage:")
    for cat in wave6_cats:
        total, with_nc = conn.execute(
            """
            SELECT COUNT(DISTINCT cp.id),
                   COUNT(DISTINCT CASE WHEN pa.key IS NOT NULL AND pa.key != 'color' THEN cp.id END)
            FROM canonical_products cp
            LEFT JOIN product_search_attributes pa ON pa.product_id = cp.id
            WHERE cp.category = ?
            """,
            (cat,),
        ).fetchone()
        pct = (with_nc * 100.0 / total) if total else 0.0
        print(f"  {cat:32s}: {with_nc:5d} / {total:5d} ({pct:5.1f}%)")

    conn.close()


if __name__ == '__main__':
    db_file = sys.argv[1] if len(sys.argv) > 1 else 'prices.db'
    run_enrichment(db_file)
