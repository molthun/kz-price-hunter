import json
import re
import time
import sqlite3
import hashlib
import secrets
import datetime
from typing import Optional, Dict, Any, List
from config import DB_PATH, get_scan_interval_seconds, merge_user_settings

def active_product_clause(alias=""):
    """Видимые предложения: не сняты полным обходом и виделись не дольше HIDE_AFTER_DAYS (P02, решение владельца).

    Устаревшие (Aging/Stale) показываются с бейджем свежести, а не скрываются: сбой магазина не убирает его каталог.
    """
    from data_quality import HIDE_AFTER_DAYS
    prefix = f"{alias}." if alias else ""
    return (f"{prefix}is_active = 1 AND julianday({prefix}updated_at) "
            f">= julianday('now') - {int(HIDE_AFTER_DAYS)}")


def fresh_benchmark_clause(alias="a"):
    """Арбитражный алерт актуален, пока цена конкурента-основания не устарела (≤ 72 ч; P02 C02).

    Для алертов без competitor_seen_at (созданных до P02) берётся время создания алерта.
    """
    from data_quality import AGING_HOURS
    prefix = f"{alias}." if alias else ""
    return (f"({prefix}alert_type NOT IN ('MARKET_ARBITRAGE', 'ARBITRAGE') OR "
            f"julianday(COALESCE({prefix}competitor_seen_at, {prefix}created_at)) >= julianday('now') - {int(AGING_HOURS)} / 24.0)")


def fresh_price_clause(alias=""):
    """Предложения с неустаревшей ценой (не Stale): только они дают алерты, скидки и лучшую цену (P02)."""
    from data_quality import AGING_HOURS
    prefix = f"{alias}." if alias else ""
    return (f"{prefix}is_active = 1 AND julianday({prefix}updated_at) "
            f">= julianday('now') - {int(AGING_HOURS)} / 24.0")


def reconcile_source(shop_key, source_url, products, complete=False):
    """Only a verified complete source may retire its previously observed offers."""
    ids = {str(p["id"]) for p in products}
    with get_connection() as conn:
        if complete and ids:
            conn.execute("UPDATE product_sources SET active=0 WHERE shop_key=? AND source_url=?", (shop_key, source_url))
        conn.executemany("""INSERT INTO product_sources(product_id,shop_key,source_url,active)
            VALUES (?,?,?,1) ON CONFLICT(product_id,shop_key,source_url) DO UPDATE SET active=1""",
            [(pid, shop_key, source_url) for pid in ids])
        if complete and ids:
            conn.execute("""UPDATE products SET is_active=0 WHERE id IN
                (SELECT product_id FROM product_sources WHERE shop_key=? AND source_url=?)
                AND NOT EXISTS (SELECT 1 FROM product_sources s WHERE s.product_id=products.id AND s.active=1)""",
                (shop_key, source_url))
        conn.commit()
    invalidate_alerts_cache()


def get_source_baseline(shop_key: str, source_url: str, kind: str) -> Optional[Dict[str, Any]]:
    """Норма источника (P02): медиана принятых результатов того же вида; при нехватке истории — число
    активных предложений источника (если их достаточно); иначе None."""
    from data_quality import BASELINE_WINDOW, MIN_BASELINE_ITEMS, baseline_from_history
    with get_connection() as conn:
        rows = conn.execute("""SELECT valid, with_image FROM source_scans
            WHERE shop_key=? AND source_url=? AND kind=? AND accepted=1
            ORDER BY finished_at DESC, id DESC LIMIT ?""", (shop_key, source_url, kind, BASELINE_WINDOW)).fetchall()
        baseline = baseline_from_history([dict(r) for r in rows])
        if baseline:
            return baseline
        active = conn.execute("SELECT COUNT(*) FROM product_sources WHERE shop_key=? AND source_url=? AND active=1",
                              (shop_key, source_url)).fetchone()[0]
    if active >= MIN_BASELINE_ITEMS:
        return {"valid": float(active), "image_share": None, "basis": "active_offers", "samples": 0}
    return None


def record_source_scan(shop_key, source_url, category, scan_id, started_at, kind, assessment, metrics) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reason = "; ".join(assessment["reasons"] + assessment["warnings"])[:500] or None
    with get_connection() as conn:
        conn.execute("""INSERT INTO source_scans (shop_key, source_url, category, scan_id, started_at, finished_at, kind,
                quality, reason, received, valid, rejected, duplicates, with_image, baseline, baseline_basis, accepted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shop_key, source_url, category, scan_id, started_at, now, kind, assessment["quality"], reason,
             metrics["received"], metrics["valid"], metrics["rejected"], metrics.get("duplicates", 0), metrics["with_image"],
             assessment["baseline"], assessment["basis"], int(bool(assessment["learn"]))))
        conn.commit()


def get_last_source_quality(shop_keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """Качество последнего обхода магазина: худшая оценка среди категорий его последнего scan_id (P02)."""
    order = {"failed": 0, "degraded": 1, "warning": 2, "unknown": 3, "ok": 4}
    result: Dict[str, Dict[str, Any]] = {}
    if not shop_keys:
        return result
    marks = ",".join("?" * len(shop_keys))
    with get_connection() as conn:
        rows = conn.execute(f"""SELECT shop_key, scan_id, quality, reason, category, finished_at FROM source_scans
            WHERE shop_key IN ({marks}) AND finished_at >= ? ORDER BY finished_at DESC, id DESC""",
            [*shop_keys, (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat()]
        ).fetchall()
    last_scan: Dict[str, Any] = {}
    for r in rows:
        key = r["shop_key"]
        if key not in last_scan:
            last_scan[key] = r["scan_id"]
        if r["scan_id"] != last_scan[key]:
            continue
        cur = result.get(key)
        if cur is None or order.get(r["quality"], 3) < order.get(cur["quality"], 3):
            result[key] = {"quality": r["quality"], "reason": r["reason"], "category": r["category"],
                           "finished_at": r["finished_at"]}
    return result


def prune_source_scans(days: Optional[int] = None) -> int:
    from data_quality import SOURCE_SCANS_RETENTION_DAYS
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=days or SOURCE_SCANS_RETENTION_DAYS)).isoformat()
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM source_scans WHERE finished_at < ?", (cutoff,)).rowcount
        conn.commit()
    return deleted


def record_search(query: str, city: Optional[str], source: str, outcome: str, results: int,
                  now: Optional[datetime.datetime] = None) -> None:
    """Записывает исход поиска в дневные агрегаты (P06).

    Хранится только то, что нужно отчёту: день, город, источник, исход и число результатов. Кто искал —
    не передаётся сюда вовсе. Текст запроса попадает в `search_queries` лишь начиная с третьего повтора
    и только если не похож на личные данные.
    """
    import search_analytics as sa
    if outcome not in sa.OUTCOMES:
        raise ValueError(f"Неизвестный исход поиска: {outcome}")
    if source not in sa.SOURCES:
        raise ValueError(f"Неизвестный источник поиска: {source}")
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    bucket = moment.strftime("%Y-%m-%d")
    # Город приводится к справочнику: в параметре может прийти произвольный текст с личными данными (F01)
    city_name = sa.canonical_city(city)
    results = max(0, int(results or 0))

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO search_stats (bucket, city, source, outcome, searches, results_sum)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(bucket, city, source, outcome) DO UPDATE SET
                searches = searches + 1, results_sum = results_sum + excluded.results_sum
        """, (bucket, city_name, source, outcome, results))

        text = sa.storable_text(query)
        if text:
            key = sa.query_key(query, city_name)
            conn.execute("""
                INSERT INTO search_query_seen (query_key, seen, last_seen) VALUES (?, 1, ?)
                ON CONFLICT(query_key) DO UPDATE SET seen = seen + 1, last_seen = excluded.last_seen
            """, (key, moment.isoformat()))
            seen = conn.execute("SELECT seen FROM search_query_seen WHERE query_key = ?", (key,)).fetchone()[0]
            if seen >= sa.MIN_OCCURRENCES_TO_STORE_TEXT:
                conn.execute(f"""
                    INSERT INTO search_queries (bucket, normalized_query, city, searches, {outcome},
                                                results_sum, last_seen)
                    VALUES (?, ?, ?, 1, 1, ?, ?)
                    ON CONFLICT(bucket, normalized_query, city) DO UPDATE SET
                        searches = searches + 1, {outcome} = {outcome} + 1,
                        results_sum = results_sum + excluded.results_sum, last_seen = excluded.last_seen
                """, (bucket, text, city_name, results, moment.isoformat()))
        conn.commit()


def search_totals(days: int = 7, city: Optional[str] = None,
                  now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Сводка по поиску за период: исходы, доля успеха и доля ошибок, разбивка по источникам."""
    import search_analytics as sa
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    where, params = "bucket >= ?", [since]
    if city and city != sa.CITY_ALL:
        where += " AND city = ?"
        params.append(sa.canonical_city(city))

    counts = {k: 0 for k in sa.OUTCOMES}
    by_source: Dict[str, Dict[str, Any]] = {}
    results_sum = 0
    with get_connection() as conn:
        for row in conn.execute(f"SELECT source, outcome, SUM(searches) AS n, SUM(results_sum) AS r "
                                f"FROM search_stats WHERE {where} GROUP BY source, outcome", params):
            counts[row["outcome"]] = counts.get(row["outcome"], 0) + row["n"]
            results_sum += row["r"] or 0
            src = by_source.setdefault(row["source"], {k: 0 for k in sa.OUTCOMES})
            src[row["outcome"]] = src.get(row["outcome"], 0) + row["n"]

    total = sum(counts.values())
    for src in by_source.values():
        src["total"] = sum(src[k] for k in sa.OUTCOMES)
        src["success_rate"] = sa.success_rate(src)
        src["error_rate"] = sa.error_rate(src)
    return {
        "days": int(days),
        "city": city or "Все",
        "total": total,
        "counts": counts,
        "success_rate": sa.success_rate(counts),
        "error_rate": sa.error_rate(counts),
        "avg_results": round(results_sum / total, 1) if total else None,
        "by_source": by_source,
    }


def search_queries(days: int = 7, outcome: Optional[str] = None, limit: int = 50,
                   city: Optional[str] = None, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Запросы с текстом (от третьего повтора): самые частые или самые проблемные за период."""
    import search_analytics as sa
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    where, params = "bucket >= ?", [since]
    if city and city != sa.CITY_ALL:
        where += " AND city = ?"
        params.append(sa.canonical_city(city))
    # «Плохо отвечаем» — только запросы, где людям действительно нечего было показать
    having = " HAVING bad > 0" if outcome == "bad" else ""
    order = "bad DESC, searches DESC" if outcome == "bad" else "searches DESC"
    if outcome in sa.OUTCOMES:
        order = f"{outcome} DESC, searches DESC"

    rows = []
    with get_connection() as conn:
        for row in conn.execute(f"""
            SELECT normalized_query, city, SUM(searches) AS searches, SUM(found) AS found, SUM(weak) AS weak,
                   SUM(not_found) AS not_found, SUM(error) AS error,
                   SUM(weak) + SUM(not_found) AS bad, MAX(last_seen) AS last_seen
            FROM search_queries WHERE {where}
            GROUP BY normalized_query, city{having}
            ORDER BY {order} LIMIT ?
        """, params + [max(1, int(limit))]):
            item = dict(row)
            item["success_rate"] = sa.success_rate(item)
            rows.append(item)
    return rows


def prune_search_analytics(days: Optional[int] = None,
                           now: Optional[datetime.datetime] = None) -> int:
    """Удаляет аналитику поиска старше срока хранения (P06: 90 дней) вместе со счётчиком повторов."""
    import search_analytics as sa
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff_day = (moment - datetime.timedelta(days=days or sa.RETENTION_DAYS)).strftime("%Y-%m-%d")
    cutoff_ts = (moment - datetime.timedelta(days=days or sa.RETENTION_DAYS)).isoformat()
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM search_stats WHERE bucket < ?", (cutoff_day,)).rowcount
        deleted += conn.execute("DELETE FROM search_queries WHERE bucket < ?", (cutoff_day,)).rowcount
        deleted += conn.execute("DELETE FROM search_query_seen WHERE last_seen < ?", (cutoff_ts,)).rowcount
        conn.commit()
    return deleted


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    # Магазины сканируются параллельно: ждем освобождения блокировки вместо ошибки "database is locked"
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def _columns(cursor, table: str) -> set:
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}


