"""Нормализация, сортировка и извлечение размеров обуви и одежды (v6.44.0).

Единый модуль для:
  - скраперов (Kari, Intertop, KIMEX и др.): нормализация списка доступных размеров
    в канонический вид (`["36", "37", "37.5", "38"]` или `["S", "M", "L", "XL"]`);
  - фасетов и SQL-индекса `offer_sizes`: естественная сортировка размеров;
  - поиска (`extract_size_from_query`): безопасное извлечение размера из запроса
    только при явном маркере («размер 42», «42 р») или обувном/одежном контексте
    (чтобы никогда не путать 42" ТВ или 38 мм часы с размером обуви).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Tuple

_LETTER_ORDER = {
    "XXS": 100,
    "XS": 101,
    "S": 102,
    "M": 103,
    "L": 104,
    "XL": 105,
    "XXL": 106,
    "XXXL": 107,
    "XXXXL": 108,
    "ONE SIZE": 200,
}

_LETTER_ALIASES = {
    "2XS": "XXS",
    "XXS": "XXS",
    "XS": "XS",
    "S": "S",
    "M": "M",
    "L": "L",
    "XL": "XL",
    "2XL": "XXL",
    "XXL": "XXL",
    "3XL": "XXXL",
    "XXXL": "XXXL",
    "4XL": "XXXXL",
    "XXXXL": "XXXXL",
    "OS": "ONE SIZE",
    "ONESIZE": "ONE SIZE",
    "ONE SIZE": "ONE SIZE",
}

_NUM_SIZE_RE = re.compile(r"^(\d{2})(?:[.,](0|5))?$")
_EXPLICIT_SIZE_RE = re.compile(
    r"(?iu)(?<!\w)(?:"
    r"(?:размер[ауе]?|р-р|р\.|size)\s*[:\-]?\s*(\d{2}(?:[.,]5)?|2xs|xxs|xs|2xl|3xl|4xl|xxxl|xxl|xl|[sml])"
    r"|(\d{2}(?:[.,]5)?|2xs|xxs|xs|2xl|3xl|4xl|xxxl|xxl|xl)\s*(?:размер[ауе]?|р-р|р\b)"
    r")(?!\w)"
)

_SHOE_CONTEXT_RE = re.compile(
    r"(?iu)\b(?:кроссов\w*|ботин\w*|полуботин\w*|сапог\w*|полусапог\w*|туфл\w*|туфел\w*|"
    r"кед[ыа]?|обув\w*|слипон\w*|мокасин\w*|сандал\w*|босонож\w*|лофер\w*|балетк\w*|"
    r"угг\w*|шлеп\w*|сланц\w*|сабо|берц\w*|дутик\w*|валенк\w*|челси|бутс\w*)\b"
)

_CLOTHING_CONTEXT_RE = re.compile(
    r"(?iu)\b(?:куртк\w*|пуховик\w*|пальто|футболк\w*|джинс\w*|брюк\w*|плать\w*|"
    r"свитер\w*|худи|толстовк\w*|рубашк\w*|ветровк\w*|комбинезон\w*|костюм\w*|одежд\w*|шорт\w*|юбк\w*)\b"
)

_CONTEXT_NUM_RE = re.compile(
    r"(?iu)(?<![\w.,])(\d{2}(?:[.,]5)?)(?![\w.,]|\s*(?:см|мм|м\b|гб|gb|тб|tb|мб|mb|л\b|кг|г\b|гр|вт|w\b|гц|hz|\"|''|дюйм|%|₸|тг|тенге|kzt))"
)
_CONTEXT_LETTER_RE = re.compile(r"(?iu)(?<!\w)(2xs|xxs|xs|2xl|3xl|4xl|xxxl|xxl|xl|[sml])(?!\w)")
# Дробная запятая размера: «37,5» (в списке «37,5;38»), но не «36,38»
_DECIMAL_COMMA_RE = re.compile(r"(?<!\d)(\d{2}),([05])(?!\d)")
# После явного маркера стоит единица измерения техники: «размер 42 дюйма», «размер 42"» — это не одежда
_NON_APPAREL_UNIT_RE = re.compile(r"(?iu)^\s*(?:дюйм\w*|\"|''|см\b|мм\b|inch\w*)")


# Обувь для вида размерной сетки (DS-01): поисковый контекст плюс виды, которые в нём не нужны.
_SHOE_KIND_EXTRA_RE = re.compile(
    r"(?iu)\b(?:ботильон\w*|ботфорт\w*|тапк\w*|тапоч\w*|слингбэк\w*|мюли|эспадрил\w*|сникер\w*|шлёп\w*)\b"
)
# Латинские двойники кириллицы в названиях источников («Бoтильоны» с латинской o)
_LATIN_LOOKALIKES = str.maketrans("aceopxyABCEHKMOPTX", "асеорхуАВСЕНКМОРТХ")


def _is_shoe_text(text: str) -> bool:
    folded = text.translate(_LATIN_LOOKALIKES)
    return bool(_SHOE_CONTEXT_RE.search(folded) or _SHOE_KIND_EXTRA_RE.search(folded))


def offer_size_kind(category: Any, title: Any) -> str:
    """Вид размерной сетки предложения: «shoe» (обувь) или «apparel» (одежда и прочее), DS-01.

    Номер 42 у кроссовок и у куртки — разные размеры. Категория решает, если однозначна;
    смешанные категории («Одежда и обувь», «Скидки и распродажи») решаются по названию.
    """
    cat = str(category or "")
    name = str(title or "")
    cat_shoe = _is_shoe_text(cat)
    cat_cloth = bool(_CLOTHING_CONTEXT_RE.search(cat))
    if cat_shoe and not cat_cloth:
        return "shoe"
    if cat_cloth and not cat_shoe:
        return "apparel"
    if _is_shoe_text(name) and not _CLOTHING_CONTEXT_RE.search(name):
        return "shoe"
    return "apparel"


def normalize_size_token(raw: Any) -> Optional[str]:
    """Приводит одиночный токен размера к каноническому виду или возвращает None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or len(text) > 24:
        return None
    # Убираем префиксы/суффиксы вроде "EU 42", "RU 38", "42 р.", "42 EUR"
    cleaned = re.sub(r"(?iu)\b(?:eu|eur|ru|rus|размер[ауе]?|size|р-р|р\.)\b", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -—:/.,()[]")
    if not cleaned:
        return None

    hm = _HEIGHT_CANON_RE.match(text)
    if hm:
        return _height_token(int(hm.group(1)))

    upper = cleaned.upper()
    if upper in _LETTER_ALIASES:
        return _LETTER_ALIASES[upper]

    m = _NUM_SIZE_RE.match(cleaned)
    if m:
        whole = int(m.group(1))
        half = m.group(2)
        if 16 <= whole <= 58:
            if half == "5":
                return f"{whole}.5"
            return str(whole)
    return None


# SZ-05: детская одежда по росту (см). Канонический вид «рост 92», чтобы не смешивать с обувью 42 или EU 56.
HEIGHT_MIN, HEIGHT_MAX = 50, 188
_HEIGHT_CANON_RE = re.compile(r"(?iu)^рост\s*(\d{2,3})$")
_HEIGHT_RAW_RE = re.compile(r"(?iu)^(?:рост\s*)?(\d{2,3})(?:\s*(?:[-–/]|до)\s*(\d{2,3}))?\s*(?:см)?$")


def _height_token(value: int) -> Optional[str]:
    return f"рост {value}" if HEIGHT_MIN <= value <= HEIGHT_MAX else None


def normalize_height_sizes(raw: Any) -> List[str]:
    """Ростовой размер из размерного варианта источника: «92», «98-104», «рост 110», «116 см».

    Диапазон раскладывается на края («98-104» → рост 98, рост 104), чтобы его находил поиск по любому краю.
    Вызывать только для заведомо размерных полей: голое число без контекста размером не считается.
    """
    if raw is None:
        return []
    text = re.sub(r"\s+", " ", str(raw).strip())
    m = _HEIGHT_RAW_RE.match(text)
    if not m:
        return []
    out = []
    for part in (m.group(1), m.group(2)):
        if part:
            token = _height_token(int(part))
            if not token:
                return []
            if token not in out:
                out.append(token)
    return out


def sort_size_key(size: str) -> Tuple[int, float, str]:
    """Ключ естественной сортировки размеров: числа по возрастанию, затем XS..XXXL."""
    norm = normalize_size_token(size) or str(size).strip().upper()
    try:
        return (0, float(norm), norm)
    except ValueError:
        pass
    hm = _HEIGHT_CANON_RE.match(norm)
    if hm:
        return (1, float(hm.group(1)), norm)
    if norm in _LETTER_ORDER:
        return (2, float(_LETTER_ORDER[norm]), norm)
    return (3, 0.0, norm)


def normalize_available_sizes(raw_sizes: Any) -> List[str]:
    """Нормализует коллекцию размеров, удаляет дубли и сортирует по возрастанию."""
    if raw_sizes is None:
        return []
    if isinstance(raw_sizes, str):
        # SZ-02: «37,5» внутри списка — дробный размер, а не два размера 37 и 5
        text = _DECIMAL_COMMA_RE.sub(r"\1.\2", raw_sizes)
        items: Iterable[Any] = re.split(r"[,;/|]+", text)
    elif isinstance(raw_sizes, dict):
        items = raw_sizes.keys()
    elif isinstance(raw_sizes, (list, tuple, set)):
        items = raw_sizes
    else:
        return []

    seen = set()
    out: List[str] = []
    for entry in items:
        if isinstance(entry, dict):
            token = entry.get("size") or entry.get("value") or entry.get("name")
        else:
            token = entry
        norm = normalize_size_token(token)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    out.sort(key=sort_size_key)
    return out


# После роста допустимы «см»; дюймы, мм и знак дюйма — это техника
_NON_HEIGHT_UNIT_RE = re.compile(r"(?iu)^\s*(?:дюйм\w*|\"|''|мм\b|inch\w*)")
_QUERY_HEIGHT_RE = re.compile(
    r"(?iu)(?<!\w)(?:(?:на\s+)?рост[а]?\s*(\d{2,3})(?:\s*см\b)?|(\d{2,3})\s*(?:см\s*)?рост\w*|(?:размер[ауе]?|р-р)\s*(\d{2,3})(?:\s*см\b)?)(?!\w)"
)


def extract_size_from_query(query: str) -> Tuple[Optional[str], str]:
    """Извлекает размер из поискового запроса при явном маркере или обувном/одежном контексте.

    Возвращает `(normalized_size, cleaned_query)`. Если размер не найден, возвращает `(None, query)`.
    """
    text = str(query or "").strip()
    if not text:
        return None, text

    # 0. Рост (SZ-05): «рост 98», «на рост 104», «98 рост», «размер 110» (110 — не обувь, значит рост)
    m_h = _QUERY_HEIGHT_RE.search(text)
    if m_h:
        value = int(m_h.group(1) or m_h.group(2) or m_h.group(3))
        token = _height_token(value)
        if token and not (m_h.group(3) and value <= 58) and not _NON_HEIGHT_UNIT_RE.match(text[m_h.end():]):
            cleaned = re.sub(r"\s+", " ", (text[:m_h.start()] + " " + text[m_h.end():])).strip()
            return token, cleaned

    # 1. Явный маркер: «размер 42», «42 размер», «р. 38», «size XL»
    m_exp = _EXPLICIT_SIZE_RE.search(text)
    if m_exp:
        raw_tok = m_exp.group(1) or m_exp.group(2)
        norm = normalize_size_token(raw_tok)
        if norm and _NON_APPAREL_UNIT_RE.match(text[m_exp.end():]):
            norm = None
        if norm:
            cleaned = (text[:m_exp.start()] + " " + text[m_exp.end():]).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            return norm, cleaned

    # 2. Обувной контекст: «кроссовки 42», «женские ботинки 38.5»
    if _SHOE_CONTEXT_RE.search(text):
        for m in _CONTEXT_NUM_RE.finditer(text):
            norm = normalize_size_token(m.group(1))
            if norm:
                try:
                    num = float(norm)
                except ValueError:
                    continue
                if 18 <= num <= 48:
                    cleaned = (text[:m.start()] + " " + text[m.end():]).strip()
                    cleaned = re.sub(r"\s+", " ", cleaned)
                    return norm, cleaned

    # 3. Одежный контекст: «куртка XL», «пуховик 2XL»
    if _CLOTHING_CONTEXT_RE.search(text):
        m_let = _CONTEXT_LETTER_RE.search(text)
        if m_let:
            norm = normalize_size_token(m_let.group(1))
            if norm:
                cleaned = (text[:m_let.start()] + " " + text[m_let.end():]).strip()
                cleaned = re.sub(r"\s+", " ", cleaned)
                return norm, cleaned

    return None, text
