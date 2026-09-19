"""Ядро структурированной телеметрии KZ Price Hunter (Этап P01).

Обеспечивает:
- Модель структурированных событий (event_id, timestamp, type, severity, component,
  shop, city, category, scan_id, request_id, message, data_json).
- Контекстное распространение trace-данных через `contextvars` (scan_id, shop, category).
- Очистку чувствительных данных (санитизация URL, удаление токенов/ключей, redact_secrets).
- Сбор HTTP-метрик по бакетам minute/hour/day (запросы, коды, timeout/connection errors,
  latency sum и p95, bytes, Retry-After, отказы по cooldown).
- p95 считается по равномерной выборке задержек бакета (Algorithm R), а не как среднее из p95.
- Fail-Open: запись идёт в память (O(1) под блокировкой), в SQLite — фоновым сбросом батчами
  с коротким busy_timeout. Сбой БД теряет только телеметрию, сбор и сервис не ждут и не падают.
- Очистку (retention) устаревших данных.
- Отключение: TELEMETRY_ENABLED=0 (события и метрики не собираются вовсе).

Фоновый сброс запускает процесс-владелец (веб-сервер, main.py) через telemetry.start();
без него данные копятся в ограниченном буфере до явного flush() — так тесты не пишут
в рабочую prices.db.
"""
import contextvars
import datetime
import json
import math
import os
import random
import sqlite3
import re
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from security_logging import redact_secrets

# Контекстные переменные для автоматической привязки логов/метрик к текущему обходу/запросу
current_scan_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_scan_id", default=None)
current_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_request_id", default=None)
current_shop: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_shop", default=None)
current_city: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_city", default=None)
current_category: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_category", default=None)

# Типы событий
EVENT_HTTP_REQUEST = "http_request"
EVENT_HTTP_COOLDOWN = "http_cooldown"
EVENT_SCAN_START = "scan_start"
EVENT_SCAN_END = "scan_end"
EVENT_SCAN_CATEGORY = "scan_category"
EVENT_SCAN_ERROR = "scan_error"
EVENT_DEGRADATION = "degradation"
EVENT_RECOVERY = "recovery"
EVENT_ANOMALY_DETECTED = "anomaly_detected"
EVENT_PRICE_CHANGED = "price_changed"
EVENT_SEARCH_QUERY = "search_query"
EVENT_AI_QUERY = "ai_query"
EVENT_TELEGRAM_ALERT = "telegram_alert"
EVENT_BACKUP_RUN = "backup_run"
EVENT_SYSTEM_ERROR = "system_error"

# Уровни важности
SEVERITY_DEBUG = "DEBUG"
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_ERROR = "ERROR"
SEVERITY_CRITICAL = "CRITICAL"

# Компоненты системы
COMPONENT_SCRAPER = "scraper"
COMPONENT_SCHEDULER = "scheduler"
COMPONENT_HTTP = "http"
COMPONENT_DETECTOR = "detector"
COMPONENT_AI = "ai"
COMPONENT_SEARCH = "search"
COMPONENT_TELEGRAM = "telegram"
COMPONENT_BACKUP = "backup"
COMPONENT_SYSTEM = "system"

# Максимальный размер сериализованных данных в событии (4 КБ)
MAX_DATA_JSON_BYTES = 4096
# Максимальное количество выборок на бакет для расчета p95 (резервуарная выборка)
MAX_SAMPLES_PER_BUCKET = 500
# Ограничения буфера до сброса в БД: переполнение отбрасывает новые данные (счётчик dropped)
MAX_PENDING_EVENTS = 5000
MAX_PENDING_LATENCIES_PER_KEY = 2000
FLUSH_INTERVAL_SECONDS = 10.0
PRUNE_INTERVAL_SECONDS = 6 * 3600
# Телеметрия не ждёт блокировку SQLite дольше этого: обход важнее диагностики
DB_BUSY_TIMEOUT_SECONDS = 2.0

