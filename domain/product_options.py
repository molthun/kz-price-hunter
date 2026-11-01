"""Параметры товара, которые покупатель выбирает внутри карточки.

В выдаче одна карточка, а исполнения различаются внутри неё. Новый параметр — ещё одна запись
в _EXTRACTORS: поле offer["options"] и выбор на странице подхватят его сами.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import product_key

# Значение, подпись, образец цвета, основы русских и английских написаний
_COLORS: List[Tuple[str, str, str, str]] = [
    ("black", "Чёрный", "#1f2328", r"ч[её]рн\w*|black|onyx|midnight black"),
    ("white", "Белый", "#ffffff", r"бел(?:ый|ая|ое|ые|ого|ую)|white|icy white"),
    ("gray", "Серый", "#8a8f98", r"сер(?:ый|ая|ое|ые|ого|ую)|gr[ae]y|графит\w*|graphite"),
    ("silver", "Серебристый", "#c4c8cf", r"серебр\w*|silver"),
    ("gold", "Золотой", "#d4af37", r"золот\w*|gold"),
    ("blue", "Синий", "#2563eb", r"син(?:ий|яя|ее|ие|его|юю)|blue"),
    ("lightblue", "Голубой", "#7cc4ef", r"голуб\w*|light blue|sky blue"),
    ("green", "Зелёный", "#2e9e5b", r"зел[её]н\w*|green|мятн\w*|mint"),
    ("red", "Красный", "#d92d20", r"красн\w*|red"),
    ("pink", "Розовый", "#f29ac0", r"розов\w*|pink"),
    ("purple", "Фиолетовый", "#7c4dbd", r"фиолет\w*|лилов\w*|лавандов\w*|purple|violet|lavender"),
    ("yellow", "Жёлтый", "#f5c518", r"ж[её]лт\w*|yellow"),
    ("orange", "Оранжевый", "#f27a1a", r"оранж\w*|orange"),
    ("brown", "Коричневый", "#7a4f2c", r"коричн\w*|brown|мокко|mocha"),
    ("beige", "Бежевый", "#e0cfb1", r"бежев\w*|beige|латте|кремов\w*|cream"),
    ("titanium", "Титановый", "#9c9a94", r"титанов\w*|titanium"),
    ("starlight", "Сияющая звезда", "#efe6d8", r"starlight|сияющ\w* звезд\w*"),
    ("midnight", "Тёмная ночь", "#1d2733", r"midnight|т[её]мн\w* ноч\w*"),
]
_COLOR_INFO = {value: (label, swatch) for value, label, swatch, _ in _COLORS}
_COLOR_RES = [(value, re.compile(rf"(?<![\w-])(?:{words})(?![\w])", re.I)) for value, _, _, words in _COLORS]
# «черно-оранжевый»: первая часть составного цвета
_COMPOUND_PARTS = {"черно": "black", "чёрно": "black", "бело": "white", "серо": "gray", "сине": "blue",
                   "красно": "red", "зелено": "green", "золотисто": "gold", "розово": "pink"}
_COMPOUND_RE = re.compile(r"(?<![\w-])(" + "|".join(_COMPOUND_PARTS) + r")-(\w+)", re.I)


def title_color(title: Any) -> Optional[str]:
    """Цвет из названия: "black" или составной "black+orange"; None, если цвета нет."""
    text = str(title or "")
    found = set()
    for match in _COMPOUND_RE.finditer(text):
        found.add(_COMPOUND_PARTS[match.group(1).lower()])
        found.update(value for value, regex in _COLOR_RES if regex.fullmatch(match.group(2)))
    for value, regex in _COLOR_RES:
        if regex.search(text):
            found.add(value)
    # "midnight black" это чёрный, а не два цвета
    if "midnight" in found and "black" in found:
        found.discard("midnight")
    return "+".join(sorted(found)) if found else None


def _color_label(value: str) -> str:
    return " / ".join(_COLOR_INFO[part][0] for part in value.split("+"))


def _color_swatches(value: str) -> List[str]:
    return [_COLOR_INFO[part][1] for part in value.split("+")]


_EXTRACTORS: List[Tuple[str, str, Callable[[Any], Optional[str]], Callable[[str], str], Callable[[str], List[str]]]] = [
    ("color", "Цвет", title_color, _color_label, _color_swatches),
]


def offer_options(title: Any) -> Dict[str, str]:
    """Параметры одного предложения по его названию."""
    out = {}
    for key, _, extract, _, _ in _EXTRACTORS:
        value = extract(title)
        if value:
            out[key] = value
    return out


def build_options(offers: List[Any]) -> List[Dict[str, Any]]:
    """Параметры для выбора в карточке: только те, у которых есть хотя бы два значения."""
    result = []
    for key, label, _, value_label, swatches in _EXTRACTORS:
        groups: Dict[str, List[Any]] = {}
        for offer in offers:
            value = (offer.options or {}).get(key)
            if value:
                groups.setdefault(value, []).append(offer)
        if len(groups) < 2:
            continue
        values = []
        for value, items in groups.items():
            prices = [o.price for o in items if o.is_purchasable]
            values.append({
                "value": value,
                "label": value_label(value),
                "swatches": swatches(value),
                "offers_count": len(items),
                "min_price": min(prices) if prices else None,
            })
        values.sort(key=lambda v: (v["min_price"] is None, v["min_price"] or 0, v["label"]))
        result.append({"id": key, "label": label, "values": values})
    return result


# Исполнения, которые в базе остаются отдельными товарами со своими ценами, но выбираются в одной
# карточке. Только там, где бренд и модель однозначны: у ноутбуков одно «MacBook Air 13» покрывает
# разные процессоры, и выбор памяти скрыл бы эту разницу.
FAMILY_PARAMS: Dict[str, Tuple[str, ...]] = {
    "Смартфоны": ("storage", "ram"),
    "Планшеты": ("storage", "ram"),
    "Смарт-часы": ("size",),
}
_FAMILY_LABELS = {"storage": "Память", "ram": "Оперативная память", "size": "Размер корпуса"}
_WATCH_SIZE_RE = re.compile(r"(?<![\d.,])(3[89]|4\d|5[0-5])\s?(?:мм|mm)(?![a-zа-я])", re.I)


# Запись «12/512Gb» и «16/1TB»: первое число всегда оперативная память в гигабайтах
_RAM_STORAGE_RE = re.compile(r"(?<![\w.])(\d+)\s*/\s*(\d+)\s*(гб|gb|тб|tb)(?![a-zа-я])", re.I)
# Слова, которые делают другую модель: Note 12 и Note 12 Pro+ — разные телефоны, хотя поле модели у них одно
_TIER_RE = re.compile(
    r"(?<![a-zа-я0-9])(pro|про|plus|плюс|max|макс|ultra|ультра|lite|лайт|mini|мини|air|fe|se|neo|prime|turbo|[45]g)(?![a-zа-я0-9])"
    r"|(?<=[a-zа-я0-9])(\+)",
    re.I,
)
_TIER_ALIASES = {"про": "pro", "плюс": "plus", "+": "plus", "макс": "max", "ультра": "ultra", "лайт": "lite", "мини": "mini"}


def model_tier(title: Any) -> frozenset:
    """Уточнения модели в названии: {"pro", "plus"}."""
    found = (a or b for a, b in _TIER_RE.findall(str(title or "")))
    return frozenset(_TIER_ALIASES.get(word.lower(), word.lower()) for word in found)


def _gigabytes(title: Any) -> List[float]:
    text = str(title or "")
    paired = _RAM_STORAGE_RE.search(text)
    if paired:
        ram, storage, unit = float(paired.group(1)), float(paired.group(2)), paired.group(3).lower()
        return [ram, storage * 1024 if unit in ("тб", "tb") else storage]
    out = []
    for item in product_key.title_variants(title):
        unit = item["unit"]
        if unit in ("гб", "gb"):
            out.append(item["value"])
        elif unit in ("тб", "tb"):
            out.append(item["value"] * 1024)
    return out


def family_params(category: Any, title: Any) -> Dict[str, str]:
    """Параметры исполнения по названию: {"storage": "128", "ram": "6"}, значения в ГБ или мм."""
    wanted = FAMILY_PARAMS.get(str(category or ""), ())
    out: Dict[str, str] = {}
    if "storage" in wanted:
        sizes = sorted(set(_gigabytes(title)))
        if sizes and sizes[-1] >= 16:
            out["storage"] = f"{sizes[-1]:g}"
            if len(sizes) > 1 and sizes[0] <= 32:
                out["ram"] = f"{sizes[0]:g}"
    if "size" in wanted:
        found = _WATCH_SIZE_RE.search(str(title or ""))
        if found:
            out["size"] = found.group(1)
    return out


def family_key(product: Any) -> Optional[Tuple[Any, ...]]:
    """Семейство товара или None, если объединять нельзя."""
    if product.category not in FAMILY_PARAMS or not product.brand or not product.model:
        return None
    if (product.item_kind or "device") != "device":
        return None
    return (product.brand, product.model, product.category, model_tier(product.title))


def _family_label(param: str, value: str) -> str:
    number = float(value)
    if param == "size":
        return f"{value} мм"
    return f"{number / 1024:g} ТБ" if number >= 1024 else f"{value} ГБ"


def build_family_options(current_id: str, category: str, title: str,
                         siblings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Параметры, значения которых ведут в карточки других исполнений той же модели."""
    current = family_params(category, title)
    tier = model_tier(title)
    rows = [{**s, "params": family_params(category, s.get("title"))} for s in siblings
            if model_tier(s.get("title")) == tier]
    result = []
    for param in FAMILY_PARAMS.get(category, ()):
        if param not in current:
            continue
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            if row["params"].get(param):
                groups.setdefault(row["params"][param], []).append(row)
        if len(groups) < 2:
            continue
        values = []
        for value, items in groups.items():
            def rank(row):
                same = sum(row["params"].get(p) == current.get(p) for p in current if p != param)
                return (row["id"] != current_id, -same, row.get("min_price") or float("inf"))
            prices = [r["min_price"] for r in items if r.get("min_price")]
            values.append({
                "value": value,
                "label": _family_label(param, value),
                "product_id": sorted(items, key=rank)[0]["id"],
                "min_price": min(prices) if prices else None,
                "current": value == current[param],
            })
        values.sort(key=lambda v: float(v["value"]))
        result.append({"id": param, "label": _FAMILY_LABELS[param], "values": values, "navigates": True})
    return result


def collapse_family_cards(cards: List[Any]) -> List[Any]:
    """Исполнения одной модели на странице выдачи — одна карточка с самой низкой ценой."""
    first_seen: Dict[Tuple[Any, ...], int] = {}
    out: List[Any] = []
    for card in cards:
        family = family_key(card.product)
        if family is None:
            out.append(card)
            continue
        position = first_seen.get(family)
        if position is None:
            first_seen[family] = len(out)
            out.append(card)
            continue
        kept = out[position]
        extra = kept.variants_count + 1
        if card.min_price is not None and (kept.min_price is None or card.min_price < kept.min_price):
            out[position] = card
            kept = card
        kept.variants_count = extra
    return out
