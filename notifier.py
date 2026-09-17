import urllib.parse
import requests
from typing import Dict, Any
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def format_price(amount: int) -> str:
    return f"{amount:,} ₸".replace(",", " ")

def send_alert(product: Dict[str, Any], anomaly: Dict[str, Any]):
    title = product["title"]
    shop = product.get("shop", "DNS Казахстан")
    url = product["url"]
    city = product.get("city", "Астана")
    category = product.get("category", "")
    image_url = product.get("image_url", "")

    emoji_header = anomaly["emoji"]
    old_price_str = format_price(anomaly["old_price"])
    new_price_str = format_price(anomaly["new_price"])
    savings_str = format_price(anomaly["savings"])
    drop_pct = anomaly["drop_pct"]
    reason = anomaly["reason"]

    shop_emoji = "🏪"
    if "dns" in shop.lower():
        shop_emoji = "🟧"
    elif "белый ветер" in shop.lower() or "shop.kz" in shop.lower():
        shop_emoji = "🟦"
    elif "технодом" in shop.lower():
        shop_emoji = "🔴"
    elif "forcecom" in shop.lower():
        shop_emoji = "⚡️"
    elif "sulpak" in shop.lower() or "сульпак" in shop.lower():
        shop_emoji = "🟢"
    elif "мечта" in shop.lower() or "mechta" in shop.lower():
        shop_emoji = "🟣"
    elif "alser" in shop.lower():
        shop_emoji = "🟡"
    elif "эврика" in shop.lower() or "evrika" in shop.lower():
        shop_emoji = "🔷"
    elif "moon" in shop.lower():
        shop_emoji = "🚀"
    elif "kaspi" in shop.lower() or "каспи" in shop.lower():
        shop_emoji = "🔴"
    elif "4mobile" in shop.lower():
        shop_emoji = "📱"


    # Формируем сообщение
    message_text = (
        f"{emoji_header}\n\n"
        f"{shop_emoji} <b>Магазин:</b> {shop}\n"
        f"🏷 <b>Товар:</b> {title}\n"
        f"📁 <b>Категория:</b> {category}\n"
        f"📍 <b>Регион:</b> {city}\n\n"
        f"❌ <b>Старая цена:</b> <s>{old_price_str}</s>\n"
        f"✅ <b>Новая цена:</b> <b>{new_price_str}</b> (-{drop_pct}%)\n"
        f"💰 <b>Выгода:</b> <b>{savings_str}</b>\n\n"
        f"ℹ️ {reason}\n\n"
        f"🔗 <a href='{url}'>Купить в {shop}</a>"
    )

    # 1. Печать в консоль
    print("\n" + "=" * 65)
    print(f"{emoji_header} | {shop_emoji} {shop}")
    print(f"Товар: {title}")
    print(f"Старая цена: {old_price_str}  -->  Новая цена: {new_price_str} (-{drop_pct}%)")
    print(f"Экономия: {savings_str} | Регион: {city}")
    print(f"Ссылка: {url}")
    print("=" * 65 + "\n")

    # 2. Отправка в Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": message_text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": f"⚡️ Открыть товар в {shop}", "url": url}]
                    ]
                }
            }

            if image_url and image_url.startswith("http"):
                payload["photo"] = image_url
                res = requests.post(tg_url, json=payload, timeout=10)
            else:
                msg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                res = requests.post(msg_url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message_text,
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": f"⚡️ Открыть товар в {shop}", "url": url}]
                        ]
                    }
                }, timeout=10)

            if res.status_code != 200:
                print(f"[Telegram Error] Статус {res.status_code}: {res.text}")
        except Exception as e:
            print(f"[Telegram Exception]: {e}")
