"""AI-помощник администратора (этап P12): объясняет происходящее по проверяемым цифрам.

Устройство простое и намеренно скучное:
- у помощника есть **только чтение** и только готовые сводки — ни одной команды, которая что-то меняет;
- он видит не всю базу и не сырые логи, а ограниченный структурированный срез: состояние магазинов,
  инциденты, аналитику поиска, обходы, расходы AI, планировщик, качество цен, копии;
- у каждого числа указан период и источник, поэтому любой вывод можно перепроверить руками;
- **факты отделены от гипотез**: цифры собирает код, а модель только объясняет их словами;
- если данных нет, так и говорится. Недостаток данных не превращается в выдуманную причину — ответ с
  числами, которых нет в собранных фактах, отклоняется целиком.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# Инструменты помощника: только чтение. Список закрытый — сюда не попадёт ничего, что меняет данные.
TOOLS: Dict[str, Dict[str, Any]] = {}

MAX_ANSWER_CHARS = 2500
MAX_CONTEXT_ITEMS = 12
DEFAULT_DAYS = 7


def tool(name: str, title: str, keywords: Tuple[str, ...]):
    """Регистрирует инструмент чтения: имя, человеческое название и слова, по которым он подбирается."""
    def wrap(fn: Callable[..., Dict[str, Any]]):
        TOOLS[name] = {"name": name, "title": title, "keywords": keywords, "fn": fn}
        return fn
    return wrap


@tool("shops", "Состояние магазинов", ("магазин", "обход", "сканирован", "каталог", "dns", "товар"))
def _shops(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    from web.server import SHOP_REGISTRY, enabled_shop_keys
    registry = {key: (value[2] if len(value) > 2 else key, 0) for key, value in SHOP_REGISTRY.items()}
    data = monitoring.shops_overview(registry, enabled_shop_keys(), now=now)
    shops = data["shops"] if isinstance(data, dict) else data
    return {"period": "последний обход каждого магазина",
            "shops": [{k: s.get(k) for k in ("shop", "name", "status", "reason", "items", "last_success_at")}
                      for s in shops][:25]}


@tool("incidents", "Инциденты", ("инцидент", "ошибк", "сбой", "403", "429", "блокиров", "упал", "почему"))
def _incidents(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    rows = monitoring.incidents(days, now=now)
    return {"period": f"{days} дн.", "total": len(rows),
            "open": sum(1 for r in rows if r.get("open")),
            "incidents": [{k: r.get(k) for k in ("component", "type", "shop", "count", "open",
                                                 "first_seen", "last_seen", "hypothesis")}
                          for r in rows[:MAX_CONTEXT_ITEMS]]}


@tool("scans", "Обходы магазинов и HTTP",
      ("обход", "каталог", "товар", "упал", "меньше", "403", "429", "блокиров", "запрос", "dns"))
def _scans(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Сколько товаров принёс последний обход каждой категории и как отвечали магазины по HTTP.

    Ровно те наблюдения, по которым видно падение каталога и рост отказов: обход стал приносить
    в разы меньше карточек, а доля 4xx выросла.
    """
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = (moment - datetime.timedelta(days=max(1, int(days)))).isoformat()
    scans: List[Dict[str, Any]] = []
    with database.get_connection() as conn:
        for row in conn.execute(
                "SELECT shop_key, category, quality, received, valid, finished_at FROM source_scans "
                "WHERE finished_at >= ? ORDER BY id DESC LIMIT ?", (since, MAX_CONTEXT_ITEMS)):
            scans.append(dict(row))
    http = database.http_metrics_by_shop(days=days, now=moment)
    by_shop = {}
    for shop, row in http.items():
        requests = int(row.get("requests") or 0)
        by_shop[shop] = {
            "requests": requests,
            "errors": int(row.get("errors") or 0),
            "blocked_4xx_429": int(row.get("blocked") or 0),
            "error_share_pct": round(100.0 * int(row.get("errors") or 0) / requests, 1) if requests else None,
            "blocked_share_pct": round(100.0 * int(row.get("blocked") or 0) / requests, 1) if requests else None,
        }
    return {"period": f"{days} дн.", "recent_scans": scans, "http_by_shop": by_shop}


