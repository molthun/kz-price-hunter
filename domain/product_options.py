"""Параметры товара, которые покупатель выбирает внутри карточки.

В выдаче одна карточка, а исполнения различаются внутри неё. Новый параметр — ещё одна запись
в _EXTRACTORS: поле offer["options"] и выбор на странице подхватят его сами.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import product_key

# Подарок — маркетинг, а не другой товар. Явный: «+ подарок», «+ акция 4 подарка», «+ чехол в подарок».
_EXPLICIT_GIFT_RE = re.compile(
    r"\s*\+\s*(?:(?:акци\w*\s*)?(?:\d+\s*)?подар\w*\s*(?P<rest>.*)|(?P<named>[^+]*?)\s+в\s+подарок\b.*)$",
    re.I,
)
# Без слова «подарок»: «Смартфон + Наушники». Подарком считается только при цене как у самого товара
# (gift_bundles.py). «+ Cellular» и «+ монтажный комплект» сюда не подходят: это само исполнение.
_ADDED_PRODUCT_RE = re.compile(
    r"\s+\+\s+(?P<rest>(?:наушники|наушник|смарт[- ]часы|часы|смартфон|рюкзак|адаптер|чехол|стекло|ремешок|"
    r"powerbank|повербанк|зарядное|блок\s+питания|кабель|колонка|портативн\w+|фитнес[- ]браслет)\b.*)$",
    re.I,
)


def split_explicit_gift(title: Any) -> Tuple[str, Optional[str]]:
    """(название без подарка, что дарят; пустая строка, если не сказано) или (название, None)."""
    text = str(title or "").strip()
    found = _EXPLICIT_GIFT_RE.search(text)
    if not found or found.start() == 0:
        return text, None
    return text[:found.start()].strip(), (found.group("rest") or found.group("named") or "").strip()


def split_added_product(title: Any) -> Tuple[str, Optional[str]]:
    """Как split_explicit_gift, но для «Товар + другой товар» без слова «подарок»."""
    main, gift = split_explicit_gift(title)
    if gift is not None:
        return main, gift
    if main.lower().startswith("комплект"):
        return main, None                           # в «Комплекте» главный товар бывает и вторым
    found = _ADDED_PRODUCT_RE.search(main)
    if not found or found.start() == 0:
        return main, None
    return main[:found.start()].strip(), found.group("rest").strip()


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
    text = split_added_product(title)[0]            # цвет подарка не цвет товара
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
    found = (a or b for a, b in _TIER_RE.findall(split_added_product(title)[0]))
    return frozenset(_TIER_ALIASES.get(word.lower(), word.lower()) for word in found)


def _gigabytes(title: Any) -> List[float]:
    text = split_added_product(title)[0]
    paired = _RAM_STORAGE_RE.search(text)
    if paired:
        ram, storage, unit = float(paired.group(1)), float(paired.group(2)), paired.group(3).lower()
        return [ram, storage * 1024 if unit in ("тб", "tb") else storage]
    out = []
    for item in product_key.title_variants(text):
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
        found = _WATCH_SIZE_RE.search(split_added_product(title)[0])
        if found:
            out["size"] = found.group(1)
    return out


# Суббренды считаются брендом производителя, а в модели остаются: «Redmi Note 13» у Xiaomi и у Redmi — один телефон
_SUB_BRANDS = {"redmi": "xiaomi", "poco": "xiaomi"}
_MODEL_MEMORY_RE = re.compile(r"\d+\s*(?:гб|gb|тб|tb)\b", re.I)
_TITLE_SPEC_RE = re.compile(r"\d+\s*(?:гб|gb|тб|tb|мм|mm)(?![a-zа-я])", re.I)


def family_model(brand: Any, model: Any, title: Any) -> str:
    """Модель без цвета: магазины дописывают его к ней («Reno 14F Luminous», «Galaxy S26 sky»).

    С конца модели снимаются слова, которые в названии стоят после памяти или размера: там магазины пишут
    цвет, и он не всегда из словаря. Слова с цифрами и уточнения вроде Pro остаются: они меняют модель.
    """
    brand_word = str(brand or "").lower().strip()
    words = re.findall(r"[\w+]+", _MODEL_MEMORY_RE.sub(" ", str(model or "").lower()))
    main = split_added_product(title)[0].lower()
    specs = list(_TITLE_SPEC_RE.finditer(main))
    after_spec = set(re.findall(r"[\w+]+", main[specs[-1].end():])) if specs else set()
    while len(words) > 1:
        word = words[-1]
        if re.search(r"\d", word) or _TIER_RE.fullmatch(word):
            break
        if word not in after_spec and not title_color(word):
            break
        words.pop()
    # Название бренда в начале модели и суббренд: «Xiaomi Redmi 10» и «Redmi 10» — одно
    while len(words) > 1 and words[0] in (brand_word, _SUB_BRANDS.get(brand_word)):
        words.pop(0)
    if brand_word in _SUB_BRANDS and words[0] != brand_word:
        words.insert(0, brand_word)
    return " ".join(words)


def family_key(product: Any) -> Optional[Tuple[Any, ...]]:
    """Семейство товара или None, если объединять нельзя."""
    if product.category not in FAMILY_PARAMS or not product.brand or not product.model:
        return None
    if (product.item_kind or "device") != "device":
        return None
    brand = str(product.brand).lower().strip()
    return (_SUB_BRANDS.get(brand, brand), family_model(product.brand, product.model, product.title),
            product.category, model_tier(product.title))


def _family_label(param: str, value: str) -> str:
    number = float(value)
    if param == "size":
        return f"{value} мм"
    return f"{number / 1024:g} ТБ" if number >= 1024 else f"{value} ГБ"


def same_variant_ids(category: str, title: str, siblings: List[Dict[str, Any]]) -> List[str]:
    """Товары семейства того же исполнения: они отличаются только цветом, и их предложения — одна карточка.

    Если магазин не указал параметр, это не отличие: «256GB» и «8/256Gb» — одно исполнение.
    """
    current = family_params(category, title)
    tier = model_tier(title)
    out = []
    for row in siblings:
        params = family_params(category, row.get("title"))
        if model_tier(row.get("title")) != tier or not params or not current:
            continue
        shared = set(params) & set(current)
        if shared and all(params[p] == current[p] for p in shared):
            out.append(row["id"])
    return out


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


def family_id(product: Any) -> Optional[str]:
    """Значение колонки canonical_products.family_id: по нему выдача объединяет исполнения."""
    key = family_key(product)
    if key is None:
        return None
    brand, model, category, tier = key
    return "|".join((brand, model, category, "+".join(sorted(tier))))


def refresh_family_ids(conn) -> int:
    """Пересчитывает семейства после сканирования и сортировки. Возвращает число изменённых товаров."""
    marks = ",".join("?" * len(FAMILY_PARAMS))
    rows = conn.execute(
        f"""SELECT id, title, brand, model, category, item_kind, family_id FROM canonical_products
            WHERE category IN ({marks}) OR family_id IS NOT NULL""",
        list(FAMILY_PARAMS),
    ).fetchall()
    updates = []
    for row in rows:
        product = SimpleNamespace(title=row[1], brand=row[2], model=row[3], category=row[4], item_kind=row[5])
        new_id = family_id(product)
        if new_id != row[6]:
            updates.append((new_id, row[0]))
    if updates:
        conn.executemany("UPDATE canonical_products SET family_id = ? WHERE id = ?", updates)
        conn.commit()
    return len(updates)


def mark_gifts(offers: List[Any]) -> bool:
    """Подарок и чистое название у предложений. Возвращает True, если подарок есть хотя бы у одного.

    «Товар + другой товар» в одной группе с обычным предложением — подарок: иначе сюда бы его не объединили.
    """
    parsed = [(offer, split_added_product(offer.title)) for offer in offers if offer.title]
    has_plain = any(gift is None for _, (_, gift) in parsed)
    any_gift = False
    for offer, (main, gift) in parsed:
        explicit = split_explicit_gift(offer.title)[1] is not None
        if gift is not None and (explicit or has_plain):
            offer.title = main
            offer.gift = gift
            any_gift = True
    return any_gift
