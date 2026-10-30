import os
import sys
import json
import re
import time
import sqlite3
import hashlib
import secrets
import datetime
import logging
import threading
from typing import Optional, Dict, Any, List, Tuple, Iterable
from config import DB_PATH, get_scan_interval_seconds, merge_user_settings

logger = logging.getLogger("kz_price_hunter.database")

_DUAL_WRITE_STATS = {
    "total": 0,
    "failures": 0,
    "last_error": None,
}

def get_dual_write_stats() -> Dict[str, Any]:
    """Возвращает статистику работы Dual-Write v2."""
    return dict(_DUAL_WRITE_STATS)

def reset_dual_write_stats() -> None:
    """Сбрасывает счетчики Dual-Write v2 (для тестов)."""
    _DUAL_WRITE_STATS["total"] = 0
    _DUAL_WRITE_STATS["failures"] = 0
    _DUAL_WRITE_STATS["last_error"] = None

def _record_dual_write_error(pid: str, err: Exception) -> None:
    """Фиксирует сбой двойной записи в статистику, лог и телеметрию."""
    _DUAL_WRITE_STATS["failures"] += 1
    _DUAL_WRITE_STATS["last_error"] = str(err)
    logger.warning("Dual-write v2 failed for product %s: %s", pid, err)
    try:
        from telemetry import telemetry, SEVERITY_WARNING, COMPONENT_SYSTEM
        telemetry.record_event(
            "dual_write_error",
            SEVERITY_WARNING,
            COMPONENT_SYSTEM,
            f"Dual-write v2 failed for product {pid}: {err}",
            data={"product_id": pid, "error": str(err)},
            offer_id=f"off_{pid}",  # сквозной ID предложения V2 (S2-E24, V07)
        )
    except Exception:
        pass

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


def _reconcile_source_conn(conn, shop_key, source_url, product_ids, complete=False,
                           observed_seq: Optional[float] = None) -> Optional[List[str]]:
    """Сверка источника в открытой транзакции conn, без фиксации (R04).

    Возвращает id товаров, снятых этой сверкой (G01: по ним проверяются наблюдения).
    None — источник уже сверен по более свежему обходу: старый результат не снимает товары,
    появившиеся позже, и не возвращает снятые (F01).
    """
    if observed_seq is not None:
        row = conn.execute("SELECT observed_seq FROM source_observed WHERE shop_key = ? AND source_url = ?",
                           (shop_key, source_url)).fetchone()
        if row and row[0] > observed_seq:
            return None
        conn.execute("""INSERT INTO source_observed (shop_key, source_url, observed_seq) VALUES (?, ?, ?)
                        ON CONFLICT(shop_key, source_url) DO UPDATE SET observed_seq = excluded.observed_seq""",
                     (shop_key, source_url, observed_seq))
    ids = {str(pid) for pid in product_ids}
    existing_active = {
        r[0] for r in conn.execute(
            "SELECT product_id FROM product_sources WHERE shop_key = ? AND source_url = ? AND active = 1",
            (shop_key, source_url),
        )
    }
    active_before: set = set()
    if complete and ids:
        active_before = {r[0] for r in conn.execute(
            "SELECT p.id FROM products p JOIN product_sources ps ON ps.product_id = p.id "
            "WHERE ps.shop_key = ? AND ps.source_url = ? AND p.is_active = 1", (shop_key, source_url))}
        to_deactivate = sorted(existing_active - ids)
        for i in range(0, len(to_deactivate), 500):
            chunk = to_deactivate[i:i + 500]
            marks = ",".join("?" * len(chunk))
            conn.execute(
                f"UPDATE product_sources SET active=0 WHERE shop_key=? AND source_url=? AND product_id IN ({marks})",
                (shop_key, source_url, *chunk),
            )
    to_activate = sorted(ids - existing_active)
    if to_activate:
        conn.executemany("""INSERT INTO product_sources(product_id,shop_key,source_url,active)
            VALUES (?,?,?,1) ON CONFLICT(product_id,shop_key,source_url) DO UPDATE SET active=1""",
            [(pid, shop_key, source_url) for pid in to_activate])
    if complete and ids:
        conn.execute("""UPDATE products SET is_active=0 WHERE is_active=1 AND id IN
            (SELECT product_id FROM product_sources WHERE shop_key=? AND source_url=? AND active=0)
            AND NOT EXISTS (SELECT 1 FROM product_sources s WHERE s.product_id=products.id AND s.active=1)""",
            (shop_key, source_url))
        _retire_mirrored_offers_conn(conn, shop_key, source_url)
    if not active_before:
        return []
    disappeared = sorted(active_before - ids)
    retired: List[str] = []
    for i in range(0, len(disappeared), 500):
        chunk = disappeared[i:i + 500]
        marks = ",".join("?" * len(chunk))
        retired.extend(
            r[0] for r in conn.execute(f"SELECT id FROM products WHERE is_active = 0 AND id IN ({marks})", chunk)
        )
    if retired:
        _queue_watch_refresh_conn(conn, retired)
    return retired


def _queue_watch_refresh_conn(conn, product_ids) -> None:
    """G03: задание обновить память наблюдений — в той же транзакции, что снятие товара."""
    now = time.time()
    # Новое снятие — новый переход: задание снова начинается с фазы 'retire', даже если прежнее
    # ещё не удалено
    conn.executemany("""INSERT INTO watch_refresh (product_id, created_at, phase, gen) VALUES (?, ?, 'retire', 0)
                        ON CONFLICT(product_id) DO UPDATE SET phase = 'retire', gen = watch_refresh.gen + 1,
                                                              created_at = excluded.created_at""",
                     [(str(pid), now) for pid in product_ids])


def _watch_refresh_jobs(ids: List[str], phase: Optional[str] = None) -> Dict[str, int]:
    jobs: Dict[str, int] = {}
    with get_connection() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            sql = f"SELECT product_id, gen FROM watch_refresh WHERE product_id IN ({','.join('?' * len(chunk))})"
            params: List[Any] = list(chunk)
            if phase:
                sql += " AND phase = ?"
                params.append(phase)
            jobs.update((r[0], r[1]) for r in conn.execute(sql, params))
    return jobs


def refresh_retired_watches(product_ids: List[str]) -> None:
    """Выполняет задания watch_refresh этих товаров: «снят», затем текущее состояние (G03–G05).

    Задание значит «товар был снят» независимо от того, что с ним сейчас (G04). Две фазы:
      1. 'retire' → память наблюдений получает was_available = 0 в одной транзакции с переводом
         задания в 'current'; фаза и поколение проверяются в той же транзакции, поэтому ни повтор,
         ни конкурирующий обработчик не применят снятие второй раз (_apply_retire_transition).
      2. 'current' → оценка текущего состояния. Если товар уже вернулся, наблюдение даёт «снова
         в продаже»; повтор и конкурент идемпотентны — после события память уже was_available = 1.
    Затем удаляются задания в фазе 'current' того же поколения: новое снятие, случившееся тем
    временем, не подтверждается старым вызовом. Ошибка оставляет задание в его фазе.
    """
    ids = [str(pid) for pid in product_ids]
    if not ids:
        return
    for start in range(0, len(ids), 500):
        page_ids = ids[start:start + 500]
        retire = _watch_refresh_jobs(page_ids, "retire")
        if retire:
            _apply_retire_transition(retire)
        current = _watch_refresh_jobs(page_ids, "current")
        if not current:
            continue  # задания уже выполнены другим путём (возврат товара, старт, обслуживание)
        returned = [o for o in watched_offers(list(current)) if o.get("is_available")]
        if returned:
            evaluate_watches(returned)
        with get_connection() as conn:
            for pid, gen in current.items():
                conn.execute("DELETE FROM watch_refresh WHERE product_id = ? AND phase = 'current' AND gen = ?",
                             (pid, gen))
            conn.commit()


def watch_refresh_pending(product_ids: List[str]) -> List[str]:
    """Из этих товаров — те, по которым ещё не применён переход «снят» (G04)."""
    ids = [str(pid) for pid in product_ids]
    pending: List[str] = []
    with get_connection() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            pending.extend(r[0] for r in conn.execute(
                f"SELECT product_id FROM watch_refresh WHERE product_id IN ({','.join('?' * len(chunk))})", chunk))
    return pending


def watch_refresh_count() -> int:
    """Число накопленных заданий обновления памяти наблюдений (G03)."""
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM watch_refresh").fetchone()[0]


def drain_watch_refresh(page: int = 500, max_pages: Optional[int] = None) -> int:
    """Выполняет накопленные задания обновления памяти наблюдений страницами по page.

    По умолчанию (max_pages=None) разбирает всю очередь до конца; при заданном max_pages
    выполняет не более указанного числа страниц за вызов (для фонового разбора после старта
    HTTP-сервера без блокировки порта и цикла событий). Возвращает число обработанных заданий.
    """
    done = 0
    pages = 0
    while max_pages is None or pages < max_pages:
        with get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT product_id FROM watch_refresh ORDER BY rowid LIMIT ?", (page,))]
        if not ids:
            return done
        refresh_retired_watches(ids)
        done += len(ids)
        pages += 1
    return done


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _retire_mirrored_offers_conn(conn, shop_key: Optional[str] = None, source_url: Optional[str] = None) -> int:
    """G01: снятый товар снимается и в V2 — поиск и наблюдения читают offers, а не products.

    Затрагиваются только зеркальные предложения dual-write (id = 'off_' || products.id), и только
    у товаров, которые уже сняты в products — то есть не подтверждены ни одним активным источником.
    Предложения соцсетей и ручные каналы продавца имеют свои id и не трогаются. Возврат товара
    делает сама запись: dual-write ставит предложению is_active = 1.
    Без shop_key/source_url — по всей базе (разовая починка рассинхронизации прежних версий).
    """
    if not _has_table(conn, "offers"):
        return 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    scope = ""
    params: List[Any] = [now]
    if shop_key is not None:
        scope = ("AND p.id IN (SELECT product_id FROM product_sources WHERE shop_key = ? AND source_url = ?)")
        params.extend([shop_key, source_url])
    return conn.execute(f"""
        UPDATE offers SET is_active = 0, updated_at = ?
        WHERE is_active = 1 AND id IN (SELECT 'off_' || p.id FROM products p WHERE p.is_active = 0 {scope})
    """, params).rowcount


def sync_retired_offers() -> int:
    """Починка при старте: сняты в products, но активны в offers (до 6.4.6). Идемпотентна.

    Снятие offers и задания обновить память наблюдений по этим товарам фиксируются одной
    транзакцией; сами задания выполняет drain_watch_refresh() и повторяет при следующем старте,
    если проверка сорвалась (G02, G03). Один раз, по метке в schema_metadata, в задания ставятся и
    товары, которые уже сняла 6.4.6 без обновления памяти.
    """
    with get_connection() as conn:
        if not _has_table(conn, "offers"):
            return 0
        conn.execute("BEGIN IMMEDIATE")
        product_ids = [r[0] for r in conn.execute(
            "SELECT p.id FROM products p JOIN offers o ON o.id = 'off_' || p.id "
            "WHERE p.is_active = 0 AND o.is_active = 1")]
        count = _retire_mirrored_offers_conn(conn)
        _queue_watch_refresh_conn(conn, product_ids)
        if not conn.execute("SELECT 1 FROM schema_metadata WHERE name = ?", (WATCH_REFRESH_SEED,)).fetchone():
            _queue_watch_refresh_conn(conn, [r[0] for r in conn.execute(
                "SELECT p.id FROM products p JOIN offers o ON o.id = 'off_' || p.id "
                "WHERE p.is_active = 0 AND o.is_active = 0")])
            conn.execute("INSERT INTO schema_metadata (name, value) VALUES (?, ?)",
                         (WATCH_REFRESH_SEED, datetime.datetime.now(datetime.timezone.utc).isoformat()))
        conn.commit()
    if count:
        print(f"[DB] G01: снято {count} предложений V2, которых уже нет в каталоге")
    return count


# Метка разового досева заданий памяти для баз, которые уже прошли починку 6.4.6 (G03)
WATCH_REFRESH_SEED = "watch_refresh_seed_v1"


def reconcile_source(shop_key, source_url, products, complete=False):
    """Only a verified complete source may retire its previously observed offers."""
    with get_connection() as conn:
        retired = _reconcile_source_conn(conn, shop_key, source_url, [p["id"] for p in products], complete,
                                         time.time())
        conn.commit()
    invalidate_alerts_cache()
    return retired or []


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


