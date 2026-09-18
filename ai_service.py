"""
ai_service.py - Универсальный сервис искусственного интеллекта для KZ Price Hunter.

Реализует:
1. Асинхронное взаимодействие с Google Gemini REST API (gemini-2.5-flash / gemini-1.5-flash) и OpenAI API без сторонних тяжелых SDK.
2. Интеллектуальный разбор сложных человеческих запросов (Natural Language Query Parsing) в структурированные фильтры базы данных.
3. Определение необходимости AI-парсинга (эвристика разговорных запросов).
4. Встроенное кэширование ответов AI в оперативной памяти (LRU Cache) для мгновенного отклика и экономии квоты API.
5. Защита от зависаний (таймаут 3.5 с) и прозрачный откат к стандартному FTS5-поиску при отсутствии ключа или сбое сети.
"""

import os
import re
from model_matching import valid_ai_canonical_key
import json
import time
import asyncio
from typing import Optional, Dict, Any, List
import aiohttp

import config

# Кэш в памяти: query_key -> (timestamp, parsed_dict)
_QUERY_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 3600 * 4  # 4 часа

# Список стандартных категорий каталога для точной привязки AI
CATALOG_CATEGORIES = [
    "Смартфоны", "Ноутбуки", "Видеокарты", "Мониторы", "Наушники",
    "Планшеты", "Телевизоры", "Игровые приставки", "Процессоры",
    "Материнские платы", "SSD", "Корпуса", "Блоки питания",
    "Клавиатуры", "Мыши", "Смарт-часы", "Пылесосы",
    "Стиральные машины", "Холодильники", "Кондиционеры"
]

# Регулярные выражения для выявления разговорных запросов
_NL_PATTERNS = [
    re.compile(r'\b(до|от|дешевле|дороже|около|примерно|в пределах)\s+\d+', re.IGNORECASE),
    re.compile(r'\b\d+\s*(к|k|тыс|тысяч|млн|тенге|тг|₸)\b', re.IGNORECASE),
    re.compile(r'\b(посоветуй|подбери|найди|какой|порекомендуй|покажи|выбери)\b', re.IGNORECASE),
    re.compile(r'\b(для учебы|для работы|для игр|для гейминга|для офиса|для дома|для подарка|в подарок)\b', re.IGNORECASE),
    re.compile(r'\b(хороший|лучший|недорогой|дешевый|бюджетный|мощный|тихий|компактный|легкий|тонкий)\b', re.IGNORECASE),
    re.compile(r'\b(со скидкой|на скидках|по акции|с акцией|с выгодой|подешевле|максимальная скидка)\b', re.IGNORECASE),
    re.compile(r'\b(с хорошей камерой|с мощным аккумулятором|с большой батареей|с олед|oled)\b', re.IGNORECASE),
]

def should_use_ai_parsing(query: str) -> bool:
    """
    Определяет, содержит ли поисковый запрос естественный язык или условия цены/назначения,
    требующие интеллектуального разбора.
    """
    if not query or len(query.strip()) < 3:
        return False
    
    q = query.strip().lower()
    for pattern in _NL_PATTERNS:
        if pattern.search(q):
            return True

    # Запрос из 4 и более слов чаще всего является разговорным описанием
    words = [w for w in re.split(r'\s+', q) if len(w) > 1]
    if len(words) >= 4 and not any(w in ("rtx", "gtx", "core", "ryzen", "pro", "max", "ultra") for w in words):
        return True

    return False


def _get_api_credentials() -> Dict[str, Any]:
    """Извлекает ключи API из переменных окружения или системных настроек settings.json."""
    creds = dict(config.get_ai_config())
    selected = creds.get("ai_provider", "auto")
    if selected == "gemini":
        creds["openai_api_key"] = ""
    elif selected == "openai":
        creds["gemini_api_key"] = ""
    return creds


