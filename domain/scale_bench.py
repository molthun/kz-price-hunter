"""
Scale Benchmarking & Storage Decision Engine (S2-E30, KZ Price Hunter 2.0).

Automates performance evaluation against checkpoints (100k, 500k, 1m, 5m, 10m):
  - search p95 latency
  - database and FTS index size
  - write latency (1k batch)
  - backup duration (SQLite Online Backup API)
  - memory and cold startup
  - objective storage decision (STAY_ON_SQLITE vs EVALUATE_POSTGRESQL)
"""

from __future__ import annotations

import datetime
import logging
import math
import os
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
    utc_now_iso,
)
from domain.search_core import SearchCore
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)

logger = logging.getLogger("kz_price_hunter.scale_bench")


@dataclass
class ScaleCheckpoint:
    name: str
    target_offers: int
    actual_offers: int
    total_products: int
    total_sellers: int
    total_channels: int
    search_p95_ms: float
    search_avg_ms: float
    write_latency_1k_ms: float
    db_size_mb: float
    fts_size_mb: float
    price_history_size_mb: float
    backup_duration_s: float
    memory_mb: Optional[float] = None
    startup_ms: float = 0.0
    evaluated_at: str = field(default_factory=utc_now_iso)
    history_index_size_mb: float = 0.0
    size_measurement_method: str = "dbstat"  # "dbstat" | "estimated"
    peak_rss_mb: Optional[float] = None
    cold_startup_ms: float = 0.0
    concurrency_verified: bool = False
    concurrency_write_latency_ms: Optional[float] = None
    concurrency_errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StorageDecision:
    decision: str  # STAY_ON_SQLITE | EVALUATE_POSTGRESQL | NOT_MEASURED
    is_sqlite_sufficient: bool
    summary: str
    reasons: List[str]
    metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_storage_decision(checkpoint: ScaleCheckpoint) -> StorageDecision:
    """
    Evaluates whether SQLite remains sufficient or PostgreSQL migration should begin.
    Based on objective threshold metrics defined in Stage 30 (SP30-04, SP30-06).
    """
    reasons: List[str] = []
    evaluate_pg = False

    # SP30-04: Полная валидация полноты и корректности ключевых метрик
    invalid_reasons: List[str] = []

    # search_p95_ms must be finite and > 0.0
    if not (isinstance(checkpoint.search_p95_ms, (int, float)) and math.isfinite(checkpoint.search_p95_ms) and checkpoint.search_p95_ms > 0.0):
        invalid_reasons.append("Замеры задержки поиска отсутствуют или содержат некорректные значения (NaN / non-positive).")

    # write_latency_1k_ms must be finite and > 0.0
    if not (isinstance(checkpoint.write_latency_1k_ms, (int, float)) and math.isfinite(checkpoint.write_latency_1k_ms) and checkpoint.write_latency_1k_ms > 0.0):
        invalid_reasons.append("Замеры задержки пакетной записи отсутствуют или содержат некорректные значения (NaN / non-positive).")

    # backup_duration_s must be finite and > 0.0
    if not (isinstance(checkpoint.backup_duration_s, (int, float)) and math.isfinite(checkpoint.backup_duration_s) and checkpoint.backup_duration_s > 0.0):
        invalid_reasons.append("Замеры длительности бэкапа отсутствуют или содержат некорректные значения (NaN / non-positive).")

    # db_size_mb must be finite and > 0.0
    if not (isinstance(checkpoint.db_size_mb, (int, float)) and math.isfinite(checkpoint.db_size_mb) and checkpoint.db_size_mb > 0.0):
        invalid_reasons.append("Размер базы данных отсутствует или некорректен (<= 0 / NaN).")

    # actual_offers must be > 0 and correspond to checkpoint target
    if checkpoint.actual_offers <= 0:
        invalid_reasons.append("Фактическое количество офферов равно 0 или отрицательно.")
    elif checkpoint.target_offers > 0 and checkpoint.actual_offers < int(checkpoint.target_offers * 0.8):
        invalid_reasons.append(
            f"Фактическое количество предложений ({checkpoint.actual_offers:,}) не соответствует целевой контрольной точке ({checkpoint.target_offers:,})."
        )

    if invalid_reasons:
        return StorageDecision(
            decision="NOT_MEASURED",
            is_sqlite_sufficient=False,  # SP30-04: нечисловые/неполные метрики НЕ дают положительный вердикт!
            summary="Нагрузочные замеры для данной контрольной точки не проводились, неполны или недостоверны.",
            reasons=invalid_reasons,
            metrics=checkpoint.to_dict(),
        )

    # 1. Search Latency SLA: p95 must be <= 50ms (warning at 100ms)
    if checkpoint.search_p95_ms > 100.0:
        evaluate_pg = True
        reasons.append(f"Поисковая задержка p95 ({checkpoint.search_p95_ms:.1f} мс) превышает допустимый порог 100 мс.")
    elif checkpoint.search_p95_ms > 50.0:
        reasons.append(f"Поисковая задержка p95 ({checkpoint.search_p95_ms:.1f} мс) находится в допустимом диапазоне (50–100 мс).")
    else:
        reasons.append(f"Поисковая задержка p95 в норме ({checkpoint.search_p95_ms:.1f} мс <= 50 мс).")

    # 2. Write Latency: 1k batch insert <= 300ms (warning at 500ms)
    if checkpoint.write_latency_1k_ms > 500.0:
        evaluate_pg = True
        reasons.append(f"Задержка пакетной записи ({checkpoint.write_latency_1k_ms:.1f} мс на 1k) превышает 500 мс.")
    else:
        reasons.append(f"Задержка записи в норме ({checkpoint.write_latency_1k_ms:.1f} мс на 1k предложений).")

    # 3. Database File Size: warning at 10 GB
    if checkpoint.db_size_mb > 10240.0:
        evaluate_pg = True
        reasons.append(f"Размер базы данных ({checkpoint.db_size_mb:.1f} МБ) превышает 10 ГБ.")
    else:
        reasons.append(f"Размер базы данных оптимален ({checkpoint.db_size_mb:.1f} МБ).")

    # 4. Backup Duration: <= 20 seconds (warning at 30s)
    if checkpoint.backup_duration_s > 30.0:
        evaluate_pg = True
        reasons.append(f"Время создания консистентного бэкапа ({checkpoint.backup_duration_s:.1f} с) превышает 30 с.")
    else:
        reasons.append(f"Время онлайн-бэкапа в норме ({checkpoint.backup_duration_s:.2f} с).")

    decision = "EVALUATE_POSTGRESQL" if evaluate_pg else "STAY_ON_SQLITE"
    if decision == "STAY_ON_SQLITE":
        summary = (
            f"SQLite полностью справляется с объемом {checkpoint.name} ({checkpoint.actual_offers} предложений). "
            f"Задержка p95 {checkpoint.search_p95_ms:.1f} мс, бэкап {checkpoint.backup_duration_s:.2f} с. "
            "Переход на PostgreSQL не требуется."
        )
    else:
        summary = (
            f"Для контрольной точки {checkpoint.name} зафиксировано превышение пороговых метрик. "
            "Рекомендуется подготовка пилота PostgreSQL."
        )

    return StorageDecision(
        decision=decision,
        is_sqlite_sufficient=not evaluate_pg,
        summary=summary,
        reasons=reasons,
        metrics=checkpoint.to_dict(),
    )