def _add_column(cursor, table: str, column: str, declaration: str) -> None:
    """Аддитивная колонка: добавляется, только если её нет (ошибки не скрываются)."""
    if column not in _columns(cursor, table):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _create_schema(cursor) -> None:
    """Таблицы, колонки, индексы и триггеры. Идемпотентно и без изменения данных."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            shop TEXT DEFAULT 'DNS Казахстан',
            title TEXT NOT NULL,
            category TEXT,
            url TEXT NOT NULL,
            image_url TEXT,
            current_price INTEGER NOT NULL,
            first_seen_price INTEGER NOT NULL,
            old_price_on_site INTEGER DEFAULT 0,
            min_price INTEGER NOT NULL,
            max_price INTEGER NOT NULL,
            canonical_key TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop TEXT DEFAULT 'DNS Казахстан',
            city TEXT DEFAULT 'Астана',
            product_id TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            old_price INTEGER,
            new_price INTEGER NOT NULL,
            discount_pct REAL,
            savings_kzt INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)
    # Таблица постоянного кэша канонических ключей моделей (для предотвращения повторных обращений к AI)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS title_canonical_cache (
            title_hash TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Названия, которые уже отправлялись в AI: неудачи не повторяются при каждом обходе
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS title_ai_attempts (
            title_hash TEXT PRIMARY KEY,
            attempted_at TIMESTAMP NOT NULL
        )
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS schema_metadata (name TEXT PRIMARY KEY, value TEXT)")
    # Пользователи (вход через Telegram) и их личные настройки
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            photo_url TEXT,
            settings TEXT NOT NULL DEFAULT '{}',
            is_blocked INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Сессии: в базе хранится только SHA-256 токена из cookie
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    # Состояние сканирования по каждому магазину: свежесть считается отдельно,
    # поэтому прерванный цикл (например, из-за перезапуска контейнера) догоняется по отставшим магазинам
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shop_scans (
            shop_key TEXT PRIMARY KEY,
            last_attempt_at TIMESTAMP,
            last_success_at TIMESTAMP,
            last_items INTEGER DEFAULT 0,
            last_duration_sec REAL DEFAULT 0,
            last_error TEXT
        )
    """)
    cursor.execute("""CREATE TABLE IF NOT EXISTS product_sources (
        product_id TEXT NOT NULL, shop_key TEXT NOT NULL, source_url TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(product_id, shop_key, source_url)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS notification_outbox (
        id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, last_error TEXT,
        UNIQUE(alert_id, user_id)
    )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracked_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            query TEXT NOT NULL,
            master_category TEXT,
            search_count INTEGER DEFAULT 1,
            last_searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_scanned_at TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            is_hot INTEGER DEFAULT 0
        )
    """)
    # Аренда планировщика: обходит только один процесс (gui.py, main.py, второй контейнер)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_lease (
            name TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    # История наблюдений: строка пишется только при изменении цены или зачёркнутой цены
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_observations (
            product_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            old_price_on_site INTEGER NOT NULL DEFAULT 0,
            observed_at TEXT NOT NULL
        )
    """)

    # Аддитивные колонки старых баз: сохраняют данные и безопасны при повторе
    for table, column, declaration in (
        ("products", "shop", "TEXT DEFAULT 'DNS Казахстан'"),
        ("products", "city", "TEXT DEFAULT 'Астана'"),
        ("products", "canonical_key", "TEXT"),
        ("products", "description", "TEXT"),
        ("products", "is_active", "INTEGER NOT NULL DEFAULT 1"),
        ("products", "old_price_on_site", "INTEGER DEFAULT 0"),
        ("alerts", "shop", "TEXT DEFAULT 'DNS Казахстан'"),
        ("alerts", "city", "TEXT DEFAULT 'Астана'"),
        ("alerts", "competitor_shop", "TEXT"),
        ("alerts", "is_dismissed", "INTEGER DEFAULT 0"),
        # P02 (C02): когда в последний раз наблюдалась цена конкурента — основание арбитражного алерта
        ("alerts", "competitor_seen_at", "TEXT"),
        ("shop_scans", "status", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("shop_scans", "failure_count", "INTEGER NOT NULL DEFAULT 0"),
        ("shop_scans", "next_retry_at", "REAL"),
        ("tracked_categories", "is_hot", "INTEGER DEFAULT 0"),
    ):
        _add_column(cursor, table, column, declaration)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_id ON products(id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_city ON products(city)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_shop_city_price ON products(shop, city, current_price)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_canonical_key ON products(canonical_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_product ON alerts(product_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_canonical_cache_key ON title_canonical_cache(canonical_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_due ON notification_outbox(status, next_attempt_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracked_cat_active ON tracked_categories(is_active, last_scanned_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_obs_product ON price_observations(product_id, observed_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_obs_time ON price_observations(observed_at)")

    # Полнотекстовый индекс FTS5; перестраивается миграцией, а не при каждом старте
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            id UNINDEXED,
            title,
            shop,
            city,
            category,
            content='products',
            content_rowid='rowid'
        )
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
            INSERT INTO products_fts(rowid, id, title, shop, city, category)
            VALUES (new.rowid, new.id, new.title, new.shop, new.city, new.category);
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, id, title, shop, city, category)
            VALUES('delete', old.rowid, old.id, old.title, old.shop, old.city, old.category);
        END;
    """)
    # Только при изменении индексируемых полей: обновление цены не переписывает индекс
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE OF id, title, shop, city, category ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, id, title, shop, city, category)
            VALUES('delete', old.rowid, old.id, old.title, old.shop, old.city, old.category);
            INSERT INTO products_fts(rowid, id, title, shop, city, category)
            VALUES (new.rowid, new.id, new.title, new.shop, new.city, new.category);
        END;
    """)

    # Итоги обхода источников (магазин + URL категории) с метриками качества (P02). Принятые (accepted=1)
    # результаты образуют baseline источника; degraded/warning/failed его не обучают.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS source_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_key TEXT NOT NULL,
            source_url TEXT NOT NULL,
            category TEXT,
            scan_id TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            quality TEXT NOT NULL,
            reason TEXT,
            received INTEGER NOT NULL DEFAULT 0,
            valid INTEGER NOT NULL DEFAULT 0,
            rejected INTEGER NOT NULL DEFAULT 0,
            with_image INTEGER NOT NULL DEFAULT 0,
            baseline REAL,
            baseline_basis TEXT,
            accepted INTEGER NOT NULL DEFAULT 0
        )
    """)
    _add_column(cursor, "source_scans", "duplicates", "INTEGER NOT NULL DEFAULT 0")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_scans_source ON source_scans(shop_key, source_url, kind, accepted, finished_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_scans_time ON source_scans(finished_at)")

    # Таблицы структурированной телеметрии и HTTP-агрегатов (P01). shop хранится как '' вместо
    # NULL: в UNIQUE значения NULL различны, и upsert агрегата создавал бы строку на каждый запрос.
    # telemetry_http_samples — ограниченная выборка задержек на бакет для p95 (не среднее из p95).
    # Таблицы только добавляются и не меняют существующие данные, поэтому schema_version не повышается:
    # образ предыдущей версии (5.7.1) стартует на этой базе и просто не использует их (откат без бэкапа).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            timestamp TEXT NOT NULL,
            type TEXT NOT NULL,
            severity TEXT NOT NULL,
            component TEXT NOT NULL,
            shop TEXT,
            city TEXT,
            category TEXT,
            scan_id TEXT,
            request_id TEXT,
            message TEXT NOT NULL,
            data_json TEXT,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_events_ts ON telemetry_events(timestamp, type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_events_scan ON telemetry_events(scan_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_events_comp_sev ON telemetry_events(component, severity)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_http_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_type TEXT NOT NULL,
            bucket_start TEXT NOT NULL,
            host TEXT NOT NULL,
            shop TEXT NOT NULL DEFAULT '',
            total_requests INTEGER DEFAULT 0,
            status_2xx INTEGER DEFAULT 0,
            status_3xx INTEGER DEFAULT 0,
            status_4xx INTEGER DEFAULT 0,
            status_5xx INTEGER DEFAULT 0,
            status_429 INTEGER DEFAULT 0,
            timeouts INTEGER DEFAULT 0,
            connection_errors INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            cooldown_rejections INTEGER DEFAULT 0,
            retry_after_max_s REAL,
            bytes_total INTEGER DEFAULT 0,
            latency_sum_ms REAL DEFAULT 0,
            latency_p95_ms REAL DEFAULT 0,
            UNIQUE(bucket_type, bucket_start, host, shop)
        )
    """)
    # Распределение кодов ответа {"200": n, "403": n, "timeout": n, "cooldown": n} (A05)
    _add_column(cursor, "telemetry_http_aggregates", "status_codes", "TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_http_agg_bucket ON telemetry_http_aggregates(bucket_type, bucket_start)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_http_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_type TEXT NOT NULL,
            bucket_start TEXT NOT NULL,
            host TEXT NOT NULL,
            shop TEXT NOT NULL DEFAULT '',
            latency_ms REAL NOT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_http_samples ON telemetry_http_samples(bucket_type, bucket_start, host, shop)")

    # Аналитика поиска (P06). Только агрегаты по дням: ни идентификатора пользователя, ни Telegram ID,
    # ни IP, ни времени с точностью до запроса. Таблицы добавляются, существующие данные не меняются —
    # schema_version не повышается (откат на предыдущий образ без восстановления базы).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_stats (
            bucket TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            outcome TEXT NOT NULL,
            searches INTEGER NOT NULL DEFAULT 0,
            results_sum INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bucket, city, source, outcome)
        )
    """)
    # Счётчик повторов по необратимому ключу: текст запроса сохраняется только начиная с третьего раза,
    # а по ключу его не восстановить, поэтому редкие (и потому более узнаваемые) запросы текстом не хранятся
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_query_seen (
            query_key TEXT PRIMARY KEY,
            seen INTEGER NOT NULL DEFAULT 0,
            last_seen TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_queries (
            bucket TEXT NOT NULL,
            normalized_query TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            searches INTEGER NOT NULL DEFAULT 0,
            found INTEGER NOT NULL DEFAULT 0,
            weak INTEGER NOT NULL DEFAULT 0,
            not_found INTEGER NOT NULL DEFAULT 0,
            error INTEGER NOT NULL DEFAULT 0,
            results_sum INTEGER NOT NULL DEFAULT 0,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (bucket, normalized_query, city)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_queries_bucket ON search_queries(bucket)")


# ---------------------------------------------------------------------------
# Пронумерованные миграции данных. Каждая выполняется один раз, в своей транзакции;
# номер последней применённой хранится в schema_metadata.schema_version.
# Перед применением ожидающих миграций к непустой базе создаётся бэкап (SQLite backup API).
# ---------------------------------------------------------------------------

def _migration_legacy_identity_v2(conn) -> None:
    """Прежний шаг identity_v2 (пересчёт canonical_key), если он ещё не выполнялся."""
    if conn.execute("SELECT 1 FROM schema_metadata WHERE name = 'identity_v2'").fetchone():
        return
    from model_matching import extract_canonical_key
    rows = conn.execute("SELECT id, title FROM products").fetchall()
    conn.executemany("UPDATE products SET canonical_key = ? WHERE id = ?",
                     [(extract_canonical_key(r['title']), r['id']) for r in rows])
    conn.execute("DELETE FROM title_canonical_cache")
    conn.execute("INSERT INTO schema_metadata VALUES ('identity_v2', '1')")


def _migration_legacy_cleanups(conn) -> None:
    """Разовые чистки, которые раньше выполнялись при каждом старте."""
    # Ссылки на картинки Белого Ветра и товары Kaspi Магазина (kaspi.kz/p/ -> kaspi.kz/shop/p/)
    conn.execute("UPDATE products SET image_url = REPLACE(image_url, 'https://shop.kz//static.shop.kz', 'https://static.shop.kz') WHERE image_url LIKE 'https://shop.kz//static.shop.kz%'")
    conn.execute("UPDATE products SET url = REPLACE(url, 'https://kaspi.kz/p/', 'https://kaspi.kz/shop/p/') WHERE url LIKE '%kaspi.kz/p/%'")
    # Испорченные аномалии и цены (глюки парсинга DNS/shopkz)
    conn.execute("""
        DELETE FROM alerts
        WHERE old_price > 10000000
           OR new_price > 10000000
           OR savings_kzt > 10000000
           OR old_price <= 0
           OR new_price <= 0
           OR (alert_type = 'ZERO_GLITCH' AND (old_price = 722280 OR product_id LIKE '%9aeu789t40278%'))
    """)
    conn.execute("UPDATE products SET max_price = current_price WHERE max_price > 10000000")
    conn.execute("UPDATE products SET first_seen_price = current_price WHERE first_seen_price > 10000000")
    conn.execute("DELETE FROM products WHERE current_price > 10000000")


def _migration_offer_identity_reset(conn) -> None:
    """Аудит H05: id предложения включает город (kaspi_1@astana). Каталог пересобирается с нуля.

    Решение владельца 18.09.2026: старый каталог не переносится (в нём цены разных городов
    перезаписывали друг друга, цены Flip были занижены в ~1000 раз). Удаляются товары, алерты,
    источники, очередь уведомлений и история цен; сбрасывается состояние обходов, чтобы все
    магазины обошлись сразу. Пользователи, сессии, личные настройки, отслеживаемые категории
    и кэш AI-ключей сохраняются. Бэкап до миграции создаёт run_migrations().
    """
    conn.execute("DELETE FROM notification_outbox")
    conn.execute("DELETE FROM alerts")
    conn.execute("DELETE FROM product_sources")
    conn.execute("DELETE FROM price_observations")
    conn.execute("DELETE FROM products")
    conn.execute("INSERT INTO products_fts(products_fts) VALUES('delete-all')")
    conn.execute("DELETE FROM shop_scans")
    conn.execute("UPDATE tracked_categories SET last_scanned_at = NULL")
    conn.execute("INSERT OR REPLACE INTO schema_metadata VALUES ('identity_version', '3')")
    # Первый обход пересобранного каталога заново найдёт все текущие скидки: пользователи их уже
    # получали, поэтому на время полного цикла алерты пишутся в ленту без рассылки в Telegram
    muted_until = time.time() + CATALOG_REBUILD_MUTE_SECONDS
    conn.execute("INSERT OR REPLACE INTO schema_metadata VALUES ('notifications_muted_until', ?)", (str(muted_until),))


def _migration_fts_update_trigger(conn) -> None:
    """Триггер FTS только на индексируемые поля (старый срабатывал на каждое обновление цены) + rebuild."""
    conn.execute("DROP TRIGGER IF EXISTS products_au")
    conn.execute("""
        CREATE TRIGGER products_au AFTER UPDATE OF id, title, shop, city, category ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, id, title, shop, city, category)
            VALUES('delete', old.rowid, old.id, old.title, old.shop, old.city, old.category);
            INSERT INTO products_fts(rowid, id, title, shop, city, category)
            VALUES (new.rowid, new.id, new.title, new.shop, new.city, new.category);
        END;
    """)
    conn.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")


def offer_namespace_plan(conn) -> Dict[str, Any]:
    """План переименования id без префикса магазина (R-H01). Ничего не меняет.

    renames — [(старый id, новый id, магазин)]; collided — id, в которые писали несколько магазинов
    (у одного product_id источники разных shop_key): их история и алерты смешаны;
    conflicts — новый id уже занят другой записью.
    """
    from offer_identity import namespaced_id
    renames, conflicts = [], []
    existing = {r[0] for r in conn.execute("SELECT id FROM products")}
    for pid, shop in conn.execute("SELECT id, shop FROM products").fetchall():
        base, sep, city = pid.partition("@")
        new_base = namespaced_id(base, shop)
        if new_base == base:
            continue
        new_id = f"{new_base}{sep}{city}"
        (conflicts if new_id in existing else renames).append((pid, new_id, shop))
    collided = {r[0] for r in conn.execute(
        "SELECT product_id FROM product_sources GROUP BY product_id HAVING COUNT(DISTINCT shop_key) > 1")}
    return {"renames": renames, "conflicts": conflicts,
            "collided": sorted(pid for pid, _, _ in renames + conflicts if pid in collided)}


def _migration_offer_namespace(conn) -> None:
    """R-H01: id без префикса магазина получают префикс; каталог не пересобирается.

    Переименование проходит по products, product_sources (по shop_key каждой строки), алертам,
    истории цен и неотправленным уведомлениям. Записи, в которые уже писали несколько магазинов,
    нельзя разделить: они переименовываются по текущему магазину, а их история цен, исходная и
    минимальная/максимальная цены и алерты сбрасываются. Конфликты (новый id уже занят)
    сливаются в существующую запись.
    """
    from offer_identity import id_prefix
    plan = offer_namespace_plan(conn)
    print(f"[DB] Миграция 5: переименований {len(plan['renames'])}, конфликтов {len(plan['conflicts'])}, "
          f"смешанных записей (история сброшена) {len(plan['collided'])}")
    collided = set(plan["collided"])
    renamed = {old: new for old, new, _ in plan["renames"] + plan["conflicts"]}

    for old, new, _ in plan["conflicts"]:
        conn.execute("DELETE FROM products WHERE id = ?", (old,))
    for old, new, _ in plan["renames"]:
        conn.execute("UPDATE products SET id = ? WHERE id = ?", (new, old))

    for old, new in renamed.items():
        conn.execute("UPDATE alerts SET product_id = ? WHERE product_id = ?", (new, old))
        conn.execute("UPDATE price_observations SET product_id = ? WHERE product_id = ?", (new, old))

    # Источники — по shop_key каждой строки: у смешанной записи источники разных магазинов
    # получают каждый свой префикс
    for product_id, shop_key, source_url in conn.execute("SELECT product_id, shop_key, source_url FROM product_sources").fetchall():
        base, sep, city = product_id.partition("@")
        prefix = id_prefix(shop_key) + "_"
        if base.startswith(prefix) or shop_key not in _shop_keys():
            continue
        conn.execute("""UPDATE OR IGNORE product_sources SET product_id = ?
                        WHERE product_id = ? AND shop_key = ? AND source_url = ?""",
                     (f"{prefix}{base}{sep}{city}", product_id, shop_key, source_url))

    # Смешанная история недостоверна: сброс до текущей цены, без выдуманных точек
    for old in collided:
        new = renamed[old]
        conn.execute("DELETE FROM price_observations WHERE product_id = ?", (new,))
        conn.execute("""UPDATE products SET first_seen_price = current_price, min_price = current_price,
                        max_price = current_price, old_price_on_site = 0 WHERE id = ?""", (new,))
        alert_ids = [r[0] for r in conn.execute("SELECT id FROM alerts WHERE product_id = ?", (new,))]
        for alert_id in alert_ids:
            conn.execute("DELETE FROM notification_outbox WHERE alert_id = ?", (alert_id,))
        conn.execute("DELETE FROM alerts WHERE product_id = ?", (new,))

    # Неотправленные уведомления ищут товар по id из payload
    for row_id, payload in conn.execute("SELECT id, payload FROM notification_outbox WHERE status = 'pending'").fetchall():
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        product = data.get("product") or {}
        new = renamed.get(str(product.get("id")))
        if new:
            product["id"] = new
            conn.execute("UPDATE notification_outbox SET payload = ? WHERE id = ?",
                         (json.dumps(data, ensure_ascii=False), row_id))


def _shop_keys():
    from config import SHOP_KEYS
    return SHOP_KEYS


CATALOG_REBUILD_MUTE_SECONDS = 6 * 3600


def notifications_muted() -> bool:
    """Тихий режим после пересборки каталога: алерты без рассылки (см. миграцию 3)."""
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM schema_metadata WHERE name = 'notifications_muted_until'").fetchone()
    try:
        return bool(row) and float(row[0]) > time.time()
    except (TypeError, ValueError):
        return False


MIGRATIONS = (
    (1, "legacy_identity_v2", _migration_legacy_identity_v2),
    (2, "legacy_cleanups", _migration_legacy_cleanups),
    (3, "offer_identity_reset", _migration_offer_identity_reset),
    (4, "fts_update_trigger", _migration_fts_update_trigger),
    (5, "offer_namespace", _migration_offer_namespace),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def get_schema_version(conn) -> int:
    row = conn.execute("SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def backup_database(label: str) -> Optional[str]:
    """Консистентная копия через SQLite backup API в DATA_DIR/backups; проверяется quick_check."""
    from config import DATA_DIR
    if not DB_PATH.exists():
        return None
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Микросекунды + случайный суффикс: два бэкапа в одну секунду не перезапишут друг друга
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = backup_dir / f"prices-{label}-{stamp}-{secrets.token_hex(3)}.db"
    started = time.monotonic()
    outcome = "failed"
    try:
        src = sqlite3.connect(DB_PATH, timeout=30)
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"Бэкап не прошёл quick_check: {result}")
        finally:
            dst.close()
            src.close()
        outcome = "ok"
        return str(dest)
    finally:
        _record_backup(label, outcome, dest, time.monotonic() - started)


def _record_backup(label, outcome, dest, duration) -> None:
    try:
        from telemetry import telemetry, EVENT_BACKUP_RUN, SEVERITY_INFO, SEVERITY_ERROR, COMPONENT_BACKUP
        size = dest.stat().st_size if outcome == "ok" and dest.exists() else None
        telemetry.record_event(
            EVENT_BACKUP_RUN, SEVERITY_INFO if outcome == "ok" else SEVERITY_ERROR, COMPONENT_BACKUP,
            f"Бэкап {label}: {outcome}",
            data={"label": label, "outcome": outcome, "file": dest.name, "size_bytes": size,
                  "duration_sec": round(duration, 2)})
    except Exception:
        pass


class SchemaTooNew(RuntimeError):
    """База обновлена более новой версией приложения, чем запущенная."""


def _existing_state(conn) -> Dict[str, Any]:
    """Версия схемы и наличие данных — без создания таблиц (работает и на пустом файле)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    version = get_schema_version(conn) if "schema_metadata" in tables else 0
    has_data = any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                   for t in ("products", "alerts", "users") if t in tables)
    return {"version": version, "has_data": has_data}


def check_schema_compatible(conn) -> int:
    """Старое приложение на базе новой схемы (откат образа без восстановления базы) не стартует:
    оно писало бы данные в формате, который новая схема уже изменила (R-M12)."""
    version = _existing_state(conn)["version"]
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(
            f"Версия схемы базы {version} новее, чем поддерживает приложение ({SCHEMA_VERSION}). "
            "Запустите версию приложения, которая создала эту базу, или восстановите бэкап "
            "data/backups/prices-pre-v*.db, сделанный перед обновлением.")
    return version


def run_migrations(backup_done: bool = False) -> List[int]:
    """Применяет ожидающие миграции по порядку; ошибка останавливает старт, транзакция откатывается."""
    with get_connection() as conn:
        current = check_schema_compatible(conn)
        has_data = _existing_state(conn)["has_data"]
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return []
    if has_data and not backup_done:
        path = backup_database(f"pre-v{pending[-1][0]}")
        print(f"[DB] Бэкап перед миграциями {current}→{pending[-1][0]}: {path}")

    applied = []
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        for version, name, migrate in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Повторная проверка под блокировкой: второй процесс не применит миграцию дважды
                if get_schema_version(conn) >= version:
                    conn.execute("ROLLBACK")
                    continue
                migrate(conn)
                conn.execute("INSERT OR REPLACE INTO schema_metadata VALUES ('schema_version', ?)", (str(version),))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            applied.append(version)
            print(f"[DB] Миграция {version} ({name}) применена")
    finally:
        conn.close()
    if 3 in applied and has_data:
        # Вернуть место после удаления каталога (вне транзакции)
        vacuum_conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        try:
            vacuum_conn.execute("VACUUM")
        finally:
            vacuum_conn.close()
    return applied


def init_db():
    # Сначала проверка совместимости и бэкап, потом любые изменения схемы (R-M12):
    # даже аддитивные колонки создаются уже после копии исходной базы
    backup_done = False
    if DB_PATH.exists():
        with get_connection() as conn:
            state = _existing_state(conn)
            check_schema_compatible(conn)
        if state["has_data"] and state["version"] < SCHEMA_VERSION:
            path = backup_database(f"pre-v{SCHEMA_VERSION}")
            print(f"[DB] Бэкап перед обновлением схемы {state['version']}→{SCHEMA_VERSION}: {path}")
            backup_done = True
    with get_connection() as conn:
        _create_schema(conn.cursor())
        conn.commit()
    run_migrations(backup_done=backup_done)
    cleanup_expired_sessions()
    reset_stale_running_scans()

def acquire_scheduler_lease(owner: str, ttl_seconds: float, name: str = "scan") -> bool:
    """Берёт или продлевает аренду; False — действующая аренда у другого процесса."""
    now = time.time()
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT owner, expires_at FROM scheduler_lease WHERE name = ?", (name,)).fetchone()
        if row and row[0] != owner and row[1] > now:
            conn.execute("ROLLBACK")
            return False
        conn.execute("INSERT OR REPLACE INTO scheduler_lease (name, owner, expires_at) VALUES (?, ?, ?)",
                     (name, owner, now + ttl_seconds))
        conn.execute("COMMIT")
        return True
    finally:
        conn.close()


def release_scheduler_lease(owner: str, name: str = "scan") -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM scheduler_lease WHERE name = ? AND owner = ?", (name, owner))
        conn.commit()


def get_metadata(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT value FROM schema_metadata WHERE name = ?", (name,)).fetchone()
            return row[0] if row else default
    except sqlite3.OperationalError:
        return default


def set_metadata(name: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO schema_metadata (name, value) VALUES (?, ?)", (name, str(value)))
        conn.commit()


def get_cached_canonical_key(title: str) -> Optional[str]:
    """Возвращает сохраненный канонический ключ из постоянного кэша SQLite."""
    if not title:
        return None
    th = hashlib.sha256(title.strip().encode("utf-8")).hexdigest()
    with get_connection() as conn:
        row = conn.execute("SELECT canonical_key FROM title_canonical_cache WHERE title_hash = ?", (th,)).fetchone()
        return row[0] if row else None

def save_cached_canonical_key(title: str, canonical_key: str):
    """Сохраняет канонический ключ товара в постоянный кэш SQLite."""
    if not title or not canonical_key:
        return
    th = hashlib.sha256(title.strip().encode("utf-8")).hexdigest()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO title_canonical_cache (title_hash, title, canonical_key)
            VALUES (?, ?, ?)
            ON CONFLICT(title_hash) DO UPDATE SET canonical_key = excluded.canonical_key
        """, (th, title.strip(), canonical_key.strip().lower()))
        conn.commit()

def save_cached_canonical_keys_batch(mapping: Dict[str, str]):
    """Пакетное сохранение канонических ключей в постоянный кэш SQLite."""
    if not mapping:
        return
    rows = [
        (hashlib.sha256(t.strip().encode("utf-8")).hexdigest(), t.strip(), k.strip().lower())
        for t, k in mapping.items() if t and k
    ]
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO title_canonical_cache (title_hash, title, canonical_key)
            VALUES (?, ?, ?)
            ON CONFLICT(title_hash) DO UPDATE SET canonical_key = excluded.canonical_key
        """, rows)
        conn.commit()

def _title_hash(title: str) -> str:
    return hashlib.sha256(title.strip().encode("utf-8")).hexdigest()

def _chunks(items: List[Any], size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def get_cached_canonical_keys_batch(titles: List[str]) -> Dict[str, str]:
    """Канонические ключи из кэша для набора названий одним запросом на 500 штук."""
    by_hash = {_title_hash(t): t for t in titles if t and t.strip()}
    found: Dict[str, str] = {}
    with get_connection() as conn:
        for chunk in _chunks(list(by_hash)):
            marks = ",".join("?" * len(chunk))
            for th, key in conn.execute(f"SELECT title_hash, canonical_key FROM title_canonical_cache WHERE title_hash IN ({marks})", chunk):
                if key:
                    found[by_hash[th]] = key
    return found

def get_recent_ai_attempts(titles: List[str], max_age_days: int) -> set:
    """Названия, которые отправлялись в AI за последние max_age_days дней."""
    by_hash = {_title_hash(t): t for t in titles if t and t.strip()}
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)).isoformat()
    recent = set()
    with get_connection() as conn:
        for chunk in _chunks(list(by_hash)):
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(f"SELECT title_hash FROM title_ai_attempts WHERE attempted_at >= ? AND title_hash IN ({marks})", [since, *chunk])
            recent.update(by_hash[r[0]] for r in rows)
    return recent

def record_ai_attempts(titles: List[str]) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = [(_title_hash(t), now) for t in titles if t and t.strip()]
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO title_ai_attempts (title_hash, attempted_at) VALUES (?, ?)
            ON CONFLICT(title_hash) DO UPDATE SET attempted_at = excluded.attempted_at
        """, rows)
        conn.commit()

def update_products_canonical_keys(pairs: List[tuple]) -> int:
    """Проставляет канонические ключи товарам: [(canonical_key, product_id), ...]."""
    if not pairs:
        return 0
    with get_connection() as conn:
        conn.executemany("UPDATE products SET canonical_key = ? WHERE id = ?", pairs)
        conn.commit()
    return len(pairs)

PRICE_HISTORY_RETENTION_DAYS = 180


def _record_observation(cursor, pid: str, existing, price: int, old_on_site: int, now: str) -> None:
    """Наблюдение пишется для нового предложения и при изменении цены или зачёркнутой цены."""
    if existing is not None and existing["current_price"] == price and (existing["old_price_on_site"] or 0) == old_on_site:
        return
    cursor.execute("INSERT INTO price_observations (product_id, price, old_price_on_site, observed_at) VALUES (?, ?, ?, ?)",
                   (pid, price, old_on_site, now))


def get_price_observations(product_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Изменения цены предложения, новые первыми."""
    with get_connection() as conn:
        rows = conn.execute("""SELECT price, old_price_on_site, observed_at FROM price_observations
                               WHERE product_id = ? ORDER BY observed_at DESC LIMIT ?""",
                            (str(product_id), limit)).fetchall()
        return [dict(r) for r in rows]


def prune_price_observations(days: int = PRICE_HISTORY_RETENTION_DAYS) -> int:
    """Удаляет наблюдения старше срока хранения; возвращает число удалённых строк."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM price_observations WHERE observed_at < ?", (cutoff,)).rowcount
        conn.commit()
        return deleted


def site_old_price(value: Any, current_price: int) -> int:
    """Зачёркнутая цена текущего наблюдения: только больше текущей, иначе 0 (скидка снята)."""
    from scrapers.base import price_value
    old = price_value(value)
    return old if old > current_price else 0


def save_or_update_product(p: Dict[str, Any]) -> Dict[str, Any]:
    from model_matching import extract_canonical_key

    pid = str(p["id"])
    shop = p.get("shop", "DNS Казахстан")
    city = p.get("city", "Астана")
    title = p["title"]
    category = p.get("category", "")
    url = p["url"]
    if url and "kaspi.kz/p/" in url and "kaspi.kz/shop/p/" not in url:
        url = url.replace("kaspi.kz/p/", "kaspi.kz/shop/p/")
    image_url = p.get("image_url", "")
    if image_url and "shop.kz//static.shop.kz" in image_url:
        image_url = image_url.replace("https://shop.kz//static.shop.kz", "https://static.shop.kz").replace("shop.kz//static.shop.kz", "static.shop.kz")
    description = p.get("description") or ""
    try:
        current_price = int(p.get("price", 0))
    except (TypeError, ValueError):
        current_price = 0
    if current_price > 10_000_000 or current_price <= 0:
        return {
            "is_new": False,
            "old_price": 0,
            "first_seen_price": 0,
            "current_price": 0,
            "price_changed": False
        }
    canonical_key = p.get("canonical_key") or extract_canonical_key(title)
    old_on_site = site_old_price(p.get("old_price_on_site"), current_price)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_price, first_seen_price, min_price, max_price, old_price_on_site FROM products WHERE id = ?", (pid,))
        existing = cursor.fetchone()
        _record_observation(cursor, pid, existing, current_price, old_on_site, now)

        if existing is None:
            cursor.execute("""
                INSERT INTO products (id, shop, city, title, category, url, image_url, description, current_price, old_price_on_site, first_seen_price, min_price, max_price, canonical_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pid, shop, city, title, category, url, image_url, description, current_price, old_on_site, current_price, current_price, current_price, canonical_key, now, now))
            conn.commit()
            return {
                "is_new": True,
                "old_price": current_price,
                "first_seen_price": current_price,
                "current_price": current_price,
                "price_changed": False
            }
        else:
            old_price = existing["current_price"]
            first_seen_price = existing["first_seen_price"]
            min_price = min(existing["min_price"], current_price)
            max_price = max(existing["max_price"], current_price)
            price_changed = (old_price != current_price)

            cursor.execute("""
                UPDATE products
                SET is_active = 1, shop = ?, city = ?, title = ?, category = ?, url = ?, image_url = ?,
                    description = CASE WHEN ? != '' THEN ? ELSE description END,
                    current_price = ?, old_price_on_site = ?, min_price = ?, max_price = ?, canonical_key = COALESCE(?, canonical_key), updated_at = ?
                WHERE id = ?
            """, (shop, city, title, category, url, image_url, description, description, current_price, old_on_site, min_price, max_price, canonical_key, now, pid))
            conn.commit()

            return {
                "is_new": False,
                "old_price": old_price,
                "first_seen_price": first_seen_price,
                "current_price": current_price,
                "price_changed": price_changed
            }

def get_price_history_batch(product_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Возвращает сохраненную историю цен для набора товаров (до пакетной перезаписи)."""
    ids = [str(pid) for pid in product_ids]
    history: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return history

    with get_connection() as conn:
        cursor = conn.cursor()
        # Порциями, чтобы не упереться в лимит параметров SQLite
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"SELECT id, current_price, first_seen_price FROM products WHERE id IN ({placeholders})",
                chunk
            )
            for row in cursor.fetchall():
                history[row["id"]] = {
                    "old_price": row["current_price"],
                    "first_seen_price": row["first_seen_price"]
                }
    return history

def save_or_update_products_batch(products: List[Dict[str, Any]]) -> int:
    """Массовая вставка/обновление товаров в единой транзакции (для YML выгрузок и больших каталогов)."""
    if not products:
        return 0

    from model_matching import extract_canonical_key

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    updated_count = 0

    with get_connection() as conn:
        cursor = conn.cursor()
        for p in products:
            pid = str(p["id"])
            shop = p.get("shop", "Белый Ветер")
            city = p.get("city", "Астана")
            title = p["title"]
            category = p.get("category", "")
            url = p["url"]
            if url and "kaspi.kz/p/" in url and "kaspi.kz/shop/p/" not in url:
                url = url.replace("kaspi.kz/p/", "kaspi.kz/shop/p/")
            image_url = p.get("image_url", "")
            if image_url and "shop.kz//static.shop.kz" in image_url:
                image_url = image_url.replace("https://shop.kz//static.shop.kz", "https://static.shop.kz").replace("shop.kz//static.shop.kz", "static.shop.kz")
            description = p.get("description") or ""
            try:
                current_price = int(p.get("price", 0))
            except (TypeError, ValueError):
                current_price = 0
            if current_price > 10_000_000 or current_price <= 0:
                continue
            canonical_key = p.get("canonical_key") or extract_canonical_key(title)
            old_on_site = site_old_price(p.get("old_price_on_site"), current_price)

            cursor.execute("SELECT current_price, min_price, max_price, old_price_on_site FROM products WHERE id = ?", (pid,))
            existing = cursor.fetchone()
            _record_observation(cursor, pid, existing, current_price, old_on_site, now)

            if existing is None:
                cursor.execute("""
                    INSERT INTO products (id, shop, city, title, category, url, image_url, description, current_price, old_price_on_site, first_seen_price, min_price, max_price, canonical_key, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (pid, shop, city, title, category, url, image_url, description, current_price, old_on_site, current_price, current_price, current_price, canonical_key, now, now))
            else:
                min_price = min(existing["min_price"], current_price)
                max_price = max(existing["max_price"], current_price)
                cursor.execute("""
                    UPDATE products
                    SET is_active = 1, shop = ?, city = ?, title = ?, category = ?, url = ?, image_url = ?,
                        description = CASE WHEN ? != '' THEN ? ELSE description END,
                        current_price = ?, old_price_on_site = ?, min_price = ?, max_price = ?, canonical_key = COALESCE(?, canonical_key), updated_at = ?
                    WHERE id = ?
                """, (shop, city, title, category, url, image_url, description, description, current_price, old_on_site, min_price, max_price, canonical_key, now, pid))
            updated_count += 1
        conn.commit()

    return updated_count

def was_alert_sent_recently(product_id: str, new_price: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM alerts
            WHERE product_id = ? AND new_price = ? AND datetime(created_at) >= datetime('now', '-1 day')
            LIMIT 1
        """, (str(product_id), new_price))
        return cursor.fetchone() is not None

def record_alert(product_id: str, alert_type: str, old_price: int, new_price: int, discount_pct: float, savings_kzt: int, shop: str = "DNS Казахстан", city: str = "Астана", competitor_shop: Optional[str] = None, deliveries=None, competitor_seen_at: Optional[str] = None):
    if old_price > 10_000_000 or new_price > 10_000_000 or savings_kzt > 10_000_000 or old_price <= 0 or new_price <= 0:
        return 0
    with get_connection() as conn:
        cursor = conn.cursor()
        # Проверка дубля и вставка — одна транзакция под блокировкой записи: два процесса
        # или повторная обработка не создадут два алерта на одно событие (M13)
        conn.execute("BEGIN IMMEDIATE")
        duplicate = cursor.execute("""
            SELECT 1 FROM alerts
            WHERE product_id = ? AND new_price = ? AND datetime(created_at) >= datetime('now', '-1 day')
            LIMIT 1
        """, (str(product_id), new_price)).fetchone()
        if duplicate:
            conn.rollback()
            return 0
        cursor.execute("""
            INSERT INTO alerts (shop, city, product_id, alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop, competitor_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (shop, city, str(product_id), alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop, competitor_seen_at))
        alert_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for user_id, payload in deliveries or []:
            conn.execute("""INSERT INTO notification_outbox(alert_id,user_id,payload,created_at)
                VALUES (?,?,?,?)""", (alert_id, user_id, json.dumps(payload, ensure_ascii=False), time.time()))
        conn.commit()
        invalidate_alerts_cache()
        return alert_id

def get_db_freshness(threshold_seconds: int = 10800) -> Dict[str, Any]:
    """Определяет свежесть базы данных на основе времени последнего обновления товаров.
    По умолчанию порог устаревания: 3 часа (10800 секунд).
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(updated_at) FROM products")
        row = cursor.fetchone()
        latest_str = row[0] if row else None

        cursor.execute("SELECT COUNT(*) FROM products WHERE " + active_product_clause())
        total_count = cursor.fetchone()[0]

    if not latest_str or total_count == 0:
        return {
            "latest_update": None,
            "age_seconds": None,
            "is_stale": True,
            "threshold_seconds": threshold_seconds,
            "total_products": total_count
        }

    try:
        if "T" in latest_str:
            latest_dt = datetime.datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
        else:
            latest_dt = datetime.datetime.strptime(latest_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)

        now = datetime.datetime.now(datetime.timezone.utc)
        age_seconds = max(0, int((now - latest_dt).total_seconds()))
    except Exception:
        age_seconds = 999999

    return {
        "latest_update": latest_str,
        "age_seconds": age_seconds,
        "is_stale": age_seconds >= threshold_seconds,
        "threshold_seconds": threshold_seconds,
        "total_products": total_count
    }

# Сколько последних кандидатов просматривается при фильтрации ленты по личным порогам
ALERTS_SCAN_WINDOW = 1500

# Лента фильтруется в Python, а панель опрашивает статистику каждые 3 секунды,
# поэтому результат ненадолго кэшируется: во время сканирования это снимает нагрузку с базы
_ALERTS_CACHE: Dict[Any, Any] = {}
_ALERTS_CACHE_TTL_SECONDS = 15

def _alerts_cache_key(user_settings: Dict[str, Any], city, alert_type, limit):
    fingerprint = json.dumps(user_settings, sort_keys=True, ensure_ascii=False)
    return (fingerprint, city, alert_type, limit)

def invalidate_alerts_cache() -> None:
    _ALERTS_CACHE.clear()
    _DEALS_TOTAL_CACHE.clear()


# Число предложений витрины скидок по городу (P04 U01): бейдж и плитка считают тем же get_store_deals,
# что и сама витрина (склейка по модели и магазину, арбитраж, свежесть цен), а не отдельным COUNT по products
_DEALS_TOTAL_CACHE: Dict[Optional[str], tuple] = {}


def store_deals_counts(city: Optional[str] = None) -> Dict[str, int]:
    """Счётчики витрины по городу: всего предложений и разбивка по типам после склейки (P04 U01, E01)."""
    key = city if city and city != "Все" else None
    cached = _DEALS_TOTAL_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _ALERTS_CACHE_TTL_SECONDS:
        return cached[1]
    counts = dict(get_store_deals(city=key, deal_type="all", limit=0)["type_totals"])
    _DEALS_TOTAL_CACHE[key] = (now, counts)
    return counts


def store_deals_total(city: Optional[str] = None) -> int:
    return store_deals_counts(city)["all"]

def _alert_type_clause(alert_type: Optional[str]):
    if not alert_type:
        return "", []
    at = alert_type.lower()
    if at in ("anomaly", "anomalies", "glitch", "zero_glitch"):
        return " AND a.alert_type = 'ZERO_GLITCH'", []
    if at in ("discount", "discounts"):
        return " AND a.alert_type IN ('SUPER_DISCOUNT', 'MARKET_ARBITRAGE')", []
    if at in ("super", "super_discount"):
        return " AND a.alert_type = 'SUPER_DISCOUNT'", []
    if at in ("arbitrage", "market_arbitrage"):
        return " AND a.alert_type = 'MARKET_ARBITRAGE'", []
    return " AND a.alert_type = ?", [alert_type]

def dismiss_alert(alert_id: int) -> bool:
    """Скрывает/удаляет алерт из ленты (пользователь нажал 'Скрыть' или алерт неактуален)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE alerts SET is_dismissed = 1 WHERE id = ?", (alert_id,))
        # Скрытый алерт больше не рассылается (M13)
        cursor.execute("UPDATE notification_outbox SET status='cancelled', last_error='alert dismissed' "
                       "WHERE alert_id = ? AND status = 'pending'", (alert_id,))
        conn.commit()
    invalidate_alerts_cache()
    return True

def get_product_by_id(product_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает полную детальную информацию о товаре по его ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (str(product_id),))
        row = cursor.fetchone()
        return dict(row) if row else None

def _fetch_filtered_alerts(user_settings: Dict[str, Any], city: Optional[str] = None, alert_type: Optional[str] = None, limit: Optional[int] = None) -> list:
    from detector import alert_matches_user

    key = _alerts_cache_key(user_settings, city, alert_type, limit)
    cached = _ALERTS_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _ALERTS_CACHE_TTL_SECONDS:
        return cached[1]

    query = """
        SELECT a.*, p.title, p.url, p.image_url, p.category, p.description
        FROM alerts a
        LEFT JOIN products p ON a.product_id = p.id
        WHERE (a.is_dismissed IS NULL OR a.is_dismissed = 0)
          AND a.old_price <= 10000000 AND a.new_price <= 10000000
          AND (julianday('now') - julianday(a.created_at)) <= 7.0
          AND """ + fresh_price_clause("p") + " AND " + fresh_benchmark_clause("a") + " AND p.current_price = a.new_price"
    params: List[Any] = []
    if city and city != "Все":
        query += " AND (a.city = ? OR a.city IS NULL)"
        params.append(city)
    clause, clause_params = _alert_type_clause(alert_type)
    query += clause
    params.extend(clause_params)
    query += " ORDER BY a.id DESC LIMIT ?"
    params.append(ALERTS_SCAN_WINDOW)

    result = []
    with get_connection() as conn:
        for row in conn.execute(query, params):
            item = dict(row)
            if alert_matches_user(item, user_settings):
                result.append(item)
                if limit is not None and len(result) >= limit:
                    break

    _ALERTS_CACHE[key] = (now, result)
    if len(_ALERTS_CACHE) > 200:
        _ALERTS_CACHE.clear()
    return result

def get_stats(user_settings: Optional[Dict[str, Any]] = None, city: Optional[str] = None) -> Dict[str, Any]:
    """Сводные счётчики. Акции, аномалии и арбитраж — в выбранном городе и тем же определением, что и списки
    (P04 U01–U03); total_products — вся база (плитка «Всего товаров в базе»)."""
    settings = user_settings if user_settings is not None else merge_user_settings({})
    city = city if city and city != "Все" else None
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE " + active_product_clause())
        total_products = cursor.fetchone()[0]

        cursor.execute("SELECT shop, COUNT(*) as count FROM products WHERE " + active_product_clause() + " GROUP BY shop")
        shops_stats = {row["shop"]: row["count"] for row in cursor.fetchall()}

    deals_counts = store_deals_counts(city)
    total_store_deals = deals_counts["all"]
    # «Из них арбитраж» — из того же набора витрины, что и общее число (P04 E01), а не из ленты алертов
    total_arbitrage = deals_counts["arbitrage"]

    # Счетчики аномалий и скидок — по личным порогам пользователя (у гостей — по умолчанию) и городу
    visible = _fetch_filtered_alerts(settings, city=city)
    total_anomalies = sum(1 for a in visible if a["alert_type"] == "ZERO_GLITCH")
    total_alert_discounts = len(visible) - total_anomalies

    return {
        "total_products": total_products,
        "total_alerts": len(visible),
        "total_anomalies": total_anomalies,
        "total_arbitrage": total_arbitrage,
        "total_discounts": total_store_deals,
        "total_store_deals": total_store_deals,
        "total_alert_discounts": total_alert_discounts,
        "shops": shops_stats,
        "city": city or "Все",
        "db_freshness": get_db_freshness(threshold_seconds=get_scan_interval_seconds())
    }

def get_alerts(limit: int = 150, city: Optional[str] = None, alert_type: Optional[str] = None, user_settings: Optional[Dict[str, Any]] = None) -> list:
    settings = user_settings if user_settings is not None else merge_user_settings({})
    return _fetch_filtered_alerts(settings, city=city, alert_type=alert_type, limit=limit)

def _catalog_filters(shop, city, search, use_fts: bool):
    where = [active_product_clause()]
    params: List[Any] = []
    if shop and shop != "Все":
        where.append("shop = ?")
        params.append(shop)
    if city and city != "Все":
        where.append("(city = ? OR city IS NULL)")
        params.append(city)
    if search:
        expr = _catalog_fts_expr(search) if use_fts else None
        if expr:
            # Полнотекстовый индекс по названию вместо полного перебора LIKE (R-M08)
            where.append("rowid IN (SELECT rowid FROM products_fts WHERE products_fts MATCH ?)")
            params.append(expr)
        else:
            where.append("title LIKE ?")
            params.append(f"%{search}%")
    return " AND ".join(where), params


def _catalog_fts_expr(search: str) -> Optional[str]:
    """Каждое слово запроса — префикс слова в названии: «смартфон» находит «Смартфоны»."""
    tokens = re.findall(r"\w+", search.lower())
    if not tokens:
        return None
    return " AND ".join('title : "' + t.replace('"', '') + '"*' for t in tokens)


def _catalog_use_fts(shop, city, search) -> bool:
    """FTS, если он что-то находит; иначе прежний LIKE (фрагмент внутри слова, например «phone» в «iphone»)."""
    if not search or not _catalog_fts_expr(search):
        return False
    where, params = _catalog_filters(shop, city, search, True)
    with get_connection() as conn:
        try:
            return conn.execute(f"SELECT 1 FROM products WHERE {where} LIMIT 1", params).fetchone() is not None
        except sqlite3.OperationalError:
            return False


def get_products_list(shop: Optional[str] = None, city: Optional[str] = None, search: Optional[str] = None, limit: int = 50, offset: int = 0) -> list:
    where, params = _catalog_filters(shop, city, search, _catalog_use_fts(shop, city, search))
    with get_connection() as conn:
        rows = conn.execute(f"SELECT * FROM products WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                            params + [limit, offset]).fetchall()
    from data_quality import annotate
    return [annotate(dict(row)) for row in rows]

def get_products_count(shop: Optional[str] = None, city: Optional[str] = None, search: Optional[str] = None) -> int:
    where, params = _catalog_filters(shop, city, search, _catalog_use_fts(shop, city, search))
    with get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM products WHERE {where}", params).fetchone()[0]

def find_market_comparisons(
    title: str,
    current_shop: str,
    current_price: int,
    city: Optional[str] = None,
    limit: int = 15
) -> Optional[Dict[str, Any]]:
    """Ищет аналогичные товары в других магазинах через канонический ключ или FTS5.
    Рассчитывает статистику цен конкурентов: минимальная цена, средняя цена,
    разница и процент экономии относительно рынка.

    Конкуренты — только с неустаревшей ценой (fresh_price_clause, ≤ 72 ч; P02 C02): устаревшая цена конкурента
    не может быть основанием арбитража. benchmark_seen_at — когда наблюдалась цена самого дешёвого конкурента.
    """
    if current_price <= 0 or not title:
        return None

    from model_matching import same_model, search_terms, extract_canonical_key
    from detector import is_junk_accessory, is_used_goods
    from config import CITIES_KZ
    known_cities = {c["name"] for c in CITIES_KZ.values()}
    if city not in known_cities:
        return None

    c_key = extract_canonical_key(title) or get_cached_canonical_key(title)
    rows = []

    # 1. Быстрый и точный поиск по каноническому ключу
    if c_key:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT shop, title, current_price, url, city, category, canonical_key, updated_at FROM products
                WHERE shop != ? AND (city = ? OR city = 'Казахстан') AND canonical_key = ? AND current_price > 0 AND """
                + fresh_price_clause() + """
                ORDER BY current_price ASC LIMIT 500
            """, (current_shop, city, c_key)).fetchall()

    # 2. Фолбэк на FTS5 полнотекстовый индекс
    if not rows:
        tokens = search_terms(title)
        if not tokens:
            return None
        fts_query = " AND ".join('"' + t + '"' for t in tokens)
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT shop, title, current_price, url, city, category, canonical_key, updated_at FROM products
                WHERE shop != ? AND (city = ? OR city = 'Казахстан') AND current_price > 0 AND """
                + fresh_price_clause() + """ AND rowid IN (
                    SELECT rowid FROM products_fts WHERE products_fts MATCH ?)
                ORDER BY current_price ASC LIMIT 500
            """, (current_shop, city, fts_query)).fetchall()

    valid_competitors = [dict(r) for r in rows
        if same_model(title, r["title"])
        and not is_junk_accessory(r["title"], r["category"] or "")
        and not is_used_goods(r["title"], r["category"] or "", r["url"])]
    valid_competitors = valid_competitors[:limit]

    if not valid_competitors:
        return None

    prices = [c["current_price"] for c in valid_competitors]
    min_comp_price = min(prices)
    avg_comp_price = int(sum(prices) / len(prices))
    cheapest_comp = min(valid_competitors, key=lambda x: x["current_price"])

    return {
        "competitor_count": len(valid_competitors),
        "min_price": min_comp_price,
        "avg_price": avg_comp_price,
        "cheapest_shop": cheapest_comp["shop"],
        "cheapest_title": cheapest_comp["title"],
        "benchmark_seen_at": cheapest_comp.get("updated_at"),
        "canonical_key": c_key,
        "competitors": valid_competitors
    }



# ===== Пользователи и сессии =====

SESSION_TTL_DAYS = 30

def _user_row_to_dict(row) -> Dict[str, Any]:
    user = dict(row)
    try:
        raw = json.loads(user.get("settings") or "{}")
    except (TypeError, ValueError):
        raw = {}
    user["settings"] = merge_user_settings(raw)
    user["is_blocked"] = bool(user["is_blocked"])
    return user

def upsert_telegram_user(tg: Dict[str, Any], initial_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Создает пользователя при первом входе или обновляет его профиль Telegram."""
    uid = int(tg["id"])
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone()
        if exists:
            conn.execute("""
                UPDATE users SET username = ?, first_name = ?, last_name = ?, photo_url = ?, last_login_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (tg.get("username"), tg.get("first_name"), tg.get("last_name"), tg.get("photo_url"), uid))
        else:
            conn.execute("""
                INSERT INTO users (id, username, first_name, last_name, photo_url, settings)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (uid, tg.get("username"), tg.get("first_name"), tg.get("last_name"), tg.get("photo_url"),
                  json.dumps(initial_settings or {}, ensure_ascii=False)))
        conn.commit()
    return get_user(uid)

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    return _user_row_to_dict(row) if row else None

def save_user_settings(user_id: int, clean_settings: Dict[str, Any]) -> Dict[str, Any]:
    user = get_user(user_id)
    merged = merge_user_settings({**user["settings"], **clean_settings})
    with get_connection() as conn:
        conn.execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(merged, ensure_ascii=False), int(user_id)))
        conn.commit()
    return merged

