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

# Виды наблюдений. V1 (решение владельца 2026-09-20): товар, модель, поиск, категория.
# V2: магазин, выгодные предложения и арбитраж — последние два срабатывают на тех же находках,
# что показывает лента, поэтому «сделка» здесь означает записанный алерт, а не чьё-то мнение о цене.
# V2 (S2-E20): продавец (seller) для подписки на предложения конкретного продавца.
PRODUCT, MODEL, SEARCH, CATEGORY = "product", "model", "search", "category"
SHOP, DEAL, ARBITRAGE = "shop", "deal", "arbitrage"
SELLER = "seller"
KINDS = (PRODUCT, MODEL, SEARCH, CATEGORY, SHOP, DEAL, ARBITRAGE, SELLER)

# Для наблюдений за сделками и арбитражем цель — это область поиска, а не конкретный товар
ANY_TARGET = "все"
ALERT_KINDS = (DEAL, ARBITRAGE)
DEAL_ALERT_TYPES = ("SUPER_DISCOUNT", "ZERO_GLITCH")
ARBITRAGE_ALERT_TYPES = ("MARKET_ARBITRAGE", "ARBITRAGE")

# Условия срабатывания
TARGET_PRICE = "target_price"    # цена не выше заданной
DROP_PCT = "drop_pct"            # снижение не меньше заданного процента
DROP_KZT = "drop_kzt"            # снижение не меньше заданной суммы
ANY_DROP = "any_drop"            # любое снижение цены
BEST_PRICE = "best_price"        # цена ниже всего, что видели по этому наблюдению
BACK_IN_STOCK = "back_in_stock"  # товар снова появился в продаже
DISCOUNT_PCT = "discount_pct"    # скидка в находке не меньше заданной (для сделок и арбитража)
ANY_FIND = "any_find"            # любая находка по этому наблюдению

# V2 триггеры (S2-E20)
PRICE_DROP = "price_drop"        # снижение цены (порог в тенге или процентах, либо любое)
NEW_OFFER = "new_offer"          # появление нового предложения
NEW_SELLER = "new_seller"        # появление нового продавца с этим товаром
NEW_USED_OFFER = "new_used_offer"# появление б/у предложения (USED или REFURBISHED)
BETTER_CHANNEL = "better_channel"# появление более выгодного канала покупки
BETTER_SELLER = "better_seller"  # появление продавца с лучшей ценой или условием

CONDITIONS = (TARGET_PRICE, DROP_PCT, DROP_KZT, ANY_DROP, BEST_PRICE, BACK_IN_STOCK,
              DISCOUNT_PCT, ANY_FIND, PRICE_DROP, NEW_OFFER, NEW_SELLER,
              NEW_USED_OFFER, BETTER_CHANNEL, BETTER_SELLER)

NEEDS_THRESHOLD = (TARGET_PRICE, DROP_PCT, DROP_KZT, DISCOUNT_PCT)

