"""Третий источник истины для спорных пар товаров (P17-C7, docs/DEVELOPMENT_PLAN.md).

Правила ключа (product_key.py) и модель решают большинство названий, но остаются пары, где один магазин пишет
серию, а другой только артикул или характеристики. Для них решение даёт интернет: артикул или модель ищется
в своём SearXNG, а локальная модель по заголовкам и выдержкам страниц отвечает, один ли это товар.

- Выдача поиска — недоверенные данные: она идёт в подсказку изолированным блоком, по ссылкам ничего
  не открывается, в основание вердикта попадают только ссылки из самой выдачи.
- Вердикт «один товар» или «разные» засчитывается только при уверенности не ниже порога, иначе пара «неясна»
  и не склеивается.
- Поиск и вердикты кэшируются в базе (web_search_cache, product_pair_verdicts), число поисков в сутки
  ограничено настройкой search_daily_limit.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

SEARCH_TIMEOUT = 15.0
RESULTS_PER_QUERY = 6
# Поисковики за SearXNG приостанавливают выдачу при частых запросах: на замере C7 после ~80 запросов
# за 4 минуты DuckDuckGo, Brave и Google CSE отвечали «too many requests» и 3 минуты не искали, а DuckDuckGo
# стал требовать капчу. Поэтому запросы идут не чаще одного в SEARCH_MIN_INTERVAL секунд из процесса.
# Решение владельца 29.10.2026: «только SearXNG с таймаутом» — пауза задаётся настройкой search_interval_seconds
# (по умолчанию 90 с, до 960 поисков в сутки), это значение — для вызовов без настройки.
SEARCH_MIN_INTERVAL = 90.0
# None — поиска ещё не было: monotonic отсчитывается от загрузки машины, и 0.0 сразу после неё дал бы лишнюю паузу
_last_search = None
_search_lock = None
MIN_CONFIDENCE = 0.8
SAME, DIFFERENT, UNKNOWN = "same", "different", "unknown"

Search = Callable[[str], Awaitable[Optional[List[Dict[str, str]]]]]
Ask = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]

VERIFY_PROMPT = (
    "Ты эксперт по каталогам техники и потребительских товаров. Даны два названия товара из разных магазинов "
    "и результаты поиска в интернете по их артикулам или моделям. Определи, один ли это товар: та же модель "
    "производителя и то же исполнение (объём памяти, диагональ, комплект). Цвет и упаковка (OEM/BOX) не важны, "
    "если остальное совпадает.\n"
    "Опирайся на выдачу: совпадение артикула на странице производителя или магазина — сильное основание. "
    "Если выдача не позволяет решить — same: null.\n"
    "Ответь строго JSON: {\"same\": true|false|null, \"official_model\": \"модель по выдаче или null\", "
    "\"confidence\": число 0..1, \"evidence\": [\"ссылки из выдачи, на которых основан ответ\"], "
    "\"reason\": \"одна короткая фраза\"}"
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def pair_hash(title_a: str, title_b: str) -> str:
    """Пара без порядка: (a, b) и (b, a) — одна запись. Тот же хэш, что у product_key (вердикты связи)."""
    import product_key
    return product_key.pair_hash(title_a, title_b)


def queries_for(fields_a: Dict[str, Any], fields_b: Dict[str, Any]) -> List[str]:
    """Запросы: сначала артикулы производителя (как в поле, без нормализации — поисковику нужен исходный вид),
    затем «бренд модель», если артикула нет. Не больше двух разных запросов на пару."""
    out: List[str] = []
    for fields in (fields_a, fields_b):
        brand, sku, model = _text(fields.get("brand")), _text(fields.get("sku")), _text(fields.get("model"))
        if sku and re.search(r"\d", sku) and len(sku) >= 5:
            query = f"{brand} {sku}".strip() if brand.lower() not in sku.lower() else sku
        elif brand and model:
            query = f"{brand} {model}"
        else:
            continue
        if query.lower() not in (q.lower() for q in out):
            out.append(query[:120])
    return out[:2]


async def searxng_search(base: str, query: str, limit: int = RESULTS_PER_QUERY,
                         min_interval: Optional[float] = None) -> Optional[List[Dict[str, str]]]:
    """Выдача SearXNG в JSON: [{title, url, content}] или None при недоступности поиска.

    Пустая выдача, когда поисковики приостановили ответы (unresponsive_engines), — тоже None: это «поиск
    недоступен», а не «ничего не найдено», и в кэш она попадать не должна."""
    import aiohttp
    import time
    from urllib.parse import urlparse
    global _last_search, _search_lock
    if _search_lock is None:
        _search_lock = asyncio.Lock()
    url = f"{base.rstrip('/')}/search"
    async with _search_lock:
        pause = SEARCH_MIN_INTERVAL if min_interval is None else min_interval
        wait = 0 if _last_search is None else _last_search + pause - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_search = time.monotonic()
        return await _searxng_request(url, query, limit, aiohttp, urlparse)


async def _searxng_request(url, query, limit, aiohttp, urlparse):
    try:
        timeout = aiohttp.ClientTimeout(total=SEARCH_TIMEOUT, connect=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params={"q": query, "format": "json"}) as resp:
                if resp.status != 200:
                    print(f"[Verify] Поиск ответил HTTP {resp.status}")
                    return None
                data = await resp.json(content_type=None)
    except Exception as e:                          # поиск выключен, сеть, таймаут, не JSON
        print(f"[Verify] Поиск недоступен: {type(e).__name__}")
        return None
    results = []
    for item in (data or {}).get("results") or []:
        link = _text(item.get("url"))
        if not link or urlparse(link).scheme not in ("http", "https"):
            continue
        results.append({"title": _text(item.get("title"))[:200], "url": link[:300],
                        "content": _text(item.get("content"))[:300]})
        if len(results) >= limit:
            break
    if not results and (data or {}).get("unresponsive_engines"):
        print(f"[Verify] Поисковики не ответили: {len(data['unresponsive_engines'])}")
        return None
    return results


def build_prompt(title_a: str, title_b: str, results: Dict[str, List[Dict[str, str]]]) -> str:
    from ai_router import untrusted_block
    lines = [f"Название 1: {title_a}", f"Название 2: {title_b}", ""]
    for query, items in results.items():
        lines.append(f"Поиск «{query}»:")
        lines += [f"- {r['title']} | {r['url']} | {r['content']}" for r in items] or ["- (ничего не найдено)"]
    return VERIFY_PROMPT + "\n\n" + untrusted_block("\n".join(lines), "НАЗВАНИЯ_И_ВЫДАЧА")


def parse_verdict(reply: Any, allowed_urls: List[str], min_confidence: float = MIN_CONFIDENCE) -> Dict[str, Any]:
    """Ответ модели → вердикт. Неверный ответ, низкая уверенность или ответ без оснований из выдачи — «неясно»."""
    if not isinstance(reply, dict):
        return {"decision": UNKNOWN, "reason": "неверный ответ модели"}
    same = reply.get("same")
    try:
        confidence = max(0.0, min(1.0, float(reply.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    allowed = set(allowed_urls)
    evidence = [u for u in reply.get("evidence") or [] if isinstance(u, str) and u in allowed][:5]
    official = _text(reply.get("official_model"))[:120] or None
    verdict = {"same": same if isinstance(same, bool) else None, "confidence": round(confidence, 2),
               "official_model": official, "evidence": evidence, "reason": _text(reply.get("reason"))[:200]}
    if verdict["same"] is None or confidence < min_confidence or not evidence:
        verdict["decision"] = UNKNOWN
    else:
        verdict["decision"] = SAME if verdict["same"] else DIFFERENT
    return verdict


async def verify_pair(title_a: str, fields_a: Dict[str, Any], title_b: str, fields_b: Dict[str, Any],
                      search: Search, ask: Ask, min_confidence: float = MIN_CONFIDENCE) -> Dict[str, Any]:
    """Один ли товар: поиск по артикулам/моделям, затем сравнение моделью. Без поиска или выдачи — «неясно»."""
    queries = queries_for(fields_a or {}, fields_b or {})
    if not queries:
        return {"decision": UNKNOWN, "reason": "нечего искать: нет ни артикула, ни бренда с моделью", "queries": [],
                "transient": False}
    results: Dict[str, List[Dict[str, str]]] = {}
    for query in queries:
        found = await search(query)
        if found is None:
            # Поиск выключен, недоступен или исчерпан предел суток — повторить позже (P17C-04)
            return {"decision": UNKNOWN, "reason": "поиск недоступен", "queries": queries, "transient": True}
        results[query] = found
    urls = [r["url"] for items in results.values() for r in items]
    if not urls:
        return {"decision": UNKNOWN, "reason": "поиск ничего не нашёл", "queries": queries, "transient": False}
    reply = await ask(build_prompt(title_a, title_b, results))
    verdict = parse_verdict(reply, urls, min_confidence)
    verdict["queries"] = queries
    # Модель выключена («AI выкл», игра), занята или не ответила — это не «нет оснований», а временный отказ
    verdict["transient"] = reply is None
    return verdict


async def verify_pair_cached(title_a: str, fields_a: Dict[str, Any], title_b: str, fields_b: Dict[str, Any],
                             search_base: str, ask: Ask, daily_limit: int, model: Optional[str] = None,
                             min_interval: Optional[float] = None,
                             min_confidence: float = MIN_CONFIDENCE) -> Dict[str, Any]:
    """verify_pair с кэшем в базе: вердикт по паре и выдача по запросу берутся без повторного поиска,
    «неясно» не сохраняется (повторится при следующей проверке), суточный предел поисков соблюдается."""
    import database
    key = pair_hash(title_a, title_b)
    saved = database.get_pair_verdict(key)
    if saved:
        return {**saved, "cached": True}

    async def cached_search(query: str):
        hit = database.get_web_search(query)
        if hit is not None:
            return hit
        # Попытка занимается до запроса и тратится при любом исходе: пустая выдача, таймаут, HTTP-ошибка (P17C-03)
        if not database.reserve_web_search(query, daily_limit):
            return None
        found = await searxng_search(search_base, query, min_interval=min_interval)
        if found:                                   # пустую выдачу не кэшируем: повторится в следующий раз
            database.save_web_search(query, found)
        return found

    verdict = await verify_pair(title_a, fields_a, title_b, fields_b, cached_search, ask, min_confidence)
    if verdict["decision"] != UNKNOWN:
        database.save_pair_verdict(key, title_a, title_b, verdict, model)
    return verdict


def dumps(verdict: Dict[str, Any]) -> str:
    return json.dumps(verdict, ensure_ascii=False, sort_keys=True)[:4000]


def make_local_ask(config: Dict[str, Any]) -> Ask:
    """Функция ask для локальной модели по конфигурации приложения (P17C-05).

    Только через общий путь ai_service.call_local_api: «AI выкл» и выключение при игре соблюдаются и для
    запроса, ждавшего очереди; к модели один запрос за раз вместе с пользовательскими задачами; учёт — как у
    фоновой нормализации. При выключенном AI в настройках или без локальной модели к ней не обращается.
    Платная модель сюда не подключается: вердикт без локальной откладывается."""
    import ai_service
    base = config.get("local_api_base") or ""
    model = config.get("local_model") or ""
    key = config.get("local_api_key") or ""
    enabled = config.get("ai_search_enabled", True) is not False

    async def _ask(prompt: str) -> Optional[Dict[str, Any]]:
        if not (enabled and base and model):
            return None
        return await ai_service.call_local_api(prompt, base, model, key, timeout=90.0, scan=True)

    return _ask


def verification_pause_reason(config: Dict[str, Any]) -> Optional[str]:
    """Почему фоновая проверка пар сейчас не идёт (None — можно): выключен AI, нет локальной модели,
    «AI выкл» или игра на видеокарте. Пары остаются в очереди и проверяются после включения (P17C-05)."""
    if config.get("ai_search_enabled", True) is False:
        return "AI выключен в настройках"
    if not (config.get("local_api_base") and config.get("local_model")):
        return "локальная модель не настроена"
    import local_ai
    return local_ai.pause_reason()


def _merge_conflict(title_a, fields_a, key_a, title_b, fields_b, key_b) -> bool:
    """Противоречат ли друг другу пара и группы, которые вердикт «один товар» объединил бы целиком (C45R-01)."""
    import database
    import product_key
    group_a, group_b = [(title_a, fields_a, key_a)], [(title_b, fields_b, key_b)]
    if key_a != key_b:
        members = {key: [t for t in database.titles_with_canonical_key(key) if t not in (title_a, title_b)]
                   for key in (key_a, key_b) if key}
        known = database.get_title_fields_with_source([t for ts in members.values() for t in ts])
        for group, key in ((group_a, key_a), (group_b, key_b)):
            group += [(t, (known.get(t) or {}).get("fields"), key) for t in members.get(key, ())]
    return product_key.identities_conflict(group_a, group_b)


async def process_disputed_queue_tick(
    search_base: str,
    ask: Ask,
    daily_limit: int = 300,
    model: Optional[str] = None,
    min_interval: Optional[float] = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> Optional[Dict[str, Any]]:
    """Обрабатывает одну спорную пару из очереди (P17-C7).

    Возвращает dict с результатом или None, если поиск выключен, предел суток исчерпан или пора проверять
    нечего. «Неясно» не исключает пару: временный отказ — повтор с нарастающей паузой, отсутствие
    оснований — через неделю (database.defer_disputed_pair, P17C-04)."""
    import database
    if not search_base or int(daily_limit) <= 0:
        return None
    if database.web_searches_today() >= int(daily_limit):
        return None                                 # пары не трогаются: проверятся в следующие сутки

    pending = database.get_pending_disputed_pairs(limit=1)
    if not pending:
        return None
    item = pending[0]
    ph, title_a, title_b = item["pair_hash"], item["title_a"], item["title_b"]

    # Рабочие поля, иначе теневые: откуда они, видно в результате (аудит C45-02)
    fields_map = database.get_title_fields_with_source([title_a, title_b])
    item_a, item_b = fields_map.get(title_a), fields_map.get(title_b)
    if not item_a or not item_b:
        # Поля могут появиться позже: пара ждёт с повтором, а не теряется навсегда (аудит C45-05)
        status = database.defer_disputed_pair(ph, True, "no_fields")
        return {"pair_hash": ph, "status": status, "reason": "no_fields"}
    fields_a, fields_b = item_a["fields"], item_b["fields"]
    sources = {"a": item_a["source"], "b": item_b["source"]}

    verdict = await verify_pair_cached(
        title_a, fields_a, title_b, fields_b,
        search_base=search_base, ask=ask, daily_limit=daily_limit,
        model=model, min_interval=min_interval, min_confidence=min_confidence,
    )
    decision = verdict.get("decision")
    status = "resolved"
    if decision == SAME:
        import product_key
        cached = database.get_cached_canonical_keys_batch([title_a, title_b])
        key_a, key_b = cached.get(title_a), cached.get(title_b)
        pool = [k for k in (key_a, key_b) if k]
        if _merge_conflict(title_a, fields_a, key_a, title_b, fields_b, key_b):
            # «Один товар» при разном объёме, диагонали или ревизии, у чипа и компьютера с ним — ошибка извлечения
            # или вердикта: не склеивать (P17C-01, C45-01). Идентичность — по полям и названиям всех, кто переедет
            database.update_disputed_pair_status(ph, "unresolved", "same_but_variant_conflict")
            return {"pair_hash": ph, "decision": decision, "status": "unresolved", "verdict": verdict,
                    "fields_source": sources}
        if pool:
            # Преимущество k2-ключам, затем ключам с вариантами, затем самым коротким
            common_key = sorted(set(pool), key=lambda k: (0 if k.startswith("k2:") else 1,
                                                          0 if product_key._variants_of(k) else 1, len(k), k))[0]
            # Группа второго ключа переходит целиком: «один товар» — это одна группа, а не две пары названий
            moved = {title_a: common_key, title_b: common_key}
            for other in set(pool) - {common_key}:
                moved.update({t: common_key for t in database.titles_with_canonical_key(other)})
            database.rekey_titles(moved)
        database.update_disputed_pair_status(ph, "resolved", "same")
    elif decision == DIFFERENT:
        database.update_disputed_pair_status(ph, "resolved", "different")
    else:
        status = database.defer_disputed_pair(ph, bool(verdict.get("transient")), verdict.get("reason") or "")

    return {"pair_hash": ph, "decision": decision, "status": status, "verdict": verdict, "fields_source": sources}
