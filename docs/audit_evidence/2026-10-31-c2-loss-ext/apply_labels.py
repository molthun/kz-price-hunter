"""Добавляет разметку расширения слоёв потерь в scripts/k2_labeled_sample.json. Вход: labels.json
{"номер строки unlabeled_titles.txt": {"label": "same|different|packaging|uncertain", "note": "причина", "sources": []}}."""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
ALLOWED = {"same", "different", "packaging", "uncertain"}


def merged(sample, chosen, labels):
    """Новая версия разметки: прежние строки не меняются, каждая новая пара размечена ровно один раз."""
    assert set(labels) == {str(i) for i in range(len(chosen))}, "размечены не все пары"
    seen = {frozenset((r["a"], r["b"])) for r in sample["pairs"]} | {frozenset((r["a"], r["b"])) for r in sample["known_pairs"]}
    rows = []
    for i, pair in enumerate(chosen):
        item = labels[str(i)]
        assert item["label"] in ALLOWED, i
        assert item["label"] == "same" or item.get("note"), f"{i}: у метки {item['label']} нет причины"
        assert frozenset((pair["a"], pair["b"])) not in seen, i
        rows.append({**pair, "label": item["label"], "note": item.get("note", ""), "sources": item.get("sources", []), "phase": "ext"})
    return {**sample, "version": sample["version"] + 1, "pairs": sample["pairs"] + rows}


def main():
    chosen, labels = read(HERE / "strata_sealed.json"), read(HERE / "labels.json")
    path = ROOT / "scripts/k2_labeled_sample.json"
    out = merged(read(path), chosen, labels)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(collections.Counter((r["strata"][0], r["label"]) for r in out["pairs"][-len(chosen):]))


if __name__ == "__main__":
    main()