def run_scale_benchmark(
    target_offers: int = 1000,
    *,
    db_path: Optional[str] = None,
    checkpoint_name: Optional[str] = None,
    test_queries: Optional[List[str]] = None,
) -> ScaleCheckpoint:
    """
    Runs an isolated end-to-end benchmark on an SQLite database.
    Populates synthetic products, sellers, channels, offers, and runs test queries.
    """
    t_start = time.perf_counter()
    is_temp = db_path is None
    temp_dir_obj = None

    if is_temp:
        temp_dir_obj = tempfile.TemporaryDirectory()
        db_file = os.path.join(temp_dir_obj.name, "scale_bench.db")
    else:
        db_file = db_path

    # 1. Cold Startup Measurement
    t_boot = time.perf_counter()
    conn = sqlite3.connect(db_file, timeout=60.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    init_schema_v2(conn)
    startup_ms = (time.perf_counter() - t_boot) * 1000.0

    product_repo = ProductRepository(conn)
    seller_repo = SellerRepository(conn)
    channel_repo = ChannelRepository(conn)
    offer_repo = OfferRepository(conn)
    search_repo = SearchRepository(conn)
    search_core = SearchCore(search_repo)

    # 2. Populate Sellers & Channels
    num_sellers = max(5, int(target_offers / 100))
    sellers_list: List[Seller] = []
    channels_list: List[Channel] = []
    seller_channels_map: Dict[str, List[Channel]] = {}

    for s_idx in range(num_sellers):
        sid = f"s_bench_{s_idx}"
        s = Seller(id=sid, slug=f"bench-store-{s_idx}", name=f"Bench Store {s_idx}", rating=4.8)
        seller_repo.save_or_update(s)
        sellers_list.append(s)

        # 2 channels per seller (Direct + Kaspi), strictly linked to this seller (SP30-06)
        ch_dir = Channel(id=f"ch_d_{s_idx}", seller_id=sid, channel_type=ChannelType.DIRECT, name="Web Direct")
        ch_ksp = Channel(id=f"ch_k_{s_idx}", seller_id=sid, channel_type=ChannelType.KASPI, name="Kaspi Channel")
        channel_repo.save_or_update(ch_dir)
        channel_repo.save_or_update(ch_ksp)
        channels_list.extend([ch_dir, ch_ksp])
        seller_channels_map[sid] = [ch_dir, ch_ksp]

    # 3. Populate Canonical Products
    num_products = max(10, int(target_offers / 5))
    categories = ["smartphones", "laptops", "tablets", "appliances", "audio"]
    brands = ["Apple", "Samsung", "Xiaomi", "Sony", "Dyson", "LG", "Asus"]

    products_list: List[CanonicalProduct] = []
    for p_idx in range(num_products):
        b = brands[p_idx % len(brands)]
        cat = categories[p_idx % len(categories)]
        title = f"{b} Model Series {p_idx} 256GB Platinum"
        prod = CanonicalProduct(
            id=f"prod_b_{p_idx}",
            canonical_key=f"bench:{b.lower()}:{p_idx}",
            title=title,
            category=cat,
            brand=b,
            model=f"Model {p_idx}",
        )
        product_repo.save_or_update(prod)
        products_list.append(prod)

    # 4. Measure 1k Write Latency & Batch Population
    now_iso = utc_now_iso()
    batch_offers: List[Tuple] = []

    for o_idx in range(target_offers):
        p = products_list[o_idx % len(products_list)]
        s = sellers_list[o_idx % len(sellers_list)]
        # SP30-06: Канал выбирается строго из каналов ДАННОГО продавца
        s_chans = seller_channels_map[s.id]
        ch = s_chans[o_idx % len(s_chans)]
        price = 50000.0 + (o_idx % 500) * 1000.0
        cond = Condition.NEW if o_idx % 4 != 0 else Condition.USED
        oid = f"off_b_{o_idx}"

        batch_offers.append((
            oid, p.id, s.id, ch.id, f"SKU-{o_idx}", f"https://shop.kz/p/{o_idx}",
            "", price, price + 5000.0, "KZT", cond, "in_stock", "Алматы",
            now_iso, 1, "{}", now_iso, now_iso
        ))

    insert_sql = """
    INSERT OR REPLACE INTO offers (
        id, product_id, seller_id, channel_id, external_sku, url, image_url,
        price, old_price, currency, condition, availability, city,
        observed_at, is_active, raw_payload_json, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    # First measure latency of a standard 1,000 offer batch
    sample_1k = batch_offers[:1000]
    t_write = time.perf_counter()
    conn.executemany(insert_sql, sample_1k)
    conn.commit()
    write_1k_ms = (time.perf_counter() - t_write) * 1000.0

    # Insert remainder if target > 1000
    if len(batch_offers) > 1000:
        conn.executemany(insert_sql, batch_offers[1000:])
        conn.commit()

    # Populate price history points preserving all snapshot dimensions (SP30-01, SP30-06)
    history_sql = """
    INSERT INTO offer_price_history (
        offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    sample_history = [
        (r[0], r[7], r[8], now_iso, r[1], r[2], r[3], r[10], r[12])
        for r in batch_offers
    ]
    conn.executemany(history_sql, sample_history)
    conn.commit()

    # SP30-05: Сбор статистики для оптимизатора SQLite перед замерами
    conn.execute("ANALYZE")

    # 5. Measure Search Latency (p95 & avg)
    default_queries = [
        "Apple Model", "Samsung Series", "Xiaomi 256GB", "Sony Audio", "Dyson Platinum",
        "LG Series", "Asus Laptop", "Smartphones Алматы", "Model 10", "Series 5",
    ]
    queries = test_queries or default_queries
    latencies_ms: List[float] = []

    # Warmup
    for q in queries[:2]:
        search_core.search(q)

    for q in queries:
        t_q = time.perf_counter()
        search_core.search(q)
        latencies_ms.append((time.perf_counter() - t_q) * 1000.0)

    latencies_ms.sort()
    search_avg = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
    p95_idx = int(math.ceil(0.95 * len(latencies_ms))) - 1
    search_p95 = latencies_ms[max(0, min(p95_idx, len(latencies_ms) - 1))]

    # 6. Measure Database & Index File Sizes
    conn.commit()
    file_size_bytes = os.path.getsize(db_file) if os.path.exists(db_file) else 0
    wal_file = f"{db_file}-wal"
    if os.path.exists(wal_file):
        file_size_bytes += os.path.getsize(wal_file)
    db_size_mb = round(file_size_bytes / (1024.0 * 1024.0), 2)

    # SP30-06: Measure FTS and History size (table + indexes) via dbstat when available, with marked fallback
    fts_size_bytes = 0
    hist_table_bytes = 0
    hist_index_bytes = 0
    size_method = "dbstat"

    try:
        cur = conn.execute(
            """
            SELECT name, SUM(pgsize)
            FROM dbstat
            WHERE name LIKE 'canonical_products_fts%'
               OR name = 'offer_price_history'
               OR name LIKE 'sqlite_autoindex_offer_price_history%'
               OR name LIKE 'idx_oph%'
               OR name LIKE '%offer_price_history%'
            GROUP BY name
            """
        )
        for row in cur.fetchall():
            t_name, sz = row[0], row[1] or 0
            if "fts" in t_name:
                fts_size_bytes += sz
            elif t_name == "offer_price_history":
                hist_table_bytes += sz
            elif "oph" in t_name or "offer_price_history" in t_name:
                hist_index_bytes += sz
    except sqlite3.Error:
        size_method = "estimated"
        fts_row = conn.execute("SELECT COUNT(*) FROM canonical_products_fts").fetchone()
        fts_rows = fts_row[0] if fts_row else 0
        fts_size_bytes = int(fts_rows * 256)

        hist_row = conn.execute("SELECT COUNT(*) FROM offer_price_history").fetchone()
        hist_rows = hist_row[0] if hist_row else 0
        # Честная оценка: ~112 байт на строку таблицы и ~58 байт на строку первичного индекса
        hist_table_bytes = int(hist_rows * 112)
        hist_index_bytes = int(hist_rows * 58)

    hist_total_bytes = hist_table_bytes + hist_index_bytes
    fts_size_mb = round(fts_size_bytes / (1024.0 * 1024.0), 2)
    hist_size_mb = round(hist_total_bytes / (1024.0 * 1024.0), 2)
    hist_idx_size_mb = round(hist_index_bytes / (1024.0 * 1024.0), 2)

    # 6b. Cold Startup Measurement on Populated Database (SP30-06)
    t_cold = time.perf_counter()
    conn_cold = sqlite3.connect(db_file, timeout=30.0)
    conn_cold.execute("PRAGMA journal_mode = WAL")
    conn_cold.execute("PRAGMA synchronous = NORMAL")
    _ = conn_cold.execute("SELECT COUNT(*) FROM offers").fetchone()
    cold_startup_ms = (time.perf_counter() - t_cold) * 1000.0
    conn_cold.close()

    # 7. Measure Backup Duration via SQLite Online Backup API under concurrent scan/write load (SP30-06)
    backup_temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    backup_temp.close()
    backup_duration_s = 0.0
    concurrency_errors = 0
    concurrency_write_latency_ms: Optional[float] = None
    concurrency_verified = False

    try:
        dst_conn = sqlite3.connect(backup_temp.name)

        # Concurrent worker thread performing reads and write during backup
        worker_stop = False

        def concurrent_worker():
            nonlocal concurrency_errors, concurrency_write_latency_ms
            try:
                w_conn = sqlite3.connect(db_file, timeout=10.0)
                w_conn.execute("PRAGMA journal_mode = WAL")
                w_core = SearchCore(SearchRepository(w_conn))
                for _ in range(5):
                    if worker_stop:
                        break
                    w_core.search("Apple")
                    time.sleep(0.001)

                # Concurrent write probe (updates existing offer under backup)
                t_cw = time.perf_counter()
                w_conn.execute(
                    """
                    INSERT OR REPLACE INTO offers (
                        id, product_id, seller_id, channel_id, external_sku, url, image_url,
                        price, old_price, currency, condition, availability, city,
                        observed_at, is_active, raw_payload_json, created_at, updated_at
                    ) VALUES ('off_b_0', 'prod_b_0', 's_bench_0', 'ch_d_0', 'CONC_SKU', 'https://shop.kz/conc', '', 49990.0, 54990.0, 'KZT', 'new', 'in_stock', 'Алматы', ?, 1, '{}', ?, ?)
                    """,
                    (now_iso, now_iso, now_iso),
                )
                w_conn.commit()
                concurrency_write_latency_ms = round((time.perf_counter() - t_cw) * 1000.0, 2)
                w_conn.close()
            except Exception as e:
                concurrency_errors += 1
                logger.warning("Concurrent worker error during backup: %s", e)

        import threading
        t_worker = threading.Thread(target=concurrent_worker)
        t_worker.start()

        t_backup = time.perf_counter()
        # Perform backup
        conn.backup(dst_conn)
        backup_duration_s = time.perf_counter() - t_backup
        worker_stop = True
        t_worker.join(timeout=5.0)

        # Integrity check of backup database
        qc = dst_conn.execute("PRAGMA quick_check").fetchone()
        if qc and qc[0] == "ok" and concurrency_errors == 0:
            concurrency_verified = True

        dst_conn.close()
    finally:
        if os.path.exists(backup_temp.name):
            os.remove(backup_temp.name)

    # 8. Memory Footprint (Peak RSS) (SP30-06)
    mem_mb: Optional[float] = None
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # On macOS, maxrss is in bytes; on Linux, in kilobytes
        if os.uname().sysname == "Darwin":
            mem_mb = round(rusage.ru_maxrss / (1024.0 * 1024.0), 2)
        else:
            mem_mb = round(rusage.ru_maxrss / 1024.0, 2)
    except Exception:
        mem_mb = None

    # SP30-06: Фактический подсчет строк из БД
    actual_offers_row = conn.execute("SELECT COUNT(*) FROM offers").fetchone()
    actual_offers_cnt = actual_offers_row[0] if actual_offers_row else target_offers

    actual_products_row = conn.execute("SELECT COUNT(*) FROM canonical_products").fetchone()
    actual_products_cnt = actual_products_row[0] if actual_products_row else num_products

    # Cleanup
    conn.close()
    if temp_dir_obj:
        temp_dir_obj.cleanup()

    actual_name = checkpoint_name or (f"{target_offers // 1000}k" if target_offers < 1000000 else f"{target_offers // 1000000}m")

    return ScaleCheckpoint(
        name=actual_name,
        target_offers=target_offers,
        actual_offers=actual_offers_cnt,
        total_products=actual_products_cnt,
        total_sellers=num_sellers,
        total_channels=len(channels_list),
        search_p95_ms=round(search_p95, 2),
        search_avg_ms=round(search_avg, 2),
        write_latency_1k_ms=round(write_1k_ms, 2),
        db_size_mb=db_size_mb,
        fts_size_mb=fts_size_mb,
        price_history_size_mb=hist_size_mb,
        backup_duration_s=round(backup_duration_s, 3),
        memory_mb=mem_mb,
        startup_ms=round(startup_ms, 2),
        history_index_size_mb=hist_idx_size_mb,
        size_measurement_method=size_method,
        peak_rss_mb=mem_mb,
        cold_startup_ms=round(cold_startup_ms, 2),
        concurrency_verified=concurrency_verified,
        concurrency_write_latency_ms=concurrency_write_latency_ms,
        concurrency_errors=concurrency_errors,
    )


def get_current_scale_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Analyzes the current live / stand database against Stage 30 checkpoints.
    Reports honest status (ERROR_NO_SCHEMA, NOT_MEASURED, or measured verdict) (SP30-04).
    """
    try:
        r = conn.execute("SELECT COUNT(*) FROM offers").fetchone()
        total_offers = r[0] if r else 0
        r = conn.execute("SELECT COUNT(*) FROM canonical_products").fetchone()
        total_products = r[0] if r else 0
        r = conn.execute("SELECT COUNT(*) FROM sellers").fetchone()
        total_sellers = r[0] if r else 0
        r = conn.execute("SELECT COUNT(*) FROM offer_price_history").fetchone()
        total_history = r[0] if r else 0
    except sqlite3.Error as e:
        return {
            "current_counts": {
                "offers": 0,
                "products": 0,
                "sellers": 0,
                "price_history_points": 0,
            },
            "checkpoints": [
                {"name": "100k", "target": 100000, "status": "NOT_MEASURED"},
                {"name": "500k", "target": 500000, "status": "UPCOMING"},
                {"name": "1m", "target": 1000000, "status": "UPCOMING"},
                {"name": "5m", "target": 5000000, "status": "UPCOMING"},
                {"name": "10m", "target": 10000000, "status": "UPCOMING"},
            ],
            "sqlite_verdict": {
                "status": "ERROR_NO_SCHEMA",
                "message": f"Таблицы каталога не инициализированы: {e}",
                "recommended_threshold": "Требуется инициализация схемы",
            },
        }

    # Standard scale checkpoints defined in Stage 30
    checkpoints = [
        {"name": "100k", "target": 100000, "status": "NOT_MEASURED" if total_offers < 100000 else "TARGET_REACHED_UNMEASURED"},
        {"name": "500k", "target": 500000, "status": "UPCOMING"},
        {"name": "1m", "target": 1000000, "status": "UPCOMING"},
        {"name": "5m", "target": 5000000, "status": "UPCOMING"},
        {"name": "10m", "target": 10000000, "status": "UPCOMING"},
    ]

    return {
        "current_counts": {
            "offers": total_offers,
            "products": total_products,
            "sellers": total_sellers,
            "price_history_points": total_history,
        },
        "checkpoints": checkpoints,
        "sqlite_verdict": {
            "status": "NOT_MEASURED",
            "message": f"Текущий объем: {total_offers} офферов. Автоматический бенчмарк не запускался на этом экземпляре.",
            "recommended_threshold": "1M+ offers",
        },
    }
