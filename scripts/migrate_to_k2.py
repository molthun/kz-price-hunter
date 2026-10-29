"""Миграция базы кэша в стандарт k2 (P17-C4, docs/DEVELOPMENT_PLAN.md).

Переводит канонические ключи товаров из старого формата (Gemini / v1) в стандарт k2:
1. Выгружает названия из title_canonical_cache, у которых ключ ещё не в формате k2.
2. Для названий без сохранённых полей запрашивает у локальной модели поля по схеме (K2_NORMALIZE_PROMPT).
3. Сохраняет поля в title_fields, связывает по артикулам через resolve_keys_with_articles и сохраняет связи в title_articles.
4. Обновляет title_canonical_cache и products.canonical_key новыми ключами.
5. При --check-pairs сравнивает пары товаров между магазинами до и после миграции.

Запуск на машине стенда/прода:
    python3 scripts/migrate_to_k2.py --db /app/data/prices.db --base http://192.168.28.25:11434/v1 \
        --model qwen3-vl:8b-instruct --check-pairs

Режим только пересборки из уже сохранённых полей (без вызовов модели):
    python3 scripts/migrate_to_k2.py --db /app/data/prices.db --rescore-only

Пробный прогон без изменения базы:
    python3 scripts/migrate_to_k2.py --db /app/data/prices.db --limit 100 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def find_titles_to_migrate(conn: sqlite3.Connection, limit: Optional[int] = None) -> List[Tuple[str, str]]:
    """Список (title, canonical_key) из title_canonical_cache, где ключ не k2."""
    query = """
        SELECT title, canonical_key
        FROM title_canonical_cache
        WHERE canonical_key != '' AND canonical_key NOT LIKE 'k2:%'
        ORDER BY created_at ASC
    """
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"
    return conn.execute(query).fetchall()


def load_existing_fields(conn: sqlite3.Connection, titles: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Поля, которые уже были ранее сохранены в title_fields."""
    unique = list({t.strip() for t in titles if t and t.strip()})
    if not unique:
        return {}
    hashes = {hashlib.sha256(t.encode("utf-8")).hexdigest(): t for t in unique}
    res: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(hashes), 500):
        chunk = list(hashes)[i:i + 500]
        cursor = conn.execute("""
            SELECT title_hash, fields_json
            FROM title_fields
            WHERE title_hash IN (SELECT value FROM json_each(?))
        """, (json.dumps(chunk, ensure_ascii=False),))
        for row in cursor.fetchall():
            orig = hashes.get(row[0])
            if orig:
                try:
                    res[orig] = json.loads(row[1])
                except Exception:
                    pass
    return res


def load_products_shops(conn: sqlite3.Connection, titles: Iterable[str]) -> Dict[str, List[str]]:
    """Магазины для каждого названия (для проверки пар сравнения цен)."""
    unique = list({t.strip() for t in titles if t and t.strip()})
    if not unique:
        return {}
    res = defaultdict(list)
    for i in range(0, len(unique), 500):
        chunk = unique[i:i + 500]
        cursor = conn.execute("""
            SELECT title, shop
            FROM products
            WHERE title IN (SELECT value FROM json_each(?))
        """, (json.dumps(chunk, ensure_ascii=False),))
        for t, s in cursor.fetchall():
            if s and s not in res[t]:
                res[t].append(s)
    return dict(res)


