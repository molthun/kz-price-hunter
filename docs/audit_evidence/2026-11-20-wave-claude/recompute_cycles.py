"""Пересчёт длительности кругов по wave-events.json (старты волн и метки «Полный круг»)."""
import json, statistics, datetime as dt, sys
ev = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "wave-events.json"))
starts, ends, counts = {}, {}, {}
for e in ev:
    ts = dt.datetime.fromisoformat(e["ts"][:26])
    c = e["cycle"]
    if e.get("complete"):
        ends[c] = ts
        continue
    counts[c] = counts.get(c, 0) + 1
    if e["wave"] == 1 and c not in starts:
        starts[c] = ts
full = [c for c in ends if c in starts]
d = sorted((ends[c] - starts[c]).total_seconds() / 60 for c in full)
print("markers", len(ends), "fully observed", len(full), "exactly 9 starts", sum(counts[c] == 9 for c in full))
print("min/median/max %.2f/%.2f/%.2f" % (d[0], statistics.median(d), d[-1]))
