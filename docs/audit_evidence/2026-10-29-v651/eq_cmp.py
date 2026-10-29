import json, sys
a = json.load(open(sys.argv[1])); b = json.load(open(sys.argv[2]))
bad = 0
for t in a["dump"]:
    x, y = a["dump"][t], b["dump"][t]
    same = x == y
    print(f"{t:12} old={len(x):6} new={len(y):6} {'SAME' if same else 'DIFF'}")
    if not same:
        bad += 1
        sx, sy = {json.dumps(r) for r in x}, {json.dumps(r) for r in y}
        for r in list(sx - sy)[:3]: print("  only old:", r)
        for r in list(sy - sx)[:3]: print("  only new:", r)
sys.exit(1 if bad else 0)
