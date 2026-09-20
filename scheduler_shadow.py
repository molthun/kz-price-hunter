"""Теневой планировщик обходов (этап P10): что стоило бы обойти раньше — и почему.

Этот модуль **ничего не делает**: он только считает предложения по уже собранным данным (обходы, ошибки,
свежесть цен, спрос из поиска, наблюдения людей) и объясняет каждое словами. Никаких запросов к магазинам,
никаких записей в каталог, алерты или очередь уведомлений — предложения нужны, чтобы сравнить стратегии и
убедиться в их безопасности до того, как планировщику позволят решать самому (это уже следующий этап).

Из чего складывается приоритет источника (магазин + категория):
- **спрос**: сколько людей искали это за последние дни; неудовлетворённый спрос (искали и не нашли) весит
  больше — обновление такого источника может закрыть реальную потребность;
- **наблюдения**: сколько людей ждут изменения цены именно здесь;
- **возраст данных**: сколько часов прошло с последнего удачного обхода, с оглядкой на свежесть цен (P02);
- **изменчивость цен**: где цены меняются часто, там устаревание дороже;
- **качество последнего обхода**: неполный или degraded обход стоит повторить раньше;
- **стоимость и вежливость**: дорогие и хрупкие источники отодвигаются, а не разгоняются.

Ограничения (пауза после ошибок, минимальный интервал, дневной лимит запросов) проверяются на этапе
составления плана: предложение, нарушающее ограничение, не попадает в план, а причина остаётся видимой.
"""
from __future__ import annotations

import datetime
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Профили источника по его собственным метрикам
FRIENDLY = "friendly"     # отвечает быстро и дёшево, можно обходить чаще
NORMAL = "normal"
EXPENSIVE = "expensive"   # медленный или тяжёлый: обход стоит дорого
DEGRADED = "degraded"     # ошибки, 429/403 — источник надо щадить
PROFILES = (FRIENDLY, NORMAL, EXPENSIVE, DEGRADED)

PROFILE_LABELS = {
    FRIENDLY: "отвечает быстро и дёшево",
    NORMAL: "обычный источник",
    EXPENSIVE: "медленный или тяжёлый обход",
    DEGRADED: "источник отвечает ошибками — щадим",
}

# Границы профилей. Это отправные значения, а не истина: они вынесены сюда, чтобы их можно было
# обсуждать и менять по замерам, а не искать по коду.
ERROR_SHARE_DEGRADED = 0.2        # доля неуспешных ответов, после которой источник считается проблемным
BLOCK_SHARE_DEGRADED = 0.05       # доля 429/403 — признак того, что нас просят притормозить
SLOW_P95_MS = 4000.0              # медленный ответ
HEAVY_BYTES_PER_REQUEST = 2_000_000
FAST_P95_MS = 1200.0
LIGHT_BYTES_PER_REQUEST = 300_000

# Множители приоритета по профилю: дорогие и проблемные источники отодвигаются
PROFILE_WEIGHT = {FRIENDLY: 1.15, NORMAL: 1.0, EXPENSIVE: 0.75, DEGRADED: 0.35}

# Минимальные паузы между обходами одного источника по профилю, часы
MIN_INTERVAL_HOURS = {FRIENDLY: 3.0, NORMAL: 6.0, EXPENSIVE: 12.0, DEGRADED: 24.0}

# Вес сигналов в оценке приоритета
WEIGHT_DEMAND = 1.0
WEIGHT_UNMET_DEMAND = 2.0         # искали и не нашли — это дороже обычного спроса
WEIGHT_WATCHES = 1.5              # человек ждёт изменения цены именно здесь
WEIGHT_AGE = 1.0
WEIGHT_VOLATILITY = 0.8
WEIGHT_QUALITY = 1.2              # прошлый обход был неполным

STARVATION_HOURS = 72.0           # источник, не обойдённый столько времени, поднимается принудительно


def source_profile(metrics: Dict[str, Any]) -> Tuple[str, str]:
    """Профиль источника и объяснение словами.

    metrics: requests, errors, blocked (429/403), latency_p95_ms, bytes_total.
    Нет наблюдений — профиль NORMAL: молчание не повод считать источник ни хорошим, ни плохим.
    """
    requests = int(metrics.get("requests") or 0)
    if not requests:
        return NORMAL, "нет наблюдений за источником"

    errors = int(metrics.get("errors") or 0)
    blocked = int(metrics.get("blocked") or 0)
    p95 = float(metrics.get("latency_p95_ms") or 0.0)
    per_request = float(metrics.get("bytes_total") or 0.0) / requests

    error_share = errors / requests
    block_share = blocked / requests
    if error_share > ERROR_SHARE_DEGRADED:
        return DEGRADED, f"ошибок {errors} из {requests} ({error_share:.0%})"
    if block_share > BLOCK_SHARE_DEGRADED:
        return DEGRADED, f"отказов по лимиту {blocked} из {requests} — источник просит притормозить"
    if p95 > SLOW_P95_MS or per_request > HEAVY_BYTES_PER_REQUEST:
        return EXPENSIVE, (f"задержка p95 {p95:.0f} мс" if p95 > SLOW_P95_MS
                           else f"{per_request / 1_000_000:.1f} МБ на запрос")
    if p95 and p95 < FAST_P95_MS and per_request < LIGHT_BYTES_PER_REQUEST:
        return FRIENDLY, f"задержка p95 {p95:.0f} мс, лёгкие ответы"
    return NORMAL, "обычные задержка и объём"