# Условия, которые имеют смысл только для находок ленты (сделка, арбитраж), и наоборот
FIND_ONLY_CONDITIONS = (DISCOUNT_PCT, ANY_FIND)

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
    SHOP: "магазин", DEAL: "выгодные предложения", ARBITRAGE: "разница цен между магазинами",
    SELLER: "продавец",
    DISCOUNT_PCT: "скидка не меньше", ANY_FIND: "любая находка",
    TARGET_PRICE: "цена не выше", DROP_PCT: "снижение в процентах", DROP_KZT: "снижение в тенге",
    ANY_DROP: "любое снижение", BEST_PRICE: "лучшая цена за всё время", BACK_IN_STOCK: "снова в продаже",
    PRICE_DROP: "снижение цены", NEW_OFFER: "новое предложение", NEW_SELLER: "новый продавец",
    NEW_USED_OFFER: "появление б/у", BETTER_CHANNEL: "более выгодный канал", BETTER_SELLER: "лучшая цена у другого продавца",
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

    condition = str(data.get("condition") or (ANY_FIND if kind in ALERT_KINDS else ANY_DROP)).strip()
    if condition not in CONDITIONS:
        raise ValueError(f"Неизвестное условие: {condition}")
    # Условие должно быть применимо к виду: «скидка не меньше» не имеет смысла для наблюдения за
    # конкретным товаром, а «снова в продаже» — для ленты находок
    if kind in ALERT_KINDS and condition not in FIND_ONLY_CONDITIONS + (TARGET_PRICE,):
        raise ValueError(f"Для вида «{LABELS[kind]}» доступны: любая находка, скидка не меньше, цена не выше")
    if kind not in ALERT_KINDS and condition in FIND_ONLY_CONDITIONS:
        raise ValueError(f"Условие «{LABELS[condition]}» работает только для сделок и разницы цен")

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
    elif condition == PRICE_DROP and threshold is not None and str(threshold).strip() != "":
        try:
            threshold = int(threshold)
            if not 1 <= threshold <= 100_000_000:
                raise ValueError("Порог снижения: допустимо от 1 до 100 000 000")
        except (TypeError, ValueError):
            raise ValueError("Для порога снижения нужно положительное число")
    else:
        threshold = None

    mode = str(data.get("mode") or INSTANT).strip()
    if mode not in MODES:
        raise ValueError(f"Неизвестный способ доставки: {mode}")

    timezone = str(data.get("timezone") or DEFAULT_TIMEZONE).strip()
    import timezones as tz_lookup
    if tz_lookup.known(timezone) is False:      # None — базы нет, проверить нечем (регрессия 5.13.0)
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

    # V2 (S2-E20): фильтры состояний товара (new, used, refurbished)
    raw_conditions = data.get("item_conditions")
    if raw_conditions is None:
        raw_conditions = data.get("conditions")
    if isinstance(raw_conditions, str):
        raw_conditions = [c.strip() for c in raw_conditions.split(",") if c.strip()]
    if raw_conditions and (not isinstance(raw_conditions, (list, tuple, set)) or
                           any(not isinstance(c, str) for c in raw_conditions)):
        raise ValueError("Состояния товара передаются списком (new, used, refurbished)")
    allowed_conditions = {"new", "used", "refurbished"}
    clean_conditions = []
    if raw_conditions:
        for c in raw_conditions:
            cl = str(c).strip().lower()
            if cl in allowed_conditions and cl not in clean_conditions:
                clean_conditions.append(cl)

    # V2 (S2-E20): фильтры продавцов (seller_id / seller_slug / названия)
    raw_sellers = data.get("sellers") or []
    if isinstance(raw_sellers, str):
        raw_sellers = [s.strip() for s in raw_sellers.split(",") if s.strip()]
    if not isinstance(raw_sellers, (list, tuple, set)) or any(not isinstance(s, str) for s in raw_sellers):
        raise ValueError("Продавцы передаются списком")
    clean_sellers = sorted({s.strip().lower() for s in raw_sellers if s.strip()})

    # V2 (S2-E20): фильтры каналов (web, telegram, instagram, kaspi)
    raw_channels = data.get("channels") or []
    if isinstance(raw_channels, str):
        raw_channels = [ch.strip() for ch in raw_channels.split(",") if ch.strip()]
    if not isinstance(raw_channels, (list, tuple, set)) or any(not isinstance(ch, str) for ch in raw_channels):
        raise ValueError("Каналы передаются списком")
    clean_channels = sorted({ch.strip().lower() for ch in raw_channels if ch.strip()})

    return {
        "kind": kind,
        "target": target,
        "title": str(data.get("title") or target).strip()[:200],
        "condition": condition,
        "threshold": threshold,
        "city": str(data.get("city") or "").strip() or None,
        "shops": sorted({s.strip() for s in shops if s.strip()}),
        "item_conditions": clean_conditions,
        "sellers": clean_sellers,
        "channels": clean_channels,
        "mode": mode,
        "quiet_from": _time(data.get("quiet_from"), DEFAULT_QUIET_FROM),
        "quiet_to": _time(data.get("quiet_to"), DEFAULT_QUIET_TO),
        "timezone": timezone,
        "cooldown_hours": cooldown,
        "repeat": 1 if data.get("repeat", True) else 0,
        "is_active": 1 if data.get("is_active", True) else 0,
    }


