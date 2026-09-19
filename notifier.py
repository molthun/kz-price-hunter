import html
import json
import asyncio
import time
from urllib.parse import urlsplit
import requests
from typing import Dict, Any, Optional
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
    from database import claim_notification, finish_notification, get_user, get_connection, active_product_clause
    from detector import alert_matches_user, notify_level_allows
    if not get_bot_token():
        return 0
    if telegram_paused_for() > 0:
        return 0  # Telegram просил подождать: ни одно сообщение не отправляется до конца паузы
    sent = 0
    for _ in range(limit):
        item = claim_notification()
        if not item:
            break
        try:
            payload = json.loads(item["payload"])
            product, anomaly = payload["product"], payload["anomaly"]
            user = get_user(item["user_id"])
            candidate = dict(product, alert_type=anomaly["type"], new_price=anomaly["new_price"],
                             discount_pct=anomaly["drop_pct"], savings_kzt=anomaly["savings"])
            with get_connection() as conn:
                current = conn.execute("SELECT current_price FROM products WHERE id=? AND " + active_product_clause(),
                                       (str(product["id"]),)).fetchone()
                alert = conn.execute("SELECT is_dismissed FROM alerts WHERE id=?", (item["alert_id"],)).fetchone()
            if (not user or user["is_blocked"] or not user["settings"].get("telegram_notify_enabled")
                or not alert or alert[0]  # алерт удалён или скрыт администратором
                or not current or current[0] != anomaly["new_price"]
                or not alert_matches_user(candidate, user["settings"])
                or not notify_level_allows(anomaly, user["settings"].get("telegram_notify_level", "ALL"))):
                finish_notification(item["id"], "cancelled")
                continue
            result = send_telegram_alert(user["id"], product, anomaly)
            if isinstance(result, bool):  # совместимость с подменами в тестах
                result = DeliveryResult("sent" if result else "retry", None, None if result else "Telegram delivery failed")
            if result:
                finish_notification(item["id"], "sent", item["attempts"])
                sent += 1
            elif result.status == "permanent":
                finish_notification(item["id"], "failed", item["attempts"], result.error)
            else:
                finish_notification(item["id"], "pending", item["attempts"], result.error, retry_after=result.retry_after)
                if result.retry_after is not None:
                    pause_telegram(result.retry_after)  # 429 — лимит на весь бот
                    break
        except Exception as e:
            finish_notification(item["id"], "pending", item["attempts"], type(e).__name__)
    return sent


async def notification_worker():
    while True:
        try:
            await asyncio.to_thread(deliver_pending)
        except Exception as e:
            print(f"[Telegram Queue] {type(e).__name__}")
        await asyncio.sleep(10)