def _age_score(hours: Optional[float], target_hours: float) -> float:
    """Насколько данные устарели относительно желаемого интервала. Никогда не обходили — максимум."""
    if hours is None:
        return 2.0
    if target_hours <= 0:
        return 0.0
    return min(3.0, hours / target_hours)


def score(signals: Dict[str, Any], profile: str = NORMAL,
          target_hours: Optional[float] = None) -> Dict[str, Any]:
    """Оценка приоритета источника с разбором вклада каждого сигнала.

    Возвращает {"score", "parts", "reason"} — разбор нужен, чтобы предложение можно было объяснить
    человеку и проверить в тесте, а не принимать на веру.
    """
    target = target_hours if target_hours is not None else MIN_INTERVAL_HOURS.get(profile, 6.0)
    demand = float(signals.get("searches") or 0)
    unmet = float(signals.get("unmet_searches") or 0)
    watches = float(signals.get("watches") or 0)
    volatility = float(signals.get("price_changes_per_day") or 0)
    incomplete = 1.0 if signals.get("last_quality") in ("degraded", "partial", "failed") else 0.0
    age_hours = signals.get("age_hours")

    parts = {
        "спрос": WEIGHT_DEMAND * math.log1p(demand),
        "не нашли": WEIGHT_UNMET_DEMAND * math.log1p(unmet),
        "наблюдения": WEIGHT_WATCHES * math.log1p(watches),
        "возраст данных": WEIGHT_AGE * _age_score(age_hours, target),
        "изменчивость цен": WEIGHT_VOLATILITY * math.log1p(volatility),
        "прошлый обход неполный": WEIGHT_QUALITY * incomplete,
    }
    raw = sum(parts.values()) * PROFILE_WEIGHT.get(profile, 1.0)

    # Источник, забытый надолго, поднимается независимо от спроса: иначе он не обновится никогда
    starving = age_hours is not None and age_hours >= STARVATION_HOURS
    if starving or age_hours is None:
        raw += 2.0
        parts["давно не обходили"] = 2.0

    top = sorted((v, k) for k, v in parts.items() if v > 0)
    reason = ", ".join(f"{name}" for _, name in reversed(top[-2:])) or "нет заметных сигналов"
    return {"score": round(raw, 3), "parts": {k: round(v, 3) for k, v in parts.items()},
            "reason": reason, "profile": profile, "target_hours": target, "starving": starving}


def allowed(candidate: Dict[str, Any], now: datetime.datetime) -> Tuple[bool, str]:
    """Можно ли предлагать обход сейчас: пауза после ошибок и минимальный интервал профиля."""
    retry_at = candidate.get("next_retry_at")
    if retry_at:
        retry_moment = retry_at if isinstance(retry_at, datetime.datetime) else None
        if retry_moment and retry_moment > now:
            return False, "источник на паузе после ошибок"
    age_hours = candidate.get("age_hours")
    target = candidate.get("target_hours") or MIN_INTERVAL_HOURS.get(candidate.get("profile", NORMAL), 6.0)
    if age_hours is not None and age_hours < target:
        return False, f"обойдён {age_hours:.1f} ч назад, минимальный интервал {target:.0f} ч"
    return True, ""


