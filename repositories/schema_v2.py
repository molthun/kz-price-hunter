"""
Database schema DDL for KZ Price Hunter 2.0 entities.
Additive schema: creates new tables and indexes without altering legacy tables.
"""

import sqlite3

DDL_V2 = """
-- 1. Sellers
CREATE TABLE IF NOT EXISTS sellers (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    legal_name TEXT,
    bin TEXT,
    domain TEXT,
    phone TEXT,
    rating REAL,
    is_active INTEGER DEFAULT 1,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sellers_slug ON sellers(slug);
CREATE INDEX IF NOT EXISTS idx_sellers_domain ON sellers(domain);

-- 2. Channels
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    seller_id TEXT NOT NULL REFERENCES sellers(id),
    channel_type TEXT NOT NULL,
    name TEXT NOT NULL,
    external_store_id TEXT,
    base_url TEXT,
    is_active INTEGER DEFAULT 1,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channels_seller ON channels(seller_id);
CREATE INDEX IF NOT EXISTS idx_channels_type ON channels(channel_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_seller_type_ext ON channels(
    seller_id, channel_type, COALESCE(external_store_id, '')
);

-- 3. Canonical Products
CREATE TABLE IF NOT EXISTS canonical_products (
    id TEXT PRIMARY KEY,
    canonical_key TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT,
    model TEXT,
    description TEXT,
    image_url TEXT,
    attributes_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canonical_products_key ON canonical_products(canonical_key);
CREATE INDEX IF NOT EXISTS idx_canonical_products_cat ON canonical_products(category);
CREATE INDEX IF NOT EXISTS idx_canonical_products_brand ON canonical_products(brand);

-- 4. Offers
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES canonical_products(id),
    seller_id TEXT NOT NULL REFERENCES sellers(id),
    channel_id TEXT NOT NULL REFERENCES channels(id),
    external_sku TEXT NOT NULL,
    url TEXT NOT NULL,
    image_url TEXT,
    price REAL NOT NULL,
    old_price REAL,
    currency TEXT DEFAULT 'KZT',
    condition TEXT DEFAULT 'NEW',
    availability TEXT DEFAULT 'in_stock',
    city TEXT NOT NULL,
    payment_methods_json TEXT DEFAULT '[]',
    installment_available INTEGER DEFAULT 0,
    installment_months INTEGER,
    credit_available INTEGER DEFAULT 0,
    delivery_type TEXT,
    pickup_available INTEGER DEFAULT 0,
    warranty TEXT,
    published_at TEXT,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    is_active INTEGER DEFAULT 1,
    raw_payload_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id);
CREATE INDEX IF NOT EXISTS idx_offers_seller ON offers(seller_id);
CREATE INDEX IF NOT EXISTS idx_offers_channel ON offers(channel_id);
CREATE INDEX IF NOT EXISTS idx_offers_city ON offers(city);
CREATE INDEX IF NOT EXISTS idx_offers_price ON offers(price);
CREATE INDEX IF NOT EXISTS idx_offers_active ON offers(is_active);
CREATE INDEX IF NOT EXISTS idx_offers_condition ON offers(condition);
CREATE INDEX IF NOT EXISTS idx_offers_ext_sku ON offers(external_sku);
CREATE UNIQUE INDEX IF NOT EXISTS uq_offers_channel_sku ON offers(channel_id, external_sku);

-- 5. Offer Price History
CREATE TABLE IF NOT EXISTS offer_price_history (
    offer_id TEXT NOT NULL REFERENCES offers(id),
    price REAL NOT NULL,
    old_price REAL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (offer_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_oph_offer ON offer_price_history(offer_id);
CREATE INDEX IF NOT EXISTS idx_oph_observed ON offer_price_history(observed_at);

-- 6. Seller Identities (Seller Graph footprint)
CREATE TABLE IF NOT EXISTS seller_identities (
    id TEXT PRIMARY KEY,
    seller_id TEXT NOT NULL REFERENCES sellers(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    source TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(identity_type, identity_value, seller_id)
);
CREATE INDEX IF NOT EXISTS idx_seller_identities_lookup ON seller_identities(identity_type, identity_value);
CREATE INDEX IF NOT EXISTS idx_seller_identities_seller ON seller_identities(seller_id);

-- 7. Seller Review Queue (for ambiguous REVIEW matches)
CREATE TABLE IF NOT EXISTS seller_review_queue (
    id TEXT PRIMARY KEY,
    candidate_seller_id TEXT,
    matched_seller_id TEXT,
    reason TEXT NOT NULL,
    confidence REAL DEFAULT 0.0,
    details_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- 8. Seller Candidates (Discovery Pipeline)
CREATE TABLE IF NOT EXISTS seller_candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    matched_seller_id TEXT REFERENCES sellers(id) ON DELETE SET NULL,
    phone TEXT,
    domain TEXT,
    bin TEXT,
    legal_name TEXT,
    marketplace_seller_id TEXT,
    city TEXT,
    raw_metadata TEXT DEFAULT '{}',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON seller_candidates(status);
CREATE INDEX IF NOT EXISTS idx_candidates_source ON seller_candidates(source_type);

-- 9. Source Profiles (Source Profiler capabilities & tiers)
CREATE TABLE IF NOT EXISTS source_profiles (
    id TEXT PRIMARY KEY,
    seller_id TEXT REFERENCES sellers(id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES seller_candidates(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    domain TEXT,
    catalog_format TEXT NOT NULL DEFAULT 'unknown',
    extraction_tier INTEGER NOT NULL DEFAULT 4,
    has_prices INTEGER DEFAULT 1,
    has_availability INTEGER DEFAULT 1,
    capabilities_json TEXT DEFAULT '{}',
    confidence REAL DEFAULT 0.8,
    notes TEXT DEFAULT '',
    profiled_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_profiles_cand ON source_profiles(candidate_id);
CREATE INDEX IF NOT EXISTS idx_source_profiles_seller ON source_profiles(seller_id);

-- 10. Unified Product Index (FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS canonical_products_fts USING fts5(
    canonical_key UNINDEXED,
    title,
    brand,
    model,
    category,
    content='canonical_products',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS canonical_products_ai AFTER INSERT ON canonical_products BEGIN
    INSERT INTO canonical_products_fts(rowid, canonical_key, title, brand, model, category)
    VALUES (new.rowid, new.canonical_key, new.title, COALESCE(new.brand, ''), COALESCE(new.model, ''), new.category);
END;

CREATE TRIGGER IF NOT EXISTS canonical_products_ad AFTER DELETE ON canonical_products BEGIN
    INSERT INTO canonical_products_fts(canonical_products_fts, rowid, canonical_key, title, brand, model, category)
    VALUES('delete', old.rowid, old.canonical_key, old.title, COALESCE(old.brand, ''), COALESCE(old.model, ''), old.category);
END;

CREATE TRIGGER IF NOT EXISTS canonical_products_au AFTER UPDATE OF title, brand, model, category ON canonical_products
WHEN old.title IS NOT new.title OR old.brand IS NOT new.brand OR old.model IS NOT new.model OR old.category IS NOT new.category
BEGIN
    INSERT INTO canonical_products_fts(canonical_products_fts, rowid, canonical_key, title, brand, model, category)
    VALUES('delete', old.rowid, old.canonical_key, old.title, COALESCE(old.brand, ''), COALESCE(old.model, ''), old.category);
    INSERT INTO canonical_products_fts(rowid, canonical_key, title, brand, model, category)
    VALUES (new.rowid, new.canonical_key, new.title, COALESCE(new.brand, ''), COALESCE(new.model, ''), new.category);
END;

-- 11. Search Events & Quality Analytics (S2-E13)
-- Счётчик повторов запросов (правило приватности P06): текст запроса хранится только
-- начиная с третьего раза. Таблица та же, что у legacy-контура, и создаётся так же —
-- механизм один на оба пути, дублировать его нельзя.
CREATE TABLE IF NOT EXISTS search_query_seen (
    query_key TEXT PRIMARY KEY,
    seen INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS search_events (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    city TEXT,
    condition TEXT,
    channel_type TEXT,
    outcome TEXT NOT NULL,
    total_products INTEGER NOT NULL DEFAULT 0,
    total_offers INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_events_created ON search_events(created_at);
CREATE INDEX IF NOT EXISTS idx_search_events_norm_query ON search_events(normalized_query);
CREATE INDEX IF NOT EXISTS idx_search_events_city ON search_events(city);
CREATE INDEX IF NOT EXISTS idx_search_events_outcome ON search_events(outcome);

-- 12. Adaptive Discovery Tasks (S2-E14)
CREATE TABLE IF NOT EXISTS discovery_tasks (
    id TEXT PRIMARY KEY,
    signal_query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    city TEXT,
    search_count INTEGER NOT NULL DEFAULT 0,
    success_rate REAL NOT NULL DEFAULT 0.0,
    priority_score REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    search_queries TEXT NOT NULL DEFAULT '[]',
    discovered_candidates_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_tasks_status ON discovery_tasks(status);
CREATE INDEX IF NOT EXISTS idx_discovery_tasks_priority ON discovery_tasks(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_tasks_norm_query ON discovery_tasks(normalized_query, city);

-- Название, по которому social-предложение получило идентичность: решение оператора о названии
-- переносит и его (FA69R-01). Только название, не текст поста.
CREATE TABLE IF NOT EXISTS social_offer_titles (
    offer_id TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    title_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    pack TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (offer_id, title_hash)
);
CREATE INDEX IF NOT EXISTS idx_social_offer_titles_hash ON social_offer_titles(title_hash);

-- 13. Social Content Observations (S2-E08)
CREATE TABLE IF NOT EXISTS content_observations (
    id TEXT PRIMARY KEY,
    seller_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'POST',
    post_id TEXT,
    text TEXT NOT NULL DEFAULT '',
    media_urls TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    raw_metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_content_obs_seller ON content_observations(seller_id);
CREATE INDEX IF NOT EXISTS idx_content_obs_channel ON content_observations(channel_id);
CREATE INDEX IF NOT EXISTS idx_content_obs_type ON content_observations(content_type);
CREATE INDEX IF NOT EXISTS idx_content_obs_expires ON content_observations(expires_at);

-- 14. Social Sources registry (S2-E06): каналы Telegram и профили Instagram как источники контента
CREATE TABLE IF NOT EXISTS social_sources (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    url TEXT DEFAULT '',
    seller_id TEXT DEFAULT '',
    channel_id TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    last_fetch_at TEXT,
    last_status TEXT DEFAULT 'PENDING',
    last_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, handle)
);
CREATE INDEX IF NOT EXISTS idx_social_sources_enabled ON social_sources(enabled);
CREATE INDEX IF NOT EXISTS idx_social_sources_platform ON social_sources(platform);

-- 15. Scheduler plans (S2-E15/E17): история shadow-планов адаптивного планировщика 2.0
CREATE TABLE IF NOT EXISTS scheduler_plans (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    extra_categories TEXT NOT NULL DEFAULT '[]',
    demand_queries TEXT NOT NULL DEFAULT '[]',
    discovery_tasks TEXT NOT NULL DEFAULT '[]',
    reasons TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_scheduler_plans_generated ON scheduler_plans(generated_at DESC);
"""


