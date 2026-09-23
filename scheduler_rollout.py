"""Контролируемое включение адаптивного планировщика (этап P11).

Теневой планировщик (P10) только предлагал. Здесь ему позволяют влиять на настоящие обходы — но осторожно,
шагами, и с правом мгновенно вернуться к прежнему порядку.

Правила, которые здесь закреплены:
- **по умолчанию выключено**; переключатель возвращает прежнюю стратегию целиком, без остатка;
- **расширение только вручную владельцем**: 1 дружелюбный магазин → 3–5 → остальные разрешённые. Сервис
  показывает цифры «до и после» и говорит, готов ли следующий шаг, но сам его не делает (решение владельца
  2026-09-23);
- **агрессивность не растёт**: адаптивный порядок может только переставить и сократить то, что обошла бы
  прежняя стратегия. Добавить магазин, которого не было в плане, он не может — это проверяется в коде и
  тестом. Адреса, заголовки и паузы адаптивный порядок не трогает вовсе;
- **откат автоматический** по порогам, согласованным с владельцем до включения (ниже). Автоматически
  расширять — нельзя, автоматически откатываться — нужно.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Шаги включения
OFF = "off"
CANARY_ONE = "canary_one"
CANARY_FEW = "canary_few"
ALL = "all"
STAGES = (OFF, CANARY_ONE, CANARY_FEW, ALL)

STAGE_LABELS = {
    OFF: "выключено — обходы идут прежним порядком",
    CANARY_ONE: "один магазин на пробу",
    CANARY_FEW: "несколько магазинов (до 5)",
    ALL: "все разрешённые магазины",
}
NEXT_STAGE = {OFF: CANARY_ONE, CANARY_ONE: CANARY_FEW, CANARY_FEW: ALL, ALL: None}
CANARY_FEW_LIMIT = 5

SETTING_STAGE = "adaptive_scheduler_stage"
METADATA_BASELINE = "adaptive_scheduler_baseline"      # снимок метрик на момент включения шага
METADATA_STARTED = "adaptive_scheduler_started_at"
METADATA_LAST_ROLLBACK = "adaptive_scheduler_last_rollback"
METADATA_CANARY = "adaptive_scheduler_canary"          # состав шага фиксируется при включении (M01)

# --- Пороги остановки и отката. СОГЛАСОВАНЫ С ВЛАДЕЛЬЦЕМ ДО ВКЛЮЧЕНИЯ (2026-09-23), вариант «строго»:
# лучше лишний откат, чем испорченные отношения с магазином.
ERROR_GROWTH_LIMIT = 1.33        # доля ошибок выросла больше чем на треть
BLOCK_GROWTH_LIMIT = 1.33        # доля отказов по лимиту (429/403) выросла больше чем на треть
COMPLETENESS_DROP_LIMIT = 0.10   # полнота обходов упала больше чем на 10 процентных пунктов
MIN_REQUESTS_TO_JUDGE = 50       # меньше наблюдений — судить рано, ни откат, ни «готов к шагу»
MIN_SCANS_TO_JUDGE = 5
OBSERVE_HOURS = 24               # сколько наблюдать шаг, прежде чем считать его подтверждённым


def stage_of(settings: Optional[Dict[str, Any]] = None) -> str:
    """Текущий шаг включения. Неизвестное значение читается как «выключено»."""
    import config
    settings = settings if settings is not None else config.load_settings()
    value = str(settings.get(SETTING_STAGE) or OFF).strip()
    return value if value in STAGES else OFF


def can_switch(current: str, target: str) -> Tuple[bool, str]:
    """Разрешён ли переход. Вперёд — строго по одному шагу, назад и в «выключено» — всегда."""
    if target not in STAGES:
        return False, f"неизвестный шаг: {target}"
    if target == current:
        return True, ""
    if target == OFF:
        return True, ""
    if STAGES.index(target) < STAGES.index(current):
        return True, ""
    if NEXT_STAGE.get(current) == target:
        return True, ""
    return False, (f"нельзя перепрыгнуть с «{STAGE_LABELS[current]}» на «{STAGE_LABELS[target]}»: "
                   f"следующий шаг — «{STAGE_LABELS[NEXT_STAGE[current]]}»")


def canary_shops(stage: str, candidates: Sequence[Dict[str, Any]]) -> List[str]:
    """Магазины, на которые распространяется новый порядок.

    Первым берётся самый дружелюбный источник (быстрый, лёгкий, без ошибок) — на нём ошибка включения
    стоит меньше всего. Порядок детерминирован, чтобы шаг был воспроизводим.
    """
    import scheduler_shadow as sched
    if stage == OFF or not candidates:
        return []
    rank = {sched.FRIENDLY: 0, sched.NORMAL: 1, sched.EXPENSIVE: 2, sched.DEGRADED: 3}
    shops: List[str] = []
    for item in sorted(candidates, key=lambda c: (rank.get(c.get("profile"), 9), str(c.get("shop") or ""))):
        shop = str(item.get("shop") or "")
        if shop and shop not in shops:
            shops.append(shop)
    if stage == CANARY_ONE:
        return shops[:1]
    if stage == CANARY_FEW:
        return shops[:CANARY_FEW_LIMIT]
    return shops


def select_targets(stage: str, baseline_targets: Sequence[str], candidates: Sequence[Dict[str, Any]],
                   now: Optional[datetime.datetime] = None,
                   max_sources: int = 20,
                   members: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Что обходить сейчас: прежний список для обычных магазинов, адаптивный порядок — для пробных.

    Возвращает {"targets", "changed", "canary", "explanation"}. `targets` — всегда подмножество
    `baseline_targets`: новый порядок не может добавить обход, которого не было бы и так.
    """
    import scheduler_shadow as sched
    baseline = [str(s) for s in baseline_targets]
    if stage == OFF or not baseline:
        return {"targets": baseline, "changed": False, "canary": [],
                "explanation": "прежний порядок обходов"}

    # Состав шага берётся зафиксированным при включении, а не выбирается заново каждый раз (M01)
    chosen = list(members) if members is not None else canary_members()
    if not chosen:
        chosen = canary_shops(stage, candidates)
    canary = [s for s in chosen if s in baseline]
    if not canary:
        return {"targets": baseline, "changed": False, "canary": [],
                "explanation": "пробных магазинов в этой волне нет"}

    # Адаптивный порядок применяется только к пробным магазинам и только в пределах их же списка
    canary_candidates = [c for c in candidates if str(c.get("shop") or "") in canary]
    suggested = sched.plan(canary_candidates, now=now, max_sources=max_sources)["plan"]
    ordered = []
    for item in suggested:
        shop = str(item.get("shop") or "")
        if shop in canary and shop not in ordered:
            ordered.append(shop)

    # Пробный магазин, которому по правилам рано (пауза, минимальный интервал), в этой волне пропускается
    skipped = [s for s in canary if s not in ordered]
    others = [s for s in baseline if s not in canary]
    targets = ordered + others
    assert set(targets) <= set(baseline), "адаптивный порядок не может добавить обход"
    explanation = f"новый порядок для {len(ordered)} из {len(canary)} пробных магазинов"
    if skipped:
        explanation += f"; пропущены до своего срока: {', '.join(skipped)}"
    return {"targets": targets, "changed": True, "canary": canary, "skipped": skipped,
            "explanation": explanation}