# Бакеты агрегатов и формат их начала (лексикографически сравнимые строки UTC)
BUCKET_FORMATS = {
    "minute": "%Y-%m-%dT%H:%M:00Z",
    "hour": "%Y-%m-%dT%H:00:00Z",
    "day": "%Y-%m-%d",
}
# Сроки хранения, дни. Diagnostics (samples, minute) — 7, события — 30, hour — 90, day — 365
RETENTION_DAYS = {
    "events": 30,
    "samples": 7,
    "minute": 7,
    "hour": 90,
    "day": 365,
}
_EVENT_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def telemetry_enabled() -> bool:
    return os.getenv("TELEMETRY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def classify_error(error: Optional[str]) -> Optional[str]:
    """timeout / connection / other по тексту исключения вида 'ИмяКласса: сообщение'."""
    if not error:
        return None
    low = error.lower()
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "connection" in low or "resolve" in low or "ssl" in low or "refused" in low or "reset" in low:
        return "connection"
    return "other"

# Регулярные выражения для чувствительных ключей в query-параметрах
_SENSITIVE_PARAM_REGEX = re.compile(
    r"(?i)^(.*_)?(token|key|secret|password|auth|signature|session|api_key|bot_token|access_token|refresh_token)$"
)


def sanitize_url(url: str) -> str:
    """Удаляет конфиденциальные параметры из URL (токены, ключи, пароли) и применяет redact_secrets."""
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
        netloc = parts.netloc
        # Очистка userinfo (http://user:password@host -> http://user:[REDACTED]@host)
        if "@" in netloc:
            userinfo, host_port = netloc.rsplit("@", 1)
            if ":" in userinfo:
                user, _ = userinfo.split(":", 1)
                netloc = f"{user}:[REDACTED]@{host_port}"
            else:
                netloc = f"[REDACTED]@{host_port}"

        # Фильтрация query-параметров
        if parts.query:
            filtered_params = []
            for k, v in parse_qsl(parts.query, keep_blank_values=True):
                if _SENSITIVE_PARAM_REGEX.match(k):
                    filtered_params.append((k, "[REDACTED]"))
                else:
                    filtered_params.append((k, v))
            clean_query = urlencode(filtered_params)
        else:
            clean_query = ""

        clean_url = urlunsplit((parts.scheme, netloc, parts.path, clean_query, parts.fragment))
        return redact_secrets(clean_url)
    except Exception:
        return redact_secrets(str(url)[:200])


def sanitize_payload(data: Any, max_bytes: int = MAX_DATA_JSON_BYTES) -> str:
    """Сериализует полезную нагрузку в JSON, скрывает секреты и обрезает до max_bytes."""
    if data is None:
        return ""
    try:
        if isinstance(data, str):
            raw = data
        else:
            raw = json.dumps(data, ensure_ascii=False, default=str)
        redacted = redact_secrets(raw)
        if len(redacted.encode("utf-8")) > max_bytes:
            # Обрезка с сохранением валидности
            return json.dumps({"_truncated": True, "preview": redacted[: max_bytes // 2]}, ensure_ascii=False)
        return redacted
    except Exception as e:
        return json.dumps({"_serialization_error": str(e)})


def calculate_p95(latencies: List[float]) -> float:
    """Вычисляет 95-й процентиль (p95) из распределения значений.

    Не усредняет чужие процентили, а считает точный квантиль по сортированному массиву.
    """
    if not latencies:
        return 0.0
    s = sorted(latencies)
    n = len(s)
    if n == 1:
        return float(round(s[0], 2))
    # Линейная интерполяция между соседними рангами (как numpy.percentile по умолчанию)
    k = (n - 1) * 0.95
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(round(s[int(k)], 2))
    d0 = s[int(f)] * (c - k)
    d1 = s[int(c)] * (k - f)
    return float(round(d0 + d1, 2))


class _PendingAggregate:
    __slots__ = ("total", "s2xx", "s3xx", "s4xx", "s5xx", "s429", "timeouts", "conn_errors",
                 "errors", "cooldown", "retry_after_max", "bytes", "latency_sum", "latencies")

    def __init__(self):
        self.total = self.s2xx = self.s3xx = self.s4xx = self.s5xx = self.s429 = 0
        self.timeouts = self.conn_errors = self.errors = self.cooldown = self.bytes = 0
        self.retry_after_max: Optional[float] = None
        self.latency_sum = 0.0
        self.latencies: List[float] = []


class TelemetryService:
    """Структурированная телеметрия: in-memory буфер + фоновый сброс в SQLite."""

    def __init__(self, in_memory_buffer_size: int = 500):
        self._buffer: deque = deque(maxlen=in_memory_buffer_size)
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending_events: List[Dict[str, Any]] = []
        self._pending_http: Dict[tuple, _PendingAggregate] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_prune = 0.0
        self.stats = {"dropped_events": 0, "dropped_samples": 0, "failed_flushes": 0}

    # ------------------------------------------------------------------ запись

    def record_event(
        self,
        event_type: str,
        severity: str,
        component: str,
        message: str,
        shop: Optional[str] = None,
        city: Optional[str] = None,
        category: Optional[str] = None,
        scan_id: Optional[str] = None,
        request_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Записывает структурированное событие. Fail-Open: исключения не выходят наружу."""
        if not telemetry_enabled():
            return None
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            event = {
                "event_id": str(uuid.uuid4()),
                "timestamp": now.strftime(_EVENT_TS_FORMAT),
                "type": str(event_type),
                "severity": str(severity).upper(),
                "component": str(component),
                "shop": shop or current_shop.get(),
                "city": city or current_city.get(),
                "category": category or current_category.get(),
                "scan_id": scan_id or current_scan_id.get(),
                "request_id": request_id or current_request_id.get(),
                "message": redact_secrets(str(message))[:1000],
                "data_json": sanitize_payload(data) if data else None,
            }
            with self._lock:
                self._buffer.append(event)
                if len(self._pending_events) < MAX_PENDING_EVENTS:
                    self._pending_events.append(event)
                else:
                    self.stats["dropped_events"] += 1
            return event
        except Exception as e:
            print(f"[Telemetry] Не удалось записать событие ({event_type}): {type(e).__name__}", file=sys.stderr)
            return None

    def record_http_metric(
        self,
        host: str,
        method: str,
        status_code: int,
        latency_ms: float,
        bytes_count: int = 0,
        error: Optional[str] = None,
        retry_after: Optional[float] = None,
        shop: Optional[str] = None,
        url: Optional[str] = None,
        cooldown_rejected: bool = False,
    ) -> None:
        """Учитывает один HTTP-запрос в бакетах minute/hour/day.

        cooldown_rejected — запрос не отправлялся (HostCooldown): считается отдельно от запросов,
        чтобы total_requests совпадал с фактически отправленными.
        """
        if not telemetry_enabled():
            return
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            shop_name = shop or current_shop.get() or ""
            clean_host = (host or "").lower().strip()
            error_kind = classify_error(error)

            if status_code == 429 and not cooldown_rejected:
                self.record_event(
                    event_type=EVENT_HTTP_COOLDOWN,
                    severity=SEVERITY_WARNING,
                    component=COMPONENT_HTTP,
                    message=f"HTTP 429 от {clean_host} (Retry-After: {retry_after} с)",
                    shop=shop_name or None,
                    data={"host": clean_host, "url": sanitize_url(url) if url else "",
                          "retry_after": retry_after, "status": status_code},
                )
            elif error and not cooldown_rejected:
                self.record_event(
                    event_type=EVENT_HTTP_REQUEST,
                    severity=SEVERITY_ERROR,
                    component=COMPONENT_HTTP,
                    message=f"HTTP ошибка {method} к {clean_host}: {error}",
                    shop=shop_name or None,
                    data={"host": clean_host, "url": sanitize_url(url) if url else "",
                          "error": error, "kind": error_kind, "latency_ms": round(latency_ms, 2)},
                )

            with self._lock:
                for bucket_type, fmt in BUCKET_FORMATS.items():
                    key = (bucket_type, now.strftime(fmt), clean_host, shop_name)
                    agg = self._pending_http.get(key)
                    if agg is None:
                        agg = self._pending_http[key] = _PendingAggregate()
                    if cooldown_rejected:
                        agg.cooldown += 1
                        continue
                    agg.total += 1
                    if 200 <= status_code < 300:
                        agg.s2xx += 1
                    elif 300 <= status_code < 400:
                        agg.s3xx += 1
                    elif status_code == 429:
                        agg.s429 += 1
                    elif 400 <= status_code < 500:
                        agg.s4xx += 1
                    elif 500 <= status_code < 600:
                        agg.s5xx += 1
                    if error:
                        agg.errors += 1
                        if error_kind == "timeout":
                            agg.timeouts += 1
                        elif error_kind == "connection":
                            agg.conn_errors += 1
                    if retry_after is not None:
                        agg.retry_after_max = max(agg.retry_after_max or 0.0, float(retry_after))
                    agg.bytes += max(0, int(bytes_count or 0))
                    agg.latency_sum += float(latency_ms)
                    if len(agg.latencies) < MAX_PENDING_LATENCIES_PER_KEY:
                        agg.latencies.append(float(latency_ms))
                    else:
                        self.stats["dropped_samples"] += 1
        except Exception as e:
            print(f"[Telemetry] Ошибка учёта HTTP-метрики: {type(e).__name__}", file=sys.stderr)

    # ------------------------------------------------------------------ сброс в БД

    def _connect(self):
        import database  # DB_PATH читается при каждом сбросе: тесты подменяют database.DB_PATH

        conn = sqlite3.connect(database.DB_PATH, timeout=DB_BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(DB_BUSY_TIMEOUT_SECONDS * 1000)}")
        return conn

    def flush(self) -> bool:
        """Переносит накопленные события и HTTP-агрегаты в БД. False — сброс не удался (данные потеряны)."""
        with self._lock:
            events, self._pending_events = self._pending_events, []
            http, self._pending_http = self._pending_http, {}
        if not events and not http:
            return True
        with self._flush_lock:
            try:
                conn = self._connect()
                try:
                    with conn:
                        if events:
                            conn.executemany(
                                """INSERT OR IGNORE INTO telemetry_events(
                                    event_id, timestamp, type, severity, component,
                                    shop, city, category, scan_id, request_id, message, data_json
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                [(e["event_id"], e["timestamp"], e["type"], e["severity"], e["component"],
                                  e["shop"], e["city"], e["category"], e["scan_id"], e["request_id"],
                                  e["message"], e["data_json"]) for e in events],
                            )
                        for key, agg in http.items():
                            self._flush_aggregate(conn, key, agg)
                finally:
                    conn.close()
                return True
            except Exception as e:
                self.stats["failed_flushes"] += 1
                print(f"[Telemetry] Сброс в БД не удался ({type(e).__name__}), данные телеметрии пропущены",
                      file=sys.stderr)
                return False

    def _flush_aggregate(self, conn, key: tuple, agg: _PendingAggregate) -> None:
        bucket_type, bucket_start, host, shop = key
        where = "bucket_type = ? AND bucket_start = ? AND host = ? AND shop = ?"
        row = conn.execute(f"SELECT total_requests FROM telemetry_http_aggregates WHERE {where}", key).fetchone()
        seen = row[0] if row else 0

        if agg.latencies:
            # Algorithm R поверх выборки в БД: каждая задержка бакета попадает в выборку
            # с равной вероятностью MAX_SAMPLES_PER_BUCKET / seen
            stored = conn.execute(f"SELECT COUNT(*) FROM telemetry_http_samples WHERE {where}", key).fetchone()[0]
            for latency in agg.latencies:
                seen += 1
                if stored < MAX_SAMPLES_PER_BUCKET:
                    stored += 1
                elif random.randrange(seen) < MAX_SAMPLES_PER_BUCKET:
                    conn.execute(
                        f"""DELETE FROM telemetry_http_samples WHERE id = (
                               SELECT id FROM telemetry_http_samples WHERE {where} LIMIT 1 OFFSET ?)""",
                        (*key, random.randrange(stored)),
                    )
                else:
                    continue
                conn.execute(
                    "INSERT INTO telemetry_http_samples(bucket_type, bucket_start, host, shop, latency_ms) VALUES (?, ?, ?, ?, ?)",
                    (*key, latency),
                )
            samples = [r[0] for r in conn.execute(f"SELECT latency_ms FROM telemetry_http_samples WHERE {where}", key)]
            p95 = calculate_p95(samples)
        else:
            p95 = None

        conn.execute(
            """INSERT INTO telemetry_http_aggregates(
                bucket_type, bucket_start, host, shop, total_requests,
                status_2xx, status_3xx, status_4xx, status_5xx, status_429,
                timeouts, connection_errors, errors, cooldown_rejections, retry_after_max_s,
                bytes_total, latency_sum_ms, latency_p95_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0))
            ON CONFLICT(bucket_type, bucket_start, host, shop) DO UPDATE SET
                total_requests = total_requests + excluded.total_requests,
                status_2xx = status_2xx + excluded.status_2xx,
                status_3xx = status_3xx + excluded.status_3xx,
                status_4xx = status_4xx + excluded.status_4xx,
                status_5xx = status_5xx + excluded.status_5xx,
                status_429 = status_429 + excluded.status_429,
                timeouts = timeouts + excluded.timeouts,
                connection_errors = connection_errors + excluded.connection_errors,
                errors = errors + excluded.errors,
                cooldown_rejections = cooldown_rejections + excluded.cooldown_rejections,
                retry_after_max_s = CASE
                    WHEN excluded.retry_after_max_s IS NULL THEN retry_after_max_s
                    WHEN retry_after_max_s IS NULL THEN excluded.retry_after_max_s
                    ELSE MAX(retry_after_max_s, excluded.retry_after_max_s) END,
                bytes_total = bytes_total + excluded.bytes_total,
                latency_sum_ms = latency_sum_ms + excluded.latency_sum_ms,
                latency_p95_ms = COALESCE(?, latency_p95_ms)""",
            (*key, agg.total, agg.s2xx, agg.s3xx, agg.s4xx, agg.s5xx, agg.s429,
             agg.timeouts, agg.conn_errors, agg.errors, agg.cooldown, agg.retry_after_max,
             agg.bytes, agg.latency_sum, p95, p95),
        )

    # ------------------------------------------------------------------ фоновый поток

    def start(self) -> None:
        """Запускает фоновый сброс и периодическую очистку. Повторный вызов безопасен."""
        if not telemetry_enabled() or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telemetry-flush", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Останавливает фоновый поток и выполняет финальный сброс."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None
        self.flush()

    def _run(self) -> None:
        while not self._stop.wait(FLUSH_INTERVAL_SECONDS):
            try:
                self.flush()
                if time.monotonic() - self._last_prune >= PRUNE_INTERVAL_SECONDS:
                    self._last_prune = time.monotonic()
                    self.prune()
            except Exception:
                pass  # поток телеметрии не должен умирать

    # ------------------------------------------------------------------ чтение

    def get_recent_events(
        self,
        limit: int = 100,
        component: Optional[str] = None,
        severity: Optional[str] = None,
        scan_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Недавние события, новые последними: in-memory буфер, дополненный из БД."""
        with self._lock:
            events = list(self._buffer)

        def matches(e):
            return ((not component or e.get("component") == component)
                    and (not severity or e.get("severity") == severity.upper())
                    and (not scan_id or e.get("scan_id") == scan_id))

        events = [e for e in events if matches(e)]
        if len(events) >= limit:
            return events[-limit:]

        try:
            query = ("SELECT event_id, timestamp, type, severity, component, shop, city, category, "
                     "scan_id, request_id, message, data_json FROM telemetry_events WHERE 1=1")
            params: List[Any] = []
            if component:
                query += " AND component = ?"
                params.append(component)
            if severity:
                query += " AND severity = ?"
                params.append(severity.upper())
            if scan_id:
                query += " AND scan_id = ?"
                params.append(scan_id)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            conn = self._connect()
            try:
                db_events = [dict(r) for r in conn.execute(query, params)]
            finally:
                conn.close()
            seen = {e["event_id"] for e in events}
            merged = [e for e in reversed(db_events) if e["event_id"] not in seen] + events
            merged.sort(key=lambda e: e["timestamp"])
            return merged[-limit:]
        except Exception:
            return events[-limit:]

    def get_http_summary(self, bucket_type: str = "hour", limit: int = 24) -> List[Dict[str, Any]]:
        """Агрегаты HTTP по бакетам (уже сброшенные в БД), новые первыми."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """SELECT bucket_type, bucket_start, host, shop, total_requests,
                              status_2xx, status_3xx, status_4xx, status_5xx, status_429,
                              timeouts, connection_errors, errors, cooldown_rejections, retry_after_max_s,
                              bytes_total, latency_sum_ms, latency_p95_ms
                       FROM telemetry_http_aggregates
                       WHERE bucket_type = ?
                       ORDER BY bucket_start DESC, total_requests DESC
                       LIMIT ?""",
                    (bucket_type, limit),
                ).fetchall()
            finally:
                conn.close()
            results = []
            for r in rows:
                d = dict(r)
                total = d["total_requests"]
                d["avg_latency_ms"] = round(d["latency_sum_ms"] / total, 2) if total > 0 else 0.0
                results.append(d)
            return results
        except Exception:
            return []

    # ------------------------------------------------------------------ retention

    def prune(self, now: Optional[datetime.datetime] = None) -> Dict[str, int]:
        """Удаляет данные старше RETENTION_DAYS. Сравнение строк в формате соответствующего бакета."""
        deleted = {"events": 0, "samples": 0, "minute": 0, "hour": 0, "day": 0}
        try:
            now = now or datetime.datetime.now(datetime.timezone.utc)

            def cutoff(kind: str, fmt: str) -> str:
                return (now - datetime.timedelta(days=RETENTION_DAYS[kind])).strftime(fmt)

            conn = self._connect()
            try:
                with conn:
                    deleted["events"] = conn.execute(
                        "DELETE FROM telemetry_events WHERE timestamp < ?",
                        (cutoff("events", _EVENT_TS_FORMAT),)).rowcount
                    samples = 0
                    for bucket_type, fmt in BUCKET_FORMATS.items():
                        # Выборки нужны только для пересчёта p95 открытых бакетов; сам p95 остаётся в агрегате
                        samples += conn.execute(
                            "DELETE FROM telemetry_http_samples WHERE bucket_type = ? AND bucket_start < ?",
                            (bucket_type, cutoff("samples", fmt))).rowcount
                        deleted[bucket_type] = conn.execute(
                            "DELETE FROM telemetry_http_aggregates WHERE bucket_type = ? AND bucket_start < ?",
                            (bucket_type, cutoff(bucket_type, fmt))).rowcount
                    deleted["samples"] = samples
            finally:
                conn.close()
        except Exception as e:
            print(f"[Telemetry] Ошибка при очистке (prune): {type(e).__name__}", file=sys.stderr)
        return deleted


# Глобальный синглтон сервиса телеметрии
telemetry = TelemetryService()
