from database import active_product_clause
import re
import sqlite3
import asyncio
import time
from typing import List, Dict, Any, Optional, Tuple
from config import DB_PATH, SEARCH_CACHE_TTL_SECONDS, load_settings
from bounded_cache import BoundedTTLCache
from database import save_or_update_products_batch
from scrapers.kaspi import KaspiScraper

# In-memory кэш для внешних живых запросов: { "query:city": (timestamp, [items]) }
# Ограничен по числу записей и сроку: уникальные live-запросы не копятся до перезапуска (M03)
_LIVE_CACHE = BoundedTTLCache(maxsize=500, ttl=SEARCH_CACHE_TTL_SECONDS)

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

from scrapers.base import parse_price

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
    # Порядок задаётся до LIMIT: иначе «дороже»/«релевантнее» сортировали только самых дешёвых (M06)
    order_sql = "current_price DESC" if sort_by == "price_desc" else "current_price ASC"
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
                ORDER BY {"bm25(products_fts)" if sort_by == "relevance" else "p." + order_sql}
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
            ORDER BY {order_sql}
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

def determine_category_and_master(title: str, query: str = "", raw_category: str = "") -> Tuple[str, Optional[str]]:
    """Определяет понятное название категории и ее master_id на основе названия товара, поискового запроса или исходной категории."""
    text = f"{raw_category} {query} {title}".lower()

    if any(k in text for k in ["видеокарт", "geforce", "radeon", "rtx ", "gtx ", "rx 7", "rx 6"]):
        return "Видеокарты", "pc_components"
    if any(k in text for k in ["процессор", "ryzen", "core i3", "core i5", "core i7", "core i9", "intel core"]):
        return "Процессоры", "pc_components"
    if any(k in text for k in ["материнск", "motherboard"]):
        return "Материнские платы", "pc_components"
    if any(k in text for k in ["оперативн", "памят", "ddr4", "ddr5", "dimm"]):
        return "Оперативная память", "pc_components"
    if any(k in text for k in ["ssd", "накопител", "жесткий диск", "hdd", "nvme"]):
        return "Накопители SSD/HDD", "pc_components"
    if any(k in text for k in ["блок питания", "power supply"]):
        return "Блоки питания", "pc_components"
    if any(k in text for k in ["корпус", "case"]):
        return "Корпуса для ПК", "pc_components"
    if any(k in text for k in ["кулер", "охлажден", "водяное охлаждение"]):
        return "Охлаждение ПК", "pc_components"

    if any(k in text for k in ["ноутбук", "laptop", "macbook", "ultrabook", "ультрабук"]):
        return "Ноутбуки", "laptops"
    if any(k in text for k in ["моноблок", "системный блок", "компьютер"]) or re.search(r'\b(пк|pc)\b', text):
        return "Компьютеры и моноблоки", "laptops"

    if any(k in text for k in ["монитор", "дисплей"]):
        return "Мониторы", "monitors"
    if any(k in text for k in ["телевизор", "тв", "oled", "qled"]):
        return "Телевизоры", "tvs"
    if any(k in text for k in ["проектор"]):
        return "Проекторы", "tvs"

    if any(k in text for k in ["наушник", "гарнитур", "airpods", "tws"]):
        return "Наушники", "audio"
    if any(k in text for k in ["колонк", "акустик", "саундбар"]):
        return "Акустика и колонки", "audio"

    if any(k in text for k in ["пылесос", "робот-пылесос"]):
        return "Пылесосы", "appliances_small"
    if any(k in text for k in ["кофемашин", "кофеварк"]):
        return "Кофемашины", "appliances_small"
    if any(k in text for k in ["микроволнов", "свч"]):
        return "Микроволновые печи", "appliances_small"
    if any(k in text for k in ["чайник", "блендер", "утюг", "фен"]):
        return "Мелкая бытовая техника", "appliances_small"

    if any(k in text for k in ["холодильник"]):
        return "Холодильники", "appliances_large"
    if any(k in text for k in ["стиральн"]) and "порошок" not in text:
        return "Стиральные машины", "appliances_large"
    if any(k in text for k in ["кондиционер", "сплит"]):
        return "Кондиционеры", "appliances_large"
    if any(k in text for k in ["посудомоечн"]):
        return "Посудомоечные машины", "appliances_large"
    if any(k in text for k in ["плита", "варочн", "духов"]):
        return "Плиты и духовки", "appliances_large"

    if any(k in text for k in ["playstation", "ps5", "ps4", "xbox", "nintendo", "приставк", "геймпад"]):
        return "Игровые консоли", "consoles"

    if any(k in text for k in ["принтер", "мфу", "сканер"]):
        return "Оргтехника", "office_network"
    if any(k in text for k in ["роутер", "маршрутизатор", "wi-fi"]):
        return "Сетевое оборудование", "office_network"

    if any(k in text for k in ["планшет", "ipad", "tablet"]):
        return "Планшеты", "tablets_watches"
    if any(k in text for k in ["часы", "смарт-часы", "watch", "браслет"]):
        return "Смарт-часы и браслеты", "tablets_watches"

    if any(k in text for k in ["смартфон", "телефон", "iphone", "айфон", "galaxy", "xiaomi", "redmi", "poco", "pixel"]):
        return "Смартфоны", "smartphones"

    if any(k in text for k in ["перфоратор", "дрель", "шуруповерт", "шуруповёрт", "болгарк", "ушм", "лобзик", "пила", "молоток", "инструмент", "краск", "сантехник", "крепеж"]):
        return "Инструменты и ремонт", "diy"

    if any(k in text for k in ["носки", "чулк", "колготк", "одежд", "обув", "кроссовк", "ботинок", "ботинк", "футболк", "рубашк", "брюк", "джинс", "плать", "юбк", "куртк", "пальто", "белье", "бельё", "трусы", "майк", "джемпер", "свитер", "толстовк", "худи", "шорт"]):
        return "Одежда и обувь", "clothes"

    if any(k in text for k in ["наполнител", "кошач", "собач", "зоо", "корм для ко", "корм для соб", "для кошек", "для собак", "для кота", "для щенк", "для котят", "ошейник", "лоток для", "туалет для животн"]):
        return "Зоотовары", "pets"

    if any(k in text for k in ["емкост", "хранени", "посуд", "кастрюл", "сковород", "тарелк", "кружк", "бокал", "контейнер для", "ланчбокс", "хлебниц", "мебел", "стул", "стол", "шкаф", "диван", "кресл", "комод", "стеллаж", "полк", "матрас", "подушк", "одеял", "постельн", "штор"]):
        return "Дом, мебель и уют", "home_furniture"

    if any(k in text for k in ["косметик", "макияж", "помад", "тушь", "крем для", "сыворотк", "парфюм", "духи", "туалетная вода", "шампун", "бальзам", "дезодорант", "зубная паст", "щетк", "бритв", "брить"]):
        return "Красота и здоровье", "beauty_health"

    if any(k in text for k in ["мед", "мёд", "варень", "джем", "кофе", "чай", "шоколад", "масло", "крупа", "макарон", "сахар", "молоко", "сыр", "колбас", "бакале", "продукт", "конфет", "печень"]):
        return "Продукты и бакалея", "grocery"

    if any(k in text for k in ["порошок", "стирк", "ariel", "tide", "fairy", "мыло", "паста", "салфетк", "химия", "уборк"]):
        return "Бытовые товары и химия", "household"

    if any(k in text for k in ["акци", "распродаж", "скидк", "ликвидац", "outlet", "sale"]):
        return "Акции и распродажи", "actions"

    if raw_category and not raw_category.startswith("Поиск:"):
        return raw_category, None
    if query:
        return query.strip().capitalize(), None
    return "Каталог", None