def _record_source_scan_conn(conn, shop_key, source_url, category, scan_id, started_at, kind, assessment, metrics) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reason = "; ".join(assessment["reasons"] + assessment["warnings"])[:500] or None
    conn.execute("""INSERT INTO source_scans (shop_key, source_url, category, scan_id, started_at, finished_at, kind,
            quality, reason, received, valid, rejected, duplicates, with_image, baseline, baseline_basis, accepted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (shop_key, source_url, category, scan_id, started_at, now, kind, assessment["quality"], reason,
         metrics["received"], metrics["valid"], metrics["rejected"], metrics.get("duplicates", 0), metrics["with_image"],
         assessment["baseline"], assessment["basis"], int(bool(assessment["learn"]))))


def record_source_scan(shop_key, source_url, category, scan_id, started_at, kind, assessment, metrics) -> None:
    with get_connection() as conn:
        _record_source_scan_conn(conn, shop_key, source_url, category, scan_id, started_at, kind, assessment, metrics)
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
        try:
            # Очистка новых событий поиска — та же, что вызывается точечно: реализация одна,
            # иначе два независимых удаления по сроку со временем разъезжаются
            from repositories.search_analytics_repo import SearchAnalyticsRepository
            deleted += SearchAnalyticsRepository(conn).purge_old_events(
                days=days or sa.RETENTION_DAYS)
        except (sqlite3.OperationalError, ImportError):
            pass  # таблицы v2 ещё нет — legacy-контур это не ломает
        try:
            # TTL контента соцсетей (S2-E08): просроченные сторис и временные акции
            # удаляются тем же регулярным проходом, что и остальная аналитика.
            from repositories.content_repo import ContentObservationRepository
            deleted += ContentObservationRepository(conn).purge_expired(moment.isoformat())
        except (sqlite3.OperationalError, ImportError):
            pass  # таблицы v2 ещё нет
        conn.commit()
    return deleted


SLOW_WRITE_TX_SECONDS = float(os.getenv("SLOW_WRITE_TX_SECONDS", "2.0"))
_WRITE_TX_LOCK = threading.Lock()
_WRITE_TX_STATS: Dict[str, Dict[str, Any]] = {}
_SLOW_WRITE_TX_LOG: List[Dict[str, Any]] = []
_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")
_INTERNAL_FRAME_NAMES = frozenset({
    "_run_tracked", "execute", "executemany", "cursor", "_resolve_caller_op",
    "get_connection", "_connect",
})


def _classify_sql(sql: str) -> str:
    head = sql.lstrip()[:24].upper()
    if head.startswith("BEGIN"):
        if "IMMEDIATE" in head or "EXCLUSIVE" in head:
            return "begin_write"
        return "begin_deferred"
    if head.startswith(("COMMIT", "END")):
        return "commit"
    if head.startswith("ROLLBACK"):
        return "rollback"
    for verb in _WRITE_VERBS:
        if head.startswith(verb):
            return "write"
    return "other"


def _resolve_caller_op() -> str:
    try:
        frame = sys._getframe(2)
        while frame is not None:
            name = frame.f_code.co_name
            if name not in _INTERNAL_FRAME_NAMES:
                return name
            frame = frame.f_back
    except ValueError:
        pass
    return "unknown"


def get_write_tx_stats() -> Dict[str, Any]:
    """Сводка длительности write-транзакций SQLite по операциям.

    max_wait_ms — только по транзакциям с точным разделением (BEGIN IMMEDIATE). Для неявных
    транзакций ожидание не отделимо: их hold — верхняя граница, счётчик unsplit_count (T02).
    """
    with _WRITE_TX_LOCK:
        ops = {k: dict(v) for k, v in _WRITE_TX_STATS.items()}
        slow = list(_SLOW_WRITE_TX_LOG)
    max_hold_ms = max((v["max_hold_ms"] for v in ops.values()), default=0.0)
    max_wait_ms = max((v["max_wait_ms"] for v in ops.values()), default=0.0)
    return {
        "threshold_seconds": SLOW_WRITE_TX_SECONDS,
        "max_hold_ms": round(max_hold_ms, 2),
        "max_wait_ms": round(max_wait_ms, 2),
        "slow_count": sum(v["slow_count"] for v in ops.values()),
        "unsplit_count": sum(v.get("unsplit_count", 0) for v in ops.values()),
        "operations": ops,
        "recent_slow": slow,
    }


def reset_write_tx_stats() -> None:
    """Сбрасывает статистику длительности write-транзакций (для тестов и замеров)."""
    with _WRITE_TX_LOCK:
        _WRITE_TX_STATS.clear()
        _SLOW_WRITE_TX_LOG.clear()


class TrackedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=(), /):
        return self.connection._run_tracked(super().execute, sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        return self.connection._run_tracked(super().executemany, sql, seq_of_parameters)


class ClosingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tx_started_at: Optional[float] = None
        self._tx_acquired_at: Optional[float] = None
        self._tx_wait_sec: Optional[float] = None
        self._tx_split_exact: bool = False
        self._tx_op: str = "unknown"
        self._tx_statements: int = 0

    def cursor(self, factory=TrackedCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=(), /):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        return self.cursor().executemany(sql, seq_of_parameters)

    def _run_tracked(self, fn, sql, *args):
        """Учёт write-транзакций (T02): ожидание блокировки отделяется только там, где это возможно.

        * BEGIN IMMEDIATE/EXCLUSIVE — сама команда и есть захват блокировки: её время — ожидание
          (wait), удержание (hold) отсчитывается после неё. Разделение точное.
        * Неявная транзакция или BEGIN DEFERRED — первая запись SQLite одновременно ждёт блокировку
          и выполняется, и разделить их API не позволяет. Её время целиком идёт в удержание как
          верхняя граница (блокировка могла быть взята в самом начале), ожидание помечается
          неизвестным (wait = None, split = 'unknown') и в max_wait не попадает.
        """
        kind = _classify_sql(sql) if isinstance(sql, str) else "other"
        if kind == "other":
            return fn(sql, *args)
        if kind in ("commit", "rollback"):
            try:
                return fn(sql, *args)
            finally:
                # Неудачный COMMIT (SQLITE_BUSY) оставляет транзакцию открытой: учёт не закрывается
                if self._tx_started_at is not None and not self.in_transaction:
                    self._finish_write_tx(kind)
        if self._tx_started_at is None:
            self._tx_op = _resolve_caller_op()
            self._tx_statements = 0
            self._tx_started_at = time.monotonic()
            self._tx_acquired_at = None
            self._tx_wait_sec = None
            self._tx_split_exact = False
        self._tx_statements += 1
        if self._tx_acquired_at is None and kind in ("begin_write", "write"):
            t0 = time.monotonic()
            try:
                res = fn(sql, *args)
            except BaseException:
                if not self.in_transaction:
                    self._reset_write_tx()
                raise
            t1 = time.monotonic()
            if kind == "begin_write":
                self._tx_acquired_at = t1
                self._tx_wait_sec = t1 - self._tx_started_at
                self._tx_split_exact = True
            else:
                self._tx_acquired_at = t0
                self._tx_wait_sec = None
                self._tx_split_exact = False
            if not self.in_transaction:
                self._finish_write_tx("autocommit")
            return res
        res = fn(sql, *args)
        if not self.in_transaction:
            if self._tx_acquired_at is not None:
                self._finish_write_tx("autocommit")
            elif kind == "begin_deferred":
                pass  # BEGIN без записи ещё ничего не держит
        return res

    def _reset_write_tx(self) -> None:
        self._tx_started_at = None
        self._tx_acquired_at = None
        self._tx_wait_sec = None
        self._tx_split_exact = False
        self._tx_op = "unknown"
        self._tx_statements = 0

    def _finish_write_tx(self, outcome: str) -> None:
        started = self._tx_started_at
        acquired = self._tx_acquired_at
        wait_sec = self._tx_wait_sec
        exact = self._tx_split_exact
        op = self._tx_op
        stmts = self._tx_statements
        self._reset_write_tx()
        if started is None or acquired is None:
            return
        finished = time.monotonic()
        hold_sec = max(0.0, finished - acquired)
        total_sec = max(0.0, finished - started)
        hold_ms = round(hold_sec * 1000.0, 2)
        wait_ms = round(wait_sec * 1000.0, 2) if (exact and wait_sec is not None) else None
        total_ms = round(total_sec * 1000.0, 2)
        split = "exact" if exact else "unknown"
        is_slow = hold_sec >= SLOW_WRITE_TX_SECONDS or total_sec >= SLOW_WRITE_TX_SECONDS
        with _WRITE_TX_LOCK:
            entry = _WRITE_TX_STATS.get(op)
            if entry is None:
                entry = {
                    "count": 0,
                    "commits": 0,
                    "rollbacks": 0,
                    "slow_count": 0,
                    "total_hold_ms": 0.0,
                    "max_hold_ms": 0.0,
                    "max_wait_ms": 0.0,
                    "max_total_ms": 0.0,
                    "last_statements": 0,
                    "unsplit_count": 0,
                }
                _WRITE_TX_STATS[op] = entry
            entry["count"] += 1
            if outcome in ("commit", "autocommit"):
                entry["commits"] += 1
            else:
                entry["rollbacks"] += 1
            if is_slow:
                entry["slow_count"] += 1
            entry["total_hold_ms"] = round(entry["total_hold_ms"] + hold_ms, 2)
            if hold_ms > entry["max_hold_ms"]:
                entry["max_hold_ms"] = hold_ms
            if wait_ms is None:
                entry["unsplit_count"] += 1  # ожидание внутри первой записи не отделить — hold верхняя граница
            elif wait_ms > entry["max_wait_ms"]:
                entry["max_wait_ms"] = wait_ms
            if total_ms > entry["max_total_ms"]:
                entry["max_total_ms"] = total_ms
            entry["last_statements"] = stmts
            if is_slow:
                _SLOW_WRITE_TX_LOG.append({
                    "op": op,
                    "outcome": outcome,
                    "hold_ms": hold_ms,
                    "wait_ms": wait_ms,
                    "split": split,
                    "total_ms": total_ms,
                    "statements": stmts,
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
                if len(_SLOW_WRITE_TX_LOG) > 50:
                    del _SLOW_WRITE_TX_LOG[:-50]
        if is_slow:
            print(
                f"[DB Slow Write] op={op} outcome={outcome} hold={hold_sec:.3f}s"
                f"{'' if exact else ' (верхняя граница)'} "
                f"wait={'n/a' if wait_ms is None else f'{wait_ms / 1000:.3f}s'} total={total_sec:.3f}s stmts={stmts}"
            )
            try:
                from telemetry import telemetry, SEVERITY_WARNING, COMPONENT_SYSTEM
                telemetry.record_event(
                    "db_slow_write",
                    SEVERITY_WARNING,
                    COMPONENT_SYSTEM,
                    f"Slow SQLite write tx in {op}: hold={hold_sec:.3f}s ({split}) total={total_sec:.3f}s",
                    data={
                        "op": op,
                        "outcome": outcome,
                        "hold_ms": hold_ms,
                        "wait_ms": wait_ms,
                        "split": split,
                        "total_ms": total_ms,
                        "statements": stmts,
                    },
                )
            except Exception:
                pass

    def commit(self):
        try:
            return super().commit()
        finally:
            # Неудачный commit оставляет транзакцию открытой — её закроет повтор или rollback
            if self._tx_started_at is not None and not self.in_transaction:
                self._finish_write_tx("commit")

    def rollback(self):
        try:
            return super().rollback()
        finally:
            if self._tx_started_at is not None:
                self._finish_write_tx("rollback")

    def close(self):
        if self._tx_started_at is not None:
            self._finish_write_tx("close")
        return super().close()

    def __exit__(self, exc_type, exc_value, traceback):
        in_tx = self._tx_started_at is not None
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            if in_tx and self._tx_started_at is not None:
                self._finish_write_tx("rollback" if exc_type is not None else "commit")
            self.close()


def ensure_journal_mode() -> str:
    """Включает WAL один раз — пока фоновые работники не открыли свои соединения.

    Смена журнального режима требует исключительного доступа к базе. Пока она
    запрашивалась на каждом соединении, занятая база отвечала "database is locked"
    уже на открытии: другое соединение в работающем приложении есть всегда, поэтому
    перейти в WAL не удавалось никогда и падало каждое обращение (C-DB-01).

    Журнальный режим хранится в самом файле базы, поэтому ставится здесь один раз.
    А вот synchronous — настройка соединения и в файле не сохраняется, так что задавать
    её тут бессмысленно: она действовала бы только на это соединение, которое сразу
    закрывается. Её место — get_connection.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        try:
            conn.execute("PRAGMA busy_timeout = 15000;")
        except sqlite3.OperationalError:
            pass
        mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        if mode == "wal":
            return mode
        try:
            mode = str(conn.execute("PRAGMA journal_mode = WAL;").fetchone()[0]).lower()
        except sqlite3.OperationalError as e:
            print(f"[DB] Журнальный режим остался {mode}: WAL не включился ({e})")
        return mode
    finally:
        conn.close()


# Предел WAL после сброса и порог предупреждения мониторинга (6.5.2)
WAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024
WAL_WARN_BYTES = 256 * 1024 * 1024


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    # Магазины сканируются параллельно: ждем освобождения блокировки вместо ошибки "database is locked"
    conn.execute("PRAGMA busy_timeout = 15000;")
    # Журнальный режим здесь не трогаем: его смена требует исключительного доступа и валила
    # открытие соединения на занятой базе (C-DB-01). Он хранится в файле и ставится один раз
    # в ensure_journal_mode(). А synchronous в файле НЕ хранится — это настройка соединения,
    # и без неё каждое соединение работает в FULL, то есть делает полный fsync на каждом
    # коммите: на дисках NAS это дороже и дольше держит блокировки. Прагма блокировки не
    # требует (при держащем писателе выполняется мгновенно), поэтому C-DB-01 она не возвращает.
    conn.execute("PRAGMA synchronous = NORMAL;")
    # Сколько WAL оставлять на диске после его сброса (6.5.2). Без предела файл не уменьшается:
    # на проде он дорос до 561 МБ при базе 2,2 ГБ, и каждая проверка и чтение шли по нему.
    # Настройка соединения, как synchronous; блокировки не требует.
    conn.execute(f"PRAGMA journal_size_limit = {WAL_SIZE_LIMIT_BYTES};")
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
    # Поля товара, извлечённые AI по стандарту ключа (P17-C): бренд, модель, артикул, варианты. Ключ из них
    # собирает product_key.py; поля хранятся, чтобы при смене правил пересобрать ключи без повторных вызовов модели
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS title_fields (
            title_hash TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            key_version TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Связь артикула производителя с товаром и ключом (P17-C3 / link_by_article):
    # brand:article -> title_hash, canonical_key. Новое название с известным артикулом
    # получает ключ существующей группы товаров, прежние ключи не меняются (fixed).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS title_articles (
            article TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (article, title_hash)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_title_articles_title_hash ON title_articles(title_hash)")
    # Третий источник истины (P17-C7): выдача поиска по запросу и вердикт «один товар / разные» по паре названий.
    # Кэш поиска нужен и для суточного предела запросов (search_daily_limit)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_search_cache (
            query_hash TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            results_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_pair_verdicts (
            pair_hash TEXT PRIMARY KEY,
            title_a TEXT NOT NULL,
            title_b TEXT NOT NULL,
            decision TEXT NOT NULL,
            verdict_json TEXT NOT NULL,
            model TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Очередь спорных пар для проверки третьим источником истины (P17-C7):
    # k2 ≠ Gemini в теневом режиме, общий артикул при несовместимых моделях.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS disputed_product_pairs (
            pair_hash TEXT PRIMARY KEY,
            title_a TEXT NOT NULL,
            title_b TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_disputed_pairs_status ON disputed_product_pairs(status, created_at)")
    # Повтор спорной пары после временного отказа (P17C-04): число попыток, время следующей и причина последней
    _add_column(cursor, "disputed_product_pairs", "attempts", "INTEGER NOT NULL DEFAULT 0")
    _add_column(cursor, "disputed_product_pairs", "next_attempt_at", "TIMESTAMP")
    _add_column(cursor, "disputed_product_pairs", "last_reason", "TEXT")
    # Попытки поиска для суточного предела (P17C-03): каждая настоящая попытка резервируется до запроса —
    # и пустая выдача, и отказ поиска; попадание в кэш выдачи попытку не тратит
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_search_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_web_search_attempts_day ON web_search_attempts(day)")
    # Прежние идентичности товара после переноса ключа (P17CR-02): старый канонический ключ / товар V2 → новый.
    # Наблюдения MODEL и PRODUCT хранят цель в старом виде; предложение несёт свои прежние ключи и товары,
    # и наблюдение продолжает его узнавать. Записи не удаляются: цепочки A → B → C разрешаются целиком
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_aliases (
            kind TEXT NOT NULL,
            old_id TEXT NOT NULL,
            new_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (kind, old_id, new_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_identity_aliases_new ON identity_aliases(kind, new_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_web_search_cache_created ON web_search_cache(created_at)")
    # Названия, которые уже отправлялись в AI: неудачи не повторяются при каждом обходе
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS title_ai_attempts (
            title_hash TEXT PRIMARY KEY,
            attempted_at TIMESTAMP NOT NULL
        )
    """)
    # Теневой режим нормализации (P17-C5): сравнение ключей основной и теневой модели
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS normalize_shadow (
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            primary_key TEXT NOT NULL,
            shadow_key TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            example_title TEXT,
            PRIMARY KEY (day, kind, primary_key, shadow_key)
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
    # C01, C02, C05: Персистентный журнал очереди задач сохранения товаров в SQLite (Spool / Outbox).
    # Обеспечивает строгую транзакционность, гарантию непотери задач при kill -9/рестартах и fencing поколений.
    cursor.execute("""CREATE TABLE IF NOT EXISTS product_queue_spool (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT UNIQUE NOT NULL,
        shop_key TEXT,
        shop_name TEXT,
        lease_generation INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'committed', 'rejected', 'dead_letter'
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_error TEXT
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_spool_status ON product_queue_spool(status, id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_spool_shop ON product_queue_spool(shop_key, status)")
    # R03/R04/R05: владелец захвата задачи (процесс и одноразовый токен), срок захвата, стадия
    # и число записанных порций. Любая запись эффекта сверяет токен в своей транзакции.
    _add_column(cursor, "product_queue_spool", "claimed_by", "TEXT")
    _add_column(cursor, "product_queue_spool", "claim_token", "TEXT")
    _add_column(cursor, "product_queue_spool", "claim_expires_at", "REAL NOT NULL DEFAULT 0")
    _add_column(cursor, "product_queue_spool", "stage", "TEXT NOT NULL DEFAULT 'core'")
    _add_column(cursor, "product_queue_spool", "core_done", "INTEGER NOT NULL DEFAULT 0")
    _add_column(cursor, "product_queue_spool", "scan_id", "TEXT")
    _add_column(cursor, "product_queue_spool", "retired", "TEXT")  # G01: id снятых сверкой товаров
    # T01: размер порции — часть протокола задачи. core_done считается в порциях именно этого
    # размера; NULL — задача принята до 6.4.12, её порции по LEGACY_SPOOL_CHUNK_SIZE.
    _add_column(cursor, "product_queue_spool", "chunk_size", "INTEGER")
    # F01: метка наблюдения — момент приёма данных (time.time()). Запись старше уже сохранённой
    # (повтор давнего отказа после более свежего обхода) не перезаписывает товар и не снимает источник.
    _add_column(cursor, "products", "observed_seq", "REAL")
    # G03: товары, снятые из продажи, чью память наблюдений ещё нужно обновить (was_available = 0).
    # Строка пишется в той же транзакции, что снятие, и удаляется только после успешной проверки —
    # сбой или рестарт не теряют переход «снова в продаже».
    cursor.execute("""CREATE TABLE IF NOT EXISTS watch_refresh (
        product_id TEXT PRIMARY KEY,
        created_at REAL NOT NULL
    )""")
    # G05: фаза задания — 'retire' (снятие ещё не записано в память наблюдений) или 'current'
    # (записано; осталось оценить текущее состояние, это идемпотентно)
    _add_column(cursor, "watch_refresh", "phase", "TEXT NOT NULL DEFAULT 'retire'")
    # G05 (конкуренция): поколение перехода — каждое новое снятие увеличивает его; устаревший
    # обработчик не применит и не подтвердит чужое поколение
    _add_column(cursor, "watch_refresh", "gen", "INTEGER NOT NULL DEFAULT 0")
    cursor.execute("""CREATE TABLE IF NOT EXISTS source_observed (
        shop_key TEXT NOT NULL,
        source_url TEXT NOT NULL,
        observed_seq REAL NOT NULL,
        PRIMARY KEY (shop_key, source_url)
    )""")
    # Цены до записи каждой порции: повтор производных эффектов считает переход от них, а не
    # от уже обновлённой базы (R05). Строки живут до подтверждения задачи.
    cursor.execute("""CREATE TABLE IF NOT EXISTS product_queue_history (
        task_id TEXT NOT NULL,
        chunk_no INTEGER NOT NULL,
        history TEXT NOT NULL,
        PRIMARY KEY (task_id, chunk_no)
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_sources_shop_url ON product_sources(shop_key, source_url, active)")

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
    # Только при фактическом изменении индексируемых полей: обновление цены не переписывает индекс
    trig_row = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='products_au'"
    ).fetchone()
    if trig_row and "WHEN" not in (trig_row[0] or "").upper():
        cursor.execute("DROP TRIGGER IF EXISTS products_au")
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE OF id, title, shop, city, category ON products
        WHEN old.id IS NOT new.id OR old.title IS NOT new.title OR old.shop IS NOT new.shop
             OR old.city IS NOT new.city OR old.category IS NOT new.category
        BEGIN
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
    # Выборка задержек бакета одной JSON-строкой (6.5.2) вместо строки на значение в telemetry_http_samples
    _add_column(cursor, "telemetry_http_aggregates", "latency_samples", "TEXT")
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

    # Наблюдения пользователя (P07). Таблицы только добавляются — schema_version не повышается,
    # предыдущий образ стартует на этой базе и просто их не использует.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            target TEXT NOT NULL,
            title TEXT,
            condition TEXT NOT NULL,
            threshold INTEGER,
            city TEXT,
            shops TEXT NOT NULL DEFAULT '[]',
            mode TEXT NOT NULL DEFAULT 'instant',
            quiet_from TEXT NOT NULL DEFAULT '23:00',
            quiet_to TEXT NOT NULL DEFAULT '08:00',
            timezone TEXT NOT NULL DEFAULT 'Asia/Almaty',
            cooldown_hours INTEGER NOT NULL DEFAULT 6,
            repeat_mode INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_sent_at TEXT,
            UNIQUE(user_id, kind, target, condition, threshold)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watches_user ON watches(user_id, is_active)")
    # «Сработало» — не то же самое, что «выключено человеком»: сообщение по одноразовому наблюдению
    # ещё должно уйти, а новых срабатываний быть не должно (P07 G01)
    _add_column(cursor, "watches", "fired_at", "TEXT")
    # V2 (S2-E20): фильтрация по состояниям NEW/USED, продавцам и типам каналов
    _add_column(cursor, "watches", "item_conditions", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(cursor, "watches", "sellers", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(cursor, "watches", "channels", "TEXT NOT NULL DEFAULT '[]'")
    # Память наблюдения по каждому предложению: цена последнего отправленного сообщения не даёт
    # повторно уведомлять об одном и том же (возврат цены к прежнему значению — не новость)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watch_state (
            watch_id INTEGER NOT NULL,
            product_id TEXT NOT NULL,
            last_notified_price INTEGER,
            previous_price INTEGER,
            min_price INTEGER,
            was_available INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (watch_id, product_id)
        )
    """)
    # V2 (S2-E20): память по известным продавцам, каналам и офферам
    _add_column(cursor, "watch_state", "known_sellers", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(cursor, "watch_state", "known_channels", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(cursor, "watch_state", "known_offers", "TEXT NOT NULL DEFAULT '[]'")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watch_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            product_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            deliver_after REAL NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watch_events_watch ON watch_events(watch_id, status)")

    # Пульс фоновых работников (P14): «сайт отвечает» ещё не значит, что обходы идут.
    # Таблица добавляется, schema_version не меняется.
    # Результаты самопроверки копий (P15): копия, которую никто не открывал, ничего не гарантирует
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT NOT NULL,
            file TEXT,
            kind TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 0,
            detail TEXT,
            duration_sec REAL
        )
    """)
    # Проверка относится к конкретному файлу: успешная проверка вчерашней копии ничего не говорит
    # о сегодняшней, поэтому вместе с результатом хранятся размер и время изменения файла (P15 L01)
    _add_column(cursor, "backup_checks", "file_size", "INTEGER")
    # Доставка копии на другой диск или хост (P17E-02): копия рядом с базой ничего не скажет об отказе
    # NVMe, поэтому успех и сбой переноса — отдельные записи, которые видит мониторинг
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivered_at TEXT NOT NULL,
            outcome TEXT NOT NULL,
            target TEXT,
            file TEXT,
            size_bytes INTEGER,
            detail TEXT
        )
    """)
    _add_column(cursor, "backup_checks", "file_mtime", "REAL")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS component_heartbeats (
            component TEXT PRIMARY KEY,
            last_seen TEXT NOT NULL,
            note TEXT,
            beats INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Учёт AI по задачам и моделям (P08). Стоимость хранится только когда администратор задал цены,
    # иначе остаётся NULL — «не задано» честнее выдуманной цифры. Таблица добавляется, schema_version тот же.
    # Теневой отчёт сопоставления (P09): что новое правило фасовки запретило сравнивать и где оно
    # честно не знает. Хранятся только данные о товарах, без пользователей. schema_version не меняется.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
            day TEXT NOT NULL,
            tz TEXT NOT NULL DEFAULT 'UTC',
            computed_at TEXT NOT NULL,
            partial INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL,
            summary TEXT,
            summary_provider TEXT,
            PRIMARY KEY (day, tz)
        )
    """)
    # Обращения магазинов к тем, кто их парсит (см. shop_notices.py). Таблица добавочная:
    # schema_version остаётся прежним, откат на старый образ не требует восстановления базы.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shop_notices (
            shop_key TEXT PRIMARY KEY,
            host TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            notices TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_changed_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matching_pairs (
            pair_key TEXT PRIMARY KEY,
            left_title TEXT NOT NULL,
            right_title TEXT NOT NULL,
            seen INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            same INTEGER,
            confidence REAL,
            reason TEXT,
            provider TEXT,
            decided_at TEXT,
            mode TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matching_shadow (
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            reason TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            example_left TEXT,
            example_right TEXT,
            PRIMARY KEY (day, kind, reason)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage (
            day TEXT NOT NULL,
            task TEXT NOT NULL,
            audience TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            outcome TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL,
            latency_sum_ms REAL NOT NULL DEFAULT 0,
            cache_hits INTEGER NOT NULL DEFAULT 0,
            fallbacks INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            PRIMARY KEY (day, task, provider, model, outcome)
        )
    """)
    # Сколько вызовов в строке имеют известную стоимость: одна строка агрегата объединяет вызовы с ценой
    # и без неё, и без отдельного счётчика неполнота суммы была не видна (M06). У прежних строк колонка
    # остаётся NULL — это читается как «покрытие неизвестно», а не как «всё оценено».
    _add_column(cursor, "ai_usage", "priced_requests", "INTEGER")

    # Схема данных KZ Price Hunter 2.0 (Search Platform): sellers, channels, canonical_products, offers, offer_price_history.
    # Таблицы создаются аддитивно (IF NOT EXISTS), schema_version остаётся прежним (5).
    from repositories.schema_v2 import init_schema_v2
    init_schema_v2(cursor.connection)


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
        CREATE TRIGGER products_au AFTER UPDATE OF id, title, shop, city, category ON products
        WHEN old.id IS NOT new.id OR old.title IS NOT new.title OR old.shop IS NOT new.shop
             OR old.city IS NOT new.city OR old.category IS NOT new.category
        BEGIN
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
    """Консистентная копия через SQLite backup API в DATA_DIR/backups; проверяется quick_check.

    Результат отмечается пульсом компонента «резервные копии» (P14), чтобы было видно, когда копия
    делалась в последний раз и чем закончилась.
    """
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
        # Пульс компонента «резервные копии» (P14): видно, когда копия делалась и чем закончилась
        import environment
        environment.heartbeat(environment.BACKUP, f"{label}: {outcome}")
    except Exception:
        pass
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


def optimize_fts() -> None:
    """Лёгкое обслуживание FTS5-индексов (официальная команда 'optimize').

    В отличие от VACUUM не переписывает базу и не блокирует её надолго. Вызывается
    после массового обхода в консольном режиме и в ежедневном обслуживании веб-панели.
    Отсутствие таблицы (legacy-база или v2-контур ещё не создан) не ошибка.
    """
    try:
        with get_connection() as conn:
            for table in ("products_fts", "canonical_products_fts"):
                try:
                    conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
                except sqlite3.OperationalError:
                    continue  # этой FTS-таблицы ещё нет — остальные всё равно оптимизируем
            conn.execute("PRAGMA optimize")
    except Exception as e:
        print(f"[DB] Ошибка при оптимизации FTS5 (некритично): {e}")


def init_db():
    # Журнальный режим — первым делом: здесь, до запуска фоновых работников, база ещё
    # свободна, и это единственный момент, когда переход в WAL вообще возможен (C-DB-01).
    ensure_journal_mode()
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
    # В init_db остаётся только быстрая транзакционная сверка снятых offers и постановка заданий
    # в watch_refresh; сам разбор drain_watch_refresh идёт фоном после открытия HTTP-порта (P-6.4.12 / Задача 1).
    sync_retired_offers()
    cleanup_expired_sessions()
    reset_stale_running_scans()

def acquire_scheduler_lease(owner: str, ttl_seconds: float, name: str = "scan") -> bool:
    """Берёт или продлевает аренду; False — действующая аренда у другого процесса."""
    now = time.time()
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, factory=ClosingConnection)
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA synchronous = NORMAL")
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


def compare_and_set_metadata(version_key: str, expected: Optional[str],
                             pairs: Dict[str, str], attempts: int = 5) -> Optional[str]:
    """Записывает значения, только если версия состояния не изменилась. Возвращает новую версию или None.

    Так отказ одного перехода не может затереть состояние другого, успевшего пройти: он увидит чужую
    версию и ничего не тронет (M03). Транзакция BEGIN IMMEDIATE сериализует и параллельные процессы.
    """
    # База занята пишущим обходом — обычное дело на работающем сервисе: короткий повтор вместо отказа
    for attempt in range(max(1, attempts)):
        try:
            return _compare_and_set_once(version_key, expected, pairs)
        except sqlite3.OperationalError as e:
            if "lock" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if attempt == max(1, attempts) - 1:
                raise
            time.sleep(0.2 * (attempt + 1))
    return None


def _compare_and_set_once(version_key: str, expected: Optional[str],
                          pairs: Dict[str, str]) -> Optional[str]:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM schema_metadata WHERE name = ?", (version_key,)).fetchone()
        current = row[0] if row else None
        if (current or "") != (expected or ""):
            conn.rollback()
            return None
        version = str(int(current or 0) + 1)
        for name, value in pairs.items():
            conn.execute("INSERT OR REPLACE INTO schema_metadata (name, value) VALUES (?, ?)",
                         (name, str(value)))
        conn.execute("INSERT OR REPLACE INTO schema_metadata (name, value) VALUES (?, ?)",
                     (version_key, version))
        conn.commit()
        return version


def set_metadata_many(pairs: Dict[str, str]) -> None:
    """Несколько служебных значений одной транзакцией: состояние шага не должно записаться наполовину."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for name, value in pairs.items():
            conn.execute("INSERT OR REPLACE INTO schema_metadata (name, value) VALUES (?, ?)",
                         (name, str(value)))
        conn.commit()


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

def save_title_fields_batch(fields_by_title: Dict[str, Dict[str, Any]], key_version: str,
                            provider: Optional[str] = None, model: Optional[str] = None) -> None:
    """Поля товара от AI по стандарту ключа (P17-C): последняя запись по названию заменяет прежнюю."""
    rows = [(hashlib.sha256(t.strip().encode("utf-8")).hexdigest(), t.strip(),
             json.dumps(f, ensure_ascii=False, sort_keys=True)[:2000], key_version, provider, model)
            for t, f in (fields_by_title or {}).items() if t and isinstance(f, dict)]
    if not rows:
        return
    with get_connection() as conn:
        conn.execute("""INSERT INTO title_fields (title_hash, title, fields_json, key_version, provider, model)
                        SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]'),
                               json_extract(value, '$[3]'), json_extract(value, '$[4]'), json_extract(value, '$[5]')
                        FROM json_each(?) WHERE true
                        ON CONFLICT(title_hash) DO UPDATE SET fields_json = excluded.fields_json,
                            key_version = excluded.key_version, provider = excluded.provider, model = excluded.model,
                            created_at = CURRENT_TIMESTAMP""", (json.dumps(rows, ensure_ascii=False),))
        conn.commit()


def save_title_articles_batch(entries: List[Tuple[str, str, str]]) -> None:
    """Связи артикулов производителей с товарами и каноническими ключами (P17-C3 / link_by_article).

    entries — список (article, title_hash, canonical_key).
    """
    rows = [(str(art).strip().lower(), str(th).strip(), str(ck).strip())
            for art, th, ck in (entries or []) if art and th and ck]
    if not rows:
        return
    with get_connection() as conn:
        conn.execute("""INSERT INTO title_articles (article, title_hash, canonical_key)
                        SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]')
                        FROM json_each(?) WHERE true
                        ON CONFLICT(article, title_hash) DO UPDATE SET canonical_key = excluded.canonical_key,
                            created_at = CURRENT_TIMESTAMP""", (json.dumps(rows, ensure_ascii=False),))
        conn.commit()


def find_titles_by_articles(articles: Iterable[str]) -> List[Dict[str, Any]]:
    """Поиск существующих товаров с сохраненными полями по артикулам производителя.

    Возвращает [{'article': str, 'title_hash': str, 'canonical_key': str, 'title': str, 'fields': dict}].
    """
    cleaned = list({str(a).strip().lower() for a in (articles or []) if a and str(a).strip()})
    if not cleaned:
        return []
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT a.article, a.title_hash, a.canonical_key, f.title, f.fields_json
            FROM title_articles a
            JOIN title_fields f ON f.title_hash = a.title_hash
            WHERE a.article IN (SELECT value FROM json_each(?))
        """, (json.dumps(cleaned, ensure_ascii=False),))
        results = []
        for row in cursor.fetchall():
            try:
                fields = json.loads(row[4])
            except Exception:
                fields = {}
            results.append({
                "article": row[0],
                "title_hash": row[1],
                "canonical_key": row[2],
                "title": row[3],
                "fields": fields,
            })
        return results


def rebuild_title_articles_from_fields() -> int:
    """Пересобирает title_articles из title_fields и title_canonical_cache.

    Возвращает количество созданных связей.
    """
    import product_key
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT f.title_hash, f.title, f.fields_json, c.canonical_key
            FROM title_fields f
            JOIN title_canonical_cache c ON c.title_hash = f.title_hash
            WHERE c.canonical_key != ''
        """).fetchall()
        entries = []
        for th, title, fields_raw, ckey in rows:
            try:
                fields = json.loads(fields_raw)
            except Exception:
                continue
            if not isinstance(fields, dict):
                continue
            for art in product_key.article_tokens(fields):
                entries.append((art, th, ckey))
        if entries:
            conn.execute("DELETE FROM title_articles")
            conn.execute("""INSERT INTO title_articles (article, title_hash, canonical_key)
                            SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]')
                            FROM json_each(?) WHERE true
                            ON CONFLICT(article, title_hash) DO UPDATE SET canonical_key = excluded.canonical_key,
                                created_at = CURRENT_TIMESTAMP""", (json.dumps(entries, ensure_ascii=False),))
            conn.commit()
        return len(entries)


def _query_hash(query: str) -> str:
    return hashlib.sha256(" ".join(query.lower().split()).encode("utf-8")).hexdigest()


def get_web_search(query: str, max_age_days: int = 30) -> Optional[List[Dict[str, Any]]]:
    """Выдача поиска из кэша (P17-C7) или None, если запроса не было или запись старше max_age_days."""
    with get_connection() as conn:
        row = conn.execute("""SELECT results_json FROM web_search_cache WHERE query_hash = ?
                              AND created_at >= datetime('now', ?)""",
                           (_query_hash(query), f"-{int(max_age_days)} days")).fetchone()
    return json.loads(row[0]) if row else None


def save_web_search(query: str, results: List[Dict[str, Any]]) -> None:
    with get_connection() as conn:
        conn.execute("""INSERT INTO web_search_cache (query_hash, query, results_json) VALUES (?, ?, ?)
                        ON CONFLICT(query_hash) DO UPDATE SET results_json = excluded.results_json,
                            created_at = CURRENT_TIMESTAMP""",
                     (_query_hash(query), query[:200], json.dumps(results, ensure_ascii=False)[:20000]))
        conn.commit()


def web_searches_today() -> int:
    """Попыток поиска за текущие сутки UTC — для предела search_daily_limit (P17C-03)."""
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM web_search_attempts WHERE day = date('now')").fetchone()[0]


def reserve_web_search(query: str, daily_limit: int) -> bool:
    """Занять одну попытку поиска из суточного предела до настоящего запроса (P17C-03).

    Проверка и запись — в одной транзакции BEGIN IMMEDIATE: два исполнителя (воркер и скрипт, два процесса)
    не займут последнюю попытку оба. Предел 0 и меньше — поиск запрещён. Сутки — по UTC."""
    if int(daily_limit) <= 0:
        return False
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            used = conn.execute("SELECT COUNT(*) FROM web_search_attempts WHERE day = date('now')").fetchone()[0]
            if used >= int(daily_limit):
                conn.rollback()
                return False
            conn.execute("INSERT INTO web_search_attempts (day, query_hash) VALUES (date('now'), ?)",
                         (_query_hash(query),))
            conn.commit()
            return True
        except BaseException:
            conn.rollback()
            raise


def get_pair_verdict(pair_hash: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT verdict_json FROM product_pair_verdicts WHERE pair_hash = ?", (pair_hash,)).fetchone()
    return json.loads(row[0]) if row else None


def save_pair_verdict(pair_hash: str, title_a: str, title_b: str, verdict: Dict[str, Any],
                      model: Optional[str] = None) -> None:
    """Вердикт по паре (P17-C7): последний заменяет прежний."""
    with get_connection() as conn:
        conn.execute("""INSERT INTO product_pair_verdicts (pair_hash, title_a, title_b, decision, verdict_json, model)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(pair_hash) DO UPDATE SET decision = excluded.decision,
                            verdict_json = excluded.verdict_json, model = excluded.model, created_at = CURRENT_TIMESTAMP""",
                     (pair_hash, title_a[:500], title_b[:500], str(verdict.get("decision")),
                      json.dumps(verdict, ensure_ascii=False, sort_keys=True)[:4000], model))
        conn.commit()


def get_pair_decisions(pair_hashes: Iterable[str]) -> Dict[str, str]:
    """Решения третьего источника по парам {pair_hash: 'same'|'different'} — для запрета и разрешения связи
    по артикулу (P17C-01). «Неясно» не сохраняется и сюда не попадает."""
    hashes = list({h for h in (pair_hashes or []) if h})
    found: Dict[str, str] = {}
    if not hashes:
        return found
    with get_connection() as conn:
        for chunk in _chunks(hashes):
            for ph, decision in conn.execute(
                    "SELECT pair_hash, decision FROM product_pair_verdicts WHERE pair_hash IN (SELECT value FROM json_each(?))",
                    (json.dumps(chunk),)):
                found[ph] = decision
    return found


DISPUTED_MAX_ATTEMPTS = 8
DISPUTED_RETRY_MINUTES = 15            # временный отказ: 15 мин, 30, 60 … не дольше суток
DISPUTED_NO_EVIDENCE_DAYS = 7          # выдача была, но оснований для решения нет: проверить через неделю
DISPUTED_REOPEN_DAYS = 7               # «не решено» снова в очередь, если пара встретилась позже недели


def enqueue_disputed_pair(title_a: str, title_b: str, reason: str) -> bool:
    """Ставит пару в очередь спорных для проверки поиском (P17-C7).

    Возвращает True, если пара поставлена или снова открыта. Пара с окончательным вердиктом не ставится.
    Пара в статусе «не решено» (исчерпаны попытки) снова открывается, если встретилась позже
    DISPUTED_REOPEN_DAYS: временный сбой не исключает её из обработки навсегда (P17C-04)."""
    from product_verify import pair_hash, UNKNOWN
    first, second = sorted((title_a.strip(), title_b.strip()))
    if not first or not second or first == second:
        return False
    ph = pair_hash(first, second)
    existing_verdict = get_pair_verdict(ph)
    if existing_verdict and existing_verdict.get("decision") != UNKNOWN:
        return False
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO disputed_product_pairs (pair_hash, title_a, title_b, reason, status)
            VALUES (?, ?, ?, ?, 'pending')
            ON CONFLICT(pair_hash) DO UPDATE SET status = 'pending', attempts = 0, next_attempt_at = NULL,
                reason = excluded.reason, updated_at = CURRENT_TIMESTAMP
            WHERE disputed_product_pairs.status = 'unresolved'
              AND disputed_product_pairs.updated_at < datetime('now', ?)
        """, (ph, first[:500], second[:500], reason[:100], f"-{DISPUTED_REOPEN_DAYS} days"))
        conn.commit()
        return cur.rowcount > 0


def get_pending_disputed_pairs(limit: int = 10) -> List[Dict[str, Any]]:
    """Спорные пары, которые пора проверять: ожидающие и отложенные, чьё время повтора наступило."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT pair_hash, title_a, title_b, reason, created_at, attempts
            FROM disputed_product_pairs
            WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now'))
            ORDER BY COALESCE(next_attempt_at, created_at) ASC, created_at ASC
            LIMIT ?
        """, (int(limit),)).fetchall()
        return [{"pair_hash": r[0], "title_a": r[1], "title_b": r[2], "reason": r[3], "created_at": r[4],
                 "attempts": r[5]} for r in rows]