async def fetch_fields_batch(
    titles: List[str],
    base: str,
    model: str,
    key: str = "",
    timeout: float = 120.0,
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """Запрашивает извлечение полей у локальной модели для пачки названий."""
    import ai_router
    import ai_service
    import product_key

    prompt = (ai_service.K2_NORMALIZE_PROMPT + "\n\n"
              + "Нормализуй товары, перечисленные в блоке данных ниже.\n"
              + ai_router.untrusted_block("\n".join(f"- {t}" for t in titles), "НАЗВАНИЯ"))
    reply = await ai_service._call_local_api(prompt, base, model, key, timeout)
    if not (isinstance(reply, dict) and isinstance(reply.get("items"), list)):
        return {}, 1

    allowed = set(titles)
    fields = {}
    for item in reply["items"]:
        if isinstance(item, dict) and item.get("title") in allowed:
            fields[item["title"]] = {k: item.get(k) for k in product_key.FIELDS_SCHEMA}
    return fields, 0


def resolve_batch_keys(
    conn: sqlite3.Connection,
    batch_fields: Dict[str, Dict[str, Any]],
) -> Dict[str, Optional[str]]:
    """Разрешает ключи для пачки с связыванием по артикулам и сохранением fixed."""
    import database
    import product_key

    articles = set()
    for f in batch_fields.values():
        for art in product_key.article_tokens(f):
            articles.add(art)

    # Ищем существующие записи в title_articles
    existing = []
    if articles:
        cursor = conn.execute("""
            SELECT a.article, a.title_hash, a.canonical_key, f.title, f.fields_json
            FROM title_articles a
            JOIN title_fields f ON f.title_hash = a.title_hash
            WHERE a.article IN (SELECT value FROM json_each(?))
        """, (json.dumps(list(articles), ensure_ascii=False),))
        for r in cursor.fetchall():
            try:
                f_dict = json.loads(r[4])
            except Exception:
                f_dict = {}
            existing.append({
                "article": r[0], "title_hash": r[1], "canonical_key": r[2],
                "title": r[3], "fields": f_dict,
            })

    return product_key.resolve_keys_with_articles(batch_fields, existing)


def save_migrated_batch(
    conn: sqlite3.Connection,
    batch_fields: Dict[str, Dict[str, Any]],
    keys: Dict[str, Optional[str]],
    model_name: str,
    dry_run: bool = False,
) -> None:
    """Сохраняет title_fields, title_articles, title_canonical_cache и products.canonical_key."""
    import product_key

    if dry_run or not batch_fields:
        return

    # 1. title_fields
    fields_rows = [
        (hashlib.sha256(t.strip().encode("utf-8")).hexdigest(), t.strip(),
         json.dumps(f, ensure_ascii=False, sort_keys=True)[:2000], product_key.KEY_VERSION, "local", model_name)
        for t, f in batch_fields.items() if t and isinstance(f, dict)
    ]
    if fields_rows:
        conn.execute("""
            INSERT INTO title_fields (title_hash, title, fields_json, key_version, provider, model)
            SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]'),
                   json_extract(value, '$[3]'), json_extract(value, '$[4]'), json_extract(value, '$[5]')
            FROM json_each(?) WHERE true
            ON CONFLICT(title_hash) DO UPDATE SET fields_json = excluded.fields_json,
                key_version = excluded.key_version, provider = excluded.provider, model = excluded.model,
                created_at = CURRENT_TIMESTAMP
        """, (json.dumps(fields_rows, ensure_ascii=False),))

    # 2. title_articles и title_canonical_cache
    articles_rows = []
    cache_rows = []
    prod_updates = []
    for t, k in keys.items():
        if not k:
            continue
        th = hashlib.sha256(t.strip().encode("utf-8")).hexdigest()
        cache_rows.append((th, t.strip(), k))
        prod_updates.append((k, t.strip()))
        for art in product_key.article_tokens(batch_fields.get(t)):
            articles_rows.append((art.strip().lower(), th, k))

    if cache_rows:
        conn.execute("""
            INSERT INTO title_canonical_cache (title_hash, title, canonical_key)
            SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]')
            FROM json_each(?) WHERE true
            ON CONFLICT(title_hash) DO UPDATE SET canonical_key = excluded.canonical_key
        """, (json.dumps(cache_rows, ensure_ascii=False),))

    if articles_rows:
        conn.execute("""
            INSERT INTO title_articles (article, title_hash, canonical_key)
            SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]')
            FROM json_each(?) WHERE true
            ON CONFLICT(article, title_hash) DO UPDATE SET canonical_key = excluded.canonical_key,
                created_at = CURRENT_TIMESTAMP
        """, (json.dumps(articles_rows, ensure_ascii=False),))

    if prod_updates:
        conn.executemany("UPDATE products SET canonical_key = ? WHERE title = ?", prod_updates)

    conn.commit()


def check_pairs_comparison(
    old_keys: Dict[str, str],
    new_keys: Dict[str, str],
    title_shops: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Сравнивает пары (title_a, title_b) из разных магазинов, склеенные до и после."""
    # Собираем пары старых ключей
    old_groups = defaultdict(set)
    for t, k in old_keys.items():
        if k and title_shops.get(t):
            old_groups[k].add(t)

    old_pairs: Set[Tuple[str, str]] = set()
    for ts in old_groups.values():
        tlist = sorted(ts)
        for i in range(len(tlist)):
            for j in range(i + 1, len(tlist)):
                a, b = tlist[i], tlist[j]
                # Считаем парой только если товары пересекаются по магазинам (из разных магазинов)
                shops_a = set(title_shops.get(a, []))
                shops_b = set(title_shops.get(b, []))
                if shops_a - shops_b or shops_b - shops_a:
                    old_pairs.add((a, b))

    # Собираем пары новых ключей
    new_groups = defaultdict(set)
    for t, k in new_keys.items():
        if k and title_shops.get(t):
            new_groups[k].add(t)

    new_pairs: Set[Tuple[str, str]] = set()
    for ts in new_groups.values():
        tlist = sorted(ts)
        for i in range(len(tlist)):
            for j in range(i + 1, len(tlist)):
                a, b = tlist[i], tlist[j]
                shops_a = set(title_shops.get(a, []))
                shops_b = set(title_shops.get(b, []))
                if shops_a - shops_b or shops_b - shops_a:
                    new_pairs.add((a, b))

    kept = old_pairs & new_pairs
    dropped = old_pairs - new_pairs
    added = new_pairs - old_pairs

    return {
        "old_pairs_count": len(old_pairs),
        "new_pairs_count": len(new_pairs),
        "retained_pairs_count": len(kept),
        "retention_rate": round(len(kept) / len(old_pairs), 4) if old_pairs else 1.0,
        "dropped_pairs_count": len(dropped),
        "added_pairs_count": len(added),
        "dropped_examples": [{"a": a, "b": b, "old_key": old_keys.get(a),
                              "new_key_a": new_keys.get(a), "new_key_b": new_keys.get(b)}
                             for a, b in list(dropped)[:10]],
    }


async def run_migration(
    db_path: str,
    base: str,
    model: str,
    batch_size: int = 20,
    limit: Optional[int] = None,
    dry_run: bool = False,
    rescore_only: bool = False,
    check_pairs: bool = False,
    out_file: Optional[str] = None,
) -> Dict[str, Any]:
    conn = get_db_connection(db_path)
    try:
        raw_items = find_titles_to_migrate(conn, limit=limit)
        print(f"[C4] Найдено названий для миграции: {len(raw_items)}")
        if not raw_items:
            return {"total": 0, "status": "nothing_to_migrate"}

        titles_all = [t for t, _ in raw_items]
        old_keys = dict(raw_items)

        # Проверяем, у кого поля уже есть
        saved_fields = load_existing_fields(conn, titles_all)
        print(f"[C4] Из них уже сохранены поля: {len(saved_fields)}")

        title_shops = load_products_shops(conn, titles_all) if check_pairs else {}

        all_new_keys: Dict[str, str] = {}
        all_fields: Dict[str, Dict[str, Any]] = dict(saved_fields)
        api_failures = 0
        api_calls = 0
        started = time.monotonic()

        # Разделяем на те, что требуют вызова модели, и те, у кого поля уже есть
        missing_titles = [t for t in titles_all if t not in saved_fields]
        if rescore_only and missing_titles:
            print(f"[C4] --rescore-only: пропуск {len(missing_titles)} названий без полей")
            titles_to_process = [t for t in titles_all if t in saved_fields]
        else:
            titles_to_process = titles_all

        print(f"[C4] Обработка {len(titles_to_process)} названий пачками по {batch_size}...")

        for i in range(0, len(titles_to_process), batch_size):
            chunk = titles_to_process[i:i + batch_size]
            chunk_fields: Dict[str, Dict[str, Any]] = {}

            # Собираем поля: берем сохранённые или запрашиваем у модели
            to_fetch = [t for t in chunk if t not in all_fields]
            if to_fetch:
                fetched, fails = await fetch_fields_batch(to_fetch, base, model)
                api_calls += 1
                api_failures += fails
                all_fields.update(fetched)
                chunk_fields.update(fetched)

            for t in chunk:
                if t in all_fields:
                    chunk_fields[t] = all_fields[t]

            # Разрешаем ключи для пачки
            keys = resolve_batch_keys(conn, chunk_fields)
            for t, k in keys.items():
                if k:
                    all_new_keys[t] = k

            # Сохраняем в БД
            save_migrated_batch(conn, chunk_fields, keys, model, dry_run=dry_run)

            done = min(i + batch_size, len(titles_to_process))
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(titles_to_process) - done) / rate if rate > 0 else 0
            if done % 100 == 0 or done == len(titles_to_process):
                print(f"[C4] Прогресс: {done}/{len(titles_to_process)} ({done*100//len(titles_to_process)}%) | "
                      f"Скорость: {rate:.1f} наим/с | ETA: {eta:.0f} с | С ключом k2: {len(all_new_keys)}")

        total_elapsed = time.monotonic() - started
        result: Dict[str, Any] = {
            "total_titles": len(titles_all),
            "processed_titles": len(titles_to_process),
            "with_k2_key": len(all_new_keys),
            "api_calls": api_calls,
            "api_failures": api_failures,
            "seconds": round(total_elapsed, 1),
            "dry_run": dry_run,
        }

        if check_pairs:
            print("[C4] Расчёт сравнения пар до и после миграции...")
            pairs_stat = check_pairs_comparison(old_keys, all_new_keys, title_shops)
            result["pairs_comparison"] = pairs_stat
            print(f"[C4] Пар было: {pairs_stat['old_pairs_count']} | Стало: {pairs_stat['new_pairs_count']} | "
                  f"Сохранилось: {pairs_stat['retained_pairs_count']} ({pairs_stat['retention_rate']*100:.1f}%) | "
                  f"Разошлось: {pairs_stat['dropped_pairs_count']} | Новых: {pairs_stat['added_pairs_count']}")

        if out_file:
            Path(out_file).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[C4] Отчёт сохранён в {out_file}")

        return result
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Миграция ключей товаров в формат k2 (P17-C4)")
    ap.add_argument("--db", default="prices.db", help="Путь к SQLite базе данных")
    ap.add_argument("--base", default="http://192.168.28.25:11434/v1", help="URL Ollama/OpenAI API")
    ap.add_argument("--model", default="qwen3-vl:8b-instruct", help="Имя локальной модели")
    ap.add_argument("--batch-size", type=int, default=20, help="Размер пачки")
    ap.add_argument("--limit", type=int, default=None, help="Ограничение количества названий")
    ap.add_argument("--dry-run", action="store_true", help="Только расчёт без сохранения изменений в БД")
    ap.add_argument("--rescore-only", action="store_true", help="Пересборка только названий с сохранёнными полями")
    ap.add_argument("--check-pairs", action="store_true", help="Сравнение пар предложений до и после")
    ap.add_argument("--out", default=None, help="Путь к JSON-файлу отчёта")
    args = ap.parse_args()

    asyncio.run(run_migration(
        db_path=args.db,
        base=args.base,
        model=args.model,
        batch_size=args.batch_size,
        limit=args.limit,
        dry_run=args.dry_run,
        rescore_only=args.rescore_only,
        check_pairs=args.check_pairs,
        out_file=args.out,
    ))


if __name__ == "__main__":
    main()
