"""Стандарт ключа товара, не зависящий от AI-модели (P17-C1, docs/DEVELOPMENT_PLAN.md).

Ключ решает, какие товары разных магазинов — один товар, и по нему сравниваются цены. Раньше модель писала
строку ключа сама, и у каждой модели был свой формат («32.5» и «32.5mm», «null» и «0», транслит бренда):
смена модели ломала сопоставление (замер 29.10.2026: ключи qwen3-vl совпали с Gemini у 17 из 60 названий).

Теперь модель только извлекает поля по фиксированной схеме (FIELDS_SCHEMA), а ключ собирает этот модуль
по правилам. Ключ одинаков для любой модели, если поля извлечены верно:

    k2:<бренд>:<модель или артикул>[:<варианты>]

- бренд — по справочнику синонимов (model_matching.BRAND_MAP + BRAND_ALIASES), иначе транслит;
- модель и артикул — нижний регистр, только латиница и цифры, без пробелов, дефисов, цветов и маркетинга;
- варианты — «число + единица» по таблице единиц (ГБ → gb, ТБ → gb×1024, мм → mm, л → l, дюйм → in),
  десятичная точка, сортировка, через «+»;
- пустое — пусто: никаких «0»/«null»;
- без бренда (или «без бренда») — ключа нет: товар не сравнивается автоматически, чтобы не склеить разное.

Префикс «k2:» — версия правил. Если правила поменяются, ключи пересобираются из сохранённых полей без
повторных вызовов модели (KEY_VERSION).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

KEY_VERSION = "k2"

# Что модель обязана вернуть на каждое название (текст для подсказки и проверка ответа)
FIELDS_SCHEMA = {
    "brand": "бренд производителя как на упаковке; null, если бренда нет",
    "model": "серия и модель без бренда, цвета, комплектации и маркетинга; null, если нет",
    "sku": "артикул/код модели производителя (не артикул магазина); null, если нет",
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
}

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
    token = re.sub(r"[^a-z0-9]+", "", _latin(text))
    if not token or token in _EMPTY:
        return None
    return BRAND_MAP.get(token, token)


def model_token(value: Any, brand: Optional[str] = None) -> Optional[str]:
    """Модель или артикул: без бренда, цветов и маркетинга, только [a-z0-9] слитно."""
    text = _text(value)
    if text in _EMPTY:
        return None
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


def variant_tokens(variants: Any) -> List[str]:
    """Варианты к виду «число+единица»: 128gb, 0.96l, 55in. Неизвестная единица или нет числа — пропуск."""
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
        out.add(_fmt(number * mult) + canon)
    return sorted(out)[:4]


def build_key(fields: Any) -> Optional[str]:
    """Ключ по стандарту из полей модели или None (без бренда, без модели и артикула, мусор)."""
    if not isinstance(fields, dict) or fields.get("unbranded") is True:
        return None
    brand = brand_token(fields.get("brand"))
    if not brand:
        return None
    identity = model_token(fields.get("model"), brand) or model_token(fields.get("sku"), brand)
    if not identity:
        return None
    variants = variant_tokens(fields.get("variants"))
    key = f"{KEY_VERSION}:{brand}:{identity}"
    return f"{key}:{'+'.join(variants)}" if variants else key


def keys_for(items: Iterable[Dict[str, Any]], titles: Iterable[str]) -> Dict[str, Optional[str]]:
    """Ответ модели {items: [{title, ...поля}]} → {название: ключ или None} только для названий пакета."""
    allowed = set(titles)
    found: Dict[str, Optional[str]] = {}
    for item in items or []:
        if isinstance(item, dict) and item.get("title") in allowed:
            found[item["title"]] = build_key(item)
    return found
