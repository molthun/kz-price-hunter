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
    if not clean.isdigit() or not clean.replace("0", ""):
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


def _init_product_barcodes_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_barcodes (
            product_id TEXT NOT NULL,
            barcode TEXT NOT NULL,
            source TEXT DEFAULT 'scan',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (product_id, barcode)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_product_barcodes_barcode ON product_barcodes(barcode)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_product_barcodes_product_id ON product_barcodes(product_id)")
    conn.commit()


def get_product_barcodes(product_id: str) -> list[str]:
    """Возвращает список всех привязанных штрихкодов к данному товару."""
    if not product_id:
        return []
    try:
        with database.get_connection() as conn:
            _init_product_barcodes_table(conn)
            rows = conn.execute(
                "SELECT barcode FROM product_barcodes WHERE product_id = ? ORDER BY created_at DESC",
                (product_id,)
            ).fetchall()
            return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"Error getting product barcodes for {product_id}: {e}")
        return []


def link_barcode_to_products(product_ids: list[str] | str, barcode: str, source: str = "scan") -> bool:
    """
    Привязывает штрихкод к товару (или нескольким найденным товарам):
    1. Проверяет валидность штрихкода.
    2. Сохраняет связь в таблицу product_barcodes.
    3. Добавляет barcode в canonical_products.attributes_json.
    4. Обновляет barcode_cache с наименованием товара для последующего мгновенного поиска.
    """
    if not is_valid_barcode(barcode):
        return False

    if isinstance(product_ids, str):
        product_ids = [product_ids]

    clean_barcode = barcode.strip()
    if not product_ids:
        return False

    try:
        with database.get_connection() as conn:
            _init_product_barcodes_table(conn)
            _init_barcode_cache_table(conn)

            for pid in product_ids:
                if not pid:
                    continue
                # 1. Вставляем в product_barcodes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO product_barcodes (product_id, barcode, source, created_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (pid, clean_barcode, source)
                )

                # 2. Обновляем attributes_json в canonical_products
                try:
                    row = conn.execute(
                        "SELECT title, brand, attributes_json FROM canonical_products WHERE id = ?",
                        (pid,)
                    ).fetchone()

                    if row:
                        title, brand, attrs_raw = row[0], row[1], row[2]
                        try:
                            attrs = json.loads(attrs_raw) if attrs_raw else {}
                        except Exception:
                            attrs = {}

                        attrs["barcode"] = clean_barcode
                        b_list = attrs.get("barcodes", [])
                        if isinstance(b_list, list):
                            if clean_barcode not in b_list:
                                b_list.append(clean_barcode)
                        else:
                            b_list = [clean_barcode]
                        attrs["barcodes"] = b_list

                        conn.execute(
                            """
                            UPDATE canonical_products
                            SET attributes_json = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (json.dumps(attrs, ensure_ascii=False), pid)
                        )

                        # 3. Сохраняем в кэш с точным названием товара
                        if title:
                            conn.execute(
                                """
                                INSERT INTO barcode_cache (barcode, title, brand, resolved_at)
                                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                                ON CONFLICT(barcode) DO UPDATE SET
                                    title = excluded.title,
                                    brand = coalesce(excluded.brand, barcode_cache.brand),
                                    resolved_at = CURRENT_TIMESTAMP
                                """,
                                (clean_barcode, title, brand)
                            )
                except Exception as e_cp:
                    if "no such table: canonical_products" not in str(e_cp).lower():
                        logger.warning(f"Error updating canonical_product attributes for {pid}: {e_cp}")

            conn.commit()
            return True
    except Exception as e:
        logger.warning(f"Error linking barcode {clean_barcode} to products {product_ids}: {e}")
        return False


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

    # 1.1 Проверяем таблицу привязок товаров product_barcodes
    try:
        def _check_product_barcodes():
            with database.get_connection() as conn:
                _init_product_barcodes_table(conn)
                row = conn.execute(
                    """
                    SELECT cp.id, cp.title, cp.brand FROM product_barcodes pb
                    JOIN canonical_products cp ON pb.product_id = cp.id
                    WHERE pb.barcode = ?
                    ORDER BY pb.created_at DESC LIMIT 1
                    """,
                    (clean_code,)
                ).fetchone()
                if row:
                    return {"product_id": row[0], "title": row[1], "brand": row[2]}
                return None

        pb_match = await asyncio.to_thread(_check_product_barcodes)
        if pb_match:
            save_cached_barcode(clean_code, pb_match["title"], pb_match.get("brand"))
            return {
                "barcode": clean_code,
                "title": pb_match["title"],
                "brand": pb_match.get("brand"),
                "product_id": pb_match.get("product_id"),
                "source": "product_barcodes"
            }
    except Exception as e:
        if "no such table" not in str(e).lower():
            logger.debug(f"Error checking product_barcodes for {clean_code}: {e}")

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