def before_window(moment: datetime.datetime) -> Tuple[datetime.datetime, datetime.datetime]:
    """Период «до»: сутки, закончившиеся в момент включения шага."""
    return moment - datetime.timedelta(days=1), moment


def metrics(shop_keys: Sequence[str], since: datetime.datetime,
            until: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Метрики пробных магазинов за ТОЧНЫЙ период: доля ошибок, отказов по лимиту, полнота обходов.

    Период задаётся границами, а не «последними сутками»: иначе в результат шага попадали бы прежние
    успехи, а провал canary прятался бы за историей до включения (M02). По этой же причине порог
    достаточности данных считается только по наблюдениям внутри периода.
    """
    import database
    end = until or datetime.datetime.now(datetime.timezone.utc)
    keys = [str(k) for k in shop_keys]
    http = database.http_metrics_by_shop_window(since, end)

    requests = errors = blocked = 0
    for key in keys:
        row = http.get(key) or {}
        requests += int(row.get("requests") or 0)
        errors += int(row.get("errors") or 0)
        blocked += int(row.get("blocked") or 0)

    scans = complete = 0
    if keys:
        placeholders = ",".join("?" * len(keys))
        with database.get_connection() as conn:
            for row in conn.execute(
                    f"SELECT quality, COUNT(*) AS n FROM source_scans "
                    f"WHERE shop_key IN ({placeholders}) AND finished_at >= ? AND finished_at < ? "
                    f"GROUP BY quality",
                    keys + [since.isoformat(), end.isoformat()]):
                scans += row["n"]
                if row["quality"] in ("ok", "complete"):
                    complete += row["n"]

    return {
        "shops": keys,
        "since": since.isoformat(), "until": end.isoformat(),
        "hours": round((end - since).total_seconds() / 3600.0, 2),
        "requests": requests, "errors": errors, "blocked": blocked, "scans": scans,
        "error_share": round(errors / requests, 4) if requests else None,
        "block_share": round(blocked / requests, 4) if requests else None,
        "completeness": round(complete / scans, 4) if scans else None,
        "enough_data": requests >= MIN_REQUESTS_TO_JUDGE and scans >= MIN_SCANS_TO_JUDGE,
    }


def should_rollback(before: Optional[Dict[str, Any]],
                    after: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Пора ли вернуть прежний порядок. Пороги согласованы с владельцем до включения.

    Мало наблюдений — не повод ни откатывать, ни объявлять шаг удачным: об этом говорится прямо.
    """
    if not after or not after.get("enough_data"):
        return False, "наблюдений пока мало, чтобы судить"
    if not before:
        return False, "не с чем сравнивать: снимок «до» не сохранён"

    def grew(field: str, limit: float) -> Optional[str]:
        old, new = before.get(field), after.get(field)
        if old is None or new is None:
            return None
        if old == 0:
            return (f"{field}: было 0, стало {new:.1%}") if new > 0.01 else None
        if new > old * limit:
            return f"{field}: было {old:.1%}, стало {new:.1%}"
        return None

    grown_errors = grew("error_share", ERROR_GROWTH_LIMIT)
    if grown_errors:
        return True, f"выросла доля ошибок ({grown_errors})"
    grown_blocks = grew("block_share", BLOCK_GROWTH_LIMIT)
    if grown_blocks:
        return True, f"выросли отказы по лимиту ({grown_blocks})"

    old_complete, new_complete = before.get("completeness"), after.get("completeness")
    if old_complete is not None and new_complete is not None:
        if old_complete - new_complete > COMPLETENESS_DROP_LIMIT:
            return True, (f"упала полнота обходов: было {old_complete:.0%}, стало {new_complete:.0%}")
    return False, ""


def ready_for_next_step(stage: str, before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]],
                        started_at: Optional[datetime.datetime],
                        now: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
    """Готов ли следующий шаг. Решение всё равно за владельцем — здесь только основание."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    if stage == OFF:
        return False, "включение не начиналось"
    if NEXT_STAGE.get(stage) is None:
        return False, "это последний шаг"
    if not after or not after.get("enough_data"):
        return False, "наблюдений пока мало"
    rollback, why = should_rollback(before, after)
    if rollback:
        return False, f"признаки ухудшения: {why}"
    if started_at is None:
        return False, "неизвестно, когда шаг начался"
    hours = (moment - started_at).total_seconds() / 3600.0
    if hours < OBSERVE_HOURS:
        return False, f"шаг идёт {hours:.0f} ч из {OBSERVE_HOURS} ч наблюдения"
    return True, f"шаг наблюдается {hours:.0f} ч, ухудшений нет"


# --- Состояние включения: снимок «до», время начала шага и автоматический откат ------------------

def _json_metadata(key: str) -> Optional[Dict[str, Any]]:
    import json
    import database
    raw = database.get_metadata(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def start_stage(stage: str, candidates: Sequence[Dict[str, Any]],
                now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Начало шага: выбирает состав, фиксирует его, снимает метрики «до» и запоминает время.

    Состав выбирается ровно здесь и больше не пересматривается (M01): иначе ухудшившийся пробный
    магазин молча заменялся бы другим, а сравнение «до/после» относилось бы к разным источникам.
    """
    import json
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    shops = canary_shops(stage, candidates)
    since, until = before_window(moment)
    before = metrics(shops, since, until) if shops else None
    database.set_metadata(METADATA_CANARY, json.dumps(shops, ensure_ascii=False))
    database.set_metadata(METADATA_BASELINE, json.dumps(before, ensure_ascii=False) if before else "")
    database.set_metadata(METADATA_STARTED, moment.isoformat())
    return {"stage": stage, "canary": shops, "before": before, "started_at": moment.isoformat()}


