import html
import json
import asyncio
import time
from urllib.parse import urlsplit
import requests
from typing import Dict, Any, List, Optional
from config import get_bot_token, APP_URL

SHOP_EMOJI = [
    (("dns",), "🟧"),
    (("белый ветер", "shop.kz"), "🟦"),
    (("технодом",), "🔴"),
    (("forcecom",), "⚡️"),
    (("sulpak", "сульпак"), "🟢"),
    (("мечта", "mechta"), "🟣"),
    (("alser",), "🟡"),
    (("эврика", "evrika"), "🔷"),
    (("moon",), "🚀"),
    (("kaspi", "каспи"), "🔴"),
    (("4mobile",), "📱"),
    (("flip",), "🛍"),
    (("halyk", "халык"), "🏦"),
    (("tgrad", "техноград"), "🟪"),
    (("ants",), "🐜"),
    (("itmag",), "💾"),
    (("ispace",), "🍏"),
]

def format_price(amount: int) -> str:
    return f"{amount:,} ₸".replace(",", " ")

def _shop_emoji(shop: str) -> str:
    s = shop.lower()
    for keys, emoji in SHOP_EMOJI:
        if any(k in s for k in keys):
            return emoji
    return "🏪"

def telegram_api(method: str, payload: Dict[str, Any], timeout: int = 10) -> requests.Response:
    token = get_bot_token()
    if not token:
        raise RuntimeError("Токен Telegram-бота не задан (переменная окружения TELEGRAM_BOT_TOKEN)")
    return requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=timeout)

