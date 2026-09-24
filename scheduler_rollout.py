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
import threading
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

# Переходы шага выполняются по одному: параллельные запросы администратора не должны смешиваться (M03)
_SWITCH_LOCK = threading.Lock()

SETTING_STAGE = "adaptive_scheduler_stage"
METADATA_BASELINE = "adaptive_scheduler_baseline"      # снимок метрик на момент включения шага
METADATA_STARTED = "adaptive_scheduler_started_at"
METADATA_LAST_ROLLBACK = "adaptive_scheduler_last_rollback"
METADATA_CANARY = "adaptive_scheduler_canary"          # состав шага фиксируется при включении (M01)
METADATA_HTTP_START = "adaptive_scheduler_http_start"  # счётчики HTTP на момент включения (M02)
METADATA_VERSION = "adaptive_scheduler_state_version"  # версия состояния: защита конкурентных переходов (M03)

# --- Пороги остановки и отката. СОГЛАСОВАНЫ С ВЛАДЕЛЬЦЕМ ДО ВКЛЮЧЕНИЯ (2026-09-23), вариант «строго»:
# лучше лишний откат, чем испорченные отношения с магазином.
ERROR_GROWTH_LIMIT = 1.33        # доля ошибок выросла больше чем на треть
BLOCK_GROWTH_LIMIT = 1.33        # доля отказов по лимиту (429/403) выросла больше чем на треть
COMPLETENESS_DROP_LIMIT = 0.10   # полнота обходов упала больше чем на 10 процентных пунктов
# Абсолютные потолки: сравнивать с «до» можно не всегда (у магазина могло не быть HTTP-наблюдений до
# включения), но откровенно плохой источник — повод откатиться независимо от базы сравнения.
# Значения те же, по которым теневой планировщик считает источник проблемным (P10).
ABSOLUTE_ERROR_SHARE = 0.20      # каждый пятый запрос неуспешен
ABSOLUTE_BLOCK_SHARE = 0.05      # магазин просит притормозить

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


class StageRefused(Exception):
    """Шаг нельзя начать, и причина называется словами, а не молча подменяется другим магазином."""


def shop_display_name(key: str) -> str:
    """Название магазина, под которым его видит телеметрия. Неизвестный ключ остаётся собой."""
    try:
        import config
        return config.SHOP_KEYS.get(str(key), str(key))
    except Exception:
        return str(key)


def allowed_shops() -> Optional[List[str]]:
    """Магазины, которые сервис обходит сейчас, или None, если это не удалось выяснить.

    Пустой список означает «не разрешён никто» (все магазины выключены), а None — «неизвестно».
    Ни то, ни другое не может читаться как «разрешены все»: неизвестность не даёт права включать
    эксперимент на чём попало (M01).
    """
    try:
        import web.server as server
        return list(server.enabled_shop_keys())
    except Exception as e:
        print(f"[Rollout] Список включённых магазинов недоступен: {type(e).__name__}")
        return None