async def parse_natural_query(query: str, current_city: str = "Все", force: bool = False,
                              dialog_context: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Анализирует произвольный запрос пользователя с помощью Gemini Flash / OpenAI
    и возвращает структурированный фильтр для поиска по базе товаров.

    Возвращает dict с полями:
    - clean_query: очищенный поисковый запрос (например: "ноутбук ASUS")
    - category: стандартная категория или None
    - brand: бренд или None
    - min_price: минимальная цена (int) или None
    - max_price: максимальная цена (int) или None
    - only_discount: bool
    - keywords: list[str]
    - negative_keywords: list[str]
    - sort: 'price_asc' | 'price_desc' | 'savings_desc'
    - explanation: понятное пояснение от AI на русском языке
    """
    clean_q = query.strip()
    if not clean_q:
        return None

    creds = _get_api_credentials()
    if not creds.get("has_ai") or not creds.get("ai_search_enabled", True):
        return None
    # Model/provider changes take effect immediately, without stale query responses.
    cache_key = (clean_q.lower(), current_city, creds.get("ai_provider", "auto"),
                 creds.get("gemini_model"), creds.get("openai_model"), dialog_context or "")
    now = time.time()
    if cache_key in _QUERY_CACHE:
        ts, cached_val = _QUERY_CACHE[cache_key]
        if now - ts < _CACHE_TTL_SECONDS:
            return cached_val

    creds = _get_api_credentials()
    if not creds.get("has_ai") or not creds.get("ai_search_enabled", True):
        return None

    gemini_key = creds.get("gemini_api_key")
    openai_key = creds.get("openai_api_key")
    openai_base = creds.get("openai_api_base", "https://api.openai.com/v1")

    prompt = f"""
Ты — высокоточный аналитик поисковых запросов в казахстанском агрегаторе цен электроники и бытовой техники (Kaspi, DNS, Белый Ветер, Технодом и др.).
Твоя задача — разобрать запрос пользователя и вернуть СТРОГО JSON-объект с параметрами фильтрации для базы данных.

Доступные категории: {', '.join(CATALOG_CATEGORIES)}.

Запрос пользователя: "{clean_q}"
Текущий город: "{current_city}"
{_dialog_block(dialog_context)}
Правила разбора:
1. "clean_query": главное ключевое слово товара без цен и слов вежливости (например: "ноутбук", "робот-пылесос", "iPhone 15", "видеокарта RTX 4060").
2. "category": выбери ТОЛЬКО одну из доступных категорий выше, если она однозначно ясна. Если не уверена, оставь null.
3. "brand": бренд на английском, если указан (Apple, Samsung, ASUS, Xiaomi, Sony, LG, TCL и т.д.) или null.
4. "min_price" и "max_price": преобразуй в целые числа в тенге (₸). "300к", "300 тыс", "300000" -> 300000. Если не указано -> null.
5. "only_discount": true, если пользователь ищет "со скидкой", "по акции", "выгодно", "дешевле", "распродажа". Иначе false.
6. "keywords": 2-4 наиболее релевантных слова/серии для дополнения поиска (например, для "легкий ноут" -> ["Air", "Zenbook", "VivoBook", "Swift", "IdeaPad"]).
7. "negative_keywords": слова для исключения (например: ["чехол", "стекло", "копия", "ремешок"]).
8. "sort": "price_asc" (по умолчанию), "price_desc" (если ищет топовый/самый дорогой) или "savings_desc" (если ищет максимальную выгоду).
9. "explanation": краткое понятное описание для пользователя, что именно понял AI (на русском, до 10-12 слов).

Формат ответа — ТОЛЬКО валидный JSON:
{{
  "clean_query": "...",
  "category": "...",
  "brand": "...",
  "min_price": null,
  "max_price": 300000,
  "only_discount": false,
  "keywords": ["..."],
  "negative_keywords": ["..."],
  "sort": "price_asc",
  "explanation": "..."
}}
"""

    parsed_result = None

    # 1. Попытка через Google Gemini REST API (основной быстрый бесплатный провайдер)
    if gemini_key:
        parsed_result = await call_gemini_api(prompt, gemini_key, timeout=SEARCH_TIMEOUT_SECONDS)

    # 2. Fallback на OpenAI-совместимый API, если Gemini нет или не ответил
    if not parsed_result and openai_key:
        parsed_result = await call_openai_api(prompt, openai_key, openai_base, timeout=SEARCH_TIMEOUT_SECONDS)

    if parsed_result:
        # Валидация и очистка полей
        parsed_result = _sanitize_parsed_result(parsed_result, clean_q)
        _QUERY_CACHE[cache_key] = (now, parsed_result)
        return parsed_result

    return None


# Shared process budget covers web search, consultant, bot and scanner.
from auth import RateLimiter
_provider_limiter = RateLimiter(max_calls=60, period=60)
_provider_active = 0

# Фоновая нормализация названий получает только часть общего бюджета, чтобы поиск
# и консультант пользователей не упирались в лимит во время обхода магазинов
SCAN_AI_CALLS_PER_MINUTE = 20
_scan_limiter = RateLimiter(max_calls=SCAN_AI_CALLS_PER_MINUTE, period=60)
# Одно из трех одновременных обращений всегда остается пользователям
SCAN_MAX_ACTIVE = 2

# Таймауты ответа: пользователь в поиске ждет недолго, фоновым задачам можно дольше
SEARCH_TIMEOUT_SECONDS = 10
DEFAULT_TIMEOUT_SECONDS = 30

# Названия, для которых AI не вернул ключ (или вызов не удался), повторно не отправляются столько дней
AI_RETRY_DAYS = 7


async def _limited_provider_call(fn, *args, scan: bool = False):
    global _provider_active
    if scan and (_provider_active >= SCAN_MAX_ACTIVE or _scan_limiter.retry_after("scan")):
        return None
    if _provider_active >= 3 or _provider_limiter.retry_after("shared"):
        return None
    _provider_active += 1
    try:
        return await fn(*args)
    finally:
        _provider_active -= 1


async def call_gemini_api(prompt: str, api_key: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, scan: bool = False):
    return await _limited_provider_call(_call_gemini_api, prompt, api_key, timeout, scan=scan)


async def call_openai_api(prompt: str, api_key: str, api_base: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, scan: bool = False):
    return await _limited_provider_call(_call_openai_api, prompt, api_key, api_base, timeout, scan=scan)


async def _call_gemini_api(prompt: str, api_key: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Optional[Dict[str, Any]]:
    """Вызов Gemini Flash REST API через aiohttp."""
    models_to_try = [config.get_ai_config()["gemini_model"]]
    
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1
            }
        }

        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(10, timeout_seconds))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                text = parts[0].get("text", "").strip()
                                return _extract_json_from_text(text)
                    elif resp.status in (400, 404):
                        # Модель может быть недоступна в этой версии, пробуем следующую
                        continue
                    else:
                        print(f"[AI Service] Ошибка Gemini API ({model}, HTTP {resp.status})")
                        return None
        except Exception as e:
            print(f"[AI Service] Исключение при вызове Gemini API ({model}): {type(e).__name__}")
            continue

    return None


async def _call_openai_api(prompt: str, api_key: str, api_base: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Optional[Dict[str, Any]]:
    """Вызов OpenAI-совместимого API через aiohttp."""
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = {
        "model": config.get_ai_config()["openai_model"],
        "messages": [
            {"role": "system", "content": "You are a helpful JSON parser for e-commerce search queries."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(10, timeout_seconds))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        return _extract_json_from_text(content)
                else:
                    print(f"[AI Service] Ошибка OpenAI API (HTTP {resp.status})")
    except Exception as e:
        print(f"[AI Service] Ошибка OpenAI API: {type(e).__name__} (лимит ответа {timeout_seconds:g} с)")

    return None


def clean_ai_json_response(text: str) -> str:
    """Очищает текстовый ответ LLM от markdown-разметки (```json ... ```)."""
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Извлекает и парсит JSON из текстового ответа LLM."""
    if not text:
        return None
    cleaned = clean_ai_json_response(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _sanitize_parsed_result(data: Dict[str, Any], original_query: str) -> Dict[str, Any]:
    """Проверяет типы и нормализует поля структурированного ответа AI."""
    clean_q = str(data.get("clean_query") or original_query).strip()
    category = data.get("category")
    if category and category not in CATALOG_CATEGORIES:
        category = None

    brand = str(data.get("brand") or "").strip() or None

    min_p = data.get("min_price")
    max_p = data.get("max_price")
    try:
        min_p = int(min_p) if min_p and int(min_p) > 0 else None
    except (ValueError, TypeError):
        min_p = None

    try:
        max_p = int(max_p) if max_p and int(max_p) > 0 else None
    except (ValueError, TypeError):
        max_p = None

    keywords = data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    elif not isinstance(keywords, list):
        keywords = []

    neg_words = data.get("negative_keywords") or []
    if isinstance(neg_words, str):
        neg_words = [k.strip() for k in neg_words.split(",") if k.strip()]
    elif not isinstance(neg_words, list):
        neg_words = []

    sort = data.get("sort")
    if sort not in ("price_asc", "price_desc", "savings_desc"):
        sort = "price_asc"

    explanation = str(data.get("explanation") or "").strip()
    if not explanation:
        parts = []
        if category:
            parts.append(category)
        if brand:
            parts.append(f"бренд {brand}")
        if max_p:
            parts.append(f"до {max_p:,} ₸".replace(",", " "))
        explanation = "Поиск: " + ", ".join(parts) if parts else "Умный подбор по критериям"

    return {
        "clean_query": clean_q,
        "category": category,
        "brand": brand,
        "min_price": min_p,
        "max_price": max_p,
        "only_discount": bool(data.get("only_discount", False)),
        "keywords": keywords[:5],
        "negative_keywords": neg_words[:5],
        "sort": sort,
        "explanation": explanation
    }


# Сколько истории учитывать и насколько сокращать реплики
HISTORY_MAX_TURNS = 8
HISTORY_TURN_CHARS = 1500
PARSE_CONTEXT_TURN_CHARS = 400


def _dialog_block(dialog_context: Optional[str]) -> str:
    if not dialog_context:
        return ""
    return f"""
Предыдущий диалог (последние реплики, включая показанные товары):
{dialog_context}

Если текущий запрос — уточнение предыдущего («а подешевле», «а от Samsung», «а с 256 ГБ», «а что-то тише»),
сформируй САМОДОСТАТОЧНЫЙ запрос: сохрани товар, категорию и ограничения из диалога и примени изменения
из текущего сообщения. «Подешевле» — max_price ниже цен уже показанных товаров. Если текущий запрос —
новая тема, диалог не учитывай.
"""


def normalize_history(history: Any) -> List[Dict[str, str]]:
    """Последние реплики диалога в едином виде: role user/model, текст не длиннее HISTORY_TURN_CHARS."""
    turns = []
    for turn in (history or [])[-HISTORY_MAX_TURNS:]:
        if not isinstance(turn, dict) or not isinstance(turn.get("content"), str):
            continue
        role = "user" if turn.get("role") == "user" else "model"
        content = turn["content"].strip()
        if content:
            turns.append({"role": role, "content": content[:HISTORY_TURN_CHARS]})
    return turns


def history_entry_for_answer(answer: str, products: List[Dict[str, Any]]) -> str:
    """Реплика консультанта для истории: сокращенный ответ + показанные товары (для «первый», «сравни их»)."""
    text = (answer or "").strip()
    lines = []
    for i, p in enumerate(products[:6], 1):
        price = f"{int(p.get('price') or 0):,} ₸".replace(",", " ")
        lines.append(f"{i}) {p.get('title', '')} — {price} ({p.get('shop', '')})")
    tail = ("\nПоказанные товары:\n" + "\n".join(lines)) if lines else ""
    budget = HISTORY_TURN_CHARS - len(tail)
    if len(text) > budget:
        text = text[:max(0, budget - 1)].rstrip() + "…"
    return text + tail


def _format_products_for_ui(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Форматирует найденные товары в чистую структуру для карточек в Web и Telegram."""
    formatted = []
    for it in items:
        p = it.get("current_price", it.get("price", 0))
        old_p = it.get("old_price_on_site") or it.get("first_seen_price") or 0
        drop_pct = 0
        if old_p > p and p > 0:
            drop_pct = int(round((1 - p / old_p) * 100))
        formatted.append({
            "id": str(it.get("id", "")),
            "shop": it.get("shop", "Магазин"),
            "city": it.get("city", "Астана"),
            "title": it.get("title", ""),
            "category": it.get("category", ""),
            "price": p,
            "old_price": old_p if drop_pct > 0 else 0,
            "drop_pct": drop_pct,
            "url": it.get("url", "#"),
            "image_url": it.get("image_url", "")
        })
    return formatted


async def ask_ai_consultant(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    city: str = "Все"
) -> Dict[str, Any]:
    """
    Интерактивный AI-консультант по покупкам (RAG) на базе Gemini Flash / OpenAI:
    1. Распознает потребность пользователя и ищет в локальной базе 17 магазинов лучшие товары.
    2. Инжектирует актуальные цены, скидки и магазины в контекст LLM.
    3. Генерирует аргументированный ответ эксперта с подборкой конкретных товаров.
    """
    clean_msg = message.strip()
    if not clean_msg:
        return {
            "answer": "Пожалуйста, задайте вопрос о товаре или покупке (например: *«Посоветуй игровой ноутбук до 350к»* или *«Какой iPhone сейчас выгоднее купить?»*).",
            "products": [],
            "suggested_questions": [
                "Подбери игровой ноутбук до 400 000 ₸",
                "Какой iPhone сейчас выгоднее брать?",
                "Посоветуй хороший телевизор 55 дюймов",
                "Что лучше: RTX 4060 или RX 7600?"
            ]
        }

    creds = _get_api_credentials()
    if not creds.get("has_ai") or not creds.get("ai_search_enabled", True):
        # Fallback без ключа: ищем напрямую в базе и даем простой ответ
        from search_engine import search_in_database
        found = search_in_database(clean_msg, city=city)
        return {
            "answer": (
                "⚠️ **AI-сервис не настроен** (не задан `GEMINI_API_KEY` в Настройках).\n\n"
                "Вот что нашлось в базе данных по вашему запросу:"
                if found else
                "⚠️ **AI-сервис не настроен**. Перейдите в раздел **Настройки** и укажите бесплатный Google Gemini API Key."
            ),
            "products": _format_products_for_ui(found[:6]),
            "suggested_questions": []
        }

    turns = normalize_history(history)
    parse_context = "\n".join(
        f"{'Пользователь' if t['role'] == 'user' else 'Консультант'}: {t['content'][:PARSE_CONTEXT_TURN_CHARS]}"
        for t in turns
    ) or None

    # 1. Извлекаем критерии поиска; уточняющий вопрос разбирается с учетом предыдущего диалога
    parsed = await parse_natural_query(clean_msg, current_city=city, force=True, dialog_context=parse_context)
    search_query = parsed.get("clean_query", clean_msg) if parsed else clean_msg
    cat = parsed.get("category") if parsed else None
    min_p = parsed.get("min_price") if parsed else None
    max_p = parsed.get("max_price") if parsed else None
    only_disc = parsed.get("only_discount", False) if parsed else False

    # 2. Выборка товаров из базы данных
    from search_engine import search_in_database
    db_items = search_in_database(
        query=search_query,
        city=city,
        category=cat or "Все",
        min_price=min_p,
        max_price=max_p,
        only_discount=only_disc,
        sort_by="price_asc"
    )

    if not db_items:
        return {"answer": "По вашим условиям товары не найдены. Попробуйте изменить бюджет, город или категорию.",
                "products": [], "suggested_questions": [], "query_used": search_query}

    top_items = db_items[:12]

    # 3. Подготовка контекста товаров для промпта
    context_lines = []
    for idx, it in enumerate(top_items, 1):
        price_val = it.get('current_price', 0)
        price_str = f"{price_val:,} ₸".replace(',', ' ')
        old_price = it.get('old_price_on_site') or it.get('first_seen_price') or 0
        discount_str = ""
        if old_price > price_val:
            pct = int((1 - price_val / old_price) * 100)
            discount_str = f" (Скидка -{pct}%, была {old_price:,} ₸)".replace(',', ' ')
        context_lines.append(
            f"[{idx}] ID: {it['id']} | Товар: {it['title']} | Магазин: {it['shop']} | Город: {it.get('city', 'Астана')} | Цена: {price_str}{discount_str}"
        )
    catalog_context = "\n".join(context_lines) if context_lines else "В базе данных пока нет точных совпадений по этому запросу."

    # 4. История диалога (если передана)
    history_lines = [
        f"{'Пользователь' if t['role'] == 'user' else 'Консультант'}: {t['content']}" for t in turns
    ]
    dialog_context = "\n".join(history_lines) if history_lines else "Диалог только начат."

    # 5. Формирование промпта консультанта
    prompt = f"""
Ты — персональный AI-консультант и эксперт по электронике сервиса KZ Price Hunter (Казахстан).
Твоя цель — помочь пользователю выбрать оптимальную технику, сравнить модели, указать на подводные камни и посоветовать, где купить выгоднее всего.

АКТУАЛЬНЫЕ ДАННЫЕ ИЗ МАГАЗИНОВ КАЗАХСТАНА (Kaspi, DNS, Белый Ветер, Технодом, Мечта, Sulpak и др.):
{catalog_context}

ПРЕДЫДУЩИЙ ДИАЛОГ:
{dialog_context}

ТЕКУЩИЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ: "{clean_msg}"
ГОРОД ПОЛЬЗОВАТЕЛЯ: "{city}"

ТРЕБОВАНИЯ К ОТВЕТУ:
0. Это продолжение одного разговора. Если вопрос уточняет предыдущий («он», «первый вариант», «сравни их», «а подешевле»),
   отвечай в контексте ПРЕДЫДУЩЕГО ДИАЛОГА и показанных там товаров, не начинай тему заново.
1. Отвечай дружелюбно, профессионально и по делу. Используй Markdown (жирный шрифт, списки, выделения).
2. Опирайся на реальные товары и цены из списка выше: называй точные цены в тенге (₸) и конкретный магазин с лучшим предложением.
3. Если пользователь выбирает между несколькими устройствами, кратко сравни плюсы и минусы каждого.
4. В поле "recommended_product_ids" укажи ID от 1 до 5 лучших товаров из списка выше, которые ты рекомендуешь рассмотреть.
5. В поле "suggested_questions" предложи 2-3 логичных коротких вопроса, которые пользователь может задать дальше.

СТРОГО ОТВЕТЬ В ФОРМАТЕ JSON:
{{
  "answer": "Подробный структурированный ответ консультанта в Markdown...",
  "recommended_product_ids": ["...", "..."],
  "suggested_questions": ["...", "..."]
}}
"""

    gemini_key = creds.get("gemini_api_key")
    openai_key = creds.get("openai_api_key")
    openai_base = creds.get("openai_api_base", "https://api.openai.com/v1")

    res = None
    if gemini_key:
        res = await call_gemini_api(prompt, gemini_key)
    if not res and openai_key:
        res = await call_openai_api(prompt, openai_key, openai_base)

    if res and isinstance(res, dict) and "answer" in res:
        rec_ids = set(str(x) for x in (res.get("recommended_product_ids") or []))
        rec_products = [it for it in top_items if str(it["id"]) in rec_ids]
        if not rec_products:
            rec_products = top_items[:4]
        
        ui_products = _format_products_for_ui(rec_products)
        return {
            "answer": res["answer"],
            "history_turn": history_entry_for_answer(res["answer"], ui_products),
            "products": ui_products,
            "suggested_questions": res.get("suggested_questions") or [
                "Где сейчас самая низкая цена?",
                "Есть ли варианты со скидкой?",
                "Какие главные минусы у этой модели?"
            ],
            "query_used": search_query
        }

    # Fallback при сбое генерации
    answer_fallback = "Я проанализировал ваш запрос по каталогу электроники Казахстана.\n\n"
    if top_items:
        best = top_items[0]
        p_val = best.get('current_price', 0)
        answer_fallback += f"Лучшее предложение в базе: **{best['title']}** в магазине **{best['shop']}** за **{p_val:,} ₸**.\n\nНиже представлена подборка подходящих вариантов:".replace(',', ' ')
    else:
        answer_fallback += "К сожалению, по точному запросу сейчас нет предложений в наличии. Попробуйте уточнить модель или ценовой диапазон."

    fallback_products = _format_products_for_ui(top_items[:4])
    return {
        "answer": answer_fallback,
        "history_turn": history_entry_for_answer(answer_fallback, fallback_products),
        "products": fallback_products,
        "suggested_questions": [
            "Покажи товары со скидкой",
            "Посоветуй альтернативы",
            "Поиск по всем городам"
        ],
        "query_used": search_query
    }


async def normalize_product_titles_batch(
    titles: List[str],
    *,
    for_scan: bool = False,
    max_ai_calls: Optional[int] = None,
) -> Dict[str, str]:
    """Пакетная AI-нормализация наименований товаров для консервативного сопоставления между магазинами.

    Порядок: постоянный кэш SQLite → эвристика `extract_canonical_key` → AI (по 20 названий за вызов).
    Возвращает { название: ключ } для распознанных, { название: "" } для отправленных в AI без результата.
    Названия, до которых AI не дошел (лимит вызовов, бюджет), в ответе отсутствуют — их можно передать позже.

    for_scan=True — фоновая нормализация при обходе: отдельный урезанный лимит вызовов и резерв
    для пользовательских запросов. Названия, уже отправленные в AI за последние AI_RETRY_DAYS дней,
    повторно не отправляются, поэтому неудачи не расходуют квоту при каждом обходе.
    """
    from model_matching import extract_canonical_key
    from database import (get_cached_canonical_keys_batch, save_cached_canonical_keys_batch,
                          get_recent_ai_attempts, record_ai_attempts)

    unique = list(dict.fromkeys(t for t in titles if t and t.strip()))
    results: Dict[str, str] = dict(get_cached_canonical_keys_batch(unique))
    heuristic: Dict[str, str] = {}
    missing_for_ai: List[str] = []

    for t in unique:
        if t in results:
            continue
        heur = extract_canonical_key(t)
        if heur:
            results[t] = heur
            heuristic[t] = heur
        else:
            missing_for_ai.append(t)

    # Найденные эвристикой ключи сохраняются в постоянный кэш
    save_cached_canonical_keys_batch(heuristic)
    if not missing_for_ai:
        return results

    creds = _get_api_credentials()
    if not creds["has_ai"] or not creds["ai_search_enabled"]:
        return results

    # Недавние неудачи не повторяем
    recent = get_recent_ai_attempts(missing_for_ai, AI_RETRY_DAYS)
    for t in missing_for_ai:
        if t in recent:
            results[t] = ""
    missing_for_ai = [t for t in missing_for_ai if t not in recent]
    if not missing_for_ai:
        return results

    # Формируем компактный пакетный промпт (до 20 товаров)
    system_prompt = (
        "Ты эксперт по каталогам электроники и компьютерной техники в Казахстане. "
        "Твоя задача — извлечь канонический идентификатор модели (canonical_key) для каждого товара.\n"
        "Формат canonical_key: '<brand>:<model_or_family>:<capacity_or_spec>' (только латиница в нижнем регистре, цифры, дефисы и двоеточия).\n"
        "Игнорируй артикулы магазинов в скобках, слова 'смартфон', 'ноутбук', цвета, маркетинг и гарантию.\n"
        "Примеры:\n"
        "- 'Смартфон Apple iPhone 15 128Gb черный' -> 'apple:iphone 15:128gb'\n"
        "- 'Samsung Galaxy S24 Ultra 12/256GB' -> 'samsung:galaxy s24 ultra:256gb'\n"
        "- 'Видеокарта Palit GeForce RTX 4060 Dual 8GB' -> 'palit:rtx 4060:8gb'\n"
        "- 'Монитор 27\" LG UltraGear 27GP850-B' -> 'lg:27gp850:27'\n"
        "- 'Телевизор Samsung 55CU7100 4K' -> 'samsung:55cu7100:55'\n\n"
        "Ответь строго в формате JSON:\n"
        "{\n"
        "  \"items\": [\n"
        "    {\"title\": \"исходное название\", \"canonical_key\": \"бренд:модель:память\"}\n"
        "  ]\n"
        "}"
    )

    calls = 0
    for i in range(0, len(missing_for_ai), 20):
        if max_ai_calls is not None and calls >= max_ai_calls:
            break
        chunk = missing_for_ai[i:i + 20]
        user_prompt = "Нормализуй следующие товары:\n" + "\n".join(f"- {t}" for t in chunk)
        full_prompt = system_prompt + "\n\n" + user_prompt

        ai_res = None
        attempted = False
        if creds["gemini_api_key"]:
            ai_res = await call_gemini_api(full_prompt, creds["gemini_api_key"], scan=for_scan)
            attempted = True
        if not ai_res and creds["openai_api_key"]:
            ai_res = await call_openai_api(full_prompt, creds["openai_api_key"],
                                           creds.get("openai_api_base") or "https://api.openai.com/v1", scan=for_scan)
            attempted = True
        calls += 1

        if ai_res is None and attempted and for_scan and _budget_exhausted():
            # Бюджет исчерпан — вызова по сути не было: оставляем названия на потом, не помечая попыткой
            break

        new_cached = {}
        if isinstance(ai_res, dict) and isinstance(ai_res.get("items"), list):
            for item in ai_res["items"]:
                if not isinstance(item, dict):
                    continue
                orig = item.get("title")
                raw_key = item.get("canonical_key")
                ckey = re.sub(r"\s+", " ", raw_key.strip().lower()) if isinstance(raw_key, str) else ""
                # Ответ AI — недоверенные данные: принимается только ключ строгого формата
                if orig in chunk and valid_ai_canonical_key(ckey):
                    new_cached[orig] = ckey
        results.update(new_cached)
        save_cached_canonical_keys_batch(new_cached)

        # Все названия пакета помечаются попыткой: без ключа они не уйдут в AI еще AI_RETRY_DAYS дней
        record_ai_attempts(chunk)
        for t in chunk:
            results.setdefault(t, "")

    return results


def _budget_exhausted() -> bool:
    """Исчерпан ли бюджет фоновой нормализации (проверка без расхода лимита)."""
    now = time.monotonic()
    calls = _scan_limiter._calls.get("scan") or []
    recent = [c for c in calls if now - c <= _scan_limiter.period]
    return _provider_active >= SCAN_MAX_ACTIVE or len(recent) >= _scan_limiter.max_calls


async def get_or_normalize_title(title: str) -> Optional[str]:
    """Быстрое получение канонического ключа для одного названия (кэш -> эвристика -> AI)."""
    if not title:
        return None
    res = await normalize_product_titles_batch([title])
    return res.get(title)
