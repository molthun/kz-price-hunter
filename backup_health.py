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
import threading
import time
from typing import Any, Dict, List, Optional

# Как часто проверять копию по-настоящему (открыть, проверить целостность, восстановить)
VERIFY_INTERVAL_HOURS = 24
# Копия старше этого срока считается просроченной: свежесть копии — половина её ценности
BACKUP_STALE_HOURS = 48
# Сколько копий держим: старые удаляются только после успешной проверки более новой
KEEP_BACKUPS = 7
# Ограничение самопроверки: она не должна мешать работе сервиса. Предел действует на всю проверку целиком —
# копирование, проверку целостности и запросы (P15 L03); по его истечении проверка прерывается с ошибкой.
VERIFY_TIMEOUT_SECONDS = 120

# Минимальный контракт пригодной копии (P15 L02): файл SQLite с одним товаром — ещё не рабочая база.
# Без этих таблиц приложение не поднимется, поэтому такая копия не считается пригодной к восстановлению.
REQUIRED_TABLES = ("products", "alerts", "users", "watches", "schema_metadata", "sessions",
                   "notification_outbox")

# Обязательные колонки критичных таблиц. Наличия таблицы мало: база с `watches(id)` откроется, но
# «Мои наблюдения» на ней упадут (P15 L02), поэтому проверяются поля, которыми пользуется приложение.
REQUIRED_COLUMNS = {
    "products": ("id", "title", "shop", "city", "url", "current_price", "is_active"),
    "users": ("id", "settings", "is_blocked"),
    "watches": ("id", "user_id", "kind", "target", "condition", "is_active"),
    "alerts": ("id", "product_id", "alert_type", "new_price", "created_at"),
    "sessions": ("token_hash", "user_id", "expires_at"),
    "notification_outbox": ("id", "alert_id", "user_id", "payload", "status"),
}
REQUIRED_PRODUCT_COLUMNS = REQUIRED_COLUMNS["products"]      # совместимость с прежним именем

# Запросы, которые приложение действительно делает. Проверяется именно чтение нужных полей: COUNT(*)
# проходит и на базе без единой рабочей колонки.
SMOKE_QUERIES = (
    ("активные товары", "SELECT COUNT(*) FROM products WHERE is_active = 1"),
    ("товар с ценой", "SELECT id, title, shop, city, url, current_price FROM products "
                      "WHERE current_price > 0 LIMIT 1"),
    ("вход пользователя", "SELECT u.id, u.settings, u.is_blocked, s.token_hash, s.expires_at FROM users u "
                          "LEFT JOIN sessions s ON s.user_id = u.id LIMIT 1"),
    ("наблюдения человека", "SELECT id, user_id, kind, target, condition, is_active FROM watches LIMIT 1"),
    ("очередь уведомлений", "SELECT id, alert_id, user_id, payload, status FROM notification_outbox LIMIT 1"),
    ("лента алертов", "SELECT id, product_id, alert_type, new_price, created_at FROM alerts LIMIT 1"),
)


class VerificationTimeout(Exception):
    """Проверка не уложилась в отведённое время и была прервана."""


def _deadline(timeout: Optional[float] = None) -> float:
    return time.monotonic() + (VERIFY_TIMEOUT_SECONDS if timeout is None else timeout)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise VerificationTimeout("проверка не уложилась в отведённое время")


def _guard_connection(conn, deadline: float) -> None:
    """Прерывает долгие запросы к копии: sqlite timeout ограничивает ожидание блокировки, а не работу."""
    def _watch():
        return 1 if time.monotonic() > deadline else 0
    conn.set_progress_handler(_watch, 10000)


def supported_schema_versions() -> set:
    """Версии схемы, из которых приложение умеет подниматься: текущая и предыдущая."""
    import database
    current = int(database.SCHEMA_VERSION)
    return {current, current - 1} if current > 1 else {current}


