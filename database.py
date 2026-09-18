import re
import json
import time
import sqlite3
import hashlib
import secrets
import datetime
from typing import Optional, Dict, Any, List
from config import DB_PATH, get_scan_interval_seconds, merge_user_settings

def active_product_clause(alias=""):
    prefix = f"{alias}." if alias else ""
    seconds = max(86400, 2 * get_scan_interval_seconds())
    return (f"{prefix}is_active = 1 AND julianday({prefix}updated_at) "
            f">= julianday('now') - {seconds} / 86400.0")


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


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_connection():
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
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"prices-{label}-{stamp}.db"
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
    return str(dest)


def run_migrations() -> List[int]:
    """Применяет ожидающие миграции по порядку; ошибка останавливает старт, транзакция откатывается."""
    with get_connection() as conn:
        current = get_schema_version(conn)
        has_data = any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                       for t in ("products", "alerts", "users"))
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return []
    if has_data:
        path = backup_database(f"pre-v{pending[-1][0]}")
        print(f"[DB] Бэкап перед миграциями {current}→{pending[-1][0]}: {path}")

    applied = []
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
    with get_connection() as conn:
        _create_schema(conn.cursor())
        conn.commit()
    run_migrations()
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
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM schema_metadata WHERE name = ?", (name,)).fetchone()
        return row[0] if row else default


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

def record_alert(product_id: str, alert_type: str, old_price: int, new_price: int, discount_pct: float, savings_kzt: int, shop: str = "DNS Казахстан", city: str = "Астана", competitor_shop: Optional[str] = None, deliveries=None):
    if old_price > 10_000_000 or new_price > 10_000_000 or savings_kzt > 10_000_000 or old_price <= 0 or new_price <= 0:
        return 0
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO alerts (shop, city, product_id, alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (shop, city, str(product_id), alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop))
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
        try:
            cursor.execute("UPDATE alerts SET is_dismissed = 1 WHERE id = ?", (alert_id,))
        except Exception:
            cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
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
          AND """ + active_product_clause("p") + " AND p.current_price = a.new_price"
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

def get_stats(user_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    settings = user_settings if user_settings is not None else merge_user_settings({})
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE " + active_product_clause())
        total_products = cursor.fetchone()[0]

        cursor.execute("SELECT shop, COUNT(*) as count FROM products WHERE " + active_product_clause() + " GROUP BY shop")
        shops_stats = {row["shop"]: row["count"] for row in cursor.fetchall()}

    # Счетчики аномалий и скидок — по личным порогам пользователя (у гостей — по умолчанию)
    visible = _fetch_filtered_alerts(settings)
    total_anomalies = sum(1 for a in visible if a["alert_type"] == "ZERO_GLITCH")
    total_discounts = len(visible) - total_anomalies

    return {
        "total_products": total_products,
        "total_alerts": len(visible),
        "total_anomalies": total_anomalies,
        "total_discounts": total_discounts,
        "shops": shops_stats,
        "db_freshness": get_db_freshness(threshold_seconds=get_scan_interval_seconds())
    }

def get_alerts(limit: int = 150, city: Optional[str] = None, alert_type: Optional[str] = None, user_settings: Optional[Dict[str, Any]] = None) -> list:
    settings = user_settings if user_settings is not None else merge_user_settings({})
    return _fetch_filtered_alerts(settings, city=city, alert_type=alert_type, limit=limit)

def get_products_list(shop: Optional[str] = None, city: Optional[str] = None, search: Optional[str] = None, limit: int = 50, offset: int = 0) -> list:
    query = "SELECT * FROM products WHERE " + active_product_clause()
    params = []
    if shop and shop != "Все":
        query += " AND shop = ?"
        params.append(shop)
    if city and city != "Все":
        query += " AND (city = ? OR city IS NULL)"
        params.append(city)
    if search:
        query += " AND title LIKE ?"
        params.append(f"%{search}%")
    query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def get_products_count(shop: Optional[str] = None, city: Optional[str] = None, search: Optional[str] = None) -> int:
    query = "SELECT COUNT(*) FROM products WHERE " + active_product_clause()
    params = []
    if shop and shop != "Все":
        query += " AND shop = ?"
        params.append(shop)
    if city and city != "Все":
        query += " AND (city = ? OR city IS NULL)"
        params.append(city)
    if search:
        query += " AND title LIKE ?"
        params.append(f"%{search}%")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()[0]

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
                SELECT shop, title, current_price, url, city, category, canonical_key FROM products
                WHERE shop != ? AND (city = ? OR city = 'Казахстан') AND canonical_key = ? AND current_price > 0 AND """
                + active_product_clause() + """
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
                SELECT shop, title, current_price, url, city, category, canonical_key FROM products
                WHERE shop != ? AND (city = ? OR city = 'Казахстан') AND current_price > 0 AND """
                + active_product_clause() + """ AND rowid IN (
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

def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()


# ===== Состояние сканирования по магазинам =====

def reset_stale_running_scans() -> int:
    """Сбрасывает статус 'running' у магазинов, прерванных падением или перезапуском процесса."""
    with get_connection() as conn:
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
    scans = get_shop_scans()
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


def finish_notification(delivery_id, status, attempts=0, error=None):
    with get_connection() as conn:
        conn.execute("""UPDATE notification_outbox SET status=?,next_attempt_at=?,last_error=? WHERE id=?""",
            (status, time.time() + min(3600, 60 * 2 ** min(attempts, 6)), error, delivery_id))
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

