"""
Web Search via SearXNG for Kazakhstan Stores and Products (KZPH SearXNG Integration).

Предоставляет:
1. Живой поиск по ведущим интернет-магазинам Казахстана (Kaspi, Mechta, Sulpak,
   Белый Ветер, Technodom, Marwin, DNS, Flip и др.) через локальный узел SearXNG.
2. Кэширование запросов в SQLite (web_search_cache) и учет суточных квот (reserve_web_search).
3. Извлечение магазина, цен (в тенге), бейджей и нормализованных ссылок.
4. Устойчивость к отказам: таймауты, изолированные блоки, безопасный fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

import config
import database

logger = logging.getLogger("web_search")

# Реестр известных казахстанских магазинов
KNOWN_KZ_SHOPS: Dict[str, Dict[str, Any]] = {
    "kaspi.kz": {"name": "Kaspi Магазин", "badge": "Kaspi", "color": "#f14635"},
    "shop.kz": {"name": "Белый Ветер", "badge": "Белый Ветер", "color": "#0072bc"},
    "mechta.kz": {"name": "Мечта", "badge": "Мечта", "color": "#ff0033"},
    "sulpak.kz": {"name": "Sulpak", "badge": "Sulpak", "color": "#e30613"},
    "technodom.kz": {"name": "Technodom", "badge": "Technodom", "color": "#ff6600"},
    "marwin.kz": {"name": "Marwin / Меломан", "badge": "Marwin", "color": "#009ee2"},
    "flip.kz": {"name": "Flip.kz", "badge": "Flip", "color": "#28a745"},
    "dns-shop.kz": {"name": "DNS Казахстан", "badge": "DNS", "color": "#ea5a00"},
    "alser.kz": {"name": "Alser", "badge": "Alser", "color": "#e4002b"},
    "evrika.com": {"name": "Evrika", "badge": "Evrika", "color": "#1e88e5"},
    "lemanapro.kz": {"name": "Лемана ПРО", "badge": "Лемана ПРО", "color": "#53b754"},
    "arbuz.kz": {"name": "Арбуз", "badge": "Арбуз", "color": "#00a651"},
    "magnum.kz": {"name": "Magnum", "badge": "Magnum", "color": "#dc3545"},
    "intertop.kz": {"name": "Intertop", "badge": "Intertop", "color": "#111827"},
    "detmir.kz": {"name": "Детский мир", "badge": "Детский мир", "color": "#0071bc"},
    "12mesyacev.kz": {"name": "12 Месяцев", "badge": "12 Месяцев", "color": "#e67e22"},
    "monamie.kz": {"name": "Mon Amie", "badge": "Mon Amie", "color": "#8e44ad"},
    "beautymania.kz": {"name": "Французский Дом", "badge": "Французский Дом", "color": "#2c3e50"},
    "olx.kz": {"name": "OLX Казахстан", "badge": "OLX", "color": "#002f34"},
}

DEFAULT_KZ_DOMAINS = [
    "kaspi.kz", "shop.kz", "mechta.kz", "sulpak.kz",
    "technodom.kz", "marwin.kz", "flip.kz", "dns-shop.kz"
]

DEFAULT_SEARCH_ENGINES = "duckduckgo,startpage,mojeek"
SEARCH_TIMEOUT_SECONDS = 12.0
MAX_QUERY_LENGTH = 200

PRICE_RE = re.compile(
    r"(?:цена|от|стоимость)?\s*([1-9]\d{0,2}(?:[\s\u00a0]\d{3})+|[1-9]\d{3,7})\s*(?:₸|тг|тенге|kzt|\bтг\b)",
    re.IGNORECASE
)


def get_shop_info(url_or_domain: str) -> Dict[str, Any]:
    """Определяет магазин, цвет и бейдж по URL или домену."""
    try:
        domain = url_or_domain.lower()
        if "://" in domain:
            domain = urlparse(domain).netloc
        domain = re.sub(r"^www\.", "", domain)
    except Exception:
        domain = url_or_domain.lower()

    for shop_domain, meta in KNOWN_KZ_SHOPS.items():
        if domain == shop_domain or domain.endswith("." + shop_domain):
            return {
                "domain": domain,
                "shop_name": meta["name"],
                "badge": meta["badge"],
                "color": meta["color"],
                "is_known_partner": True
            }

    # Неизвестный сайт
    return {
        "domain": domain,
        "shop_name": domain,
        "badge": domain.split(".")[0].capitalize(),
        "color": "#64748b",
        "is_known_partner": False
    }


def parse_price(text: str) -> Optional[int]:
    """Извлекает цену в тенге из заголовка или описания товара."""
    if not text:
        return None
    match = PRICE_RE.search(text)
    if not match:
        return None
    try:
        num_str = re.sub(r"[\s\u00a0]", "", match.group(1))
        val = int(num_str)
        # Отсекаем неправдоподобные числа (меньше 100 ₸ или больше 50 млн ₸)
        if 100 <= val <= 50_000_000:
            return val
    except (ValueError, TypeError):
        pass
    return None


def build_search_query(raw_query: str, city: Optional[str] = None, mode: str = "kz_shops") -> str:
    """Формирует поисковый запрос к SearXNG в зависимости от режима."""
    clean = " ".join(raw_query.strip().split())[:MAX_QUERY_LENGTH]
    if not clean:
        return ""

    if mode == "kz_shops":
        # Если пользователь уже сам написал операторы site: или OR, не дублируем
        if "site:" in clean.lower():
            return clean
        domains_filter = " OR ".join(f"site:{d}" for d in DEFAULT_KZ_DOMAINS)
        return f"{clean} ({domains_filter})"

    if mode == "kz_general":
        # Общий поиск с гео-привязкой к Казахстану
        geo = city.strip() if city and city != "Все" else "Казахстан"
        return f"{clean} {geo} купить"

    return clean


async def search_web(
    query: str,
    city: Optional[str] = None,
    mode: str = "kz_shops",
    limit: int = 10,
    max_age_days: int = 7,
    search_base: Optional[str] = None
) -> Dict[str, Any]:
    """Выполняет веб-поиск через SearXNG с кэшированием в SQLite и защитой квот.

    Возвращает:
    {
      "ok": True,
      "query": "...",
      "total": 5,
      "results": [
        {
          "title": "...",
          "url": "https://...",
          "snippet": "...",
          "domain": "kaspi.kz",
          "shop_name": "Kaspi Магазин",
          "badge": "Kaspi",
          "color": "#f14635",
          "price": 29900,
          "currency": "₸",
          "engine": "duckduckgo"
        }
      ],
      "from_cache": False,
      "provider": "SearXNG (http://...)"
    }
    """
    clean_q = query.strip()
    if not clean_q:
        return {"ok": True, "query": "", "total": 0, "results": [], "from_cache": False}

    built_query = build_search_query(clean_q, city=city, mode=mode)
    cache_key = f"web:{mode}:{built_query}"

    # 1. Проверяем локальный кэш
    try:
        cached = await asyncio.to_thread(database.get_web_search, cache_key, max_age_days)
        if cached is not None and isinstance(cached, list):
            return {
                "ok": True,
                "query": clean_q,
                "total": len(cached[:limit]),
                "results": cached[:limit],
                "from_cache": True,
                "provider": "Cache (web_search_cache)"
            }
    except Exception as e:
        logger.warning("Ошибка чтения web_search_cache: %s", e)

    # 2. Определяем адрес узла SearXNG
    ai_cfg = config.get_ai_config()
    base = search_base or ai_cfg.get("search_api_base") or "http://192.168.28.25:8888"
    daily_limit = int(ai_cfg.get("search_daily_limit") or 300)

    # 3. Резервируем суточный лимит попыток
    try:
        allowed = await asyncio.to_thread(database.reserve_web_search, cache_key, daily_limit)
        if not allowed:
            return {
                "ok": False,
                "error": f"Исчерпан суточный лимит веб-поиска ({daily_limit} запросов/сутки).",
                "query": clean_q,
                "total": 0,
                "results": [],
                "from_cache": False
            }
    except Exception as e:
        logger.warning("Ошибка reserve_web_search: %s", e)

    # 4. Запрос к SearXNG
    url = f"{base.rstrip('/')}/search"
    params = {
        "q": built_query,
        "format": "json",
        "engines": DEFAULT_SEARCH_ENGINES
    }

    try:
        timeout = aiohttp.ClientTimeout(total=SEARCH_TIMEOUT_SECONDS, connect=4.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {"User-Agent": "KZPriceHunter/1.0 (+https://shop.molthun.ru)"}
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning("SearXNG HTTP %s: %s", resp.status, url)
                    return {
                        "ok": False,
                        "error": f"SearXNG ответил HTTP {resp.status}",
                        "query": clean_q,
                        "total": 0,
                        "results": [],
                        "from_cache": False
                    }
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning("SearXNG недоступен: %s (%s)", type(e).__name__, e)
        return {
            "ok": False,
            "error": "Поисковый узел SearXNG временно недоступен",
            "query": clean_q,
            "total": 0,
            "results": [],
            "from_cache": False
        }

    # 5. Обработка результатов
    raw_results = (data or {}).get("results") or []
    cleaned_results: List[Dict[str, Any]] = []
    seen_urls = set()

    for item in raw_results:
        link = str(item.get("url") or "").strip()
        if not link or link in seen_urls:
            continue
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https"):
            continue

        title = str(item.get("title") or "").strip()[:200]
        snippet = str(item.get("content") or "").strip()[:350]
        if not title:
            continue

        shop = get_shop_info(link)
        price = parse_price(title) or parse_price(snippet)

        cleaned_results.append({
            "title": title,
            "url": link,
            "snippet": snippet,
            "domain": shop["domain"],
            "shop_name": shop["shop_name"],
            "badge": shop["badge"],
            "color": shop["color"],
            "price": price,
            "currency": "₸" if price else None,
            "engine": str(item.get("engine") or "searxng"),
        })
        seen_urls.add(link)
        if len(cleaned_results) >= limit:
            break

    # 6. Сохраняем в кэш SQLite
    if cleaned_results:
        try:
            await asyncio.to_thread(database.save_web_search, cache_key, cleaned_results)
        except Exception as e:
            logger.warning("Ошибка сохранения web_search_cache: %s", e)

    return {
        "ok": True,
        "query": clean_q,
        "total": len(cleaned_results),
        "results": cleaned_results,
        "from_cache": False,
        "provider": f"SearXNG ({base})"
    }
