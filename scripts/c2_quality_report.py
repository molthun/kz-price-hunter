"""Оценка качества C2 на независимой разметке и реестр причин потерь (аудит C2A-01/02).

Читает только выгрузку стенда (docs/audit_evidence/.../stand-data.json) и разметку scripts/k2_labeled_sample.json,
базу не открывает. Прежний кэш и k2 считаются одной формулой на одних и тех же парах.

    python3 scripts/c2_quality_report.py --out docs/audit_evidence/2026-10-30-c2-labels/quality.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_key  # noqa: E402

DEFAULT_DATA = ROOT / "docs/audit_evidence/2026-10-30-c2/stand-data.json"
DEFAULT_LABELS = ROOT / "scripts/k2_labeled_sample.json"
# Пара, где хотя бы у одного названия нет ключа k2. Нет полей — проход их не получил; это не отсутствие бренда
MIXED_CAUSES = ("missing_fields_one_side", "missing_fields_both", "legacy_with_fields", "no_identity")
DROPPED_CAUSES = MIXED_CAUSES + ("variant_one_side", "variant_conflict", "revision_differs", "system_vs_component",
                                 "identity_differs_shared_code", "identity_differs_other")
ADDED_CAUSES = ("old_format_only", "old_variant_differs", "same_identity_new_old_empty", "own_keys_equal_old_identity_differs",
                "linked_by_article")
_CODE = re.compile(r"[a-z0-9][a-z0-9/._-]{4,}[a-z0-9]")


def _codes(title):
    """Коды из названия: от 6 знаков, буквы и цифры вместе, без учёта разделителей."""
    text = product_key._text(title)
    found = set()
    for token in _CODE.findall(text):
        compact = re.sub(r"[^a-z0-9]", "", token)
        if len(compact) >= 6 and re.search(r"[a-z]", compact) and re.search(r"\d", compact):
            found.add(compact)
    return found


def dropped_cause(pair):
    """Одна причина для каждой потерянной пары: сначала нет полей, затем что именно различается в k2."""
    a, b = pair["new_a"], pair["new_b"]
    if not (a and a.startswith("k2:") and b and b.startswith("k2:")):
        missing = (pair["fields_a"] is None) + (pair["fields_b"] is None)
        if missing:
            return "missing_fields_both" if missing == 2 else "missing_fields_one_side"
        return "no_identity" if not a or not b else "legacy_with_fields"
    if product_key.is_system_key(a) != product_key.is_system_key(b):
        return "system_vs_component"
    ra, rb = product_key.revision_of(a), product_key.revision_of(b)
    if ra != rb:
        return "revision_differs"
    if product_key._identity_of(a) == product_key._identity_of(b):
        va, vb = product_key._variants_of(a), product_key._variants_of(b)
        return "variant_conflict" if va and vb else "variant_one_side"
    return "identity_differs_shared_code" if _codes(pair["a"]) & _codes(pair["b"]) else "identity_differs_other"


def added_cause(pair):
    """Почему пара появилась: прежние ключи различались только форматом, вариантом или по существу."""
    old_a, old_b = pair["old_a"] or "", pair["old_b"] or ""
    if not old_a or not old_b:
        return "same_identity_new_old_empty"

    def identity(key):
        parts = key.split(":")
        brand = product_key.brand_token(parts[0]) if parts else None
        return (brand, product_key.model_token(parts[1], brand) if len(parts) > 1 else None)
    if identity(old_a) == identity(old_b):
        def variants(key):
            return ":".join(sorted(key.split(":")[2:]))
        return "old_variant_differs" if variants(old_a) != variants(old_b) else "old_format_only"
    own_a = product_key.build_key(pair["fields_a"], pair["a"]) if pair["fields_a"] else None
    own_b = product_key.build_key(pair["fields_b"], pair["b"]) if pair["fields_b"] else None
    return "own_keys_equal_old_identity_differs" if own_a and own_a == own_b else "linked_by_article"


def registry(data):
    """Полный реестр: каждая пара ровно с одной причиной; суммы сверяются с выгрузкой."""
    dropped = [{"a": p["a"], "b": p["b"], "cause": dropped_cause(p), "old": p["old"],
                "new_a": p["new_a"], "new_b": p["new_b"]} for p in data["dropped"]]
    added = [{"a": p["a"], "b": p["b"], "cause": added_cause(p), "old_a": p["old_a"], "old_b": p["old_b"],
              "new": p["new"]} for p in data["added"]]
    counts_d = collections.Counter(p["cause"] for p in dropped)
    counts_a = collections.Counter(p["cause"] for p in added)
    assert sum(counts_d.values()) == data["pairs"]["dropped"] and set(counts_d) <= set(DROPPED_CAUSES)
    assert sum(counts_a.values()) == data["pairs"]["added"] and set(counts_a) <= set(ADDED_CAUSES)
    return {"dropped": dropped, "added": added,
            "dropped_counts": {c: counts_d.get(c, 0) for c in DROPPED_CAUSES},
            "added_counts": {c: counts_a.get(c, 0) for c in ADDED_CAUSES}}


def pair_keys(data):
    """{(a, b): (прежний a, прежний b, новый a, новый b)} по всем парам выгрузки."""
    keys = {}
    for p in data["dropped"]:
        keys[(p["a"], p["b"])] = (p["old"], p["old"], p["new_a"], p["new_b"])
    for p in data["added"]:
        keys[(p["a"], p["b"])] = (p["old_a"], p["old_b"], p["new"], p["new"])
    for p in data.get("retained", []):
        keys[(p["a"], p["b"])] = (p["old"], p["old"], p["new"], p["new"])
    return keys


def lookup(item, keys, inputs):
    found = keys.get((item["a"], item["b"])) or keys.get((item["b"], item["a"]))
    if found:
        return found if (item["a"], item["b"]) in keys else (found[1], found[0], found[3], found[2])
    a, b = inputs.get(item["a"]), inputs.get(item["b"])
    if not a or not b:
        return None
    # None — названия нет в снимке (синтетическая пара); пустой ключ после no_identity — это "", такой товар есть
    if a["old"] is None and a["new"] is None or b["old"] is None and b["new"] is None:
        return None
    return (a["old"], b["old"], a["new"], b["new"])


def confusion(rows, side, packaging=None):
    """TP/FN/FP/TN одной формулой. packaging: как считать упаковку (OEM/BOX): None — исключить, True/False — как метку."""
    counts = collections.Counter()
    for row in rows:
        label = row["label"]
        if label == "packaging" and packaging is not None:
            label = "same" if packaging else "different"
        if label not in ("same", "different"):
            counts[label] += 1
            continue
        first, second = row["keys"][side], row["keys"][side + 1]
        together = bool(first) and first == second
        counts[{("same", True): "tp", ("same", False): "fn", ("different", True): "fp",
                ("different", False): "tn"}[(label, together)]] += 1
    return {k: counts.get(k, 0) for k in ("tp", "fn", "fp", "tn", "uncertain", "packaging")}


def _rate(n, total, z=1.96):
    """Доля и интервал Уилсона."""
    if not total:
        return None
    p = n / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return {"n": n, "of": total, "rate": round(p, 4), "low": round(center - half, 4), "high": round(center + half, 4)}


def _layer_of(data):
    """{пара: слой} по выгрузке. Слой — из самих ключей, а не из разметки."""
    layers = {}
    for p in data.get("retained", []):
        layers[frozenset((p["a"], p["b"]))] = "retained"
    for p in data["added"]:
        layers[frozenset((p["a"], p["b"]))] = "added"
    for p in data["dropped"]:
        both = (p["new_a"] or "").startswith("k2:") and (p["new_b"] or "").startswith("k2:")
        layers[frozenset((p["a"], p["b"]))] = "dropped_both_k2" if both else "dropped_mixed"
    return layers


def _packed(label, packaging):
    if label == "packaging" and packaging is not None:
        return "same" if packaging else "different"
    return label


# Восемь интервалов (по два на четыре слоя) держатся одновременно с вероятностью не ниже 95 %: поправка Бонферрони, 0,05/8
_JOINT_Z = 2.734


def weighted_estimate(rows, strata_sizes, packaging, known=None):
    """Оценка на всём каталоге: известная часть слоя считается как есть, случайная выборка распространяется только на остаток.

    Пары, размеченные раньше, в новый отбор попасть не могли, поэтому свежая доля к ним не применяется (C3A-02). uncertain
    не распределяется: точечно идёт отдельной строкой, а в границах считается против k2. Границы отношений — худшее сочетание
    границ слоёв; сохранившиеся пары входят в обе стороны с противоположными краями, поэтому границы осторожнее заявленных 95 %."""
    known = known or {}
    share, layer = {}, {}
    for stratum, size in strata_sizes.items():
        labels = [_packed(row["label"], packaging) for row in rows if stratum in row["strata"]]
        same, diff = labels.count("same"), labels.count("different")
        drawn = same + diff + labels.count("uncertain")
        share[stratum] = _rate(same, same + diff)
        share[stratum]["excluded"] = len(labels) - same - diff
        counts = collections.Counter(_packed(label, packaging) for label in known.get(stratum, []))
        k_same, k_diff, k_open = counts["same"], counts["different"], counts["uncertain"]
        pool = size - sum(counts.values())
        if packaging is None:
            # Упаковка исключена из сравнения: такие пары не решены ни в одну сторону
            k_open += counts["packaging"]
        layer[stratum] = {
            "size": size, "known": len(known.get(stratum, [])), "known_same": k_same, "known_different": k_diff, "known_open": k_open,
            "pool": pool, "drawn": drawn, "drawn_same": same, "drawn_different": diff, "drawn_open": drawn - same - diff,
            "true": k_same + pool * same / drawn,
            "false": k_diff + pool * diff / drawn,
            "open": k_open + pool * (drawn - same - diff) / drawn,
            "true_low": k_same + pool * _rate(same, drawn, _JOINT_Z)["low"],
            "true_high": k_same + k_open + pool * _rate(drawn - diff, drawn, _JOINT_Z)["high"],
            "false_low": k_diff + pool * _rate(diff, drawn, _JOINT_Z)["low"],
            "false_high": k_diff + k_open + pool * _rate(drawn - same, drawn, _JOINT_Z)["high"]}

    def total(names, field):
        return sum(layer[n][field] for n in names)
    base, cand = ("retained", "dropped_mixed", "dropped_both_k2"), ("retained", "added")

    def side(names):
        return {field: round(total(names, field)) for field in ("true", "false", "open", "true_low", "true_high", "false_low", "false_high")}
    return {"share_true_by_stratum": share,
            "layers": {name: {k: round(v, 1) for k, v in row.items()} for name, row in layer.items()},
            "baseline": side(base), "candidate": side(cand),
            # Какая часть настоящих совпадений прежнего кэша сохранилась в k2: рост общего числа пар её не заменяет
            "baseline_true_kept": round(layer["retained"]["true"] / total(base, "true"), 3),
            "baseline_true_kept_low": round(layer["retained"]["true_low"]
                                            / (layer["retained"]["true_low"] + total(base[1:], "true_high")), 3),
            "candidate_vs_baseline_true": round(total(cand, "true") / total(base, "true"), 3),
            "candidate_vs_baseline_true_low": round(total(cand, "true_low") / total(base, "true_high"), 3),
            "false_merges_ratio": round(total(cand, "false") / max(1, total(base, "false")), 3),
            "false_merges_ratio_high": round(total(cand, "false_high") / max(1, total(base, "false_low")), 3)}


def build(data, sample):
    keys, inputs = pair_keys(data), data["eval_inputs"]
    rows = []
    for item in sample["pairs"]:
        found = lookup(item, keys, inputs)
        rows.append({**item, "keys": found})
    known = [r for r in rows if r["keys"]]
    reg = registry(data)
    strata_sizes = {"retained": data["pairs"]["retained"],
                    "dropped_mixed": sum(reg["dropped_counts"][k] for k in MIXED_CAUSES),
                    "dropped_both_k2": sum(v for k, v in reg["dropped_counts"].items() if k not in MIXED_CAUSES),
                    "added": data["pairs"]["added"]}
    for row in rows:
        row["strata"] = ["retained" if s == "retained_pool" else s for s in row["strata"]]
    random_rows = [r for r in known if set(r["strata"]) & set(strata_sizes)]
    # Пары, известные до отбора: полная перепись своей части слоя. В выборку они не входят
    layers, census = _layer_of(data), collections.defaultdict(list)
    for item in sample.get("known_pairs", []):
        census[layers[frozenset((item["a"], item["b"]))]].append(item["label"])
    drawn = {frozenset((r["a"], r["b"])) for r in random_rows}
    assert not drawn & {frozenset((i["a"], i["b"])) for i in sample.get("known_pairs", [])}, "пара и в переписи, и в выборке"
    lost = [{"a": r["a"], "b": r["b"], "new_a": r["keys"][2], "new_b": r["keys"][3], "strata": r["strata"]}
            for r in random_rows if r["label"] == "same" and r["keys"][2] != r["keys"][3]]
    merged = [{"a": r["a"], "b": r["b"], "key": r["keys"][2], "strata": r["strata"]}
              for r in random_rows if r["label"] in ("different", "packaging") and r["keys"][2] and r["keys"][2] == r["keys"][3]]
    return {
        "labels": {"pairs": len(rows), "without_keys": len(rows) - len(known),
                   "by_label": dict(collections.Counter(r["label"] for r in rows)),
                   "unique_titles": len({t for r in rows for t in (r["a"], r["b"])})},
        "strata_sizes": strata_sizes,
        "known_by_layer": {name: len(census.get(name, [])) for name in strata_sizes},
        "random_sample": {
            "pairs": len(random_rows),
            "baseline": confusion(random_rows, 0, packaging=False),
            "candidate": confusion(random_rows, 2, packaging=False),
        },
        # Решение владельца 30.10.2026: OEM/Tray и BOX — разные товары. Остальные сценарии — для сравнения
        "weighted": {"owner_decision": weighted_estimate(random_rows, strata_sizes, False, census),
                     "packaging_excluded": weighted_estimate(random_rows, strata_sizes, None, census),
                     "packaging_same": weighted_estimate(random_rows, strata_sizes, True, census),
                     "packaging_different": weighted_estimate(random_rows, strata_sizes, False, census)},
        "candidate_false_merges": merged,
        "candidate_lost_true_pairs": lost,
        "registry_counts": {"dropped": reg["dropped_counts"], "added": reg["added_counts"]},
        "titles": {"k2": data["key_counts"]["k2"], "legacy": data["key_counts"]["legacy"],
                   "empty": data["key_counts"]["empty"]},
    }, reg


def pair_keys_now(pair_fields, a, b, legacy_a, legacy_b):
    """Ключи пары по нынешним правилам из сохранённых полей. Связь по артикулу — только внутри пары: группы каталога
    здесь нет, это оценка правил, а не пересборка копии. Без полей остаётся прежний ключ."""
    fa, fb = pair_fields.get(a), pair_fields.get(b)
    entries = {t: f for t, f in ((a, fa), (b, fb)) if isinstance(f, dict)}
    keys = product_key.resolve_keys_with_articles(entries) if entries else {}
    return keys.get(a) or (None if fa else legacy_a), keys.get(b) or (None if fb else legacy_b)


def rules_now(data, sample):
    """Та же оценка после изменения правил: ключи пересчитаны, метки и размеры слоёв прежние."""
    fields, old = {}, {}
    for p in data["dropped"]:
        fields.update({p["a"]: p["fields_a"], p["b"]: p["fields_b"]})
        old[(p["a"], p["b"])] = (p["new_a"], p["new_b"])
    for p in data["added"]:
        fields.update({p["a"]: p["fields_a"], p["b"]: p["fields_b"]})
        old[(p["a"], p["b"])] = (p["new"], p["new"])
    for title, row in data["eval_inputs"].items():
        fields.setdefault(title, row["fields"])
    keys, inputs = pair_keys(data), data["eval_inputs"]

    def outcome(rows):
        counts = collections.Counter()
        for row in rows:
            found = lookup(row, keys, inputs)
            label = "different" if row["label"] == "packaging" else row["label"]
            if not found or label == "uncertain":
                counts["excluded"] += 1
                continue
            now_a, now_b = pair_keys_now(fields, row["a"], row["b"], found[2], found[3])
            before = bool(found[2]) and found[2] == found[3]
            now = bool(now_a) and now_a == now_b
            side = "tp" if label == "same" else "fp"
            counts[f"before_{side}"] += before
            counts[f"now_{side}"] += now
            counts[f"{'true' if label == 'same' else 'false'}_total"] += 1
        return {k: counts.get(k, 0) for k in ("before_tp", "now_tp", "before_fp", "now_fp", "true_total", "false_total", "excluded")}

    pick = lambda name: [r for r in sample["pairs"] if name in r["strata"]]
    strata = {"holdout_added": outcome(pick("holdout_added")), "holdout_dropped_both_k2": outcome(pick("holdout_dropped_both_k2")),
              "tuned_added": outcome(pick("added")), "tuned_dropped_both_k2": outcome(pick("dropped_both_k2")),
              "retained_pool": outcome(pick("retained") + pick("retained_pool")),
              "dropped_mixed": outcome(pick("dropped_mixed"))}
    together = sum(1 for p in data["added"] if (lambda k: k[0] and k[0] == k[1])(pair_keys_now(fields, p["a"], p["b"], p["new"], p["new"])))
    rejoined = sum(1 for p in data["dropped"] if p["fields_a"] and p["fields_b"]
                   and (lambda k: k[0] and k[0] == k[1])(pair_keys_now(fields, p["a"], p["b"], p["new_a"], p["new_b"])))
    return {"strata": strata, "added_still_together": together, "dropped_both_k2_rejoined": rejoined,
            "limits": "Только пары прежних слоёв: пары, которые правила могли соединить заново вне их, не видны; нужна пересборка копии."}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--labels", default=str(DEFAULT_LABELS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--registry", help="Полный реестр потерянных и новых пар (JSON)")
    args = ap.parse_args()
    report, reg = build(json.loads(Path(args.data).read_text(encoding="utf-8")),
                        json.loads(Path(args.labels).read_text(encoding="utf-8")))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    report["rules_now"] = rules_now(json.loads(Path(args.data).read_text(encoding="utf-8")),
                                    json.loads(Path(args.labels).read_text(encoding="utf-8")))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.registry:
        Path(args.registry).write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("labels", "strata_sizes", "random_sample", "weighted", "registry_counts")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
