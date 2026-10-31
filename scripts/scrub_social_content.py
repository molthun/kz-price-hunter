"""Очистка уже накопленного чужого контента в content_observations (FA69R-03).

Убирает текст постов, ссылки на медиа и всё лишнее из raw_metadata, сохраняя технические поля. Новые записи
такого контента не содержат. Запуск — только по решению владельца и при остановленном приложении; существующие
бэкапы скрипт не меняет. По умолчанию лишь считает строки, изменяет при --apply:

    python scripts/scrub_social_content.py /путь/к/prices.db [--apply]

Файлы базы меняет только сама SQLite: UPDATE с secure_delete, checkpoint WAL и VACUUM. Если другое соединение
удерживает старый снимок, записи уже очищены логически, но их байты ещё лежат в файлах: скрипт сообщает об этом
кодом выхода 3, и после закрытия соединений его нужно запустить снова. Завершение означает отсутствие данных
в файлах базы и WAL, а не на носителе: снимки файловой системы и SSD скрипт не контролирует.
"""
import argparse
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from repositories.content_repo import STORED_METADATA_KEYS  # noqa: E402

EXIT_PHYSICAL_INCOMPLETE = 3


class ScrubResult(int):
    """Число очищенных записей и итог физической очистки: None — физическая часть не запрашивалась."""

    physical_complete = None
    reason = ""

    def __new__(cls, rows: int, physical_complete=None, reason: str = ""):
        result = super().__new__(cls, rows)
        result.physical_complete = physical_complete
        result.reason = reason
        return result


def _clean_metadata(raw: str) -> str:
    try:
        meta = json.loads(raw or "{}")
    except ValueError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return json.dumps({k: meta[k] for k in STORED_METADATA_KEYS if k in meta}, ensure_ascii=False)


def _checkpoint_reason(conn: sqlite3.Connection) -> str:
    """Причина, по которой WAL не перенесён и не обнулён; пусто — WAL чист или базе не нужен."""
    busy, log_frames, _moved = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if busy:
        return "wal_busy"
    return "wal_not_empty" if log_frames > 0 else ""


def _finish_physically(conn: sqlite3.Connection) -> str:
    """Вытесняет старые страницы из файлов базы и WAL. Возвращает причину незавершённости или пусто."""
    try:
        reason = _checkpoint_reason(conn)
        if reason:
            return reason
        conn.execute("VACUUM")
        reason = _checkpoint_reason(conn)
        if reason:
            return reason
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        return f"sqlite_error:{type(exc).__name__}"
    return "" if integrity == "ok" else "integrity_failed"


def scrub(conn: sqlite3.Connection, apply: bool = False) -> ScrubResult:
    rows = conn.execute("SELECT id, text, media_urls, raw_metadata FROM content_observations").fetchall()
    dirty = [(_clean_metadata(raw), row_id) for row_id, text, media, raw in rows
             if text or media != "[]" or _clean_metadata(raw) != raw]
    if not apply:
        return ScrubResult(len(dirty))

    conn.commit()
    conn.execute("PRAGMA secure_delete = ON")
    conn.executemany("UPDATE content_observations SET text = '', media_urls = '[]', raw_metadata = ? WHERE id = ?",
                     dirty)
    conn.commit()
    # Выполняется и без новых строк: повторный запуск завершает прерванную физическую очистку
    reason = _finish_physically(conn)
    return ScrubResult(len(dirty), not reason, reason)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        result = scrub(conn, args.apply)
    finally:
        conn.close()
    if not args.apply:
        print(f"Требуют очистки: {int(result)}")
        return 0
    print(f"Очищено записей: {int(result)}")
    if result.physical_complete:
        print("Физическая очистка файлов базы и WAL завершена.")
        return 0
    print(f"Записи очищены логически, физическая очистка НЕ завершена ({result.reason}). "
          "Закройте другие соединения с базой и запустите скрипт снова.")
    return EXIT_PHYSICAL_INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
