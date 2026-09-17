import re
import sqlite3
import asyncio
import time
from typing import List, Dict, Any, Optional, Tuple
from config import DB_PATH, SEARCH_CACHE_TTL_SECONDS, CITIES_KZ
from database import save_or_update_product
from scrapers.kaspi import KaspiScraper

# In-memory кэш для внешних живых запросов: { "query:city": (timestamp, [items]) }
_LIVE_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

def parse_price(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else 0

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def build_fts_query(tokens: List[str], mode: str) -> str:
    clean = [re.sub(r"[\"\*\:\(\)\^\-\+]", "", t).strip() for t in tokens]
    clean = [t for t in clean if t]
    if not clean:
        return ""
    joined = " ".join(clean)
    if mode == "EXACT":
        return f'title: "{joined}"'
    elif mode == "OR":
        terms = " OR ".join([f'"{t}"*' for t in clean])
        return f'title: ({terms})'
    terms = " ".join([f'"{t}"*' for t in clean])
    return f'title: ({terms})'

def search_in_database(
    query: str,
    shop: Optional[str] = None,
    city: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    exclude_accessories: bool = False,
    match_mode: str = "AND",
    negative_keywords: Optional[List[str]] = None,
    limit: int = 150
) -> List[Dict[str, Any]]:
    """Поиск по локальной базе данных всех магазинов с использованием FTS5 и гибкими фильтрами."""
    query_clean = query.strip()
    tokens = [t.strip().lower() for t in query_clean.split() if len(t.strip()) > 1]
    if not tokens:
        tokens = [query_clean.lower()]

    mode = match_mode.upper() if match_mode else "AND"
    conn = get_db()
    cursor = conn.cursor()
    rows = []
    used_fts = False

    # 1. Попытка высокоскоростного поиска через полнотекстовый индекс SQLite FTS5
    fts_expr = build_fts_query(tokens, mode) if query_clean else ""
    if fts_expr:
        try:
            fts_conditions = ["products_fts MATCH ?", "p.current_price > 0"]
            fts_params = [fts_expr]

            if shop and shop != "Все":
                fts_conditions.append("p.shop = ?")
                fts_params.append(shop)

            if city and city != "Все":
                fts_conditions.append("(p.city = ? OR p.city IS NULL)")
                fts_params.append(city)

            if min_price is not None and min_price > 0:
                fts_conditions.append("p.current_price >= ?")
                fts_params.append(int(min_price))

            if max_price is not None and max_price > 0:
                fts_conditions.append("p.current_price <= ?")
                fts_params.append(int(max_price))

            fts_sql = f"""
                SELECT p.* FROM products_fts f
                JOIN products p ON f.rowid = p.rowid
                WHERE {" AND ".join(fts_conditions)}
                ORDER BY p.current_price ASC
                LIMIT ?
            """
            fts_params.append(limit)
            cursor.execute(fts_sql, fts_params)
            rows = cursor.fetchall()
            used_fts = True
        except sqlite3.OperationalError:
            used_fts = False

    # 2. Fallback на классический LIKE поиск, если FTS недоступен или выдал синтаксическую ошибку
    if not used_fts:
        conditions = []
        params = []

        if mode == "EXACT":
            conditions.append("LOWER(title) LIKE ?")
            params.append(f"%{query_clean.lower()}%")
        elif mode == "OR":
            or_conds = []
            for t in tokens:
                or_conds.append("LOWER(title) LIKE ?")
                params.append(f"%{t}%")
            if or_conds:
                conditions.append(f"({' OR '.join(or_conds)})")
        else:  # AND
            for t in tokens:
                conditions.append("LOWER(title) LIKE ?")
                params.append(f"%{t}%")

        if shop and shop != "Все":
            conditions.append("shop = ?")
            params.append(shop)

        if city and city != "Все":
            conditions.append("(city = ? OR city IS NULL)")
            params.append(city)

        if min_price is not None and min_price > 0:
            conditions.append("current_price >= ?")
            params.append(int(min_price))

        if max_price is not None and max_price > 0:
            conditions.append("current_price <= ?")
            params.append(int(max_price))

        where_sql = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT * FROM products
            WHERE {where_sql} AND current_price > 0
            ORDER BY current_price ASC
            LIMIT ?
        """
        params.append(limit)
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    conn.close()
    results = [dict(r) for r in rows]

    # 5. Исключение аксессуаров / чехлов / хлама
    from detector import is_junk_accessory
    if exclude_accessories:
        from config import load_settings
        junk_list = load_settings().get("junk_keywords", [])
        results = [r for r in results if not is_junk_accessory(r["title"], custom_keywords=junk_list)]

    # 6. Исключение пользовательских минус-слов
    if negative_keywords:
        negs = [nk.strip().lower() for nk in negative_keywords if nk.strip()]
        if negs:
            results = [r for r in results if not any(nk in r["title"].lower() for nk in negs)]

    return results

async def search_live_stores(query: str, city: str = "Астана") -> List[Dict[str, Any]]:
    """Живой опрос площадок (включая Kaspi) по поисковому запросу с кэшированием."""
    city_name = city or "Астана"
    cache_key = f"{query.strip().lower()}:{city_name.strip().lower()}"
    now_ts = time.time()

    # Anti-DDoS: проверка in-memory кэша
    if cache_key in _LIVE_CACHE:
        cached_ts, cached_items = _LIVE_CACHE[cache_key]
        if (now_ts - cached_ts) < SEARCH_CACHE_TTL_SECONDS:
            return cached_items

    all_found = []

    # 1. Поиск в Kaspi Магазине
    try:
        kaspi_city_code = CITIES_KZ.get(city_name, {}).get("kaspi_code", "710000000")
        kaspi = KaspiScraper(city_code=kaspi_city_code)
        kaspi_results = await kaspi.search(query, max_items=15)
        for item in kaspi_results:
            item["city"] = city_name
            save_or_update_product(item)
            all_found.append(item)
    except Exception as e:
        print(f"[SearchEngine] Ошибка live-поиска в Kaspi: {e}")

    # 2. Поиск в Белом Ветре (shop.kz) через HTTP
    try:
        from curl_cffi import requests
        from bs4 import BeautifulSoup
        import urllib.parse

        shopkz_city = CITIES_KZ.get(city_name, {}).get("shopkz_city", "astana")
        enc = urllib.parse.quote(query)
        url = f"https://shop.kz/search/?q={enc}"
        r = requests.get(url, impersonate="chrome124", cookies={"BITRIX_SM_CITY": shopkz_city}, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".bx_catalog_item")
            for c in cards[:10]:
                title_el = c.select_one(".bx_catalog_item_title a")
                price_el = c.select_one(".current_price span, .current_price")
                if not title_el or not price_el:
                    continue
                title = title_el.text.strip()
                p_val = parse_price(price_el.text)
                if p_val <= 0:
                    continue
                rel_link = title_el.get("href", "")
                full_link = f"https://shop.kz{rel_link}" if rel_link.startswith("/") else rel_link
                img_el = c.select_one("img")
                img_src = img_el.get("data-src") or img_el.get("src") or "" if img_el else ""

                item_data = {
                    "shop": "Белый Ветер",
                    "id": f"shopkz_{rel_link[-20:]}",
                    "title": title,
                    "category": f"Поиск: {query}",
                    "url": full_link,
                    "image_url": img_src if img_src.startswith("http") else f"https://shop.kz{img_src}",
                    "price": p_val,
                    "old_price_on_site": 0,
                    "city": city_name
                }
                save_or_update_product(item_data)
                all_found.append(item_data)
    except Exception as e:
        print(f"[SearchEngine] Ошибка live-поиска в Shop.kz: {e}")

    # 3. Поиск в 4mobile (4mobile.pages.dev)
    try:
        from scrapers.fourmobile import FourMobileScraper
        four_mobile = FourMobileScraper()
        fm_results = await four_mobile.search_live(query)
        for item in fm_results:
            item["city"] = city_name
            save_or_update_product(item)
            all_found.append(item)
    except Exception as e:
        print(f"[SearchEngine] Ошибка live-поиска в 4mobile: {e}")

    # Сохраняем результат в кэш
    _LIVE_CACHE[cache_key] = (now_ts, all_found)
    return all_found

async def get_best_price_summary(
    query: str,
    live: bool = False,
    shop: Optional[str] = None,
    city: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    exclude_accessories: bool = True,
    match_mode: str = "AND",
    sort_by: str = "price_asc",
    negative_keywords: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Комплексный поиск с выявлением минимальной цены, экономии и сравнением магазинов.
    По умолчанию (Cache-First) поиск осуществляется ИСКЛЮЧИТЕЛЬНО по локальной базе данных,
    не создавая сетевой нагрузки на сайты магазинов.
    """
    query_clean = query.strip()
    if not query_clean:
        return {
            "query": "",
            "total_found": 0,
            "best_deal": None,
            "price_stats": None,
            "store_comparison": [],
            "items": []
        }

    # 1. Поиск по локальной базе данных (Cache-First)
    local_items = search_in_database(
        query_clean,
        shop=shop,
        city=city,
        min_price=min_price,
        max_price=max_price,
        exclude_accessories=exclude_accessories,
        match_mode=match_mode,
        negative_keywords=negative_keywords
    )

    # 2. Опрос внешних площадок ТОЛЬКО при явном запросе пользователя (Anti-DDoS защита)
    if live:
        await search_live_stores(query_clean, city=city or "Астана")
        local_items = search_in_database(
            query_clean,
            shop=shop,
            city=city,
            min_price=min_price,
            max_price=max_price,
            exclude_accessories=exclude_accessories,
            match_mode=match_mode,
            negative_keywords=negative_keywords
        )

    if not local_items:
        return {
            "query": query_clean,
            "total_found": 0,
            "best_deal": None,
            "price_stats": None,
            "store_comparison": [],
            "items": []
        }

    # Анализ цен
    prices = [item["current_price"] for item in local_items if item["current_price"] > 0]
    min_p = min(prices)
    max_p = max(prices)
    avg_p = int(sum(prices) / len(prices))

    # Победитель по минимальной цене
    cheapest_item = min(local_items, key=lambda x: x["current_price"])

    savings = max_p - min_p
    savings_pct = int(round((savings / max_p) * 100)) if max_p > min_p else 0

    # Лучшая цена в каждом магазине
    store_map: Dict[str, Dict[str, Any]] = {}
    for item in local_items:
        s_name = item["shop"]
        if s_name not in store_map or item["current_price"] < store_map[s_name]["current_price"]:
            store_map[s_name] = item

    store_comparison = []
    for s_name, item in sorted(store_map.items(), key=lambda x: x[1]["current_price"]):
        diff = item["current_price"] - min_p
        store_comparison.append({
            "shop": s_name,
            "min_price": item["current_price"],
            "title": item["title"],
            "url": item["url"],
            "image_url": item.get("image_url", ""),
            "diff_from_best": diff,
            "diff_kzt": diff
        })

    # Сортировка итоговой выдачи
    formatted_items = []
    for it in local_items:
        it_copy = dict(it)
        it_copy["price"] = it["current_price"]
        it_copy["diff_from_best"] = it["current_price"] - min_p
        it_copy["savings_vs_max"] = max_p - it["current_price"]
        formatted_items.append(it_copy)

    if sort_by == "price_desc":
        formatted_items.sort(key=lambda x: x["current_price"], reverse=True)
    elif sort_by == "savings_desc":
        formatted_items.sort(key=lambda x: x["savings_vs_max"], reverse=True)
    else:  # price_asc
        formatted_items.sort(key=lambda x: x["current_price"], reverse=False)

    return {
        "query": query_clean,
        "total_found": len(local_items),
        "best_deal": {
            "id": cheapest_item["id"],
            "shop": cheapest_item["shop"],
            "title": cheapest_item["title"],
            "price": cheapest_item["current_price"],
            "current_price": cheapest_item["current_price"],
            "url": cheapest_item["url"],
            "image_url": cheapest_item.get("image_url", ""),
            "savings_vs_max": savings,
            "savings_pct": savings_pct,
            "max_market_price": max_p
        },
        "price_stats": {
            "min": min_p,
            "max": max_p,
            "avg": avg_p,
            "min_price": min_p,
            "max_price": max_p,
            "avg_price": avg_p,
            "stores_count": len(store_map)
        },
        "store_comparison": store_comparison,
        "items": formatted_items
    }
