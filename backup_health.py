"""Резервные копии, сроки хранения и самопроверка (этап P15).

Бэкап, который никто ни разу не открывал, — это надежда, а не копия. Поэтому здесь не только делают копии,
но и **проверяют их по-настоящему**: открывают файл, прогоняют проверку целостности, читают схему и
пересчитывают ключевые таблицы, а периодически ещё и восстанавливают копию во временную базу и убеждаются,
что с ней можно работать. Временная база после проверки удаляется.

Сроки хранения собраны в одном месте: видно, что именно удаляется, через сколько дней и сколько строк это
сейчас занимает. Данные не удаляются «просто так» — у каждой строки политики есть причина.

Отдельная честность: внутренняя проверка не заметит, что процесс целиком остановился. Это может увидеть
только наблюдатель снаружи, и в отчёте об этом сказано прямо, а не подразумевается.
"""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Dict, List, Optional

# Как часто проверять копию по-настоящему (открыть, проверить целостность, восстановить)
VERIFY_INTERVAL_HOURS = 24
# Копия старше этого срока считается просроченной: свежесть копии — половина её ценности
BACKUP_STALE_HOURS = 48
# Сколько копий держим: старые удаляются только после успешной проверки более новой
KEEP_BACKUPS = 7
# Ограничение самопроверки: она не должна мешать работе сервиса
VERIFY_TIMEOUT_SECONDS = 120


def _backup_dir():
    from config import DATA_DIR
    return DATA_DIR / "backups"


def list_backups() -> List[Dict[str, Any]]:
    """Копии от новых к старым: имя, размер, возраст."""
    directory = _backup_dir()
    items: List[Dict[str, Any]] = []
    try:
        entries = list(directory.glob("*.db"))
    except OSError:
        return items
    now = time.time()
    for path in entries:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({"path": str(path), "name": path.name, "size_bytes": stat.st_size,
                      "age_hours": round(max(0.0, (now - stat.st_mtime) / 3600.0), 2),
                      "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime,
                                                                     datetime.timezone.utc).isoformat()})
    items.sort(key=lambda i: i["age_hours"])
    return items


