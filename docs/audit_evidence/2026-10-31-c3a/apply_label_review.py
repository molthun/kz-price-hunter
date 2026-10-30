"""Исправление разметки C3 (C3A-01, C3A-02): пересмотр меток и перенос прежних меток слоёв в отдельную перепись.

Вход: метки C3 (2026-10-30-c3/labeled_sample.json), прежняя разметка (git show d127f4e:scripts/k2_labeled_sample.json),
эталон k2_verified_pairs.json. Выход: scripts/k2_labeled_sample.json, журнал изменений label_changes.json. Базу не открывает.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
key = lambda row: frozenset((row["a"], row["b"]))

HP = ["https://www.hp.com/ca-en/shop/products/accessories/hyperx-cloud-iii-s-wireless-gaming-headset-white-pink-dg0r2aa",
      "https://www.hp.com/us-en/shop/pdp/hyperx-cloud-iii-s-wireless-gaming-headset-%28black-red%29"]
PACK = "упаковка указана только с одной стороны: OEM/BOX по названию не решить, решение владельца — это разные товары"
CHANGES = [
    ("Гарнитура HyperX Cloud III S - Gaming Headset (White-Pink) DG0R2AA", "same", "оба артикула — Wireless Cloud III S, разница в цвете (C3A-01)", HP),
    ("Гарнитура Razer Kaira X for Xbox - Pulse Red RZ04-03970500-R3M1|Наушники Razer Kaira X for Xbox RZ04-03970300-R3M1 White", "same", "цвета одной модели (C3A-01)", ["https://www.razer.com/console-headsets/razer-kaira-x-for-xbox"]),
    ("Смартфон TECNO SPARK 50 6 ГБ/128 ГБ 128 ГБ, 6.78’’, 6 Гб, Black, 128 ГБ Tecno, SPARK|Смартфон Tecno Spark 50, 8 GB, 128 GB, Halo Blue (KN4N)", "different", "6 ГБ и 8 ГБ памяти", []),
    ("Комплект оперативной памяти Kingston Fury Beast Beast White EXPO DDR5 DIMM 32Gb KIT (2x16Gb) 6000MHz (KF560C36BWE2K2-32)|Оперативная память 32 GB kit Kingston Fury Beast KF560C30BWEK2-32", "different", "тайминги CL36 и CL30 в артикуле", []),
    ("Комплект оперативной памяти Hiksemi (Hikvision) Future RGB, DDR4 16 GB kit (2x8GB) 3200MHz (HSC416U32C5 8G*2)|Оперативная память Hiksemi Future HSC416U32F3", "uncertain", "разные коды; комплект против неясной поставки", []),
    ("Видеокарта MaxSun MS-RX550 TF 4G|Видеокарта MaxSun RX 550, 4 GB, Radeon RX 550", "uncertain", "во втором названии нет модели карты", []),
    ("Видеокарта MaxSun MS-RX550 TF 4G (MS-RX550 TF 4G)|Видеокарта MaxSun RX 550, 4 GB, Radeon RX 550", "uncertain", "во втором названии нет модели карты", []),
    ("Видеокарта AFOX GT740 4GB D3 LP Single Fan AF740-4096D3L3|Видеокарта Afox GT 740, 4 GB, GeForce GT 740", "uncertain", "во втором названии нет артикула и ревизии", []),
    ("Видеокарта 8 GB Afox RX 580 2048SP AFRX580-8192D5H3-V3|Видеокарта Afox RX 580, 8 GB, Radeon RX 580", "uncertain", "во втором названии нет ревизии; как у соседней пары AFRX580", []),
    ("Bluetooth гарнитура Edifier W820NB Plus EDF200118, Grey|Наушники Edifier W820NB Plus v.2025 бежевый", "uncertain", "v.2025: как у соседней пары W820NB", []),
    ("Карта памяти Canvas Select Plus A1 U1 V10 128 ГБ Kingston Technology|Карта памяти SDXC 128 GB Kingston Canvas Select Plus, Class 10 UHS-I U1", "uncertain", "microSD или SD: во втором названии неясно", []),
    ("Планшет DOOGEE Tab A9 10,1\" 3GB/64GB Wi-Fi Grey|Планшет Doogee Tab A9 2025 Wi-Fi 10.1\" 3/64Gb Grey", "uncertain", "модель 2025 года или прежняя", []),
    ("Ноутбук Tecno MEGABOOK K16S 16\" FHD i3-1315U 8GB 512GB Win 11 Tecno MEGABOOK K16S|Ноутбук Tecno Megabook K16SAA 512GB 8GB", "uncertain", "процессор второго не указан", []),
    ("Наушники SOUNDCORE Liberty 5 Pro (D1203G31), Голубой TWS|Наушники soundcore Liberty 5 Pro D1203G21", "uncertain", "значение кодов G21/G31 источником не подтверждено", []),
    ("Наушники SOUNDCORE Liberty 5 Pro (D1203G31), Голубой TWS|Наушники soundcore Liberty 5 Pro D1203G11", "uncertain", "значение кодов G11/G31 источником не подтверждено", []),
    ("AirPods Max 2|Наушники Apple AirPods Max Silver", "uncertain", "поколение второго не указано; как у соседних пар AirPods Max 2", []),
    ("Оперативная память 16GB NOMAD NMD5600D5U46-16GB|Оперативная память NOMAD RGB NMD5600D5U46-16GBRGBW DDR5 16GB 5600MHz - White (NMD5600D5U46-16GBRGBW)", "uncertain", "у первого артикул без суффикса исполнения", []),
    ("Компьютерный корпус Gamemax Infinity Mini 3 ARGB Fans + Controller Black без Б/П|Корпус GameMax Infinity Mini BK, Black", "uncertain", "комплект второго не указан; как у соседней пары Infinity Mini", []),
]
PACK_PAIRS = [
    "Процессор Intel Core i3 10105|Процессор Intel Core i3 10105, LGA1200, OEM",
    "Процессор AMD Ryzen 5 4500 OEM (100-000000644)|Процессор AMD Ryzen 5 4500 Ryzen 5, 3.6 ГГц AMD",
    "Процессор Intel Core i3 10105|Процессор Intel Сore i3-10105 oem",
    "Intel Core i7 13700KF OEM|Процессор Intel Core i7 Processor 13700KF 1700 (i7-13700KF)",
    "Intel Core i3 10105 OEM|Процессор Intel Core i3 10105",
    "AMD Ryzen 5 5600 OEM|Процессор AMD Ryzen 5 5600 Ryzen 5, 3.5 ГГц AMD",
    "AMD Ryzen 5 7500F OEM|Процессор AMD Ryzen 5 7500F",
    "Процессор Intel Core i5-14400|Процессор Intel Core i5-14400 (CM8071504821112SRN46)",
    "Процессор AMD Ryzen 7 7700X OEM|Процессор AMD Ryzen 7 7700X Ryzen 7, 4.5 ГГц AMD",
    "Intel Core i7 13700F OEM|Процессор Intel Core i7-13700F (SRMBB)",
    "Процессор Intel Core i5 10500|Процессор Intel Core i5 10500, LGA1200, OEM",
    "Процессор CPU S-1700 Intel Core i5-13400 OEM|Процессор Intel Core i5 13400",
    "Intel Core i9 14900K OEM|Процессор Intel Core i9 14900K Core i9, 3.2 ГГц Intel",
    "AMD Ryzen 5 4500 OEM|Процессор AMD Ryzen 5 4500 Ryzen 5, 3.6 ГГц AMD",
]
HYPERX_B = "Наушники HyperX Cloud III S Wireless Gaming Headset A59Z0AA Red"
# Метка подтверждена источником, менять не пришлось: пригодится при следующей проверке
CONFIRMED = [
    {"pair": ["27\" BenQ 9H.LGXLA.CPE черный", "Монитор 27\" Benq BL2780"], "label": "same", "sources": ["https://mts.ee/en/toode/benq-bl2780-686-cm-27-1920-x-1080-pixels-full-hd-led-black-229396169"]},
    {"pair": ["Материнская плата ASRock B760M-H2/M.2 (90-MXBN00-A0UAYZ)", "Материнская плата ASRock B760M-H2/M.2 D5"], "label": "same", "note": "артикул описан как DDR5", "sources": ["https://www.terabyteshop.com.br/produto/30590/placa-mae-asrock-b760m-h2m2-chipset-b760-intel-lga-1700-matx-ddr5-90-mxbn00-a0uayz"]},
    {"pair": ["Гарнитура Razer Barracuda X 2022 - Quartz Pink RZ04-04430300-R3M1", "Наушники Razer Barracuda X 2022 Roblox Edition RZ04-04430400-R3M1"], "label": "different", "note": "Roblox Edition — не только цвет: виртуальный предмет в комплекте", "sources": ["https://gamesbeat.com/razer-roblox-partner-peripherals-bundled-digital-item/"]},
]


def main():
    labels = read(ROOT / "docs/audit_evidence/2026-10-30-c3/labeled_sample.json")
    previous = read(ROOT / "docs/audit_evidence/2026-10-31-c3-codex/previous-labels.json")
    verified = read(ROOT / "scripts/k2_verified_pairs.json")
    data = read(ROOT / "docs/audit_evidence/2026-10-30-c3/stand-data.json")
    layers = {key(p) for name in ("retained", "added", "dropped") for p in data[name]}

    plan = {}
    for text, label, reason, sources in CHANGES:
        a, _, b = text.partition("|")
        plan[frozenset((a, b or HYPERX_B))] = (label, reason, sources)
    for text in PACK_PAIRS:
        plan[frozenset(text.split("|"))] = ("uncertain", PACK, [])

    log, used = [], set()

    def apply(row, where):
        wanted = plan.get(key(row))
        if not wanted or wanted[0] == row["label"]:
            return row
        used.add(key(row))
        log.append({"a": row["a"], "b": row["b"], "where": where, "was": row["label"], "now": wanted[0], "reason": wanted[1], "sources": wanted[2]})
        return {**row, "label": wanted[0], "note": wanted[1]}

    undecided = {key(p) for p in verified["pairs"] if p.get("same") is None}
    pairs = []
    for row in labels["pairs"]:
        if key(row) in undecided and row["label"] != "uncertain":
            log.append({"a": row["a"], "b": row["b"], "where": "old_set", "was": row["label"], "now": "uncertain",
                        "reason": "в эталоне same: null; перенос C3 решил его за разметчика (C3A-01)", "sources": []})
            row = {**row, "label": "uncertain"}
        pairs.append(apply(row, "/".join(row["strata"])))
    fresh = {key(row) for row in pairs if row["strata"] != ["old_set"]}
    known = []
    for row in previous["pairs"]:
        if key(row) in layers and key(row) not in fresh:
            row = apply(row, "known")
            known.append({"a": row["a"], "b": row["b"], "label": row["label"], **({"note": row["note"]} if row.get("note") else {})})
    assert used == set(plan), [sorted(k) for k in set(plan) - used]

    out = {**labels, "version": 5,
           "protocol": labels["protocol"] + " Все 320 свежих и 537 прежних меток слоёв пересмотрены Claude 31.10.2026 по названиям, без ключей и слоёв "
                       "(аудит C3A-01). Изменения и источники: docs/audit_evidence/2026-10-31-c3a/label_changes.json. Сплошной проверки по производителю не было.",
           "sampling": labels["sampling"].replace("new_merges 40 из 439", "new_merges 40 из 414"),
           "known_pairs_note": "Пары слоёв, размеченные до отбора seed 20261031 и потому не имевшие шанса в него попасть: перепись известной части слоя (аудит C3A-02).",
           "pairs": pairs, "known_pairs": known}
    (ROOT / "scripts/k2_labeled_sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "label_changes.json").write_text(json.dumps({"changed": log, "confirmed_by_source": CONFIRMED}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(log), "изменений;", len(known), "пар переписи;", len(pairs), "пар в выборке")


if __name__ == "__main__":
    main()
