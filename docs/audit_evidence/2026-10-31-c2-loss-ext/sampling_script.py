"""Отбор расширения слоёв потерь C2: seed 20261103. Только выгрузка и разметка из репозитория; базу не открывает.

Пул — пары слоя без known_pairs и первого этапа. Выход для разметчика не содержит слой, ключи и поля:
unlabeled_titles.txt. Соответствие пар слоям — strata_sealed.json, разметчик его не открывает."""
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
sys.path.insert(0, str(ROOT / "scripts"))
import c2_quality_report as c

N = int(sys.argv[1]) if len(sys.argv) > 1 else 140
read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
key = lambda r: frozenset((r["a"], r["b"]))
data, sample = read(ROOT / "docs/audit_evidence/2026-10-30-c3/stand-data.json"), read(ROOT / "scripts/k2_labeled_sample.json")
seen = {key(r) for r in sample["pairs"]} | {key(r) for r in sample["known_pairs"]}
layers = c._layer_of(data)
rng = random.Random(20261103)
chosen, pools = [], {}
for layer in ("dropped_mixed", "dropped_both_k2"):
    pool = sorted(([*sorted(k)] for k, name in layers.items() if name == layer and k not in seen))
    pools[layer] = len(pool)
    chosen += [{"a": a, "b": b, "strata": [layer]} for a, b in rng.sample(pool, N)]
rng.shuffle(chosen)
(HERE / "strata_sealed.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
(HERE / "unlabeled_titles.txt").write_text("\n".join(f'{i}|{r["a"]} ## {r["b"]}' for i, r in enumerate(chosen)) + "\n", encoding="utf-8")
(HERE / "sampling_metadata.json").write_text(json.dumps({"seed": 20261103, "per_layer": N, "pools": pools}, indent=1), encoding="utf-8")
print(pools, len(chosen))
