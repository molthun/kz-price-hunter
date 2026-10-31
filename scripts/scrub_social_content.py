"""Очистка уже накопленного чужого контента в content_observations (FA69R-03).

Убирает текст постов, ссылки на медиа и всё лишнее из raw_metadata, сохраняя технические поля. Новые записи
такого контента не содержат. Запуск — только по решению владельца; существующие бэкапы скрипт не меняет.
По умолчанию лишь считает строки, изменяет при --apply:

    python scripts/scrub_social_content.py /путь/к/prices.db [--apply]

После --apply место в файле освобождает VACUUM: без него удалённые байты физически остаются в базе.
"""
import argparse
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from repositories.content_repo import STORED_METADATA_KEYS  # noqa: E402


def _clean_metadata(raw: str) -> str:
    try:
        meta = json.loads(raw or "{}")
    except ValueError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return json.dumps({k: meta[k] for k in STORED_METADATA_KEYS if k in meta}, ensure_ascii=False)


def scrub(conn: sqlite3.Connection, apply: bool = False) -> int:
    rows = conn.execute("SELECT id, text, media_urls, raw_metadata FROM content_observations").fetchall()
    dirty = [(_clean_metadata(raw), row_id) for row_id, text, media, raw in rows
             if text or media != "[]" or _clean_metadata(raw) != raw]
    if apply and dirty:
        conn.executemany("UPDATE content_observations SET text = '', media_urls = '[]', raw_metadata = ? WHERE id = ?",
                         dirty)
        conn.commit()
        conn.execute("VACUUM")
    return len(dirty)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        count = scrub(conn, args.apply)
    finally:
        conn.close()
    print(f"{'Очищено' if args.apply else 'Требуют очистки'}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