def is_safe_image_url(url: str) -> bool:
    """Проверяет безопасность URL изображения перед отправкой в Telegram API."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        if not parsed.netloc or "@" in parsed.netloc:
            return False
        return True
    except Exception:
        return False

class DeliveryResult:
    """Итог отправки: истинен только при успехе (совместим с прежним bool).

    status: sent — доставлено; retry — временная ошибка (429, 5xx, сеть), повторить позже;
    permanent — повтор бессмысленен (бот заблокирован, чат не найден, неверный запрос).
    """
    __slots__ = ("status", "retry_after", "error")

    def __init__(self, status: str, retry_after: Optional[float] = None, error: Optional[str] = None):
        self.status = status
        self.retry_after = retry_after
        self.error = error

    def __bool__(self) -> bool:
        return self.status == "sent"

    def __repr__(self) -> str:
        return f"DeliveryResult({self.status!r}, retry_after={self.retry_after!r}, error={self.error!r})"


def classify_telegram_response(res) -> DeliveryResult:
    """Разбирает ответ Bot API: 429 с паузой retry_after, 400/403/404 — постоянные, остальное — временные."""
    if res.status_code == 200:
        return DeliveryResult("sent")
    try:
        data = res.json() or {}
    except Exception:
        data = {}
    if res.status_code == 429:
        retry_after = (data.get("parameters") or {}).get("retry_after") or res.headers.get("Retry-After")
        try:
            retry_after = float(retry_after)
        except (TypeError, ValueError):
            retry_after = None
        return DeliveryResult("retry", retry_after, "Telegram 429")
    if res.status_code in (400, 403, 404):
        return DeliveryResult("permanent", None, f"Telegram {res.status_code}")
    return DeliveryResult("retry", None, f"Telegram {res.status_code}")


def send_telegram_alert(chat_id: int, product: Dict[str, Any], anomaly: Dict[str, Any]) -> DeliveryResult:
    """Отправляет карточку аномалии в Telegram через Bot API.
    Фото с fallback на текст — только если фото отклонено (400); при 429 и блокировке бота
    текст не отправляется следом.
    """
    shop = product.get("shop", "Магазин")
    url = product.get("url", "")
    image_url = product.get("image_url", "")

    # Названия товаров приходят с сайтов магазинов — экранируем для HTML-разметки Telegram
    message_text = (
        f"{html.escape(anomaly['emoji'])}\n\n"
        f"{_shop_emoji(shop)} <b>Магазин:</b> {html.escape(shop)}\n"
        f"🏷 <b>Товар:</b> {html.escape(product['title'])}\n"
        f"📁 <b>Категория:</b> {html.escape(product.get('category', ''))}\n"
        f"📍 <b>Регион:</b> {html.escape(product.get('city', 'Астана'))}\n\n"
        f"❌ <b>Старая цена:</b> <s>{format_price(anomaly['old_price'])}</s>\n"
        f"✅ <b>Новая цена:</b> <b>{format_price(anomaly['new_price'])}</b> (-{anomaly['drop_pct']}%)\n"
        f"💰 <b>Выгода:</b> <b>{format_price(anomaly['savings'])}</b>\n\n"
        f"ℹ️ {html.escape(anomaly['reason'])}"
    )
    keyboard = [[{"text": f"⚡️ Открыть товар в {shop}", "url": url}]]
    if APP_URL:
        keyboard.append([{"text": "📊 Дашборд цен", "url": APP_URL}])
    reply_markup = {"inline_keyboard": keyboard}

    try:
        if is_safe_image_url(image_url):
            result = classify_telegram_response(telegram_api("sendPhoto", {
                "chat_id": chat_id, "photo": image_url, "caption": message_text,
                "parse_mode": "HTML", "reply_markup": reply_markup
            }))
            # Текстом — только если Telegram не смог взять фото (400); 429/403/5xx возвращаются как есть
            if result or result.error != "Telegram 400":
                if not result:
                    print(f"[Telegram Error] {result.error}")
                return result
        result = classify_telegram_response(telegram_api("sendMessage", {
            "chat_id": chat_id, "text": message_text,
            "parse_mode": "HTML", "reply_markup": reply_markup
        }))
        if not result:
            print(f"[Telegram Error] {result.error}")
        return result
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)

def send_watch_message(chat_id: int, payload: Dict[str, Any]) -> DeliveryResult:
    """Сообщение по личному наблюдению (P07): что сработало и почему — в первой же строке."""
    product = payload.get("product") or {}
    shop = product.get("shop", "Магазин")
    watch_id = payload.get("watch_id")
    price = int(payload.get("price") or 0)
    reason = str(payload.get("reason") or "").strip()
    desc = str(payload.get("description") or "").strip()

    # Понятный заголовок по типу срабатывания
    if "не выше вашей цели" in reason.lower() or "цена не выше" in desc.lower():
        sub_header = "🎯 <b>Цена достигла вашей цели!</b>\n"
    elif "самая низкая цена" in reason.lower() or "лучшая цена" in desc.lower():
        sub_header = "👑 <b>Самая низкая цена за всё время!</b>\n"
    elif "снова в продаже" in reason.lower():
        sub_header = "📦 <b>Товар снова в продаже!</b>\n"
    elif "снизилась" in reason.lower():
        sub_header = "📉 <b>Цена снизилась!</b>\n"
    else:
        sub_header = ""

    text = (
        f"🔔 <b>Сработало ваше наблюдение</b>\n"
        f"{sub_header}"
        f"{html.escape(desc)}\n\n"
        f"{_shop_emoji(shop)} <b>Магазин:</b> {html.escape(shop)}\n"
        f"🏷 <b>Товар:</b> {html.escape(product.get('title') or '')}\n"
        f"📍 <b>Регион:</b> {html.escape(product.get('city') or 'Астана')}\n"
        f"✅ <b>Цена:</b> <b>{format_price(price)}</b>\n\n"
        f"ℹ️ {html.escape(reason)}"
    )
    keyboard = [[{"text": f"⚡️ Открыть товар в {shop}", "url": product.get("url") or APP_URL or ""}]]
    second_row = []
    if watch_id:
        second_row.append({"text": "🔕 Не следить", "callback_data": f"unwatch:{watch_id}"})
    if APP_URL:
        second_row.append({"text": "🔔 Мои наблюдения", "url": APP_URL})
    if second_row:
        keyboard.append(second_row)
    try:
        return classify_telegram_response(telegram_api("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": keyboard}}))
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


DIGEST_LINES = 10        # длинное письмо не читают: остальное сворачивается в «и ещё N»


def send_watch_digest(chat_id: int, payloads: List[Dict[str, Any]]) -> DeliveryResult:
    """Одно сообщение по наблюдению в режиме сводки: сколько сработало и что именно.

    Режим «сводка» обещает человеку одно письмо, а не отложенную пачку, поэтому накопленные
    срабатывания собираются в один текст.
    """
    first = payloads[0]
    lines = [f"🔔 <b>Сводка по наблюдению</b>", html.escape(first.get("description") or ""), ""]
    lines.append(f"Сработало раз: <b>{len(payloads)}</b>")
    lines.append("")
    for payload in payloads[:DIGEST_LINES]:
        product = payload.get("product") or {}
        shop = product.get("shop") or "Магазин"
        url = product.get("url") or ""
        title = html.escape(product.get("title") or "")
        name = f'<a href="{html.escape(url, quote=True)}">{title}</a>' if url.startswith(("http://", "https://")) else title
        lines.append(f"{_shop_emoji(shop)} {name} — <b>{format_price(int(payload.get('price') or 0))}</b>"
                     f" · {html.escape(shop)}")
        if payload.get("reason"):
            lines.append(f"   ℹ️ {html.escape(payload['reason'])}")
    if len(payloads) > DIGEST_LINES:
        lines.append(f"…и ещё {len(payloads) - DIGEST_LINES}")
    keyboard = [[{"text": "🔔 Мои наблюдения", "url": APP_URL}]] if APP_URL else []
    try:
        return classify_telegram_response(telegram_api("sendMessage", {
            "chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": keyboard}}))
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


def _deliver_watch(item, payload, counts) -> tuple:
    """Отправка задания по наблюдению.

    Возвращает (отправлено ли сообщение, пауза Telegram по 429 или None).
    """
    from database import finish_notification, get_user, get_connection

    user = get_user(item["user_id"])
    with get_connection() as conn:
        watch = conn.execute("SELECT * FROM watches WHERE id = ? AND user_id = ?",
                             (payload.get("watch_id"), item["user_id"])).fetchone()
        current = conn.execute("SELECT current_price FROM products WHERE id = ?",
                               (str((payload.get("product") or {}).get("id") or ""),)).fetchone()
    # Наблюдение удалено или выключено, человек отключил Telegram, цена уже другая — сообщение неактуально
    if (not user or user["is_blocked"] or not user["settings"].get("telegram_notify_enabled")
            or not watch or not watch["is_active"]
            or not current or int(current[0] or 0) != int(payload.get("price") or 0)):
        finish_notification(item["id"], "cancelled")
        counts["cancelled"] += 1
        return False, None

    # Режим «сводка»: остальные накопленные срабатывания этого же наблюдения уходят одним сообщением
    extra_items = []
    if (watch["mode"] or "instant") == "digest":
        from database import claim_watch_digest
        for other in claim_watch_digest(watch["id"], item["user_id"], item["id"]):
            try:
                extra_items.append((other, json.loads(other["payload"])))
            except (TypeError, ValueError):
                finish_notification(other["id"], "cancelled")
                counts["cancelled"] += 1

    # Каждое срабатывание сводки проверяется отдельно: устаревшая цена не должна попасть в письмо
    fresh, stale = [(item, payload)], []
    for other, other_payload in extra_items:
        with get_connection() as conn:
            price = conn.execute("SELECT current_price FROM products WHERE id = ?",
                                 (str((other_payload.get("product") or {}).get("id") or ""),)).fetchone()
        if price and int(price[0] or 0) == int(other_payload.get("price") or 0):
            fresh.append((other, other_payload))
        else:
            stale.append(other)
    for other in stale:
        finish_notification(other["id"], "cancelled")
        counts["cancelled"] += 1

    deliveries = [d for d, _ in fresh]
    payloads = [pl for _, pl in fresh]
    result = send_watch_digest(user["id"], payloads) if len(payloads) > 1 \
        else send_watch_message(user["id"], payload)
    if isinstance(result, bool):  # совместимость с подменами в тестах
        result = DeliveryResult("sent" if result else "retry", None, None if result else "Telegram delivery failed")
    if result:
        for delivery in deliveries:
            finish_notification(delivery["id"], "sent", delivery["attempts"])
        counts["sent"] += 1
        with get_connection() as conn:
            for done in payloads:
                conn.execute("UPDATE watch_events SET status = 'sent' WHERE id = ?", (done.get("event_id"),))
            # Одноразовое наблюдение выключается ПОСЛЕ отправки: своё сообщение оно должно успеть доставить
            conn.execute("UPDATE watches SET is_active = 0 WHERE id = ? AND repeat_mode = 0", (watch["id"],))
            conn.commit()
        return True, None
    if result.status == "permanent":
        for delivery in deliveries:
            finish_notification(delivery["id"], "failed", delivery["attempts"], result.error)
        counts["failed"] += 1
        return False, None
    # Временная неудача: вся сводка возвращается в очередь целиком, ни одно срабатывание не теряется
    for delivery in deliveries:
        finish_notification(delivery["id"], "pending", delivery["attempts"], result.error,
                            retry_after=result.retry_after)
    counts["retry"] += 1
    if result.retry_after is not None:
        pause_telegram(result.retry_after)   # 429 — лимит на весь бот
        return False, float(result.retry_after)
    return False, None


def send_daily_digest(chat_id: int, payload: Dict[str, Any]) -> DeliveryResult:
    """Суточная сводка администратору (P13). Текст собран заранее из посчитанных цифр."""
    lines = payload.get("lines") or []
    text = "\n".join(str(line) for line in lines)[:4000]
    keyboard = [[{"text": "🩺 Центр мониторинга", "url": APP_URL.rstrip("/") + "/monitoring"}]] if APP_URL else []
    try:
        return classify_telegram_response(telegram_api("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": keyboard}}))
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


def _deliver_daily_digest(item, payload, counts) -> tuple:
    """Отправка суточной сводки: та же очередь, те же повторы и та же пауза 429."""
    from database import finish_notification, get_user
    import config

    user = get_user(item["user_id"])
    # Сводка — админские цифры: если человек больше не администратор или заблокирован, письмо отменяется
    if not user or user["is_blocked"] or int(item["user_id"]) not in config.ADMIN_TELEGRAM_IDS:
        finish_notification(item["id"], "cancelled")
        counts["cancelled"] += 1
        return False, None

    result = send_daily_digest(user["id"], payload)
    if isinstance(result, bool):  # совместимость с подменами в тестах
        result = DeliveryResult("sent" if result else "retry", None, None if result else "Telegram delivery failed")
    if result:
        finish_notification(item["id"], "sent", item["attempts"])
        counts["sent"] += 1
        return True, None
    if result.status == "permanent":
        finish_notification(item["id"], "failed", item["attempts"], result.error)
        counts["failed"] += 1
        return False, None
    finish_notification(item["id"], "pending", item["attempts"], result.error, retry_after=result.retry_after)
    counts["retry"] += 1
    if result.retry_after is not None:
        pause_telegram(result.retry_after)   # 429 — лимит на весь бот
        return False, float(result.retry_after)
    return False, None


def dispatch_alert(product: Dict[str, Any], anomaly: Dict[str, Any]) -> int:
    """Печатает алерт в консоль и рассылает его пользователям, чьи личные пороги он проходит.

    Возвращает количество отправленных уведомлений.
    """
    from database import get_notification_recipients
    from detector import alert_matches_user, notify_level_allows

    shop = product.get("shop", "магазин")
    print("\n" + "=" * 65)
    print(f"{anomaly['emoji']} | {_shop_emoji(shop)} {shop}")
    print(f"Товар: {product['title']}")
    print(f"Старая цена: {format_price(anomaly['old_price'])}  -->  Новая цена: {format_price(anomaly['new_price'])} (-{anomaly['drop_pct']}%)")
    print(f"Экономия: {format_price(anomaly['savings'])} | Регион: {product.get('city', 'Астана')}")
    print(f"Ссылка: {product['url']}")
    print("=" * 65 + "\n")

    if not get_bot_token():
        return 0

    candidate = {
        "alert_type": anomaly["type"],
        "new_price": anomaly["new_price"],
        "discount_pct": anomaly["drop_pct"],
        "savings_kzt": anomaly["savings"],
        "shop": shop,
        "title": product.get("title", ""),
        "category": product.get("category", ""),
        "url": product.get("url", ""),
    }

    sent = 0
    for user in get_notification_recipients():
        settings = user["settings"]
        if not alert_matches_user(candidate, settings):
            continue
        if not notify_level_allows(anomaly, settings.get("telegram_notify_level", "ALL")):
            continue
        if send_telegram_alert(user["id"], product, anomaly):
            sent += 1
    return sent


def prepare_deliveries(product, anomaly):
    from database import get_notification_recipients
    from detector import alert_matches_user, notify_level_allows
    candidate = dict(product, alert_type=anomaly["type"], new_price=anomaly["new_price"],
                     discount_pct=anomaly["drop_pct"], savings_kzt=anomaly["savings"])
    return [(u["id"], {"product": product, "anomaly": anomaly})
            for u in get_notification_recipients()
            if alert_matches_user(candidate, u["settings"])
            and notify_level_allows(anomaly, u["settings"].get("telegram_notify_level", "ALL"))]


# Пауза Telegram по 429 действует на весь бот (R-M03): хранится в БД, переживает перезапуск
# и соблюдается всеми следующими циклами доставки, а не только для одного сообщения.
TELEGRAM_PAUSE_KEY = "telegram_pause_until"
MAX_TELEGRAM_PAUSE_SECONDS = 24 * 3600


def telegram_paused_for() -> float:
    from database import get_metadata
    try:
        until = float(get_metadata(TELEGRAM_PAUSE_KEY, "0") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, until - time.time())


def pause_telegram(seconds: float) -> None:
    from database import set_metadata
    seconds = max(1.0, min(float(seconds), MAX_TELEGRAM_PAUSE_SECONDS))
    set_metadata(TELEGRAM_PAUSE_KEY, str(time.time() + seconds))


def deliver_pending(limit=10):
    if not get_bot_token():
        return 0
    if telegram_paused_for() > 0:
        return 0  # Telegram просил подождать: ни одно сообщение не отправляется до конца паузы
    sent = 0
    counts = {"sent": 0, "cancelled": 0, "retry": 0, "failed": 0, "errors": 0}
    pause = None
    try:
        sent, pause = _deliver_batch(limit, counts)
    finally:
        _record_delivery(counts, pause)
    return sent


def _record_delivery(counts, pause) -> None:
    """Сводка цикла доставки без chat_id/пользователей (P01); пустые циклы не пишутся."""
    if not any(counts.values()) and pause is None:
        return
    try:
        from telemetry import telemetry, EVENT_TELEGRAM_ALERT, SEVERITY_INFO, SEVERITY_WARNING, COMPONENT_TELEGRAM
        bad = counts["failed"] or counts["errors"] or pause is not None
        telemetry.record_event(
            EVENT_TELEGRAM_ALERT, SEVERITY_WARNING if bad else SEVERITY_INFO, COMPONENT_TELEGRAM,
            "Доставка уведомлений: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v)
            + (f"; пауза Telegram {pause:g} с" if pause is not None else ""),
            data={**counts, "pause_seconds": pause})
    except Exception:
        pass


def _deliver_batch(limit, counts):
    from database import (claim_notification, finish_notification, get_user, get_connection, fresh_price_clause,
                          fresh_benchmark_clause)
    from detector import alert_matches_user, notify_level_allows
    sent = 0
    pause = None
    for _ in range(limit):
        item = claim_notification()
        if not item:
            break
        try:
            payload = json.loads(item["payload"])
            if payload.get("kind") == "daily_digest":
                # Суточная сводка администратору (P13): своё правило актуальности, та же очередь
                delivered, pause = _deliver_daily_digest(item, payload, counts)
                sent += 1 if delivered else 0
                if pause is not None:
                    break
                continue
            if payload.get("kind") == "watch":
                # Задание по личному наблюдению: свои проверки актуальности, та же очередь и та же пауза 429
                delivered, pause = _deliver_watch(item, payload, counts)
                sent += 1 if delivered else 0
                if pause is not None:
                    break
                continue
            product, anomaly = payload["product"], payload["anomaly"]
            user = get_user(item["user_id"])
            candidate = dict(product, alert_type=anomaly["type"], new_price=anomaly["new_price"],
                             discount_pct=anomaly["drop_pct"], savings_kzt=anomaly["savings"])
            with get_connection() as conn:
                current = conn.execute("SELECT current_price FROM products WHERE id=? AND " + fresh_price_clause(),
                                       (str(product["id"]),)).fetchone()
                # Основание арбитража проверяется по записи алерта тем же правилом, что и в выдаче
                # (COALESCE(competitor_seen_at, created_at) ≤ 72 ч): старая очередь без поля в payload тоже (P02 C02)
                alert = conn.execute("SELECT is_dismissed, " + fresh_benchmark_clause("") + " AS benchmark_ok "
                                     "FROM alerts WHERE id=?", (item["alert_id"],)).fetchone()
            if (not user or user["is_blocked"] or not user["settings"].get("telegram_notify_enabled")
                or not alert or alert[0]  # алерт удалён или скрыт администратором
                or not alert[1]  # цена конкурента-основания арбитража устарела (P02 C02)
                or not current or current[0] != anomaly["new_price"]
                or not alert_matches_user(candidate, user["settings"])
                or not notify_level_allows(anomaly, user["settings"].get("telegram_notify_level", "ALL"))):
                finish_notification(item["id"], "cancelled")
                counts["cancelled"] += 1
                continue
            result = send_telegram_alert(user["id"], product, anomaly)
            if isinstance(result, bool):  # совместимость с подменами в тестах
                result = DeliveryResult("sent" if result else "retry", None, None if result else "Telegram delivery failed")
            if result:
                finish_notification(item["id"], "sent", item["attempts"])
                sent += 1
                counts["sent"] += 1
            elif result.status == "permanent":
                finish_notification(item["id"], "failed", item["attempts"], result.error)
                counts["failed"] += 1
            else:
                finish_notification(item["id"], "pending", item["attempts"], result.error, retry_after=result.retry_after)
                counts["retry"] += 1
                if result.retry_after is not None:
                    pause_telegram(result.retry_after)  # 429 — лимит на весь бот
                    pause = float(result.retry_after)
                    break
        except Exception as e:
            finish_notification(item["id"], "pending", item["attempts"], type(e).__name__)
            counts["errors"] += 1
    return sent, pause


async def notification_worker():
    import environment
    while True:
        try:
            # Суточная сводка администратору (P13): ставится в очередь раз в сутки, если включена
            try:
                import daily_digest
                await asyncio.to_thread(daily_digest.queue_if_due)
            except Exception as e:
                print(f"[Daily] Сводка не поставлена в очередь: {type(e).__name__}")
            await asyncio.to_thread(deliver_pending)
            # Пульс очереди Telegram (P14): умерший воркер не должен выглядеть работающим
            await asyncio.to_thread(environment.heartbeat, environment.TELEGRAM, "цикл доставки")
        except Exception as e:
            print(f"[Telegram Queue] {type(e).__name__}")
            from telemetry import telemetry, COMPONENT_TELEGRAM
            telemetry.record_system_error(COMPONENT_TELEGRAM, "notification_worker", e)
        await asyncio.sleep(10)
