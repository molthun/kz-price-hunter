import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LABELS_PATH = ROOT / "scripts/k2_labeled_sample.json"
EXT_PATH = ROOT / "docs/audit_evidence/2026-11-01-c2-ext/ext_labeled_pairs.json"
MERGED_OUTPUT = ROOT / "scripts/k2_labeled_sample.json"

with open(LABELS_PATH, "r", encoding="utf-8") as f:
    orig = json.load(f)

with open(EXT_PATH, "r", encoding="utf-8") as f:
    ext = json.load(f)

print(f"Original pairs: {len(orig['pairs'])}")
print(f"Ext pairs: {len(ext)}")
print(f"Known pairs: {len(orig.get('known_pairs', []))}")

# Check intersections for the new extended sample
orig_drawn = {frozenset((p["a"], p["b"])) for p in orig["pairs"]}
ext_drawn = {frozenset((p["a"], p["b"])) for p in ext}
known = {frozenset((p["a"], p["b"])) for p in orig.get("known_pairs", [])}

inter_orig_ext = orig_drawn & ext_drawn
inter_ext_known = ext_drawn & known

print(f"Intersection orig & ext: {len(inter_orig_ext)}")
print(f"Intersection ext & known: {len(inter_ext_known)}")

assert len(inter_orig_ext) == 0, f"Found duplicates between orig and ext: {len(inter_orig_ext)}"
assert len(inter_ext_known) == 0, f"Found duplicates between ext and known: {len(inter_ext_known)}"

# Prepare merged list
new_pairs = list(orig["pairs"])
for p in ext:
    new_pairs.append({
        "a": p["a"],
        "b": p["b"],
        "label": p["label"],
        "strata": p["strata"],
        "note": p.get("note", "")
    })

merged_data = dict(orig)
merged_data["version"] = 6
merged_data["protocol"] = (
    "Разметка Antigravity 31.10.2026 / 01.11.2026 по названиям вслепую: без ключей, полей, ответов модели и слоя; порядок перемешан. "
    "Включает расширенную независимую выборку C2-ext (seed 20261101: +500 пар added, +240 пар retained) "
    "для сужения доверительного интервала ложных склеек, согласованную по критериям C2/C3A. "
    "Всего в выборке 1270 пар (600 added, 300 retained, 60 dropped_mixed, 60 dropped_both_k2, 40 new_merges, 210 old_set), "
    "плюс 537 пар полной переписи известных слоёв (known_pairs)."
)
merged_data["sampling"] = (
    "Случайно: исходная выборка seed 20261031 (320 свежих пар + 210 old_set = 530 пар) + "
    "расширение seed 20261101 (+500 added из пула 1914, +240 retained из пула 839). "
    "Итого случайная выборка: 600 added (из 2280), 300 retained (из 1004), 60 dropped_mixed (из 476), 60 dropped_both_k2 (из 284). "
    "Known pairs (537) строго изолированы от выборки."
)
merged_data["pairs"] = new_pairs

# Save backup
backup_path = ROOT / "docs/audit_evidence/2026-11-01-c2-ext/k2_labeled_sample_v5_backup.json"
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(orig, f, ensure_ascii=False, indent=2)

with open(MERGED_OUTPUT, "w", encoding="utf-8") as f:
    json.dump(merged_data, f, ensure_ascii=False, indent=2)

print(f"Successfully saved merged k2_labeled_sample.json (version 6, {len(new_pairs)} pairs).")
