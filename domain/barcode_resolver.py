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
    # A08: только ASCII-цифры — `"²".isdigit()` истинно, но int("²") падает
    if not clean.isascii() or not clean.isdigit() or not clean.replace("0", ""):
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


def normalize_barcode(code: Any) -> Optional[str]:
    """A08: каноническое представление для хранения и поиска.

    UPC-A (12 цифр) хранится как GTIN-13 с ведущим нулём, чтобы один и тот же
    товар, отсканированный как UPC-A или как EAN-13, давал одну запись.
    """
    if not isinstance(code, str) or not is_valid_barcode(code):
        return None
    clean = code.strip()
    if len(clean) == 12:
        return "0" + clean
    return clean


# Источники связей, подтверждённые человеком: им доверяем раньше внешнего кэша (A02)
# `verified_photo` — связь, сверенная по фото упаковки (выставлялась на стенде вручную).
VERIFIED_SOURCES = ("manual_api", "verified_photo")


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


def get_barcode_product_ids(barcode: str) -> list[str]:
    """ID карточек, к которым уже привязан штрихкод (в каноническом виде)."""
    code = normalize_barcode(barcode)
    if not code:
        return []
    try:
        with database.get_connection() as conn:
            _init_product_barcodes_table(conn)
            rows = conn.execute(
                "SELECT product_id FROM product_barcodes WHERE barcode = ? ORDER BY created_at DESC",
                (code,),
            ).fetchall()
            return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"Error reading product_barcodes for {code}: {e}")
        return []


def _unlink_in_conn(conn, code: str, product_ids: list[str]) -> int:
    """Снимает связь кода с карточками внутри уже открытой транзакции."""
    removed = 0
    for pid in product_ids:
        cur = conn.execute("DELETE FROM product_barcodes WHERE product_id = ? AND barcode = ?", (pid, code))
        removed += cur.rowcount or 0
        row = conn.execute("SELECT attributes_json FROM canonical_products WHERE id = ?", (pid,)).fetchone()
        if not row:
            continue
        try:
            attrs = json.loads(row[0]) if row[0] else {}
        except Exception:
            continue
        if not isinstance(attrs, dict):
            continue
        changed = False
        b_list = attrs.get("barcodes")
        if isinstance(b_list, list) and code in b_list:
            b_list = [b for b in b_list if b != code]
            changed = True
            if b_list:
                attrs["barcodes"] = b_list
            else:
                attrs.pop("barcodes", None)
        if attrs.get("barcode") == code:
            changed = True
            remaining = attrs.get("barcodes") or []
            if remaining:
                attrs["barcode"] = remaining[-1]
            else:
                attrs.pop("barcode", None)
        if changed:
            conn.execute(
                "UPDATE canonical_products SET attributes_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(attrs, ensure_ascii=False), pid),
            )
    return removed


def unlink_barcode(barcode: str, product_ids: list[str]) -> int:
    """Снимает связь кода с указанными карточками. Возвращает число удалённых связей."""
    code = normalize_barcode(barcode)
    if not code or not product_ids:
        return 0
    with database.get_connection() as conn:
        _init_product_barcodes_table(conn)
        try:
            removed = _unlink_in_conn(conn, code, list(product_ids))
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        return removed


