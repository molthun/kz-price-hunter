"""Миграция базы кэша в стандарт k2 (P17-C4, docs/DEVELOPMENT_PLAN.md).

Переводит канонические ключи товаров из старого формата (Gemini / v1) в стандарт k2:
1. Выгружает названия из title_canonical_cache, у которых ключ ещё не в формате k2.
2. Для названий без сохранённых полей запрашивает у локальной модели поля по схеме (K2_NORMALIZE_PROMPT).
3. Сохраняет поля в title_fields, связывает по артикулам через resolve_keys_with_articles и сохраняет связи в title_articles.
4. Обновляет title_canonical_cache и products.canonical_key новыми ключами.
5. Название без подтверждённой идентичности (нет бренда) теряет прежний общий ключ и не сравнивается автоматически.
6. При --check-pairs сравнивает пары товаров между магазинами до и после миграции.

Исходный снимок ключей фиксируется при первом запуске прохода (--run-id) и не меняется при возобновлении: отчёт
всегда сравнивает весь исходный набор с фактическими ключами, а ошибки и пропуски накапливаются (аудит C45-08).
--verified-set добавляет оценку по проверенным парам (критерии C2).

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


def get_db_connection(db_path: str, read_only: bool = False) -> sqlite3.Connection:
    """Пробный прогон открывает базу только для чтения и не меняет режим журнала (замечание аудита P17-C)."""
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
    else:
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def find_titles_to_migrate(conn: sqlite3.Connection, limit: Optional[int] = None,
                           rescore_only: bool = False) -> List[Tuple[str, str]]:
    """Список (title, canonical_key) из title_canonical_cache.

    Первичная миграция — ключи ещё не в k2. Пересборка (--rescore-only) — все названия с сохранёнными полями,
    включая уже переведённые в k2: она нужна именно при смене правил ключа (P17C-06)."""
    if rescore_only:
        query = """
            SELECT c.title, c.canonical_key
            FROM title_canonical_cache c JOIN title_fields f ON f.title_hash = c.title_hash
            ORDER BY c.created_at ASC, c.title ASC
        """
    else:
        query = """
            SELECT title, canonical_key
            FROM title_canonical_cache
            WHERE canonical_key != '' AND canonical_key NOT LIKE 'k2:%'
            ORDER BY created_at ASC, title ASC
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
    """Запрашивает извлечение полей у локальной модели для пачки названий.

    Через общий путь call_local_api: «AI выкл» и проверка видеокарты при игре соблюдаются, один запрос
    к модели за раз, учёт — как у фоновой нормализации (P17C-05)."""
    import ai_router
    import ai_service
    import product_key

    prompt = (ai_service.K2_NORMALIZE_PROMPT + "\n\n"
              + "Нормализуй товары, перечисленные в блоке данных ниже.\n"
              + ai_router.untrusted_block("\n".join(f"- {t}" for t in titles), "НАЗВАНИЯ"))
    reply = await ai_service.call_local_api(prompt, base, model, key, timeout=timeout, scan=True)
    if not (isinstance(reply, dict) and isinstance(reply.get("items"), list)):
        return {}, 1

    allowed = set(titles)
    fields = {}
    for item in reply["items"]:
        if isinstance(item, dict) and item.get("title") in allowed:
            fields[item["title"]] = {k: item.get(k) for k in product_key.FIELDS_SCHEMA}
    return fields, 0


