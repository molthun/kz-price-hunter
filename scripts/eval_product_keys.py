"""Оценка модели для ключей товаров (P17-C2/C3, docs/DEVELOPMENT_PLAN.md).

Главное для сравнения цен — не совпадение строки ключа с прежней моделью, а то, что
названия одного товара из разных магазинов получают один ключ (полнота) и разные товары не склеиваются
(склейки). Эталон — группы, которые прежде собрал Gemini: один его ключ у названий из 2+ магазинов.
Gemini не идеален, поэтому самые «подозрительные» расхождения печатаются для ручного просмотра.

Запуск внутри контейнера приложения (база только читается):

    python3 scripts/eval_product_keys.py --base http://192.168.28.25:11434/v1 --model qwen3-vl:8b-instruct \
        --groups 80 --out /tmp/eval.json

Ключ проверяемой модели строится по стандарту k2: подсказка K2_NORMALIZE_PROMPT → поля → product_key.build_key.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_groups(db_path: str, groups: int, seed: int):
    """Группы «один ключ Gemini — названия из 2+ магазинов» среди названий, ключ которым делал AI."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute("""
        SELECT c.canonical_key, c.title, p.shop
        FROM title_canonical_cache c
        JOIN title_ai_attempts a ON a.title_hash = c.title_hash
        JOIN products p ON p.title = c.title
        WHERE c.canonical_key != '' AND c.canonical_key NOT LIKE '%|spec:%'
    """).fetchall()
    conn.close()
    by_key = defaultdict(dict)
    for key, title, shop in rows:
        by_key[key].setdefault(title, shop)
    multi = [k for k, t in by_key.items() if len(set(t.values())) >= 2]
    random.Random(seed).shuffle(multi)
    chosen = multi[:groups]
    return {k: sorted(by_key[k]) for k in chosen}


async def candidate_keys(titles, base: str, model: str, key: str = ""):
    import ai_router
    import ai_service
    import product_key
    keys, failures, calls, started = {}, 0, 0, time.monotonic()
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        prompt = ai_service.K2_NORMALIZE_PROMPT + "\n\nНормализуй товары, перечисленные в блоке данных ниже.\n" + \
            ai_router.untrusted_block("\n".join(f"- {t}" for t in chunk), "НАЗВАНИЯ")
        reply = await ai_service._call_local_api(prompt, base, model, key, 120.0)
        calls += 1
        if not (isinstance(reply, dict) and isinstance(reply.get("items"), list)):
            failures += 1
            continue
        keys.update({t: k for t, k in product_key.keys_for(reply["items"], chunk).items() if k})
    return keys, failures, calls, time.monotonic() - started


def score(groups, keys):
    title_group = {t: g for g, ts in groups.items() for t in ts}
    titles = sorted(title_group)
    same_pairs = found_same = merged_wrong = 0
    wrong_examples = []
    for a, b in itertools.combinations(titles, 2):
        same_ref = title_group[a] == title_group[b]
        same_cand = keys.get(a) is not None and keys.get(a) == keys.get(b)
        if same_ref:
            same_pairs += 1
            found_same += same_cand
        elif same_cand:
            merged_wrong += 1
            if len(wrong_examples) < 15:
                wrong_examples.append({"key": keys[a], "a": a, "b": b,
                                       "gemini_a": title_group[a], "gemini_b": title_group[b]})
    return {"titles": len(titles), "groups": len(groups), "with_key": sum(1 for t in titles if keys.get(t)),
            "same_pairs": same_pairs, "found_same": found_same,
            "recall": round(found_same / same_pairs, 3) if same_pairs else None,
            "false_merges": merged_wrong, "false_merge_examples": wrong_examples}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/app/data/prices.db")
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--groups", type=int, default=80)
    ap.add_argument("--seed", type=int, default=651)
    ap.add_argument("--out")
    args = ap.parse_args()
    groups = load_groups(args.db, args.groups, args.seed)
    titles = sorted({t for ts in groups.values() for t in ts})
    keys, failures, calls, elapsed = asyncio.run(candidate_keys(titles, args.base, args.model))
    result = {"model": args.model, "calls": calls, "bad_json_calls": failures, "seconds": round(elapsed, 1),
              **score(groups, keys)}
    missed = []
    for g, ts in groups.items():
        cand = {t: keys.get(t) for t in ts}
        if len(set(cand.values())) > 1 and len(missed) < 15:
            missed.append({"gemini": g, "candidate": cand})
    result["missed_examples"] = missed
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("examples")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