def link_barcode_to_products(product_ids: list[str] | str, barcode: str, source: str = "scan",
                             replace_product_ids: Optional[list[str]] = None) -> bool:
    """
    Привязывает штрихкод к существующим карточкам каталога одной транзакцией:
    1. Проверяет валидность и приводит штрихкод к каноническому виду.
    2. Пропускает ID, которых нет в canonical_products (A01: без осиротевших связей).
    3. Сохраняет связь в product_barcodes и добавляет код в attributes_json.
    4. Для подтверждённой человеком связи перезаписывает barcode_cache её названием
       (A02: проверенная связь приоритетнее внешнего результата), иначе заполняет кэш
       только при его отсутствии.
    Возвращает True, если записана хотя бы одна связь.
    """
    clean_barcode = normalize_barcode(barcode)
    if not clean_barcode:
        return False

    if isinstance(product_ids, str):
        product_ids = [product_ids]
    product_ids = [pid for pid in dict.fromkeys(product_ids or []) if pid]
    if not product_ids:
        return False

    verified = source in VERIFIED_SOURCES
    try:
        with database.get_connection() as conn:
            _init_product_barcodes_table(conn)
            _init_barcode_cache_table(conn)
            linked = 0
            try:
                if replace_product_ids:
                    _unlink_in_conn(conn, clean_barcode, [p for p in replace_product_ids if p not in product_ids])
                for pid in product_ids:
                    row = conn.execute(
                        "SELECT title, brand, attributes_json FROM canonical_products WHERE id = ?",
                        (pid,),
                    ).fetchone()
                    if not row:
                        continue
                    title, brand, attrs_raw = row[0], row[1], row[2]
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO product_barcodes (product_id, barcode, source, created_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (pid, clean_barcode, source),
                    )
                    if verified:
                        conn.execute(
                            "UPDATE product_barcodes SET source = ? WHERE product_id = ? AND barcode = ?",
                            (source, pid, clean_barcode),
                        )
                    try:
                        attrs = json.loads(attrs_raw) if attrs_raw else {}
                    except Exception:
                        attrs = {}
                    if not isinstance(attrs, dict):
                        attrs = {}
                    attrs["barcode"] = clean_barcode
                    b_list = attrs.get("barcodes", [])
                    if not isinstance(b_list, list):
                        b_list = []
                    if clean_barcode not in b_list:
                        b_list.append(clean_barcode)
                    attrs["barcodes"] = b_list
                    conn.execute(
                        "UPDATE canonical_products SET attributes_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (json.dumps(attrs, ensure_ascii=False), pid),
                    )
                    if title:
                        if verified:
                            conn.execute(
                                """
                                INSERT INTO barcode_cache (barcode, title, brand, resolved_at)
                                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                                ON CONFLICT(barcode) DO UPDATE SET
                                    title = excluded.title, brand = excluded.brand,
                                    resolved_at = CURRENT_TIMESTAMP
                                """,
                                (clean_barcode, title, brand),
                            )
                        else:
                            conn.execute(
                                "INSERT OR IGNORE INTO barcode_cache (barcode, title, brand, resolved_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                                (clean_barcode, title, brand),
                            )
                    linked += 1
            except Exception:
                conn.rollback()
                raise
            if not linked:
                conn.rollback()
                return False
            conn.commit()
            return True
    except Exception as e:
        logger.warning(f"Error linking barcode {clean_barcode} to products {product_ids}: {e}")
        return False


# --- A02: строгое сопоставление варианта товара ---

_TOKEN_RE = re.compile(r"[a-zа-яё]+|\d+(?:[.,]\d+)?", re.I)
# Слова без различительной силы. «без», «с», «и», «для» не входят сюда намеренно не все:
# «без» меняет вариант («без аромата»), поэтому остаётся значимым.
_STOPWORDS = frozenset({
    "шт", "штук", "уп", "упак", "упаковка", "в", "на", "и", "the", "of", "pcs", "pc",
    "купить", "цена", "товар", "новинка", "арт", "артикул",
})


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def variant_signature(title: str) -> tuple[frozenset, frozenset]:
    """(слова, числа) названия после нормализации: регистр, ё→е, простое усечение основы."""
    text = (title or "").lower().replace("ё", "е")
    words, numbers = set(), set()
    for tok in _TOKEN_RE.findall(text):
        if tok[0].isdigit():
            numbers.add(tok.replace(",", "."))
        elif tok not in _STOPWORDS and (len(tok) >= 2 or tok == "с"):
            words.add(_stem(tok))
    return frozenset(words), frozenset(numbers)


def is_exact_variant_match(resolved_title: str, product_title: str) -> bool:
    """Тот же вариант: совпадают все значимые слова и все числа (объём, количество, память).

    «Tompi с ароматом 100 шт» и «Tompi Без аромата 100 шт» различаются словами «с»/«без»,
    «100 шт» и «10 шт» — числами, поэтому автоматическая привязка между ними невозможна.
    """
    rw, rn = variant_signature(resolved_title)
    pw, pn = variant_signature(product_title)
    if len(rw) < 2 or not pw:
        return False
    return rw == pw and rn == pn


def is_loose_candidate(resolved_title: str, product_title: str) -> bool:
    """Похожая карточка для показа кандидатов (не для записи)."""
    rw, _ = variant_signature(resolved_title)
    pw, _ = variant_signature(product_title)
    common = rw & pw
    return len(common) >= 2 or (len(rw) <= 2 and len(common) >= 1)


