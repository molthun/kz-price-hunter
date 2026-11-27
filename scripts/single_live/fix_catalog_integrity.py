"""Правка уже записанных данных каталога по правилам 6.61–6.62 (поручение Codex 27.11.2026).

1. Прежняя цена: в `offers` и `offer_price_history` обнуляется `old_price`, которая не является
   скидкой по общему правилу `domain.price_rules.valid_old_price` (обе цены конечны, текущая > 0,
   прежняя строго выше текущей). Текущая цена, время наблюдения и остальные точки истории не меняются.
2. Названия: в `canonical_products.title` и `products.title` раскрываются HTML-сущности
   (`&#34;` → `"`). ID, ключ склейки, предложения, цены и наблюдения не меняются; поисковые
   индексы обновляют штатные триггеры таблиц.

По умолчанию только считает (открывает существующий файл БД в режиме `mode=ro`, без блокировки
активного писателя в WAL). `--apply` открывает существующий файл в `mode=rw` и применяет одной
транзакцией. Повторный запуск ничего не меняет. Запускать на остановленном приложении или на копии
БД, после резервной копии:

    python scripts/single_live/fix_catalog_integrity.py /app/data/prices.db [--apply]
"""
import json
import sqlite3
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from domain.price_rules import valid_old_price  # noqa: E402
from scrapers.base import clean_product_title  # noqa: E402

BAD_OLD_PRICE = "old_price IS NOT NULL AND is_valid_old_price(old_price, price) = 0"
PRICE_TABLES = ("offers", "offer_price_history")
TITLE_TABLES = (("canonical_products", "canonical_products_au"), ("products", "products_au"))


def _has(conn, kind, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)).fetchone() is not None


def _is_valid_old_price_sql(old_price, price) -> int:
    return 1 if valid_old_price(old_price, price) is not None else 0


def _open_existing_db(db_path: str, apply: bool) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Файл БД не найден: {db_path}")
    mode = "rw" if apply else "ro"
    uri = f"file:{urllib.parse.quote(str(path.resolve()))}?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.create_function("is_valid_old_price", 2, _is_valid_old_price_sql)
    return conn


def run(db_path: str, apply: bool = False) -> dict:
    conn = _open_existing_db(db_path, apply=apply)
    report = {"apply": apply, "old_price": {}, "titles": {}}
    in_tx = False
    try:
        has_any_table = any(
            _has(conn, "table", t)
            for t in (*PRICE_TABLES, *(tbl for tbl, _ in TITLE_TABLES))
        )
        if not has_any_table:
            raise ValueError(f"БД {db_path} не содержит таблиц каталога (offers/canonical_products/products)")

        if apply:
            conn.execute("BEGIN IMMEDIATE")
            in_tx = True

        for table in PRICE_TABLES:
            if not _has(conn, "table", table):
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {BAD_OLD_PRICE}").fetchone()[0]
            if apply and n:
                conn.execute(f"UPDATE {table} SET old_price = NULL WHERE {BAD_OLD_PRICE}")
            report["old_price"][table] = n
        for table, trigger in TITLE_TABLES:
            if not _has(conn, "table", table):
                continue
            if apply and not _has(conn, "trigger", trigger):
                raise RuntimeError(f"нет триггера индекса {trigger}: правка названий {table} не выполнена")
            rows = conn.execute(f"SELECT rowid, title FROM {table} WHERE title LIKE '%&%'").fetchall()
            changes = [(clean_product_title(t), rid) for rid, t in rows if t and clean_product_title(t) not in ("", t)]
            if apply and changes:
                conn.executemany(f"UPDATE {table} SET title = ? WHERE rowid = ?", changes)
            report["titles"][table] = len(changes)
        if in_tx:
            conn.execute("COMMIT")
            in_tx = False
    except Exception:
        if in_tx:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if len(args) != 1:
        sys.exit(__doc__)
    try:
        print(json.dumps(run(args[0], apply="--apply" in sys.argv[1:]), ensure_ascii=False))
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