def matches(watch: Dict[str, Any], offer: Dict[str, Any]) -> bool:
    """Относится ли предложение к наблюдению: вид, город, магазины, продавцы, каналы, состояние."""
    city = (watch.get("city") or "").strip()
    if city and city not in ("Все",) and (offer.get("city") or "") not in (city, None, ""):
        return False
    shops = watch.get("shops") or []
    if shops and (offer.get("shop") or "") not in shops:
        return False

    # V2 (S2-E20): фильтрация по продавцам
    watch_sellers = watch.get("sellers") or []
    if watch_sellers:
        offer_sellers = {
            str(offer.get("seller_id") or "").strip().lower(),
            str(offer.get("seller_slug") or "").strip().lower(),
            str(offer.get("seller_name") or "").strip().lower(),
            str(offer.get("shop") or "").strip().lower(),
        } - {""}
        if not any(ws in offer_sellers or any(ws in os for os in offer_sellers) for ws in watch_sellers):
            return False

    # V2 (S2-E20): фильтрация по типам каналов
    watch_channels = watch.get("channels") or []
    if watch_channels:
        offer_channels = {
            str(offer.get("channel_type") or "").strip().lower(),
            str(offer.get("channel_id") or "").strip().lower(),
            str(offer.get("channel") or "").strip().lower(),
        } - {""}
        if not any(ch in offer_channels for ch in watch_channels):
            return False

    # V2 (S2-E20): фильтрация по состоянию (new, used, refurbished)
    watch_conditions = watch.get("item_conditions") or []
    if watch_conditions:
        offer_cond = str(offer.get("condition") or offer.get("item_condition") or "new").strip().lower()
        if offer_cond not in watch_conditions:
            return False

    kind, target = watch["kind"], str(watch["target"]).lower()
    if kind == PRODUCT:
        offer_pid = str(offer.get("id") or "")
        return (offer_pid == watch["target"] or
                str(offer.get("canonical_product_id") or "") == watch["target"] or
                str(offer.get("product_id") or "") == watch["target"])
    if kind == MODEL:
        return (str(offer.get("canonical_key") or "").lower() == target or
                str(offer.get("model") or "").lower() == target)
    if kind == CATEGORY:
        return target in str(offer.get("category") or "").lower()
    if kind == SEARCH:
        import search_analytics
        return search_analytics.classify(watch["target"], [offer]) == search_analytics.FOUND
    if kind == SHOP:
        shop = str(offer.get("shop") or "").lower()
        return bool(shop) and (shop == target or target in shop)
    if kind == SELLER:
        offer_sellers = {
            str(offer.get("seller_id") or "").strip().lower(),
            str(offer.get("seller_slug") or "").strip().lower(),
            str(offer.get("seller_name") or "").strip().lower(),
            str(offer.get("shop") or "").strip().lower(),
        } - {""}
        return any(target == s or target in s for s in offer_sellers)
    if kind in ALERT_KINDS:
        # Находка — это записанный алерт того же вида, что показывает лента. Без алерта наблюдение молчит.
        allowed = DEAL_ALERT_TYPES if kind == DEAL else ARBITRAGE_ALERT_TYPES
        if str(offer.get("alert_type") or "") not in allowed:
            return False
        if target in ("", ANY_TARGET, "all"):
            return True
        return target in str(offer.get("category") or "").lower()
    return False