def repair_unverified_auto_links() -> int:
    """Починка при старте (A02, аудит 6.31.0). Идемпотентна.

    Снимает автоматические (не подтверждённые человеком) связи, которые не прошли бы
    текущую строгую проверку: код автоматически привязан к нескольким карточкам, у кода
    уже есть подтверждённая связь либо название карточки не совпадает с распознанным
    названием как вариант. Связи
    `manual_api` не трогаются. Возвращает число снятых связей.
    """
    removed = 0
    with database.get_connection() as conn:
        _init_product_barcodes_table(conn)
        _init_barcode_cache_table(conn)
        try:
            placeholders = ",".join("?" for _ in VERIFIED_SOURCES)
            rows = conn.execute(
                f"""
                SELECT pb.barcode, pb.product_id, cp.title, bc.title
                FROM product_barcodes pb
                LEFT JOIN canonical_products cp ON cp.id = pb.product_id
                LEFT JOIN barcode_cache bc ON bc.barcode = pb.barcode
                WHERE pb.source NOT IN ({placeholders})
                """,
                VERIFIED_SOURCES,
            ).fetchall()
        except Exception as e:
            if "no such table" in str(e).lower():
                return 0
            raise
        by_code: Dict[str, list] = {}
        for code, pid, p_title, c_title in rows:
            by_code.setdefault(code, []).append((pid, p_title, c_title))
        verified_codes = {r[0] for r in conn.execute(
            f"SELECT DISTINCT barcode FROM product_barcodes WHERE source IN ({placeholders})", VERIFIED_SOURCES)}
        try:
            for code, links in by_code.items():
                if len(links) > 1 or code in verified_codes:
                    bad = [pid for pid, _, _ in links]
                else:
                    bad = [pid for pid, p_title, c_title in links
                           if not p_title or not c_title or not is_exact_variant_match(c_title, p_title)]
                if bad:
                    removed += _unlink_in_conn(conn, code, bad)
        except Exception:
            conn.rollback()
            raise
        conn.commit()
    if removed:
        logger.warning(f"Сняты неподтверждённые автопривязки штрихкодов: {removed}")
    return removed


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
                m_match = re.search(r"встречается в следующих товарах:\s*([^.]+)", content, flags=re.I)
                if m_match:
                    candidates = [x.strip() for x in m_match.group(1).split(";") if x.strip()]
                    if candidates:
                        # Предпочитаем наиболее полное наименование (с брендом, объемом, количеством слов)
                        sorted_cands = sorted(candidates, key=lambda s: (len(s.split()), len(s)), reverse=True)
                        for c in sorted_cands:
                            cand = _clean_product_title(c)
                            if len(cand) >= 4 and not cand.isdigit():
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
    clean_code = normalize_barcode(barcode)
    if not clean_code:
        return None
    raw_code = barcode.strip()

    # 1. Связь, подтверждённая человеком, приоритетнее любого кэша (A02)
    try:
        def _check_product_barcodes():
            with database.get_connection() as conn:
                _init_product_barcodes_table(conn)
                placeholders = ",".join("?" for _ in VERIFIED_SOURCES)
                row = conn.execute(
                    f"""
                    SELECT cp.id, cp.title, cp.brand FROM product_barcodes pb
                    JOIN canonical_products cp ON pb.product_id = cp.id
                    WHERE pb.barcode = ? AND pb.source IN ({placeholders})
                    ORDER BY pb.created_at DESC LIMIT 1
                    """,
                    (clean_code, *VERIFIED_SOURCES)
                ).fetchone()
                if row:
                    return {"product_id": row[0], "title": row[1], "brand": row[2]}
                return None

        pb_match = await asyncio.to_thread(_check_product_barcodes)
        if pb_match:
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

    # 1.1 Кэш распознавания
    cached = get_cached_barcode(clean_code)
    if cached:
        cached["barcode"] = clean_code
        return cached

    # 2. Проверяем, есть ли штрихкод в названиях или внешних артикулах существующих товаров
    try:
        def _check_db():
            with database.get_connection() as conn:
                # В products title
                row = conn.execute(
                    "SELECT title FROM products WHERE title LIKE ? LIMIT 1",
                    (f"%{raw_code}%",)
                ).fetchone()
                if row:
                    return row[0]
                # В offers external_sku
                row_sku = conn.execute(
                    """
                    SELECT p.title FROM offers o
                    JOIN products p ON o.product_id = p.id
                    WHERE o.external_sku IN (?, ?) LIMIT 1
                    """,
                    (clean_code, raw_code)
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
    resolved_title = await asyncio.to_thread(_fetch_from_searxng_sync, raw_code, timeout)
    if resolved_title:
        # Улучшение названия, если это известный паттерн (например 'Влаж салф 100 шт' -> салфетки 100 шт)
        save_cached_barcode(clean_code, resolved_title)
        return {"barcode": clean_code, "title": resolved_title, "source": "searxng"}

    return None
