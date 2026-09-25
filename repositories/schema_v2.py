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
"""



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
    conn.executescript(DDL_V2)
    # Additive columns for existing v2 tables
    # Признак «источник действительно открывали» обязан переживать запись: без него профиль,
    # полученный догадкой по адресу, после чтения из базы неотличим от подтверждённого (C-E05-03)
    _ensure_column(conn, "source_profiles", "observed", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "installment_available", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "credit_available", "INTEGER DEFAULT 0")
    _ensure_column(conn, "offers", "pickup_available", "INTEGER DEFAULT 0")
