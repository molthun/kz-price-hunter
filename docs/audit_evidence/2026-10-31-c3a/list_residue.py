"""Полный остаток без k2 в копии C3 (C3A-03). Вход: residue_raw.json (read_residue.py) и выгрузка 2026-10-30-c3; базу не открывает."""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
raw = json.loads((HERE / "residue_raw.json").read_text(encoding="utf-8"))
data = json.loads((ROOT / "docs/audit_evidence/2026-10-30-c3/stand-data.json").read_text(encoding="utf-8"))
lost = collections.Counter()
for row in data["dropped"]:
    for title, key in ((row["a"], row["new_a"] or ""), (row["b"], row["new_b"] or "")):
        if not key.startswith("k2:"):
            lost[title] += 1


def status(fields):
    if fields is None:
        return "no_fields_row"
    return "fields_with_brand" if fields.get("brand") else "fields_without_brand"


rows = sorted(({"title": r["title"], "key": r["key"], "status": status(r["fields"]), "lost_pairs": lost[r["title"]]} for r in raw),
              key=lambda r: (-r["lost_pairs"], r["title"]))
missing = set(lost) - {r["title"] for r in rows}
assert not missing, sorted(missing)
out = {"note": "Все названия копии C3 с прежним ключом. Строка полей есть или нет — факт копии; отсутствие строки не означает отсутствие бренда. "
               "Разделить 480 failed и 202 missing по названиям нельзя: отчёт прохода хранит только числа.",
       "titles": len(rows), "by_status": dict(collections.Counter(r["status"] for r in rows)),
       "in_lost_pairs": sum(1 for r in rows if r["lost_pairs"]), "not_in_lost_pairs": sum(1 for r in rows if not r["lost_pairs"]),
       "top_key_brands": dict(collections.Counter(r["key"].split(":")[0] for r in rows).most_common(15)), "rows": rows}
(HERE / "residue_titles.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print({k: v for k, v in out.items() if k not in ("rows", "note")})