def replace_user_settings(user_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Полная замена личных настроек (сброс к умолчаниям), без слияния с прежними."""
    merged = merge_user_settings(settings)
    with get_connection() as conn:
        conn.execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(merged, ensure_ascii=False), int(user_id)))
        conn.commit()
    return merged

def list_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY last_login_at DESC").fetchall()
    return [_user_row_to_dict(r) for r in rows]

def set_user_blocked(user_id: int, blocked: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_blocked = ? WHERE id = ?", (1 if blocked else 0, int(user_id)))
        if blocked:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))
        conn.commit()

def get_notification_recipients() -> List[Dict[str, Any]]:
    """Незаблокированные пользователи с включенными Telegram-уведомлениями."""
    return [u for u in list_users() if not u["is_blocked"] and u["settings"].get("telegram_notify_enabled")]

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def cleanup_expired_sessions() -> int:
    """Удаляет просроченные сессии из базы данных. Возвращает количество удалённых строк."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.commit()
        return cur.rowcount

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=SESSION_TTL_DAYS)
    cleanup_expired_sessions()
    with get_connection() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (_hash_token(token), int(user_id), expires.isoformat()))
        conn.commit()
    return token

def get_session_user(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        row = conn.execute("""
            SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_blocked = 0
        """, (_hash_token(token), now)).fetchone()
    return _user_row_to_dict(row) if row else None

NOTIFICATION_RETENTION_DAYS = 30


def export_user_data(user_id: int) -> Optional[Dict[str, Any]]:
    """Всё, что сервис хранит о пользователе (M12): профиль, настройки, сессии без токенов,
    история адресованных ему уведомлений."""
    user = get_user(user_id)
    if not user:
        return None
    with get_connection() as conn:
        sessions = [dict(r) for r in conn.execute(
            "SELECT created_at, expires_at FROM sessions WHERE user_id = ? ORDER BY created_at", (int(user_id),))]
        notifications = []
        for row in conn.execute("""SELECT alert_id, status, attempts, created_at, last_error, payload
                                   FROM notification_outbox WHERE user_id = ? ORDER BY id""", (int(user_id),)):
            try:
                payload = json.loads(row["payload"])
            except ValueError:
                payload = {}
            product = payload.get("product") or {}
            notifications.append({
                "alert_id": row["alert_id"], "status": row["status"], "attempts": row["attempts"],
                "created_at": datetime.datetime.fromtimestamp(row["created_at"], datetime.timezone.utc).isoformat(),
                "product": {k: product.get(k) for k in ("title", "shop", "city", "url", "price")},
            })
    return {
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "profile": {k: user.get(k) for k in ("id", "username", "first_name", "last_name", "photo_url",
                                             "is_blocked", "created_at", "last_login_at")},
        "settings": user.get("settings") or {},
        "sessions": sessions,
        "notifications": notifications,
    }


def delete_user_account(user_id: int) -> Dict[str, int]:
    """Удаляет пользователя, его сессии и адресованные ему уведомления одной транзакцией (M12).
    Общие алерты и каталог не удаляются: они не принадлежат пользователю."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        counts = {
            "sessions": conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),)).rowcount,
            "notifications": conn.execute("DELETE FROM notification_outbox WHERE user_id = ?", (int(user_id),)).rowcount,
            "users": conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),)).rowcount,
        }
        conn.commit()
    return counts


def prune_notification_outbox(days: int = NOTIFICATION_RETENTION_DAYS) -> int:
    """Удаляет завершённые записи очереди уведомлений старше срока хранения (ожидающие не трогает)."""
    cutoff = time.time() - days * 86400
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM notification_outbox WHERE status != 'pending' AND created_at < ?",
                               (cutoff,)).rowcount
        conn.commit()
        return deleted


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()


# ===== Состояние сканирования по магазинам =====

def reset_stale_running_scans() -> int:
    """Сбрасывает статус 'running' у магазинов, прерванных падением или перезапуском процесса.
    Если аренда планировщика действует у другого процесса, его обход идёт — ничего не трогаем (R-M04)."""
    with get_connection() as conn:
        active = conn.execute("SELECT 1 FROM scheduler_lease WHERE name = 'scan' AND expires_at > ?",
                              (time.time(),)).fetchone()
        if active:
            return 0
        cur = conn.execute("""
            UPDATE shop_scans
            SET status = 'failed', last_error = 'Прервано перезапуском сервиса'
            WHERE status = 'running'
        """)
        conn.commit()
        return cur.rowcount

def record_shop_scan_start(shop_key: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO shop_scans (shop_key, last_attempt_at) VALUES (?, ?)
            ON CONFLICT(shop_key) DO UPDATE SET last_attempt_at = excluded.last_attempt_at
        """, (shop_key, now))
        conn.execute("UPDATE shop_scans SET status='running',next_retry_at=NULL WHERE shop_key=?", (shop_key,))
        conn.commit()

