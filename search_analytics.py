"""Аналитика поиска (этап P06): исход каждого поиска, нормализация и очистка текста запроса.

Что здесь есть и чего нет:
- исход определяется **качеством совпадения**, а не числом строк: десять чехлов на запрос «RTX 5090» — это WEAK,
  а не успех; ошибка backend никогда не выдаётся за «ничего не найдено»;
- текст запроса нормализуется и очищается **до** записи; запросы, похожие на личные данные (телефон, почта,
  адрес, длинные номера), не сохраняются текстом совсем;
- идентификаторы пользователя (user_id, Telegram ID, IP, сессия) сюда не попадают ни в каком виде — модуль их
  не принимает, поэтому профиль человека из этих данных не собрать.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional

# Исходы поиска
FOUND = "found"          # есть хотя бы один уверенно подходящий товар
WEAK = "weak"            # что-то нашлось, но по сути мимо: только аксессуары или слабые совпадения
NOT_FOUND = "not_found"  # подходящих строк нет
ERROR = "error"          # поиск не отработал (исключение, недоступный источник)
OUTCOMES = (FOUND, WEAK, NOT_FOUND, ERROR)

# Источники поиска, которые аналитика принимает: любое другое значение — ошибка вызова, а не новая строка отчёта
SOURCES = ("catalog", "live")

RETENTION_DAYS = 90        # срок хранения аналитики (решение владельца, 2026-09-20)
MIN_OCCURRENCES_TO_STORE_TEXT = 3  # текст запроса сохраняется только начиная с третьего раза
MAX_QUERY_CHARS = 80       # длинные тексты — не поисковый запрос, а сообщение; такие не храним текстом

# Товар считается подходящим, если совпали не меньше двух слов запроса (или единственное слово, когда
# запрос из одного слова) И все названные человеком признаки модели. Это не сортировочная оценка
# релевантности: она зависит от длины названия, и подробное название считалось бы «мимо» только из-за длины.
MIN_MATCHED_TOKENS = 2

# Слова-варианты: если человек написал «pro», «max», «plus» — товар без них другой (F02)
VARIANT_TOKENS = frozenset(("pro", "max", "plus", "ultra", "mini", "lite", "air", "se", "fe", "prox",
                            "promax", "гб", "gb", "tb", "тб"))

_SENSITIVE_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I),               # почта
    re.compile(r"(?:\+?\d[\s()-]?){10,}"),                          # телефон и длинные номера
    re.compile(r"\b\d{6,}\b"),                                      # номер карты, ИИН, заказ
    re.compile(r"https?://|\bwww\.", re.I),                         # ссылки
    re.compile(r"@[a-z0-9_]{3,}", re.I),                            # ник в мессенджере
    # Адрес: слова-указатели считаются личными данными сами по себе — в запросе о товаре они не нужны,
    # а номер дома может стоять через несколько слов («ул. Абая 15»)
    re.compile(r"\b(?:ул|улица|пр-?т|проспект|мкр|микрорайон|кв|квартира|дом)\b\.?(?=\s|$)", re.I),
)

_PUNCT = re.compile(r"[^\w\s.+-]", re.U)
_SPACES = re.compile(r"\s+")


def normalize(query: Any) -> str:
    """Нормализованный вид запроса: регистр, пробелы и лишняя пунктуация не создают разных строк."""
    text = _PUNCT.sub(" ", str(query or "").lower())
    return _SPACES.sub(" ", text).strip()


def is_sensitive(query: Any) -> bool:
    """Похоже на личные данные: такой запрос не сохраняется текстом (считается только в общих числах)."""
    text = str(query or "")
    if len(text.strip()) > MAX_QUERY_CHARS:
        return True
    return any(p.search(text) for p in _SENSITIVE_PATTERNS)


def storable_text(query: Any) -> Optional[str]:
    """Текст для хранения или None, если запрос пустой либо похож на личные данные."""
    if is_sensitive(query):
        return None
    normalized = normalize(query)
    return normalized or None


CITY_ALL = "Все"
CITY_COUNTRY = "Казахстан"
CITY_UNKNOWN = "Неизвестно"


def canonical_city(city: Any) -> str:
    """Город приводится к справочнику до записи (F01).

    В параметре city из запроса к API может прийти произвольный текст (вплоть до личных данных), поэтому
    исходное значение не сохраняется нигде: известный город — своим названием из справочника, «все» и
    «Казахстан» — общими значениями, всё остальное — «Неизвестно».
    """
    from telemetry import canonical_city as _canonical
    code = _canonical(city)
    if code in (None, "unknown"):
        return CITY_ALL if code is None else CITY_UNKNOWN
    if code == "all":
        return CITY_ALL
    if code == "kz":
        return CITY_COUNTRY
    try:
        from config import CITIES_KZ
        return next(c["name"] for c in CITIES_KZ.values() if str(c["id"]) == code)
    except Exception:
        return CITY_UNKNOWN


def query_key(query: Any, city: Any = None) -> str:
    """Ключ для подсчёта повторов: сам текст в базе не хранится до третьего раза.

    Это не защита от подбора — короткий запрос из словаря можно проверить перебором хешей; ключ лишь
    избавляет от хранения текста редких запросов в открытом виде.
    """
    base = f"{normalize(query)}|{canonical_city(city)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _has_token(title: str, token: str) -> bool:
    """Слово запроса встречается в названии как отдельное слово: «15» не совпадает с «156» или «128gb»."""
    return re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", title) is not None


def _is_accessory(title: str, nouns: Iterable[str]) -> bool:
    from search_engine import _is_accessory_for
    return _is_accessory_for(title or "", list(nouns))


def classify(query: str, items: Optional[List[Dict[str, Any]]], failed: bool = False) -> str:
    """Исход поиска по качеству совпадения, а не по числу строк.

    FOUND     — есть товар с уверенным совпадением (и это не аксессуар, когда искали не аксессуар);
    WEAK      — строки есть, но все либо аксессуары к запросу, либо совпадают слабо;
    NOT_FOUND — подходящих строк нет;
    ERROR     — поиск не отработал.
    """
    if failed:
        return ERROR
    if not items:
        return NOT_FOUND

    from search_engine import is_accessory_query

    query_clean = normalize(query)
    tokens = [t for t in query_clean.split() if t]
    if not tokens:
        return NOT_FOUND
    needed = min(MIN_MATCHED_TOKENS, len(tokens))
    # Признаки конкретной модели: номер (5090, 15), объём памяти (256gb) и слова-варианты (pro, max).
    # Их человек назвал явно, поэтому товар без них — другой товар, а не успешный ответ (F02).
    required = [t for t in tokens if any(ch.isdigit() for ch in t) or t in VARIANT_TOKENS]
    wants_accessory = is_accessory_query(query_clean)

    for item in items:
        title = str(item.get("title") or "").lower()
        if not title:
            continue
        # Аксессуар засчитывается только тогда, когда его и искали: «чехол» на запрос «RTX 5090» — не ответ
        if not wants_accessory and _is_accessory(title, tokens):
            continue
        if required and not all(_has_token(title, t) for t in required):
            continue
        if query_clean in title or sum(1 for t in tokens if _has_token(title, t)) >= needed:
            return FOUND
    return WEAK


def success_rate(counts: Dict[str, int]) -> Optional[float]:
    """Доля успеха поиска: FOUND / (FOUND + WEAK + NOT_FOUND).

    Ошибки в знаменатель не входят и не выдаются за «не найдено» — они показываются отдельной долей
    (`error_rate`), иначе поломка поиска выглядела бы как отсутствие товара.
    """
    answered = sum(int(counts.get(k) or 0) for k in (FOUND, WEAK, NOT_FOUND))
    if not answered:
        return None
    return round(int(counts.get(FOUND) or 0) * 100.0 / answered, 1)


def error_rate(counts: Dict[str, int]) -> Optional[float]:
    """Доля поисков, завершившихся ошибкой, от всех попыток (включая ошибочные)."""
    total = sum(int(counts.get(k) or 0) for k in OUTCOMES)
    if not total:
        return None
    return round(int(counts.get(ERROR) or 0) * 100.0 / total, 1)