def canary_members() -> List[str]:
    """Состав текущего шага, зафиксированный при включении. Пустой список — состав не сохранён."""
    stored = _json_metadata(METADATA_CANARY)
    return [str(s) for s in stored] if isinstance(stored, list) else []


def missing_members(candidates: Sequence[Dict[str, Any]]) -> List[str]:
    """Участники шага, которых больше нет среди кандидатов: замены им не подбирается (M01)."""
    known = {str(c.get("shop") or "") for c in candidates}
    return [s for s in canary_members() if s not in known]


def started_at(now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    import database
    raw = database.get_metadata(METADATA_STARTED)
    if not raw:
        return None
    try:
        moment = datetime.datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def baseline_metrics() -> Optional[Dict[str, Any]]:
    return _json_metadata(METADATA_BASELINE)


def rollback(reason: str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Возвращает прежний порядок обходов целиком и записывает, почему."""
    import json
    import config
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    previous = stage_of()
    settings = dict(config.load_settings())
    settings[SETTING_STAGE] = OFF
    config.save_settings(settings)
    record = {"at": moment.isoformat(), "from_stage": previous, "reason": reason,
              "canary": canary_members()}
    database.set_metadata(METADATA_LAST_ROLLBACK, json.dumps(record, ensure_ascii=False))
    database.set_metadata(METADATA_CANARY, "")      # шага больше нет — нет и его состава
    try:
        from telemetry import telemetry, SEVERITY_WARNING, COMPONENT_SCHEDULER
        telemetry.record_event("scheduler_rollback", SEVERITY_WARNING, COMPONENT_SCHEDULER,
                               f"Адаптивный планировщик выключен: {reason}",
                               data={"from_stage": previous, "reason": reason})
    except Exception:
        pass
    return record


def last_rollback() -> Optional[Dict[str, Any]]:
    return _json_metadata(METADATA_LAST_ROLLBACK)


def check_and_rollback(candidates: Sequence[Dict[str, Any]],
                       now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Проверяет признаки ухудшения и при необходимости возвращает прежний порядок.

    Вызывается после циклов обхода. Расширять шаг автоматически нельзя — это решение владельца; а вот
    откатываться нужно самому и быстро.
    """
    stage = stage_of()
    if stage == OFF:
        return None
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    shops = canary_members() or canary_shops(stage, candidates)
    began = started_at(moment)
    if not shops or began is None:
        return None
    # Считаем только то, что произошло ПОСЛЕ включения шага, и только по его участникам (M01, M02)
    after = metrics(shops, began, moment)
    needed, reason = should_rollback(baseline_metrics(), after)
    if not needed:
        return None
    record = rollback(reason, now=now)
    record["after"] = after
    return record


def status(candidates: Optional[Sequence[Dict[str, Any]]] = None,
           now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Состояние включения для отчёта: шаг, пробные магазины, цифры «до и после», готовность к шагу."""
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    if candidates is None:
        candidates = database.scheduler_candidates(days=7, now=moment)
    stage = stage_of()
    began = started_at(moment)
    shops = canary_members() if stage != OFF else []
    if stage != OFF and not shops:
        shops = canary_shops(stage, candidates)      # состав ещё не фиксировался (старое состояние)
    before = baseline_metrics()
    after = metrics(shops, began, moment) if shops and began else None
    ready, why = ready_for_next_step(stage, before, after, began, moment)
    needed, rollback_reason = should_rollback(before, after)
    # Шаг включён, а пробных магазинов нет — это состояние надо назвать, а не показывать пустым прочерком
    if stage != OFF and not shops:
        why = "пробные магазины ещё не определены: нет данных об обходах"
        ready = False
    # Участник шага пропал из кандидатов: замену ему не подбираем и молчать об этом нельзя (M01)
    gone = missing_members(candidates) if stage != OFF else []
    if gone:
        ready = False
        why = f"участники шага пропали из наблюдения: {', '.join(gone)} — замена не подбирается"
    return {
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "next_stage": NEXT_STAGE.get(stage),
        "canary": shops,
        "missing_members": gone,
        "before": before,
        "after": after,
        "ready_for_next": ready,
        "ready_reason": why,
        "rollback_needed": needed,
        "rollback_reason": rollback_reason,
        "started_at": started_at(moment).isoformat() if started_at(moment) else None,
        "last_rollback": last_rollback(),
        "thresholds": {
            "error_growth": ERROR_GROWTH_LIMIT, "block_growth": BLOCK_GROWTH_LIMIT,
            "completeness_drop": COMPLETENESS_DROP_LIMIT, "observe_hours": OBSERVE_HOURS,
            "agreed_at": "2026-09-23, до включения, решение владельца «строго»",
        },
        "note": "Расширение шага — только вручную. Откат происходит автоматически при росте ошибок или "
                "отказов по лимиту больше чем на треть либо падении полноты обходов больше чем на 10 %. "
                "Состав шага фиксируется при включении: цифры «после» считаются по тем же магазинам и "
                "только за время после включения.",
    }