def update_disputed_pair_status(pair_hash: str, status: str, reason: Optional[str] = None) -> None:
    """Окончательный статус спорной пары ('resolved', 'skipped', 'unresolved')."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE disputed_product_pairs
            SET status = ?, last_reason = COALESCE(?, last_reason), updated_at = CURRENT_TIMESTAMP
            WHERE pair_hash = ?
        """, (status, reason, pair_hash))
        conn.commit()


def defer_disputed_pair(pair_hash: str, transient: bool, reason: str = "") -> str:
    """Отложить пару без решения (P17C-04) и вернуть её новый статус.

    transient — временный отказ (поиск недоступен, предел суток, модель выключена или не ответила): повтор через
    15 мин, 30, 60 … не дольше суток. Иначе выдача была, но оснований нет: повтор через неделю. После
    DISPUTED_MAX_ATTEMPTS попыток — «не решено» до новой встречи пары (enqueue_disputed_pair)."""
    with get_connection() as conn:
        row = conn.execute("SELECT attempts FROM disputed_product_pairs WHERE pair_hash = ?", (pair_hash,)).fetchone()
        attempts = (row[0] if row else 0) + 1
        if attempts >= DISPUTED_MAX_ATTEMPTS:
            status, delay = "unresolved", None
        elif transient:
            status, delay = "pending", f"+{min(24 * 60, DISPUTED_RETRY_MINUTES * 2 ** (attempts - 1))} minutes"
        else:
            status, delay = "pending", f"+{DISPUTED_NO_EVIDENCE_DAYS} days"
        conn.execute("""
            UPDATE disputed_product_pairs
            SET status = ?, attempts = ?, last_reason = ?,
                next_attempt_at = CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END,
                updated_at = CURRENT_TIMESTAMP
            WHERE pair_hash = ?
        """, (status, attempts, reason[:200], delay, delay, pair_hash))
        conn.commit()
    return status