def verify_backup(path: str, deep: bool = True) -> Dict[str, Any]:
    """Открывает копию и проверяет, что с ней действительно можно работать.

    deep=True — полная проверка целостности (integrity_check), иначе быстрая (quick_check).
    Возвращает разбор: открылась ли, что сказала проверка, какая схема, сколько строк в ключевых таблицах.
    """
    started = time.monotonic()
    result: Dict[str, Any] = {"path": path, "name": os.path.basename(path), "ok": False,
                              "integrity": None, "schema_version": None, "counts": {}, "error": None}
    conn = None
    try:
        if not os.path.exists(path):
            result["error"] = "файла нет"
            return result
        result["size_bytes"] = os.path.getsize(path)
        # Открываем только на чтение: проверка не должна менять саму копию
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        check = "integrity_check" if deep else "quick_check"
        result["integrity"] = conn.execute(f"PRAGMA {check}").fetchone()[0]
        if result["integrity"] != "ok":
            result["error"] = f"проверка целостности: {result['integrity']}"
            return result
        row = conn.execute("SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()
        result["schema_version"] = int(row[0]) if row and str(row[0]).isdigit() else None
        for table in ("products", "alerts", "users", "watches"):
            try:
                result["counts"][table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                result["counts"][table] = None
        if not result["counts"].get("products"):
            result["error"] = "в копии нет товаров — восстанавливать из неё нечего"
            return result
        result["ok"] = True
        return result
    except (sqlite3.DatabaseError, OSError) as e:
        result["error"] = f"{type(e).__name__}: файл не читается как база"
        return result
    finally:
        if conn is not None:
            conn.close()
        result["duration_sec"] = round(time.monotonic() - started, 2)


def restore_rehearsal(path: Optional[str] = None) -> Dict[str, Any]:
    """Репетиция восстановления: копия разворачивается во временную базу и проверяется, затем удаляется.

    Это единственный способ узнать, что копия действительно пригодна: проверка на месте не доказывает,
    что из файла можно поднять рабочую базу.
    """
    started = time.monotonic()
    backups = list_backups()
    source = path or (backups[0]["path"] if backups else None)
    outcome: Dict[str, Any] = {"restored": False, "source": source, "error": None, "checks": {}}
    if not source:
        outcome["error"] = "копий нет"
        return outcome

    temp_dir = tempfile.mkdtemp(prefix="kzph-restore-")
    temp_db = os.path.join(temp_dir, "restored.db")
    try:
        shutil.copy2(source, temp_db)
        verified = verify_backup(temp_db, deep=True)
        outcome["checks"] = verified
        if not verified["ok"]:
            outcome["error"] = verified["error"]
            return outcome
        # Восстановленная база должна не только открываться, но и отвечать на обычный запрос
        conn = sqlite3.connect(temp_db, timeout=10)
        try:
            sample = conn.execute("SELECT id, title FROM products LIMIT 1").fetchone()
            outcome["sample_readable"] = bool(sample)
        finally:
            conn.close()
        outcome["restored"] = bool(outcome.get("sample_readable"))
        if not outcome["restored"]:
            outcome["error"] = "восстановленная база пуста"
        return outcome
    except (OSError, sqlite3.DatabaseError) as e:
        outcome["error"] = f"{type(e).__name__}: восстановить не удалось"
        return outcome
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)   # временная база не остаётся на диске
        outcome["duration_sec"] = round(time.monotonic() - started, 2)


def retention_policy() -> List[Dict[str, Any]]:
    """Сроки хранения одним списком: что удаляется, через сколько дней и почему.

    Собрано из действующих констант, а не переписано отдельно: расхождение политики и кода было бы
    хуже отсутствия политики.
    """
    import data_quality
    import database
    import search_analytics
    import telemetry
    return [
        {"data": "Диагностика HTTP (выборки задержек)", "table": "telemetry_http_samples",
         "days": telemetry.RETENTION_DAYS["samples"],
         "why": "нужна для разбора свежих сбоев, дальше только агрегаты"},
        {"data": "События телеметрии", "table": "telemetry_events", "days": telemetry.RETENTION_DAYS["events"],
         "why": "месяц истории покрывает разбор инцидентов"},
        {"data": "Часовые агрегаты HTTP", "table": "telemetry_http_aggregates",
         "days": telemetry.RETENTION_DAYS["hour"],
         "why": "сравнение недель и месяцев"},
        {"data": "Дневные агрегаты HTTP", "days": telemetry.RETENTION_DAYS["day"],
         "why": "годовая динамика нагрузки"},
        {"data": "История качества обходов", "table": "source_scans",
         "days": data_quality.SOURCE_SCANS_RETENTION_DAYS,
         "why": "норма источника считается по последним обходам"},
        {"data": "Аналитика поиска", "table": "search_stats", "days": search_analytics.RETENTION_DAYS,
         "why": "решение владельца: 90 дней, дальше данные не нужны"},
        {"data": "Расходы AI", "table": "ai_usage", "days": 365, "why": "годовой счёт и сравнение моделей"},
        {"data": "История цен", "table": "price_observations",
         "days": database.PRICE_HISTORY_RETENTION_DAYS,
         "why": "полгода хватает для графиков и обучения нормы"},
        {"data": "Завершённые уведомления", "table": "notification_outbox",
         "days": database.NOTIFICATION_RETENTION_DAYS,
         "why": "очередь не архив: доставленное хранится месяц"},
    ]


def due_for_verification(checks: Optional[List[Dict[str, Any]]] = None,
                         now: Optional[datetime.datetime] = None) -> bool:
    """Пора ли проверять копию: не чаще раза в сутки, чтобы самопроверка не мешала работе."""
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    checks = checks if checks is not None else database.backup_checks(limit=1)
    if not checks:
        return True
    try:
        last = datetime.datetime.fromisoformat(checks[0]["checked_at"])
    except (TypeError, ValueError, KeyError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    return (moment - last).total_seconds() >= VERIFY_INTERVAL_HOURS * 3600


def verify_backups_if_due(now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Периодическая самопроверка: раз в сутки развернуть свежую копию и убедиться, что она пригодна.

    Ошибка самой проверки не должна ломать обслуживание базы, поэтому результат записывается, а исключение
    наружу не уходит. Пульс компонента (P14) ставится в любом случае — видно, что проверка вообще идёт.
    """
    import database
    import environment
    if not due_for_verification(now=now):
        return None
    result = {"restored": False, "error": "не выполнялась"}
    try:
        result = restore_rehearsal()
        detail = result.get("error") or (
            f"схема {result.get('checks', {}).get('schema_version')}, "
            f"товаров {result.get('checks', {}).get('counts', {}).get('products')}")
        database.record_backup_check("restore", bool(result.get("restored")),
                                     file=result.get("source"), detail=detail,
                                     duration_sec=result.get("duration_sec"), now=now)
    except Exception as e:                      # самопроверка не должна ронять обслуживание
        try:
            database.record_backup_check("restore", False, detail=f"{type(e).__name__}", now=now)
        except Exception:
            pass
    finally:
        environment.heartbeat(environment.BACKUP,
                              "проверка пройдена" if result.get("restored") else "проверка не прошла")
    return result
