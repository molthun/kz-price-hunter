"""Снимок состояния прода для человека и для агента.

Зачем отдельно от `/monitoring`: та страница показывает качество данных и живёт за
сессией администратора в Telegram, то есть агент в неё не попадёт. Здесь — то, что
нужно, чтобы за один запрос понять, почему приложение тормозит или не отвечает:
режим журнала базы, простой цикла событий, размер пула потоков, глубина очереди
записи, свежие ошибки и готовый список проблем.

Разбор снимка начинается с ключа `problems`: он уже содержит выводы, а не сырые
числа. Пустой список означает, что известных признаков беды нет.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional


# Пороги, по которым снимок называет проблему. Подобраны по замерам прода 28.10.2026:
# в норме цикл событий отвечает за десятки миллисекунд, а очередь записи держится
# в пределах десятков задач и разгружается между волнами.
LOOP_LAG_WARN_MS = 250.0
LOOP_LAG_CRITICAL_MS = 1000.0
QUEUE_WARN = 200
QUEUE_CRITICAL = 1000
ERRORS_WARN_PER_HOUR = 20
RECENT_ERRORS_LIMIT = 20
FRESH_START_SECONDS = 300

_STARTED_AT = time.time()


def uptime_seconds() -> float:
    return max(0.0, time.time() - _STARTED_AT)


def _rss_mb() -> Optional[float]:
    """Потребление памяти процессом. Без psutil: /proc в Linux, getrusage в macOS."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # В Linux ru_maxrss в килобайтах, в macOS — в байтах
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:
        return None


def _file_mb(path) -> Optional[float]:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 1)
    except OSError:
        return None