def get_title_fields_for_titles(titles: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Извлекает сохранённые fields_json из title_fields для списка названий."""
    unique = list({t.strip() for t in titles if t and t.strip()})
    if not unique:
        return {}
    hashes = {hashlib.sha256(t.encode("utf-8")).hexdigest(): t for t in unique}
    results = {}
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT title_hash, fields_json
            FROM title_fields
            WHERE title_hash IN (SELECT value FROM json_each(?))
        """, (json.dumps(list(hashes), ensure_ascii=False),))
        for row in cursor.fetchall():
            orig_title = hashes.get(row[0])
            if orig_title:
                try:
                    results[orig_title] = json.loads(row[1])
                except Exception:
                    pass
    return results


def rekey_titles_conn(conn, new_keys: Dict[str, str]) -> Dict[str, int]:
    """Перенос идентичности товара на новый канонический ключ — legacy и V2 вместе (P17C-02).

    Ключ в products не единственное место идентичности: поиск V2, витрины и наблюдения читают offers,
    а предложение принадлежит каноническому товару prod_<sha(ключ)>. Поэтому по каждому названию:
    кэш ключей, title_articles, products.canonical_key; канонический товар под новым ключом (создаётся из
    данных товара, если его ещё нет); offers.product_id предложений этих товаров; product_id точек истории
    цен этих предложений — история идёт за предложением и не теряется. Прежний канонический товар не
    удаляется: на нём могут остаться предложения других названий; если он отдавал название переехавшему
    предложению, право названия освобождается для оставшихся.

    Выполняется в транзакции вызывающего (без commit): перенос всей порции применяется или откатывается целиком.
    """
    from domain.adapter import canonical_product_id
    rows = [(t.strip(), k) for t, k in (new_keys or {}).items() if t and t.strip() and k]
    stats = {"titles": len(rows), "products": 0, "offers": 0, "history": 0}
    if not rows:
        return stats
    conn.execute("""INSERT INTO title_canonical_cache (title_hash, title, canonical_key)
                    SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]')
                    FROM json_each(?) WHERE true
                    ON CONFLICT(title_hash) DO UPDATE SET canonical_key = excluded.canonical_key""",
                 (json.dumps([(_title_hash(t), t, k) for t, k in rows], ensure_ascii=False),))
    conn.executemany("UPDATE title_articles SET canonical_key = ? WHERE title_hash = ?",
                     [(k, _title_hash(t)) for t, k in rows])
    has_v2 = _has_table(conn, "offers") and _has_table(conn, "canonical_products")
    history_cols = _columns(conn, "offer_price_history") if _has_table(conn, "offer_price_history") else set()
    aliases = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for title, key in rows:
        cur = conn.execute("SELECT * FROM products WHERE title = ?", (title,))
        names = [d[0] for d in cur.description]
        products = [dict(zip(names, r)) for r in cur.fetchall()]       # и без row_factory (скрипт миграции)
        if not products:
            continue
        conn.execute("UPDATE products SET canonical_key = ? WHERE title = ?", (key, title))
        stats["products"] += len(products)
        aliases += [("key", p["canonical_key"], key) for p in products if p.get("canonical_key") and p["canonical_key"] != key]
        if not has_v2:
            continue
        # Канонический товар с этим ключом уже может быть (ключ уникален) — тогда предложение идёт к нему
        existing_cp = conn.execute("SELECT id FROM canonical_products WHERE canonical_key = ?", (key,)).fetchone()
        new_pid = existing_cp[0] if existing_cp else canonical_product_id(key)
        for product in products:
            offer_id = f"off_{product['id']}"
            current = conn.execute("SELECT product_id FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if current is None or current[0] == new_pid:
                continue                            # предложения нет (цена вне диапазона) или оно уже на месте
            old_pid = current[0]
            old_cp = conn.execute("SELECT canonical_key FROM canonical_products WHERE id = ?", (old_pid,)).fetchone()
            aliases.append(("product", old_pid, new_pid))
            if old_cp and old_cp[0] and old_cp[0] != key:
                aliases.append(("key", old_cp[0], key))
            _move_watch_state(conn, old_pid, new_pid)
            conn.execute("""INSERT INTO canonical_products (id, canonical_key, title, category, description,
                                image_url, title_offer_id, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
                         (new_pid, key, product["title"], product.get("category") or "", product.get("description") or "",
                          product.get("image_url") or "", offer_id, now, now))
            conn.execute("UPDATE offers SET product_id = ?, updated_at = ? WHERE id = ?", (new_pid, now, offer_id))
            conn.execute("UPDATE canonical_products SET title_offer_id = NULL WHERE id = ? AND title_offer_id = ?",
                         (old_pid, offer_id))
            stats["offers"] += 1
            if "product_id" in history_cols:
                cur = conn.execute("UPDATE offer_price_history SET product_id = ? WHERE offer_id = ? AND product_id = ?",
                                   (new_pid, offer_id, old_pid))
                stats["history"] += cur.rowcount
    if aliases and _has_table(conn, "identity_aliases"):
        conn.executemany("INSERT OR IGNORE INTO identity_aliases (kind, old_id, new_id) VALUES (?, ?, ?)",
                         list(dict.fromkeys(aliases)))
    stats["aliases"] = len(set(aliases))
    return stats


def _move_watch_state(conn, old_pid: str, new_pid: str) -> None:
    """Память межпродавцовых наблюдений ведётся по каноническому товару (_watch_scope_id): при переносе
    предложения на новый товар она копируется на него с объединением известных продавцов, каналов и
    предложений. Иначе пустая память на новом товаре приняла бы всех известных продавцов за новых и разослала
    ложные «новый продавец / новое предложение» (P17CR-02). Прежняя память остаётся для оставшихся предложений."""
    if not _has_table(conn, "watch_state"):
        return
    cur = conn.execute("SELECT * FROM watch_state WHERE product_id = ? OR product_id LIKE ?",
                       (old_pid, old_pid.replace("%", "") + ":%"))
    names = [d[0] for d in cur.description]
    for row in [dict(zip(names, r)) for r in cur.fetchall()]:
        scope = new_pid + row["product_id"][len(old_pid):]
        found = conn.execute("SELECT * FROM watch_state WHERE watch_id = ? AND product_id = ?",
                             (row["watch_id"], scope))
        target = found.fetchone()
        merged = dict(row, product_id=scope)
        if target:
            target = dict(zip([d[0] for d in found.description], target))
            merged = dict(target)
            for col in ("known_sellers", "known_channels", "known_offers"):
                if col in row:
                    try:
                        union = set(json.loads(row[col] or "[]")) | set(json.loads(target[col] or "[]"))
                    except (TypeError, ValueError):
                        continue
                    merged[col] = json.dumps(sorted(union), ensure_ascii=False)
            prices = [v for v in (row.get("min_price"), target.get("min_price")) if v is not None]
            merged["min_price"] = min(prices) if prices else None
        cols = list(merged)
        conn.execute(f"INSERT OR REPLACE INTO watch_state ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [merged[c] for c in cols])


def _former_identities(conn, kind: str, values: Iterable[str]) -> Dict[str, List[str]]:
    """{текущий id: [все прежние id]} по identity_aliases, с цепочками A → B → C (P17CR-02)."""
    current = {v for v in values if v}
    if not current or not _has_table(conn, "identity_aliases"):
        return {}
    olds_of: Dict[str, set] = {}
    frontier, seen = set(current), set(current)
    for _ in range(20):                               # глубина цепочек переносов ограничена
        if not frontier:
            break
        rows = conn.execute("SELECT old_id, new_id FROM identity_aliases WHERE kind = ? AND new_id IN "
                            "(SELECT value FROM json_each(?))", (kind, json.dumps(sorted(frontier)))).fetchall()
        frontier = set()
        for old, new in rows:
            olds_of.setdefault(new, set()).add(old)
            if old not in seen:
                seen.add(old)
                frontier.add(old)
    out: Dict[str, List[str]] = {}
    for value in current:
        acc, stack = set(), [value]
        while stack:
            for old in olds_of.get(stack.pop(), ()):
                if old not in acc and old != value:
                    acc.add(old)
                    stack.append(old)
        if acc:
            out[value] = sorted(acc)
    return out


def titles_with_canonical_key(key: str, limit: int = 1000) -> List[str]:
    """Названия из кэша ключей с этим ключом — участники группы при объединении (P17-C7)."""
    with get_connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT title FROM title_canonical_cache WHERE canonical_key = ? ORDER BY title LIMIT ?", (key, int(limit)))]


def rekey_titles(new_keys: Dict[str, str]) -> Dict[str, int]:
    """rekey_titles_conn в своей транзакции."""
    with get_connection() as conn:
        try:
            stats = rekey_titles_conn(conn, new_keys)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return stats


def unify_canonical_keys(title_a: str, title_b: str, common_key: str) -> Dict[str, int]:
    """Объединяет два названия под единый ключ при вердикте same из третьего источника истины.

    Идентичность переносится и в V2 (offers, канонический товар, история цен) — P17C-02."""
    return rekey_titles({title_a: common_key, title_b: common_key})


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
    for i in range(0, len(pairs), 200):
        chunk = pairs[i:i + 200]
        with get_connection() as conn:
            conn.executemany("UPDATE products SET canonical_key = ? WHERE id = ?", chunk)
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
    observed_seq = time.time()  # прямая запись — свежее наблюдение (F01)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_price, first_seen_price, min_price, max_price, old_price_on_site FROM products WHERE id = ?", (pid,))
        existing = cursor.fetchone()
        _record_observation(cursor, pid, existing, current_price, old_on_site, now)

        if existing is None:
            cursor.execute("""
                INSERT INTO products (id, shop, city, title, category, url, image_url, description, current_price, old_price_on_site, first_seen_price, min_price, max_price, canonical_key, created_at, updated_at, observed_seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pid, shop, city, title, category, url, image_url, description, current_price, old_on_site, current_price, current_price, current_price, canonical_key, now, now, observed_seq))
            res = {
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
                    current_price = ?, old_price_on_site = ?, min_price = ?, max_price = ?, canonical_key = COALESCE(?, canonical_key), updated_at = ?,
                    observed_seq = COALESCE(?, observed_seq)
                WHERE id = ?
            """, (shop, city, title, category, url, image_url, description, description, current_price, old_on_site, min_price, max_price, canonical_key, now, observed_seq, pid))
            res = {
                "is_new": False,
                "old_price": old_price,
                "first_seen_price": first_seen_price,
                "current_price": current_price,
                "price_changed": price_changed
            }

        if os.environ.get("DUAL_WRITE_V2", "1") != "0":
            _DUAL_WRITE_STATS["total"] += 1
            try:
                from domain.adapter import sync_legacy_product_to_v2
                sync_legacy_product_to_v2(conn, p, now, canonical_key=canonical_key)
            except Exception as e:
                _record_dual_write_error(pid, e)

        conn.commit()
        return res

def get_price_history_batch(product_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Возвращает сохраненную историю цен для набора товаров (до пакетной перезаписи)."""
    ids = [str(pid) for pid in product_ids]
    history: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return history

    with get_connection() as conn:
        return _price_history_conn(conn, ids)


def _price_history_conn(conn, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Прежние цены товаров порции — одним чтением (json_group_array отдаёт одну строку).

    Чтение идёт под блокировкой записи: построчная выборка отпускала бы GIL на каждой строке (6.5.1).
    """
    history: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return history
    row = conn.execute(
        "SELECT json_group_array(json_array(id, current_price, first_seen_price, is_active)) "
        "FROM products WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(ids), ensure_ascii=False),),
    ).fetchone()
    for pid, current_price, first_seen_price, is_active in json.loads(row[0] or "[]"):
        history[pid] = {
            "old_price": current_price,
            "first_seen_price": first_seen_price,
            # снят прежней сверкой: возврат — повод проверить наблюдения «снова в продаже» (G01)
            "was_active": is_active,
        }
    return history


def _prepare_products_chunk(chunk: List[Dict[str, Any]]) -> List[Optional[Dict[str, Any]]]:
    """Значения записи порции без обращения к базе; None — товар с недопустимой ценой.

    Вызывается до захвата блокировки записи: разбор названий, ключей и продавцов — чистый Python,
    а под блокировкой он ждал бы GIL наравне с потоками обхода (6.5.1). Ошибка подготовки v2
    не роняет порцию: она сохраняется и учитывается как сбой двойной записи этого товара.
    """
    from model_matching import extract_canonical_key

    dual = os.environ.get("DUAL_WRITE_V2", "1") != "0"
    prepared: List[Optional[Dict[str, Any]]] = []
    for p in chunk:
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
            prepared.append(None)
            continue
        canonical_key = p.get("canonical_key") or extract_canonical_key(title)
        item: Dict[str, Any] = {
            "id": pid, "shop": shop, "city": city, "title": title, "category": category, "url": url,
            "image_url": image_url, "description": description, "price": current_price,
            "old_on_site": site_old_price(p.get("old_price_on_site"), current_price),
            "canonical_key": canonical_key, "dual": dual, "v2": None, "v2_error": None,
        }
        if dual:
            try:
                from domain.adapter import prepare_legacy_offer_v2
                item["v2"] = prepare_legacy_offer_v2(p, canonical_key=canonical_key)
            except Exception as e:
                item["v2_error"] = e
        prepared.append(item)
    return prepared


def _save_products_chunk(conn, chunk: List[Dict[str, Any]], now: str, observed_seq: Optional[float] = None,
                         prepared: Optional[List[Optional[Dict[str, Any]]]] = None) -> int:
    """Записывает порцию товаров в открытой транзакции conn, не фиксируя её (R04/R05).

    observed_seq — когда данные получены (момент приёма задачи). Товар, у которого уже сохранено
    более свежее наблюдение, не трогается (F01); None — запись без метки, как прежде.

    Запись наборная (6.5.1): прежние значения читаются одним запросом, решения по каждому товару
    принимаются в Python в порядке порции (повтор товара видит предыдущую запись), а наблюдения,
    новые товары и обновления пишутся по одному оператору из JSON-параметра. Прежняя запись делала
    ~6 вызовов SQLite на товар; каждый отпускает GIL, и под нагрузкой обхода удержание блокировки
    записи порцией доходило до минуты. observed_seq передаётся параметром, а не через JSON: дробные
    числа в JSON у SQLite 3.46 теряют знаки, а от точной метки зависят F01 и spool_superseded.
    prepared — результат _prepare_products_chunk, посчитанный до блокировки.
    """
    if prepared is None:
        prepared = _prepare_products_chunk(chunk)
    items = [it for it in prepared if it is not None]
    if not items:
        return 0

    ids = list(dict.fromkeys(it["id"] for it in items))
    row = conn.execute(
        "SELECT json_group_array(json_array(id, current_price, min_price, max_price, old_price_on_site, "
        "?1 IS NOT NULL AND observed_seq IS NOT NULL AND observed_seq > ?1)) "
        "FROM products WHERE id IN (SELECT value FROM json_each(?2))",
        (observed_seq, json.dumps(ids, ensure_ascii=False)),
    ).fetchone()
    state: Dict[str, Dict[str, Any]] = {}
    newer: set = set()
    for pid, price, min_price, max_price, old_on_site, is_newer in json.loads(row[0] or "[]"):
        state[pid] = {"price": price, "old": old_on_site, "min": min_price, "max": max_price}
        if is_newer:
            newer.add(pid)  # F01: сохранено более свежее наблюдение — старые данные его не откатывают

    observations: List[List[Any]] = []
    rows: Dict[str, Dict[str, Any]] = {}
    v2_items: List[Dict[str, Any]] = []
    updated_count = 0
    for it in items:
        pid = it["id"]
        if pid in newer:
            continue
        existing = state.get(pid)
        price, old_on_site = it["price"], it["old_on_site"]
        # Наблюдение пишется для нового предложения и при изменении цены или зачёркнутой цены
        if existing is None or existing["price"] != price or (existing["old"] or 0) != old_on_site:
            observations.append([pid, price, old_on_site])
        fields = {k: it[k] for k in ("shop", "city", "title", "category", "url", "image_url")}
        if existing is None:
            out = {"insert": True, **fields, "description": it["description"], "price": price,
                   "old": old_on_site, "first_seen": price, "min": price, "max": price,
                   "canonical_key": it["canonical_key"]}
            rows[pid] = out
        else:
            # Повтор товара в порции складывается в одну строку с тем же итогом, что две записи подряд:
            # описание и ключ — последние непустые, прежние сохраняются, если новых нет
            out = rows.setdefault(pid, {"insert": False, "description": "", "canonical_key": None})
            out.update(fields, price=price, old=old_on_site,
                       min=min(existing["min"], price), max=max(existing["max"], price))
            if it["description"] != "":
                out["description"] = it["description"]
            if it["canonical_key"] is not None:
                out["canonical_key"] = it["canonical_key"]
        state[pid] = {"price": price, "old": old_on_site, "min": out["min"], "max": out["max"]}
        if it["dual"]:
            _DUAL_WRITE_STATS["total"] += 1
            if it["v2_error"] is not None:
                _record_dual_write_error(pid, it["v2_error"])
            elif it["v2"] is not None:
                v2_items.append(it["v2"])
        updated_count += 1

    if observations:
        conn.execute(
            "INSERT INTO price_observations (product_id, price, old_price_on_site, observed_at) "
            "SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]'), ?1 "
            "FROM json_each(?2)",
            (now, json.dumps(observations, ensure_ascii=False)),
        )
    inserts = [{"id": pid, **r} for pid, r in rows.items() if r["insert"]]
    if inserts:
        conn.execute("""
            INSERT INTO products (id, shop, city, title, category, url, image_url, description, current_price, old_price_on_site, first_seen_price, min_price, max_price, canonical_key, created_at, updated_at, observed_seq)
            SELECT json_extract(value, '$.id'), json_extract(value, '$.shop'), json_extract(value, '$.city'),
                   json_extract(value, '$.title'), json_extract(value, '$.category'), json_extract(value, '$.url'),
                   json_extract(value, '$.image_url'), json_extract(value, '$.description'),
                   json_extract(value, '$.price'), json_extract(value, '$.old'), json_extract(value, '$.first_seen'),
                   json_extract(value, '$.min'), json_extract(value, '$.max'), json_extract(value, '$.canonical_key'),
                   ?1, ?1, ?2
            FROM json_each(?3)
        """, (now, observed_seq, json.dumps(inserts, ensure_ascii=False)))
    updates = [{"id": pid, **r} for pid, r in rows.items() if not r["insert"]]
    if updates:
        conn.execute("""
            UPDATE products
            SET is_active = 1, shop = j.shop, city = j.city, title = j.title, category = j.category,
                url = j.url, image_url = j.image_url,
                description = CASE WHEN j.description != '' THEN j.description ELSE products.description END,
                current_price = j.price, old_price_on_site = j.old, min_price = j.min, max_price = j.max,
                canonical_key = COALESCE(j.canonical_key, products.canonical_key), updated_at = ?1,
                observed_seq = COALESCE(?2, products.observed_seq)
            FROM (SELECT json_extract(value, '$.id') AS id, json_extract(value, '$.shop') AS shop,
                         json_extract(value, '$.city') AS city, json_extract(value, '$.title') AS title,
                         json_extract(value, '$.category') AS category, json_extract(value, '$.url') AS url,
                         json_extract(value, '$.image_url') AS image_url,
                         json_extract(value, '$.description') AS description,
                         json_extract(value, '$.price') AS price, json_extract(value, '$.old') AS old,
                         json_extract(value, '$.min') AS min, json_extract(value, '$.max') AS max,
                         json_extract(value, '$.canonical_key') AS canonical_key
                  FROM json_each(?3)) AS j
            WHERE products.id = j.id
        """, (now, observed_seq, json.dumps(updates, ensure_ascii=False)))

    if v2_items:
        _write_products_chunk_v2(conn, v2_items, now)
    return updated_count


def _write_products_chunk_v2(conn, v2_items: List[Dict[str, Any]], now: str) -> None:
    """Двойная запись порции в v2. Сбой наборной записи не теряет остальные товары: она
    откатывается целиком (write_legacy_offers_v2 атомарна) и повторяется по одному товару, —
    сбой одного товара откатывает только его записи и учитывается как сбой его двойной записи,
    остальные записываются (V651-01)."""
    from domain.adapter import write_legacy_offers_v2

    if not conn.in_transaction:
        conn.execute("BEGIN")  # RELEASE внешней точки сохранения зафиксировал бы транзакцию
    try:
        write_legacy_offers_v2(conn, v2_items, now, seen_sellers=set())
        return
    except Exception:
        pass
    seen_sellers: set = set()
    for item in v2_items:
        try:
            write_legacy_offers_v2(conn, [item], now, seen_sellers=seen_sellers)
        except Exception as e:
            _record_dual_write_error(item["external_sku"], e)


def save_or_update_products_batch(products: List[Dict[str, Any]]) -> int:
    """Массовая вставка/обновление товаров порциями в транзакциях (для YML выгрузок и больших каталогов)."""
    if not products:
        return 0

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    observed_seq = time.time()
    updated_count = 0
    chunk_size = 100

    for i in range(0, len(products), chunk_size):
        chunk = products[i:i + chunk_size]
        prepared = _prepare_products_chunk(chunk)
        for attempt in range(3):
            try:
                with get_connection() as conn:
                    updated_count += _save_products_chunk(conn, chunk, now, observed_seq, prepared=prepared)
                    conn.commit()
                break
            except sqlite3.OperationalError as e:
                if attempt == 2 or "locked" not in str(e).lower():
                    raise
                time.sleep(0.5 * (attempt + 1))

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

    import catalog_quality
    valid_competitors = []
    for r in rows:
        ok, reason = catalog_quality.comparable(title, r["title"])
        if not ok:
            # Разная фасовка — разные товары: сравнение 1 л с 1,5 л показало бы выгоду, которой нет (P09).
            # В теневой отчёт попадают те пары, которые без этого правила считались бы одним товаром.
            if same_model(title, r["title"]) or (catalog_quality.identity_tokens(title)
                                                 == catalog_quality.identity_tokens(r["title"])):
                record_matching_shadow("blocked", reason, title, r["title"])
            continue
        if not catalog_quality.same_product(title, r["title"]):
            unsure, unsure_reason = catalog_quality.uncertain(title, r["title"])
            if unsure and catalog_quality.identity_tokens(title) == catalog_quality.identity_tokens(r["title"]):
                # Слова совпали, но фасовку указал только один магазин: сравнивать вслепую не будем,
                # случай записывается для разбора (P09)
                record_matching_shadow("uncertain", unsure_reason, title, r["title"])
                if not _ai_allows_comparison(title, r["title"]):
                    continue
                # Модель разобрала этот случай и уверена — сравнение разрешено (P09, AI-часть)
                record_matching_shadow("ai_allowed", unsure_reason, title, r["title"])
            else:
                continue
        if is_junk_accessory(r["title"], r["category"] or "") or is_used_goods(r["title"], r["category"] or "", r["url"]):
            continue
        valid_competitors.append(dict(r))
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
    история адресованных ему уведомлений, его наблюдения и их срабатывания (P07)."""
    user = get_user(user_id)
    if not user:
        return None
    watches = list_watches(user_id)
    events = list(iter_watch_events(user_id))  # полностью: лимит списка интерфейса здесь не действует (F03)
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
        # Наблюдения принадлежат человеку и входят в его выгрузку (P07)
        "watches": watches,
        "watch_events": events,
    }


def delete_user_account(user_id: int) -> Dict[str, int]:
    """Удаляет пользователя, его сессии и адресованные ему уведомления одной транзакцией (M12).
    Общие алерты и каталог не удаляются: они не принадлежат пользователю."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Наблюдения принадлежат человеку, поэтому уходят вместе с ним (P07)
        conn.execute("DELETE FROM watch_state WHERE watch_id IN (SELECT id FROM watches WHERE user_id = ?)",
                     (int(user_id),))
        conn.execute("DELETE FROM watch_events WHERE watch_id IN (SELECT id FROM watches WHERE user_id = ?)",
                     (int(user_id),))
        counts = {
            "sessions": conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),)).rowcount,
            "notifications": conn.execute("DELETE FROM notification_outbox WHERE user_id = ?", (int(user_id),)).rowcount,
            "watches": conn.execute("DELETE FROM watches WHERE user_id = ?", (int(user_id),)).rowcount,
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

def get_shop_notices() -> Dict[str, Dict[str, Any]]:
    """Известные обращения магазинов к парсерам, по ключу магазина (см. shop_notices.py)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM shop_notices").fetchall()
    return {row["shop_key"]: dict(row) for row in rows}


def save_shop_notice(shop_key: str, host: str, fingerprint: str, notices: str,
                     checked_at: str) -> bool:
    """Сохраняет обращение магазина. Возвращает True, если текст новый или изменился.

    Дата первого появления не переписывается: важно знать, когда магазин заговорил впервые.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT fingerprint, first_seen_at FROM shop_notices WHERE shop_key = ?",
                           (shop_key,)).fetchone()
        changed = row is None or row["fingerprint"] != fingerprint
        first_seen = row["first_seen_at"] if row else checked_at
        last_changed = checked_at if changed else None
        if row is None:
            conn.execute("""INSERT INTO shop_notices
                            (shop_key, host, fingerprint, notices, first_seen_at,
                             last_changed_at, last_checked_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (shop_key, host, fingerprint, notices, first_seen, checked_at, checked_at))
        elif changed:
            conn.execute("""UPDATE shop_notices SET host = ?, fingerprint = ?, notices = ?,
                            last_changed_at = ?, last_checked_at = ? WHERE shop_key = ?""",
                         (host, fingerprint, notices, checked_at, checked_at, shop_key))
        else:
            conn.execute("UPDATE shop_notices SET last_checked_at = ? WHERE shop_key = ?",
                         (checked_at, shop_key))
        conn.commit()
    return changed


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


# Сводка забирает ВСЕ готовые срабатывания наблюдения, а не первые двадцать: иначе пакет из 25 событий
# разваливался на два письма подряд (M10). Верхняя граница остаётся защитой от бесконечного письма.
MAX_DIGEST_BATCH = 500


def claim_watch_digest(watch_id: int, user_id: int, exclude_id: int,
                       limit: int = MAX_DIGEST_BATCH) -> List[Dict[str, Any]]:
    """Забирает остальные готовые задания того же наблюдения: они уйдут одним сообщением, а не пачкой.

    Берутся только задания, которым уже пора, и только этого человека и этого наблюдения. Каждое
    забирается так же, как обычное — с увеличением попытки, чтобы при сбое доставки оно не потерялось
    и не ушло дважды.

    Берутся все готовые срабатывания до предела MAX_DIGEST_BATCH: за один обход наблюдение создаёт не
    больше MAX_EVENTS_PER_WATCH_PER_RUN событий, поэтому обычный пакет умещается в одно письмо (M10).
    Если срабатываний окажется больше предела, остаток уйдёт следующим письмом — об этом говорит само
    письмо, а не молчание.
    """
    now = time.time()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""SELECT * FROM notification_outbox
            WHERE status = 'pending' AND user_id = ? AND id <> ? AND next_attempt_at <= ?
              AND json_extract(payload, '$.kind') = 'watch'
              AND json_extract(payload, '$.watch_id') = ?
            ORDER BY id LIMIT ?""", (user_id, exclude_id, now, watch_id, limit)).fetchall()
        for row in rows:
            conn.execute("UPDATE notification_outbox SET attempts = attempts + 1, next_attempt_at = ? "
                         "WHERE id = ?", (now + 120, row["id"]))
        conn.commit()
    return [dict(r) for r in rows]


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





# ---------------------------------------------------------------------------
# Наблюдения пользователя (P07): хранение, оценка срабатываний и постановка в очередь доставки.
# Правила (что считается срабатыванием, тихие часы, пауза) живут в watches.py.
# ---------------------------------------------------------------------------

def _watch_row(row) -> Dict[str, Any]:
    item = dict(row)
    for col in ("shops", "item_conditions", "sellers", "channels"):
        try:
            item[col] = json.loads(item.get(col) or "[]")
        except (ValueError, TypeError):
            item[col] = []
    item["repeat"] = bool(item.pop("repeat_mode", 1))
    item["fired"] = bool(item.get("fired_at"))
    return item


def list_watches(user_id: int, only_active: bool = False) -> List[Dict[str, Any]]:
    import watches as w
    query = "SELECT * FROM watches WHERE user_id = ?"
    if only_active:
        query += " AND is_active = 1"
    with get_connection() as conn:
        rows = [_watch_row(r) for r in conn.execute(query + " ORDER BY id DESC", (int(user_id),))]
    for row in rows:
        row["description"] = w.describe(row)
    return rows


def create_watch(user_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Создаёт наблюдение пользователя; повторное создание того же возвращает существующее."""
    import watches as w
    clean = w.normalize(data)
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM watches WHERE user_id = ?", (int(user_id),)).fetchone()[0]
        if count >= w.MAX_WATCHES_PER_USER:
            raise ValueError(f"Больше {w.MAX_WATCHES_PER_USER} наблюдений не поддерживается — "
                             f"удалите ненужные")
        existing = conn.execute("""SELECT * FROM watches WHERE user_id = ? AND kind = ? AND target = ?
                                   AND condition = ? AND threshold IS ?""",
                                (int(user_id), clean["kind"], clean["target"], clean["condition"],
                                 clean["threshold"])).fetchone()
        if existing:
            conn.execute("""UPDATE watches SET is_active = 1,
                                   shops = ?, item_conditions = ?, sellers = ?, channels = ?
                            WHERE id = ?""",
                         (json.dumps(clean["shops"], ensure_ascii=False),
                          json.dumps(clean["item_conditions"], ensure_ascii=False),
                          json.dumps(clean["sellers"], ensure_ascii=False),
                          json.dumps(clean["channels"], ensure_ascii=False),
                          existing["id"]))
            conn.commit()
            return _watch_row(conn.execute("SELECT * FROM watches WHERE id = ?", (existing["id"],)).fetchone())
        conn.execute("""
            INSERT INTO watches (user_id, kind, target, title, condition, threshold, city, shops,
                                 item_conditions, sellers, channels, mode,
                                 quiet_from, quiet_to, timezone, cooldown_hours, repeat_mode, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(user_id), clean["kind"], clean["target"], clean["title"], clean["condition"], clean["threshold"],
              clean["city"], json.dumps(clean["shops"], ensure_ascii=False),
              json.dumps(clean["item_conditions"], ensure_ascii=False),
              json.dumps(clean["sellers"], ensure_ascii=False),
              json.dumps(clean["channels"], ensure_ascii=False),
              clean["mode"], clean["quiet_from"], clean["quiet_to"], clean["timezone"],
              clean["cooldown_hours"], clean["repeat"], clean["is_active"],
              datetime.datetime.now(datetime.timezone.utc).isoformat()))
        watch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return _watch_row(conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone())


def update_watch(user_id: int, watch_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Меняет своё наблюдение. Чужое не найдётся: выборка всегда ограничена владельцем."""
    import watches as w
    with get_connection() as conn:
        current = conn.execute("SELECT * FROM watches WHERE id = ? AND user_id = ?",
                               (int(watch_id), int(user_id))).fetchone()
        if not current:
            return None
        merged = _watch_row(current)
        merged.update({k: v for k, v in data.items() if k in (
            "kind", "target", "title", "condition", "threshold", "city", "shops",
            "item_conditions", "sellers", "channels", "mode",
            "quiet_from", "quiet_to", "timezone", "cooldown_hours", "repeat", "is_active")})
        clean = w.normalize(merged)
        conn.execute("""
            UPDATE watches SET kind=?, target=?, title=?, condition=?, threshold=?, city=?, shops=?,
                   item_conditions=?, sellers=?, channels=?, mode=?,
                   quiet_from=?, quiet_to=?, timezone=?, cooldown_hours=?, repeat_mode=?, is_active=?
            WHERE id = ? AND user_id = ?
        """, (clean["kind"], clean["target"], clean["title"], clean["condition"], clean["threshold"], clean["city"],
              json.dumps(clean["shops"], ensure_ascii=False),
              json.dumps(clean["item_conditions"], ensure_ascii=False),
              json.dumps(clean["sellers"], ensure_ascii=False),
              json.dumps(clean["channels"], ensure_ascii=False),
              clean["mode"], clean["quiet_from"], clean["quiet_to"],
              clean["timezone"], clean["cooldown_hours"], clean["repeat"], clean["is_active"],
              int(watch_id), int(user_id)))
        conn.commit()
        return _watch_row(conn.execute("SELECT * FROM watches WHERE id = ?", (int(watch_id),)).fetchone())


def delete_watch(user_id: int, watch_id: int) -> bool:
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM watches WHERE id = ? AND user_id = ?",
                               (int(watch_id), int(user_id))).rowcount
        if deleted:
            # Сначала снимаем неотправленные задания: после удаления срабатываний их уже не найти
            conn.execute("UPDATE notification_outbox SET status='cancelled', last_error='watch deleted' "
                         "WHERE status='pending' AND alert_id IN "
                         "(SELECT -id FROM watch_events WHERE watch_id = ?)", (int(watch_id),))
            conn.execute("DELETE FROM watch_state WHERE watch_id = ?", (int(watch_id),))
            conn.execute("DELETE FROM watch_events WHERE watch_id = ?", (int(watch_id),))
        conn.commit()
    return bool(deleted)


def _watch_states(conn, watch_id: int, product_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM watch_state WHERE watch_id = ? AND product_id = ?",
                       (int(watch_id), str(product_id))).fetchone()
    if not row:
        return {}
    res = dict(row)
    for col in ("known_sellers", "known_channels", "known_offers"):
        try:
            res[col] = json.loads(res.get(col) or "[]")
        except (ValueError, TypeError):
            res[col] = []
    return res


MAX_EVENTS_PER_WATCH_PER_RUN = 25


def _watch_scope_id(watch: Dict[str, Any], offer: Dict[str, Any]) -> str:
    """Ключ памяти наблюдения по предложению (B09).

    Для межпродавцовых условий (NEW_OFFER, NEW_SELLER, etc.) память ведётся по каноническому товару.
    Для условий снижения цены (ANY_DROP, DROP_*, TARGET_PRICE) — по конкретному офферу,
    чтобы цена одного продавца не вызывала ложное срабатывание у другого.
    """
    import watches as w
    product_id = str(offer.get("id") or "")
    offer_key = str(offer.get("id") or offer.get("offer_id") or "").strip()
    canonical_id = str(offer.get("canonical_product_id") or offer.get("product_id") or offer.get("canonical_key") or product_id)
    if watch.get("condition") in getattr(w, "CROSS_OFFER_CONDITIONS", ()):
        city = str(offer.get("city") or "").strip().lower()
        cond = str(offer.get("condition") or "new").strip().lower()
        return f"{canonical_id}:{city}:{cond}" if (city or cond != "new") else canonical_id
    return offer_key if offer_key else product_id


def _apply_retire_transition(jobs: Dict[str, int]) -> List[str]:
    """Фаза 1 задания watch_refresh (G05): «товар снят» записывается в память наблюдений
    (was_available = 0) в одной транзакции с переводом задания в фазу 'current'.

    jobs — {product_id: поколение}, прочитанные вызывающим. Под тем же BEGIN IMMEDIATE заново
    проверяется, что задание существует, всё ещё в фазе 'retire' и того же поколения: обработчик,
    опоздавший за конкурентом, ничего не меняет. Возвращает id, к которым переход применён.
    Событий фаза 1 не создаёт.
    """
    import watches as w
    if not jobs:
        return []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        has_active_watches = conn.execute("SELECT 1 FROM watches WHERE is_active = 1 LIMIT 1").fetchone() is not None
    preloaded_offers = watched_offers(list(jobs)) if has_active_watches else []

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            still: List[str] = []
            for pid, gen in jobs.items():
                if conn.execute("SELECT 1 FROM watch_refresh WHERE product_id = ? AND phase = 'retire' AND gen = ?",
                                (pid, gen)).fetchone():
                    still.append(pid)
            if not still:
                conn.rollback()
                return []
            active = [_watch_row(r) for r in conn.execute("SELECT * FROM watches WHERE is_active = 1")]
            if active:
                offers = preloaded_offers if preloaded_offers else watched_offers(still)
                still_set = set(still)
                for offer in offers:
                    oid = str(offer.get("id") or "")
                    ext = str(offer.get("external_sku") or "")
                    if oid not in still_set and ext not in still_set:
                        continue
                    for watch in active:
                        if not w.matches(watch, offer):
                            continue
                        conn.execute("""
                            INSERT INTO watch_state (watch_id, product_id, was_available, updated_at)
                            VALUES (?, ?, 0, ?)
                            ON CONFLICT(watch_id, product_id) DO UPDATE SET
                                was_available = 0, updated_at = excluded.updated_at
                        """, (watch["id"], _watch_scope_id(watch, offer), now))
            for pid in still:
                conn.execute("UPDATE watch_refresh SET phase = 'current' WHERE product_id = ? AND gen = ?",
                             (pid, jobs[pid]))
            conn.commit()
            return still
        except BaseException:
            conn.rollback()
            raise


def evaluate_watches(offers: List[Dict[str, Any]], now: Optional[datetime.datetime] = None) -> int:
    """Сверяет изменившиеся предложения с активными наблюдениями и ставит срабатывания в очередь.

    Одно срабатывание — одно задание доставки: задание кладётся в общую очередь уведомлений с
    alert_id = -id срабатывания, а её UNIQUE(alert_id, user_id) не даёт продублировать отправку даже
    при повторном запуске или параллельных воркерах (P07).
    """
    import watches as w
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    queued = 0
    with get_connection() as conn:
        active = [_watch_row(r) for r in conn.execute("SELECT * FROM watches WHERE is_active = 1")]
    if not active:
        return 0

    per_watch: Dict[int, int] = {}
    for offer in offers:
        product_id = str(offer.get("id") or "")
        if not product_id:
            continue
        for watch in active:
            if not w.matches(watch, offer):
                continue
            # Широкое наблюдение (магазин, сделки) за один обход может задеть сотни предложений.
            # Очередь не должна расти без предела: остальное попадёт в следующую проверку.
            if per_watch.get(watch["id"], 0) >= MAX_EVENTS_PER_WATCH_PER_RUN:
                continue
            price = int(offer.get("current_price") or 0)
            available = 1 if offer.get("is_available", True) else 0

            seller_key = str(offer.get("seller_slug") or offer.get("seller_id") or offer.get("seller_name") or offer.get("shop") or "").strip().lower()
            channel_key = str(offer.get("channel_type") or offer.get("channel_id") or offer.get("channel") or "").strip().lower()
            offer_key = str(offer.get("id") or offer.get("offer_id") or "").strip()

            scope_id = _watch_scope_id(watch, offer)

            with get_connection() as conn:
                # Чтение состояния, решение и запись события — одна транзакция на запись: два
                # одновременных обхода не создадут два сообщения об одном снижении (P07 G02)
                conn.execute("BEGIN IMMEDIATE")
                fresh = conn.execute("SELECT is_active, fired_at, last_sent_at, repeat_mode FROM watches "
                                     "WHERE id = ?", (watch["id"],)).fetchone()
                if not fresh or not fresh["is_active"] or (not fresh["repeat_mode"] and fresh["fired_at"]):
                    conn.rollback()
                    continue
                state = _watch_states(conn, watch["id"], scope_id)
                triggered, reason = w.condition_met(watch, offer, state)

                known_sellers = set(state.get("known_sellers") or [])
                if seller_key:
                    known_sellers.add(seller_key)
                known_channels = set(state.get("known_channels") or [])
                if channel_key:
                    known_channels.add(channel_key)
                known_offers = set(state.get("known_offers") or [])
                if offer_key:
                    known_offers.add(offer_key)
                sellers_json = json.dumps(sorted(known_sellers), ensure_ascii=False)
                channels_json = json.dumps(sorted(known_channels), ensure_ascii=False)
                offers_json = json.dumps(sorted(known_offers), ensure_ascii=False)

                if not triggered:
                    # Память обновляется всегда: следующее снижение считается от актуальной цены
                    conn.execute("""
                        INSERT INTO watch_state (watch_id, product_id, previous_price, min_price,
                                                 was_available, updated_at,
                                                 known_sellers, known_channels, known_offers)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(watch_id, product_id) DO UPDATE SET
                            previous_price = excluded.previous_price,
                            min_price = MIN(COALESCE(min_price, excluded.min_price), excluded.min_price),
                            was_available = excluded.was_available,
                            updated_at = excluded.updated_at,
                            known_sellers = excluded.known_sellers,
                            known_channels = excluded.known_channels,
                            known_offers = excluded.known_offers
                    """, (watch["id"], scope_id, price, price or None, available, moment.isoformat(),
                          sellers_json, channels_json, offers_json))
                    conn.commit()
                    continue

                last_sent_at = None
                if fresh["last_sent_at"]:
                    last_sent_at = datetime.datetime.fromisoformat(fresh["last_sent_at"])
                send_now, wait_reason = w.deliver_now(watch, moment, last_sent_at)
                deliver_after = 0.0 if send_now else w.digest_due_at(watch, moment).timestamp()

                conn.execute("""INSERT INTO watch_events (watch_id, product_id, price, reason, created_at,
                                                          status, deliver_after)
                                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                             (watch["id"], product_id, price, reason, moment.isoformat(),
                              "queued" if send_now else f"waiting:{wait_reason}", deliver_after))
                event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                payload = {"kind": "watch", "watch_id": watch["id"], "event_id": event_id,
                           "reason": reason, "price": price,
                           "product": {k: offer.get(k) for k in (
                               "id", "title", "shop", "city", "url", "image_url",
                               "seller_name", "seller_slug", "channel_type", "condition", "item_condition"
                           )},
                           "description": w.describe(watch)}
                conn.execute("""INSERT OR IGNORE INTO notification_outbox
                                (alert_id, user_id, payload, status, created_at, next_attempt_at)
                                VALUES (?, ?, ?, 'pending', ?, ?)""",
                             (-event_id, watch["user_id"], json.dumps(payload, ensure_ascii=False),
                              moment.timestamp(), deliver_after))
                conn.execute("""
                    INSERT INTO watch_state (watch_id, product_id, last_notified_price, previous_price,
                                             min_price, was_available, updated_at,
                                             known_sellers, known_channels, known_offers)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(watch_id, product_id) DO UPDATE SET
                        last_notified_price = excluded.last_notified_price,
                        previous_price = excluded.previous_price,
                        min_price = MIN(COALESCE(min_price, excluded.min_price), excluded.min_price),
                        was_available = excluded.was_available,
                        updated_at = excluded.updated_at,
                        known_sellers = excluded.known_sellers,
                        known_channels = excluded.known_channels,
                        known_offers = excluded.known_offers
                """, (watch["id"], scope_id, price, price, price or None, available, moment.isoformat(),
                      sellers_json, channels_json, offers_json))
                # Одноразовое наблюдение помечается сработавшим, но остаётся включённым до отправки:
                # выключение прямо здесь отменило бы его собственное сообщение (G01)
                conn.execute("UPDATE watches SET last_sent_at = ?, fired_at = COALESCE(fired_at, ?) "
                             "WHERE id = ?", (moment.isoformat(), moment.isoformat(), watch["id"]))
                conn.commit()
                per_watch[watch["id"]] = per_watch.get(watch["id"], 0) + 1
                watch["last_sent_at"] = moment.isoformat()
                if not watch["repeat"]:
                    watch["fired_at"] = moment.isoformat()
                queued += 1
    return queued


WATCH_ALERT_WINDOW_MINUTES = 30      # находка считается свежей столько времени после записи алерта


def watched_offers(product_ids: List[str], now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Сохранённые предложения по идентификаторам — вход для проверки наблюдений (P07, A02).

    К предложению добавляется свежая находка ленты (алерт), если она есть: вид, скидка и магазин-основание.
    Поддерживает V2-схему (offers, canonical_products, sellers, channels) с передачей
    seller_id, seller_slug, channel_type, condition и canonical_product_id.
    """
    ids = [str(i) for i in product_ids if i]
    if not ids:
        return []
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    # created_at у алертов пишет сам SQLite (CURRENT_TIMESTAMP, UTC, без смещения) —
    # сравниваем в том же виде, иначе граница окна сдвинулась бы
    since = (moment - datetime.timedelta(minutes=WATCH_ALERT_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    rows: List[Dict[str, Any]] = []
    with get_connection() as conn:
        has_v2 = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='offers'").fetchone())

        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            found_ids = set()

            if has_v2:
                # B08: Скрапер может передавать legacy ID ('after-lease') или V2 offer ID ('off_after-lease').
                # Ищем как по o.id, так и по o.external_sku и префиксированным ID.
                lookup_ids = list(chunk)
                for i in chunk:
                    if not i.startswith("off_"):
                        lookup_ids.append(f"off_{i}")
                    else:
                        lookup_ids.append(i[4:])
                unique_lookup = list(dict.fromkeys(lookup_ids))
                v2_placeholders = ",".join("?" * len(unique_lookup))

                v2_sql = f"""
                    SELECT
                        o.id,
                        o.id AS offer_id,
                        o.external_sku,
                        o.product_id AS canonical_product_id,
                        o.product_id,
                        cp.canonical_key,
                        COALESCE(cp.title, o.id) AS title,
                        COALESCE(cp.category, 'general') AS category,
                        COALESCE(s.name, 'Магазин') AS shop,
                        s.id AS seller_id,
                        s.slug AS seller_slug,
                        s.name AS seller_name,
                        c.id AS channel_id,
                        c.channel_type,
                        LOWER(COALESCE(o.condition, 'new')) AS condition,
                        o.city,
                        o.url,
                        o.image_url,
                        o.availability,
                        o.expires_at,
                        o.observed_at,
                        o.is_active,
                        s.is_active AS seller_is_active,
                        c.is_active AS channel_is_active,
                        CAST(o.price AS INTEGER) AS current_price
                    FROM offers o
                    LEFT JOIN canonical_products cp ON o.product_id = cp.id
                    LEFT JOIN sellers s ON o.seller_id = s.id
                    LEFT JOIN channels c ON o.channel_id = c.id
                    WHERE o.id IN ({v2_placeholders}) OR o.external_sku IN ({v2_placeholders})
                """
                v2_rows = [dict(r) for r in conn.execute(v2_sql, unique_lookup + unique_lookup)]
                from domain.purchasability import offer_purchasable
                for r in v2_rows:
                    found_ids.add(r["id"])
                    if r.get("external_sku"):
                        found_ids.add(r["external_sku"])
                    if r["id"].startswith("off_"):
                        found_ids.add(r["id"][4:])
                    # Если запрашивался legacy ID (external_sku), сохраняем r["id"] для совместимости
                    if r.get("external_sku") and r.get("external_sku") in chunk:
                        r["id"] = r["external_sku"]

                    # C08, R08: то же правило, что у поиска и витрины — наличие, TTL, свежесть
                    # наблюдения и активность предложения, канала и продавца
                    r["is_available"] = 1 if offer_purchasable(r, now=moment) else 0

                    rows.append(r)

            missing = [i for i in chunk if i not in found_ids]
            if missing:
                m_placeholders = ",".join("?" * len(missing))
                legacy_rows = [dict(r) for r in conn.execute(
                    f"SELECT id, title, shop, city, url, image_url, category, canonical_key, current_price, "
                    f"is_active, updated_at FROM products WHERE id IN ({m_placeholders})", missing)]
                from domain.purchasability import offer_purchasable
                for r in legacy_rows:
                    r["is_available"] = 1 if offer_purchasable(r, now=moment) else 0
                    from domain.adapter import resolve_seller_and_channel_meta
                    slug, sname, ch_type = resolve_seller_and_channel_meta(r.get("shop") or "")
                    r["seller_id"] = f"seller_{slug}"
                    r["seller_slug"] = slug
                    r["seller_name"] = sname
                    r["channel_type"] = ch_type
                    r["condition"] = "new"
                    r["availability"] = "in_stock"
                    r["expires_at"] = None
                    r["product_id"] = r.get("id")
                    r["canonical_product_id"] = r.get("id")
                    rows.append(r)

            # Прежние ключи и товары предложения (P17CR-02): наблюдения со старой целью продолжают его узнавать
            former_keys = _former_identities(conn, "key", [r.get("canonical_key") for r in rows])
            former_pids = _former_identities(conn, "product", [r.get("canonical_product_id") for r in rows])
            for r in rows:
                r["former_keys"] = former_keys.get(r.get("canonical_key") or "", [])
                r["former_product_ids"] = former_pids.get(r.get("canonical_product_id") or "", [])

            alert_ids = list(dict.fromkeys(chunk + list(found_ids)))
            alert_placeholders = ",".join("?" * len(alert_ids))
            for row in conn.execute(
                    f"SELECT product_id, alert_type, discount_pct, new_price, competitor_shop "
                    f"FROM alerts WHERE product_id IN ({alert_placeholders}) AND is_dismissed = 0 "
                    f"AND created_at >= ? ORDER BY id", alert_ids + [since]):
                for offer in rows:
                    if (offer["id"] == row["product_id"] or offer.get("external_sku") == row["product_id"]) and int(row["new_price"] or 0) == int(offer["current_price"] or 0):
                        offer["alert_type"] = row["alert_type"]
                        offer["discount_pct"] = row["discount_pct"]
                        offer["competitor_shop"] = row["competitor_shop"]
    return rows


def iter_watch_events(user_id: int, page: int = 500):
    """Все срабатывания пользователя, от старых к новым, страницами по id (F03, выгрузка данных).

    Список в интерфейсе ограничен; выгрузка — нет. Страницы не держат курсор открытым дольше одной выборки.
    """
    last_id = 0
    while True:
        with get_connection() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT e.id, e.watch_id, e.product_id, e.price, e.reason, e.created_at, e.status,
                       w.kind, w.target, w.title
                FROM watch_events e JOIN watches w ON w.id = e.watch_id
                WHERE w.user_id = ? AND e.id > ? ORDER BY e.id LIMIT ?
            """, (int(user_id), last_id, page))]
        if not rows:
            return
        yield from rows
        last_id = rows[-1]["id"]


def watch_events(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """История срабатываний наблюдений пользователя (для «Моих наблюдений» и выгрузки данных)."""
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT e.id, e.watch_id, e.product_id, e.price, e.reason, e.created_at, e.status,
                   w.kind, w.target, w.title
            FROM watch_events e JOIN watches w ON w.id = e.watch_id
            WHERE w.user_id = ? ORDER BY e.id DESC LIMIT ?
        """, (int(user_id), max(1, int(limit))))]


# ---------------------------------------------------------------------------
# Учёт AI (P08): сколько вызовов, токенов и денег ушло на какую задачу.
# ---------------------------------------------------------------------------

def record_ai_usage(task: str, audience: str, provider: str, model: str, outcome: str,
                    input_tokens: int = 0, output_tokens: int = 0, cost: Optional[float] = None,
                    latency_ms: float = 0.0, error: Optional[str] = None,
                    cache_hit: bool = False, fallback: bool = False,
                    now: Optional[datetime.datetime] = None) -> None:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    day = moment.strftime("%Y-%m-%d")
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO ai_usage (day, task, audience, provider, model, outcome, requests, priced_requests,
                                  input_tokens, output_tokens, cost_usd, latency_sum_ms, cache_hits,
                                  fallbacks, errors, last_error)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, task, provider, model, outcome) DO UPDATE SET
                requests = requests + 1,
                priced_requests = COALESCE(priced_requests, 0) + excluded.priced_requests,
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                cost_usd = CASE WHEN excluded.cost_usd IS NULL THEN cost_usd
                                ELSE COALESCE(cost_usd, 0) + excluded.cost_usd END,
                latency_sum_ms = latency_sum_ms + excluded.latency_sum_ms,
                cache_hits = cache_hits + excluded.cache_hits,
                fallbacks = fallbacks + excluded.fallbacks,
                errors = errors + excluded.errors,
                last_error = COALESCE(excluded.last_error, last_error)
        """, (day, task, audience, provider, model, outcome,
              1 if cost is not None else 0,          # вызов с известной стоимостью (M06)
              int(input_tokens or 0), int(output_tokens or 0),
              cost, float(latency_ms or 0.0), 1 if cache_hit else 0, 1 if fallback else 0,
              1 if outcome in ("error", "bad_response") else 0, error))
        conn.commit()


def ai_usage(days: int = 7, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Сводка расходов AI за период: по задачам и по моделям, с честной пометкой о неизвестной цене."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    totals = {"requests": 0, "provider_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
              "cache_hits": 0, "fallbacks": 0, "errors": 0, "deterministic": 0}
    by_task: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    priced = True
    with get_connection() as conn:
        for row in conn.execute("SELECT * FROM ai_usage WHERE day >= ?", (since,)):
            item = dict(row)
            totals["requests"] += item["requests"]
            totals["input_tokens"] += item["input_tokens"]
            totals["output_tokens"] += item["output_tokens"]
            totals["cache_hits"] += item["cache_hits"]
            totals["fallbacks"] += item["fallbacks"]
            totals["errors"] += item["errors"]
            # «Пауза локальной модели» (P17-A) — модель не вызывалась, это не обращение к провайдеру
            called = item["provider"] not in ("none", "cache") and item["outcome"] != "local_paused"
            if called:
                totals["provider_calls"] += item["requests"]
                if item["cost_usd"] is None and item["input_tokens"] + item["output_tokens"]:
                    priced = False
            if item["outcome"] == "deterministic":
                totals["deterministic"] += item["requests"]
            totals["cost_usd"] += item["cost_usd"] or 0.0

            task = by_task.setdefault(item["task"], {"audience": item["audience"], "requests": 0,
                                                     "provider_calls": 0, "errors": 0, "cache_hits": 0,
                                                     "fallbacks": 0, "cost_usd": 0.0, "latency_sum_ms": 0.0})
            task["requests"] += item["requests"]
            task["errors"] += item["errors"]
            task["cache_hits"] += item["cache_hits"]
            task["fallbacks"] += item["fallbacks"]
            task["cost_usd"] += item["cost_usd"] or 0.0
            task["latency_sum_ms"] += item["latency_sum_ms"]
            if called:
                task["provider_calls"] += item["requests"]

            if not called:
                continue
            model = by_model.setdefault(f'{item["provider"]}/{item["model"]}',
                                        {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                         "cost_usd": 0.0, "errors": 0})
            model["requests"] += item["requests"]
            model["input_tokens"] += item["input_tokens"]
            model["output_tokens"] += item["output_tokens"]
            model["cost_usd"] += item["cost_usd"] or 0.0
            model["errors"] += item["errors"]

    for task in by_task.values():
        task["avg_latency_ms"] = round(task["latency_sum_ms"] / task["provider_calls"], 1) if task["provider_calls"] else None
        task.pop("latency_sum_ms")
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    # Самые частые причины отказов: «100 % ошибок» без причины ничего не объясняет (найдено владельцем)
    with get_connection() as conn:
        failures = [dict(r) for r in conn.execute(
            "SELECT provider, model, last_error AS reason, SUM(errors) AS count FROM ai_usage "
            "WHERE day >= ? AND errors > 0 AND last_error IS NOT NULL "
            "GROUP BY provider, model, last_error ORDER BY count DESC LIMIT 5", (since,))]
    return {"days": int(days), "totals": totals, "by_task": by_task, "by_model": by_model,
            "failures": failures,
            "cost_known": priced,
            "note": ("Стоимость посчитана по ценам, заданным администратором." if priced else
                     "Цены части моделей не заданы — стоимость показана не полностью.")}


def prune_ai_usage(days: int = 365, now: Optional[datetime.datetime] = None) -> int:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (moment - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM ai_usage WHERE day < ?", (cutoff,)).rowcount
        conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# Теневой отчёт сопоставления (P09): видно, что изменило правило фасовки и где оно не уверено.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Суточная сводка (P13): один день — одна запись, пересчёт заменяет её целиком.
# ---------------------------------------------------------------------------

def save_daily_report(day: str, tz: str, computed_at: str, partial: int, payload: str,
                      summary: Optional[str] = None, provider: Optional[str] = None) -> None:
    """Пересчёт того же дня не создаёт вторую запись. Пересказ не теряется, если его не передали."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO daily_reports (day, tz, computed_at, partial, payload, summary, summary_provider)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, tz) DO UPDATE SET
                computed_at = excluded.computed_at, partial = excluded.partial,
                payload = excluded.payload,
                summary = COALESCE(excluded.summary, daily_reports.summary),
                summary_provider = COALESCE(excluded.summary_provider, daily_reports.summary_provider)
        """, (day, tz, computed_at, int(partial), payload, summary, provider))
        conn.commit()


def daily_report(day: str, tz: str = "UTC") -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM daily_reports WHERE day = ? AND tz = ?", (day, tz)).fetchone()
    return dict(row) if row else None


def daily_reports(limit: int = 30) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        return [{"day": r["day"], "tz": r["tz"], "computed_at": r["computed_at"],
                 "partial": bool(r["partial"]), "has_summary": bool(r["summary"])}
                for r in conn.execute("SELECT day, tz, computed_at, partial, summary FROM daily_reports "
                                      "ORDER BY day DESC LIMIT ?", (limit,))]


DAILY_REPORT_RETENTION_DAYS = 180


def prune_daily_reports(keep_days: int = DAILY_REPORT_RETENTION_DAYS, now: Optional[datetime.datetime] = None) -> int:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (moment - datetime.timedelta(days=max(1, int(keep_days)))).strftime("%Y-%m-%d")
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM daily_reports WHERE day < ?", (cutoff,))
        conn.commit()
        return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Спорные пары сопоставления и решения модели по ним (P09, AI-часть).
# ---------------------------------------------------------------------------

MATCHING_PAIRS_RETENTION_DAYS = 90
MAX_MATCHING_PAIRS = 5000        # очередь разбора не должна расти без предела


def record_matching_pair(left_title: str, right_title: str, pair_key: str,
                         now: Optional[datetime.datetime] = None) -> None:
    """Запоминает спорную пару для последующего разбора. Повтор только обновляет счётчик."""
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM matching_pairs WHERE same IS NULL").fetchone()[0]
        exists = conn.execute("SELECT 1 FROM matching_pairs WHERE pair_key = ?", (pair_key,)).fetchone()
        if not exists and count >= MAX_MATCHING_PAIRS:
            return                # очередь заполнена: новые случаи подождут следующей очистки
        conn.execute("""
            INSERT INTO matching_pairs (pair_key, left_title, right_title, seen, first_seen, last_seen)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(pair_key) DO UPDATE SET seen = seen + 1, last_seen = excluded.last_seen
        """, (pair_key, str(left_title)[:300], str(right_title)[:300], moment, moment))
        conn.commit()


def pending_matching_pairs(limit: int = 10) -> List[Dict[str, Any]]:
    """Самые частые нерешённые пары: разбирать сначала то, что встречается чаще."""
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT pair_key, left_title, right_title, seen FROM matching_pairs WHERE same IS NULL "
            "ORDER BY seen DESC, rowid LIMIT ?", (int(limit),))]


def save_matching_decision(pair_key: str, same: bool, confidence: float, reason: str,
                           provider: Optional[str], mode: str,
                           now: Optional[datetime.datetime] = None) -> None:
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    with get_connection() as conn:
        conn.execute("""UPDATE matching_pairs SET same = ?, confidence = ?, reason = ?, provider = ?,
                        mode = ?, decided_at = ? WHERE pair_key = ?""",
                     (1 if same else 0, float(confidence), str(reason)[:300], provider, mode,
                      moment, pair_key))
        conn.commit()


def matching_decision(pair_key: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM matching_pairs WHERE pair_key = ? AND same IS NOT NULL",
                           (pair_key,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["same"] = bool(item["same"])
    return item


def matching_decisions_summary(limit: int = 20) -> Dict[str, Any]:
    """Что модель разобрала: сколько пар ждёт, сколько решено и с какой уверенностью."""
    with get_connection() as conn:
        pending = conn.execute("SELECT COUNT(*) FROM matching_pairs WHERE same IS NULL").fetchone()[0]
        decided = conn.execute("SELECT COUNT(*) FROM matching_pairs WHERE same IS NOT NULL").fetchone()[0]
        yes = conn.execute("SELECT COUNT(*) FROM matching_pairs WHERE same = 1").fetchone()[0]
        examples = [dict(r) for r in conn.execute(
            "SELECT left_title, right_title, same, confidence, reason, provider, decided_at, seen "
            "FROM matching_pairs WHERE same IS NOT NULL ORDER BY decided_at DESC LIMIT ?", (int(limit),))]
    for item in examples:
        item["same"] = bool(item["same"])
    return {"pending": pending, "decided": decided, "same": yes, "different": decided - yes,
            "examples": examples}


def prune_matching_pairs(keep_days: int = MATCHING_PAIRS_RETENTION_DAYS,
                         now: Optional[datetime.datetime] = None) -> int:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (moment - datetime.timedelta(days=max(1, int(keep_days)))).isoformat()
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM matching_pairs WHERE last_seen < ?", (cutoff,))
        conn.commit()
        return cur.rowcount or 0

def _ai_allows_comparison(left_title: str, right_title: str) -> bool:
    """Разрешила ли модель сравнение этой спорной пары.

    Сетевых вызовов здесь нет: путь сравнения цен не должен ждать модель. Спорная пара только
    запоминается, разбирает её отдельная задача обслуживания, а сюда попадает уже готовое решение —
    и только если владелец включил режим «on».
    """
    try:
        import catalog_ai
        key = catalog_ai.pair_key(left_title, right_title)
        record_matching_pair(left_title, right_title, key)
        if catalog_ai.mode() != catalog_ai.ON:
            return False
        return catalog_ai.accepted(matching_decision(key))
    except Exception as e:                     # сбой AI-части не должен ломать сравнение цен
        print(f"[Matching] AI-разбор недоступен: {type(e).__name__}")
        return False


def record_matching_shadow(kind: str, reason: str, left: str, right: str,
                           now: Optional[datetime.datetime] = None) -> None:
    """Fail-open: отчёт не должен мешать обходу и сравнению цен."""
    try:
        moment = now or datetime.datetime.now(datetime.timezone.utc)
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO matching_shadow (day, kind, reason, count, example_left, example_right)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(day, kind, reason) DO UPDATE SET count = count + 1
            """, (moment.strftime("%Y-%m-%d"), kind, reason, str(left)[:200], str(right)[:200]))
            conn.commit()
    except Exception:
        pass


def record_normalize_shadow(kind: str, primary_key: str, shadow_key: str, example_title: str,
                            now: Optional[datetime.datetime] = None) -> None:
    """Учёт теневой нормализации (P17-C5). Fail-open: ошибка записи не мешает основной нормализации."""
    try:
        moment = now or datetime.datetime.now(datetime.timezone.utc)
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO normalize_shadow (day, kind, primary_key, shadow_key, count, example_title)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(day, kind, primary_key, shadow_key) DO UPDATE SET count = count + 1
            """, (moment.strftime("%Y-%m-%d"), kind, str(primary_key)[:150], str(shadow_key)[:150], str(example_title)[:200]))
            conn.commit()
    except Exception:
        pass


def normalize_shadow_summary(days: int = 7, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Сводка теневой нормализации (P17-C5): совпадения, расхождения, доля совпадений и примеры."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    matched, mismatch, rows = 0, 0, []
    try:
        with get_connection() as conn:
            for row in conn.execute("SELECT * FROM normalize_shadow WHERE day >= ? ORDER BY count DESC LIMIT 200", (since,)):
                item = dict(row)
                rows.append(item)
                if item["kind"] == "matched":
                    matched += int(item["count"])
                else:
                    mismatch += int(item["count"])
    except Exception:
        pass
    total = matched + mismatch
    match_rate = round(matched / total, 4) if total > 0 else None
    return {
        "days": days,
        "matched": matched,
        "mismatch": mismatch,
        "total": total,
        "match_rate": match_rate,
        "breakdown": rows,
    }


def matching_shadow(days: int = 7, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Сводка теневого отчёта: сколько сравнений заблокировано по фасовке и сколько случаев спорных."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    blocked, uncertain, rows = 0, 0, []
    with get_connection() as conn:
        for row in conn.execute("SELECT * FROM matching_shadow WHERE day >= ? ORDER BY count DESC LIMIT 100",
                                (since,)):
            item = dict(row)
            rows.append(item)
            if item["kind"] == "blocked":
                blocked += item["count"]
            else:
                uncertain += item["count"]
    return {"days": int(days), "blocked": blocked, "uncertain": uncertain, "rows": rows,
            "note": "«Запрещено» — разная фасовка, такие цены не сравниваются. «Не уверены» — фасовка "
                    "указана только у одного предложения, поэтому цены тоже не сравниваются: это кандидаты "
                    "на разбор моделью."}


# ---------------------------------------------------------------------------
# Сигналы для теневого планировщика (P10). Только чтение уже собранных данных:
# дополнительных обходов магазинов ради планирования не делается.
# ---------------------------------------------------------------------------

def scheduler_candidates(days: int = 7, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Источники (магазин + категория) с сигналами: возраст, качество, спрос, наблюдения, изменчивость цен."""
    import scheduler_shadow as sched
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since_day = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    since_ts = (moment - datetime.timedelta(days=max(1, int(days)))).isoformat()

    candidates: Dict[tuple, Dict[str, Any]] = {}
    with get_connection() as conn:
        # Последний обход каждой пары «магазин + категория» и его качество
        for row in conn.execute("""
            SELECT s.shop_key, s.category, s.quality, s.finished_at
            FROM source_scans s
            JOIN (SELECT shop_key, category, MAX(id) AS last_id FROM source_scans GROUP BY shop_key, category) last
              ON last.last_id = s.id
        """):
            try:
                finished = datetime.datetime.fromisoformat(row["finished_at"])
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=datetime.timezone.utc)
                age_hours = max(0.0, (moment - finished).total_seconds() / 3600.0)
            except (TypeError, ValueError):
                age_hours = None
            candidates[(row["shop_key"], row["category"])] = {
                "shop": row["shop_key"], "category": row["category"],
                "age_hours": age_hours, "last_quality": row["quality"],
                "searches": 0, "unmet_searches": 0, "watches": 0, "price_changes_per_day": None,
            }

        # Пауза после ошибок берётся из состояния магазина: планировщик её обязан уважать
        retries = {r["shop_key"]: r["next_retry_at"] for r in conn.execute(
            "SELECT shop_key, next_retry_at FROM shop_scans")}

        # Спрос: запросы людей, сопоставленные с категориями по тем же правилам, что и поиск
        demand_rows = [dict(r) for r in conn.execute(
            "SELECT normalized_query, SUM(searches) AS searches, SUM(weak) + SUM(not_found) AS unmet "
            "FROM search_queries WHERE bucket >= ? GROUP BY normalized_query", (since_day,))]

        # Наблюдения людей: по категории напрямую, по товару и модели — через магазин товара
        watch_rows = [dict(r) for r in conn.execute(
            "SELECT kind, target FROM watches WHERE is_active = 1")]

        # Фактические переходы текущей цены, включая предыдущее наблюдение до окна.
        # Первая цена и изменение только зачёркнутой цены переходом не считаются.
        from config import SHOP_KEYS
        shop_aliases = {str(name).strip().casefold(): key for key, name in SHOP_KEYS.items()}
        shop_aliases.update({key.casefold(): key for key in SHOP_KEYS})
        for row in conn.execute("""
            WITH history AS (
                SELECT product_id, price, observed_at,
                       LAG(price) OVER (PARTITION BY product_id ORDER BY julianday(observed_at), rowid) AS previous
                FROM price_observations
                WHERE julianday(observed_at) <= julianday(?)
            )
            SELECT p.shop, p.category, COUNT(*) AS samples,
                   SUM(CASE WHEN h.previous IS NOT NULL AND h.price != h.previous THEN 1 ELSE 0 END) AS changes
            FROM history h JOIN products p ON p.id = h.product_id
            WHERE julianday(h.observed_at) >= julianday(?)
            GROUP BY p.shop, p.category
        """, (moment.isoformat(), since_ts)):
            shop_key = shop_aliases.get(str(row["shop"] or "").strip().casefold())
            item = candidates.get((shop_key, row["category"]))
            if item is not None:
                item["price_changes_per_day"] = round(
                    (item["price_changes_per_day"] or 0.0) + row["changes"] / max(1, int(days)), 2)

    from search_engine import determine_category_and_master
    for row in demand_rows:
        name, _ = determine_category_and_master("", row["normalized_query"])
        target = str(name or "").lower()
        for item in candidates.values():
            if target and target in str(item["category"] or "").lower():
                item["searches"] += int(row["searches"] or 0)
                item["unmet_searches"] += int(row["unmet"] or 0)

    for row in watch_rows:
        target = str(row["target"] or "").lower()
        for item in candidates.values():
            category = str(item["category"] or "").lower()
            if row["kind"] == "category" and target and target in category:
                item["watches"] += 1
            elif row["kind"] in ("search", "model") and target and any(
                    word in category for word in target.split() if len(word) > 3):
                item["watches"] += 1

    http = http_metrics_by_shop(days=days, now=moment)
    result = []
    for (shop_key, _category), item in candidates.items():
        profile, why = sched.source_profile(http.get(shop_key, {}))
        item["profile"] = profile
        item["profile_reason"] = why
        item["target_hours"] = sched.MIN_INTERVAL_HOURS.get(profile, 6.0)
        retry_at = retries.get(shop_key)
        if retry_at:
            try:
                moment_retry = datetime.datetime.fromtimestamp(float(retry_at), datetime.timezone.utc)
                item["next_retry_at"] = moment_retry
            except (TypeError, ValueError):
                pass
        result.append(item)
    return result