def _pair_decisions(conn: sqlite3.Connection, hashes: List[str]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for i in range(0, len(hashes), 500):
        for ph, decision in conn.execute(
                "SELECT pair_hash, decision FROM product_pair_verdicts WHERE pair_hash IN (SELECT value FROM json_each(?))",
                (json.dumps(hashes[i:i + 500]),)):
            found[ph] = decision
    return found


def resolve_batch_keys(
    conn: sqlite3.Connection,
    batch_fields: Dict[str, Dict[str, Any]],
    planned: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Optional[str]]:
    """Разрешает ключи для пачки с связыванием по артикулам и сохранением fixed.

    planned — названия прошлых пачек пробного прогона ({title: {"fields", "canonical_key"}}): пробный прогон
    ничего не пишет, поэтому их связи учитываются из памяти, и прогноз совпадает с настоящим проходом.
    Сохранённые вердикты «разные / один товар» учитываются так же, как в конвейере (P17C-01)."""
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

    if planned:
        known = {item["title"] for item in existing}
        for title, item in planned.items():
            if title not in known and title not in batch_fields and \
                    articles.intersection(product_key.article_tokens(item["fields"])):
                existing.append({"title": title, "fields": item["fields"], "canonical_key": item["canonical_key"]})

    return product_key.resolve_keys_with_articles(
        batch_fields, existing, pair_verdicts=lambda hashes: _pair_decisions(conn, hashes))


def save_migrated_batch(
    conn: sqlite3.Connection,
    batch_fields: Dict[str, Dict[str, Any]],
    keys: Dict[str, Optional[str]],
    model_name: str,
    dry_run: bool = False,
) -> None:
    """Сохраняет title_fields, title_articles и переносит ключи названий одной транзакцией на пачку.

    Перенос ключа — database.rekey_titles_conn: кэш ключей, products и V2 (offers, канонический товар,
    история цен) вместе, иначе поиск V2 остаётся на старых группах (P17C-02)."""
    if dry_run or not batch_fields:
        return
    try:
        _save_migrated_batch(conn, batch_fields, keys, model_name)
    except BaseException:
        conn.rollback()                             # пачка применяется целиком или не применяется
        raise


def _save_migrated_batch(conn, batch_fields, keys, model_name) -> None:
    import database
    import product_key

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

    # 2. title_articles и перенос ключей
    articles_rows = []
    for t, k in keys.items():
        if not k:
            continue
        th = hashlib.sha256(t.strip().encode("utf-8")).hexdigest()
        for art in product_key.article_tokens(batch_fields.get(t)):
            articles_rows.append((art.strip().lower(), th, k))

    if articles_rows:
        conn.execute("""
            INSERT INTO title_articles (article, title_hash, canonical_key)
            SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]')
            FROM json_each(?) WHERE true
            ON CONFLICT(article, title_hash) DO UPDATE SET canonical_key = excluded.canonical_key,
                created_at = CURRENT_TIMESTAMP
        """, (json.dumps(articles_rows, ensure_ascii=False),))

    database.rekey_titles_conn(conn, {t: k for t, k in keys.items() if k})
    database.clear_title_identity_conn(conn, [t for t, k in keys.items() if not k and t in batch_fields
                                              and product_key.no_key_reason(batch_fields[t]) == "no_identity"])
    conn.commit()


def _cross_shop(shops_a: Set[str], shops_b: Set[str]) -> bool:
    """Два названия можно сравнить по цене между магазинами: есть магазин у A и другой магазин у B.

    Разность множеств магазинов для этого не годится: у A и B с одинаковыми {shop1, shop2} она пуста,
    хотя пары предложений shop1↔shop2 существуют (P17C-07)."""
    return bool(shops_a) and bool(shops_b) and len(shops_a | shops_b) >= 2


def _pairs(keys: Dict[str, str], title_shops: Dict[str, List[str]]) -> Set[Tuple[str, str]]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for t, k in keys.items():
        if k and title_shops.get(t):
            groups[k].append(t)
    pairs: Set[Tuple[str, str]] = set()
    for ts in groups.values():
        tlist = sorted(ts)
        for i, a in enumerate(tlist):
            for b in tlist[i + 1:]:
                if _cross_shop(set(title_shops[a]), set(title_shops[b])):
                    pairs.add((a, b))
    return pairs


def check_pairs_comparison(
    old_keys: Dict[str, str],
    new_keys: Dict[str, str],
    title_shops: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Сохранность сравнения цен: пары названий, сравнимых между разными магазинами, до и после.

    Единица метрики — пара названий с общим ключом, у которых есть предложения хотя бы в двух разных
    магазинах (одно название в нескольких магазинах — само по себе не пара). Без пар до миграции
    сохранность не определена: retention_rate = None и valid = False, а не 100 %.
    Названия, ключ которых миграция не дала, считаются разошедшимися."""
    old_pairs = _pairs(old_keys, title_shops)
    new_pairs = _pairs(new_keys, title_shops)
    kept = old_pairs & new_pairs
    dropped = old_pairs - new_pairs
    added = new_pairs - old_pairs
    return {
        "unit": "пары названий с предложениями хотя бы в двух разных магазинах",
        "valid": bool(old_pairs),
        "old_pairs_count": len(old_pairs),
        "new_pairs_count": len(new_pairs),
        "retained_pairs_count": len(kept),
        "retention_rate": round(len(kept) / len(old_pairs), 4) if old_pairs else None,
        "dropped_pairs_count": len(dropped),
        "added_pairs_count": len(added),
        "dropped_examples": [{"a": a, "b": b, "old_key": old_keys.get(a),
                              "new_key_a": new_keys.get(a), "new_key_b": new_keys.get(b)}
                             for a, b in sorted(dropped)[:10]],
    }


def _baseline_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS k2_migration_runs (
        run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, api_calls INTEGER NOT NULL DEFAULT 0,
        api_failures INTEGER NOT NULL DEFAULT 0, failed_titles INTEGER NOT NULL DEFAULT 0,
        missing_titles INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS k2_migration_baseline (
        run_id TEXT NOT NULL, title TEXT NOT NULL, old_key TEXT NOT NULL, PRIMARY KEY (run_id, title))""")


def load_baseline(conn: sqlite3.Connection, run_id: str, read_only: bool) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Неизменяемый снимок {название: ключ} всего кэша на первый запуск прохода и его счётчики.

    Пробный прогон ничего не пишет: снимок берётся из памяти, если прохода ещё не было."""
    has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'k2_migration_runs'").fetchone()
    if not read_only:
        _baseline_tables(conn)
    run = conn.execute("SELECT created_at, api_calls, api_failures, failed_titles, missing_titles, seconds "
                       "FROM k2_migration_runs WHERE run_id = ?", (run_id,)).fetchone() if has_tables or not read_only else None
    names = ("created_at", "api_calls", "api_failures", "failed_titles", "missing_titles", "seconds")
    if run:
        rows = conn.execute("SELECT title, old_key FROM k2_migration_baseline WHERE run_id = ?", (run_id,)).fetchall()
        return dict(rows), dict(zip(names, run))
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = conn.execute("SELECT title, canonical_key FROM title_canonical_cache").fetchall()
    if not read_only:
        conn.execute("INSERT INTO k2_migration_runs (run_id, created_at) VALUES (?, ?)", (run_id, created))
        conn.executemany("INSERT INTO k2_migration_baseline (run_id, title, old_key) VALUES (?, ?, ?)",
                         [(run_id, t, k) for t, k in rows])
        conn.commit()
    return dict(rows), dict(zip(names, (created, 0, 0, 0, 0, 0.0)))


def actual_keys(conn: sqlite3.Connection, titles: Iterable[str], planned: Dict[str, str]) -> Dict[str, str]:
    """Фактические ключи названий сейчас; planned — поверх них в пробном прогоне (пусто — ключа нет)."""
    found: Dict[str, str] = {}
    unique = list(titles)
    for i in range(0, len(unique), 500):
        for t, k in conn.execute("SELECT title, canonical_key FROM title_canonical_cache WHERE title IN "
                                 "(SELECT value FROM json_each(?))", (json.dumps(unique[i:i + 500], ensure_ascii=False),)):
            found[t] = k
    found.update(planned)
    return found


def evaluate_verified_pairs(path: str, keys: Dict[str, str]) -> Dict[str, Any]:
    """Оценка по проверенным парам (критерии C2): полнота на положительных и ложные склейки на отрицательных.

    Пары, которых нет в этой базе или у которых нет ключа, считаются отдельно и в долю полноты не входят как успех."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tp, fn, tn, false_merges, absent = 0, 0, 0, [], 0
    for pair in data["pairs"]:
        a, b = pair["a"], pair["b"]
        if a not in keys or b not in keys:
            absent += 1
        elif pair["same"]:
            if keys[a] and keys[a] == keys[b]:
                tp += 1
            else:
                fn += 1
        elif keys[a] and keys[a] == keys[b]:
            false_merges.append({"a": a, "b": b, "key": keys[a]})
        else:
            tn += 1
    return {"version": data.get("version"), "pairs": len(data["pairs"]), "absent_in_db": absent,
            "same_found": tp, "same_lost": fn, "different_kept_apart": tn, "false_merges": false_merges,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None}


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
    run_id: str = "k2",
    verified_set: Optional[str] = None,
) -> Dict[str, Any]:
    conn = get_db_connection(db_path, read_only=dry_run)
    try:
        import product_key
        baseline, totals = load_baseline(conn, run_id, read_only=dry_run)
        raw_items = find_titles_to_migrate(conn, limit=limit, rescore_only=rescore_only)
        print(f"[C4] Найдено названий для миграции: {len(raw_items)}, в исходном снимке {run_id}: {len(baseline)}")
        if not raw_items and not check_pairs and not verified_set:
            return {"total": 0, "status": "nothing_to_migrate"}

        titles_all = [t for t, _ in raw_items]

        # Проверяем, у кого поля уже есть
        saved_fields = load_existing_fields(conn, titles_all)
        print(f"[C4] Из них уже сохранены поля: {len(saved_fields)}")

        all_new_keys: Dict[str, str] = {}
        no_identity: Set[str] = set()
        incomplete: Set[str] = set()
        failed_titles = 0
        missing_titles = 0
        planned: Dict[str, Dict[str, Any]] = {}
        all_fields: Dict[str, Dict[str, Any]] = dict(saved_fields)
        api_failures = 0
        api_calls = 0
        started = time.monotonic()

        # Разделяем на те, что требуют вызова модели, и те, у кого поля уже есть
        without_fields = [t for t in titles_all if t not in saved_fields]
        if rescore_only and without_fields:
            print(f"[C4] --rescore-only: пропуск {len(without_fields)} названий без полей")
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
                # Отказ модели и пропуск названия в ответе — не «нет бренда»: прежний ключ остаётся до повтора
                failed_titles += len(to_fetch) if fails else 0
                missing_titles += 0 if fails else len(set(to_fetch) - set(fetched))
                all_fields.update(fetched)
                chunk_fields.update(fetched)

            for t in chunk:
                if t in all_fields:
                    chunk_fields[t] = all_fields[t]

            # Разрешаем ключи для пачки
            keys = resolve_batch_keys(conn, chunk_fields, planned if dry_run else None)
            for t, k in keys.items():
                if k:
                    all_new_keys[t] = k
                    if dry_run:
                        planned[t] = {"fields": chunk_fields[t], "canonical_key": k}
                elif product_key.no_key_reason(chunk_fields.get(t)) == "no_identity":
                    no_identity.add(t)
                else:
                    incomplete.add(t)

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
        totals = {**totals, "api_calls": totals["api_calls"] + api_calls,
                  "api_failures": totals["api_failures"] + api_failures,
                  "failed_titles": totals["failed_titles"] + failed_titles,
                  "missing_titles": totals["missing_titles"] + missing_titles,
                  "seconds": round(totals["seconds"] + total_elapsed, 1)}
        if not dry_run:
            conn.execute("""UPDATE k2_migration_runs SET api_calls = ?, api_failures = ?, failed_titles = ?,
                            missing_titles = ?, seconds = ? WHERE run_id = ?""",
                         (totals["api_calls"], totals["api_failures"], totals["failed_titles"],
                          totals["missing_titles"], totals["seconds"], run_id))
            conn.commit()
        current = actual_keys(conn, baseline, {**(all_new_keys if dry_run else {}),
                                               **({t: "" for t in no_identity} if dry_run else {})})
        result: Dict[str, Any] = {
            # Этот запуск
            "total_titles": len(titles_all),
            "processed_titles": len(titles_to_process),
            "with_k2_key": len(all_new_keys),
            "no_identity_titles": len(no_identity),
            "incomplete_titles": len(incomplete),
            "failed_titles": failed_titles,
            "missing_titles": missing_titles,
            "api_calls": api_calls,
            "api_failures": api_failures,
            "seconds": round(total_elapsed, 1),
            "dry_run": dry_run,
            # Весь проход: фактическое состояние исходного снимка и накопленные счётчики
            "run": {"run_id": run_id, "baseline_created_at": totals["created_at"], "baseline_titles": len(baseline),
                    "actual_k2_keys": sum(1 for k in current.values() if k.startswith("k2:")),
                    "actual_legacy_keys": sum(1 for k in current.values() if k and not k.startswith("k2:")),
                    "actual_no_key": sum(1 for k in current.values() if not k),
                    "cumulative": {k: totals[k] for k in ("api_calls", "api_failures", "failed_titles",
                                                          "missing_titles", "seconds")}},
        }
        if not raw_items:
            result["status"] = "nothing_to_migrate"

        if verified_set:
            result["verified_pairs"] = evaluate_verified_pairs(verified_set, current)

        if check_pairs:
            print("[C4] Расчёт сравнения пар до и после миграции по всему исходному снимку...")
            title_shops = load_products_shops(conn, baseline)
            pairs_stat = check_pairs_comparison(baseline, current, title_shops)
            result["pairs_comparison"] = pairs_stat
            rate = pairs_stat["retention_rate"]
            print(f"[C4] Пар было: {pairs_stat['old_pairs_count']} | Стало: {pairs_stat['new_pairs_count']} | "
                  f"Сохранилось: {pairs_stat['retained_pairs_count']} "
                  f"({'нет пар — не определено' if rate is None else f'{rate*100:.1f}%'}) | "
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
    ap.add_argument("--run-id", default="k2", help="Проход миграции: его исходный снимок не меняется при возобновлении")
    ap.add_argument("--verified-set", default=None, help="JSON проверенных пар для оценки по критериям C2")
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
        run_id=args.run_id,
        verified_set=args.verified_set,
    ))


if __name__ == "__main__":
    main()
