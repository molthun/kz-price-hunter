"""Остаток без k2 среди потерянных пар C3 (C3A-03). Только выгрузка 2026-10-30-c3; базу не открывает."""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
data = json.loads((ROOT / "docs/audit_evidence/2026-10-30-c3/stand-data.json").read_text(encoding="utf-8"))
sides = {}
pairs = 0
for row in data["dropped"]:
    a, b = row["new_a"] or "", row["new_b"] or ""
    if a.startswith("k2:") and b.startswith("k2:"):
        continue
    pairs += 1
    for title, key, fields in ((row["a"], a, row.get("fields_a")), (row["b"], b, row.get("fields_b"))):
        if not key.startswith("k2:"):
            entry = sides.setdefault(title, {"title": title, "key": key, "has_fields": fields is not None, "pairs": 0})
            entry["pairs"] += 1
rows = sorted(sides.values(), key=lambda r: (-r["pairs"], r["title"]))
out = {"note": "Только названия из 522 потерянных пар. Весь остаток копии — 682 названия (480 failed + 202 missing); "
               "полный список требует чтения копии на стенде и разрешения владельца.",
       "pairs": pairs, "titles": len(rows), "by_fields": dict(collections.Counter(r["has_fields"] for r in rows)), "rows": rows}
(HERE / "residue_titles.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(pairs, len(rows), out["by_fields"])
for r in rows[:12]:
    print(r["pairs"], r["has_fields"], r["key"], "|", r["title"])
