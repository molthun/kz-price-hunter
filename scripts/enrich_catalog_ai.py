#!/usr/bin/env python3
"""
Catalog AI Enrichment CLI & Background Worker (KZPH-G5-06).

Enriches canonical products in SQLite prices.db with search attributes (RAM, storage,
screen size, color, CPU, load capacity, volume, etc.) using deterministic rules and Local AI (Ollama).
"""
import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.catalog_enrichment_ai import (
    batch_enrich_catalog,
    can_use_local_ai,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DDL_ATTRIBUTES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("enrich_catalog")


def main():
    parser = argparse.ArgumentParser(description="KZ Price Hunter Catalog AI Enrichment")
    parser.add_argument("--db-path", default=os.environ.get("DATABASE_PATH", "prices.db"), help="Path to SQLite database")
    parser.add_argument("--limit", type=int, default=100, help="Max products to enrich in this run")
    parser.add_argument("--category", default=None, help="Filter by specific category name or ID")
    parser.add_argument("--no-ai", action="store_true", help="Disable Local AI, use deterministic rules only")
    parser.add_argument("--force", action="store_true", help="Re-enrich products that already have attributes")
    parser.add_argument("--pause", type=float, default=0.3, help="Seconds to pause between AI calls")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Ollama endpoint URL")
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL, help="Ollama model name")

    args = parser.parse_args()

    db_file = Path(args.db_path)
    if not db_file.exists():
        # Try finding in data/
        alt_db = Path("data") / args.db_path
        if alt_db.exists():
            db_file = alt_db
        else:
            logger.error(f"Database not found: {args.db_path}")
            sys.exit(1)

    logger.info(f"Opening database: {db_file}")
    conn = sqlite3.connect(str(db_file), timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = WAL")

    # Ensure product_search_attributes table exists
    conn.executescript(DDL_ATTRIBUTES)
    conn.commit()

    use_ai = not args.no_ai
    if use_ai:
        allowed, reason = can_use_local_ai()
        if not allowed:
            logger.warning(f"Local AI not available ({reason}). Running rules-only.")
            use_ai = False
        else:
            logger.info(f"Local AI ready ({args.ollama_model} @ {args.ollama_url}).")

    logger.info(f"Starting enrichment (limit={args.limit}, category={args.category}, use_ai={use_ai}, force={args.force})...")
    t0 = time.time()

    stats = batch_enrich_catalog(
        conn,
        limit=args.limit,
        category=args.category,
        use_ai=use_ai,
        pause_between_ai_calls=args.pause,
        force=args.force,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
    )

    elapsed = time.time() - t0
    logger.info(
        f"Enrichment completed in {elapsed:.1f}s: "
        f"candidates={stats['total_candidates']}, processed={stats['processed']}, "
        f"enriched={stats['enriched']} (rules={stats['by_rule']}, AI={stats['by_ai']}), "
        f"empty={stats['empty']}"
    )

    conn.close()


if __name__ == "__main__":
    main()
