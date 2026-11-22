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
_CONTEXT_LETTER_RE = re.compile(r"(?iu)(?<!\w)(2xs|xxs|xs|2xl|3xl|4xl|xxxl|xxl|xl)(?!\w)")


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


def sort_size_key(size: str) -> Tuple[int, float, str]:
    """Ключ естественной сортировки размеров: числа по возрастанию, затем XS..XXXL."""
    norm = normalize_size_token(size) or str(size).strip().upper()
    try:
        return (0, float(norm), norm)
    except ValueError:
        pass
    if norm in _LETTER_ORDER:
        return (1, float(_LETTER_ORDER[norm]), norm)
    return (2, 0.0, norm)


def normalize_available_sizes(raw_sizes: Any) -> List[str]:
    """Нормализует коллекцию размеров, удаляет дубли и сортирует по возрастанию."""
    if raw_sizes is None:
        return []
    if isinstance(raw_sizes, str):
        items: Iterable[Any] = re.split(r"[,;/|]+", raw_sizes)
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


def extract_size_from_query(query: str) -> Tuple[Optional[str], str]:
    """Извлекает размер из поискового запроса при явном маркере или обувном/одежном контексте.

    Возвращает `(normalized_size, cleaned_query)`. Если размер не найден, возвращает `(None, query)`.
    """
    text = str(query or "").strip()
    if not text:
        return None, text

    # 1. Явный маркер: «размер 42», «42 размер», «р. 38», «size XL»
    m_exp = _EXPLICIT_SIZE_RE.search(text)
    if m_exp:
        raw_tok = m_exp.group(1) or m_exp.group(2)
        norm = normalize_size_token(raw_tok)
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