def http_metrics_by_shop(days: int = 7, now: Optional[datetime.datetime] = None) -> Dict[str, Dict[str, Any]]:
    """HTTP-метрики магазина за период из агрегатов телеметрии (P01): нагрузка, ошибки, задержка."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    metrics: Dict[str, Dict[str, Any]] = {}
    with get_connection() as conn:
        for row in conn.execute("""
            SELECT shop, SUM(total_requests) AS requests, SUM(errors) + SUM(status_5xx) AS errors,
                   SUM(status_429) + SUM(status_4xx) AS blocked, MAX(latency_p95_ms) AS latency_p95_ms,
                   SUM(bytes_total) AS bytes_total
            FROM telemetry_http_aggregates
            WHERE bucket_type = 'day' AND bucket_start >= ? AND shop != ''
            GROUP BY shop
        """, (since,)):
            metrics[row["shop"]] = dict(row)
    return metrics


def http_counters_for_hour(moment: datetime.datetime) -> Dict[str, Dict[str, Any]]:
    """Счётчики того часа, в который попал момент: нужны, чтобы посчитать дельту неполного часа (M02)."""
    bucket = moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    counters: Dict[str, Dict[str, Any]] = {}
    with get_connection() as conn:
        for row in conn.execute("""
            SELECT shop, SUM(total_requests) AS requests, SUM(errors) + SUM(status_5xx) AS errors,
                   SUM(status_429) + SUM(status_4xx) AS blocked
            FROM telemetry_http_aggregates
            WHERE bucket_type = 'hour' AND bucket_start = ? AND shop != ''
            GROUP BY shop
        """, (bucket,)):
            counters[row["shop"]] = {"requests": int(row["requests"] or 0),
                                     "errors": int(row["errors"] or 0),
                                     "blocked": int(row["blocked"] or 0)}
    return {"hour": bucket, "shops": counters}


def http_metrics_by_shop_window(since: datetime.datetime,
                                until: Optional[datetime.datetime] = None,
                                start_counters: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """HTTP-метрики за ТОЧНЫЙ период по часовым агрегатам (P11, M02).

    Дневных сумм для границы внутри суток недостаточно: они втягивают трафик до начала шага и прячут
    провал пробного магазина.

    Неполный первый час не выбрасывается (M02): если передан снимок счётчиков на момент начала
    (`start_counters` от `http_counters_for_hour`), из часа, в котором шаг начался, вычитается то, что
    уже было записано до старта. Без снимка час начала не засчитывается вовсе — это честнее, чем
    засчитать чужой трафик, и такой случай виден в отчёте.
    """
    end = until or datetime.datetime.now(datetime.timezone.utc)
    start = since.astimezone(datetime.timezone.utc)
    fmt = "%Y-%m-%dT%H:00:00Z"
    start_hour = start.replace(minute=0, second=0, microsecond=0)
    partial_hour = start_hour.strftime(fmt)
    counters = (start_counters or {}).get("shops") or {}
    uses_delta = bool(start_counters) and (start_counters.get("hour") == partial_hour)
    if start.minute or start.second or start.microsecond:
        lo = partial_hour if uses_delta else (start_hour + datetime.timedelta(hours=1)).strftime(fmt)
    else:
        lo = partial_hour
        uses_delta = False                 # шаг начался ровно на границе часа: вычитать нечего
    hi = end.astimezone(datetime.timezone.utc).strftime(fmt)

    metrics: Dict[str, Dict[str, Any]] = {}
    with get_connection() as conn:
        for row in conn.execute("""
            SELECT shop, SUM(total_requests) AS requests, SUM(errors) + SUM(status_5xx) AS errors,
                   SUM(status_429) + SUM(status_4xx) AS blocked, MAX(latency_p95_ms) AS latency_p95_ms
            FROM telemetry_http_aggregates
            WHERE bucket_type = 'hour' AND bucket_start >= ? AND bucket_start <= ? AND shop != ''
            GROUP BY shop
        """, (lo, hi)):
            item = dict(row)
            if uses_delta:
                before = counters.get(row["shop"]) or {}
                for field in ("requests", "errors", "blocked"):
                    item[field] = max(0, int(item.get(field) or 0) - int(before.get(field) or 0))
            metrics[row["shop"]] = item
    metrics["_coverage"] = {"from": lo, "to": hi, "partial_hour": "delta" if uses_delta else
                            ("full" if lo == partial_hour else "excluded")}
    return metrics


# ---------------------------------------------------------------------------
# Пульс фоновых работников (P14).
# ---------------------------------------------------------------------------

def record_heartbeat(component: str, note: str = "", now: Optional[datetime.datetime] = None) -> None:
    """Отметка работника «я жив». Пишется часто, поэтому одной короткой операцией."""
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO component_heartbeats (component, last_seen, note, beats) VALUES (?, ?, ?, 1)
            ON CONFLICT(component) DO UPDATE SET last_seen = excluded.last_seen,
                                                note = excluded.note, beats = beats + 1
        """, (str(component), moment, str(note or "")[:200]))
        conn.commit()


