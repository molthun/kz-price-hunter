"""Замер третьего источника истины (P17-C7): поиск в SearXNG + сравнение локальной моделью.

Берёт отчёт scripts/eval_product_keys.py (поля модели и группы Gemini) и проверяет два набора пар:
- «пропуски» — один товар по Gemini, но ключи k2 разные: их поиск должен подтвердить;
- «трудные разные» — разные группы Gemini одного бренда: их поиск не должен склеить.
Gemini не идеален, поэтому сомнительные ответы печатаются для ручного просмотра.

    python3 scripts/eval_pair_verify.py --report /tmp/eval_qwen3.json --search http://192.168.28.25:8888 \
        --base http://192.168.28.25:11434/v1 --model qwen3-vl:8b-instruct --out /tmp/verify.json

Медленный ночной замер (поисковики за SearXNG блокируют частые запросы): --interval 90.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pick_pairs(report, positives: int, negatives: int, seed: int):
    import product_key
    from scripts.eval_product_keys import keys_from_fields
    fields, groups = report["fields"], report["reference_groups"]
    keys = keys_from_fields(fields)
    group_of = {t: g for g, ts in groups.items() for t in ts}
    missed, hard = [], []
    for a, b in itertools.combinations(sorted(group_of), 2):
        if a not in fields or b not in fields:
            continue
        same_ref = group_of[a] == group_of[b]
        if same_ref and keys.get(a) != keys.get(b):
            missed.append((a, b))
        elif not same_ref and product_key.brand_token(fields[a].get("brand")) == \
                product_key.brand_token(fields[b].get("brand")):
            hard.append((a, b))
    rnd = random.Random(seed)
    rnd.shuffle(missed)
    rnd.shuffle(hard)
    return missed[:positives], hard[:negatives]


async def run(report, pairs, search_base, base, model):
    import ai_service
    import product_verify as pv

    async def search(query):
        return await pv.searxng_search(search_base, query)

    async def ask(prompt):
        return await ai_service._call_local_api(prompt, base, model, "", 90.0)

    out = []
    for kind, items in pairs.items():
        for a, b in items:
            verdict = await pv.verify_pair(a, report["fields"][a], b, report["fields"][b], search, ask)
            out.append({"kind": kind, "a": a, "b": b, "at": time.strftime("%H:%M:%S"), **verdict})
            print(f"{out[-1]['at']} {kind} {verdict['decision']} | {verdict.get('reason', '')[:60]}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--search", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--positives", type=int, default=25)
    ap.add_argument("--negatives", type=int, default=25)
    ap.add_argument("--seed", type=int, default=651)
    ap.add_argument("--interval", type=float, help="пауза между запросами поиска, с (по умолчанию — как в сервисе)")
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.interval is not None:
        import product_verify
        product_verify.SEARCH_MIN_INTERVAL = args.interval
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    missed, hard = pick_pairs(report, args.positives, args.negatives, args.seed)
    started = time.monotonic()
    rows = asyncio.run(run(report, {"missed": missed, "hard_different": hard}, args.search, args.base, args.model))
    summary = {"model": args.model, "seconds": round(time.monotonic() - started, 1)}
    for kind in ("missed", "hard_different"):
        part = [r for r in rows if r["kind"] == kind]
        summary[kind] = {d: sum(1 for r in part if r["decision"] == d) for d in ("same", "different", "unknown")}
        summary[kind]["pairs"] = len(part)
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
