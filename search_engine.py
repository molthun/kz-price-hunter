from database import active_product_clause
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

LAYOUT_RU_TO_EN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю.ёЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,Ё",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./`QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?~"
)

LAYOUT_EN_TO_RU = str.maketrans(
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./`QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?~",
    "йцукенгшщзхъфывапролджэячсмитьбю.ёЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,Ё"
)

BRAND_SYNONYMS = {
    'айфон': 'iphone',
    'самсунг': 'samsung',
    'сяоми': 'xiaomi',
    'ксиоми': 'xiaomi',
    'хиаоми': 'xiaomi',
    'редми': 'redmi',
    'эппл': 'apple',
    'эпл': 'apple',
    'хуавей': 'huawei',
    'хонор': 'honor',
    'макбук': 'macbook',
    'айпад': 'ipad',
    'сони': 'sony',
    'асус': 'asus',
    'леново': 'lenovo',
    'эйсер': 'acer',
    'асер': 'acer',
    'хп': 'hp',
    'делл': 'dell',
    'дел': 'dell',
    'хбокс': 'xbox',
    'иксбокс': 'xbox',
    'плейстейшн': 'playstation',
    'плейстейшен': 'playstation',
    'стиралка': 'стиральная',
    'телик': 'телевизор',
    'комп': 'компьютер',
    'видюха': 'видеокарта',
    'проц': 'процессор',
    'ноут': 'ноутбук',
    'лэптоп': 'ноутбук',
    'уши': 'наушники',
    'часы': 'watch',
}

ACCESSORY_KEYWORDS = [
    "чехол", "стекло", "пленка", "плёнка", "ремешок", "ремешки", "браслет для", "strap", "watch band", "кабель", "переходник",
    "держатель", "подставка", "амбушюры", "накладка", "салфетки", "зарядное",
    "зарядка", "блок питания", "адаптер", "пульт", "джойстик", "геймпад"
]

def parse_price(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", str(price_str or ""))
    return int(digits) if digits else 0

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_accessory_query(query: str) -> bool:
    """Проверяет, ищет ли пользователь явно аксессуар."""
    q = query.lower()
    return any(k in q for k in ACCESSORY_KEYWORDS)

def stem_russian_word(word: str) -> str:
    """Усечение падежных и грамматических окончаний русских слов."""
    if not re.search(r'[а-яё]', word):
        return word
    stem = re.sub(r'(ами|ями|ов|ев|ей|ом|ем|ой|ых|их|ого|его|ому|ему|ая|яя|ое|ее|ые|ие|[аяоеыиуюьеэ])$', '', word)
    return stem if len(stem) >= 3 else word

def expand_token_fts(token: str) -> str:
    """Преобразует токен в безопасную FTS5 конструкцию с поддержкой синонимов и морфологии."""
    clean = re.sub(r'[^\w\-]', '', token).strip()
    if not clean:
        return ""

    # Числа (5, 15, 24, 4060) ищем строго без префиксного wildcard, чтобы 5 не матчила 500
    if clean.isdigit():
        return f'"{clean}"'

    lower = clean.lower()

    # Специфические модели с цифрами
    if lower in ('ps5', 'пс5'):
        return '(ps5* OR (playstation* AND "5"))'
    if lower in ('ps4', 'пс4'):
        return '(ps4* OR (playstation* AND "4"))'

    candidates = [lower]

    # Синонимы брендов
    if lower in BRAND_SYNONYMS:
        candidates.append(BRAND_SYNONYMS[lower])

    # Исправление раскладки клавиатуры (только если результат алфавитно-цифровой)
    ru_trans = clean.translate(LAYOUT_EN_TO_RU).lower()
    if ru_trans != lower and re.match(r'^[а-яё0-9]+$', ru_trans):
        candidates.append(ru_trans)

    en_trans = clean.translate(LAYOUT_RU_TO_EN).lower()
    if en_trans != lower and re.match(r'^[a-z0-9]+$', en_trans):
        candidates.append(en_trans)

    parts = []
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        stemmed = stem_russian_word(c)
        clause = f'{stemmed}*' if len(stemmed) >= 2 else f'"{stemmed}"'
        parts.append(clause)

    if len(parts) == 1:
        return parts[0]
    return f'({" OR ".join(parts)})'

def build_fts_query(query: str, mode: str = "AND") -> str:
    """Генерирует полнотекстовый запрос FTS5 по полям {title category}."""
    raw_tokens = [t.strip() for t in query.split() if t.strip()]
    if not raw_tokens:
        return ""

    if mode == "EXACT":
        clean = re.sub(r'[\"\*\:\(\)\^\-\+]', ' ', query).strip()
        clean = " ".join(clean.split())
        return f'{{title category}}: "{clean}"' if clean else ""

    clauses = [expand_token_fts(t) for t in raw_tokens]
    clauses = [c for c in clauses if c]
    if not clauses:
        return ""

    joiner = " OR " if mode.upper() == "OR" else " AND "
    return joiner.join([f"{{title category}}: {c}" for c in clauses])

# Названия, начинающиеся с этих слов, — аксессуары, а не сам товар
_ACCESSORY_LEADS = ("сумка", "рюкзак", "чехол", "кейс", "кронштейн", "крепление", "подставка", "держатель",
                    "пленка", "плёнка", "стекло", "кабель", "адаптер", "переходник", "зарядное", "блок питания",
                    "аккумулятор для", "фильтр", "пылесборник", "мешок", "мешки", "щетка", "насадка", "пульт",
                    "наклейка", "шлейф", "клавиатура для", "матрица", "петли", "вентилятор для", "кулер для")

def _is_accessory_for(title: str, nouns: List[str]) -> bool:
    t = " ".join(title.lower().replace("ё", "е").split())
    if t.startswith(_ACCESSORY_LEADS):
        return True
    return any(re.search(rf"\bдля\s+(?:\S+\s+){{0,2}}{re.escape(n)}", t) for n in nouns)

def score_relevance(product: Dict[str, Any], query_clean: str, query_tokens: List[str]) -> float:
    """Вычисляет релевантность товара для сортировки результатов."""
    title_lower = product.get("title", "").lower()
    category_lower = product.get("category", "").lower()
    score = 0.0

    # Точное совпадение поисковой фразы в заголовке
    if query_clean in title_lower:
        score += 100.0
        if title_lower.startswith(query_clean):
            score += 50.0

    # Наличие каждого токена запроса в заголовке
    for t in query_tokens:
        t_low = t.lower()
        if t_low in title_lower:
            score += 15.0
        elif t_low in category_lower:
            score += 5.0

    # Небольшой штраф за избыточную длину заголовка (фокус на целевой модели)
    score -= len(title_lower) * 0.05
    return score

def search_in_database(
    query: str,
    shop: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    only_discount: bool = False,
    exclude_accessories: bool = True,
    match_mode: str = "AND",
    sort_by: str = "price_asc",
    negative_keywords: Optional[List[str]] = None,
    limit: int = 250,
    junk_keywords: Optional[List[str]] = None,
    prefer_keywords: Optional[List[str]] = None,
    product_nouns: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Полнофункциональный поиск по базе данных с FTS5, поддержкой синонимов, категорий и городов.

    prefer_keywords — мягкое предпочтение (например, игровые модели), product_nouns — основы названий
    товара из запроса: аксессуары «для <товара>» убираются. С этими фильтрами кандидатов берется
    с запасом, иначе 250 самых дешевых совпадений оказываются сумками и креплениями.
    """
    result_limit = limit
    if prefer_keywords or product_nouns:
        limit = max(limit, 3000)
    query_clean = query.strip().lower()
    raw_tokens = [t.strip().lower() for t in query.split() if t.strip()]

    mode = match_mode.upper() if match_mode else "AND"
    conn = get_db()
    cursor = conn.cursor()
    rows = []
    used_fts = False

    # 1. Попытка высокоскоростного поиска через FTS5
    fts_expr = build_fts_query(query, mode) if query_clean else ""
    if fts_expr:
        try:
            fts_conditions = ["products_fts MATCH ?", "p.current_price > 0", active_product_clause("p")]
            fts_params = [fts_expr]

            if shop and shop != "Все":
                fts_conditions.append("p.shop = ?")
                fts_params.append(shop)

            # Гибкая обработка городов: Астана матчит и "Астана / Казахстан", и пустые (общенациональные)
            if city and city != "Все":
                fts_conditions.append("(p.city = ? OR p.city LIKE ? OR p.city IS NULL OR p.city = 'Все' OR p.city LIKE '%Казахстан%')")
                fts_params.extend([city, f"%{city}%"])

            if category and category != "Все":
                fts_conditions.append("p.category LIKE ?")
                fts_params.append(f"%{category}%")

            if min_price is not None and min_price > 0:
                fts_conditions.append("p.current_price >= ?")
                fts_params.append(int(min_price))

            if max_price is not None and max_price > 0:
                fts_conditions.append("p.current_price <= ?")
                fts_params.append(int(max_price))

            if only_discount:
                fts_conditions.append("(p.first_seen_price > p.current_price OR (p.old_price_on_site IS NOT NULL AND p.old_price_on_site > p.current_price))")

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

    # 2. Fallback на многофакторный LIKE поиск (если FTS недоступен или выдал ошибку)
    if not used_fts or (len(rows) == 0 and mode == "AND"):
        conditions = [active_product_clause()]
        params = []

        if query_clean:
            if mode == "EXACT":
                conditions.append("(LOWER(title) LIKE ? OR LOWER(category) LIKE ?)")
                params.extend([f"%{query_clean}%", f"%{query_clean}%"])
            else:
                token_conds = []
                for t in raw_tokens:
                    syn = BRAND_SYNONYMS.get(t, t)
                    stem = stem_russian_word(t)
                    sub = "(LOWER(title) LIKE ? OR LOWER(title) LIKE ? OR LOWER(category) LIKE ?)"
                    token_conds.append(sub)
                    params.extend([f"%{t}%", f"%{syn}%", f"%{stem}%"])
                
                joiner = " OR " if mode == "OR" else " AND "
                if token_conds:
                    conditions.append(f"({joiner.join(token_conds)})")

        if shop and shop != "Все":
            conditions.append("shop = ?")
            params.append(shop)

        if city and city != "Все":
            conditions.append("(city = ? OR city LIKE ? OR city IS NULL OR city = 'Все' OR city LIKE '%Казахстан%')")
            params.extend([city, f"%{city}%"])

        if category and category != "Все":
            conditions.append("category LIKE ?")
            params.append(f"%{category}%")

        if min_price is not None and min_price > 0:
            conditions.append("current_price >= ?")
            params.append(int(min_price))

        if max_price is not None and max_price > 0:
            conditions.append("current_price <= ?")
            params.append(int(max_price))

        if only_discount:
            conditions.append("(first_seen_price > current_price OR (old_price_on_site IS NOT NULL AND old_price_on_site > current_price))")

        where_sql = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT * FROM products
            WHERE {where_sql} AND current_price > 0
            ORDER BY current_price ASC
            LIMIT ?
        """
        params.append(limit)
        cursor.execute(sql, params)
        like_rows = cursor.fetchall()
        if not rows:
            rows = like_rows

    conn.close()
    results = [dict(r) for r in rows]

    # A merchant category can be mislabeled (e.g. Fitbit under Apple Watch).
    if {'apple', 'watch'}.issubset(set(query_clean.lower().split())):
        results = [r for r in results if re.search(r'\b(?:apple|эппл|эпл)\b', r.get('title', '').lower())
                   and re.search(r'\b(?:watch|вотч)\b', r.get('title', '').lower())]

    # 3. Умная фильтрация чехлов/аксессуаров:
    # Исключаем аксессуары ТОЛЬКО если пользователь сам их явно не искал
    if exclude_accessories and not is_accessory_query(query_clean):
        from detector import is_junk_accessory
        results = [r for r in results if not is_junk_accessory(r.get("title", ""), r.get("category", ""), custom_keywords=junk_keywords)]

    # 4. Исключение минус-слов
    if negative_keywords:
        negs = [nk.strip().lower() for nk in negative_keywords if nk.strip()]
        if negs:
            results = [r for r in results if not any(nk in r.get("title", "").lower() for nk in negs)]

    # 4.0. Аксессуары к искомому товару: «Сумка для ноутбука», «Кронштейн для двух мониторов»
    if product_nouns:
        results = [r for r in results if not _is_accessory_for(r.get("title", ""), product_nouns)]

    # 4.1. Мягкий фильтр по назначению («для игр»): оставляем подходящие модели, если такие нашлись
    if prefer_keywords:
        prefs = [pk.lower() for pk in prefer_keywords if pk]
        preferred = [r for r in results if any(pk in f"{r.get('title', '')} {r.get('category', '')}".lower() for pk in prefs)]
        if preferred:
            results = preferred

    # 5. Сортировка выдачи
    if sort_by == "price_desc":
        results.sort(key=lambda x: x["current_price"], reverse=True)
    elif sort_by == "relevance" and raw_tokens:
        results.sort(key=lambda x: score_relevance(x, query_clean, raw_tokens), reverse=True)
    else:  # price_asc
        results.sort(key=lambda x: x["current_price"], reverse=False)

    return results[:result_limit]

async def search_live_stores(query: str, city: str = "Астана") -> List[Dict[str, Any]]:
    """Живой опрос площадок (Kaspi, shop.kz, 4mobile) с кэшированием."""
    city_name = city or "Астана"
    cache_key = f"{query.strip().lower()}:{city_name.strip().lower()}"
    now_ts = time.time()

    if cache_key in _LIVE_CACHE:
        cached_ts, cached_items = _LIVE_CACHE[cache_key]
        if (now_ts - cached_ts) < SEARCH_CACHE_TTL_SECONDS:
            return cached_items

    all_found = []

    # 1. Kaspi
    try:
        city_config = next((c for c in CITIES_KZ.values() if c["name"] == city_name or c["id"] == city_name), CITIES_KZ["astana"])
        kaspi_city_code = city_config["kaspi_code"]
        kaspi = KaspiScraper(city_code=kaspi_city_code)
        kaspi_results = await kaspi.search(query, max_items=15)
        for item in kaspi_results:
            item["city"] = city_name
            save_or_update_product(item)
            all_found.append(item)
    except Exception as e:
        print(f"[SearchEngine] Ошибка live-поиска в Kaspi: {e}")

    # 2. Белый Ветер (shop.kz)
    try:
        from curl_cffi import requests
        from bs4 import BeautifulSoup
        import urllib.parse

        city_config = next((c for c in CITIES_KZ.values() if c["name"] == city_name or c["id"] == city_name), CITIES_KZ["astana"])
        shopkz_city = city_config["shopkz_city"]
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

    # 3. 4mobile
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

    _LIVE_CACHE[cache_key] = (now_ts, all_found)
    return all_found

async def get_best_price_summary(
    query: str,
    live: bool = False,
    shop: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    only_discount: bool = False,
    exclude_accessories: bool = True,
    match_mode: str = "AND",
    sort_by: str = "price_asc",
    negative_keywords: Optional[List[str]] = None,
    junk_keywords: Optional[List[str]] = None,
    prefer_keywords: Optional[List[str]] = None,
    product_nouns: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Комплексный поиск с агрегацией лучшей цены, экономии, распределением по магазинам и категориям."""
    query_clean = query.strip()
    if not query_clean:
        return {
            "query": "",
            "total_found": 0,
            "best_deal": None,
            "price_stats": None,
            "store_comparison": [],
            "shop_counts": {},
            "items": []
        }

    # 1. Поиск по локальной базе данных
    local_items = search_in_database(
        query_clean,
        shop=shop,
        city=city,
        category=category,
        min_price=min_price,
        max_price=max_price,
        only_discount=only_discount,
        exclude_accessories=exclude_accessories,
        match_mode=match_mode,
        sort_by=sort_by,
        negative_keywords=negative_keywords,
        junk_keywords=junk_keywords,
        prefer_keywords=prefer_keywords,
        product_nouns=product_nouns
    )

    # 2. Опрос внешних площадок при запросе
    if live:
        await search_live_stores(query_clean, city=city or "Астана")
        local_items = search_in_database(
            query_clean,
            shop=shop,
            city=city,
            category=category,
            min_price=min_price,
            max_price=max_price,
            only_discount=only_discount,
            exclude_accessories=exclude_accessories,
            match_mode=match_mode,
            sort_by=sort_by,
            negative_keywords=negative_keywords,
            junk_keywords=junk_keywords,
            prefer_keywords=prefer_keywords,
            product_nouns=product_nouns
        )

    if not local_items:
        return {
            "query": query_clean,
            "total_found": 0,
            "best_deal": None,
            "price_stats": None,
            "store_comparison": [],
            "shop_counts": {},
            "items": []
        }

    prices = [item["current_price"] for item in local_items if item["current_price"] > 0]
    min_p = min(prices)
    max_p = max(prices)
    avg_p = int(sum(prices) / len(prices))

    cheapest_item = min(local_items, key=lambda x: x["current_price"])

    from model_matching import same_model
    comparable = [item for item in local_items
                  if item['shop'] != cheapest_item['shop']
                  and item.get('city') == cheapest_item.get('city')
                  and same_model(cheapest_item['title'], item['title'])]
    comparable_max = max([min_p] + [item['current_price'] for item in comparable])
    savings = comparable_max - min_p
    savings_pct = int(round(savings / comparable_max * 100)) if savings else 0

    # Агрегация по магазинам
    store_map: Dict[str, Dict[str, Any]] = {}
    shop_counts: Dict[str, int] = {}
    for item in local_items:
        s_name = item["shop"]
        shop_counts[s_name] = shop_counts.get(s_name, 0) + 1
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
            "city": item.get("city", ""),
            "diff_from_best": diff,
            "diff_kzt": diff,
            "count": shop_counts.get(s_name, 1)
        })

    formatted_items = []
    for it in local_items:
        it_copy = dict(it)
        it_copy["price"] = it["current_price"]
        it_copy["diff_from_best"] = it["current_price"] - min_p
        it_copy["savings_vs_max"] = 0  # A broad query can contain different models.
        it_copy["old_price"] = it.get("old_price_on_site") or it.get("first_seen_price") or 0
        formatted_items.append(it_copy)

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
            "city": cheapest_item.get("city", ""),
            "savings_vs_max": savings,
            "savings_pct": savings_pct,
            "max_market_price": comparable_max
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
        "shop_counts": shop_counts,
        "items": formatted_items
    }