def plan(candidates: Iterable[Dict[str, Any]], now: Optional[datetime.datetime] = None,
         max_sources: int = 20, per_shop_limit: int = 3) -> Dict[str, Any]:
    """Составляет предложение расписания: что обойти в первую очередь и почему.

    Ограничения соблюдаются здесь же: паузы, минимальные интервалы, не больше нескольких категорий
    одного магазина за цикл и общий предел. Отклонённые кандидаты остаются видимыми с причиной —
    молчаливый пропуск невозможно проверить.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    suggestions, skipped = [], []
    for candidate in candidates:
        profile = candidate.get("profile") or NORMAL
        assessment = score(candidate, profile, candidate.get("target_hours"))
        item = {**candidate, **assessment}
        ok, why = allowed(item, moment)
        if not ok:
            skipped.append({**item, "skip_reason": why})
            continue
        suggestions.append(item)

    # Порядок: сначала самые нужные; при равенстве — те, что дольше не обходились
    suggestions.sort(key=lambda s: (-s["score"], -(s.get("age_hours") or 1e9)))

    per_shop: Dict[str, int] = {}
    chosen = []
    for item in suggestions:
        shop = str(item.get("shop") or "")
        if per_shop.get(shop, 0) >= per_shop_limit:
            skipped.append({**item, "skip_reason": f"уже выбрано {per_shop_limit} категорий магазина"})
            continue
        if len(chosen) >= max_sources:
            skipped.append({**item, "skip_reason": "план на цикл уже заполнен"})
            continue
        per_shop[shop] = per_shop.get(shop, 0) + 1
        chosen.append(item)

    return {
        "generated_at": moment.isoformat(),
        "plan": chosen,
        "skipped": skipped,
        "limits": {"max_sources": max_sources, "per_shop_limit": per_shop_limit},
        "note": "Это предложение, а не действие: теневой планировщик ничего не обходит и ничего не меняет.",
    }


# --- Сравнение стратегий на записанных данных ------------------------------------------------
# Симуляция нужна, чтобы сравнивать стратегии честно: одни и те же входные данные, один и тот же счёт.

def strategy_round_robin(candidates: List[Dict[str, Any]], now: datetime.datetime,
                         limit: int) -> List[Dict[str, Any]]:
    """Как сейчас: по очереди, кто дольше всех не обходился."""
    ordered = sorted(candidates, key=lambda c: -(c.get("age_hours") or 1e9))
    return ordered[:limit]


def strategy_adaptive(candidates: List[Dict[str, Any]], now: datetime.datetime,
                      limit: int) -> List[Dict[str, Any]]:
    """Предлагаемая: по приоритету со всеми сигналами и ограничениями."""
    return plan(candidates, now=now, max_sources=limit)["plan"]


STRATEGIES = {"round_robin": strategy_round_robin, "adaptive": strategy_adaptive}


def simulate(candidates: List[Dict[str, Any]], cycles: int = 5, limit: int = 10,
             cycle_hours: float = 6.0, strategy: str = "adaptive",
             now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Прогоняет несколько циклов на записанных данных и считает, чем один порядок лучше другого.

    Метрики: средний возраст данных к концу, сколько неудовлетворённого спроса и наблюдений охвачено,
    сколько источников не обошли ни разу (starvation) и не нарушены ли ограничения.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    state = [dict(c) for c in candidates]
    covered_unmet = covered_watches = 0
    visits: Dict[str, int] = {}
    violations: List[str] = []
    pick = STRATEGIES[strategy]

    for cycle in range(cycles):
        cycle_now = moment + datetime.timedelta(hours=cycle_hours * cycle)
        chosen = pick(state, cycle_now, limit)
        for item in chosen:
            key = f"{item.get('shop')}/{item.get('category')}"
            target = item.get("target_hours") or MIN_INTERVAL_HOURS.get(item.get("profile", NORMAL), 6.0)
            age = item.get("age_hours")
            if age is not None and age < target:
                violations.append(f"{key}: обойдён раньше минимального интервала")
            visits[key] = visits.get(key, 0) + 1
            covered_unmet += int(item.get("unmet_searches") or 0)
            covered_watches += int(item.get("watches") or 0)
        chosen_keys = {f"{i.get('shop')}/{i.get('category')}" for i in chosen}
        for item in state:
            key = f"{item.get('shop')}/{item.get('category')}"
            item["age_hours"] = 0.0 if key in chosen_keys else (item.get("age_hours") or 0.0) + cycle_hours

    ages = [float(c.get("age_hours") or 0.0) for c in state]
    never = [f"{c.get('shop')}/{c.get('category')}" for c in state
             if not visits.get(f"{c.get('shop')}/{c.get('category')}")]
    return {
        "strategy": strategy,
        "cycles": cycles,
        "avg_age_hours": round(sum(ages) / len(ages), 2) if ages else 0.0,
        "max_age_hours": round(max(ages), 2) if ages else 0.0,
        "covered_unmet_searches": covered_unmet,
        "covered_watches": covered_watches,
        "visits": visits,
        "never_visited": never,
        "violations": violations,
    }


def compare(candidates: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
    """Сравнение стратегий на одних и тех же данных — воспроизводимо и без побочных действий."""
    results = {name: simulate(candidates, strategy=name, **kwargs) for name in STRATEGIES}
    adaptive, baseline = results["adaptive"], results["round_robin"]
    return {
        "results": results,
        "verdict": {
            "unmet_demand_covered": adaptive["covered_unmet_searches"] - baseline["covered_unmet_searches"],
            "watches_covered": adaptive["covered_watches"] - baseline["covered_watches"],
            "avg_age_hours": round(adaptive["avg_age_hours"] - baseline["avg_age_hours"], 2),
            "violations": len(adaptive["violations"]),
        },
        "note": "Симуляция сравнивает решения на записанных данных. Она не доказывает ускорение реальных "
                "обходов — это проверяется только при настоящем включении.",
    }