def _close_scraper(scraper) -> None:
    close = getattr(scraper, "close", None)
    if close:
        close()


def _record_search(source: str, query: str, started: float, outcome: str, results: int,
                   **extra) -> None:
    """Событие поиска: форма запроса без текста (P06), число результатов, длительность, исход (P01)."""
    try:
        from telemetry import (telemetry, query_shape, EVENT_SEARCH_QUERY, SEVERITY_INFO, SEVERITY_ERROR,
                               COMPONENT_SEARCH, MAX_SEARCH_EVENTS_PER_MINUTE)
        telemetry.record_event(
            EVENT_SEARCH_QUERY, SEVERITY_ERROR if outcome == "error" else SEVERITY_INFO, COMPONENT_SEARCH,
            f"Поиск {source}: {outcome}, {results} результатов",
            data={"source": source, "outcome": outcome, "results": results,
                  "duration_ms": round((time.monotonic() - started) * 1000.0, 1), **query_shape(query), **extra},
            throttle_key=f"search:{source}", throttle_per_minute=MAX_SEARCH_EVENTS_PER_MINUTE)
    except Exception:
        pass


async def search_live_stores(query: str, city: str = "Астана") -> List[Dict[str, Any]]:
    started = time.monotonic()
    outcome, items, cached = "error", [], False
    try:
        items, cached = await _search_live_stores(query, city)
        outcome = "found" if items else "not_found"
        return items
    finally:
        from offer_identity import city_config
        _record_search("live", query, started, outcome, len(items), requested_city=city,
                       city=city_config(city)["id"], cached=cached)