def file_identity(path: str) -> Dict[str, Any]:
    """Приметы файла копии. Размер и время изменения дешевле дайджеста и достаточны, чтобы заметить,
    что проверялась другая копия или что файл подменили под прежним именем."""
    try:
        stat = os.stat(path)
    except OSError:
        return {"file": os.path.basename(path), "file_size": None, "file_mtime": None}
    return {"file": os.path.basename(path), "file_size": stat.st_size,
            "file_mtime": round(stat.st_mtime, 3)}


def same_file(identity: Dict[str, Any], check: Dict[str, Any]) -> bool:
    """Относится ли результат проверки к этому же файлу (имя, размер и время изменения)."""
    if not check or not identity:
        return False
    if str(check.get("file") or "") != str(identity.get("file") or ""):
        return False
    if check.get("file_size") is None or check.get("file_mtime") is None:
        return False          # старая запись без примет: доверять ей нельзя
    return (int(check["file_size"]) == int(identity["file_size"] or -1)
            and abs(float(check["file_mtime"]) - float(identity["file_mtime"] or -1)) < 0.01)


def last_check_for(identity: Dict[str, Any], checks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Последняя проверка именно этой копии, если она была."""
    for check in checks:
        if same_file(identity, check):
            return check
    return None


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
                      "mtime": stat.st_mtime,
                      "age_hours": round(max(0.0, (now - stat.st_mtime) / 3600.0), 2),
                      "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime,
                                                                     datetime.timezone.utc).isoformat()})
    # Сортировка по точному времени изменения, а не по округлённому возрасту: две копии одной минуты
    # иначе вставали бы в произвольном порядке, и «самой свежей» могла оказаться не та (P15 L01)
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def verify_backup(path: str, deep: bool = True, deadline: Optional[float] = None) -> Dict[str, Any]:
    """Открывает копию и проверяет, что из неё действительно можно поднять рабочую базу.

    Проверяются: целостность файла, поддерживаемая версия схемы, наличие обязательных таблиц и колонок,
    и то, что копия выдерживает запросы, которые делает само приложение. Файл SQLite с одной строкой
    товаров пригодной копией не считается (P15 L02). Вся работа ограничена по времени (L03).
    """
    started = time.monotonic()
    limit = deadline if deadline is not None else _deadline()
    result: Dict[str, Any] = {"path": path, "name": os.path.basename(path), "ok": False,
                              "integrity": None, "schema_version": None, "counts": {},
                              "missing_tables": [], "missing_columns": {}, "error": None}
    conn = None
    try:
        if not os.path.exists(path):
            result["error"] = "файла нет"
            return result
        result["size_bytes"] = os.path.getsize(path)
        result.update(file_identity(path))
        # Открываем только на чтение: проверка не должна менять саму копию
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        _guard_connection(conn, limit)
        check = "integrity_check" if deep else "quick_check"
        result["integrity"] = conn.execute(f"PRAGMA {check}").fetchone()[0]
        if result["integrity"] != "ok":
            result["error"] = f"проверка целостности: {result['integrity']}"
            return result
        _check_deadline(limit)

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        result["missing_tables"] = [t for t in REQUIRED_TABLES if t not in tables]
        if result["missing_tables"]:
            result["error"] = "в копии нет обязательных таблиц: " + ", ".join(result["missing_tables"])
            return result

        row = conn.execute("SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()
        version = int(row[0]) if row and str(row[0]).isdigit() else None
        result["schema_version"] = version
        supported = supported_schema_versions()
        if version is None:
            result["error"] = "в копии не записана версия схемы"
            return result
        if version not in supported:
            result["error"] = (f"версия схемы {version} не поддерживается "
                               f"(приложение работает с {sorted(supported)})")
            return result

        missing_columns = {}
        for table, required in REQUIRED_COLUMNS.items():
            _check_deadline(limit)
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            absent = [c for c in required if c not in columns]
            if absent:
                missing_columns[table] = absent
        result["missing_columns"] = missing_columns
        if missing_columns:
            result["error"] = "не хватает колонок: " + "; ".join(
                f"{table} → {', '.join(cols)}" for table, cols in missing_columns.items())
            return result

        for table in ("products", "alerts", "users", "watches"):
            _check_deadline(limit)
            result["counts"][table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not result["counts"].get("products"):
            result["error"] = "в копии нет товаров — восстанавливать из неё нечего"
            return result
        result["ok"] = True
        return result
    except VerificationTimeout as e:
        result["error"] = str(e)
        result["timed_out"] = True
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

    limit = _deadline()
    outcome["identity"] = file_identity(source)
    temp_dir = tempfile.mkdtemp(prefix="kzph-restore-")
    temp_db = os.path.join(temp_dir, "restored.db")
    try:
        _copy_with_deadline(source, temp_db, limit)
        verified = verify_backup(temp_db, deep=True, deadline=limit)
        outcome["checks"] = verified
        if not verified["ok"]:
            outcome["error"] = verified["error"]
            outcome["timed_out"] = verified.get("timed_out", False)
            return outcome
        # Восстановленная база должна выдерживать запросы, которые делает приложение, а не просто открываться
        conn = sqlite3.connect(temp_db, timeout=10)
        _guard_connection(conn, limit)
        queries: Dict[str, Any] = {}
        try:
            for label, sql in SMOKE_QUERIES:
                _check_deadline(limit)
                try:
                    # Успех — что запрос выполнился на этой схеме. Пустая таблица не поломка:
                    # у нового сервиса может не быть ни одного пользователя или наблюдения.
                    conn.execute(sql).fetchone()
                    queries[label] = True
                except sqlite3.Error as e:
                    queries[label] = f"{type(e).__name__}: {e}"
        finally:
            conn.close()
        outcome["queries"] = queries
        failed = [label for label, ok in queries.items() if ok is not True]
        outcome["sample_readable"] = not failed
        outcome["restored"] = not failed
        if failed:
            outcome["error"] = "восстановленная база не отвечает на запросы: " + ", ".join(failed)
        return outcome
    except VerificationTimeout as e:
        outcome["error"] = str(e)
        outcome["timed_out"] = True
        return outcome
    except (OSError, sqlite3.DatabaseError) as e:
        outcome["error"] = f"{type(e).__name__}: восстановить не удалось"
        return outcome
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)   # временная база не остаётся на диске
        outcome["duration_sec"] = round(time.monotonic() - started, 2)


def _copy_with_deadline(source: str, dest: str, deadline: float, chunk: int = 8 * 1024 * 1024) -> None:
    """Копирование кусками: между кусками проверяется время, поэтому предел действует и на большой файл."""
    with open(source, "rb") as src, open(dest, "wb") as dst:
        while True:
            _check_deadline(deadline)
            block = src.read(chunk)
            if not block:
                break
            dst.write(block)


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
                         now: Optional[datetime.datetime] = None,
                         newest: Optional[Dict[str, Any]] = None) -> bool:
    """Пора ли проверять копию.

    Проверяем, если самой свежей копии проверка ещё не касалась (новый или подменённый файл — P15 L01),
    либо если её проверка была больше суток назад. Суточная пауза не должна прятать новую копию.
    """
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    checks = checks if checks is not None else database.backup_checks(limit=20)
    backups = list_backups()
    newest = newest if newest is not None else (backups[0] if backups else None)
    if not newest:
        return not checks          # копий нет: проверять нечего, но и прятать это не нужно

    identity = file_identity(newest["path"])
    own_check = last_check_for(identity, checks or [])
    if not own_check:
        return True                # эту копию ещё никто не проверял
    try:
        last = datetime.datetime.fromisoformat(own_check["checked_at"])
    except (TypeError, ValueError, KeyError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    return (moment - last).total_seconds() >= VERIFY_INTERVAL_HOURS * 3600


# Идущая проверка: пока прежняя не завершилась, новую не начинаем, иначе зависшее чтение размножится
_running = threading.Lock()
_running_thread: Optional[threading.Thread] = None


def _bounded(work, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Выполняет работу с хранилищем копий в отдельном потоке и ждёт её не дольше предела (P15 L03).

    Под ограничение попадает **всё**: перечисление каталога, чтение метаданных, выбор копии, копирование и
    проверка. Зависнуть может уже listdir на сломанном диске, поэтому ничего из этого не делается в потоке
    вызывающего.

    Чего здесь нет и не может быть: средствами Python нельзя прервать уже начатое блокирующее чтение.
    Поэтому вызывающий освобождается вовремя, результат помечается непройденной проверкой, а зависший поток
    остаётся фоновым — он не держит блокировок рабочей базы, убирает свою временную папку и не даёт начать
    новую проверку, пока не завершится.
    """
    global _running_thread
    limit = VERIFY_TIMEOUT_SECONDS if timeout is None else timeout
    if _running_thread is not None and _running_thread.is_alive():
        return {"restored": False, "error": "предыдущая проверка ещё не завершилась", "checks": {},
                "timed_out": True, "still_running": True, "identity": {}}

    box: Dict[str, Any] = {"identity": {}}

    def run():
        with _running:
            try:
                box["result"] = work(box)
            except Exception as e:                  # поток проверки не должен падать молча
                box["result"] = {"restored": False, "error": f"{type(e).__name__}", "checks": {}}

    worker = threading.Thread(target=run, name="backup-verify", daemon=True)
    _running_thread = worker
    worker.start()
    worker.join(timeout=limit)
    if worker.is_alive():
        # Идентичность файла берётся из того, что поток успел узнать: иначе отказ не привязать к копии
        return {"restored": False, "checks": {}, "timed_out": True, "still_running": True,
                "identity": dict(box.get("identity") or {}),
                "error": f"проверка не уложилась в {limit:g} с и продолжается фоном"}
    result = box.get("result") or {"restored": False, "error": "проверка не дала результата", "checks": {}}
    result.setdefault("identity", box.get("identity") or {})
    return result


def restore_rehearsal_bounded(path: Optional[str] = None,
                              timeout: Optional[float] = None) -> Dict[str, Any]:
    """Репетиция восстановления с твёрдым пределом для вызывающего (P15 L03)."""
    def work(box):
        target = path
        if target is None:
            backups = list_backups()               # перечисление тоже под ограничением
            target = backups[0]["path"] if backups else None
        if target:
            box["identity"] = file_identity(target)
        return restore_rehearsal(target)

    return _bounded(work, timeout)


def verify_backups_if_due(now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Периодическая самопроверка: раз в сутки развернуть свежую копию и убедиться, что она пригодна.

    Всё, что трогает диск, — включая решение «пора ли» — выполняется под общим ограничением времени, потому
    что зависнуть может уже перечисление каталога (P15 L03). Ошибка самой проверки не ломает обслуживание:
    результат записывается, исключение наружу не уходит. Пульс компонента (P14) ставится в любом случае.
    """
    import database
    import environment

    def work(box):
        if not due_for_verification(now=now):
            return {"skipped": True}
        backups = list_backups()
        target = backups[0]["path"] if backups else None
        if target:
            box["identity"] = file_identity(target)
        return restore_rehearsal(target)

    result = _bounded(work)
    if result.get("skipped"):
        return None

    try:
        identity = result.get("identity") or {}
        detail = result.get("error") or (
            f"схема {result.get('checks', {}).get('schema_version')}, "
            f"товаров {result.get('checks', {}).get('counts', {}).get('products')}")
        database.record_backup_check("restore", bool(result.get("restored")),
                                     file=identity.get("file"), detail=detail,
                                     duration_sec=result.get("duration_sec"),
                                     file_size=identity.get("file_size"),
                                     file_mtime=identity.get("file_mtime"), now=now)
    except Exception:                               # учёт не должен ронять обслуживание
        pass
    finally:
        environment.heartbeat(environment.BACKUP,
                              "проверка пройдена" if result.get("restored") else "проверка не прошла")
    return result
