"""Monitoring Center V1 (этап P03): состояние магазинов, обходов, сервисов и инциденты — только чтение.

Источники: shop_scans (итог обхода магазина), source_scans (качество категорий, P02), telemetry_events и
telemetry_http_aggregates (P01), notification_outbox, scan_state процесса.

Главное правило — нет ложного зелёного: без наблюдений статус «unknown», а не «healthy».
Статусы: healthy, limited, degraded, offline, empty, unknown, disabled.
"""
import datetime
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import data_quality

HEALTHY, LIMITED, DEGRADED, OFFLINE, EMPTY, UNKNOWN, DISABLED = (
    "healthy", "limited", "degraded", "offline", "empty", "unknown", "disabled")
# Порядок «хуже → лучше» для сводок
STATUS_ORDER = [OFFLINE, DEGRADED, EMPTY, LIMITED, UNKNOWN, HEALTHY, DISABLED]

OFFLINE_AFTER_HOURS = data_quality.AGING_HOURS   # нет полного обхода дольше 72 ч при последнем провале
OFFLINE_CONSECUTIVE_FAILURES = 3
SECTION_WINDOW_HOURS = 24
ERROR_SHARE_DEGRADED = 0.2
INCIDENT_WINDOW_DAYS = 7
INCIDENT_QUIET_HOURS = 1                           # компонентный инцидент считается закрытым после часа тишины
HISTORY_SCANS = 15
EVENTS_LIMIT_MAX = 500
INCIDENTS_LIMIT = 200
SHOP_INCIDENTS_LIMIT = 50

UTC = datetime.timezone.utc
_EVENT_TS = "%Y-%m-%dT%H:%M:%S.%fZ"


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def _parse(ts: Any) -> Optional[datetime.datetime]:
    return data_quality._parse_time(ts)


def _hours_since(ts: Any, now: datetime.datetime) -> Optional[float]:
    dt = _parse(ts)
    return None if dt is None else max(0.0, (now - dt).total_seconds() / 3600.0)


