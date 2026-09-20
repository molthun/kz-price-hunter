"""Наблюдения пользователя (этап P07): за чем следим, когда это считается срабатыванием и когда молчим.

Здесь только правила — без базы, сети и Telegram. Главное, что они обеспечивают:
- одно срабатывание на одно событие: возврат цены к прежнему значению (149 990 → 151 000 → 149 990) не
  считается новым снижением, потому что сравнение идёт с ценой последнего уведомления, а не с предыдущим
  наблюдением;
- цель по цене срабатывает один раз, пока цена не поднимется выше цели и не опустится снова;
- тихие часы и пауза между сообщениями (cooldown) соблюдаются по времени владельца наблюдения;
- одноразовое наблюдение после срабатывания выключается, повторяющееся продолжает работать.
"""
from __future__ import annotations

import datetime
import zoneinfo
from typing import Any, Dict, List, Optional, Tuple

# Виды наблюдений (V1: решение владельца 2026-09-20 — товар, модель, поиск, категория)
PRODUCT, MODEL, SEARCH, CATEGORY = "product", "model", "search", "category"
KINDS = (PRODUCT, MODEL, SEARCH, CATEGORY)

# Условия срабатывания
TARGET_PRICE = "target_price"    # цена не выше заданной
DROP_PCT = "drop_pct"            # снижение не меньше заданного процента
DROP_KZT = "drop_kzt"            # снижение не меньше заданной суммы
ANY_DROP = "any_drop"            # любое снижение цены
BEST_PRICE = "best_price"        # цена ниже всего, что видели по этому наблюдению
BACK_IN_STOCK = "back_in_stock"  # товар снова появился в продаже
CONDITIONS = (TARGET_PRICE, DROP_PCT, DROP_KZT, ANY_DROP, BEST_PRICE, BACK_IN_STOCK)

NEEDS_THRESHOLD = (TARGET_PRICE, DROP_PCT, DROP_KZT)

INSTANT, DIGEST = "instant", "digest"
MODES = (INSTANT, DIGEST)

DEFAULT_TIMEZONE = "Asia/Almaty"
DEFAULT_QUIET_FROM = "23:00"     # решение владельца: ночью не будим
DEFAULT_QUIET_TO = "08:00"
DEFAULT_COOLDOWN_HOURS = 6
DEFAULT_DIGEST_HOUR = 10
MAX_WATCHES_PER_USER = 50        # чтобы один человек не превратил рассылку в поток

LABELS = {
    PRODUCT: "товар", MODEL: "модель", SEARCH: "поисковый запрос", CATEGORY: "категория",
    TARGET_PRICE: "цена не выше", DROP_PCT: "снижение в процентах", DROP_KZT: "снижение в тенге",
    ANY_DROP: "любое снижение", BEST_PRICE: "лучшая цена за всё время", BACK_IN_STOCK: "снова в продаже",
}


def _time(value: Any, fallback: str) -> str:
    """Время вида ЧЧ:ММ; пустое — значение по умолчанию."""
    text = str(value or "").strip() or fallback
    try:
        hour, minute = text.split(":")
        return f"{int(hour):02d}:{int(minute):02d}" if 0 <= int(hour) < 24 and 0 <= int(minute) < 60 else fallback
    except (ValueError, AttributeError):
        raise ValueError(f"Время должно быть в виде ЧЧ:ММ, получено «{value}»")


def normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    """Проверяет и приводит наблюдение к хранимому виду; объясняет ошибку словами."""
    kind = str(data.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"Неизвестный вид наблюдения: {kind or '—'}")
    target = str(data.get("target") or "").strip()
    if not target:
        raise ValueError("Не указано, за чем наблюдать")
    if len(target) > 200:
        raise ValueError("Слишком длинное значение для наблюдения")

    condition = str(data.get("condition") or ANY_DROP).strip()
    if condition not in CONDITIONS:
        raise ValueError(f"Неизвестное условие: {condition}")

    threshold = data.get("threshold")
    if condition in NEEDS_THRESHOLD:
        try:
            threshold = int(threshold)
        except (TypeError, ValueError):
            raise ValueError(f"Для условия «{LABELS[condition]}» нужно число")
        if condition == DROP_PCT and not 1 <= threshold <= 99:
            raise ValueError("Процент снижения: допустимо от 1 до 99")
        if condition in (TARGET_PRICE, DROP_KZT) and not 1 <= threshold <= 100_000_000:
            raise ValueError("Сумма: допустимо от 1 до 100 000 000 ₸")
    else:
        threshold = None

    mode = str(data.get("mode") or INSTANT).strip()
    if mode not in MODES:
        raise ValueError(f"Неизвестный способ доставки: {mode}")

    timezone = str(data.get("timezone") or DEFAULT_TIMEZONE).strip()
    try:
        zoneinfo.ZoneInfo(timezone)
    except Exception:
        raise ValueError(f"Неизвестный часовой пояс: {timezone}")

    try:
        cooldown = int(data.get("cooldown_hours", DEFAULT_COOLDOWN_HOURS))
    except (TypeError, ValueError):
        raise ValueError("Пауза между сообщениями: нужно число часов")
    if not 0 <= cooldown <= 720:
        raise ValueError("Пауза между сообщениями: допустимо от 0 до 720 часов")

    shops = data.get("shops") or []
    if not isinstance(shops, list) or any(not isinstance(s, str) for s in shops):
        raise ValueError("Магазины передаются списком")

    return {
        "kind": kind,
        "target": target,
        "title": str(data.get("title") or target).strip()[:200],
        "condition": condition,
        "threshold": threshold,
        "city": str(data.get("city") or "").strip() or None,
        "shops": sorted({s.strip() for s in shops if s.strip()}),
        "mode": mode,
        "quiet_from": _time(data.get("quiet_from"), DEFAULT_QUIET_FROM),
        "quiet_to": _time(data.get("quiet_to"), DEFAULT_QUIET_TO),
        "timezone": timezone,
        "cooldown_hours": cooldown,
        "repeat": 1 if data.get("repeat", True) else 0,
        "is_active": 1 if data.get("is_active", True) else 0,
    }


def matches(watch: Dict[str, Any], offer: Dict[str, Any]) -> bool:
    """Относится ли предложение к наблюдению: вид, город и выбранные магазины."""
    city = (watch.get("city") or "").strip()
    if city and city not in ("Все",) and (offer.get("city") or "") not in (city, None, ""):
        return False
    shops = watch.get("shops") or []
    if shops and (offer.get("shop") or "") not in shops:
        return False

    kind, target = watch["kind"], str(watch["target"]).lower()
    if kind == PRODUCT:
        return str(offer.get("id") or "") == watch["target"]
    if kind == MODEL:
        return str(offer.get("canonical_key") or "").lower() == target
    if kind == CATEGORY:
        return target in str(offer.get("category") or "").lower()
    if kind == SEARCH:
        import search_analytics
        return search_analytics.classify(watch["target"], [offer]) == search_analytics.FOUND
    return False


