"""Фоновая сортировка каталога: сначала правила, локальная модель только там, где они не справились.

Модель не может ничего испортить молча: каждое решение пишется в category_ai_verdicts со старой
категорией, а применяется только уверенное и не расширяющее раздел. Применённое решение закреплено:
следующее сканирование магазина категорию не перезаписывает (repositories/product_repo.py).
Включается переменной окружения CATALOG_AI_SORT_ENABLED=1.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import re
import statistics
from typing import Any, Awaitable, Callable, Dict, List, Optional

from domain.category import MASTER_TAXONOMY, TaxonomyMapper

MIN_CONFIDENCE = 0.90  # на выборке стенда ниже этого порога ошибки заметны, выше не встретились
BATCH_SIZE = 20
PAUSE_BETWEEN_CALLS_SECONDS = 0.5  # Ollama одна на прод и стенд
RULE_MODEL = "rule"

_CATEGORIES_TEXT = "\n".join(f"{c.id}: {c.name}" for c in MASTER_TAXONOMY)
_mapper = TaxonomyMapper()

Ask = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]


def enabled() -> bool:
    return os.environ.get("CATALOG_AI_SORT_ENABLED") == "1"


def build_prompt(title: str, current: str) -> str:
    return (
        "Ты сортируешь товары интернет-магазинов Казахстана по разделам каталога.\n"
        f"Разделы (код: название):\n{_CATEGORIES_TEXT}\nother: нет подходящего раздела\n\n"
        "Выбери самый узкий подходящий раздел. Смотри на то, чем товар является, а не с чем связан: "
        "чехол для телефона это чехол, мясорубка это техника для кухни, порошок для стирки это "
        "бытовая химия, таблетки это лекарства. Если не уверен, ставь низкую уверенность.\n\n"
        f"Товар: {title}\nРаздел магазина: {current}\n\n"
        'Ответь JSON: {"id":"код","c":уверенность от 0 до 1}'
    )


def decide(current: str, answer: Any) -> Dict[str, Any]:
    """Разбирает ответ модели. new_category есть всегда, когда модель назвала раздел; applied — можно ли его применить."""
    result: Dict[str, Any] = {"new_category": None, "confidence": None, "applied": False}
    if not isinstance(answer, dict):
        return result
    target = _mapper.get_by_id(str(answer.get("id") or "").strip().lower())
    try:
        confidence = float(answer.get("c"))
    except (TypeError, ValueError):
        confidence = 0.0
    result["confidence"] = confidence
    if not target:
        return result
    result["new_category"] = target.name
    if confidence < MIN_CONFIDENCE or target.name == current:
        return result
    # Из узкого раздела в его же родителя не переносим: это потеря точности
    current_cat = next((c for c in MASTER_TAXONOMY if c.name == current), None)
    if current_cat and target.id in {b["id"] for b in _mapper.get_breadcrumbs(current_cat.id)}:
        return result
    result["applied"] = True
    return result


def next_candidates(conn, limit: int = BATCH_SIZE) -> List[Dict[str, str]]:
    """Сначала товары вне каталога, затем лежащие в корневых разделах, затем остальные."""
    names = [c.name for c in MASTER_TAXONOMY]
    roots = [c.name for c in MASTER_TAXONOMY if not c.parent_id]
    base = (
        "SELECT p.id, p.title, p.category FROM canonical_products p "
        "WHERE NOT EXISTS (SELECT 1 FROM category_ai_verdicts v WHERE v.product_id = p.id) "
    )
    rows: list = []
    for clause, params in (
        (f"AND COALESCE(p.category, '') NOT IN ({','.join('?' * len(names))})", names),
        (f"AND p.category IN ({','.join('?' * len(roots))})", roots),
        ("", []),
    ):
        rows = conn.execute(f"{base}{clause} LIMIT ?", [*params, limit]).fetchall()
        if rows:
            break
    return [{"id": r[0], "title": r[1] or "", "category": r[2] or ""} for r in rows]


def save_verdict(conn, product_id: str, old: str, new: Optional[str], confidence: Optional[float],
                 model: str, applied: bool) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO category_ai_verdicts
            (product_id, old_category, new_category, confidence, model, applied, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (product_id, old, new, confidence, model, 1 if applied else 0, now),
    )
    if applied:
        conn.execute("UPDATE canonical_products SET category = ? WHERE id = ?", (new, product_id))
    conn.commit()


async def sort_tick(get_conn: Callable[[], Any], ask: Ask, model: str, classifier: Any,
                    limit: int = BATCH_SIZE, pause: float = PAUSE_BETWEEN_CALLS_SECONDS) -> Dict[str, int]:
    """Один проход. Без ответа модели товар остаётся в очереди, проход прерывается."""
    stats = {"rule": 0, "asked": 0, "applied": 0, "failed": 0}

    def _with_conn(fn, *args):
        conn = get_conn()
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    for item in await asyncio.to_thread(_with_conn, next_candidates, limit):
        rule = classifier.classify_rules(item["title"])
        if rule and rule.category_name == item["category"]:
            await asyncio.to_thread(_with_conn, save_verdict, item["id"], item["category"],
                                    item["category"], None, RULE_MODEL, False)
            stats["rule"] += 1
            continue
        answer = await ask(build_prompt(item["title"], item["category"]))
        if answer is None:
            stats["failed"] += 1
            break
        verdict = decide(item["category"], answer)
        await asyncio.to_thread(_with_conn, save_verdict, item["id"], item["category"],
                                verdict["new_category"], verdict["confidence"], model, verdict["applied"])
        stats["asked"] += 1
        stats["applied"] += 1 if verdict["applied"] else 0
        await asyncio.sleep(pause)
    return stats


# Проверка подозрительно дешёвых товаров в разделах устройств. Раздел здесь — наш запрос к каталогу магазина,
# поэтому в «Смартфоны» приходят держатели и колонки без типа в названии. Модель хорошо видит, что товар
# не на месте, но раздел часто называет неверно («Смарт часы» → «Кабели»), поэтому её ответ сильно ограничен.
CHECK_MODEL = "ai_check"
CHEAP_RATIO = 0.15
_DEVICE_SIGNS = {
    "Смартфоны": r"смартфон|телефон|\d\s*/\s*\d|\d\s*(?:гб|gb)|phone",
    "Ноутбуки": r"ноутбук|laptop|\d\s*(?:гб|gb)|intel|ryzen|celeron",
    "Планшеты": r"планшет|pad\b|tab\b|дюйм|\d\s*(?:гб|gb)",
    "Смарт-часы": r"часы|watch|браслет|band|\d\s*(?:мм|mm)",
    "Телевизоры": r"телевиз|проектор|\btv\b|\d\s*см\b|дюйм|\"",
    "Мониторы": r"монитор|дюйм|\"",
    "Холодильники": r"холодил|морозил",
    "Стиральные машины": r"стирал",
    "Пылесосы": r"пылесос",
    "Кондиционеры": r"кондиционер|сплит",
    "Видеокарты": r"видеокарт|geforce|radeon|\bgt\s?\d|\brtx\b|\bgtx\b",
    "Процессоры": r"процессор|intel|amd|ryzen|xeon|pentium|celeron",
    "Материнские платы": r"материнск|\b[abhxz]\d{3}",
    "Игровые приставки": r"консол|приставк|dendy|playstation|xbox|nintendo",
}
# Разделы, названным моделью которым можно верить; остальное уходит в общий раздел родителя
# Дешёвый смартфон или ноутбук без памяти в названии почти не встречается, поэтому там верим и коду
_ANY_TITLE_CATEGORIES = {"Смартфоны", "Ноутбуки"}
_VACUUM_PART_RE = re.compile(r"стяжк|щетк|щётк|насадк|фильтр|мешок|мешки|шланг|турбощ", re.I)
_CHECK_TARGETS = {"vacuum_parts", "toys", "haircare", "phone_cases", "printer_ink", "home", "kids", "audio", "car_electronics"}


def find_cheap_suspects(conn, classifier) -> List[Dict[str, Any]]:
    """Дешевле CHEAP_RATIO медианы раздела, без признаков устройства и правила по названию, не из выгрузки."""
    suspects: List[Dict[str, Any]] = []
    for category, signs in _DEVICE_SIGNS.items():
        rows = conn.execute(
            """SELECT c.id, c.title, MIN(o.price), COALESCE(MAX(p.shop = 'Белый Ветер'), 0), MAX(v.model = ?)
               FROM canonical_products c
               JOIN offers o ON o.product_id = c.id AND o.is_active = 1 AND o.price > 0
               LEFT JOIN products p ON p.id = substr(o.id, 5)
               LEFT JOIN category_ai_verdicts v ON v.product_id = c.id
               WHERE c.category = ? GROUP BY c.id""",
            (CHECK_MODEL, category),
        ).fetchall()
        if len(rows) < 20:
            continue
        limit = statistics.median(r[2] for r in rows) * CHEAP_RATIO
        sign_re = re.compile(signs, re.I)
        for product_id, title, price, from_feed, checked in rows:
            if price >= limit or from_feed or checked or sign_re.search(title or ""):
                continue
            rule = classifier.classify_rules(title)
            if rule and rule.source in ("rule_title", "rule_accessory"):
                continue
            suspects.append({"id": product_id, "title": title, "price": price, "category": category})
    return suspects


def build_check_prompt(item: Dict[str, Any]) -> str:
    name = item["category"].lower()
    return (
        f"Товар лежит в разделе «{item['category']}», но стоит {int(item['price'])} тенге, намного дешевле обычного. "
        f"Если это действительно {name} (например, дешёвая модель), ответь same:true. "
        "Если это аксессуар, запчасть или другой товар, ответь same:false и выбери раздел.\n"
        f"Разделы (код: название):\n{_CATEGORIES_TEXT}\n\nТовар: {item['title']}\n\n"
        'Ответь JSON: {"same":true или false,"id":"код","c":уверенность от 0 до 1}'
    )


def _names_a_thing(title: str) -> bool:
    """В названии есть слово, а не только код: «Haley HY-906» модель оценить не может, это бывает и дешёвый пылесос."""
    from domain.product_options import title_color
    words = re.findall(r"[А-ЯЁа-яё][а-яё]{3,}", title or "")
    return any(not title_color(word) for word in words)


def decide_check(category: str, answer: Any, title: str = "") -> Optional[str]:
    """Новый раздел или None, если товар остаётся на месте."""
    if not isinstance(answer, dict) or answer.get("same") is not False:
        return None
    if category not in _ANY_TITLE_CATEGORIES and not _names_a_thing(title):
        return None
    try:
        if float(answer.get("c")) < MIN_CONFIDENCE:
            return None
    except (TypeError, ValueError):
        return None
    target = _mapper.get_by_id(str(answer.get("id") or "").strip().lower())
    if target and target.id == "vacuum_parts" and not _VACUUM_PART_RE.search(title or ""):
        return None
    if target and target.id in _CHECK_TARGETS and target.name != category:
        return target.name
    current = next((c for c in MASTER_TAXONOMY if c.name == category), None)
    parent = _mapper.get_by_id(current.parent_id) if current and current.parent_id else None
    return parent.name if parent else None


def save_check(conn, item: Dict[str, Any], target: Optional[str], model_answer: Any) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    confidence = model_answer.get("c") if isinstance(model_answer, dict) else None
    conn.execute(
        """INSERT OR REPLACE INTO category_ai_verdicts
           (product_id, old_category, new_category, confidence, model, applied, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (item["id"], item["category"], target or item["category"], confidence, CHECK_MODEL, 1 if target else 0, now),
    )
    if target:
        conn.execute("UPDATE canonical_products SET category = ?, item_kind = 'accessory' WHERE id = ?", (target, item["id"]))
    conn.commit()


async def check_cheap_devices(get_conn: Callable[[], Any], ask: Ask, classifier: Any,
                              pause: float = PAUSE_BETWEEN_CALLS_SECONDS) -> Dict[str, int]:
    """Один проход по подозрительным товарам. Проверенные повторно не спрашиваются."""
    def _with_conn(fn, *args):
        conn = get_conn()
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    stats = {"asked": 0, "moved": 0}
    for item in await asyncio.to_thread(_with_conn, find_cheap_suspects, classifier):
        answer = await ask(build_check_prompt(item))
        if answer is None:
            break
        target = decide_check(item["category"], answer, item["title"])
        await asyncio.to_thread(_with_conn, save_check, item, target, answer)
        stats["asked"] += 1
        stats["moved"] += 1 if target else 0
        await asyncio.sleep(pause)
    return stats
