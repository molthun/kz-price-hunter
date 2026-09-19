"""Замеры масштаба на КОПИИ базы (R-M08): стоимость проверки арбитража, поиска и проекция на обход.

    DATA_DIR=<папка с копией prices.db> python scripts/measure_scale.py [--sample 500] [--scale 10]

--scale N создаёт в той же папке синтетически увеличенную копию (товары размножаются с новыми
id и городами) и мерит на ней. Рабочую базу не открывать: только копию.
"""
import argparse
import asyncio
import os
import random
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0.0


def inflate(db_path: str, factor: int) -> None:
    """Размножает products (с FTS через триггеры) до factor раз: те же названия, новые id/города."""
    cities = ["Астана", "Алматы", "Шымкент", "Караганда", "Актобе", "Павлодар", "Казахстан"]
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""SELECT id, shop, city, title, category, url, image_url, current_price, first_seen_price,
                           min_price, max_price, canonical_key FROM products""").fetchall()
    for copy in range(1, factor):
        conn.executemany("""INSERT OR IGNORE INTO products (id, shop, city, title, category, url, image_url, current_price,
                            first_seen_price, min_price, max_price, canonical_key, is_active, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))""",
                         [(f"{r[0]}~x{copy}", r[1], cities[(copy + i) % len(cities)], r[3], r[4], r[5], r[6],
                           int(r[7] * random.uniform(0.9, 1.1)), r[8], r[9], r[10], r[11]) for i, r in enumerate(rows)])
        conn.commit()
    conn.close()


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, *args):
        self.count += 1


def measure(sample: int) -> None:
    import database
    import search_engine
    from detector import check_market_arbitrage, get_candidate_settings
    with database.get_connection() as conn:
        total = conn.execute("SELECT count(*) FROM products").fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            "SELECT id, shop, city, title, category, url, current_price AS price FROM products ORDER BY random() LIMIT ?", (sample,))]
    print(f"products: {total}, sample: {len(rows)}")
    settings = get_candidate_settings()

    counter = QueryCounter()
    original = database.get_connection

    def counted():
        conn = original()
        conn.set_trace_callback(counter)
        return conn

    database.get_connection = counted
    times = []
    try:
        for p in rows:
            t = time.perf_counter()
            check_market_arbitrage(p, custom_settings=settings)
            times.append((time.perf_counter() - t) * 1000)
    finally:
        database.get_connection = original
    print(f"arbitrage per product: p50={percentile(times, .5):.2f} ms p95={percentile(times, .95):.2f} ms "
          f"max={max(times):.1f} ms, SQL statements/product={counter.count / max(1, len(rows)):.1f}")
    per_item = statistics.mean(times) / 1000
    for n in (15_000, 50_000, 200_000):
        print(f"  проекция: обход {n} товаров → арбитраж ≈ {per_item * n / 60:.1f} мин в одном потоке")

    queries = ["iphone 15", "ноутбук", "samsung galaxy s24", "телевизор 55", "пылесос", "шуруповерт"]
    search_times = []
    for q in queries * 3:
        t = time.perf_counter()
        asyncio.run(search_engine.get_best_price_summary(q, city="Астана"))
        search_times.append((time.perf_counter() - t) * 1000)
    print(f"best-price search: p50={percentile(search_times, .5):.1f} ms p95={percentile(search_times, .95):.1f} ms")
    t = time.perf_counter()
    database.get_products_list(search="смартфон", limit=50)
    database.get_products_count(search="смартфон")
    print(f"catalog list+count (search): {(time.perf_counter() - t) * 1000:.1f} ms")
    try:
        import resource
        print(f"peak RSS: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == 'darwin' else 1024):.0f} MB")
    except ImportError:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--scale", type=int, default=1)
    args = parser.parse_args()
    from config import DB_PATH
    if args.scale > 1:
        inflate(str(DB_PATH), args.scale)
    measure(args.sample)