def record_shop_scan_result(shop_key, items, duration_sec, error=None, status=None):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status = status or ("partial" if error and items else "failed" if error else "complete")
    with get_connection() as conn:
        row = conn.execute("SELECT failure_count FROM shop_scans WHERE shop_key=?", (shop_key,)).fetchone()
        failures = (int(row[0]) if row else 0) + 1 if error else 0
        health_before, health_after = _update_shop_health(conn, shop_key, status)
        retry = time.time() + min(3600, 300 * 2 ** min(failures - 1, 4)) if error else None
        conn.execute("""INSERT INTO shop_scans
            (shop_key,last_attempt_at,last_success_at,last_items,last_duration_sec,last_error,status,failure_count,next_retry_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(shop_key) DO UPDATE SET
            last_attempt_at=excluded.last_attempt_at,
            last_success_at=COALESCE(excluded.last_success_at,shop_scans.last_success_at),
            last_items=excluded.last_items,last_duration_sec=excluded.last_duration_sec,
            last_error=excluded.last_error,status=excluded.status,
            failure_count=excluded.failure_count,next_retry_at=excluded.next_retry_at""",
            (shop_key, now, now if status == "complete" else None, items, duration_sec,
             error[:500] if error else None, status, failures, retry))
        conn.commit()
    _record_shop_transition(shop_key, health_before, health_after, items, error)