def canary_shops(stage: str, candidates: Sequence[Dict[str, Any]],
                 allowed: Optional[Sequence[str]] = None) -> List[str]:
    """Магазины, на которые распространяется новый порядок.

    Берутся только включённые источники: кандидаты собираются из истории обходов и содержат в том числе
    выключенные магазины, а их сервис всё равно не обходит (M01).

    Первым берётся наименее рискованный источник: дружелюбный (быстрый, лёгкий, без ошибок), а если
    такого нет — обычный (NORMAL: обычные задержка и объём). Решение владельца 2026-09-24: требовать
    именно FRIENDLY оказалось недостижимо — у магазинов Казахстана страницы тяжелее 300 КБ, и таких
    источников в проде просто не бывает, отчего первый шаг не начинался вовсе.
    Медленные и тяжёлые (EXPENSIVE) и отвечающие ошибками (DEGRADED) первым шагом по-прежнему
    запрещены. Порядок детерминирован, чтобы шаг был воспроизводим.
    """
    import scheduler_shadow as sched
    if stage == OFF or not candidates:
        return []
    permitted_list = allowed if allowed is not None else allowed_shops()
    if permitted_list is None:
        raise StageRefused("не удалось выяснить, какие магазины включены: шаг не начинается")
    permitted = set(permitted_list)
    if not permitted:
        raise StageRefused("сейчас не включён ни один магазин: пробному шагу не на чем работать")
    rank = {sched.FRIENDLY: 0, sched.NORMAL: 1, sched.EXPENSIVE: 2, sched.DEGRADED: 3}
    ordered: List[Tuple[int, str]] = []
    for item in sorted(candidates, key=lambda c: (rank.get(c.get("profile"), 9), str(c.get("shop") or ""))):
        shop = str(item.get("shop") or "")
        if not shop or shop not in permitted:
            continue
        if shop not in [s for _, s in ordered]:
            ordered.append((rank.get(item.get("profile"), 9), shop))
    if not ordered:
        raise StageRefused("среди включённых магазинов нет источников с историей обходов")

    if stage == CANARY_ONE:
        profile, shop = ordered[0]
        if profile > rank[sched.NORMAL]:
            raise StageRefused(
                "первый шаг делается на дружелюбном или обычном источнике; сейчас все включённые "
                "магазины либо медленные и тяжёлые, либо отвечают ошибками — начинать на таком нельзя")
        return [shop]
    shops = [s for _, s in ordered]
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
        try:
            chosen = canary_shops(stage, candidates)
        except StageRefused:
            return {"targets": baseline, "changed": False, "canary": [],
                    "explanation": "состав пробного шага не определён"}
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
            until: Optional[datetime.datetime] = None,
            start_counters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Метрики пробных магазинов за ТОЧНЫЙ период: доля ошибок, отказов по лимиту, полнота обходов.

    Период задаётся границами, а не «последними сутками»: иначе в результат шага попадали бы прежние
    успехи, а провал canary прятался бы за историей до включения (M02). По этой же причине порог
    достаточности данных считается только по наблюдениям внутри периода.
    """
    import database
    end = until or datetime.datetime.now(datetime.timezone.utc)
    keys = [str(k) for k in shop_keys]
    http = database.http_metrics_by_shop_window(since, end, start_counters)
    coverage = http.pop("_coverage", {})

    requests = errors = blocked = 0
    for key in keys:
        # Обходы хранятся под ключом настроек («alser»), а HTTP-метрики — под названием магазина
        # («Alser»). Без сопоставления шаг не видел ни одного запроса, и откат был слеп (найдено
        # владельцем на первом же пробном шаге).
        row = http.get(key) or http.get(shop_display_name(key)) or {}
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
        # Какой период реально покрыт счётчиками HTTP: «delta» — неполный час учтён вычитанием,
        # «excluded» — час начала не засчитан (нет снимка счётчиков) (M02)
        "http_coverage": coverage,
        "hours": round((end - since).total_seconds() / 3600.0, 2),
        "requests": requests, "errors": errors, "blocked": blocked, "scans": scans,
        "complete_scans": complete,
        "need": {"requests": MIN_REQUESTS_TO_JUDGE, "scans": MIN_SCANS_TO_JUDGE},
        "error_share": round(errors / requests, 4) if requests else None,
        "block_share": round(blocked / requests, 4) if requests else None,
        "completeness": round(complete / scans, 4) if scans else None,
        "enough_data": requests >= MIN_REQUESTS_TO_JUDGE and scans >= MIN_SCANS_TO_JUDGE,
    }


def should_rollback(before: Optional[Dict[str, Any]],
                    after: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Пора ли вернуть прежний порядок. Пороги согласованы с владельцем до включения.

    Мало наблюдений — не повод ни откатывать, ни объявлять шаг удачным: об этом говорится прямо.
    Но «мало запросов» не должно закрывать глаза на провалившиеся обходы: полнота оценивается по
    своему минимуму отдельно от HTTP (M02).
    """
    if not after:
        return False, "наблюдений пока мало, чтобы судить"
    if not after.get("enough_data"):
        # Обходов уже достаточно, чтобы судить о полноте, даже если запросов записано мало
        scans_enough = int(after.get("scans") or 0) >= MIN_SCANS_TO_JUDGE
        old_complete, new_complete = (before or {}).get("completeness"), after.get("completeness")
        if scans_enough and old_complete is not None and new_complete is not None \
                and old_complete - new_complete > COMPLETENESS_DROP_LIMIT:
            return True, (f"упала полнота обходов: было {old_complete:.0%}, стало {new_complete:.0%}")
        return False, "наблюдений пока мало, чтобы судить"
    # Потолки проверяются до сравнения с «до»: отсутствие базы сравнения не должно оправдывать провал
    error_share = after.get("error_share")
    if error_share is not None and error_share > ABSOLUTE_ERROR_SHARE:
        return True, (f"доля ошибок {error_share:.0%} — выше допустимой {ABSOLUTE_ERROR_SHARE:.0%} "
                      f"независимо от того, что было до включения")
    block_share = after.get("block_share")
    if block_share is not None and block_share > ABSOLUTE_BLOCK_SHARE:
        return True, (f"отказы по лимиту {block_share:.0%} — выше допустимых "
                      f"{ABSOLUTE_BLOCK_SHARE:.0%}: магазин просит притормозить")

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