def condition_met(watch: Dict[str, Any], offer: Dict[str, Any],
                  state: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Сработало ли условие и как это объяснить человеку.

    `state` — память по этому наблюдению и предложению: цена последнего отправленного сообщения
    (`last_notified_price`), минимальная виденная цена (`min_price`) и было ли предложение в продаже
    (`was_available`). Сравнение с ценой последнего сообщения — то, что не даёт слать одно и то же дважды.
    """
    state = state or {}
    price = int(offer.get("current_price") or 0)
    if price <= 0:
        return False, ""
    condition = watch["condition"]
    threshold = watch.get("threshold")
    last_sent = state.get("last_notified_price")
    available = offer.get("is_available", True)

    if condition == BACK_IN_STOCK:
        # В памяти наблюдения признак хранится числом, поэтому сравниваем по значению, а не по тождеству
        if available and state.get("was_available") in (False, 0):
            return True, "товар снова в продаже"
        return False, ""

    if not available:
        return False, ""

    if condition == TARGET_PRICE:
        if price > int(threshold):
            return False, ""
        # Пока цена держится не выше цели, повторно не пишем: напоминание раз в день — это не новость
        if last_sent is not None and price >= int(last_sent):
            return False, ""
        return True, f"цена {price} ₸ — не выше вашей цели {int(threshold)} ₸"

    if condition == BEST_PRICE:
        seen_min = state.get("min_price")
        if seen_min is None or price < int(seen_min):
            return True, f"{price} ₸ — самая низкая цена за всё время наблюдения"
        return False, ""

    # Снижения считаются от цены последнего отправленного сообщения, иначе возврат к прежней цене
    # выглядел бы новым снижением (149 990 → 151 000 → 149 990)
    base = last_sent if last_sent is not None else state.get("previous_price")
    if base is None or price >= int(base):
        return False, ""
    drop = int(base) - price
    if condition == ANY_DROP:
        return True, f"цена снизилась с {int(base)} до {price} ₸"
    if condition == DROP_KZT:
        if drop >= int(threshold):
            return True, f"цена снизилась на {drop} ₸ (вы просили от {int(threshold)} ₸)"
        return False, ""
    if condition == DROP_PCT:
        pct = drop * 100.0 / int(base)
        if pct >= float(threshold):
            return True, f"цена снизилась на {pct:.0f} % (вы просили от {int(threshold)} %)"
        return False, ""
    return False, ""


def _local(watch: Dict[str, Any], moment: datetime.datetime) -> datetime.datetime:
    tz = zoneinfo.ZoneInfo(watch.get("timezone") or DEFAULT_TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(tz)


def in_quiet_hours(watch: Dict[str, Any], moment: datetime.datetime) -> bool:
    """Тихие часы по времени владельца наблюдения; интервал через полночь тоже работает."""
    start, end = watch.get("quiet_from") or DEFAULT_QUIET_FROM, watch.get("quiet_to") or DEFAULT_QUIET_TO
    if start == end:
        return False
    now = _local(watch, moment).strftime("%H:%M")
    if start > end:                     # интервал через полночь: 23:00 → 08:00
        return now >= start or now < end
    return start <= now < end


def cooldown_left(watch: Dict[str, Any], moment: datetime.datetime,
                  last_sent_at: Optional[datetime.datetime]) -> float:
    """Сколько секунд осталось до конца паузы между сообщениями по этому наблюдению."""
    hours = int(watch.get("cooldown_hours", DEFAULT_COOLDOWN_HOURS) or 0)
    if not hours or last_sent_at is None:
        return 0.0
    if last_sent_at.tzinfo is None:
        last_sent_at = last_sent_at.replace(tzinfo=datetime.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, (last_sent_at + datetime.timedelta(hours=hours) - moment).total_seconds())


def deliver_now(watch: Dict[str, Any], moment: datetime.datetime,
                last_sent_at: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
    """Можно ли отправить сейчас; иначе — почему ждём (срабатывание при этом не теряется)."""
    if cooldown_left(watch, moment, last_sent_at) > 0:
        return False, "пауза между сообщениями"
    if in_quiet_hours(watch, moment):
        return False, "тихие часы"
    if (watch.get("mode") or INSTANT) == DIGEST:
        return False, "сводка"
    return True, ""


def digest_due_at(watch: Dict[str, Any], moment: datetime.datetime,
                  hour: int = DEFAULT_DIGEST_HOUR) -> datetime.datetime:
    """Ближайшее время, когда можно отправить накопленное: конец тихих часов или час сводки."""
    local = _local(watch, moment)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += datetime.timedelta(days=1)
    while in_quiet_hours(watch, target):
        target += datetime.timedelta(minutes=30)
    return target.astimezone(datetime.timezone.utc)


def describe(watch: Dict[str, Any]) -> str:
    """Короткое человеческое описание наблюдения для списка и для текста сообщения."""
    what = f"{LABELS.get(watch['kind'], watch['kind'])} «{watch.get('title') or watch['target']}»"
    condition = watch["condition"]
    if condition == TARGET_PRICE:
        rule = f"цена не выше {int(watch['threshold']):,} ₸".replace(",", " ")
    elif condition == DROP_PCT:
        rule = f"снижение от {int(watch['threshold'])} %"
    elif condition == DROP_KZT:
        rule = f"снижение от {int(watch['threshold']):,} ₸".replace(",", " ")
    else:
        rule = LABELS.get(condition, condition)
    where = []
    if watch.get("city"):
        where.append(watch["city"])
    if watch.get("shops"):
        where.append(", ".join(watch["shops"]))
    tail = f" · {' · '.join(where)}" if where else ""
    return f"{what}: {rule}{tail}"
