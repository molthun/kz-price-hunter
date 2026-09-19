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
# Сводка HTTP текущей категории обхода (dict, изменяется на месте): связывает страницы с итогом категории
current_http_trace: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("current_http_trace", default=None)

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
EVENT_TELEGRAM_BOT = "telegram_bot"

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
MAX_PENDING_HTTP_KEYS = 5000
# Диагностические события HTTP 4xx/5xx: не больше стольких в минуту на host+код, остальное — в счётчиках
MAX_HTTP_DIAG_EVENTS_PER_MINUTE = 20
MAX_SEARCH_EVENTS_PER_MINUTE = 60
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
    """timeout / connection / other по тексту исключения. Сам текст не сохраняется (A01)."""
    if not error:
        return None
    low = error.lower()
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "connection" in low or "resolve" in low or "ssl" in low or "refused" in low or "reset" in low:
        return "connection"
    return "other"


_ERROR_NAME_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,79}$")


def safe_error_name(error: Optional[str]) -> Optional[str]:
    """Только имя класса исключения ('ConnectionError: https://…' → 'ConnectionError').

    Сообщения транспортных ошибок содержат URL, заголовки и тела ответов — в телеметрию не попадают.
    """
    if not error:
        return None
    name = str(error).split(":", 1)[0].strip()
    return name if _ERROR_NAME_REGEX.match(name) else "Error"


# Имена полей и query-параметров с секретами. Список явный: суффикс «_key» сам по себе не секрет
# (shop_key), а key/api_key/access_key — секрет. Разделители: _, -, . (X-Amz-Signature, x.auth).
_SENSITIVE_NAME_REGEX = re.compile(
    r"(?i)^(?:key|(?:.*[_.-])?(?:token|secret|passw(?:or)?d|pwd|auth|authorization|signature|sig|cookies?|"
    r"set-cookie|credentials?|jwt|session(?:_?id)?|sid|api_?key|access_?key|secret_?key|private_?key))$"
)
# Совместимость со старым именем
_SENSITIVE_PARAM_REGEX = _SENSITIVE_NAME_REGEX
_URL_IN_TEXT_REGEX = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,15}://[^\s'\"<>]+")
_COOKIE_IN_TEXT_REGEX = re.compile(r"(?i)\b(set-cookie|cookie)(\s*[:=]\s*)[^\r\n]*")
_MAX_SANITIZE_DEPTH = 6
_MAX_SANITIZE_ITEMS = 100
_REDACTED = "[REDACTED]"


def is_sensitive_name(name: Any) -> bool:
    return bool(_SENSITIVE_NAME_REGEX.match(str(name)))