# Полнота итога обхода магазина: чем больше, тем лучше. running — не итог и сюда не попадает.
SHOP_HEALTH_RANK = {"failed": 0, "partial": 1, "limited": 2, "complete": 3}


def _update_shop_health(conn, shop_key, status):
    """Состояние здоровья источника для событий деградации/восстановления (P01, B01).

    Хранится отдельно от shop_scans.status (его перезаписывает running) и failure_count (его сбрасывает limited,
    он управляет повторами). degraded — была деградация, не закрытая полным (complete) обходом.
    Возвращает (до, после); до = None для источника без истории.
    """
    if status not in SHOP_HEALTH_RANK:
        return None, None
    name = f"shop_health:{shop_key}"
    row = conn.execute("SELECT value FROM schema_metadata WHERE name = ?", (name,)).fetchone()
    try:
        before = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        before = None
    if before and before.get("status") not in SHOP_HEALTH_RANK:
        before = None
    degraded = bool(before and before.get("degraded"))
    if before and SHOP_HEALTH_RANK[status] < SHOP_HEALTH_RANK[before["status"]]:
        degraded = True
    if status == "complete":
        degraded = False
    after = {"status": status, "degraded": degraded}
    conn.execute("INSERT INTO schema_metadata (name, value) VALUES (?, ?) "
                 "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, json.dumps(after)))
    return before, after


def _record_shop_transition(shop_key, before, after, items, error) -> None:
    """degradation — итог хуже предыдущего; recovery — complete после деградации; partial_recovery —
    улучшение без полного восстановления (например failed → limited). Без истории событий нет."""
    if not before or not after or before["status"] == after["status"]:
        return
    try:
        from telemetry import (telemetry, EVENT_DEGRADATION, EVENT_RECOVERY, EVENT_PARTIAL_RECOVERY,
                               SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO, COMPONENT_SCRAPER)
        old, new = before["status"], after["status"]
        data = {"shop_key": shop_key, "from": old, "to": new, "items": items}
        if SHOP_HEALTH_RANK[new] < SHOP_HEALTH_RANK[old]:
            telemetry.record_event(
                EVENT_DEGRADATION, SEVERITY_ERROR if new == "failed" else SEVERITY_WARNING, COMPONENT_SCRAPER,
                f"Магазин {shop_key}: {old} → {new}", data={**data, "error": str(error)[:300] if error else None})
        elif new == "complete" and before.get("degraded"):
            telemetry.record_event(EVENT_RECOVERY, SEVERITY_INFO, COMPONENT_SCRAPER,
                                   f"Магазин {shop_key}: восстановлен ({old} → complete)", data=data)
        elif before.get("degraded"):
            telemetry.record_event(EVENT_PARTIAL_RECOVERY, SEVERITY_INFO, COMPONENT_SCRAPER,
                                   f"Магазин {shop_key}: частичное улучшение ({old} → {new})", data=data)
    except Exception:
        pass

def get_shop_scans() -> Dict[str, Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM shop_scans").fetchall()
    return {row["shop_key"]: dict(row) for row in rows}

def _age_seconds(timestamp: Optional[str]) -> Optional[int]:
    if not timestamp:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return max(0, int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()))
    except Exception:
        return None

def get_stale_shops(shop_keys: List[str], max_age_seconds: int) -> List[str]:
    """Магазины, которые пора обойти: сначала те, что дольше всех не обновлялись."""
    scans = get_shop_scans()
    stale = []
    for key in shop_keys:
        row = scans.get(key) or {}
        if row.get("next_retry_at") and row["next_retry_at"] > time.time():
            continue
        timestamp = row.get("last_attempt_at") if row.get("status") == "limited" else row.get("last_success_at")
        age = _age_seconds(timestamp)
        if row.get("status") == "running" or row.get("last_error") or age is None or age >= max_age_seconds:
            stale.append((key, age if age is not None else 10**9))
    return [key for key, _ in sorted(stale, key=lambda x: -x[1])]

def get_shops_scan_report(shop_keys: List[str]) -> List[Dict[str, Any]]:
    """Сводка по магазинам для админ-панели."""
    from data_quality import freshness
    scans = get_shop_scans()
    quality = get_last_source_quality(list(shop_keys))
    report = []
    for key in shop_keys:
        row = scans.get(key) or {}
        report.append({
            "shop_key": key,
            "status": row.get("status", "unknown"),
            "last_attempt_at": row.get("last_attempt_at"),
            "next_retry_at": row.get("next_retry_at"),
            "last_success_at": row.get("last_success_at"),
            "age_seconds": _age_seconds(row.get("last_success_at")),
            "last_items": row.get("last_items") or 0,
            "last_duration_sec": round(row.get("last_duration_sec") or 0, 1),
            "last_error": row.get("last_error"),
            # P02: свежесть магазина — по последнему полному обходу; качество — худшая категория последнего обхода
            "freshness": freshness(row.get("last_success_at"))["freshness"],
            "last_quality": (quality.get(key) or {}).get("quality", "unknown"),
            "last_quality_reason": (quality.get(key) or {}).get("reason"),
        })
    return report


# Durable notification queue. A lease recovers interrupted deliveries after restart.
def claim_notification():
    now = time.time()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE notification_outbox SET status='expired'
            WHERE status='pending' AND (created_at < ? OR attempts >= 8)""", (now - 86400,))
        row = conn.execute("""SELECT * FROM notification_outbox
            WHERE status='pending' AND next_attempt_at <= ? ORDER BY id LIMIT 1""", (now,)).fetchone()
        if row:
            conn.execute("UPDATE notification_outbox SET attempts=attempts+1,next_attempt_at=? WHERE id=?",
                         (now + 120, row["id"]))
        conn.commit()
    return dict(row) if row else None


def finish_notification(delivery_id, status, attempts=0, error=None, retry_after=None):
    """retry_after — пауза, которую назвал Telegram (429); иначе экспоненциальная задержка."""
    # Пауза, названная Telegram, соблюдается полностью (до суток); своя задержка — до часа
    if retry_after is not None:
        delay = max(1.0, min(float(retry_after), 24 * 3600))
    else:
        delay = max(1.0, min(3600, 60 * 2 ** min(attempts, 6)))
    with get_connection() as conn:
        conn.execute("""UPDATE notification_outbox SET status=?,next_attempt_at=?,last_error=? WHERE id=?""",
            (status, time.time() + delay, error, delivery_id))
        conn.commit()


def notification_stats():
    with get_connection() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status,COUNT(*) FROM notification_outbox GROUP BY status")}


# ===== Отслеживаемые категории (сохраненные из поисковых запросов пользователей) =====

def save_tracked_category(name: str, query: str, master_category: Optional[str] = None) -> Dict[str, Any]:
    """Сохраняет категорию из поискового запроса или обновляет ее счетчик популярности."""
    clean_name = (name or "").strip()
    clean_query = (query or "").strip()
    if not clean_name or not clean_query:
        return {}

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, search_count, master_category FROM tracked_categories WHERE name = ?", (clean_name,))
        row = cursor.fetchone()
        if row:
            cat_id = row[0]
            new_count = (row[1] or 1) + 1
            m_cat = master_category or row[2]
            cursor.execute("""
                UPDATE tracked_categories
                SET search_count = ?, last_searched_at = CURRENT_TIMESTAMP, master_category = COALESCE(?, master_category), is_active = 1
                WHERE id = ?
            """, (new_count, m_cat, cat_id))
            conn.commit()
            return {"id": cat_id, "name": clean_name, "query": clean_query, "search_count": new_count, "master_category": m_cat}
        else:
            cursor.execute("""
                INSERT INTO tracked_categories (name, query, master_category, search_count, last_searched_at, is_active)
                VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, 1)
            """, (clean_name, clean_query, master_category))
            cat_id = cursor.lastrowid
            conn.commit()
            return {"id": cat_id, "name": clean_name, "query": clean_query, "search_count": 1, "master_category": master_category}

def get_tracked_categories(active_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    """Возвращает список отслеживаемых категорий с подсчетом товаров в базе."""
    with get_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT c.*, 
                   (SELECT count(*) FROM products p WHERE (p.category = c.name OR p.title LIKE '%' || c.query || '%') AND p.is_active = 1) AS products_count
            FROM tracked_categories c
        """
        params: List[Any] = []
        if active_only:
            query += " WHERE c.is_active = 1"
        query += " ORDER BY c.is_hot DESC, c.search_count DESC, c.last_searched_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def get_tracked_categories_counts() -> Dict[str, int]:
    """Возвращает количество активных Hot и ротируемых отслеживаемых категорий."""
    with get_connection() as conn:
        hot_cnt = conn.execute("SELECT COUNT(*) FROM tracked_categories WHERE is_active = 1 AND is_hot = 1").fetchone()[0]
        rolling_cnt = conn.execute("SELECT COUNT(*) FROM tracked_categories WHERE is_active = 1 AND (is_hot = 0 OR is_hot IS NULL)").fetchone()[0]
    return {"hot": hot_cnt, "rolling": rolling_cnt, "total": hot_cnt + rolling_cnt}


