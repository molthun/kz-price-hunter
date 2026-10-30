"""Детерминированный случайный отбор пар для расширенной выборки C2-ext.
Seed: 20261101
Слои:
- added: 500 пар из чистого пула 1 914 (всего на стенде 2 280, исключены все 888 уже размеченных пар).
- retained: 240 пар из чистого пула 839 (всего на стенде 1 004, исключены все 888 уже размеченных пар).
Итого: 740 новых пар со строгим нулевым пересечением с существующими выборками.
"""
import hashlib
import json
import random
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent
ROOT = EVIDENCE_DIR.parents[2]
STAND_PATH = ROOT / "docs/audit_evidence/2026-10-30-c3/stand-data.json"
LABELS_PATH = ROOT / "scripts/k2_labeled_sample.json"

SEED = 20261101
N_ADDED = 500
N_RETAINED = 240


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    with open(STAND_PATH, "r", encoding="utf-8") as f:
        stand_data = json.load(f)

    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        labels_data = json.load(f)

    # All already known or drawn pairs
    all_existing = {
        frozenset((p["a"], p["b"]))
        for p in labels_data.get("pairs", [])
    } | {
        frozenset((p["a"], p["b"]))
        for p in labels_data.get("known_pairs", [])
    }

    print(f"Total existing isolated pairs: {len(all_existing)}")

    # Pools from stand data
    # Sort deterministically by (a, b) before sampling
    raw_added = [
        {"a": p["a"], "b": p["b"], "strata": ["added"]}
        for p in stand_data["added"]
        if frozenset((p["a"], p["b"])) not in all_existing
    ]
    raw_added.sort(key=lambda x: (x["a"], x["b"]))

    raw_retained = [
        {"a": p["a"], "b": p["b"], "strata": ["retained"]}
        for p in stand_data.get("retained", [])
        if frozenset((p["a"], p["b"])) not in all_existing
    ]
    raw_retained.sort(key=lambda x: (x["a"], x["b"]))

    print(f"Clean pool added: {len(raw_added)} (target: {N_ADDED})")
    print(f"Clean pool retained: {len(raw_retained)} (target: {N_RETAINED})")

    rng = random.Random(SEED)
    sample_added = rng.sample(raw_added, N_ADDED)
    sample_retained = rng.sample(raw_retained, N_RETAINED)

    # Combine and shuffle for blind labeling
    combined = sample_added + sample_retained
    rng.shuffle(combined)

    # Verify zero intersection
    drawn_keys = {frozenset((p["a"], p["b"])) for p in combined}
    assert len(drawn_keys) == len(combined) == (N_ADDED + N_RETAINED)
    assert not (drawn_keys & all_existing), "Intersection with existing pairs must be strictly empty!"

    # Save raw drawn pairs (with strata)
    raw_drawn_path = EVIDENCE_DIR / "raw_drawn_pairs.json"
    with open(raw_drawn_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    # Save blind unlabeled sample (no strata, indexed 0..739)
    unlabeled = [
        {"idx": i, "a": p["a"], "b": p["b"], "strata": p["strata"]}
        for i, p in enumerate(combined)
    ]
    unlabeled_path = EVIDENCE_DIR / "unlabeled_sample.json"
    with open(unlabeled_path, "w", encoding="utf-8") as f:
        json.dump(unlabeled, f, ensure_ascii=False, indent=2)

    # Metadata
    meta = {
        "seed": SEED,
        "n_added": N_ADDED,
        "n_retained": N_RETAINED,
        "total_drawn": len(combined),
        "stand_file": str(STAND_PATH.relative_to(ROOT)),
        "stand_sha256": sha256_file(STAND_PATH),
        "labels_source": str(LABELS_PATH.relative_to(ROOT)),
        "labels_sha256": sha256_file(LABELS_PATH),
        "raw_drawn_sha256": sha256_file(raw_drawn_path),
        "unlabeled_sha256": sha256_file(unlabeled_path),
        "pool_sizes": {
            "stand_added_total": len(stand_data["added"]),
            "clean_pool_added": len(raw_added),
            "stand_retained_total": len(stand_data.get("retained", [])),
            "clean_pool_retained": len(raw_retained),
            "isolated_existing_pairs": len(all_existing),
        }
    }
    meta_path = EVIDENCE_DIR / "sampling_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"Sampling complete. Metadata written to {meta_path.name}.")


if __name__ == "__main__":
    main()