def condition_met(watch: Dict[str, Any], offer: Dict[str, Any],
                  state: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Сработало ли условие и как это объяснить человеку.

    `state` — память по этому наблюдению и предложению: цена последнего отправленного сообщения
    (`last_notified_price`), минимальная виденная цена (`min_price`), было ли в продаже (`was_available`),
    список известных продавцов (`known_sellers`), список известных каналов (`known_channels`),
    список известных предложений (`known_offers`).
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

    # V2 (S2-E20): NEW_OFFER
    if condition == NEW_OFFER:
        known_offers = set(state.get("known_offers") or [])
        offer_id = str(offer.get("id") or offer.get("offer_id") or "")
        if offer_id and offer_id in known_offers:
            return False, ""
        seller_label = offer.get("seller_name") or offer.get("shop") or "магазин"
        return True, f"новое предложение: {price} ₸ ({seller_label})"

    # V2 (S2-E20): NEW_SELLER
    if condition == NEW_SELLER:
        known_sellers = set(state.get("known_sellers") or [])
        seller_key = str(offer.get("seller_slug") or offer.get("seller_id") or offer.get("seller_name") or offer.get("shop") or "").strip().lower()
        if not seller_key or seller_key in known_sellers:
            return False, ""
        seller_label = offer.get("seller_name") or offer.get("shop") or seller_key
        return True, f"новый продавец «{seller_label}»: {price} ₸"

    # V2 (S2-E20): NEW_USED_OFFER
    if condition == NEW_USED_OFFER:
        cond = str(offer.get("condition") or offer.get("item_condition") or "").strip().lower()
        if cond not in ("used", "refurbished"):
            return False, ""
        known_offers = set(state.get("known_offers") or [])
        offer_id = str(offer.get("id") or offer.get("offer_id") or "")
        if offer_id and offer_id in known_offers:
            if last_sent is not None and price >= int(last_sent):
                return False, ""
        seller_label = offer.get("seller_name") or offer.get("shop") or "продавец"
        cond_label = "б/у" if cond == "used" else "refurbished"
        return True, f"появилось {cond_label} предложение: {price} ₸ ({seller_label})"

    # V2 (S2-E20): BETTER_CHANNEL
    if condition == BETTER_CHANNEL:
        channel = str(offer.get("channel_type") or offer.get("channel") or "")
        seen_min = state.get("min_price")
        known_channels = set(state.get("known_channels") or [])
        if seen_min is not None and price < int(seen_min):
            return True, f"более выгодный канал ({channel or 'альтернативный'}): {price} ₸ (ранее от {int(seen_min)} ₸)"
        if channel and channel.lower() not in known_channels and (last_sent is None or price < int(last_sent)):
            return True, f"новый канал предложения ({channel}): {price} ₸"
        return False, ""

    # V2 (S2-E20): BETTER_SELLER
    if condition == BETTER_SELLER:
        seen_min = state.get("min_price")
        seller_label = offer.get("seller_name") or offer.get("shop") or "продавец"
        if seen_min is not None and price < int(seen_min):
            return True, f"лучшая цена у продавца «{seller_label}»: {price} ₸ (ранее от {int(seen_min)} ₸)"
        return False, ""

    if condition in (ANY_FIND, DISCOUNT_PCT):
        # Повтор того же не шлём: сравнение с ценой последнего сообщения по этой же находке
        if last_sent is not None and price >= int(last_sent):
            return False, ""
        discount = float(offer.get("discount_pct") or 0)
        if condition == DISCOUNT_PCT and discount < float(threshold):
            return False, ""
        where = offer.get("competitor_shop")
        tail = f" (в {where} дороже)" if where else ""
        if discount > 0:
            return True, f"находка: {price} ₸, скидка {discount:.0f} %{tail}"
        return True, f"находка: {price} ₸{tail}"

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
    if condition == PRICE_DROP:
        if threshold is None:
            return True, f"цена снизилась с {int(base)} до {price} ₸"
        if int(threshold) <= 100:
            pct = drop * 100.0 / int(base)
            if pct >= float(threshold):
                return True, f"цена снизилась на {pct:.0f} % (порог от {int(threshold)} %)"
            return False, ""
        else:
            if drop >= int(threshold):
                return True, f"цена снизилась на {drop} ₸ (порог от {int(threshold)} ₸)"
            return False, ""

    return False, ""


def _local(watch: Dict[str, Any], moment: datetime.datetime) -> datetime.datetime:
    import timezones
    # Без базы часовых поясов считаем по UTC: тихие часы сдвинутся, но уведомления не сломаются
    tz = timezones.zone(watch.get("timezone") or DEFAULT_TIMEZONE)
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
    target = watch.get("title") or watch["target"]
    if watch["kind"] in ALERT_KINDS:
        scope = "" if str(watch["target"]).strip().lower() in ("", ANY_TARGET, "all") \
            else f" в категории «{target}»"
        what = f"{LABELS[watch['kind']]}{scope}"
    else:
        what = f"{LABELS.get(watch['kind'], watch['kind'])} «{target}»"
    condition = watch["condition"]
    if condition == TARGET_PRICE:
        rule = f"цена не выше {int(watch['threshold']):,} ₸".replace(",", " ")
    elif condition == DROP_PCT:
        rule = f"снижение от {int(watch['threshold'])} %"
    elif condition == DROP_KZT:
        rule = f"снижение от {int(watch['threshold']):,} ₸".replace(",", " ")
    elif condition == DISCOUNT_PCT:
        rule = f"скидка от {int(watch['threshold'])} %"
    elif condition == PRICE_DROP:
        if watch.get("threshold"):
            th = int(watch["threshold"])
            rule = f"снижение от {th} %" if th <= 100 else f"снижение от {th:,} ₸".replace(",", " ")
        else:
            rule = "любое снижение цены"
    else:
        rule = LABELS.get(condition, condition)
    where = []
    if watch.get("city"):
        where.append(watch["city"])
    if watch.get("shops"):
        where.append(", ".join(watch["shops"]))
    if watch.get("sellers"):
        where.append(f"продавцы: {', '.join(watch['sellers'])}")
    if watch.get("channels"):
        where.append(f"каналы: {', '.join(watch['channels'])}")
    if watch.get("item_conditions"):
        cond_map = {"new": "новое", "used": "б/у", "refurbished": "refurbished"}
        where.append("состояние: " + ", ".join(cond_map.get(c, c) for c in watch["item_conditions"]))
    tail = f" · {' · '.join(where)}" if where else ""
    return f"{what}: {rule}{tail}"