@tool("search", "Аналитика поиска", ("поиск", "ищут", "запрос", "не найден", "спрос"))
def _search(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    data = monitoring.search_analytics(days=days, now=now)
    return {"period": f"{days} дн.", "status": data["status"], "reason": data["reason"],
            "total": data["total"], "success_rate": data["success_rate"], "error_rate": data["error_rate"],
            "counts": data["counts"],
            "bad_queries": [{k: q.get(k) for k in ("normalized_query", "searches", "success_rate")}
                            for q in data.get("bad_queries", [])[:5]]}


@tool("ai", "Расходы AI", ("ai", "модель", "токен", "gemini", "openai", "деньги", "стоимост"))
def _ai(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    data = monitoring.ai_spending(days=days, now=now)
    return {"period": f"{days} дн.", "status": data["status"], "reason": data["reason"],
            "totals": data["totals"], "cost_known": data["cost_known"], "by_task": data["by_task"]}


@tool("scheduler", "Планировщик обходов", ("планировщик", "расписани", "очеред", "порядок", "обновл"))
def _scheduler(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    import scheduler_rollout as rollout
    suggestions = monitoring.scheduler_suggestions(days=days, now=now)
    state = rollout.status(now=now)
    return {"period": f"{days} дн.", "status": suggestions["status"], "reason": suggestions["reason"],
            "candidates": suggestions["candidates"], "profiles": suggestions["profiles"],
            "plan": [{k: p.get(k) for k in ("shop", "category", "score", "age_hours", "reason")}
                     for p in suggestions.get("plan", [])[:5]],
            "rollout_stage": state["stage"], "rollout_canary": state["canary"]}


@tool("quality", "Качество цен и сопоставления", ("цена", "качеств", "фасовк", "сравнен", "арбитраж"))
def _quality(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    data = monitoring.matching_quality(days=days, now=now)
    return {"period": f"{days} дн.", "status": data["status"], "reason": data["reason"],
            "blocked": data["blocked"], "uncertain": data["uncertain"]}


@tool("system", "Система и работники", ("систем", "версия", "работник", "перезапуск", "пульс", "окружени"))
def _system(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    data = monitoring.environment_section(now=now)
    return {"period": "сейчас", "status": data["status"], "reason": data["reason"],
            "versions": data["versions"], "uptime_seconds": data["uptime_seconds"],
            "components": [{k: c.get(k) for k in ("component", "label", "state", "reason")}
                           for c in data["components"]]}


@tool("backups", "Резервные копии", ("копи", "бэкап", "восстановл", "хранени"))
def _backups(days: int = DEFAULT_DAYS, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    import monitoring
    data = monitoring.backup_section(now=now)
    newest = data.get("newest") or {}
    return {"period": "сейчас", "status": data["status"], "reason": data["reason"],
            "backups": len(data.get("backups", [])),
            "newest_age_hours": newest.get("age_hours"),
            "last_check": (data.get("newest_check") or {}).get("detail")}


def pick_tools(question: str, limit: int = 3) -> List[str]:
    """Подбирает инструменты по словам вопроса. Выбор делает код, а не модель: так он предсказуем."""
    text = str(question or "").lower()
    scored = []
    for name, spec in TOOLS.items():
        hits = sum(1 for word in spec["keywords"] if word in text)
        if hits:
            scored.append((-hits, name))
    if not scored:
        # Общий вопрос — показываем состояние сервиса
        return ["shops", "incidents", "system"][:limit]
    scored.sort()
    return [name for _, name in scored[:limit]]


def collect(question: str, days: int = DEFAULT_DAYS,
            now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Собирает факты по вопросу: только чтение, у каждого блока — источник и период."""
    facts: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for name in pick_tools(question):
        spec = TOOLS[name]
        try:
            facts[name] = {"источник": spec["title"], **spec["fn"](days=days, now=now)}
        except Exception as e:                      # недоступный блок — это тоже факт, а не повод молчать
            errors[name] = f"{type(e).__name__}"
    return {"question": str(question or "")[:500], "days": days, "facts": facts, "unavailable": errors}


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(value: Any) -> set:
    """Все числа в структуре — для проверки, что ответ не выдумал цифру."""
    found = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= numbers_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= numbers_in(item)
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        found.add(_norm_number(value))
    elif isinstance(value, str):
        for match in _NUMBER_RE.findall(value):
            found.add(_norm_number(match))
    return found


def _norm_number(value: Any) -> str:
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.4f}".rstrip("0").rstrip(".")


# Нумерация пункта списка: только в начале строки и только как разметка списка. Разрешение по
# синтаксису, а не по величине числа — иначе «12 ошибок» проходило бы как «пункт 12» (M04).
_LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*(?:[-*•]\s*)?\d{1,2}[.)](?=\s)")


def strip_list_numbering(text: str) -> str:
    """Убирает номера пунктов списка, чтобы они не считались утверждением о величине."""
    return _LIST_MARKER_RE.sub("•", str(text or ""))


def unverified_numbers(answer: str, context: Dict[str, Any]) -> List[str]:
    """Числа ответа, которых нет в собранных фактах.

    Проценты и округления модель считает сама, поэтому допускаются числа, выводимые из фактов: доли
    и округления до целого. Всё остальное — повод не показывать такой ответ.

    Чего эта проверка **не** делает: она не подтверждает, что число отнесено к нужному показателю.
    Ответ «ошибок 50» при фактах «ошибок 0, запросов 50» она пропустит, потому что число 50 в данных
    есть. Проверка ловит выдуманные величины, а не перепутанные (M04).
    """
    known = numbers_in(context)
    derived = set()
    for value in list(known):
        try:
            number = float(value)
        except ValueError:
            continue
        derived.add(_norm_number(round(number)))               # округление до целого
        derived.add(_norm_number(round(number, 1)))
        if 0 <= number <= 1:
            derived.add(_norm_number(round(number * 100)))     # доля → проценты
        # Деление на сто не добавляется: из «1» оно делало «0», и выдуманный ноль проходил проверку (M04)
    allowed = known | derived
    text = strip_list_numbering(answer)
    return [n for n in (_norm_number(m) for m in _NUMBER_RE.findall(text)) if n not in allowed]


def facts_summary(context: Dict[str, Any]) -> str:
    """Человеческая сводка фактов без AI: сервис обязан отвечать и при выключенной модели."""
    lines = []
    for name, block in (context.get("facts") or {}).items():
        source = block.get("источник", name)
        period = block.get("period", "")
        reason = block.get("reason") or block.get("status")
        lines.append(f"• {source} ({period}): {reason}" if reason else f"• {source} ({period})")
    for name, error in (context.get("unavailable") or {}).items():
        lines.append(f"• {TOOLS.get(name, {}).get('title', name)}: данные недоступны ({error})")
    return "\n".join(lines) or "Данных для ответа нет."


def build_prompt(context: Dict[str, Any]) -> str:
    """Промпт: факты подаются как данные, а не как указания; требования к ответу заданы явно."""
    import ai_router
    import json
    facts = json.dumps(context.get("facts") or {}, ensure_ascii=False, indent=1, default=str)
    return (
        "Ты помогаешь администратору сервиса мониторинга цен разобраться в его состоянии.\n"
        "Отвечай по-русски, коротко и по делу. Правила, которые нельзя нарушать:\n"
        "1. Используй ТОЛЬКО числа из блока данных ниже. Не придумывай и не оценивай недостающие значения.\n"
        "2. Разделяй факты и предположения: сначала «Факты», затем «Возможная причина» со словом «вероятно».\n"
        "3. Если данных для вывода не хватает — так и напиши, какого именно наблюдения не хватает.\n"
        "4. У каждого числа указывай период или источник, как они даны в данных.\n"
        "5. Ничего не предлагай изменить в настройках магазинов без явного вопроса об этом.\n\n"
        f"Вопрос администратора: {context.get('question')}\n\n"
        + ai_router.untrusted_block(facts, "ДАННЫЕ")
    )


async def answer(question: str, days: int = DEFAULT_DAYS,
                 now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Ответ помощника: факты собирает код, объясняет модель, числа перепроверяются.

    Без AI сервис всё равно отвечает — фактами. Ответ модели с числом, которого нет в фактах, не
    показывается: лучше сухая сводка, чем уверенная выдумка.
    """
    import ai_router
    context = collect(question, days=days, now=now)
    summary = facts_summary(context)
    result = {"question": context["question"], "days": days, "facts": context["facts"],
              "unavailable": context["unavailable"], "summary": summary, "answer": None,
              "ai": None, "rejected": None,
              "note": "Цифры собраны кодом из отчётов мониторинга; модель только объясняет их словами. "
                      "Помощник работает только на чтение. Проверка отклоняет числа, которых нет в "
                      "данных, но не гарантирует, что число отнесено к нужному показателю — сверяйтесь "
                      "с фактами ниже."}
    try:
        routed = await ai_router.run("admin_assistant", build_prompt(context),
                                     validate=lambda v: v if isinstance(v, (dict, str)) else None)
    except ai_router.AIUnavailable as e:
        result["ai"] = f"AI недоступен: {e}"
        return result

    value = routed["result"]
    text = value if isinstance(value, str) else str(value.get("answer") or value.get("text") or "")
    text = text.strip()[:MAX_ANSWER_CHARS]
    if not text:
        result["ai"] = "модель не дала ответа"
        return result

    invented = unverified_numbers(text, context["facts"])
    if invented:
        result["rejected"] = f"в ответе есть числа, которых нет в данных: {', '.join(invented[:5])}"
        result["ai"] = routed.get("provider")
        return result
    result["answer"] = text
    result["ai"] = routed.get("provider")
    return result