def state_snapshot() -> Dict[str, str]:
    """Текущее состояние включения целиком — чтобы вернуть его при неудачной активации (M03)."""
    import database
    return {key: (database.get_metadata(key) or "")
            for key in (METADATA_CANARY, METADATA_HTTP_START, METADATA_BASELINE, METADATA_STARTED)}


def state_version() -> Optional[str]:
    import database
    return database.get_metadata(METADATA_VERSION) or None


def prepare_stage(stage: str, candidates: Sequence[Dict[str, Any]],
                  now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Считает всё нужное для шага, НИЧЕГО не записывая: состав, снимок «до», счётчики, время."""
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    shops = canary_shops(stage, candidates)          # может отказать словами (StageRefused)
    since, until = before_window(moment)
    before = metrics(shops, since, until) if shops else None
    # Счётчики часа, в котором шаг начался: без них первый неполный час выпал бы из наблюдения (M02)
    http_start = database.http_counters_for_hour(moment)
    return {"stage": stage, "canary": shops, "before": before, "http_start": http_start,
            "started_at": moment.isoformat()}


def _state_pairs(prepared: Dict[str, Any]) -> Dict[str, str]:
    import json
    return {
        METADATA_CANARY: json.dumps(prepared["canary"], ensure_ascii=False),
        METADATA_HTTP_START: json.dumps(prepared["http_start"], ensure_ascii=False),
        METADATA_BASELINE: json.dumps(prepared["before"], ensure_ascii=False) if prepared["before"] else "",
        METADATA_STARTED: prepared["started_at"],
    }


def start_stage(stage: str, candidates: Sequence[Dict[str, Any]],
                now: Optional[datetime.datetime] = None,
                expected_version: Optional[str] = ...) -> Dict[str, Any]:
    """Начало шага: выбирает состав, фиксирует его, снимает метрики «до» и запоминает время.

    Состав выбирается ровно здесь и больше не пересматривается (M01). Всё состояние пишется одной
    транзакцией и только если версия состояния не изменилась с момента подготовки: параллельный переход
    не может быть затёрт чужим отказом (M03).
    """
    import database
    prepared = prepare_stage(stage, candidates, now=now)
    expected = state_version() if expected_version is ... else expected_version
    version = database.compare_and_set_metadata(METADATA_VERSION, expected, _state_pairs(prepared))
    if version is None:
        raise StageRefused("состояние включения изменилось параллельно: повторите переход")
    return {"stage": stage, "canary": prepared["canary"], "before": prepared["before"],
            "started_at": prepared["started_at"], "version": version}


def switch_stage(target: str, candidates: Sequence[Dict[str, Any]],
                 now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Единственный путь смены шага: проверка перехода → подготовка → активация.

    Переходы сериализованы: внутри процесса — замком, между процессами — условной записью состояния по
    версии. Настройка меняется ПОСЛЕ записи состояния; если активация не удалась, откатывается только
    собственная подготовка, и только если её никто не успел сменить (M03).
    """
    import config
    import database
    with _SWITCH_LOCK:
        current = stage_of()
        ok, why = can_switch(current, target)
        if not ok:
            raise StageRefused(why)

        before_version = state_version()
        previous_state = state_snapshot()
        if target == OFF:
            # Настройка первой: если её не удалось записать, шаг остаётся включённым вместе с составом
            settings = dict(config.load_settings())
            settings[SETTING_STAGE] = OFF
            config.save_settings(settings)
            database.compare_and_set_metadata(METADATA_VERSION, before_version,
                                              {METADATA_CANARY: "", METADATA_HTTP_START: ""})
            return {"stage": OFF}

        started = start_stage(target, candidates, now=now, expected_version=before_version)
        try:
            settings = dict(config.load_settings())
            settings[SETTING_STAGE] = target
            config.save_settings(settings)
        except Exception:
            # Отменяем только СВОЮ подготовку: если её версию уже сменил другой переход, не трогаем
            database.compare_and_set_metadata(METADATA_VERSION, started["version"], previous_state)
            raise
        return started


def http_start_counters() -> Optional[Dict[str, Any]]:
    """Снимок счётчиков HTTP на момент включения шага (M02)."""
    return _json_metadata(METADATA_HTTP_START)


def canary_members() -> List[str]:
    """Состав текущего шага, зафиксированный при включении. Пустой список — состав не сохранён."""
    stored = _json_metadata(METADATA_CANARY)
    return [str(s) for s in stored] if isinstance(stored, list) else []


def missing_members(candidates: Sequence[Dict[str, Any]],
                    allowed: Optional[Sequence[str]] = None) -> List[str]:
    """Участники шага, которых больше нельзя наблюдать: пропали из кандидатов или выключены.

    Пустой список разрешённых означает, что выключены все, поэтому недоступны и все участники —
    прежнее условие «permitted and …» молча признавало такую ситуацию нормальной (M01). Замена
    участникам не подбирается: либо шаг продолжается тем же составом, либо владелец его возвращает.
    """
    known = {str(c.get("shop") or "") for c in candidates}
    permitted_list = allowed if allowed is not None else allowed_shops()
    if permitted_list is None:
        return list(canary_members())    # разрешения неизвестны — наблюдать нельзя ни за кем
    permitted = set(permitted_list)
    return [shop for shop in canary_members() if shop not in known or shop not in permitted]


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


def rollback(reason: str, now: Optional[datetime.datetime] = None,
             expected_version: Optional[str] = ...) -> Dict[str, Any]:
    """Возвращает прежний порядок обходов целиком и записывает, почему.

    Порядок действий важен (M03): сначала выключается сама настройка, и только потом очищается
    состояние шага. Если запись настройки не удалась — в базе ничего не менялось, шаг остаётся
    включённым вместе со своим составом и снимком счётчиков, а откат НЕ записывается как состоявшийся.
    Обратный порядок оставлял бы включённый режим с пустыми метаданными.

    Откат тоже проходит через замок и версию состояния: он не должен выключить шаг, который владелец
    только что включил заново.
    """
    import json
    import config
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    expected = state_version() if expected_version is ... else expected_version
    previous = stage_of()
    record = {"at": moment.isoformat(), "from_stage": previous, "reason": reason,
              "canary": canary_members()}
    with _SWITCH_LOCK:
        if state_version() != expected:
            record["skipped"] = "состояние включения изменилось параллельно: откат не применён"
            return record
        try:
            settings = dict(config.load_settings())
            settings[SETTING_STAGE] = OFF
            config.save_settings(settings)
        except Exception as e:                       # noqa: BLE001 — отчёт важнее трассировки
            record["failed"] = f"не удалось выключить шаг: {type(e).__name__}"
            print(f"[Rollout] Откат не выполнен: {type(e).__name__}")
            return record                            # состояние шага осталось нетронутым
        version = database.compare_and_set_metadata(METADATA_VERSION, expected, {
            METADATA_LAST_ROLLBACK: json.dumps(record, ensure_ascii=False),
            METADATA_CANARY: "",                     # шага больше нет — нет и его состава
            METADATA_HTTP_START: "",
        })
        if version is None:
            # Состояние успел сменить другой процесс: настройка уже выключена, чужие данные не трогаем
            record["skipped"] = "состояние включения изменилось параллельно: метаданные не очищены"
            return record
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
    version = state_version()
    after = metrics(shops, began, moment, http_start_counters())
    needed, reason = should_rollback(baseline_metrics(), after)
    if not needed:
        return None
    record = rollback(reason, now=now, expected_version=version)
    if record.get("skipped") or record.get("failed"):
        return None
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
    refused = ""
    if stage != OFF and not shops:
        try:
            shops = canary_shops(stage, candidates)  # состав ещё не фиксировался (старое состояние)
        except StageRefused as e:
            shops, refused = [], str(e)
    before = baseline_metrics()
    after = metrics(shops, began, moment, http_start_counters()) if shops and began else None
    ready, why = ready_for_next_step(stage, before, after, began, moment)
    needed, rollback_reason = should_rollback(before, after)
    # Шаг включён, а пробных магазинов нет — это состояние надо назвать, а не показывать пустым прочерком
    if stage != OFF and not shops:
        why = refused or "пробные магазины ещё не определены: нет данных об обходах"
        ready = False
    # Участник шага пропал из кандидатов: замену ему не подбираем и молчать об этом нельзя (M01)
    gone = missing_members(candidates) if stage != OFF else []
    if gone:
        ready = False
        why = (f"участники шага недоступны для наблюдения (пропали или выключены): {', '.join(gone)} — "
               f"замена не подбирается, продолжать шаг нельзя")
    profiles = {str(c.get("shop") or ""): c.get("profile") for c in candidates}
    return {
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "canary_profiles": {shop: profiles.get(shop) for shop in shops},
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
