"""
Barcode Resolver Module (EAN-13, EAN-8, UPC-A, GTIN-14).

Обеспечивает автоматическое распознавание и сопоставление цифровых штрихкодов товаров
с человекочитаемыми наименованиями из каталога и открытых источников Казахстана.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request

import config
import database

logger = logging.getLogger("barcode_resolver")


def is_valid_barcode(code: str) -> bool:
    """
    Проверяет, является ли строка валидным штрихкодом (EAN-13, EAN-8, UPC-A, GTIN-14)
    по длине и контрольной сумме стандарта GS1 (Modulo-10).
    """
    if not code or not isinstance(code, str):
        return False
    clean = code.strip()
    if not clean.isdigit():
        return False

    length = len(clean)
    if length == 13:
        # EAN-13: четные позиции с весом 1, нечетные с весом 3 (0-indexed)
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(clean[:-1]))
        check = (10 - (total % 10)) % 10
        return check == int(clean[-1])
    elif length == 8:
        # EAN-8: четные с весом 3, нечетные с весом 1
        total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(clean[:-1]))
        check = (10 - (total % 10)) % 10
        return check == int(clean[-1])
    elif length == 12:
        # UPC-A: эквивалент EAN-13 с лидирующим 0
        return is_valid_barcode("0" + clean)
    elif length == 14:
        # GTIN-14: четные с весом 3, нечетные с весом 1
        total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(clean[:-1]))
        check = (10 - (total % 10)) % 10
        return check == int(clean[-1])

    return False


def _init_barcode_cache_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barcode_cache (
            barcode TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            brand TEXT,
            resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def get_cached_barcode(barcode: str) -> Optional[Dict[str, Any]]:
    """Проверяет локальный кэш штрихкодов в SQLite."""
    try:
        with database.get_connection() as conn:
            _init_barcode_cache_table(conn)
            row = conn.execute(
                "SELECT barcode, title, brand FROM barcode_cache WHERE barcode = ?",
                (barcode,)
            ).fetchone()
            if row:
                return {"barcode": row[0], "title": row[1], "brand": row[2], "source": "cache"}
    except Exception as e:
        logger.warning(f"Error reading barcode_cache: {e}")
    return None


def save_cached_barcode(barcode: str, title: str, brand: Optional[str] = None) -> None:
    """Сохраняет сопоставление штрихкода в SQLite."""
    try:
        with database.get_connection() as conn:
            _init_barcode_cache_table(conn)
            conn.execute(
                """
                INSERT INTO barcode_cache (barcode, title, brand, resolved_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(barcode) DO UPDATE SET
                    title = excluded.title,
                    brand = coalesce(excluded.brand, barcode_cache.brand),
                    resolved_at = CURRENT_TIMESTAMP
                """,
                (barcode, title, brand)
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Error saving to barcode_cache: {e}")


def _clean_product_title(raw_text: str) -> str:
    """Очищает найденное наименование товара от мусорных префиксов и суффиксов."""
    if not raw_text:
        return ""
    text = raw_text.strip()
    # Убираем упоминания штрихкода
    text = re.sub(r"[\s\-_]*(?:Штрих-код|штрихкод|barcode|EAN|GTIN)[\s\-_:]*\d+", "", text, flags=re.I)
    # Убираем хвосты сайтов и рекламные слова
    text = re.sub(r"[\s\-_]+(?:barcode-list\.ru|Купить|цена|в интернет-магазине|Magnum|Kaspi|Flip).*$", "", text, flags=re.I)
    # Нормализуем пробелы
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fetch_from_searxng_sync(barcode: str, timeout: float = 3.0) -> Optional[str]:
    """Синхронный опрос локального узла SearXNG."""
    searxng_url = getattr(config, "SEARXNG_URL", "http://192.168.28.25:8888")
    query_url = f"{searxng_url}/search?q={urllib.parse.quote(barcode)}&format=json"

    try:
        req = urllib.request.Request(
            query_url,
            headers={"User-Agent": "KZPriceHunter-BarcodeResolver/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            results = data.get("results", [])

            for r in results:
                content = r.get("content", "")
                title = r.get("title", "")

                # Паттерн 1: "Этот штрих-код встречается в следующих товарах: <NAME>"
                m_match = re.search(r"встречается в следующих товарах:\s*([^;.]+)", content, flags=re.I)
                if m_match:
                    cand = _clean_product_title(m_match.group(1))
                    if len(cand) >= 3 and not cand.isdigit():
                        return cand

                # Паттерн 2: Извлекаем из заголовка карточки штрихкода
                cand_title = _clean_product_title(title)
                if len(cand_title) >= 4 and not cand_title.isdigit():
                    return cand_title
    except Exception as e:
        logger.debug(f"SearXNG barcode lookup error for {barcode}: {e}")

    return None


async def resolve_barcode(barcode: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """
    Разрешает штрихкод товара в наименование.
    1. Проверяет SQLite кэш (мгновенно).
    2. Проверяет наличие в базе существующих товаров.
    3. Опрашивает локальный SearXNG / открытые реестры штрихкодов.
    """
    if not is_valid_barcode(barcode):
        return None

    clean_code = barcode.strip()

    # 1. Проверяем кэш
    cached = get_cached_barcode(clean_code)
    if cached:
        return cached

    # 2. Проверяем, есть ли штрихкод в названиях или внешних артикулах существующих товаров
    try:
        def _check_db():
            with database.get_connection() as conn:
                # В products title
                row = conn.execute(
                    "SELECT title FROM products WHERE title LIKE ? LIMIT 1",
                    (f"%{clean_code}%",)
                ).fetchone()
                if row:
                    return row[0]
                # В offers external_sku
                row_sku = conn.execute(
                    """
                    SELECT p.title FROM offers o
                    JOIN products p ON o.product_id = p.id
                    WHERE o.external_sku = ? LIMIT 1
                    """,
                    (clean_code,)
                ).fetchone()
                if row_sku:
                    return row_sku[0]
                return None

        db_title = await asyncio.to_thread(_check_db)
        if db_title:
            save_cached_barcode(clean_code, db_title)
            return {"barcode": clean_code, "title": db_title, "source": "database"}
    except Exception as e:
        if "no such table" not in str(e).lower():
            logger.warning(f"Error checking db for barcode {clean_code}: {e}")

    # 3. Внешний резолвинг через SearXNG
    resolved_title = await asyncio.to_thread(_fetch_from_searxng_sync, clean_code, timeout)
    if resolved_title:
        # Улучшение названия, если это известный паттерн (например 'Влаж салф 100 шт' -> салфетки 100 шт)
        save_cached_barcode(clean_code, resolved_title)
        return {"barcode": clean_code, "title": resolved_title, "source": "searxng"}

    return None
