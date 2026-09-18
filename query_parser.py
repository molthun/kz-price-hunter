"""Разбор поисковой фразы по правилам, без AI.

Используется, когда AI-разбор недоступен (гости, AI выключен или не ответил): обычный поиск по базе
ищет все слова запроса сразу, поэтому фраза «ноутбук для игр до 400к» ничего не находит —
мешают «для», «до», «400к». Здесь из фразы извлекаются цена и «со скидкой», убираются служебные
слова, а назначение («для игр») превращается в мягкое предпочтение моделей.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

# Множители цены: «400к», «400 тыс», «1,5 млн»
_MULTIPLIERS = {
    "к": 1_000, "k": 1_000, "тыс": 1_000, "тысяч": 1_000, "тысячи": 1_000, "т": 1_000,
    "млн": 1_000_000, "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000,
}
_CURRENCY = r"(?:тенге|тг|₸|kzt)"
# Сокращение должно стоять отдельным словом: иначе «т» из «тенге» примется за «тыс»
_AMOUNT = rf"(\d+(?:[.,]\d+)?(?:\s\d{{3}})*)\s*(млн|миллион(?:а|ов)?|тысяч[иа]?|тыс|к|k|т)?(?![а-яa-z])\.?\s*{_CURRENCY}?"

# Слова, которые не описывают товар: вежливость, намерение, предлоги, валюта
_STOP_WORDS = {
    "для", "до", "от", "и", "в", "во", "на", "с", "со", "по", "под", "за", "из", "к", "а", "или", "не",
    "мне", "меня", "нам", "нужен", "нужна", "нужно", "нужны", "хочу", "хотим", "купить", "куплю", "ищу",
    "найди", "найти", "подбери", "подобрать", "посоветуй", "посоветуйте", "порекомендуй", "покажи",
    "выбери", "какой", "какая", "какое", "какие", "какую", "что", "лучше", "лучший", "лучшая", "лучшее",
    "хороший", "хорошая", "хорошее", "хорошие", "недорогой", "недорогая", "недорогие", "дешевый", "дешевая",
    "дешевые", "бюджетный", "бюджетная", "бюджетные", "пожалуйста", "цена", "цене", "ценой", "стоимость",
    "тенге", "тг", "₸", "kzt", "примерно", "около", "пределах", "максимум", "минимум", "бюджет",
    "дешевле", "дороже", "подешевле", "скидкой", "скидке", "скидки", "акции", "акцией",
    "распродаже", "выгодно", "выгодный", "игр", "игры", "гейминга", "учебы", "работы", "офиса", "дома",
    "подарка", "подарок", "уровня", "класса",
    # В названиях диагональ пишется как 27", поэтому слово «дюймов» только мешает поиску
    "дюйм", "дюйма", "дюймов", "дюймовый", "дюймовая", "inch",
}

# Назначение → слова в названиях подходящих моделей (мягкий фильтр: если таких нет, выдача не сужается)
_PURPOSES = [
    (re.compile(r"\b(для\s+игр\w*|для\s+гейминга|игров\w*|геймерск\w*|gaming)\b"),
     "для игр",
     ["gaming", "игров", "rtx", "rog", "tuf", "legion", "loq", "nitro", "predator", "victus", "omen",
      "katana", "cyborg", "thin gf", "alienware", "radeon rx", "geforce"]),
]

_DISCOUNT = re.compile(r"\b(со\s+скидк\w*|по\s+акци\w*|с\s+акци\w*|на\s+распродаж\w*|распродаж\w*|скидк\w*|подешевле|выгодн\w*)\b")

# Разговорные названия → слово, по которому ищет база
_SYNONYMS = {
    "ноут": "ноутбук", "ноуты": "ноутбук", "ноутбуки": "ноутбук", "лэптоп": "ноутбук", "лаптоп": "ноутбук",
    "телик": "телевизор", "телевизоры": "телевизор", "телек": "телевизор",
    "айфон": "iphone", "айфоны": "iphone", "айпад": "ipad", "макбук": "macbook",
    "видюха": "видеокарта", "видеокарты": "видеокарта", "проц": "процессор", "процессоры": "процессор",
    "смартфоны": "смартфон", "телефон": "смартфон", "телефоны": "смартфон", "мобильник": "смартфон",
    "наушники": "наушники", "уши": "наушники", "стиралка": "стиральная", "пылесосы": "пылесос",
    "мониторы": "монитор", "планшеты": "планшет", "холодильники": "холодильник",
    "кондиционеры": "кондиционер", "приставка": "playstation", "консоль": "playstation", "плойка": "playstation",
}


# Типы товаров: если запрос про сам товар, аксессуары «для <товара>» из выдачи убираются
_PRODUCT_NOUNS = {
    "ноутбук": "ноутбук", "монитор": "монитор", "телевизор": "телевизор", "смартфон": "смартфон",
    "планшет": "планшет", "пылесос": "пылесос", "холодильник": "холодильник", "видеокарта": "видеокарт",
    "процессор": "процессор", "наушники": "наушник", "стиральная": "стиральн", "кондиционер": "кондиционер",
    "принтер": "принтер", "проектор": "проектор", "iphone": "iphone", "ipad": "ipad", "macbook": "macbook",
    "playstation": "playstation", "роутер": "роутер", "фотоаппарат": "фотоаппарат", "колонка": "колонк",
    "кофемашина": "кофемашин", "микроволновка": "микроволнов", "моноблок": "моноблок",
}


def _to_int(number: str, suffix: Optional[str], price_context: bool = False) -> Optional[int]:
    """Число в тенге. price_context — число стоит после «до/от/дешевле»: «до 400» понимается как 400 000."""
    try:
        value = float(number.replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    mult = _MULTIPLIERS.get((suffix or "").lower().rstrip("."), 1)
    if mult == 1 and value < 1000:
        if not price_context:
            return None  # голое число без контекста цены — скорее характеристика (512 ГБ, 55")
        mult = 1_000
    return int(value * mult)


def _extract_prices(text: str) -> Tuple[Optional[int], Optional[int], str]:
    """Возвращает (min_price, max_price, текст без ценовых фрагментов)."""
    min_price = max_price = None

    # «от 100 до 200к», «100-200 тыс»: множитель второй границы относится к обеим
    range_re = re.compile(rf"(?:\bот\s+)?(\d+(?:[.,]\d+)?)\s*(млн|тыс|к|k)?(?![а-яa-z])\s*(?:-|–|—|\bдо\b)\s*{_AMOUNT}")
    m = range_re.search(text)
    if m and (m.group(0).lstrip().startswith("от") or m.group(2) or m.group(4) or re.search(_CURRENCY, m.group(0))):
        suffix = m.group(4) or m.group(2)
        low = _to_int(m.group(1), m.group(2) or suffix, price_context=True)
        high = _to_int(m.group(3), suffix, price_context=True)
        if low and high and low < high:
            return low, high, text[:m.start()] + " " + text[m.end():]

    patterns = [
        (rf"\b(?:до|не\s+дороже|дешевле|в\s+пределах|максимум|бюджет(?:ом)?)\s+{_AMOUNT}", "max"),
        (rf"\b(?:от|дороже|минимум|не\s+дешевле)\s+{_AMOUNT}", "min"),
        (_AMOUNT, "any"),
    ]
    for pattern, kind in patterns:
        for m in list(re.finditer(pattern, text)):
            fragment = m.group(0)
            if kind == "any" and not (m.group(2) or re.search(_CURRENCY, fragment)):
                continue  # просто число без «к/тыс/₸» — не цена
            value = _to_int(m.group(1), m.group(2), price_context=(kind != "any"))
            if not value:
                continue
            if kind == "min" and min_price is None:
                min_price = value
            elif kind in ("max", "any") and max_price is None:
                max_price = value
            else:
                continue
            text = text.replace(fragment, " ", 1)
    return min_price, max_price, text


def parse_query_rules(query: str) -> Optional[Dict[str, Any]]:
    """Разбор фразы по правилам. None — если в запросе нечего разбирать (обычный список слов)."""
    original = (query or "").strip()
    if not original:
        return None
    text = original.lower().replace("ё", "е")

    min_price, max_price, text = _extract_prices(text)

    only_discount = bool(_DISCOUNT.search(text))
    text = _DISCOUNT.sub(" ", text)

    purpose = None
    prefer: List[str] = []
    for pattern, label, keywords in _PURPOSES:
        if pattern.search(text):
            purpose, prefer = label, keywords
            text = pattern.sub(" ", text)
            break

    words = []
    for w in re.findall(r"[a-zа-я0-9][a-zа-я0-9\-+.]*", text):
        w = w.strip(".-")
        if not w or w in _STOP_WORDS:
            continue
        words.append(_SYNONYMS.get(w, w))
    clean_query = " ".join(dict.fromkeys(words))

    changed = (min_price or max_price or only_discount or purpose
               or clean_query != " ".join(original.lower().split()))
    if not changed or not clean_query:
        return None

    parts = [clean_query]
    if purpose:
        parts.append(purpose)
    if min_price and max_price:
        parts.append(f"{min_price:,}–{max_price:,} ₸".replace(",", " "))
    elif max_price:
        parts.append(f"до {max_price:,} ₸".replace(",", " "))
    elif min_price:
        parts.append(f"от {min_price:,} ₸".replace(",", " "))
    if only_discount:
        parts.append("со скидкой")

    return {
        "source": "rules",
        "clean_query": clean_query,
        "category": None,
        "brand": None,
        "min_price": min_price,
        "max_price": max_price,
        "only_discount": only_discount,
        "keywords": [],
        "prefer_keywords": prefer,
        # Основы названий товаров из запроса — для отсечения аксессуаров «для ноутбука»
        "product_nouns": [_PRODUCT_NOUNS[w] for w in words if w in _PRODUCT_NOUNS],
        "negative_keywords": [],
        "sort": "price_asc",
        "explanation": "Ищу: " + ", ".join(parts),
    }