def sanitize_url(url: str) -> str:
    """URL без userinfo, фрагмента и значений чувствительных query-параметров; затем redact_secrets."""
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
        netloc = parts.netloc
        if "@" in netloc:
            # Логин тоже может быть секретом (токен в userinfo): удаляется целиком
            netloc = f"{_REDACTED}@{netloc.rsplit('@', 1)[1]}"
        clean_query = ""
        if parts.query:
            clean_query = urlencode([(k, _REDACTED if is_sensitive_name(k) else v)
                                     for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        # Фрагмент не нужен диагностике и может нести токен (OAuth implicit flow)
        return redact_secrets(urlunsplit((parts.scheme, netloc, parts.path, clean_query, "")))
    except Exception:
        return _REDACTED


def sanitize_text(text: Any) -> str:
    """Свободный текст: встроенные URL очищаются структурно, Cookie-заголовки скрываются, затем redact_secrets."""
    try:
        clean = _URL_IN_TEXT_REGEX.sub(lambda m: sanitize_url(m.group(0)), str(text))
        clean = _COOKIE_IN_TEXT_REGEX.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", clean)
        return redact_secrets(clean)
    except Exception:
        return _REDACTED


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_SANITIZE_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_SANITIZE_ITEMS:
                out["_truncated_items"] = len(value) - i
                break
            key = sanitize_text(k)[:100]
            out[key] = _REDACTED if is_sensitive_name(k) else _sanitize_value(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [_sanitize_value(v, depth + 1) for v in items[:_MAX_SANITIZE_ITEMS]]
        if len(items) > _MAX_SANITIZE_ITEMS:
            out.append({"_truncated_items": len(items) - _MAX_SANITIZE_ITEMS})
        return out
    return sanitize_text(value)


def _fit_json(obj: Any, max_bytes: int) -> str:
    """Валидный JSON не длиннее max_bytes байт UTF-8 (с учётом обёртки и экранирования, A02)."""
    raw = json.dumps(obj, ensure_ascii=False)
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) <= max_bytes:
        return raw

    def wrap(n: int) -> str:
        preview = raw_bytes[:n].decode("utf-8", "ignore")  # не разрывает многобайтовые символы
        return json.dumps({"_truncated": True, "preview": preview}, ensure_ascii=False)

    # Экранирование может удваивать длину — наибольший префикс ищется бинарным поиском
    best, lo, hi = '{"_truncated": true}', 0, min(len(raw_bytes), max_bytes)
    while lo <= hi:
        mid = (lo + hi) // 2
        out = wrap(mid)
        if len(out.encode("utf-8")) <= max_bytes:
            best, lo = out, mid + 1
        else:
            hi = mid - 1
    return best


def sanitize_payload(data: Any, max_bytes: int = MAX_DATA_JSON_BYTES) -> str:
    """JSON полезной нагрузки: секреты удалены по именам полей и в тексте, размер ≤ max_bytes байт."""
    if data is None:
        return ""
    try:
        clean = _sanitize_value({"text": data} if isinstance(data, str) else data)
        return _fit_json(clean, max_bytes)
    except Exception as e:
        return json.dumps({"_serialization_error": type(e).__name__})


def query_shape(query: Any) -> Dict[str, int]:
    """Форма поискового запроса без текста и хеша: текст может содержать персональные данные (P06)."""
    text = str(query or "").strip()
    return {"query_len": len(text), "query_tokens": len(text.split()),
            "query_has_digits": int(any(ch.isdigit() for ch in text))}


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
                 "errors", "cooldown", "retry_after_max", "bytes", "latency_sum", "latencies", "codes")

    def __init__(self):
        self.total = self.s2xx = self.s3xx = self.s4xx = self.s5xx = self.s429 = 0
        self.timeouts = self.conn_errors = self.errors = self.cooldown = self.bytes = 0
        self.retry_after_max: Optional[float] = None
        self.latency_sum = 0.0
        # Равномерная выборка (Algorithm R) по всем total запросам окна до flush (A03)
        self.latencies: List[float] = []
        self.codes: Dict[str, int] = {}


def merge_reservoirs(sample_a: List[float], count_a: int, sample_b: List[float], count_b: int,
                     size: int, rng=random) -> List[float]:
    """Равномерная выборка объединения двух популяций по их равномерным выборкам.

    Выбор без возвращения: следующий элемент берётся из популяции A с вероятностью
    оставшихся_A / (оставшихся_A + оставшихся_B). Требует len(sample_x) >= min(size, count_x).
    """
    a, b = list(sample_a), list(sample_b)
    rng.shuffle(a)
    rng.shuffle(b)
    left_a, left_b = count_a, count_b
    out: List[float] = []
    while len(out) < size and (left_a > 0 or left_b > 0):
        take_a = rng.random() * (left_a + left_b) < left_a
        if take_a and a:
            out.append(a.pop())
            left_a -= 1
        elif not take_a and b:
            out.append(b.pop())
            left_b -= 1
        elif a or b:  # выборка короче популяции (например, часть выборок удалена retention)
            out.append((a or b).pop())
            if a:
                left_a -= 1
            else:
                left_b -= 1
        else:
            break
    return out


class TelemetryService:
    """Структурированная телеметрия: in-memory буфер + фоновый сброс в SQLite."""

    def __init__(self, in_memory_buffer_size: int = 500):
        self._buffer: deque = deque(maxlen=in_memory_buffer_size)
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending_events: List[Dict[str, Any]] = []
        self._pending_http: Dict[tuple, _PendingAggregate] = {}
        self._diag_counts: Dict[tuple, int] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_prune = 0.0
        self.stats = {"dropped_events": 0, "dropped_http": 0, "suppressed_events": 0, "failed_flushes": 0}

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
        throttle_key: Optional[str] = None,
        throttle_per_minute: int = MAX_HTTP_DIAG_EVENTS_PER_MINUTE,
    ) -> Optional[Dict[str, Any]]:
        """Записывает структурированное событие. Fail-Open: исключения не выходят наружу.

        throttle_key — не больше throttle_per_minute событий в минуту с этим ключом (частые источники:
        поиск, ошибки воркеров); подавленные учитываются в stats["suppressed_events"].
        """
        if not telemetry_enabled():
            return None
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            if throttle_key is not None and not self._allow(now, throttle_key, throttle_per_minute):
                return None
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
                "message": sanitize_text(message)[:1000],
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
        error_kind: Optional[str] = None,
    ) -> None:
        """Учитывает один HTTP-запрос в бакетах minute/hour/day и в сводке текущей категории.

        error — имя класса исключения; текст сообщения не сохраняется (A01). error_kind — timeout /
        connection / other (если не передан, определяется по error). cooldown_rejected — запрос не
        отправлялся (HostCooldown): считается отдельно, total_requests = фактически отправленные.
        """
        if not telemetry_enabled():
            return
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            shop_name = shop or current_shop.get() or ""
            clean_host = (host or "").lower().strip()
            error_kind = (error_kind or classify_error(error)) if error else None
            error_name = safe_error_name(error)
            code_key = "cooldown" if cooldown_rejected else (error_kind if error else str(int(status_code or 0)))
            safe_url = sanitize_url(url) if url else ""

            self._record_http_diagnostics(now, clean_host, method, status_code, latency_ms, error_name,
                                          error_kind, retry_after, safe_url, shop_name, cooldown_rejected)

            with self._lock:
                trace = current_http_trace.get()
                if trace is not None:
                    trace_codes = trace.setdefault("status_codes", {})
                    trace_codes[code_key] = trace_codes.get(code_key, 0) + 1
                    if not cooldown_rejected:
                        trace["requests"] = trace.get("requests", 0) + 1
                        trace["bytes"] = trace.get("bytes", 0) + max(0, int(bytes_count or 0))

                for bucket_type, fmt in BUCKET_FORMATS.items():
                    key = (bucket_type, now.strftime(fmt), clean_host, shop_name)
                    agg = self._pending_http.get(key)
                    if agg is None:
                        if len(self._pending_http) >= MAX_PENDING_HTTP_KEYS:
                            self.stats["dropped_http"] += 1
                            continue
                        agg = self._pending_http[key] = _PendingAggregate()
                    agg.codes[code_key] = agg.codes.get(code_key, 0) + 1
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
                    # Algorithm R: память ограничена, выборка равномерна по всем запросам окна (A03)
                    if len(agg.latencies) < MAX_SAMPLES_PER_BUCKET:
                        agg.latencies.append(float(latency_ms))
                    else:
                        j = random.randrange(agg.total)
                        if j < MAX_SAMPLES_PER_BUCKET:
                            agg.latencies[j] = float(latency_ms)
        except Exception as e:
            print(f"[Telemetry] Ошибка учёта HTTP-метрики: {type(e).__name__}", file=sys.stderr)

    def _record_http_diagnostics(self, now, host, method, status_code, latency_ms, error_name, error_kind,
                                 retry_after, safe_url, shop_name, cooldown_rejected) -> None:
        """События по неуспешным ответам: 429, 4xx/5xx, транспортные ошибки (A05).

        scan_id/category/shop берутся из контекста в record_event. Частота ограничена на host+код в минуту.
        """
        if cooldown_rejected:
            return
        if error_name:
            event_type, severity, code = EVENT_HTTP_REQUEST, SEVERITY_ERROR, error_kind or "other"
            message = f"HTTP {method} {host}: {error_name} ({error_kind})"
        elif status_code == 429:
            event_type, severity, code = EVENT_HTTP_COOLDOWN, SEVERITY_WARNING, "429"
            message = f"HTTP 429 от {host} (Retry-After: {retry_after} с)"
        elif status_code >= 400:
            event_type, code = EVENT_HTTP_REQUEST, str(status_code)
            severity = SEVERITY_ERROR if status_code >= 500 else SEVERITY_WARNING
            message = f"HTTP {status_code} {method} {host}"
        else:
            return
        self.record_event(
            event_type=event_type, severity=severity, component=COMPONENT_HTTP, message=message,
            shop=shop_name or None,
            data={"host": host, "method": method, "status": status_code or None, "url": safe_url,
                  "error": error_name, "kind": error_kind, "retry_after": retry_after,
                  "latency_ms": round(float(latency_ms), 2)},
            throttle_key=f"http:{host}:{code}",
        )

    def _allow(self, now: datetime.datetime, key: str, per_minute: int) -> bool:
        minute = now.strftime(BUCKET_FORMATS["minute"])
        with self._lock:
            if len(self._diag_counts) > 1000:
                self._diag_counts = {k: v for k, v in self._diag_counts.items() if k[0] == minute}
            seen = self._diag_counts.get((minute, key), 0)
            self._diag_counts[(minute, key)] = seen + 1
            if seen >= per_minute:
                self.stats["suppressed_events"] += 1
                return False
        return True

    def record_system_error(self, component: str, where: str, exc: BaseException,
                            severity: str = SEVERITY_ERROR, data: Optional[Dict[str, Any]] = None) -> None:
        """Необработанная ошибка компонента: только имя класса и место, без текста исключения (A01)."""
        self.record_event(
            EVENT_SYSTEM_ERROR, severity, component, f"{where}: {type(exc).__name__}",
            data={"where": where, "error": type(exc).__name__, **(data or {})},
            throttle_key=f"system:{component}:{where}:{type(exc).__name__}", throttle_per_minute=5,
        )

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
                conn.isolation_level = None
                try:
                    # IMMEDIATE: чтение-слияние-запись агрегата атомарно и между процессами
                    conn.execute("BEGIN IMMEDIATE")
                    try:
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
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
                finally:
                    conn.close()
                return True
            except Exception as e:
                self.stats["failed_flushes"] += 1
                print(f"[Telemetry] Сброс в БД не удался ({type(e).__name__}), данные телеметрии пропущены",
                      file=sys.stderr)
                return False

    def _flush_aggregate(self, conn, key: tuple, agg: _PendingAggregate) -> None:
        where = "bucket_type = ? AND bucket_start = ? AND host = ? AND shop = ?"
        row = conn.execute(
            f"SELECT total_requests, status_codes FROM telemetry_http_aggregates WHERE {where}", key).fetchone()
        stored_total = row[0] if row else 0
        codes: Dict[str, int] = {}
        if row and row[1]:
            try:
                codes = {str(k): int(v) for k, v in json.loads(row[1]).items()}
            except Exception:
                codes = {}
        for code, n in agg.codes.items():
            codes[code] = codes.get(code, 0) + n

        p95 = None
        if agg.latencies:
            # Слияние равномерной выборки из БД (stored_total запросов) и окна (agg.total), A03
            stored = [r[0] for r in conn.execute(
                f"SELECT latency_ms FROM telemetry_http_samples WHERE {where}", key)]
            merged = merge_reservoirs(stored, stored_total, agg.latencies, agg.total, MAX_SAMPLES_PER_BUCKET)
            conn.execute(f"DELETE FROM telemetry_http_samples WHERE {where}", key)
            conn.executemany(
                "INSERT INTO telemetry_http_samples(bucket_type, bucket_start, host, shop, latency_ms) VALUES (?, ?, ?, ?, ?)",
                [(*key, v) for v in merged],
            )
            p95 = calculate_p95(merged)

        conn.execute(
            """INSERT INTO telemetry_http_aggregates(
                bucket_type, bucket_start, host, shop, total_requests,
                status_2xx, status_3xx, status_4xx, status_5xx, status_429,
                timeouts, connection_errors, errors, cooldown_rejections, retry_after_max_s,
                bytes_total, latency_sum_ms, latency_p95_ms, status_codes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0), ?)
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
                latency_p95_ms = COALESCE(?, latency_p95_ms),
                status_codes = excluded.status_codes""",
            (*key, agg.total, agg.s2xx, agg.s3xx, agg.s4xx, agg.s5xx, agg.s429,
             agg.timeouts, agg.conn_errors, agg.errors, agg.cooldown, agg.retry_after_max,
             agg.bytes, agg.latency_sum, p95, json.dumps(codes, sort_keys=True), p95),
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
                              bytes_total, latency_sum_ms, latency_p95_ms, status_codes
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
                try:
                    d["status_codes"] = json.loads(d["status_codes"] or "{}")
                except Exception:
                    d["status_codes"] = {}
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