async def _search_live_stores(query: str, city: str = "Астана"):
    """Живой опрос площадок (Kaspi, 4mobile, Forte Market) с кэшированием.

    Город у предложения — только фактически опрошенный: «Все»/неизвестный город опрашивает
    Астану и так и подписывается; 4mobile продаёт из Астаны; Forte без цены города — «Казахстан».
    Белый Ветер не опрашивается: поиск на сайте отрисовывается скриптом, а весь каталог
    приходит из официальной выгрузки при обходе.
    """
    from offer_identity import assign_offer_ids, city_config
    polled_city = city_config(city)
    city_name = polled_city["name"]
    enabled = load_settings().get("enabled_shops", {})
    active_shops = tuple(k for k in ("kaspi", "fourmobile", "fortemarket") if enabled.get(k, True))
    cache_key = (query.strip().lower(), city_name.strip().lower(), active_shops)
    now_ts = time.time()

    if cache_key in _LIVE_CACHE:
        cached_ts, cached_items = _LIVE_CACHE[cache_key]
        if (now_ts - cached_ts) < SEARCH_CACHE_TTL_SECONDS:
            return cached_items, True

    all_found = []

    async def _store(items):
        items = list(assign_offer_ids(items))
        for item in items:
            cat_name, _ = determine_category_and_master(item.get("title", ""), query, item.get("category", ""))
            if cat_name:
                item["category"] = cat_name
        # Одна транзакция вне event loop вместо записи по товару в основном потоке (M06)
        await asyncio.to_thread(save_or_update_products_batch, items)
        all_found.extend(items)

    # 1. Kaspi: цены выбранного города (код города Kaspi)
    if "kaspi" in active_shops:
        try:
            kaspi = KaspiScraper(city_code=polled_city["kaspi_code"])
            try:
                await _store(await kaspi.search(query, max_items=15))
            finally:
                _close_scraper(kaspi)
        except Exception as e:
            print(f"[SearchEngine] Ошибка live-поиска в Kaspi: {type(e).__name__}")

    # 2. 4mobile: единый магазин, город задаёт сам адаптер
    if "fourmobile" in active_shops:
        try:
            from scrapers.fourmobile import FourMobileScraper
            four_mobile = FourMobileScraper()
            try:
                await _store(await four_mobile.search_live(query))
            finally:
                _close_scraper(four_mobile)
        except Exception as e:
            print(f"[SearchEngine] Ошибка live-поиска в 4mobile: {type(e).__name__}")

    # 3. Forte Market: цена города или общая по Казахстану (метку ставит адаптер)
    if "fortemarket" in active_shops:
        try:
            from scrapers.fortemarket import ForteMarketScraper
            forte = ForteMarketScraper(city=city_name)
            try:
                await _store(await forte.search_live(query, city=city_name))
            finally:
                _close_scraper(forte)
        except Exception as e:
            print(f"[SearchEngine] Ошибка live-поиска в Forte Market: {type(e).__name__}")

    # Авто-регистрация категории для ротации в волнах обновлений
    if all_found:
        from database import save_tracked_category
        seen_cats = set()
        for it in all_found:
            c_name, m_id = determine_category_and_master(it.get("title", ""), query, it.get("category", ""))
            if c_name and c_name not in seen_cats:
                seen_cats.add(c_name)
                try:
                    save_tracked_category(name=c_name, query=query.strip(), master_category=m_id)
                except Exception:
                    pass

    _LIVE_CACHE[cache_key] = (now_ts, all_found)
    return all_found, False

async def get_best_price_summary(query: str, live: bool = False, **kwargs) -> Dict[str, Any]:
    """Комплексный поиск (см. _get_best_price_summary) с событием телеметрии поиска."""
    started = time.monotonic()
    outcome, total = "error", 0
    try:
        result = await _get_best_price_summary(query, live=live, **kwargs)
        total = int(result.get("total_found") or 0)
        outcome = "found" if total else "not_found"
        return result
    finally:
        defaults = {"only_discount": False, "exclude_accessories": True, "match_mode": "AND", "sort_by": "price_asc"}
        # Только имена применённых фильтров, без значений (ключевые слова пользователя — личные настройки)
        filters = sorted(k for k, v in kwargs.items()
                         if (k in defaults and v != defaults[k]) or (k not in defaults and v not in (None, "", [])))
        _record_search("summary", query, started, outcome, total, live=live,
                       city=kwargs.get("city"), filters=filters)


async def _get_best_price_summary(
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

    # Refresh is explicit. The HTTP entrypoint authorizes and limits live=True;
    # empty/stale local results must never silently start outbound requests.
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

    # Свежесть каждого предложения (P02): устаревшие (Stale) показываются с бейджем, но не дают лучшую цену,
    # статистику и сравнение магазинов. Если свежих нет — лучшей цены нет (all_stale).
    import data_quality
    for item in local_items:
        data_quality.annotate(item)
    priced = [item for item in local_items if item["current_price"] > 0]
    pool = [item for item in priced if item["freshness"] != data_quality.STALE]
    stale_count = len(priced) - len(pool)
    basis = pool or priced
    prices = [item["current_price"] for item in basis]
    min_p = min(prices)
    max_p = max(prices)
    avg_p = int(sum(prices) / len(prices))

    cheapest_item = min(basis, key=lambda x: x["current_price"])

    from model_matching import same_model
    comparable = [item for item in basis
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
    for item in basis:
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
            "city": item.get("city", ""),
            "diff_from_best": diff,
            "diff_kzt": diff,
            "count": shop_counts.get(s_name, 1),
            "freshness": item["freshness"],
            "last_seen_at": item["last_seen_at"],
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
        "stale_count": stale_count,
        "all_stale": not pool,
        "best_deal": None if not pool else {
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
            "max_market_price": comparable_max,
            "freshness": cheapest_item["freshness"],
            "last_seen_at": cheapest_item["last_seen_at"],
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