def get_due_tracked_categories(limit: int = 3, include_all_hot: bool = False) -> List[Dict[str, Any]]:
    """Категории для текущей волны: сначала «горячие» (is_hot), затем ротируемые.

    Если include_all_hot=True: все активные Hot-категории включаются в текущую волну гарантированно,
    плюс до `limit` ротируемых категорий строго по очереди (наиболее давно не сканировавшиеся первыми).
    Если include_all_hot=False: сохраняется квота на общий размер выдачи (для обратной совместимости).
    """
    if limit <= 0 and not include_all_hot:
        return []
    order = """ORDER BY CASE WHEN last_scanned_at IS NULL THEN 0 ELSE 1 END,
                        last_scanned_at ASC, search_count DESC, id ASC"""
    with get_connection() as conn:
        hot_query = f"SELECT * FROM tracked_categories WHERE is_active = 1 AND is_hot = 1 {order}"
        hot = [dict(r) for r in (conn.execute(hot_query) if include_all_hot else conn.execute(hot_query + " LIMIT ?", (limit,)))]
        rolling = [dict(r) for r in conn.execute(
            f"SELECT * FROM tracked_categories WHERE is_active = 1 AND (is_hot = 0 OR is_hot IS NULL) {order} LIMIT ?",
            (limit,))]

    if include_all_hot:
        # Все hot категории + порция ротируемых без вытеснения
        seen = set()
        res = []
        for c in hot + rolling[:limit]:
            if c["id"] not in seen:
                seen.add(c["id"])
                res.append(c)
        return res

    hot_quota = limit - 1 if limit > 1 and rolling else limit
    used_hot = min(len(hot), hot_quota)
    chosen = hot[:used_hot]
    chosen += rolling[:limit - len(chosen)]
    remaining = limit - len(chosen)
    chosen += hot[used_hot:used_hot + remaining]
    return chosen

