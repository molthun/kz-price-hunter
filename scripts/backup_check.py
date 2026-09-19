"""Проверка бэкапа базы перед восстановлением (R-M12): целостность, версия схемы, объём данных.

    python scripts/backup_check.py data/backups/prices-pre-v5-....db

Открывает файл только на чтение. Код возврата 0 — бэкап пригоден, 1 — нет.
Восстановление: остановить контейнер, заменить data/prices.db проверенной копией (удалить
prices.db-wal и prices.db-shm), запустить образ той версии, которая создала бэкап.
"""
import os
import sqlite3
import sys

TABLES = ("products", "users", "sessions", "alerts", "price_observations", "tracked_categories")


def check_backup(path: str) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = 0
        if "schema_metadata" in tables:
            row = conn.execute("SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()
            version = int(row[0]) if row and str(row[0]).isdigit() else 0
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES if t in tables}
    finally:
        conn.close()
    return {"ok": integrity == "ok", "integrity": integrity, "schema_version": version,
            "counts": counts, "size_mb": round(os.path.getsize(path) / 1e6, 1)}


def main(path: str) -> int:
    report = check_backup(path)
    print(f"integrity: {report['integrity']}, schema_version: {report['schema_version']}, размер: {report['size_mb']} МБ")
    for table, count in report["counts"].items():
        print(f"  {table}: {count}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
