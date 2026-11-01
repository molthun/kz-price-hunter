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
