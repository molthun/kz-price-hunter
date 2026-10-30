"""Разбор открытых пар слоя added по источникам. Запускается один раз поверх apply_label_review.py; базу не открывает.

Метка меняется только при подтверждении источником, в любую сторону. Остальные остаются uncertain с причиной.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
SAMPLE = ROOT / "scripts/k2_labeled_sample.json"
LOG = HERE / "label_changes.json"
MAX2 = ["https://support.apple.com/en-us/121205", "https://www.apple.com/airpods-max/specs/"]
SOUNDCORE = ["https://www.anker.com/uk/products/d1203-liberty-5-pro-anc-earbuds-clear-calls", "https://www.ceneo.pl/196456029", "https://www.ceneo.pl/196455960", "https://www.ceneo.pl/196456001",
             "https://www.heavins.ie/products/soundcore-liberty-5-pro-ear-buds-white-d1203g21"]
CHANGES = [
    ("Наушники SOUNDCORE Liberty 5 Pro (D1203G31), Голубой TWS", "Наушники soundcore Liberty 5 Pro D1203G21", "same",
     "G31 голубой, G21 белый: разница в цвете", SOUNDCORE),
    ("Наушники SOUNDCORE Liberty 5 Pro (D1203G31), Голубой TWS", "Наушники soundcore Liberty 5 Pro D1203G11", "same",
     "G31 голубой, G11 чёрный: разница в цвете", SOUNDCORE),
    ("Батарейка дисковая литиевая PANASONIC LRV08/1B", "Батарейка щелочная LRV08 LRV08, щелочной, 1 шт. Panasonic", "same",
     "LRV08/1B — щелочная 12 В, одна штука; «литиевая» в первом названии ошибочна",
     ["https://www.panasonic.com/sg/consumer/energy-solutions/battery/specialty-batteries/alkaline-button-others/lr-v08-1bpa.html"]),
    ("Коврик для мыши 2E Gaming Pro Speed XL", "Коврик для мыши Gaming Pro Speed полиэстер, резина, 36×27.5 см, 36 × 27.5 × 0,3 см 2Е", "different",
     "XL — 800×450 мм, 360×275 мм — размер M",
     ["https://shop.2egaming.com/en/new-2e-gaming-pro-series-game-surfaces/", "https://e-katalog.kz/ua/2E-GAMING-PRO-MOUSE-PAD-SPEED-XL.htm", "https://metromart.ge/shop/product/2647-Mousepad-2E-Gaming-PRO-Speed-2E-SPEED-M-BK-PRO-Black-22690"]),
    ("AirPods Max 2", "Apple AirPods Max Orange беспроводные наушники (MWW73)", "different",
     "MWW73 — USB-C 2024 года на H1; AirPods Max 2 — поколение 2026 года на H2", MAX2 + ["https://www.bestbuy.com/product/apple-airpods-max-usb-c-orange/JJGCQ3ZZ5H"]),
    ("AirPods Max 2", "Наушники APPLE AirPods MAX (MWW63ZE/A) Blue", "different",
     "MWW63 — USB-C 2024 года на H1; AirPods Max 2 — поколение 2026 года на H2", MAX2 + ["https://lcd-phone.com/en/apple-store/33248-airpods-max-usb-c-2024-blue-retail-box-apple-mww63zma-0195949544309.html"]),
]
# Источник не найден или не читается: метка не меняется
KEPT = {"упаковка только с одной стороны": "по названию не решить; нужна карточка предложения или правило владельца",
        "Edifier W820NB Plus EDF200118 и v.2025": "связь кода с версией 2025 только в выдаче поиска, страница магазина не открылась (403)",
        "Jabra Evolve2 50 25089-999-799 и USB-C без артикула": "артикул второго предложения не подтверждён на срезе; нынешняя карточка Forte указывает тот же 25089-999-799 (OA-01)",
        "AFOX AF1660S H4-V2 и H7-V2": "различие кодов источником не описано",
        "AFOX GT 740, MaxSun RX 550": "во втором названии нет кода карты, источник этого не решит"}


def main():
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    log = json.loads(LOG.read_text(encoding="utf-8"))
    rows = {frozenset((r["a"], r["b"])): (r, where) for where in ("pairs", "known_pairs") for r in sample[where]}
    for a, b, label, reason, sources in CHANGES:
        row, where = rows[frozenset((a, b))]
        assert row["label"] == "uncertain", (a, b, row["label"])
        log["changed"].append({"a": a, "b": b, "where": f"open_added/{where}", "was": row["label"], "now": label, "reason": reason, "sources": sources})
        row["label"], row["note"] = label, reason
    log["open_added_kept"] = KEPT
    sample["open_added_review"] = "Открытые пары added разобраны по источникам 31.10.2026 (Claude): только подтверждённые, в любую сторону; см. label_changes.json. Исполнитель знал итоги, проверка не независимая."
    SAMPLE.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(CHANGES), "изменений")


if __name__ == "__main__":
    main()
