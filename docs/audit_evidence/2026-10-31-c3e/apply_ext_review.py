"""Замена меток генератора C2-ext слепой разметкой (EXT-01). Вход: ext_blind_order.json, ext_blind_review.json; базу не открывает."""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
key = lambda a, b: frozenset((a, b))
PACK = "упаковка указана только с одной стороны: OEM/BOX по названию не решить"


def main():
    order, review = read(HERE / "ext_blind_order.json"), read(HERE / "ext_blind_review.json")
    labels = {}
    for i, (a, b) in enumerate(order):
        labels[key(a, b)] = ("same", "")
    for label, group in (("different", review["different"]), ("packaging", review["packaging"]), ("uncertain", review["uncertain"])):
        for i, note in group.items():
            labels[key(*order[int(i)])] = (label, note)
    for i in review["uncertain_packaging"]:
        labels[key(*order[i])] = ("uncertain", PACK)
    path = ROOT / "scripts/k2_labeled_sample.json"
    sample = read(path)
    raw = {key(r["a"], r["b"]) for r in read(ROOT / "docs/audit_evidence/2026-11-01-c2-ext/raw_drawn_pairs.json")}
    assert raw == set(labels), "список разметки не совпал с отбором"
    log, used = [], 0
    for row in sample["pairs"]:
        k = key(row["a"], row["b"])
        if k not in labels:
            continue
        used += 1
        label, note = labels[k]
        if row["label"] != label:
            log.append({"a": row["a"], "b": row["b"], "generator": row["label"], "now": label, "reason": note})
        row.update(label=label, note=note, phase="ext")
    assert used == 740
    sample["version"] = 7
    sample["protocol"] = sample["protocol"].split("Разметка Antigravity")[0] + (
        "Первая выборка (530 пар) и known_pairs — по журналу 2026-10-31-c3a. Метки 740 пар расширения (seed 20261101) "
        "заменены 31.10.2026: Claude прочитал только названия в новом порядке (seed 20261102), без ключей, слоя и меток генератора. "
        "Исполнитель знал прежние итоги и ошибки из аудита, поэтому разметка не независимая; сплошной проверки по источникам нет.")
    sample["sampling"] = ("Этап 1: seed 20261031, 320 свежих пар. Этап 2: seed 20261101, 500 added из 1914 и 240 retained из 839. "
                          "Расчёт: метки этапа 1 и 40 new_merges этих слоёв считаются известной частью, доля этапа 2 применяется только к его пулу (аудит EXT-02).")
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "ext_label_changes.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(log), collections.Counter((c["generator"], c["now"]) for c in log), collections.Counter(v[0] for v in labels.values()))


if __name__ == "__main__":
    main()
