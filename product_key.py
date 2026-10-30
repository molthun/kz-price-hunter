"""Стандарт ключа товара, не зависящий от AI-модели (P17-C1, docs/DEVELOPMENT_PLAN.md).

Ключ решает, какие товары разных магазинов — один товар, и по нему сравниваются цены. Раньше модель писала
строку ключа сама, и у каждой модели был свой формат («32.5» и «32.5mm», «null» и «0», транслит бренда):
смена модели ломала сопоставление (замер 29.10.2026: ключи qwen3-vl совпали с Gemini у 17 из 60 названий).

Теперь модель только извлекает поля по фиксированной схеме (FIELDS_SCHEMA), а ключ собирает этот модуль
по правилам. Ключ одинаков для любой модели, если поля извлечены верно:

    k2:<бренд>:<модель или артикул>[:<варианты>]

- бренд — по справочнику синонимов (model_matching.BRAND_MAP + BRAND_ALIASES), иначе транслит;
- модель и артикул — нижний регистр, только латиница и цифры, без пробелов, дефисов, цветов и маркетинга;
- варианты — «число + единица» по таблице единиц (ГБ → gb, ТБ и от 1000 ГБ → tb, дюйм и диагональ в см → in, л → l; габариты и частоты не входят),
  десятичная точка, сортировка, через «+»;
- пустое — пусто: никаких «0»/«null»;
- без бренда (или «без бренда») — ключа нет: товар не сравнивается автоматически, чтобы не склеить разное;
- ревизия и готовый компьютер — метки в конце идентичности: «m2442ph.v3», «ryzen55600gt.system» (аудит C45-01).

Префикс «k2:» — версия правил. Если правила поменяются, ключи пересобираются из сохранённых полей без
повторных вызовов модели (KEY_VERSION).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Optional

KEY_VERSION = "k2"

# Что модель обязана вернуть на каждое название (текст для подсказки и проверка ответа)
FIELDS_SCHEMA = {
    "brand": "бренд производителя как на упаковке; null, если бренда нет",
    "model": "серия и модель без бренда, цвета, комплектации и маркетинга; null, если нет",
    "sku": "артикул/код модели производителя (не артикул магазина); null, если нет",
    "edition": "исполнение внутри модели: Gaming, Dual, OC, Edition, ревизия V2/V3; null, если нет (ревизию код добавит в ключ)",
    "variants": "список отличающих исполнение характеристик: [{\"value\": число, \"unit\": \"гб|тб|мм|см|л|мл|г|кг|дюйм|вт|мач|шт\"}]",
    "unbranded": "true, если товар без бренда (noname, «без бренда»)",
}

# Синонимы брендов сверх model_matching.BRAND_MAP: написания, которые встречаются в названиях магазинов
BRAND_ALIASES = {
    "lg electronics": "lg", "элджи": "lg", "филипс": "philips", "бош": "bosch", "бош сименс": "bosch",
    "тефаль": "tefal", "редмонд": "redmond", "поларис": "polaris", "витек": "vitek", "витэк": "vitek",
    "китфорт": "kitfort", "делонги": "delonghi", "de'longhi": "delonghi", "де лонги": "delonghi",
    "дайсон": "dyson", "хайсенс": "hisense", "хаер": "haier", "хайер": "haier", "беко": "beko",
    "индезит": "indesit", "аристон": "ariston", "атлант": "atlant", "горенье": "gorenje",
    "электролюкс": "electrolux", "канон": "canon", "никон": "nikon", "джбл": "jbl", "маршал": "marshall",
    "логитек": "logitech", "рейзер": "razer", "хайперикс": "hyperx", "тп-линк": "tplink", "tp-link": "tplink",
    "кингстон": "kingston", "western digital": "wd", "вд": "wd", "сигейт": "seagate", "интел": "intel",
    "амд": "amd", "оппо": "oppo", "виво": "vivo", "реалми": "realme", "ван плюс": "oneplus",
    "one plus": "oneplus", "текно": "tecno", "инфиникс": "infinix", "моторола": "motorola", "нокиа": "nokia",
    "яндекс": "yandex", "yandeks": "yandex", "сбер": "sber", "асрок": "asrock",
    "patriot memory": "patriot", "patriotmemory": "patriot", "team group": "teamgroup",
}

# Заглушки «нет значения» в полях
_EMPTY = {"", "0", "null", "none", "nil", "n/a", "na", "-", "—", "unknown", "noname", "no name", "нет",
          "без бренда", "unbranded", "generic", "ноунейм", "no brand"}

# Слова, которые не отличают модель: цвета, типы товара, маркетинг
_NOISE = {
    "черный", "белый", "серый", "синий", "зеленый", "красный", "бежевый", "серебристый", "золотой",
    "фиолетовый", "розовый", "голубой", "желтый", "оранжевый", "коричневый", "графитовый",
    "black", "white", "gray", "grey", "blue", "green", "red", "silver", "gold", "purple", "pink",
    "beige", "yellow", "orange", "brown", "graphite", "midnight", "starlight", "titanium", "natural",
    "new", "новинка", "original", "оригинал", "global", "version", "eu", "ru", "kz",
    # Маркетинг телевизоров: «QLED 50E7S» и «50E7S» — одна модель (разбор потерь C2)
    "qled", "oled", "miniled", "led", "uhd", "fhd", "smart", "tv", "4k",
    # Имена линеек производителя чипа, которые одни магазины пишут, а другие нет (замер C2)
    "geforce", "radeon",
}

# Единицы, которые отличают исполнение одной модели и поэтому входят в ключ. Длины (габариты), частоты и
# прочее в ключ не идут: по замеру C2 модель записывала туда размеры корпуса и «3500 Гц» из «Ryzen 5 3500X»,
# и одинаковые товары расходились по разным ключам. Диагональ — только в дюймах: «109 см» у телевизора и
# «43"» в соседнем магазине — один товар, поэтому сантиметры ≥ 50 переводятся в дюймы.
KEY_UNITS = {"gb", "mb", "l", "ml", "g", "kg", "in", "w", "mah", "pcs"}

# Единицы: синоним → (каноническая единица, множитель)
_UNITS = {
    "гб": ("gb", 1), "gb": ("gb", 1), "тб": ("gb", 1024), "tb": ("gb", 1024),
    "мб": ("mb", 1), "mb": ("mb", 1),
    "мм": ("mm", 1), "mm": ("mm", 1), "см": ("mm", 10), "cm": ("mm", 10), "м": ("m", 1), "m": ("m", 1),
    "л": ("l", 1), "l": ("l", 1), "литр": ("l", 1), "мл": ("ml", 1), "ml": ("ml", 1),
    "г": ("g", 1), "гр": ("g", 1), "g": ("g", 1), "кг": ("kg", 1), "kg": ("kg", 1),
    "дюйм": ("in", 1), "дюйма": ("in", 1), "дюймов": ("in", 1), '"': ("in", 1), "in": ("in", 1), "inch": ("in", 1),
    "вт": ("w", 1), "w": ("w", 1), "квт": ("w", 1000), "kw": ("w", 1000),
    "мач": ("mah", 1), "mah": ("mah", 1), "шт": ("pcs", 1), "pcs": ("pcs", 1),
    "гц": ("hz", 1), "hz": ("hz", 1),
}

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya", "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u",
    "ү": "u", "һ": "h", "і": "i",
}


def _text(value: Any) -> str:
    if value is None or isinstance(value, (bool, dict, list)):
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().lower().replace("ё", "е")


def _latin(text: str) -> str:
    return "".join(_CYR.get(ch, ch) for ch in text)


def brand_token(value: Any) -> Optional[str]:
    """Бренд к одному виду: справочник синонимов, иначе транслит; только [a-z0-9]."""
    from model_matching import BRAND_MAP
    text = _text(value)
    if text in _EMPTY:
        return None
    mapped = BRAND_ALIASES.get(text) or BRAND_MAP.get(text)
    if mapped:
        return mapped
    text = re.sub(r"\s+(technology|technologies|electronics|memory|corporation|corp|inc|ltd|co)\.?$", "", text)
    mapped = BRAND_ALIASES.get(text) or BRAND_MAP.get(text)
    if mapped:
        return mapped
    token = re.sub(r"[^a-z0-9]+", "", _latin(text))
    if not token or token in _EMPTY:
        return None
    return BRAND_ALIASES.get(token) or BRAND_MAP.get(token, token)


def model_token(value: Any, brand: Optional[str] = None, drop: Iterable[Any] = ()) -> Optional[str]:
    """Модель или артикул: без бренда, цветов и маркетинга, только [a-z0-9] слитно.

    drop — поля, которые модель иногда приклеивает к модели (артикул, исполнение): их слова вычищаются,
    чтобы «B850 PRO RS 90-MXBQK0» и «B850 PRO RS» дали одну модель (замер C2)."""
    text = _text(value)
    if text in _EMPTY:
        return None
    for extra in drop:
        extra_text = _text(extra)
        if extra_text and extra_text not in _EMPTY and extra_text != text:
            text = text.replace(extra_text, " ")
    words = re.findall(r"[a-zа-я0-9]+(?:[.,][0-9]+)?", text)
    kept = []
    for w in words:
        w = w.replace(",", ".")
        if w in _NOISE or (brand and _latin(w) == brand):
            continue
        kept.append(_latin(w))
    token = re.sub(r"[^a-z0-9.]+", "", "".join(kept)).strip(".")
    return token or None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:[.,]\d+)?", _text(value))
    return float(m.group(0).replace(",", ".")) if m else None


def _fmt(number: float) -> str:
    return str(int(number)) if number == int(number) else f"{number:.3f}".rstrip("0").rstrip(".")


# Объём памяти и диагональ код берёт из самого названия: по замеру C2 модель через раз не вписывала их в
# variants («Kingston NV3 2 TB» → то с «2048gb», то без), и один товар расходился по двум ключам.
_TITLE_VARIANT_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(гб|gb|тб|tb|см|дюйм\w*|\"|''|”)(?![a-zа-я])(?!/s\b)")


def title_variants(title: Any) -> List[Dict[str, Any]]:
    """Объём (ГБ/ТБ) и диагональ (дюймы от 10, сантиметры 50–250) из названия в виде variants модели."""
    text = _text(title)
    # «4/128Gb», «8/256 ГБ» — оперативная и встроенная память одной записью
    found = [{"value": float(first), "unit": unit}
             for first, unit in re.findall(r"(?<![\w.])(\d+)\s*/\s*\d+\s*(гб|gb|тб|tb)(?![a-zа-я])", text)]
    for number, unit in _TITLE_VARIANT_RE.findall(text):
        value = float(number.replace(",", "."))
        if unit in ("''", "”"):
            unit = '"'
        if unit.startswith("дюйм"):
            unit = "дюйм"
        if unit in ('"', "дюйм") and value < 10:
            continue                                    # 2.5" и 3.5" у дисков — форм-фактор, не вариант
        if unit == "см" and not 50 <= value <= 250:
            continue                                    # габарит, а не диагональ
        found.append({"value": value, "unit": unit})
    return found


def _storage(value_gb: float) -> str:
    """1000 ГБ и 1 ТБ (1024 ГБ) — один объём: от 1000 ГБ — в терабайтах с одним знаком."""
    if value_gb < 1000:
        return _fmt(value_gb) + "gb"
    tb = value_gb / 1024 if value_gb % 1024 == 0 else value_gb / 1000
    return _fmt(round(tb, 1)) + "tb"


def variant_tokens(variants: Any) -> List[str]:
    """Варианты к виду «число+единица»: 128gb, 2tb, 0.96l, 55in. Неизвестная единица или нет числа — пропуск."""
    out = set()
    for item in variants if isinstance(variants, list) else []:
        if isinstance(item, dict):
            value, unit = item.get("value"), _text(item.get("unit")).strip(". ")
        else:
            text = _text(item)
            m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(.+?)\s*$", text)
            value, unit = (m.group(1), m.group(2)) if m else (None, "")
        number = _number(value)
        if number is None or number <= 0 or unit not in _UNITS:
            continue
        canon, mult = _UNITS[unit]
        value_c = number * mult
        if canon == "mm" and 500 <= value_c <= 2500:
            canon, value_c = "in", round(value_c / 25.4)       # диагональ экрана в сантиметрах
        if canon not in KEY_UNITS or (canon == "in" and value_c < 10):
            continue                                        # экран телефона (6.78") не отличает его варианты
        out.add(_storage(value_c) if canon == "gb" else _fmt(value_c) + canon)
    return sorted(out)[:4]


# Ревизия (V2, V3, rev 1.1) — другой товар, а не оформление: Sanc M2442PH V2 и V3 склеивались (аудит C45-01).
# Только как отдельное слово: «NV3» и «V3» внутри артикула ревизией не считаются.
_REVISION_RE = re.compile(r"(?<![a-z0-9.])(?:v|ver|rev)\.?\s?(\d{1,2}(?:\.\d)?)(?![a-z0-9])")
_REVISION_MARK_RE = re.compile(r"\.(v\d{1,2}(?:\.\d)?)(?:\.system)?$")
# Названия процессоров и видеокарт: у готового компьютера с таким чипом та же «модель», что у самого чипа
_COMPONENT_RE = re.compile(r"^(?:ryzen|athlon|threadripper|epyc|core[iu]?\d|corei|i[3579]\d|xeon|pentium|celeron|"
                           r"rtx\d|gtx\d|rx\d|arc[ab]\d)")
_SYSTEM_WORD_RE = re.compile(r"компьютер|системн\w* блок|системник|(?<![a-zа-я0-9])пк(?![a-zа-я0-9])|неттоп|ноутбук|"
                             r"моноблок|сборка|laptop|notebook|mini ?pc|workstation")
_COMPONENT_WORD_RE = re.compile(r"процессор|видеокарт|(?<![a-z0-9])(?:cpu|gpu)(?![a-z0-9])")
_SYSTEM_PARTS = (r"(?<![a-z])ram(?![a-z])|озу", r"ssd|hdd|nvme", r"dvd", r"(?<![a-z0-9])\d{3,4}\s?(?:w|вт)(?![a-z])",
                 r"windows|win ?1[01]|freedos|dos(?![a-z])")
SYSTEM_MARK = ".system"
_MARK_RE = re.compile(r"\.(system|box|wifi|flex|acc|ms|uc|link\d+[a-z]?)(?=\.|$)")
# Правила задачи качества ключей (разбор ложных склеек C2). Действуют только в своём классе товаров: в других
# те же слова ничего не значат
_CPU_RE = re.compile(r"^(?:ryzen|athlon|threadripper|epyc|core|i[3579]\d|xeon|pentium|celeron)")
_BOX_RE = re.compile(r"(?<![a-z])(?:box|wof)(?![a-z])|бокс|с кулером|with\b.*\bcooler")
_CABLE_RE = re.compile(r"кабел|патч|patch cord|шнур|удлинител|сетевой фильтр|фильтр сетевой")
_LENGTH_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?(?:м|m)(?![a-zа-я0-9])")
_LENGTH_IN_CODE_RE = re.compile(r"[-\s]?\d+(?:[.,]\d+)?\s?[mм](?:/[a-z]{1,2})?(?![a-zа-я0-9])")
_WATCH_RE = re.compile(r"часы|watch|fenix|forerunner|epix")
_WATCH_SIZE_RE = re.compile(r"(?<![\d.,])(3[89]|4\d|5[0-5])\s?(?:мм|mm)(?![a-zа-я])")
_RAM_RE = re.compile(r"оперативн|память\b.*\b(?:ddr|dimm)|модул\w* памяти|(?<![a-z])(?:dimm|ddr-?[345])(?![a-z])")
_RAM_CODE_RE = re.compile(r"(?<![a-z0-9])[a-z]{2,}\d[a-z0-9/-]{5,}")
_RAM_SPEC_RE = re.compile(r"(?:lp)?ddr-?\d|pc\d|sodimm|udimm|dimm")
_DDR_RE = re.compile(r"(?<![a-z])ddr-?([345])(?![0-9])")
_BOARD_BRANDS = {"asrock", "asus", "msi", "gigabyte", "maxsun", "biostar"}
_CHIPSET_RE = re.compile(r"^[abhxz]\d{3}")
_WIFI_RE = re.compile(r"wi-?fi")
_BOARD_RE = re.compile(r"материнск")
_ACCESSORY_RE = re.compile(r"^\W*(?:пульт|геймпад|контроллер|чехол|стекло|пл[её]нка|ремешок|держатель|сумка|зарядн\w*)(?![a-zа-я])")
_ADAPTER_RE = re.compile(r"(?<![a-z0-9])link\s?(\d{2,3}[a-z]?)(?![a-z0-9])")
_PLATFORM_RE = re.compile(r"(?<![a-z])(ms|uc)(?![a-z])")
_GPU_IN_TITLE_RE = re.compile(r"(?<![a-z0-9])(?:rtx|gtx|rx)\s?\d|видеокарт")
_CHIPSET_IN_TITLE_RE = re.compile(r"(?<![a-z0-9])[abhxz]\d{3}m?(?![0-9])")
_CHIP_IN_TITLE_RE = re.compile(r"ryzen|athlon|threadripper|epyc|core\s?i\d|(?<![a-z0-9])i[3579]-\d|xeon|pentium|celeron|"
                               r"(?<![a-z0-9])(?:rtx|gtx|rx)\s?\d")
_SPEED_CLASS_RE = re.compile(r"карт\w* памяти|micro ?sd|sdxc|sdhc|(?<![a-z0-9])[ua][123](?![a-z0-9])")


def _revision(fields: Dict[str, Any], identity: str, title: Any) -> Optional[str]:
    """Ревизия: сначала из названия, сразу за концом модели («M2442PH V3», но не «Bluetooth V5»), оно надёжнее
    ответа модели; затем из edition, артикула и модели. Вид — v<номер>. У карт памяти V30 — класс скорости."""
    text = _text(title)
    if _SPEED_CLASS_RE.search(text):
        return None
    words = re.findall(r"[a-zа-я0-9]+(?:[.,][0-9]+)?", text)
    for i, word in enumerate(words[1:], 1):
        found = re.fullmatch(r"(?:v|ver|rev)(\d{1,2}(?:\.\d)?)", word)
        if found and identity.endswith(_latin(words[i - 1])):
            return "v" + found.group(1)
    for name in ("edition", "sku", "model"):
        found = _REVISION_RE.search(_text(fields.get(name)))
        if found:
            return "v" + found.group(1)
    return None


def _is_system(identity: str, title: Any) -> bool:
    """Готовый компьютер с процессором или видеокартой в названии, а не сам чип: слово или хотя бы две части сборки.
    Для остальных товаров не применяется: «Ноутбук Asus X515» и «Asus X515» — один товар (аудит C45-01)."""
    if not _COMPONENT_RE.match(identity):
        return False
    text = _text(title)
    parts = sum(bool(re.search(p, text)) for p in _SYSTEM_PARTS)
    return parts >= 2 or (bool(_SYSTEM_WORD_RE.search(text)) and not _COMPONENT_WORD_RE.search(text))


def _short_sku_suffix(fields: Dict[str, Any], identity: str, brand: str, title: Any) -> str:
    """Короткий номер в поле артикула, стоящий в названии сразу за моделью: model «Ryzen 5», sku «7600X», название
    «AMD Ryzen 5 7600X BOX» — это Ryzen 5 7600X, а не Ryzen 5 (потеря правильной пары, аудит C45-08)."""
    sku = model_token(fields.get("sku"), brand)
    if not sku or _STRONG_ARTICLE.match(sku) or not re.search(r"\d", sku) or sku in identity:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", _latin(_text(title)))
    return sku if identity + sku in compact else ""


def _strong_code(fields: Dict[str, Any], brand: str, title: str) -> Optional[str]:
    """Полный код производителя: из поля артикула, иначе из названия."""
    sku = model_token(fields.get("sku"), brand)
    if sku and _STRONG_ARTICLE.match(sku) and not _RAM_SPEC_RE.match(sku):
        return sku
    return _title_ram_code(title)


def _title_ram_code(title: str) -> Optional[str]:
    """Первый код производителя в названии. DDR4-3200 и PC4-25600 — стандарт, он общий у разных моделей (C2R-01)."""
    for found in _RAM_CODE_RE.findall(title):
        if not _RAM_SPEC_RE.match(found):
            return re.sub(r"[^a-z0-9]", "", found)
    return None


def _class_marks(identity: str, brand: str, title: str) -> str:
    """Метки того, что внутри класса товаров делает его другим товаром, а ключ по модели не видит: поставка процессора
    (решение владельца: OEM и BOX разные), Wi-Fi платы, адаптер и платформа гарнитуры, аксессуар к товару."""
    marks = []
    if _CPU_RE.match(identity) and _BOX_RE.search(title):
        marks.append("box")
    if brand in _BOARD_BRANDS and _CHIPSET_RE.match(identity) and (_WIFI_RE.search(title) or "wifi" in identity):
        marks.append("wifi")
    if brand == "jabra":
        if "flex" in title or "flex" in identity:
            marks.append("flex")
        adapter, platform = _ADAPTER_RE.search(title), _PLATFORM_RE.search(title)
        marks += [f"link{adapter.group(1)}"] if adapter else []
        marks += [platform.group(1)] if platform else []
    if _ACCESSORY_RE.search(title):
        marks.append("acc")
    return "".join(f".{m}" for m in marks)


def _class_variants(title: str, ram_without_code: bool) -> List[str]:
    """Размеры, которые только в своём классе отличают товар: длина кабеля, диаметр часов, тип памяти без кода."""
    if _CABLE_RE.search(title):
        found = _LENGTH_RE.search(title)
        return [f"{_fmt(float(found.group(1).replace(',', '.')))}m"] if found else []
    if _WATCH_RE.search(title):
        found = _WATCH_SIZE_RE.findall(title)
        return [f"{found[-1]}mm"] if found else []
    if ram_without_code:
        found = _DDR_RE.search(title)
        return [f"ddr{found.group(1)}"] if found else []
    return []


def build_key(fields: Any, title: Any = None) -> Optional[str]:
    """Ключ по стандарту из полей модели или None (без бренда, без модели и артикула, мусор).

    title — исходное название: объём и диагональ из него заменяют то, что модель записала в variants
    в тех же единицах (память и дюймы), остальные варианты берутся от модели."""
    if not isinstance(fields, dict) or fields.get("unbranded") is True:
        return None
    brand = brand_token(fields.get("brand"))
    if not brand:
        return None
    text = _text(title)
    model, sku = fields.get("model"), fields.get("sku")
    if _CABLE_RE.search(text):
        # Длина и цвет в коде кабеля (PP12-3M/B) — не другая модель: длина уйдёт в варианты
        model, sku = (_LENGTH_IN_CODE_RE.sub("", _text(v)) if isinstance(v, str) else v for v in (model, sku))
    identity = model_token(model, brand, (sku, fields.get("edition"))) or model_token(sku, brand)
    if not identity:
        return None
    is_ram = bool(_RAM_RE.search(text)) and not _BOARD_RE.search(text) and not _CHIPSET_RE.match(identity)
    ram_code = _strong_code(fields, brand, text) if is_ram else None
    # У памяти линейка (Fury Beast) объединяет разные частоты и типы, товар определяет только код
    identity = ram_code or identity
    identity += _short_sku_suffix(fields, identity, brand, title)
    revision = _revision(fields, identity, title)
    if revision:
        identity = re.sub(rf"(?:v|ver|rev)\.?{re.escape(revision[1:])}$", "", identity) or identity
    plain_identity = identity                           # метки не участвуют в проверке «диагональ в номере модели»
    marks = _class_marks(plain_identity, brand, text)
    # Модификатор один независимо от того, записала ли его модель в model: «b850mxwifi» и «b850mx» с Wi-Fi (C2R-04)
    for word in ("wifi", "flex"):
        if f".{word}" in marks:
            identity = identity.replace(word, "") or identity
    plain_identity = identity
    identity += marks
    if revision:
        identity += f".{revision}"
    if _is_system(plain_identity, title):
        identity += SYSTEM_MARK
    variants = variant_tokens(fields.get("variants"))
    from_title = variant_tokens(title_variants(title)) if title else []
    if from_title:
        own = {"in"} if any(v.endswith("in") for v in from_title) else set()
        if any(v.endswith(("gb", "tb")) for v in from_title):
            own |= {"gb", "tb"}
        variants = sorted({v for v in variants if re.sub(r"^[0-9.]+", "", v) not in own} | set(from_title))[:4]
    # Диагональ, которая уже есть в номере модели (65E7S, 27GP850, U11), в ключ не идёт: одни магазины
    # пишут «65"», другие нет, и один телевизор расходился по двум ключам (замер C2)
    variants = sorted(set(variants) | set(_class_variants(text, is_ram and not ram_code)))[:4]
    variants = [v for v in variants
                if not (v.endswith("in") and re.search(rf"(?<![0-9]){re.escape(v[:-2].split('.')[0])}(?![0-9])", plain_identity))]
    key = f"{KEY_VERSION}:{brand}:{identity}"
    return f"{key}:{'+'.join(variants)}" if variants else key


def keys_for(items: Iterable[Dict[str, Any]], titles: Iterable[str]) -> Dict[str, Optional[str]]:
    """Ответ модели {items: [{title, ...поля}]} → {название: ключ или None} только для названий пакета."""
    allowed = set(titles)
    found: Dict[str, Optional[str]] = {}
    for item in items or []:
        if isinstance(item, dict) and item.get("title") in allowed:
            found[item["title"]] = build_key(item, item["title"])
    return found


def no_key_reason(fields: Any) -> Optional[str]:
    """Почему ключа нет: 'no_identity' — модель подтвердила отсутствие бренда, сравнивать нельзя;
    'incomplete' — ответ неполный или противоречивый, прежняя идентичность не снимается (аудит C45R-02);
    None — ключ строится. Отсутствующее поле — не подтверждение: подтверждает только unbranded=true
    или явно записанная пустышка вместо бренда («noname»)."""
    if not fields_well_formed(fields):
        return "incomplete"
    if build_key(fields):
        return None
    brand = fields.get("brand")
    if brand_token(brand):
        return "incomplete"                             # бренд есть, а модели нет, или «без бренда» при бренде
    if fields.get("unbranded") is True or (isinstance(brand, str) and brand.strip()):
        return "no_identity"
    return "incomplete"


def fields_well_formed(fields: Any) -> bool:
    """Поля ответа модели правильных типов, и обязательные brand и model присутствуют (null допустим)."""
    if not isinstance(fields, dict) or "brand" not in fields or "model" not in fields:
        return False
    for name in ("brand", "model", "sku", "edition"):
        value = fields.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
            return False
    return (fields.get("variants") is None or isinstance(fields["variants"], list)) \
        and (fields.get("unbranded") is None or isinstance(fields["unbranded"], bool))


def fields_usable(fields: Any) -> bool:
    """Ответ можно принять: из него строится ключ или он подтверждает отсутствие идентичности."""
    return no_key_reason(fields) != "incomplete"


def marks_of(key: str) -> frozenset:
    """Метки класса в идентичности ключа: то, что делает товар другим при той же модели (без ревизии)."""
    return frozenset(_MARK_RE.findall(_identity_of(key)))


def is_system_key(key: str) -> bool:
    return _identity_of(key).endswith(SYSTEM_MARK)


def revision_of(key: str) -> str:
    """Ревизия по ключу: «v3» у …m2442ph.v3 и у старого формата «sanc:m2442ph v3:23.8», иначе пусто."""
    if key.startswith(f"{KEY_VERSION}:"):
        found = _REVISION_MARK_RE.search(_identity_of(key))
        return found.group(1) if found else ""
    parts = key.split(":")
    found = _REVISION_RE.search(parts[1]) if len(parts) > 1 else None
    return f"v{found.group(1)}" if found else ""


def _unit_class(token: str) -> str:
    """Единицы одной величины к одной: 1 кг = 1000 г, 1 л = 1000 мл (ГБ и ТБ приводит variant_tokens)."""
    found = re.fullmatch(r"(\d+(?:\.\d+)?)(kg|l)", token)
    return f"{_fmt(float(found.group(1)) * 1000)}{'g' if found.group(2) == 'kg' else 'ml'}" if found else token


def _key_variants(key: str) -> Optional[set]:
    """Варианты ключа с единицами; None — в старом ключе есть число, единицу которого не восстановить.

    Голое число от 10 в старом формате — диагональ, так писала её подсказка v1 («lg:27gp850:27»)."""
    if key.startswith(f"{KEY_VERSION}:"):
        return {_unit_class(v) for v in _variants_of(key).split("+") if v}
    found = set()
    for token in key.split(":")[2:]:
        token = token.strip().lower()
        if not re.search(r"\d", token):
            continue                                    # box, oem — не варианты
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            if float(token) < 10:
                return None
            token += "in"
        typed = variant_tokens([token])
        if not typed:
            return None
        found.add(_unit_class(typed[0]))
    return found


def keys_conflict(a: str, b: str) -> bool:
    """Ключи заведомо о разном, и никакой вердикт их не склеит: варианты, ревизия, компонент и готовый компьютер.
    Работает и со старым форматом (аудит C45R-01)."""
    va, vb, ra, rb = _key_variants(a), _key_variants(b), revision_of(a), revision_of(b)
    return bool((va and vb and va != vb) or (ra and rb and ra != rb) or marks_of(a) != marks_of(b))


def _title_revision(title: str) -> str:
    """Ревизия по одному названию, когда полей нет: грубо, но ошибка здесь только откажет в объединении."""
    text = _text(title)
    found = None if _SPEED_CLASS_RE.search(text) else _REVISION_RE.search(text)
    return f"v{found.group(1)}" if found else ""


def _title_is_system(title: str) -> bool:
    """Готовый компьютер по одному названию, когда полей нет: чип в тексте и признаки сборки (грубо, как и ревизия)."""
    text = _text(title)
    if not _CHIP_IN_TITLE_RE.search(text):
        return False
    parts = sum(bool(re.search(p, text)) for p in _SYSTEM_PARTS)
    return parts >= 2 or (bool(_SYSTEM_WORD_RE.search(text)) and not _COMPONENT_WORD_RE.search(text))


def _title_marks(title: str) -> frozenset:
    """Метки по одному названию, когда полей нет: грубо, ошибка здесь только откажет в объединении (C2R-03)."""
    text = _text(title)
    marks = set()
    if _title_is_system(title):
        marks.add("system")
    if _BOX_RE.search(text) and _CHIP_IN_TITLE_RE.search(text) and not _GPU_IN_TITLE_RE.search(text):
        marks.add("box")
    if _WIFI_RE.search(text) and (_BOARD_RE.search(text) or _CHIPSET_IN_TITLE_RE.search(text)):
        marks.add("wifi")
    if "jabra" in text:
        marks |= {"flex"} if "flex" in text else set()
        adapter, platform = _ADAPTER_RE.search(text), _PLATFORM_RE.search(text)
        marks |= {f"link{adapter.group(1)}"} if adapter else set()
        marks |= {platform.group(1)} if platform else set()
    if _ACCESSORY_RE.search(text):
        marks.add("acc")
    return frozenset(marks)


def _title_sizes(title: str) -> frozenset:
    """Объём, диагональ и классовые размеры, известные из самого названия."""
    text = _text(title)
    ram = bool(_RAM_RE.search(text)) and not _BOARD_RE.search(text) and not _CHIPSET_IN_TITLE_RE.search(text)
    return frozenset(variant_tokens(title_variants(title))) | frozenset(_class_variants(text, ram))


def _size_class(token: str) -> str:
    if token.startswith("ddr"):
        return "ddr"
    unit = re.sub(r"^[0-9.]+", "", token)
    return "memory" if unit in ("gb", "tb") else unit


def _capacity_conflict(first: Iterable[str], second: Iterable[str]) -> bool:
    """Размеры, известные из названия, сравниваются по своей величине: прочие варианты модели (мощность, вес) в
    названии не ищутся, и их отсутствие противоречием не считается."""
    for size_class in ("memory", "in", "m", "mm", "ddr"):
        a = {t for t in first if _size_class(t) == size_class}
        b = {t for t in second if _size_class(t) == size_class}
        if a and b and a != b:
            return True
    return False


def identities_conflict(group_a: Iterable[tuple], group_b: Iterable[tuple]) -> bool:
    """Можно ли объединить две группы [(название, поля или None, сохранённый ключ или None)].

    Собственная идентичность считается по полям и названию, а не по назначенному ключу: старый или устаревший
    ключ не знает о ревизии, а участник группы мог попасть в неё ошибочно (аудит C45R-01). У участника без полей
    объём, диагональ, ревизия и признаки сборки берутся из названия (аудит C45RR-01): ошибка этой грубой проверки
    только откажет в объединении."""
    def profile(items):
        out = set()
        for title, fields, key in items:
            own = (build_key(fields, title) if isinstance(fields, dict) else None) or ""
            # У всех, не только без полей: диагональ из номера модели (24B1) в ключ не входит, но в названии видна
            marks = marks_of(own) if own else _title_marks(title)
            code = _title_ram_code(_text(title)) if _RAM_RE.search(_text(title)) else None
            out.add((own, key or "", _title_revision(title), _title_sizes(title), marks, code))
        return out

    def known(own, key):
        return (_key_variants(own) if own else None) or (_key_variants(key) if key else None) or set()
    first, second = profile(group_a), profile(group_b)
    for own_a, key_a, rev_a, title_a, marks_a, code_a in first:
        for own_b, key_b, rev_b, title_b, marks_b, code_b in second:
            if (own_a and own_b and keys_conflict(own_a, own_b)) or (key_a and key_b and keys_conflict(key_a, key_b)) \
                    or (rev_a and rev_b and rev_a != rev_b) or marks_a != marks_b \
                    or (code_a and code_b and code_a != code_b) \
                    or _capacity_conflict(title_a, title_b | known(own_b, key_b)) \
                    or _capacity_conflict(title_b, title_a | known(own_a, key_a)):
                return True
    return False


def compare_keys(primary: str, shadow: str) -> str:
    """Сравнение ключа основной и теневой модели: matched, mismatch или incomparable (аудит C45-04, C45R-03).

    k2 с k2 — строкой. Старый строковый ключ разбирается теми же правилами: «samsung:galaxy s24:256gb» и
    «k2:samsung:galaxys24:256gb» — один товар, разница формата не ошибка модели. Варианты сравниваются с единицами:
    1 ГБ и 1 ТБ, 85 г и 85 мл — разное. Что из старого ключа не разобрать однозначно (нет бренда, norm:, unknown:,
    число без единицы, вариант есть только с одной стороны) — несопоставимо и в долю совпадений не входит."""
    if primary == shadow:
        return "matched"
    if primary.startswith(f"{KEY_VERSION}:"):
        return "mismatch"
    parts = primary.split(":")
    if len(parts) < 2 or parts[0] in ("norm", "unknown"):
        return "incomparable"
    brand = brand_token(parts[0])
    identity = model_token(parts[1], brand)
    if not brand or not identity:
        return "incomparable"
    if shadow.split(":", 2)[1] != brand or _identity_of(shadow) != identity:
        return "mismatch"
    old, new = _key_variants(primary), _key_variants(shadow)
    if old is None:
        return "incomparable"
    # Диагональ, которая уже есть в номере модели, в k2 не пишется («aoc:27b31h:27» и «k2:aoc:27b31h»)
    old = {v for v in old if not (v.endswith("in") and re.search(rf"(?<![0-9]){re.escape(v[:-2].split('.')[0])}(?![0-9])", identity))}
    if old == new:
        return "matched"
    return "mismatch" if old and new else "incomparable"


# Связь по артикулу производителя (P17-C3). Модель по-разному решает, что писать в «модель»: серию («MTE110S»)
# или артикул («TS128GMTE110S»), — и один товар получает два ключа. По замеру это главный остаток пропусков
# у всех проверенных моделей. Если у двух названий одного бренда совпадает «сильный» артикул (от 6 знаков,
# буквы и цифры) и объём/диагональ не противоречат, это один товар — у него один ключ.
_STRONG_ARTICLE = re.compile(r"^(?=.*[a-z])(?=.*[0-9])[a-z0-9.]{6,}$")


def article_tokens(fields: Any) -> List[str]:
    """«бренд:артикул» из полей sku и model, только сильные артикулы (общие «m2», «ddr4» не годятся)."""
    if not isinstance(fields, dict) or fields.get("unbranded") is True:
        return []
    brand = brand_token(fields.get("brand"))
    if not brand:
        return []
    # Только поля целиком, без разбора на части: модель записывает в артикул и имена комплектующих
    # («Evolve2 65 Link380a» и «Evolve2 85 Link380a» — разные гарнитуры с одним адаптером, замер C3)
    candidates = [fields.get("sku"), fields.get("model")]
    found = set()
    for value in candidates:
        token = model_token(value, brand)
        if token and _STRONG_ARTICLE.match(token):
            found.add(f"{brand}:{token}")
    return sorted(found)


def _variants_of(key: str) -> str:
    parts = key.split(":", 3)
    return parts[3] if len(parts) > 3 else ""


def _identity_of(key: str) -> str:
    parts = key.split(":", 3)
    return parts[2] if len(parts) > 2 else ""


def _compatible(a: str, b: str, article: str) -> bool:
    """Общий артикул связывает, только если ключи не противоречат: варианты те же (или у одного их нет),
    а модели совпадают или модель одного из названий и есть этот артикул. Иначе общий «артикул» — чужое
    поле: «Link380a MS Stereo» у Evolve2 65 и Evolve2 85.

    «Одна модель содержит другую» не связывает: так Galaxy S24 склеивался с Galaxy S24 Ultra (аудит P17C-01);
    на замере C3 это правило ни одной пары не добавляло (полнота 0,874 с ним и без него)."""
    va, vb = _variants_of(a), _variants_of(b)
    if va and vb and va != vb:
        return False
    ia, ib = _identity_of(a), _identity_of(b)
    token = article.split(":", 1)[-1]
    return ia == ib or token in (ia, ib)


def pair_hash(title_a: str, title_b: str) -> str:
    """Пара названий без порядка: (a, b) и (b, a) — одна запись (вердикты и очередь P17-C7)."""
    first, second = sorted((str(title_a).strip(), str(title_b).strip()))
    return hashlib.sha256(f"{first}\n{second}".encode("utf-8")).hexdigest()


def article_pairs(entries: Dict[str, Any]) -> List[tuple]:
    """Пары названий с общим артикулом — кандидаты на связь; по ним загружаются известные вердикты."""
    by_article: Dict[str, List[str]] = {}
    for t in sorted(entries):
        if entries[t][1]:
            for art in article_tokens(entries[t][0]):
                by_article.setdefault(art, []).append(t)
    pairs = set()
    for members in by_article.values():
        for i, first in enumerate(members):
            for other in members[i + 1:]:
                pairs.add((first, other))
    return sorted(pairs)


def link_by_article(
    entries: Dict[str, Any],
    fixed: Iterable[str] = (),
    pair_verdicts: Optional[Dict[str, str]] = None,
    on_disputed: Optional[Callable[[str, str, str], Any]] = None,
) -> Dict[str, str]:
    """{название: (поля, ключ)} → {название: ключ} с общим ключом у названий одного артикула.

    Совместимость проверяется для всей объединяемой группы, а не для отдельной пары (P17C-01):
    - в группе не больше одного набора вариантов (объём, диагональ): название без вариантов не служит мостом
      между 128 и 256 ГБ. Если у его соседей по артикулу разные варианты, оно не связывается ни с кем;
    - подтверждённый вердикт «разные» (pair_verdicts, P17-C7) запрещает объединение групп, в которых есть эта пара;
    - вердикт «один товар» снимает только проверку моделей, но не противоречие вариантов.

    Обход идёт в порядке артикулов и названий, поэтому результат не зависит от порядка входа.
    Ключ группы — из fixed (уже сохранённые и используемые ключи не меняются), иначе ключ с вариантами,
    затем самый частый, короткий и по алфавиту.
    on_disputed — callback(title_a, title_b, reason) для пар с общим артикулом, которые объединить нельзя.
    """
    fixed = set(fixed)
    verdicts = pair_verdicts or {}
    titles = sorted(t for t, (_, key) in entries.items() if key)
    parent = {t: t for t in titles}
    # Варианты — из сохранённых полей и названия, а не из назначенного ключа: сохранённое 128 ГБ могло
    # получить ключ без объёма от прежней группы, и тогда 256 ГБ присоединялся бы к нему (P17CR-01)
    own_variants = {t: _fields_variants(entries[t][0], t, entries[t][1]) for t in titles}
    variants = {t: ({own_variants[t]} - {""}) for t in titles}
    # Ревизия и «готовый компьютер» — тоже по полям: ключ мог быть назначен прежней группой (аудит C45-01)
    own_keys = {t: _fields_key(entries[t][0], t, entries[t][1]) for t in titles}
    revisions = {t: ({revision_of(own_keys[t])} - {""}) for t in titles}
    # Метки (компьютер, поставка, Wi-Fi, адаптер, аксессуар) различают товар, и общий артикул этого не отменяет
    marks = {t: marks_of(own_keys[t]) for t in titles}
    members = {t: {t} for t in titles}

    def root(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    by_article: Dict[str, List[str]] = {}
    for t in titles:
        for art in article_tokens(entries[t][0]):
            by_article.setdefault(art, []).append(t)

    # Название без вариантов, у соседей которого по артикулу разные варианты, неоднозначно: не связывается
    neighbour_variants: Dict[str, set] = {t: set() for t in titles}
    for group in by_article.values():
        for t in group:
            for other in group:
                if other != t:
                    neighbour_variants[t] |= variants[other]
    ambiguous = {t for t in titles if not variants[t] and len(neighbour_variants[t]) > 1}

    def different(group_a, group_b) -> bool:
        return any(verdicts.get(pair_hash(x, y)) == "different" for x in group_a for y in group_b)

    for article in sorted(by_article):
        group = by_article[article]
        for i, first in enumerate(group):
            for other in group[i + 1:]:
                a, b = root(first), root(other)
                if a == b:
                    continue
                verdict = verdicts.get(pair_hash(first, other))
                if marks[first] != marks[other]:
                    continue                            # процессор и компьютер с ним, OEM и BOX: не спор, а разные товары
                if first in ambiguous or other in ambiguous:
                    reason = "ambiguous_variants"
                elif len(variants[a] | variants[b]) > 1:
                    reason = "variant_conflict"
                elif len(revisions[a] | revisions[b]) > 1:
                    reason = "revision_conflict"
                elif different(members[a], members[b]):
                    reason = None                       # уже решено третьим источником — не спорная
                elif bool(revisions[a]) != bool(revisions[b]) and verdict != "same":
                    reason = "revision_unknown"         # ревизия известна только у одного: решает проверка
                elif verdict == "same" or _compatible(entries[first][1], entries[other][1], article):
                    parent[b] = a
                    variants[a] |= variants.pop(b)
                    revisions[a] |= revisions.pop(b)
                    members[a] |= members.pop(b)
                    continue
                else:
                    reason = "incompatible_article"
                if reason and on_disputed and verdict not in ("same", "different"):
                    on_disputed(first, other, f"{reason}:{article}")
    groups: Dict[str, List[str]] = {}
    for t in titles:
        groups.setdefault(root(t), []).append(t)
    out = {}
    for group in groups.values():
        pool = [entries[t][1] for t in group if t in fixed] or [entries[t][1] for t in group]
        best = sorted(set(pool), key=lambda k: (0 if _variants_of(k) else 1, -pool.count(k), len(k), k))[0]
        for t in group:
            if t in fixed:
                out[t] = entries[t][1]
            elif not _key_fits(best, t, marks, own_keys):
                # Сохранённый ключ прежних правил не снимает метку или ревизию: новый BOX не получает ключ без .box,
                # иначе несовместимая группа OEM окажется с той же строкой (C2R-02)
                fitting = sorted(k for k in set(pool) if _key_fits(k, t, marks, own_keys)
                                 and (not own_variants[t] or _variants_of(k) == own_variants[t]))
                out[t] = fitting[0] if fitting else own_keys[t]
            elif own_variants[t] and _variants_of(best) != own_variants[t]:
                # Ключ группы без объёма не стирает известный объём нового названия: сохранённый ключ без
                # вариантов не должен стать общим для 128 и 256 (P17CR-01). Берётся ключ группы с тем же
                # объёмом, иначе — собственный.
                same = sorted(k for k in set(pool) if _variants_of(k) == own_variants[t])
                out[t] = same[0] if same else entries[t][1]
            else:
                out[t] = best
    return out


def _key_fits(key: str, title: str, marks: Dict[str, frozenset], own_keys: Dict[str, str]) -> bool:
    """Ключ группы сохраняет обязательные метки названия и его ревизию, если она известна."""
    revision = revision_of(own_keys[title])
    return marks_of(key) == marks[title] and (not revision or revision_of(key) == revision)


def _fields_key(fields: Any, title: str, key: str) -> str:
    """Ключ названия по его собственным полям; без полей — назначенный."""
    return (build_key(fields, title) if isinstance(fields, dict) else None) or key


def _fields_variants(fields: Any, title: str, key: str) -> str:
    """Варианты названия по его полям (с объёмом и диагональю из названия); без полей — из ключа."""
    return _variants_of(_fields_key(fields, title, key))


def resolve_keys_with_articles(
    new_fields: Dict[str, Dict[str, Any]],
    existing_items: Iterable[Dict[str, Any]] = (),
    pair_verdicts: Any = None,
    on_disputed: Optional[Callable[[str, str, str], Any]] = None,
) -> Dict[str, Optional[str]]:
    """Вычисляет ключи по стандарту k2 для new_fields с объединением по артикулам (P17-C3).

    existing_items — список уже сохранённых товаров [{'title': ..., 'fields': ..., 'canonical_key': ...}].
    Их ключи фиксированы (fixed) и не меняются.
    pair_verdicts — вердикты третьего источника истины (P17-C7): словарь {pair_hash: decision} или функция,
    которая по списку pair_hash пар с общим артикулом возвращает такой словарь (так конвейер загружает
    сохранённые вердикты из базы только для нужных пар).
    on_disputed — функция постановки спорных пар в очередь.
    Возвращает {title: key} для всех названий из new_fields.
    """
    entries: Dict[str, Any] = {}
    fixed_titles = set()

    for item in existing_items:
        t = item.get("title")
        f = item.get("fields")
        k = item.get("canonical_key")
        if t and isinstance(f, dict) and k and t not in new_fields:
            entries[t] = (f, k)
            fixed_titles.add(t)

    base_keys: Dict[str, Optional[str]] = {}
    for title, fields in new_fields.items():
        k = build_key(fields, title)
        base_keys[title] = k
        if k:
            entries[title] = (fields, k)

    if entries:
        verdicts = pair_verdicts
        if callable(pair_verdicts):
            verdicts = pair_verdicts([pair_hash(a, b) for a, b in article_pairs(entries)]) or {}
        linked = link_by_article(entries, fixed=fixed_titles, pair_verdicts=verdicts,
                                 on_disputed=on_disputed)
        return {t: linked.get(t) or base_keys.get(t) for t in new_fields}
    return base_keys


