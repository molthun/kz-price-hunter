"""Параметры товара, которые покупатель выбирает внутри карточки.

В выдаче одна карточка, а исполнения различаются внутри неё. Новый параметр — ещё одна запись
в _EXTRACTORS: поле offer["options"] и выбор на странице подхватят его сами.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

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
