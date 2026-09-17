import html
import requests
from typing import Dict, Any
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

def send_telegram_alert(chat_id: int, product: Dict[str, Any], anomaly: Dict[str, Any]) -> bool:
    """Отправляет карточку аномалии в личный чат пользователя. Возвращает True при успехе."""
    shop = product.get("shop", "магазин")
    url = product["url"]
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
    reply_markup = {
        "inline_keyboard": [
            [{"text": f"⚡️ Открыть товар в {shop}", "url": url}],
            [{"text": "📊 Дашборд цен", "url": APP_URL}]
        ]
    }

    try:
        if image_url and image_url.startswith("http"):
            res = telegram_api("sendPhoto", {
                "chat_id": chat_id, "photo": image_url, "caption": message_text,
                "parse_mode": "HTML", "reply_markup": reply_markup
            })
            if res.status_code == 200:
                return True
            # Telegram не смог загрузить фото — отправляем текстом
        res = telegram_api("sendMessage", {
            "chat_id": chat_id, "text": message_text,
            "parse_mode": "HTML", "reply_markup": reply_markup
        })
        if res.status_code != 200:
            print(f"[Telegram Error] chat {chat_id}: статус {res.status_code}: {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"[Telegram Exception] chat {chat_id}: {e}")
        return False

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
