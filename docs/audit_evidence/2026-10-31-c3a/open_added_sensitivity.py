"""Чувствительность верхней границы к оставшимся открытым парам added. Не оценка и не замена меток: только справка."""
import collections
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import c2_quality_report as c

read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
S = read(ROOT / "scripts/k2_labeled_sample.json")
D = read(ROOT / "docs/audit_evidence/2026-10-30-c3/stand-data.json")
layers = c._layer_of(D)
sizes = read(Path(__file__).parent / "quality.json")["strata_sizes"]
key = lambda r: frozenset((r["a"], r["b"]))
is_cpu = lambda r: bool(re.search(r"Ryzen|Core i\d|Сore i\d", r["a"] + r["b"]))


def run(change):
    s = copy.deepcopy(S)
    rows = [r for r in s["pairs"] if set(r["strata"]) & set(sizes)]
    known = s["known_pairs"]
    for r in rows + known:
        added = layers.get(key(r)) == "added" if r in known else "added" in r["strata"]
        if added and r["label"] == "uncertain":
            r["label"] = change(r)
    census = collections.defaultdict(list)
    for r in known:
        census[layers[key(r)]].append(r["label"])
    w = c.weighted_estimate(rows, sizes, False, census)
    return {"true": w["candidate"]["true"], "false": w["candidate"]["false"], "open": w["candidate"]["open"], "false_high": w["candidate"]["false_high"],
            "false_ratio": w["false_merges_ratio"], "false_ratio_high": w["false_merges_ratio_high"], "true_ratio_low": w["candidate_vs_baseline_true_low"]}


fresh = [r for r in S["pairs"] if "added" in r["strata"] and r["label"] == "uncertain"]
known = [r for r in S["known_pairs"] if layers.get(key(r)) == "added" and r["label"] == "uncertain"]
out = {"open_fresh": len(fresh), "open_fresh_cpu": sum(map(is_cpu, fresh)), "open_known": len(known), "open_known_cpu": sum(map(is_cpu, known)),
       "as_is": run(lambda r: r["label"]),
       "cpu_unspecified_same": run(lambda r: "same" if is_cpu(r) else r["label"]),
       "cpu_unspecified_different": run(lambda r: "different" if is_cpu(r) else r["label"]),
       "all_open_same": run(lambda r: "same"), "all_open_different": run(lambda r: "different")}
(Path(__file__).parent / "open_added_sensitivity.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