def toggle_tracked_category_hot(category_id: int, is_hot: bool):
    """Устанавливает или снимает Hot-статус с отслеживаемой категории."""
    with get_connection() as conn:
        conn.execute("UPDATE tracked_categories SET is_hot = ? WHERE id = ?", (1 if is_hot else 0, category_id))
        conn.commit()

def mark_tracked_category_scanned(category_id: int):
    """Фиксирует факт сканирования категории в волне."""
    with get_connection() as conn:
        conn.execute("UPDATE tracked_categories SET last_scanned_at = CURRENT_TIMESTAMP WHERE id = ?", (category_id,))
        conn.commit()

def toggle_tracked_category(category_id: int, is_active: bool):
    """Включает или выключает категорию из ротации волн."""
    with get_connection() as conn:
        conn.execute("UPDATE tracked_categories SET is_active = ? WHERE id = ?", (1 if is_active else 0, category_id))
        conn.commit()

def delete_tracked_category(category_id: int):
    """Удаляет категорию из отслеживаемых."""
    with get_connection() as conn:
        conn.execute("DELETE FROM tracked_categories WHERE id = ?", (category_id,))
        conn.commit()

def set_tracked_category_parent(category_id: int, master_category: str):
    """Назначает родительскую группу для отслеживаемой категории."""
    with get_connection() as conn:
        conn.execute("UPDATE tracked_categories SET master_category = ? WHERE id = ?", (master_category, category_id))
        conn.commit()