def heartbeats() -> Dict[str, Dict[str, Any]]:
    """Последний пульс каждого компонента."""
    with get_connection() as conn:
        return {r["component"]: dict(r) for r in conn.execute("SELECT * FROM component_heartbeats")}


# ---------------------------------------------------------------------------
# Самопроверка копий и фактическое потребление по срокам хранения (P15).
# ---------------------------------------------------------------------------

def record_backup_check(kind: str, ok: bool, file: Optional[str] = None, detail: Optional[str] = None,
                        duration_sec: Optional[float] = None, file_size: Optional[int] = None,
                        file_mtime: Optional[float] = None,
                        now: Optional[datetime.datetime] = None) -> None:
    """Результат проверки вместе с приметами самого файла: к какой именно копии он относится (P15 L01)."""
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    with get_connection() as conn:
        conn.execute("""INSERT INTO backup_checks (checked_at, file, kind, ok, detail, duration_sec,
                                                   file_size, file_mtime)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (moment, str(file or "")[:200], str(kind), 1 if ok else 0,
                      str(detail or "")[:300] or None, duration_sec, file_size, file_mtime))
        # История самопроверок нужна недолго: держим последние 100 записей
        conn.execute("DELETE FROM backup_checks WHERE id NOT IN "
                     "(SELECT id FROM backup_checks ORDER BY id DESC LIMIT 100)")
        conn.commit()


def record_backup_delivery(outcome: str, target: str = "", file: str = "", size_bytes: Any = None,
                           detail: str = "", now: Optional[datetime.datetime] = None) -> None:
    """Итог доставки ночной копии на другой диск (P17E-02). Зовётся скриптом копирования через
    docker exec: «ok» — только после проверки конечного файла, всё остальное — сбой с причиной."""
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    try:
        size = int(size_bytes) if size_bytes not in (None, "") else None
    except (TypeError, ValueError):
        size = None
    with get_connection() as conn:
        conn.execute("INSERT INTO backup_deliveries (delivered_at, outcome, target, file, size_bytes, detail) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (moment, "ok" if outcome == "ok" else "failed", str(target or "")[:200],
                      str(file or "")[:200], size, str(detail or "")[:300] or None))
        conn.execute("DELETE FROM backup_deliveries WHERE id NOT IN "
                     "(SELECT id FROM backup_deliveries ORDER BY id DESC LIMIT 200)")
        conn.commit()


def backup_deliveries(limit: int = 10) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM backup_deliveries ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))]


def backup_checks(limit: int = 10) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM backup_checks ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))]


def retention_usage() -> List[Dict[str, Any]]:
    """Сколько строк сейчас занимает каждый вид данных и насколько старые есть записи."""
    plan = [
        # С 6.5.2 выборки живут в telemetry_http_aggregates.latency_samples; здесь — строки до 6.5.2,
        # которые переносятся при сбросе своего ключа или удаляются очисткой через 7 дней
        ("Диагностика HTTP (выборки до 6.5.2)", "telemetry_http_samples", "created_at", "epoch"),
        ("События телеметрии", "telemetry_events", "timestamp", "text"),
        ("Агрегаты HTTP", "telemetry_http_aggregates", "bucket_start", "text"),
        ("История качества обходов", "source_scans", "finished_at", "text"),
        ("Аналитика поиска", "search_stats", "bucket", "text"),
        ("Запросы с текстом", "search_queries", "bucket", "text"),
        ("Расходы AI", "ai_usage", "day", "text"),
        ("Суточные сводки", "daily_reports", "day", "text"),
        ("Спорные пары сопоставления", "matching_pairs", "last_seen", "text"),
        ("История цен", "price_observations", "observed_at", "text"),
        ("Очередь уведомлений", "notification_outbox", "created_at", "epoch"),
    ]
    rows: List[Dict[str, Any]] = []
    with get_connection() as conn:
        for label, table, column, kind in plan:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                oldest = conn.execute(f"SELECT MIN({column}) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue
            if kind == "epoch" and oldest:
                try:
                    oldest = datetime.datetime.fromtimestamp(float(oldest),
                                                             datetime.timezone.utc).isoformat()
                except (TypeError, ValueError):
                    oldest = None
            rows.append({"data": label, "table": table, "rows": count, "oldest": oldest})
    return rows


# ---------------------------------------------------------------------------
# Журнал задач записи товаров (Product Spool), C01–C05 и R01–R06 повторной приёмки.
#
# Контракт: задача принята, только если строка вставлена в той же транзакции, где проверены
# аренда планировщика у поставщика и бюджет журнала. Принятую задачу завершает любой законный
# воркер: захват (claim) — строка с владельцем, одноразовым токеном и сроком. Каждая транзакция
# эффекта сверяет токен, поэтому воркер, у которого захват перехватили, ничего не запишет.
# Товары пишутся порциями; цены до записи сохраняются рядом, и повтор производных эффектов
# (алерты, наблюдения) считает переход от них. Подтверждение удаляет строку по токену.
# ---------------------------------------------------------------------------

SPOOL_CLAIM_SECONDS = 60.0
# Размер порции НОВЫХ задач. Уже принятая задача пишется порциями своего размера (колонка
# chunk_size), иначе смена константы меняет смысл сохранённого core_done (T01).
SPOOL_CHUNK_SIZE = 100
# Размер порций задач, принятых до появления chunk_size (6.4.3–6.4.11), и единственный размер,
# который понимают эти версии при откате (release_gate проверяет это перед откатом).
LEGACY_SPOOL_CHUNK_SIZE = 200
SPOOL_TASK_CHUNK_SIZE_SUPPORTED = True  # метка для release_gate: код читает chunk_size задачи
SPOOL_ACTIVE = ("pending", "processing")
SPOOL_UNRESOLVED = ("dead_letter", "rejected")   # rejected — строки прежней версии (C05)


def _spool_claim_row(conn, task_id: str, token: str):
    return conn.execute(
        "SELECT stage, core_done, created_at FROM product_queue_spool "
        "WHERE task_id = ? AND claim_token = ? AND status = 'processing'", (task_id, token)).fetchone()


def spool_admit(task_id: str, shop_key: Optional[str], shop_name: str, scan_id: Optional[str],
                payload: Dict[str, Any], lease_owner: Optional[str] = None,
                shop_budget: int = 200, total_budget: int = 2000, lease_name: str = "scan") -> str:
    """Принимает задачу одной транзакцией: аренда, бюджет и вставка (R02, R04).

    Возвращает 'accepted', 'full' (бюджет исчерпан, можно повторить позже) или 'fenced'
    (аренда планировщика не у поставщика — его результаты не принимаются). Ошибка базы
    пробрасывается: принятие без записи в журнал не считается принятием.
    """
    now = time.time()
    payload_str = json.dumps(payload, ensure_ascii=False)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if lease_owner is not None:
            lease = conn.execute("SELECT owner, expires_at FROM scheduler_lease WHERE name = ?",
                                 (lease_name,)).fetchone()
            if not lease or lease[0] != lease_owner or lease[1] <= now:
                conn.rollback()
                return "fenced"
        marks = ",".join("?" * len(SPOOL_ACTIVE))
        total = conn.execute(f"SELECT COUNT(*) FROM product_queue_spool WHERE status IN ({marks})",
                             SPOOL_ACTIVE).fetchone()[0]
        per_shop = conn.execute(f"SELECT COUNT(*) FROM product_queue_spool WHERE status IN ({marks}) "
                                f"AND shop_key IS ?", (*SPOOL_ACTIVE, shop_key)).fetchone()[0]
        if total >= total_budget or per_shop >= shop_budget:
            conn.rollback()
            return "full"
        conn.execute("""
            INSERT INTO product_queue_spool (task_id, shop_key, shop_name, scan_id, lease_generation, payload,
                                             status, attempts, created_at, updated_at, chunk_size)
            VALUES (?, ?, ?, ?, 0, ?, 'pending', 0, ?, ?, ?)
        """, (task_id, shop_key, shop_name, scan_id, payload_str, now, now, SPOOL_CHUNK_SIZE))
        conn.commit()
    return "accepted"


def spool_claim(owner: str, ttl: float = SPOOL_CLAIM_SECONDS) -> Optional[Dict[str, Any]]:
    """Захватывает старейшую доступную задачу: pending или processing с истёкшим захватом (R03, R04).

    Задачи одного магазина выполняются по порядку приёма: пока более ранняя задача магазина
    не завершена, следующая не выдаётся — старые данные не перезапишут более новые.
    """
    now = time.time()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT s.id, s.task_id, s.shop_key, s.shop_name, s.scan_id, s.payload, s.attempts, s.stage, s.core_done,
                   s.chunk_size
            FROM product_queue_spool s
            WHERE (s.status = 'pending' OR (s.status = 'processing' AND s.claim_expires_at < ?))
              AND NOT EXISTS (SELECT 1 FROM product_queue_spool o
                              WHERE o.shop_key IS s.shop_key AND o.id < s.id
                                AND o.status IN ('pending', 'processing'))
            ORDER BY s.id ASC LIMIT 1
        """, (now,)).fetchone()
        if not row:
            conn.rollback()
            return None
        token = secrets.token_hex(16)
        attempts = row["attempts"] + 1
        conn.execute("""
            UPDATE product_queue_spool
            SET status = 'processing', claimed_by = ?, claim_token = ?, claim_expires_at = ?,
                attempts = ?, updated_at = ?
            WHERE id = ?
        """, (owner, token, now + ttl, attempts, now, row["id"]))
        conn.commit()
    try:
        item = json.loads(row["payload"])
    except (TypeError, ValueError):
        item = {}
    item.update(task_id=row["task_id"], shop_key=row["shop_key"], shop_name=row["shop_name"],
                scan_id=row["scan_id"], attempts=attempts, stage=row["stage"], core_done=row["core_done"],
                chunk_size=row["chunk_size"] or LEGACY_SPOOL_CHUNK_SIZE,
                claim_token=token)
    return item