def _conn():
    import database
    conn = sqlite3.connect(database.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _event_cutoff(now: datetime.datetime, hours: float) -> str:
    return (now - datetime.timedelta(hours=hours)).strftime(_EVENT_TS)


def _loads(value: Any) -> Dict[str, Any]:
    try:
        data = json.loads(value) if value else {}
        return data if isinstance(data, dict) else {"value": data}
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------------------------- статусы (чистые)

def shop_status(enabled: bool, scan: Optional[Dict[str, Any]], quality: Optional[str],
                now: Optional[datetime.datetime] = None) -> Tuple[str, str]:
    """Статус магазина и короткая причина по последнему итогу обхода, качеству (P02) и свежести."""
    now = now or _now()
    if not enabled:
        return DISABLED, "Магазин выключен в настройках"
    scan = scan or {}
    status = scan.get("status")
    if not scan or status in (None, "unknown") or (status == "running" and not scan.get("last_success_at")
                                                   and not scan.get("last_items")):
        return UNKNOWN, "Нет завершённых обходов — состояние неизвестно"
    since_full = _hours_since(scan.get("last_success_at"), now)
    failures = int(scan.get("failure_count") or 0)
    error = scan.get("last_error") or ""
    if status == "failed":
        if since_full is None or since_full > OFFLINE_AFTER_HOURS or failures >= OFFLINE_CONSECUTIVE_FAILURES:
            full = "полного обхода не было" if since_full is None else f"полный обход {since_full:.0f} ч назад"
            return OFFLINE, f"Обход не удаётся ({failures} подряд), {full}: {error}".strip(": ")
        return DEGRADED, f"Последний обход не удался: {error}".strip(": ")
    if status == "partial" or quality in (data_quality.DEGRADED, data_quality.WARNING):
        return DEGRADED, error or f"Качество последнего обхода: {quality}"
    if int(scan.get("last_items") or 0) == 0 and status in ("complete", "limited"):
        return EMPTY, "Последний обход не вернул товаров"
    freshness = data_quality.freshness(scan.get("last_success_at"), now)["freshness"]
    if status == "limited":
        return LIMITED, "Каталог собран не полностью (ограничение страниц или конец не подтверждён)"
    if status == "running":
        return LIMITED if freshness == data_quality.FRESH else DEGRADED, "Идёт обход"
    if freshness in (data_quality.AGING, data_quality.STALE):
        return DEGRADED, f"Полный обход {since_full:.0f} ч назад — данные устаревают"
    return HEALTHY, "Последний обход полный, данные свежие"


def rate_status(total: int, errors: int, *, disabled: bool = False, disabled_reason: str = "") -> Tuple[str, str]:
    """Статус сервиса по событиям за окно: нет событий — unknown, доля ошибок > 20 % — degraded."""
    if disabled:
        return DISABLED, disabled_reason or "Выключено"
    if total == 0:
        return UNKNOWN, f"Нет событий за {SECTION_WINDOW_HOURS} ч"
    share = errors / total
    if share > ERROR_SHARE_DEGRADED:
        return DEGRADED, f"Ошибок {errors} из {total} ({share:.0%})"
    return HEALTHY, f"{total} событий, ошибок {errors}"


def worst(statuses: Iterable[str]) -> str:
    statuses = [s for s in statuses if s != DISABLED]
    if not statuses:
        return UNKNOWN
    return min(statuses, key=STATUS_ORDER.index)


# ---------------------------------------------------------------------------------------------- магазины

def _latest_categories(conn, shop_keys: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Последний итог каждой категории (источника) магазина из source_scans."""
    if not shop_keys:
        return {}
    marks = ",".join("?" * len(shop_keys))
    rows = conn.execute(f"""
        SELECT s.* FROM source_scans s
        JOIN (SELECT shop_key, source_url, MAX(id) AS id FROM source_scans WHERE shop_key IN ({marks})
              GROUP BY shop_key, source_url) l ON s.id = l.id
        ORDER BY s.shop_key, s.category""", shop_keys).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["shop_key"]].append(dict(r))
    return out


def _http_by_shop(conn, now: datetime.datetime) -> Dict[str, Dict[str, Any]]:
    """HTTP за 24 ч по магазину (имя магазина из контекста обхода): суммы по часовым бакетам.

    p95 за сутки из часовых p95 не выводится (среднее из p95 некорректно) — показывается худший часовой p95.
    """
    since = (now - datetime.timedelta(hours=SECTION_WINDOW_HOURS)).strftime(data_quality_hour_format())
    rows = conn.execute("""SELECT shop, host, total_requests, status_2xx, status_4xx, status_5xx, status_429,
            timeouts, connection_errors, errors, cooldown_rejections, bytes_total, latency_sum_ms, latency_p95_ms,
            status_codes FROM telemetry_http_aggregates WHERE bucket_type='hour' AND bucket_start >= ?""",
                        (since,)).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        agg = out.setdefault(r["shop"] or "", {"requests": 0, "errors": 0, "timeouts": 0, "connection_errors": 0,
                                              "status_429": 0, "status_4xx": 0, "status_5xx": 0,
                                              "cooldown_rejections": 0, "bytes": 0, "latency_sum_ms": 0.0,
                                              "worst_hour_p95_ms": 0.0, "status_codes": defaultdict(int),
                                              "hosts": set()})
        agg["requests"] += r["total_requests"]
        for k in ("errors", "timeouts", "connection_errors", "status_429", "status_4xx", "status_5xx",
                  "cooldown_rejections"):
            agg[k] += r[k] or 0
        agg["bytes"] += r["bytes_total"] or 0
        agg["latency_sum_ms"] += r["latency_sum_ms"] or 0
        agg["worst_hour_p95_ms"] = max(agg["worst_hour_p95_ms"], r["latency_p95_ms"] or 0)
        agg["hosts"].add(r["host"])
        for code, n in _loads(r["status_codes"]).items():
            agg["status_codes"][code] += int(n or 0)
    for agg in out.values():
        agg["avg_latency_ms"] = round(agg["latency_sum_ms"] / agg["requests"], 1) if agg["requests"] else None
        agg["status_codes"] = dict(agg["status_codes"])
        agg["hosts"] = sorted(agg["hosts"])
        del agg["latency_sum_ms"]
    return out


def data_quality_hour_format() -> str:
    from telemetry import BUCKET_FORMATS
    return BUCKET_FORMATS["hour"]


def shops_overview(registry: Dict[str, Tuple[str, int]], enabled: Iterable[str],
                   now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Сводка по всем магазинам реестра: {shop_key: (название, число категорий)}."""
    import database
    now = now or _now()
    enabled = set(enabled)
    keys = list(registry)
    scans = database.get_shop_scans()
    quality = database.get_last_source_quality(keys)
    with closing(_conn()) as conn:
        cats = _latest_categories(conn, keys)
        http = _http_by_shop(conn, now)
    result = []
    for key in keys:
        name, n_categories = registry[key]
        scan = scans.get(key)
        q = (quality.get(key) or {}).get("quality")
        status, reason = shop_status(key in enabled, scan, q, now)
        shop_cats = cats.get(key, [])
        baseline = sum(c["baseline"] or 0 for c in shop_cats if c["baseline"]) or None
        h = http.get(name) or {}
        result.append({
            "shop_key": key, "name": name, "status": status, "reason": reason,
            "enabled": key in enabled,
            "scan_status": (scan or {}).get("status", "unknown"),
            "last_attempt_at": (scan or {}).get("last_attempt_at"),
            "last_success_at": (scan or {}).get("last_success_at"),
            "last_items": (scan or {}).get("last_items") or 0,
            "last_error": (scan or {}).get("last_error"),
            "failure_count": (scan or {}).get("failure_count") or 0,
            "next_retry_at": (scan or {}).get("next_retry_at"),
            "freshness": data_quality.freshness((scan or {}).get("last_success_at"), now)["freshness"],
            "quality": q or "unknown",
            "categories_total": n_categories,
            "categories_observed": len(shop_cats),
            "categories_problem": sum(1 for c in shop_cats if c["quality"] in ("degraded", "failed", "warning")),
            "baseline_items": baseline,
            "http_requests_24h": h.get("requests", 0),
            "http_errors_24h": h.get("errors", 0) + h.get("status_4xx", 0) + h.get("status_5xx", 0) + h.get("status_429", 0),
            "http_worst_p95_ms": h.get("worst_hour_p95_ms") or None,
        })
    result.sort(key=lambda s: (STATUS_ORDER.index(s["status"]), s["name"].lower()))
    return result


def shop_detail(key: str, name: str, categories: List[Dict[str, Any]], enabled: bool,
                now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Карточка магазина: итоги обходов, категории с качеством и нормой, HTTP, история, события, инциденты."""
    import database
    now = now or _now()
    scan = database.get_shop_scans().get(key)
    q = (database.get_last_source_quality([key]).get(key) or {})
    status, reason = shop_status(enabled, scan, q.get("quality"), now)
    names = {c["url"]: c["name"] for c in categories}
    with closing(_conn()) as conn:
        latest = _latest_categories(conn, [key]).get(key, [])
        http = _http_by_shop(conn, now).get(name) or {}
        hist_rows = conn.execute("""SELECT scan_id, MIN(started_at) AS started_at, MAX(finished_at) AS finished_at,
                COUNT(*) AS categories, SUM(valid) AS valid, SUM(received) AS received, SUM(rejected) AS rejected,
                SUM(duplicates) AS duplicates, GROUP_CONCAT(quality) AS qualities
            FROM source_scans WHERE shop_key = ? GROUP BY COALESCE(scan_id, id)
            ORDER BY finished_at DESC LIMIT ?""", (key, HISTORY_SCANS)).fetchall()
        events = [dict(r) for r in conn.execute("""SELECT timestamp, type, severity, category, scan_id, message, data_json
            FROM telemetry_events WHERE shop = ? OR data_json LIKE ? ORDER BY timestamp DESC LIMIT 50""",
                                                 (name, f'%"shop_key": "{key}"%')).fetchall()]
    observed = {c["source_url"] for c in latest}
    category_rows = [{
        "name": c["category"] or names.get(c["source_url"]) or c["source_url"],
        "source_url": c["source_url"], "quality": c["quality"], "kind": c["kind"], "reason": c["reason"],
        "received": c["received"], "valid": c["valid"], "rejected": c["rejected"],
        "duplicates": c.get("duplicates", 0), "baseline": c["baseline"], "baseline_basis": c["baseline_basis"],
        "finished_at": c["finished_at"],
    } for c in latest] + [{
        "name": cat["name"], "source_url": cat["url"], "quality": "unknown", "kind": None,
        "reason": "Категория ещё не обходилась после включения мониторинга качества", "received": None,
        "valid": None, "rejected": None, "duplicates": None, "baseline": None, "baseline_basis": None,
        "finished_at": None,
    } for cat in categories if cat["url"] not in observed]
    history = [{
        "scan_id": r["scan_id"], "started_at": r["started_at"], "finished_at": r["finished_at"],
        "categories": r["categories"], "valid": r["valid"], "received": r["received"],
        "rejected": r["rejected"], "duplicates": r["duplicates"],
        "quality": worst_quality((r["qualities"] or "").split(",")),
    } for r in hist_rows]
    for e in events:
        e["data"] = _loads(e.pop("data_json"))
    return {
        "shop_key": key, "name": name, "status": status, "reason": reason, "enabled": enabled,
        "scan": scan or {}, "freshness": data_quality.freshness((scan or {}).get("last_success_at"), now),
        "last_quality": q, "categories": category_rows, "http": http, "history": history, "events": events,
        "incidents": [i for i in incidents(now=now, shop_keys={name: key})
                      if i.get("shop_key") == key or i.get("shop") in (name, key)][:SHOP_INCIDENTS_LIMIT],
    }


def worst_quality(values: Iterable[str]) -> str:
    order = ["failed", "degraded", "warning", "unknown", "ok"]
    values = [v for v in values if v in order]
    return min(values, key=order.index) if values else "unknown"


# ---------------------------------------------------------------------------------------------- разделы

def _events_since(conn, types: Iterable[str], hours: float, now: datetime.datetime) -> List[Dict[str, Any]]:
    types = list(types)
    marks = ",".join("?" * len(types))
    rows = conn.execute(f"""SELECT timestamp, type, severity, component, shop, category, scan_id, message, data_json
        FROM telemetry_events WHERE type IN ({marks}) AND timestamp >= ? ORDER BY timestamp DESC""",
                        [*types, _event_cutoff(now, hours)]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = _loads(d.pop("data_json"))
        out.append(d)
    return out


def scanning_section(scan_state: Dict[str, Any], expected_interval_sec: int,
                     now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    from telemetry import EVENT_SCAN_END
    now = now or _now()
    with closing(_conn()) as conn:
        ends = _events_since(conn, [EVENT_SCAN_END], 24 * INCIDENT_WINDOW_DAYS, now)[:20]
    last = ends[0] if ends else None
    limit_h = 2 * max(expected_interval_sec, 3 * 3600) / 3600.0
    if scan_state.get("is_running"):
        status, reason = HEALTHY, "Идёт обход"
    elif last is None:
        status, reason = UNKNOWN, "Нет завершённых обходов в журнале"
    else:
        age = _hours_since(last["timestamp"], now) or 0
        outcome = last["data"].get("outcome")
        if age > limit_h:
            status, reason = OFFLINE, f"Последний обход завершился {age:.0f} ч назад (ожидалось не реже {limit_h:.0f} ч)"
        elif outcome in ("failed", "lease_lost", "cancelled"):
            status, reason = DEGRADED, f"Последний обход: {outcome}"
        else:
            status, reason = HEALTHY, f"Последний обход {age:.1f} ч назад: {outcome or 'completed'}"
    return {"status": status, "reason": reason, "state": {k: scan_state.get(k) for k in (
        "is_running", "current_shop", "current_category", "progress_pct", "scan_type", "total_scanned",
        "anomalies_found", "last_completed", "error", "wave_info")},
            "recent_scans": [{"timestamp": e["timestamp"], "scan_id": e["scan_id"], "severity": e["severity"],
                              **{k: e["data"].get(k) for k in ("scan_type", "outcome", "total_scanned",
                                                               "anomalies_found", "duration_sec", "error")}}
                             for e in ends]}


def search_section(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    from telemetry import EVENT_SEARCH_QUERY, calculate_p95
    now = now or _now()
    with closing(_conn()) as conn:
        events = _events_since(conn, [EVENT_SEARCH_QUERY], SECTION_WINDOW_HOURS, now)
    by_source: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    durations = []
    for e in events:
        by_source[e["data"].get("source", "?")][e["data"].get("outcome", "?")] += 1
        if e["data"].get("duration_ms") is not None:
            durations.append(float(e["data"]["duration_ms"]))
    errors = sum(1 for e in events if e["data"].get("outcome") == "error")
    status, reason = rate_status(len(events), errors)
    not_found = sum(1 for e in events if e["data"].get("outcome") == "not_found")
    return {"status": status, "reason": reason, "total": len(events), "errors": errors, "not_found": not_found,
            "by_source": {k: dict(v) for k, v in by_source.items()},
            "p95_ms": calculate_p95(durations) if durations else None,
            "note": "Частые события поиска ограничены 60/мин на источник — это выборка, а не точный счётчик"}


def ai_section(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import config
    from telemetry import EVENT_AI_QUERY
    now = now or _now()
    cfg = config.get_ai_config()
    with closing(_conn()) as conn:
        events = _events_since(conn, [EVENT_AI_QUERY], SECTION_WINDOW_HOURS, now)
    called = [e for e in events if e["data"].get("provider_called")]
    errors = sum(1 for e in called if e["data"].get("outcome") not in ("ok",))
    disabled = not cfg.get("has_ai") or not cfg.get("enabled")
    status, reason = rate_status(len(called), errors, disabled=disabled,
                                 disabled_reason="AI не настроен или выключен в настройках")
    outcomes: Dict[str, int] = defaultdict(int)
    tokens_in = tokens_out = 0
    for e in events:
        outcomes[e["data"].get("outcome", "?")] += 1
        tokens_in += int(e["data"].get("input_tokens") or 0)
        tokens_out += int(e["data"].get("output_tokens") or 0)
    try:
        import ai_service
        calls_today, limit = ai_service.ai_calls_today(), ai_service.DAILY_AI_CALL_LIMIT
    except Exception:
        calls_today, limit = None, None
    return {"status": status, "reason": reason, "provider": cfg.get("provider"), "calls_24h": len(called),
            "errors_24h": errors, "outcomes": dict(outcomes), "input_tokens_24h": tokens_in,
            "output_tokens_24h": tokens_out, "calls_today": calls_today, "daily_limit": limit}


def telegram_section(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import config
    import database
    import notifier
    from telemetry import EVENT_TELEGRAM_ALERT, EVENT_SYSTEM_ERROR
    now = now or _now()
    with closing(_conn()) as conn:
        deliveries = _events_since(conn, [EVENT_TELEGRAM_ALERT], SECTION_WINDOW_HOURS, now)
        sys_errors = [e for e in _events_since(conn, [EVENT_SYSTEM_ERROR], SECTION_WINDOW_HOURS, now)
                      if e["component"] == "telegram"]
    sums: Dict[str, int] = defaultdict(int)
    for e in deliveries:
        for k in ("sent", "cancelled", "retry", "failed", "errors"):
            sums[k] += int(e["data"].get(k) or 0)
    # Попытка отправки — sent, retry (временная ошибка: 429/5xx/сеть), failed (постоянная), errors (исключение);
    # cancelled — не попытка. Ошибки воркера доставки — тоже неуспех: сообщения не уходили (D02)
    unsuccessful = sums["retry"] + sums["failed"] + sums["errors"] + len(sys_errors)
    attempts = sums["sent"] + unsuccessful
    paused = notifier.telegram_paused_for()
    status, reason = rate_status(attempts, unsuccessful,
                                 disabled=not config.get_bot_token(), disabled_reason="TELEGRAM_BOT_TOKEN не задан")
    if status == DEGRADED:
        reason = (f"Не доставлено {unsuccessful} из {attempts}: временных {sums['retry']}, постоянных {sums['failed']}, "
                  f"исключений {sums['errors']}, ошибок воркера {len(sys_errors)}")
    if status not in (DISABLED,) and paused > 0:
        status, reason = DEGRADED, f"Telegram попросил паузу ещё на {paused:.0f} с"
    return {"status": status, "reason": reason, "delivery_24h": dict(sums), "pause_seconds": round(paused),
            "outbox": database.notification_stats(), "worker_errors_24h": len(sys_errors),
            "note": "Без доставок за 24 ч статус unknown: отправлять было нечего или воркер не работает"}


def system_section(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import database
    import sqlite3 as _sqlite
    import sys
    import version
    from telemetry import EVENT_SYSTEM_ERROR, telemetry
    now = now or _now()
    with closing(_conn()) as conn:
        errors = _events_since(conn, [EVENT_SYSTEM_ERROR], SECTION_WINDOW_HOURS, now)
        schema = conn.execute("SELECT value FROM schema_metadata WHERE name='schema_version'").fetchone()
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("products", "telemetry_events", "source_scans", "notification_outbox")}
        active = conn.execute("SELECT COUNT(*) FROM products WHERE is_active = 1").fetchone()[0]
    path = str(database.DB_PATH)
    size = sum(os.path.getsize(p) for p in (path, path + "-wal") if os.path.exists(p))
    thread = getattr(telemetry, "_thread", None)
    flusher_alive = bool(thread and thread.is_alive())
    failed = telemetry.stats.get("failed_flushes", 0)
    if failed or not flusher_alive:
        status = DEGRADED
        reason = "Фоновый сброс телеметрии не запущен" if not flusher_alive else f"Неудачных сбросов телеметрии: {failed}"
    elif errors:
        status, reason = DEGRADED, f"Системных ошибок за 24 ч: {len(errors)}"
    else:
        status, reason = HEALTHY, "Системных ошибок за 24 ч нет"
    return {"status": status, "reason": reason, "version": version.__version__,
            "schema_version": schema[0] if schema else None, "python": sys.version.split()[0],
            "sqlite": _sqlite.sqlite_version, "db_size_bytes": size, "counts": {**counts, "active_products": active},
            "telemetry": {**telemetry.stats, "flusher_alive": flusher_alive}, "errors_24h": len(errors),
            "recent_errors": [{"timestamp": e["timestamp"], "component": e["component"],
                               "where": e["data"].get("where"), "error": e["data"].get("error"),
                               "route": e["data"].get("route")} for e in errors[:20]],
            "note": "Git SHA, uptime и heartbeat компонентов — этап P14"}


# ---------------------------------------------------------------------------------------------- журнал и инциденты

def events(component: Optional[str] = None, severity: Optional[str] = None, event_type: Optional[str] = None,
           shop: Optional[str] = None, scan_id: Optional[str] = None, limit: int = 200,
           before: Optional[str] = None) -> List[Dict[str, Any]]:
    """Журнал событий из БД с фильтрами; severity — минимальный уровень (WARNING → WARNING, ERROR, CRITICAL)."""
    levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    query = ("SELECT event_id, timestamp, type, severity, component, shop, city, category, scan_id, message, "
             "data_json FROM telemetry_events WHERE 1=1")
    params: List[Any] = []
    if component:
        query += " AND component = ?"
        params.append(component)
    if severity and severity.upper() in levels:
        allowed = levels[levels.index(severity.upper()):]
        query += f" AND severity IN ({','.join('?' * len(allowed))})"
        params.extend(allowed)
    if event_type:
        query += " AND type = ?"
        params.append(event_type)
    if shop:
        query += " AND shop = ?"
        params.append(shop)
    if scan_id:
        query += " AND scan_id = ?"
        params.append(scan_id)
    if before:
        query += " AND timestamp < ?"
        params.append(before)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(max(1, min(int(limit), EVENTS_LIMIT_MAX)))
    with closing(_conn()) as conn:
        rows = [dict(r) for r in conn.execute(query, params)]
    for r in rows:
        r["data"] = _loads(r.pop("data_json"))
    return rows


_DIGITS = re.compile(r"\d+")

HYPOTHESES = [
    (("429", "cooldown"), "Магазин ограничивает частоту запросов (429 / Retry-After)"),
    (("403", "401", "challenge", "cloudflare"), "Доступ заблокирован: антибот-защита или изменение доступа"),
    (("404", "410"), "Страница или категория удалена либо изменился URL"),
    (("5xx", "500", "502", "503", "504"), "Ошибка на стороне сайта магазина"),
    (("timeout",), "Сайт отвечает медленно или недоступен (timeout)"),
    (("connection", "ssl", "resolve"), "Сетевая недоступность сайта (соединение, DNS, TLS)"),
    (("качество", "товаров", "отброшено", "повтор"), "Выдача изменилась: вероятна смена вёрстки или «тихая поломка»"),
    (("пустая выдача", "карточки не найдены"), "Сайт отдал пустую страницу: смена вёрстки или блокировка"),
    (("lease", "аренд"), "Обход перехватил другой процесс (аренда планировщика)"),
    (("budget_exhausted",), "Исчерпан дневной бюджет AI"),
]


def _signature(e: Dict[str, Any]) -> str:
    d = e["data"]
    if e["type"] in ("http_request", "http_cooldown"):
        return f"{d.get('host') or ''}:{d.get('status') or d.get('kind') or d.get('error') or ''}"
    if e["type"] == "system_error":
        return f"{d.get('where') or ''}:{d.get('error') or ''}"
    if e["type"] == "ai_query":
        return str(d.get("outcome") or "")
    text = str(d.get("error") or (d.get("quality") or {}).get("reasons") or e["message"] or "")
    return _DIGITS.sub("#", text)[:120]


def hypothesis(text: str, event_type: str = "", signature: str = "") -> str:
    low = text.lower()
    for needles, explanation in HYPOTHESES:
        if any(n in low for n in needles):
            return explanation
    if event_type == "system_error":
        where, _, error = signature.partition(":")
        return f"Необработанное исключение {error or '?'} в {where or 'компоненте'} — ошибка кода или данных"
    if event_type == "search_query":
        return "Поиск завершается ошибкой (база или живой опрос магазинов) — см. события"
    if event_type == "ai_query":
        return f"AI-провайдер отвечает с ошибкой ({signature or '?'})"
    return "Причина не классифицирована — см. события инцидента"


# Явные успешные исходы операций (D01): произвольный INFO и успех другой операции того же компонента не
# доказывают восстановления. Область — конкретная операция, а не компонент целиком.
def _component_success(e: Dict[str, Any]) -> Optional[str]:
    """Какую операцию подтверждает успешное событие: ключ области или None."""
    d, t = e["data"], e["type"]
    if t == "ai_query" and d.get("outcome") == "ok" and d.get("provider_called"):
        return f"ai:{d.get('purpose') or 'user'}"
    if t == "search_query" and d.get("outcome") in ("found", "not_found"):
        return "search"
    if t == "telegram_alert" and int(d.get("sent") or 0) > 0:
        return "telegram_delivery"
    if t == "scan_end" and d.get("outcome") == "completed":
        return "scheduler"
    if t == "backup_run" and d.get("outcome") == "ok":
        return "backup"
    return None


# Ошибки воркеров (system_error.where) → операция, успех которой подтверждает их восстановление.
# Только прямые связи: ai_normalize_worker вызывает AI с purpose=normalize, notification_worker — deliver_pending
# (сводка telegram_alert), auto_scan_worker запускает обходы (scan_end). У telegram_polling, http_handler и прочих
# сигнала успеха в V1 нет — такие инциденты не восстанавливаются, а только затихают (quiet).
_WHERE_SCOPE = {"ai_normalize_worker": "ai:normalize", "notification_worker": "telegram_delivery",
                "auto_scan_worker": "scheduler"}


def _incident_scope(event_type: str, data: Dict[str, Any]) -> Optional[str]:
    """Область успеха для компонентного инцидента по типу проблемного события; None — сигнала нет."""
    if event_type == "system_error":
        return _WHERE_SCOPE.get(data.get("where") or "")   # без запасного перехода на компонент
    if event_type == "ai_query":
        return f"ai:{data.get('purpose') or 'user'}"
    return {"search_query": "search", "telegram_alert": "telegram_delivery", "scan_end": "scheduler",
            "backup_run": "backup"}.get(event_type)


_SUCCESS_TYPES = ("scan_category", "recovery", "ai_query", "search_query", "telegram_alert", "scan_end", "backup_run")
QUIET_UNCONFIRMED_HOURS = 24


def _category_success(e: Dict[str, Any]) -> bool:
    """Подтверждённый успех категории: полный (complete) итог без ошибки и без degraded/warning по качеству.
    limited и warning восстановлением не считаются (D01)."""
    d = e["data"]
    quality = (d.get("quality") or {}).get("quality")
    return (e["type"] == "scan_category" and e["severity"] == "INFO" and d.get("complete") is True
            and not d.get("error") and quality in (None, "ok", "unknown"))


def _host_recovered_at(conn, host: str, after: str) -> Optional[str]:
    """Первый минутный бакет с успешными (2xx) ответами хоста после последнего появления проблемы."""
    from telemetry import BUCKET_FORMATS
    last = _parse(after)
    if not host or last is None:
        return None
    minute_after = (last + datetime.timedelta(minutes=1)).strftime(BUCKET_FORMATS["minute"])
    row = conn.execute("""SELECT MIN(bucket_start) FROM telemetry_http_aggregates
        WHERE bucket_type='minute' AND host = ? AND bucket_start >= ? AND status_2xx > 0""", (host, minute_after)).fetchone()
    return row[0] if row and row[0] else None


def incidents(days: int = INCIDENT_WINDOW_DAYS, now: Optional[datetime.datetime] = None,
              shop_keys: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Повторяющиеся проблемы (WARNING+) за окно группируются в инциденты: начало, последнее появление, число,
    масштаб, гипотеза и восстановление, соотнесённое с областью инцидента (D01):

    - категории магазина — у каждой затронутой категории после её последней проблемы есть подтверждённый успех
      (_category_success), либо магазин получил recovery (полный обход после деградации, P02);
    - HTTP без категории — у каждого затронутого хоста после последней проблемы есть минута с 2xx, либо recovery;
    - магазин без категории и хоста (degradation, падение магазина) — только recovery;
    - компонент — явный успешный исход той же операции (_incident_scope / _component_success) и час без повторов;
      если у операции нет сигнала успеха, после QUIET_UNCONFIRMED_HOURS без повторов — «quiet» (не подтверждено).
    Состояния: open, recovered, quiet (quiet не считается открытым, но и восстановленным не называется).
    shop_keys — {название магазина: ключ}: проблемные события пишут название, recovery (P02) — ключ магазина.
    """
    shop_keys = shop_keys or {}
    now = now or _now()
    since = _event_cutoff(now, days * 24)
    marks = ",".join("?" * len(_SUCCESS_TYPES))
    with closing(_conn()) as conn:
        # Только проблемы и события, способные подтвердить успех: окно не читается целиком
        rows = conn.execute(f"""SELECT timestamp, type, severity, component, shop, category, scan_id, message, data_json
            FROM telemetry_events WHERE timestamp >= ?
              AND (severity IN ('WARNING', 'ERROR', 'CRITICAL') OR type IN ({marks}))
            ORDER BY timestamp""", (since, *_SUCCESS_TYPES)).fetchall()
        problems: Dict[tuple, Dict[str, Any]] = {}
        cat_ok: Dict[tuple, str] = {}
        shop_recovery: Dict[str, str] = {}
        comp_ok: Dict[str, str] = {}
        for r in rows:
            e = dict(r)
            e["data"] = _loads(e.pop("data_json"))
            shop = e["shop"] or e["data"].get("shop_key")
            if e["severity"] in ("INFO", "DEBUG"):
                if _category_success(e) and shop and e["category"]:
                    cat_ok[(shop, e["category"])] = e["timestamp"]
                if e["type"] == "recovery" and shop:
                    shop_recovery[shop] = e["timestamp"]
                    if e["data"].get("shop_key"):
                        shop_recovery[e["data"]["shop_key"]] = e["timestamp"]
                scope = _component_success(e)
                if scope:
                    comp_ok[scope] = e["timestamp"]
                continue
            if e["type"] in ("scan_start", "scan_end") and e["severity"] != "ERROR":
                continue
            key = (e["component"], e["type"], shop or "", _signature(e))
            inc = problems.get(key)
            if inc is None:
                inc = problems[key] = {
                    # Стабильный id между процессами (hash() рандомизирован — см. дефект Arbuz в P00)
                    "id": hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:12],
                    "component": e["component"], "type": e["type"], "shop": e["shop"],
                    "shop_key": e["data"].get("shop_key"), "signature": key[3],
                    "severity": e["severity"], "first_seen": e["timestamp"], "count": 0,
                    "cat_last": {}, "host_last": {}, "scan_ids": set(), "sample": e["message"],
                    "scope": _incident_scope(e["type"], e["data"]),
                }
            inc["count"] += 1
            inc["last_seen"] = e["timestamp"]
            inc["sample"] = e["message"]
            if e["severity"] in ("ERROR", "CRITICAL"):
                inc["severity"] = e["severity"]
            if e["category"]:
                inc["cat_last"][e["category"]] = e["timestamp"]
            if e["data"].get("host"):
                inc["host_last"][e["data"]["host"]] = e["timestamp"]
            if e["scan_id"]:
                inc["scan_ids"].add(e["scan_id"])

        out = []
        for key, inc in problems.items():
            component, _type, shop, signature = key
            recovered_at, progress = None, None
            quiet_h = _hours_since(inc["last_seen"], now) or 0
            state = "open"
            if shop:
                rec = (shop_recovery.get(shop) or shop_recovery.get(shop_keys.get(shop, ""))
                       or (inc["shop_key"] and shop_recovery.get(inc["shop_key"])))
                if rec and rec > inc["last_seen"]:
                    recovered_at = rec
                elif inc["cat_last"]:
                    fixed = {c: cat_ok[(shop, c)] for c, last in inc["cat_last"].items()
                             if cat_ok.get((shop, c), "") > last}
                    progress = {"recovered": len(fixed), "total": len(inc["cat_last"])}
                    if len(fixed) == len(inc["cat_last"]):
                        recovered_at = max(fixed.values())
                elif inc["host_last"]:
                    fixed = {h: _host_recovered_at(conn, h, last) for h, last in inc["host_last"].items()}
                    progress = {"recovered": sum(1 for v in fixed.values() if v), "total": len(fixed)}
                    if all(fixed.values()):
                        recovered_at = max(fixed.values())
            else:
                scope = inc["scope"]
                ok_at = comp_ok.get(scope) if scope else None
                if ok_at and ok_at > inc["last_seen"] and quiet_h >= INCIDENT_QUIET_HOURS:
                    recovered_at = ok_at
                elif quiet_h >= QUIET_UNCONFIRMED_HOURS:
                    state = "quiet"
            if recovered_at:
                state = "recovered"
            out.append({
                **{k: v for k, v in inc.items() if k not in ("cat_last", "host_last", "scan_ids")},
                "scale": {"events": inc["count"], "categories": sorted(inc["cat_last"])[:20],
                          "categories_count": len(inc["cat_last"]), "hosts": sorted(inc["host_last"]),
                          "scans": len(inc["scan_ids"])},
                "hypothesis": hypothesis(f"{signature} {inc['sample']}", _type, signature),
                "recovered_at": recovered_at,
                "recovery_progress": progress,
                "state": state,
                "open": state == "open",
            })
    # Открытые первыми, внутри — самые свежие
    out.sort(key=lambda i: (not i["open"], -(_parse(i["last_seen"]) or now).timestamp()))
    return out


# ---------------------------------------------------------------------------------------------- обзор

def overview(registry: Dict[str, Tuple[str, int]], enabled: Iterable[str], scan_state: Dict[str, Any],
             expected_interval_sec: int, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    shops = shops_overview(registry, enabled, now)
    counts: Dict[str, int] = defaultdict(int)
    for s in shops:
        counts[s["status"]] += 1
    active = [s["status"] for s in shops if s["status"] != DISABLED]
    if not active:
        shops_status, shops_reason = UNKNOWN, "Нет включённых магазинов"
    else:
        shops_status = worst(active)
        bad = counts[OFFLINE] + counts[DEGRADED] + counts[EMPTY]
        shops_reason = (f"Проблемы у {bad} из {len(active)} магазинов" if bad else
                        f"Нет данных по {counts[UNKNOWN]} из {len(active)}" if counts[UNKNOWN] else
                        f"Все {len(active)} магазинов в порядке")
    sections = {
        "shops": {"status": shops_status, "reason": shops_reason, "counts": dict(counts)},
        "scanning": scanning_section(scan_state, expected_interval_sec, now),
        "search": search_section(now),
        "ai": ai_section(now),
        "telegram": telegram_section(now),
        "system": system_section(now),
    }
    open_incidents = [i for i in incidents(now=now, shop_keys={name: key for key, (name, _n) in registry.items()})
                      if i["open"]]
    sections["events"] = {
        "status": DEGRADED if any(i["severity"] in ("ERROR", "CRITICAL") for i in open_incidents)
        else (LIMITED if open_incidents else HEALTHY),
        "reason": f"Открытых инцидентов: {len(open_incidents)}" if open_incidents else "Открытых инцидентов нет",
        "open_incidents": len(open_incidents),
    }
    return {"generated_at": now.isoformat(), "status": worst(v["status"] for v in sections.values()),
            "sections": sections, "shops": shops, "open_incidents": open_incidents[:10]}