def get_hierarchical_categories() -> Dict[str, Any]:
    """Возвращает дерево категорий: родительские группы (из config.MASTER_CATEGORIES)
    и входящие в них пользовательские подкатегории из поиска (tracked_categories).
    """
    from config import MASTER_CATEGORIES
    tracked = get_tracked_categories(active_only=False, limit=500)

    groups = {}
    for m_id, meta in MASTER_CATEGORIES.items():
        groups[m_id] = {
            "id": m_id,
            "name": meta["name"],
            "icon": meta["icon"],
            "description": meta.get("description", ""),
            "subcategories": [],
            "total_products": 0,
            "total_searches": 0,
            "hot_count": 0,
        }

    unassigned = []
    for item in tracked:
        m_id = item.get("master_category")
        prod_count = item.get("products_count") or 0
        search_cnt = item.get("search_count") or 0
        is_hot = bool(item.get("is_hot"))

        if m_id and m_id in groups:
            groups[m_id]["subcategories"].append(item)
            groups[m_id]["total_products"] += prod_count
            groups[m_id]["total_searches"] += search_cnt
            if is_hot:
                groups[m_id]["hot_count"] += 1
        else:
            unassigned.append(item)

    return {
        "groups": list(groups.values()),
        "unassigned": unassigned,
        "total_tracked": len(tracked),
    }


def get_store_deals(
    shop: Optional[str] = None,
    city: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    deal_type: Optional[str] = "all",
    sort_by: str = "discount_desc",
    limit: int = 60,
    offset: int = 0
) -> Dict[str, Any]:
    """Возвращает агрегированную витрину акций и супер-скидок по всем магазинам Казахстана.
    Объединяет скидки сайтов магазинов (products) и межмагазинный арбитраж (alerts).
    """
    raw_deals: List[Dict[str, Any]] = []
    with get_connection() as conn:
        # 1. Каталожные скидки из products
        p_query = f"""
            SELECT p.id, p.shop, p.title, p.category, p.city, p.url, p.image_url,
                   p.current_price AS new_price,
                   CASE 
                       WHEN p.old_price_on_site > p.current_price THEN p.old_price_on_site
                       ELSE p.first_seen_price
                   END AS old_price,
                   p.canonical_key
            FROM products p
            WHERE ((p.old_price_on_site > p.current_price AND p.current_price > 0)
               OR (p.category IN ('actions', 'Акции и распродажи') AND p.first_seen_price > p.current_price AND p.current_price > 0))
              AND {fresh_price_clause("p")}
        """
        p_params: List[Any] = []
        if city and city != "Все":
            p_query += " AND (p.city = ? OR p.city IS NULL)"
            p_params.append(city)

        for r in conn.execute(p_query, p_params):
            old_p = r["old_price"] or r["new_price"]
            new_p = r["new_price"]
            if old_p <= new_p or old_p <= 0:
                continue
            sav = old_p - new_p
            pct = round(sav * 100.0 / old_p)
            raw_deals.append({
                "id": f"p_{r['id']}",
                "product_id": r["id"],
                "shop": r["shop"],
                "title": r["title"],
                "category": r["category"] or "",
                "city": r["city"],
                "url": r["url"],
                "image_url": r["image_url"],
                "old_price": old_p,
                "new_price": new_p,
                "discount_pct": pct,
                "savings_kzt": sav,
                "alert_type": "SUPER_DISCOUNT" if pct >= 30 else "STORE_DISCOUNT",
                "competitor_shop": None,
                "canonical_key": r["canonical_key"]
            })

        # 2. Арбитраж цен и обнаруженные аномалии из alerts
        a_query = f"""
            SELECT a.id, a.shop, a.city, a.product_id, a.alert_type, a.old_price, a.new_price,
                   a.discount_pct, a.savings_kzt, a.competitor_shop,
                   p.title, p.category, p.url, p.image_url, p.canonical_key
            FROM alerts a
            JOIN products p ON a.product_id = p.id
            WHERE (a.is_dismissed IS NULL OR a.is_dismissed = 0)
              AND a.alert_type IN ('MARKET_ARBITRAGE', 'ARBITRAGE', 'SUPER_DISCOUNT')
              AND {fresh_price_clause("p")}
              AND {fresh_benchmark_clause("a")}
              -- цена алерта должна совпадать с текущей ценой товара, иначе он устарел (E02, как в _fetch_filtered_alerts)
              AND p.current_price = a.new_price
        """
        a_params: List[Any] = []
        if city and city != "Все":
            a_query += " AND (a.city = ? OR a.city IS NULL)"
            a_params.append(city)

        for r in conn.execute(a_query, a_params):
            raw_deals.append({
                "id": f"a_{r['id']}",
                "product_id": r["product_id"],
                "shop": r["shop"],
                "title": r["title"],
                "category": r["category"] or "",
                "city": r["city"],
                "url": r["url"],
                "image_url": r["image_url"],
                "old_price": r["old_price"],
                "new_price": r["new_price"],
                "discount_pct": r["discount_pct"],
                "savings_kzt": r["savings_kzt"],
                "alert_type": r["alert_type"],
                "competitor_shop": r["competitor_shop"],
                "canonical_key": r["canonical_key"]
            })

    # Дедупликация: если товар представлен и в каталоге, и в алертах, берём запись с большей выгодой
    dedup: Dict[tuple, Dict[str, Any]] = {}
    for d in raw_deals:
        k = (str(d.get("canonical_key") or d.get("product_id") or d["title"]).lower(), str(d["shop"]))
        if k not in dedup or (d["savings_kzt"] > dedup[k]["savings_kzt"]):
            dedup[k] = d

    all_deals = list(dedup.values())

    # Подсчёт магазинов ДО фильтрации по конкретному магазину (чтобы пользователь видел счётчики всех сетей)
    from collections import Counter
    shops_counter = Counter(d["shop"] for d in all_deals if d["shop"])
    shops_summary = [{"shop": "Все", "count": len(all_deals)}]
    for s_name, count in shops_counter.most_common():
        shops_summary.append({"shop": s_name, "count": count})


    # Применение фильтров
    filtered = all_deals
    if shop and shop != "Все":
        filtered = [d for d in filtered if d["shop"] == shop]

    if category and category != "Все":
        cat_lower = category.lower()
        filtered = [d for d in filtered if cat_lower in (d["category"] or "").lower()]

    if search:
        s_lower = search.lower()
        filtered = [d for d in filtered if s_lower in (d["title"] or "").lower()]

    def _is_super(d):
        return d["discount_pct"] >= 30 or d["alert_type"] == "SUPER_DISCOUNT"

    def _is_arbitrage(d):
        return d["alert_type"] in ("MARKET_ARBITRAGE", "ARBITRAGE")

    # Разбивка по типам — по тому же набору после склейки и тех же фильтрах списка (город, магазин, категория,
    # поиск), но до выбора вкладки и страницы (P04 E01): счётчик вкладки равен её же total при тех же фильтрах
    type_totals = {
        "all": len(filtered),
        "super": sum(1 for d in filtered if _is_super(d)),
        "arbitrage": sum(1 for d in filtered if _is_arbitrage(d)),
    }

    if deal_type == "super":
        filtered = [d for d in filtered if _is_super(d)]
    elif deal_type == "arbitrage":
        filtered = [d for d in filtered if _is_arbitrage(d)]

    # Сортировка
    if sort_by == "savings_desc":
        filtered.sort(key=lambda x: (x["savings_kzt"], x["discount_pct"]), reverse=True)
    elif sort_by == "price_asc":
        filtered.sort(key=lambda x: (x["new_price"], -x["discount_pct"]))
    else:  # discount_desc
        filtered.sort(key=lambda x: (x["discount_pct"], x["savings_kzt"]), reverse=True)

    total_count = len(filtered)
    page_items = filtered[offset : offset + limit]

    return {
        "deals": page_items,
        "total": total_count,
        "shops_summary": shops_summary,
        "type_totals": type_totals,
        "offset": offset,
        "limit": limit
    }