def spool_renew(task_id: str, token: str, ttl: float = SPOOL_CLAIM_SECONDS) -> bool:
    """Продлевает захват; False — захват уже не наш (перехвачен или задача завершена)."""
    now = time.time()
    with get_connection() as conn:
        cur = conn.execute("UPDATE product_queue_spool SET claim_expires_at = ?, updated_at = ? "
                           "WHERE task_id = ? AND claim_token = ? AND status = 'processing'",
                           (now + ttl, now, task_id, token))
        conn.commit()
        return cur.rowcount == 1


def spool_apply_chunk(task_id: str, token: str, chunk_no: int, chunk: List[Dict[str, Any]],
                      ttl: float = SPOOL_CLAIM_SECONDS) -> bool:
    """Пишет порцию товаров и её прежние цены одной транзакцией под проверкой захвата (R04, R05).

    False — захват потерян, ничего не записано. Уже записанная порция повторно не пишется.
    """
    # Подготовка — до захвата блокировки: под ней чистый Python ждёт GIL наравне с обходом (6.5.1)
    prepared: Optional[List[Optional[Dict[str, Any]]]] = None
    prepare_error: Optional[BaseException] = None
    try:
        prepared = _prepare_products_chunk(chunk)
    except Exception as e:
        prepare_error = e  # как прежде: всплывает только при живом захвате и непройденной порции
    now = time.time()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            claim = _spool_claim_row(conn, task_id, token)
            if not claim:
                conn.rollback()
                return False
            if claim["stage"] != "core" or claim["core_done"] > chunk_no:
                conn.rollback()
                return True
            if prepare_error is not None:
                raise prepare_error
            history = _price_history_conn(conn, [str(p["id"]) for p in chunk])
            # Метка наблюдения — момент приёма задачи: повтор давнего отказа не откатит товар,
            # уже записанный более поздним обходом (F01)
            _save_products_chunk(conn, chunk, now_iso, claim["created_at"], prepared=prepared)
            conn.execute("INSERT OR REPLACE INTO product_queue_history (task_id, chunk_no, history) VALUES (?, ?, ?)",
                         (task_id, chunk_no, json.dumps(history, ensure_ascii=False)))
            conn.execute("UPDATE product_queue_spool SET core_done = ?, claim_expires_at = ?, updated_at = ? "
                         "WHERE task_id = ?", (chunk_no + 1, now + ttl, now, task_id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return True


def spool_finish_core(task_id: str, token: str, product_ids: List[str],
                      reconcile: Optional[Dict[str, Any]], scan_log: Optional[Dict[str, Any]],
                      ttl: float = SPOOL_CLAIM_SECONDS) -> bool:
    """Сверка источника, журнал обхода и переход к производным эффектам — одна транзакция (R04, C04)."""
    now = time.time()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            claim = _spool_claim_row(conn, task_id, token)
            if not claim:
                conn.rollback()
                return False
            if claim["stage"] != "core":
                conn.rollback()
                return True
            retired: List[str] = []
            if reconcile:
                retired = _reconcile_source_conn(conn, reconcile["key"], reconcile["url"], product_ids,
                                                 reconcile.get("complete", False), claim["created_at"]) or []
            if scan_log:
                _record_source_scan_conn(conn, scan_log["key"], scan_log["url"], scan_log["name"],
                                         scan_log["scan_id"], scan_log["cat_started"], scan_log["kind"],
                                         scan_log["assessment"], scan_log["metrics"])
            conn.execute("UPDATE product_queue_spool SET stage = 'derived', retired = ?, claim_expires_at = ?, "
                         "updated_at = ? WHERE task_id = ?",
                         (json.dumps(retired) if retired else None, now + ttl, now, task_id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    if reconcile:
        invalidate_alerts_cache()
    return True


def spool_history(task_id: str) -> Dict[str, Dict[str, Any]]:
    """Цены товаров задачи до их записи (по всем порциям)."""
    merged: Dict[str, Dict[str, Any]] = {}
    with get_connection() as conn:
        for row in conn.execute("SELECT history FROM product_queue_history WHERE task_id = ? ORDER BY chunk_no",
                                (task_id,)):
            merged.update(json.loads(row[0]))
    return merged


def spool_retired(task_id: str) -> List[str]:
    """Товары, снятые полной сверкой этой задачи: наблюдения узнают, что их больше нет (G01)."""
    with get_connection() as conn:
        row = conn.execute("SELECT retired FROM product_queue_spool WHERE task_id = ?", (task_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else []


def spool_superseded(task_id: str, product_ids: List[str]) -> List[str]:
    """Товары задачи, которые уже обновлены более свежим наблюдением, чем эта задача (F01).

    Для них производные эффекты (алерты, наблюдения) не считаются: переход цены устарел,
    и уведомление по нему сообщило бы то, чего уже нет.
    """
    ids = [str(p) for p in product_ids]
    superseded: List[str] = []
    with get_connection() as conn:
        row = conn.execute("SELECT created_at FROM product_queue_spool WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return superseded
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            superseded.extend(r[0] for r in conn.execute(
                f"SELECT id FROM products WHERE id IN ({marks}) AND observed_seq > ?", (*chunk, row[0])))
    return superseded


def spool_ack(task_id: str, token: str) -> bool:
    """Подтверждение: удаляет задачу и её историю, только если захват наш (R01, R04)."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM product_queue_spool WHERE task_id = ? AND claim_token = ? "
                           "AND status = 'processing'", (task_id, token))
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.execute("DELETE FROM product_queue_history WHERE task_id = ?", (task_id,))
        conn.commit()
    return True


def spool_nack(task_id: str, token: str, error: Optional[str] = None, max_retries: int = 3) -> Optional[str]:
    """Освобождает захват для повтора или переводит задачу в dead_letter. Возвращает новый статус."""
    now = time.time()
    err_str = (str(error) if error else "Execution error")[:500]
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT attempts FROM product_queue_spool WHERE task_id = ? AND claim_token = ? "
                           "AND status = 'processing'", (task_id, token)).fetchone()
        if not row:
            conn.rollback()
            return None
        status = "dead_letter" if row[0] > max_retries else "pending"
        conn.execute("""
            UPDATE product_queue_spool
            SET status = ?, last_error = ?, claimed_by = NULL, claim_token = NULL, claim_expires_at = 0,
                updated_at = ?
            WHERE task_id = ?
        """, (status, err_str, now, task_id))
        conn.commit()
    return status


def spool_recover_orphans(owner_is_dead) -> int:
    """Освобождает захваты доказанно погибших владельцев (R03, R04).

    Живой чужой захват не трогается: его заберут только после истечения срока, если владелец
    перестанет продлевать. owner_is_dead(owner) решает по хосту и PID владельца.
    """
    now = time.time()
    released = 0
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT task_id, claimed_by, claim_expires_at FROM product_queue_spool "
                            "WHERE status = 'processing'").fetchall()
        for row in rows:
            if row["claim_expires_at"] < now or not row["claimed_by"] or owner_is_dead(row["claimed_by"]):
                conn.execute("UPDATE product_queue_spool SET claim_expires_at = 0, updated_at = ? WHERE task_id = ?",
                             (now, row["task_id"]))
                released += 1
        conn.commit()
    return released


def spool_task_states(task_ids: List[str]) -> Dict[str, str]:
    """Статусы задач по ID; завершённых (удалённых) задач в ответе нет."""
    states: Dict[str, str] = {}
    ids = [str(t) for t in task_ids if t]
    with get_connection() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(f"SELECT task_id, status FROM product_queue_spool WHERE task_id IN ({marks})",
                                    chunk):
                states[row[0]] = row[1]
    return states


def spool_stats(shop_key: Optional[str] = None) -> Dict[str, int]:
    """Число задач журнала по статусам. Ошибка чтения пробрасывается (R06)."""
    stats = {"pending": 0, "processing": 0, "dead_letter": 0, "rejected": 0}
    with get_connection() as conn:
        sql = "SELECT status, COUNT(*) FROM product_queue_spool"
        params: List[Any] = []
        if shop_key:
            sql += " WHERE shop_key = ?"
            params.append(shop_key)
        sql += " GROUP BY status"
        for st, cnt in conn.execute(sql, params):
            if st in stats:
                stats[st] = cnt
    return stats


def spool_failures(limit: int = 50) -> List[Dict[str, Any]]:
    """Нерешённые отказы журнала: что не записано, по какому магазину и обходу, с какой ошибкой."""
    marks = ",".join("?" * len(SPOOL_UNRESOLVED))
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(f"""
            SELECT task_id, shop_key, shop_name, scan_id, status, stage, attempts, last_error, created_at, updated_at
            FROM product_queue_spool WHERE status IN ({marks}) ORDER BY id LIMIT ?
        """, (*SPOOL_UNRESOLVED, max(1, int(limit))))]


def spool_resolve_failures(action: str, task_ids: Optional[List[str]] = None) -> int:
    """Решение по отказам: 'retry' — вернуть в работу, 'archive' — снять как обработанные вручную.

    Архив отделяет исторические отказы от текущих: readiness видит только нерешённые (R06).
    """
    if action not in ("retry", "archive"):
        raise ValueError(f"Неизвестное действие: {action}")
    now = time.time()
    marks = ",".join("?" * len(SPOOL_UNRESOLVED))
    sql_set = ("status = 'pending', attempts = 0, claimed_by = NULL, claim_token = NULL, claim_expires_at = 0"
               if action == "retry" else "status = 'archived'")
    sql = f"UPDATE product_queue_spool SET {sql_set}, updated_at = ? WHERE status IN ({marks})"
    params: List[Any] = [now, *SPOOL_UNRESOLVED]
    if task_ids is not None:
        ids = [str(t) for t in task_ids if t]
        if not ids:
            return 0
        sql += f" AND task_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    with get_connection() as conn:
        count = conn.execute(sql, params).rowcount
        if action == "archive":
            conn.execute("DELETE FROM product_queue_history WHERE task_id IN "
                         "(SELECT task_id FROM product_queue_spool WHERE status = 'archived')")
        conn.commit()
    return count


def prune_spool_archive(days: int = 14) -> int:
    """Архивные отказы хранятся две недели: payload целой категории занимает место."""
    cutoff = time.time() - days * 86400
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM product_queue_spool WHERE status = 'archived' AND updated_at < ?",
                               (cutoff,)).rowcount
        conn.commit()
    return deleted
