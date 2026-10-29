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
    "edition": "исполнение внутри модели, не меняющее сам товар: Gaming, Dual, OC, V3, Edition; null, если нет",
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
_TITLE_VARIANT_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(гб|gb|тб|tb|см|дюйм\w*|\"|''|”)(?![a-zа-я])")


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
        if canon not in KEY_UNITS:
            continue
        out.add(_storage(value_c) if canon == "gb" else _fmt(value_c) + canon)
    return sorted(out)[:4]


def build_key(fields: Any, title: Any = None) -> Optional[str]:
    """Ключ по стандарту из полей модели или None (без бренда, без модели и артикула, мусор).

    title — исходное название: объём и диагональ из него заменяют то, что модель записала в variants
    в тех же единицах (память и дюймы), остальные варианты берутся от модели."""
    if not isinstance(fields, dict) or fields.get("unbranded") is True:
        return None
    brand = brand_token(fields.get("brand"))
    if not brand:
        return None
    identity = (model_token(fields.get("model"), brand, (fields.get("sku"), fields.get("edition")))
                or model_token(fields.get("sku"), brand))
    if not identity:
        return None
    variants = variant_tokens(fields.get("variants"))
    from_title = variant_tokens(title_variants(title)) if title else []
    if from_title:
        own = {"in"} if any(v.endswith("in") for v in from_title) else set()
        if any(v.endswith(("gb", "tb")) for v in from_title):
            own |= {"gb", "tb"}
        variants = sorted({v for v in variants if re.sub(r"^[0-9.]+", "", v) not in own} | set(from_title))[:4]
    # Диагональ, которая уже есть в номере модели (65E7S, 27GP850, U11), в ключ не идёт: одни магазины
    # пишут «65"», другие нет, и один телевизор расходился по двум ключам (замер C2)
    variants = [v for v in variants
                if not (v.endswith("in") and re.search(rf"(?<![0-9]){re.escape(v[:-2].split('.')[0])}(?![0-9])", identity))]
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
    а модели совпадают, одна содержит другую или модель и есть этот артикул. Иначе общий «артикул» — чужое
    поле: «Link380a MS Stereo» у Evolve2 65 и Evolve2 85."""
    va, vb = _variants_of(a), _variants_of(b)
    if va and vb and va != vb:
        return False
    ia, ib = _identity_of(a), _identity_of(b)
    token = article.split(":", 1)[-1]
    return ia == ib or ia in ib or ib in ia or token in (ia, ib)


def link_by_article(entries: Dict[str, Any], fixed: Iterable[str] = ()) -> Dict[str, str]:
    """{название: (поля, ключ)} → {название: ключ} с общим ключом у названий одного артикула.

    Ключ группы — самый частый в ней (затем короткий, затем по алфавиту); если в группе есть названия из fixed
    (их ключи уже сохранены и используются), берётся самый частый из их ключей: прежние ключи не меняются."""
    fixed = set(fixed)
    titles = [t for t, (_, key) in entries.items() if key]
    parent = {t: t for t in titles}

    def root(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    by_article: Dict[str, List[str]] = {}
    for t in titles:
        for art in article_tokens(entries[t][0]):
            by_article.setdefault(art, []).append(t)
    for article, members in by_article.items():
        for i, first in enumerate(members):
            for other in members[i + 1:]:
                a, b = root(first), root(other)
                if a != b and _compatible(entries[first][1], entries[other][1], article):
                    parent[b] = a
    groups: Dict[str, List[str]] = {}
    for t in titles:
        groups.setdefault(root(t), []).append(t)
    out = {}
    for members in groups.values():
        pool = [entries[t][1] for t in members if t in fixed] or [entries[t][1] for t in members]
        best = sorted(set(pool), key=lambda k: (-pool.count(k), len(k), k))[0]
        for t in members:
            out[t] = entries[t][1] if t in fixed else best
    return out

