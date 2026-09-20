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

RETENTION_DAYS = 90        # срок хранения аналитики (решение владельца, 2026-09-20)
MIN_OCCURRENCES_TO_STORE_TEXT = 3  # текст запроса сохраняется только начиная с третьего раза
MAX_QUERY_CHARS = 80       # длинные тексты — не поисковый запрос, а сообщение; такие не храним текстом

# Товар считается подходящим, если в его названии есть не меньше двух слов запроса (или единственное слово,
# когда запрос из одного слова). Это не сортировочная оценка релевантности: она зависит от длины названия,
# и товар с подробным названием мог бы считаться «мимо» только из-за длины.
MIN_MATCHED_TOKENS = 2

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


def query_key(query: Any, city: Optional[str] = None) -> str:
    """Ключ для подсчёта повторов без хранения текста: по нему текст не восстановить."""
    base = f"{normalize(query)}|{(city or '').strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


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
    wants_accessory = is_accessory_query(query_clean)

    for item in items:
        title = str(item.get("title") or "").lower()
        if not title:
            continue
        # Аксессуар засчитывается только тогда, когда его и искали: «чехол» на запрос «RTX 5090» — не ответ
        if not wants_accessory and _is_accessory(title, tokens):
            continue
        if query_clean in title or sum(1 for t in tokens if t in title) >= needed:
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