def database_snapshot(connect: Callable[[], sqlite3.Connection], db_path) -> Dict[str, Any]:
    """Режим работы базы и её объём. Журнальный режим здесь — главный ответ:
    вне WAL читатели ждут писателей, и это видно по времени ответа страниц."""
    snap: Dict[str, Any] = {"path": str(db_path), "size_mb": _file_mb(db_path),
                            "wal_mb": _file_mb(str(db_path) + "-wal")}
    try:
        conn = connect()
    except Exception as e:
        snap["error"] = f"{type(e).__name__}: {e}"
        return snap
    try:
        def pragma(name: str) -> Any:
            try:
                row = conn.execute(f"PRAGMA {name};").fetchone()
                return row[0] if row else None
            except sqlite3.Error:
                return None

        snap["journal_mode"] = pragma("journal_mode")
        snap["synchronous"] = pragma("synchronous")
        snap["busy_timeout_ms"] = pragma("busy_timeout")
        snap["page_size"] = pragma("page_size")
        counts: Dict[str, Any] = {}
        for table in ("products", "offers", "sellers", "alerts", "watches"):
            try:
                counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
        snap["rows"] = counts
        try:
            snap["schema_version"] = conn.execute(
                "SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()[0]
        except (sqlite3.Error, TypeError):
            snap["schema_version"] = None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return snap


def recent_errors(connect: Callable[[], sqlite3.Connection], hours: float = 1.0,
                  limit: int = RECENT_ERRORS_LIMIT) -> Dict[str, Any]:
    """Свежие системные ошибки из телеметрии: что именно ломается прямо сейчас.

    Отбор идёт по `created_at` — это epoch-секунды, выставляемые самой базой, поэтому
    фильтр не зависит от формата текстовой метки и часового пояса процесса.
    """
    cutoff = time.time() - hours * 3600
    result: Dict[str, Any] = {"window_hours": hours, "total": 0, "by_component": {}, "last": []}
    try:
        conn = connect()
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    try:
        rows = conn.execute(
            "SELECT timestamp, component, type, severity, message FROM telemetry_events "
            "WHERE severity IN ('ERROR', 'CRITICAL') AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?", (cutoff, limit)).fetchall()
        result["last"] = [{"at": r[0], "component": r[1], "type": r[2], "severity": r[3],
                           "message": (r[4] or "")[:300]} for r in rows]
        totals = conn.execute(
            "SELECT component, count(*) FROM telemetry_events "
            "WHERE severity IN ('ERROR', 'CRITICAL') AND created_at >= ? GROUP BY component",
            (cutoff,)).fetchall()
        result["by_component"] = {r[0]: r[1] for r in totals}
        result["total"] = sum(result["by_component"].values())
    except sqlite3.Error as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return result


def detect_problems(snapshot: Dict[str, Any]) -> List[Dict[str, str]]:
    """Переводит числа снимка в список проблем. Это первое, что читает агент."""
    problems: List[Dict[str, str]] = []

    def add(severity: str, code: str, what: str, why: str) -> None:
        problems.append({"severity": severity, "code": code, "problem": what, "meaning": why})

    db = snapshot.get("database", {})
    mode = str(db.get("journal_mode") or "").lower()
    if db.get("error"):
        add("critical", "db_unreachable", f"База не отвечает: {db['error']}",
            "Запросы, которым нужна база, будут падать или ждать до таймаута.")
    elif mode and mode != "wal":
        add("critical", "db_not_wal", f"Журнальный режим базы — {mode}, а не wal",
            "Вне WAL читатели ждут писателей: страницы будут залипать во время обхода.")

    runtime = snapshot.get("runtime", {})
    lag = runtime.get("event_loop_lag_ms")
    if isinstance(lag, (int, float)):
        if lag >= LOOP_LAG_CRITICAL_MS:
            add("critical", "loop_stalled", f"Цикл событий простаивает {lag:.0f} мс",
                "Сервер не успевает отвечать даже там, где не нужна база: процессор занят.")
        elif lag >= LOOP_LAG_WARN_MS:
            add("warning", "loop_lag", f"Задержка цикла событий {lag:.0f} мс",
                "Ответы заметно замедляются под нагрузкой обхода.")

    queue = snapshot.get("queue", {})
    size = queue.get("size")
    if queue.get("worker_running") is False:
        add("critical", "writer_down", "Воркер записи в базу не работает",
            "Собранные товары копятся в очереди и не попадают в базу.")
    if isinstance(size, int):
        if size >= QUEUE_CRITICAL:
            add("critical", "queue_overflow", f"Очередь записи — {size} задач",
                "Писатель не справляется; при вытеснении Redis часть данных будет потеряна.")
        elif size >= QUEUE_WARN:
            add("warning", "queue_backlog", f"Очередь записи — {size} задач",
                "Писатель отстаёт от скраперов, данные на витрине запаздывают.")

    errors = snapshot.get("errors", {})
    total = errors.get("total")
    if isinstance(total, int) and total >= ERRORS_WARN_PER_HOUR:
        top = max(errors.get("by_component", {}).items(), key=lambda kv: kv[1], default=("?", 0))
        add("warning", "error_rate", f"{total} ошибок за час, больше всего в «{top[0]}»",
            "Смотрите errors.last: там последние сообщения с временем и компонентом.")

    service = snapshot.get("service", {})
    up = service.get("uptime_seconds")
    if isinstance(up, (int, float)) and up < FRESH_START_SECONDS:
        add("info", "fresh_start", f"Процесс поднят {up:.0f} с назад",
            "Метрики ещё не накопились; возможен недавний перезапуск или выкладка.")

    return problems


def collect(*, version_info: Dict[str, Any], connect: Callable[[], sqlite3.Connection], db_path,
            queue: Dict[str, Any], scan: Dict[str, Any], thread_pool_size: Optional[int],
            event_loop_lag_ms: Optional[float], now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Полный снимок. Значения, которые нельзя получить синхронно (задержка цикла
    событий, состояние очереди), передаются вызывающей стороной."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    snapshot: Dict[str, Any] = {
        "generated_at": moment.isoformat(),
        "service": {
            "version": version_info.get("version"),
            "release_date": version_info.get("release_date"),
            "uptime_seconds": round(uptime_seconds(), 1),
            "started_at": datetime.datetime.fromtimestamp(_STARTED_AT, tz=moment.tzinfo).isoformat()
            if moment.tzinfo else datetime.datetime.fromtimestamp(_STARTED_AT).isoformat(),
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "cpu_count": os.cpu_count(),
            "thread_pool_size": thread_pool_size,
            "threads_alive": threading.active_count(),
            "rss_mb": _rss_mb(),
            "event_loop_lag_ms": round(event_loop_lag_ms, 1) if isinstance(event_loop_lag_ms, (int, float)) else None,
        },
        "database": database_snapshot(connect, db_path),
        "queue": dict(queue),
        "scan": dict(scan),
        "errors": recent_errors(connect),
    }
    snapshot["problems"] = detect_problems(snapshot)
    snapshot["status"] = ("critical" if any(p["severity"] == "critical" for p in snapshot["problems"])
                          else "warning" if any(p["severity"] == "warning" for p in snapshot["problems"])
                          else "ok")
    return snapshot


def _esc(value: Any) -> str:
    from html import escape
    return escape("—" if value is None else str(value))


def _rows_html(data: Dict[str, Any]) -> str:
    return "".join(
        f"<tr><th>{_esc(k)}</th><td>{_esc(v if not isinstance(v, dict) else ', '.join(f'{a}={b}' for a, b in v.items()))}</td></tr>"
        for k, v in data.items())


def render_html(snapshot: Dict[str, Any]) -> str:
    """Та же картина, что в JSON, но читаемая глазами. Без внешних файлов и скриптов:
    страница должна открываться даже когда всё остальное плохо."""
    status = snapshot.get("status", "ok")
    colors = {"ok": "#16a34a", "warning": "#d97706", "critical": "#dc2626"}
    titles = {"ok": "Известных проблем нет", "warning": "Есть предупреждения", "critical": "Есть критические проблемы"}
    problems = snapshot.get("problems") or []
    problems_html = "".join(
        f"<li class='{_esc(p['severity'])}'><b>{_esc(p['problem'])}</b><br><span>{_esc(p['meaning'])}</span>"
        f" <code>{_esc(p['code'])}</code></li>" for p in problems) or "<li class='ok'>Признаков беды нет</li>"
    errors = snapshot.get("errors", {})
    errors_html = "".join(
        f"<tr><td>{_esc(e['at'])}</td><td>{_esc(e['component'])}</td><td>{_esc(e['type'])}</td>"
        f"<td>{_esc(e['message'])}</td></tr>" for e in errors.get("last", []))
    sections = "".join(
        f"<h2>{_esc(name)}</h2><table>{_rows_html(snapshot.get(key, {}))}</table>"
        for key, name in (("service", "Сервис"), ("runtime", "Процесс"), ("database", "База"),
                          ("queue", "Очередь записи"), ("scan", "Обход")))
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Диагностика KZ Price Hunter</title><meta name="robots" content="noindex,nofollow">
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px;background:#0f172a;color:#e2e8f0}}
h1{{margin:0 0 4px;font-size:20px}} h2{{margin:28px 0 8px;font-size:15px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}}
.status{{display:inline-block;padding:6px 14px;border-radius:999px;color:#fff;font-weight:700;background:{colors.get(status, '#64748b')}}}
table{{border-collapse:collapse;width:100%;max-width:900px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #1e293b;vertical-align:top}}
th{{color:#94a3b8;font-weight:500;width:220px}}
ul{{list-style:none;padding:0;max-width:900px}}
li{{padding:10px 14px;border-left:4px solid #475569;background:#1e293b;margin-bottom:8px;border-radius:0 6px 6px 0}}
li.critical{{border-color:#dc2626}} li.warning{{border-color:#d97706}} li.info{{border-color:#0284c7}} li.ok{{border-color:#16a34a}}
li span{{color:#94a3b8}} code{{color:#64748b;font-size:12px}}
p.hint{{color:#64748b;max-width:900px}}
</style></head><body>
<h1>Диагностика KZ Price Hunter</h1>
<p><span class="status">{_esc(titles.get(status, status))}</span> &nbsp; снимок {_esc(snapshot.get('generated_at'))}</p>
<p class="hint">Машиночитаемый тот же снимок — <code>/api/diag</code>. Начинайте с проблем: они уже содержат вывод, а не сырые числа.</p>
<h2>Проблемы</h2><ul>{problems_html}</ul>
{sections}
<h2>Последние ошибки ({_esc(errors.get('total'))} за {_esc(errors.get('window_hours'))} ч)</h2>
<table><tr><th>Время</th><th>Компонент</th><th>Тип</th><th>Сообщение</th></tr>{errors_html or '<tr><td colspan=4>Ошибок нет</td></tr>'}</table>
</body></html>"""