def rebuild_canonical_products_fts(conn: sqlite3.Connection) -> None:
    """Rebuilds canonical_products_fts index from current canonical_products rows."""
    conn.execute("INSERT INTO canonical_products_fts(canonical_products_fts) VALUES('rebuild')")




def _ensure_column(conn: sqlite3.Connection, table: str, col: str, col_type: str) -> None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if cols and col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


def init_schema_v2(conn: sqlite3.Connection) -> None:
    """
    Apply Schema 2.0 DDL to SQLite connection.
    Idempotent and safe to run multiple times.
    """
    trig_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='canonical_products_au'"
    ).fetchone()
    if trig_row and "WHEN" not in (trig_row[0] or "").upper():
        conn.execute("DROP TRIGGER IF EXISTS canonical_products_au")
    conn.executescript(DDL_V2)
    # Additive columns for existing v2 tables
    # Признак «источник действительно открывали» обязан переживать запись: без него профиль,
    # полученный догадкой по адресу, после чтения из базы неотличим от подтверждённого (C-E05-03)
    _ensure_column(conn, "source_profiles", "observed", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "installment_available", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "credit_available", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "pickup_available", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "expires_at", "TEXT")
    _ensure_column(conn, "seller_candidates", "discovery_task_id", "TEXT")
    # Предложение, задающее название и категорию канонического товара (6.5.1): без него каждое
    # предложение другого магазина переписывало название общего товара и его FTS при каждом обходе
    _ensure_column(conn, "canonical_products", "title_offer_id", "TEXT")
    # Измерения точки истории на момент наблюдения (V02 аудита 6.5.0): без них линия цены
    # строилась по ТЕКУЩЕМУ предложению, и смена NEW→USED переписывала всё его прошлое.
    # У прежних строк колонки пусты — для них остаётся только текущее предложение.
    for column in ("product_id", "seller_id", "channel_id", "condition", "city"):
        _ensure_column(conn, "offer_price_history", column, "TEXT")

    # C-E11-02: Ensure existing canonical_products rows are indexed in FTS on initial creation
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM canonical_products")
        prod_count = cur.fetchone()[0]
        if prod_count > 0:
            cur.execute("SELECT COUNT(*) FROM canonical_products_fts_docsize")
            fts_count = cur.fetchone()[0]
            if fts_count < prod_count:
                rebuild_canonical_products_fts(conn)
    except Exception:
        pass
