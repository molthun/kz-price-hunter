"""Только чтение: GET прод /api/version и /api/products?shop=Меломан постранично. Ничего не пишет."""
import json, urllib.request, urllib.parse, collections, datetime
B="https://shop.molthun.ru"
print("version", json.load(urllib.request.urlopen(B+"/api/version", timeout=30)))
rows=[]; off=0
while True:
    d=json.load(urllib.request.urlopen(B+"/api/products?"+urllib.parse.urlencode({"shop":"Меломан","limit":200,"offset":off}), timeout=60))
    p=d.get("products") or []
    rows+=p; off+=200
    if len(p)<200 or off>20000: break
print("total", d.get("total"), "read", len(rows))
print("keys", sorted(rows[0].keys()) if rows else None)
print("id prefixes", collections.Counter(r["id"].split("_")[0]+("_v2" if r["id"].startswith("marwin_v2_") else "") for r in rows))
for f in ("updated_at","last_seen","scanned_at","date"):
    vals=[r.get(f) for r in rows if r.get(f)]
    if vals:
        days=collections.Counter(str(v)[:10] for v in vals)
        print(f, "min", min(vals), "max", max(vals)); print(" by day", sorted(days.items())[-12:])
print("by category", collections.Counter(r.get("category") for r in rows).most_common(12))
