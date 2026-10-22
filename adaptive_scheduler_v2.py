"""Adaptive Scheduler 2.0 (S2-E15/E17): план обновления категорий по спросу.

Что делает:
- собирает сигналы неудовлетворённого спроса (S2-E13) и PENDING-задачи discovery (S2-E14);
- переводит их в категории каталога, которые стоит обновить в ближайшей волне;
- по умолчанию работает в режиме shadow: план считается и записывается, но волна
  обхода не меняется. Включённый режим (on) добавляет к волне не больше
  MAX_EXTRA_CATEGORIES категорий — приоритет остаётся за базовым планом волн.

Ограничения этапа:
- задачи discovery здесь только учитываются как сигнал; их статусы не меняются,
  исполнение задач (поиск новых продавцов) — отдельный этап;
- ошибка планировщика не имеет права ломать обход: любое исключение возвращает
  «плана нет», волна остаётся базовой.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import MASTER_CATEGORIES

logger = logging.getLogger("kz_price_hunter.adaptive_scheduler_v2")

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ON = "on"
MODES = (MODE_OFF, MODE_SHADOW, MODE_ON)
ENV_VAR = "ADAPTIVE_SCHEDULER_2"
DEFAULT_MODE = MODE_SHADOW
MAX_EXTRA_CATEGORIES = 2
PLANS_KEEP = 200

# Основы слов для сопоставления запроса с мастер-категорией. Сопоставление детерминированное:
# никакого AI здесь нет, иначе план стал бы невоспроизводимым.
CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "smartphones": ("смартфон", "телефон", "айфон", "iphone", "galaxy", "pixel", "redmi", "honor"),
    "laptops": ("ноутбук", "ультрабук", "макбук", "macbook", "моноблок", "нетбук"),
    "pc_components": ("видеокарт", "rtx", "radeon", "процессор", "ryzen", "intel core", "материнск",
                      "ssd", "оператив", "озу", "блок питания", "корпус", "кулер"),
    "monitors": ("монитор", "дисплей"),
    "tvs": ("телевизор", "проектор", "oled", "qled"),
    "audio": ("наушник", "гарнитур", "airpods", "колонк", "саундбар"),
    "tablets_watches": ("планшет", "ipad", "смарт-час", "smart watch", "apple watch", "фитнес-браслет"),
    "consoles": ("приставк", "playstation", "ps5", "xbox", "nintendo", "консоль"),
    "appliances_large": ("холодильник", "стиральн", "посудомоечн", "кондиционер", "морозильн"),
    "appliances_small": ("пылесос", "микроволнов", "кофемашин", "мультиварк", "чайник", "утюг"),
    "office_network": ("принтер", "мфу", "роутер", "маршрутизатор", "сканер", "картридж"),
    "grocery": ("продукт", "бакале", "кофе", "чай", "молок", "мясо", "овощ"),
    "household": ("бытов", "химия", "порошок", "салфетк", "бумага туалет"),
    "diy": ("дрель", "инструмент", "стройматериал", "краска", "плитка", "саморез"),
    "clothes": ("одежд", "обувь", "кроссовк", "куртк", "джинс", "платье"),
    "pets": ("корм", "зоотовар", "наполнитель", "когтеточк", "поводок"),
    "home_furniture": ("мебель", "диван", "кресло", "стол", "матрас", "шкаф", "кроват"),
    "beauty_health": ("парфюм", "косметик", "крем", "шампун", "витамин", "лекарств", "тонометр"),
}


def normalize_text(text: Any) -> str:
    """Нижний регистр и «ё» → «е»: запросы и названия категорий сравниваются одинаково."""
    return str(text or "").lower().replace("ё", "е")


def categories_for_query(query: Any) -> List[str]:
    """Категории каталога, к которым относится запрос. Порядок — порядок MASTER_CATEGORIES."""
    text = normalize_text(query)
    if not text:
        return []
    matched: List[str] = []
    for key in MASTER_CATEGORIES:
        keywords = CATEGORY_KEYWORDS.get(key, ())
        if any(keyword in text for keyword in keywords):
            matched.append(key)
    return matched


def mode(environ: Optional[Dict[str, str]] = None) -> str:
    """Режим планировщика: off / shadow / on. Неизвестное значение — безопасный shadow."""
    env = environ if environ is not None else os.environ
    value = normalize_text(env.get(ENV_VAR, DEFAULT_MODE)).strip()
    return value if value in MODES else DEFAULT_MODE


def _signal_score(signal: Any) -> float:
    """Чем больше спроса и меньше успеха, тем важнее обновить категорию."""
    try:
        search_count = float(getattr(signal, "search_count", 0) or 0)
        success_rate = float(getattr(signal, "success_rate", 100.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, search_count * (1.0 - success_rate / 100.0)), 2)


def _signal_query(signal: Any) -> str:
    return str(getattr(signal, "normalized_query", "") or getattr(signal, "sample_query", "") or "")


def _enabled_set(enabled_categories: Any) -> set:
    if enabled_categories is None:
        return set(MASTER_CATEGORIES)
    if isinstance(enabled_categories, dict):
        return {key for key, value in enabled_categories.items() if value and key in MASTER_CATEGORIES}
    return {key for key in enabled_categories or () if key in MASTER_CATEGORIES}


def build_plan(demand_signals: Iterable[Any] = (), discovery_tasks: Iterable[Any] = (),
               enabled_categories: Any = None, limit: int = MAX_EXTRA_CATEGORIES,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Считает план: какие категории обновить из-за спроса и какие discovery-задачи учесть."""
    enabled = _enabled_set(enabled_categories)
    category_scores: Dict[str, float] = {}
    demand_entries: List[Dict[str, Any]] = []
    task_entries: List[Dict[str, Any]] = []

    for signal in demand_signals or ():
        query = _signal_query(signal)
        score = _signal_score(signal)
        categories = categories_for_query(query)
        if not categories or score <= 0:
            continue
        demand_entries.append({
            "query": query,
            "score": score,
            "search_count": getattr(signal, "search_count", 0),
            "success_rate": getattr(signal, "success_rate", None),
            "categories": categories,
        })
        for key in categories:
            if key in enabled:
                category_scores[key] = category_scores.get(key, 0.0) + score

    for task in discovery_tasks or ():
        status = str(getattr(task, "status", "") or "")
        if status and status != "PENDING":
            continue
        query = str(getattr(task, "signal_query", "") or getattr(task, "normalized_query", "") or "")
        categories = categories_for_query(query)
        try:
            priority = float(getattr(task, "priority_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            priority = 0.0
        score = priority if priority > 0 else 0.0
        if not categories or score <= 0:
            continue
        task_entries.append({
            "task_id": getattr(task, "id", ""),
            "query": query,
            "city": getattr(task, "city", None),
            "priority_score": score,
            "categories": categories,
        })
        for key in categories:
            if key in enabled:
                category_scores[key] = category_scores.get(key, 0.0) + score

    order = {key: index for index, key in enumerate(MASTER_CATEGORIES)}
    ordered = sorted(category_scores.items(), key=lambda item: (-item[1], order.get(item[0], 999)))
    extra = [key for key, _ in ordered[:max(0, int(limit))]]
    demand_entries.sort(key=lambda item: item["score"], reverse=True)
    task_entries.sort(key=lambda item: item["priority_score"], reverse=True)

    return {
        "id": f"plan_{uuid.uuid4().hex[:12]}",
        "mode": mode(),
        "generated_at": now_iso or datetime.now(timezone.utc).isoformat(),
        "extra_categories": extra,
        "category_scores": {key: round(score, 2) for key, score in ordered},
        "demand_queries": demand_entries[:10],
        "discovery_tasks": task_entries[:10],
        "reasons": {
            "demand_signals": len(demand_entries),
            "discovery_tasks": len(task_entries),
            "enabled_categories": len(enabled),
        },
    }


def load_inputs(conn, min_searches: int = 5, max_success_rate: float = 70.0,
                limit: int = 20) -> Tuple[List[Any], List[Any]]:
    """Читает сигналы спроса и PENDING-задачи discovery из БД."""
    from domain.adaptive_discovery import DiscoveryTaskStatus
    from repositories.discovery_task_repo import DiscoveryTaskRepository
    from repositories.search_analytics_repo import SearchAnalyticsRepository

    signals = SearchAnalyticsRepository(conn).get_discovery_signals(
        min_searches=min_searches, max_success_rate=max_success_rate, limit=limit)
    tasks = DiscoveryTaskRepository(conn).find_tasks(
        status=DiscoveryTaskStatus.PENDING, limit=limit)
    tasks.sort(key=lambda task: getattr(task, "priority_score", 0.0), reverse=True)
    return list(signals), list(tasks)


def record_plan(conn, plan: Dict[str, Any]) -> str:
    """Сохраняет план в scheduler_plans (история для наблюдения за shadow-режимом)."""
    conn.execute(
        """
        INSERT INTO scheduler_plans (id, mode, generated_at, extra_categories,
                                     demand_queries, discovery_tasks, reasons)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["id"], plan["mode"], plan["generated_at"],
            json.dumps(plan["extra_categories"], ensure_ascii=False),
            json.dumps(plan["demand_queries"], ensure_ascii=False),
            json.dumps(plan["discovery_tasks"], ensure_ascii=False),
            json.dumps(plan["reasons"], ensure_ascii=False),
        ),
    )
    conn.execute(
        "DELETE FROM scheduler_plans WHERE id NOT IN "
        "(SELECT id FROM scheduler_plans ORDER BY generated_at DESC LIMIT ?)",
        (PLANS_KEEP,),
    )
    conn.commit()
    return plan["id"]


def latest_plan(conn) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM scheduler_plans ORDER BY generated_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "mode": row["mode"],
        "generated_at": row["generated_at"],
        "extra_categories": json.loads(row["extra_categories"] or "[]"),
        "demand_queries": json.loads(row["demand_queries"] or "[]"),
        "discovery_tasks": json.loads(row["discovery_tasks"] or "[]"),
        "reasons": json.loads(row["reasons"] or "{}"),
    }


def get_plan(conn=None, enabled_categories: Any = None,
             limit: int = MAX_EXTRA_CATEGORIES) -> Dict[str, Any]:
    """Считает и (если режим не off) записывает план. Без conn открывает своё соединение."""
    from database import get_connection  # локально: модуль импортируется из web/server.py

    active_mode = mode()
    if active_mode == MODE_OFF:
        return {"id": "", "mode": MODE_OFF, "generated_at": None, "extra_categories": [],
                "category_scores": {}, "demand_queries": [], "discovery_tasks": [],
                "reasons": {"demand_signals": 0, "discovery_tasks": 0}}
    owns = conn is None
    if owns:
        conn = get_connection()
    try:
        signals, tasks = load_inputs(conn)
        plan = build_plan(signals, tasks, enabled_categories=enabled_categories, limit=limit)
        plan["mode"] = active_mode
        record_plan(conn, plan)
        return plan
    finally:
        if owns:
            conn.close()


def adapt_wave_plan(base_categories: Iterable[str], conn=None,
                    enabled_categories: Any = None) -> Dict[str, Any]:
    """Точка интеграции с волной обхода.

    Возвращает {"mode", "extra_categories", "summary"}. extra_categories пуст в shadow/off
    или при любой ошибке — базовый план волн важнее надстройки.
    """
    result = {"mode": mode(), "extra_categories": [], "summary": {}}
    if result["mode"] == MODE_OFF:
        return result
    try:
        plan = get_plan(conn=conn, enabled_categories=enabled_categories)
        base = list(base_categories or [])
        result["summary"] = {
            "mode": plan["mode"],
            "extra_categories": plan["extra_categories"],
            "demand_signals": len(plan["demand_queries"]),
            "discovery_tasks": len(plan["discovery_tasks"]),
        }
        if result["mode"] == MODE_ON:
            result["extra_categories"] = [key for key in plan["extra_categories"] if key not in base]
        return result
    except Exception as error:  # планировщик не должен ломать обход
        logger.warning("adaptive scheduler plan failed: %s", type(error).__name__)
        result["summary"] = {"mode": result["mode"], "error": type(error).__name__}
        return result
