import html
import json
import os
import asyncio
import time
from urllib.parse import urlsplit
import requests
from typing import Dict, Any, List, Optional
import config
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

# TG-14: стандартные визуальные эффекты сообщений Telegram (Bot API 7.5)
EFFECT_FIRE = "5104841245755180586"       # 🔥 Огонь (горячие скидки, падение цены)
EFFECT_CONFETTI = "5046509860389126442"   # 🎉 Конфетти (срабатывание наблюдения)
EFFECT_THUMBS_UP = "5107584321108051014"  # 👍 Лайк
EFFECT_HEART = "5159385139981059251"      # ❤️ Сердце


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
    __slots__ = ("status", "retry_after", "error", "message_id", "has_photo")

    def __init__(self, status: str, retry_after: Optional[float] = None, error: Optional[str] = None,
                 message_id: Optional[int] = None, has_photo: bool = False):
        self.status = status
        self.retry_after = retry_after
        self.error = error
        self.message_id = message_id      # номер отправленного сообщения: по нему находку можно поправить (TG-06)
        self.has_photo = has_photo

    def __bool__(self) -> bool:
        return self.status == "sent"

    def __repr__(self) -> str:
        return f"DeliveryResult({self.status!r}, retry_after={self.retry_after!r}, error={self.error!r})"


def classify_telegram_response(res) -> DeliveryResult:
    """Разбирает ответ Bot API: 429 с паузой retry_after, 400/403/404 — постоянные, остальное — временные."""
    try:
        data = res.json() or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if res.status_code == 200:
        sent = data.get("result")
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        return DeliveryResult("sent", message_id=message_id if isinstance(message_id, int) else None)
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


def button_style(style: Optional[str]) -> Optional[str]:
    """TG-08: возвращает style (primary, success, danger) по Bot API 9.4, если включен TELEGRAM_BUTTON_STYLES."""
    if not style or not config.button_styles_enabled():
        return None
    s = str(style).lower().strip()
    return s if s in ("primary", "success", "danger") else None


def url_button(text: str, url: str, style: Optional[str] = None) -> Dict[str, Any]:
    """Кнопка перехода по URL с опциональным стилем."""
    btn: Dict[str, Any] = {"text": text, "url": url}
    st = button_style(style)
    if st:
        btn["style"] = st
    return btn


def callback_button(text: str, data: str, style: Optional[str] = None) -> Dict[str, Any]:
    """Кнопка обратного вызова callback_data с опциональным стилем."""
    btn: Dict[str, Any] = {"text": text, "callback_data": data}
    st = button_style(style)
    if st:
        btn["style"] = st
    return btn


def copy_button(text: str, copy_text: str, style: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """TG-08: кнопка копирования текста в буфер обмена (Bot API 7.11).
    None если выключено через TELEGRAM_COPY_BUTTON или текст пустой.
    """
    if not config.copy_button_enabled() or not copy_text:
        return None
    val = str(copy_text).strip()
    if not val:
        return None
    if len(val) > 256:
        val = val[:256]
    btn: Dict[str, Any] = {"text": text, "copy_text": {"text": val}}
    st = button_style(style)
    if st:
        btn["style"] = st
    return btn


def site_button(text: str, url: str, style: Optional[str] = None) -> Dict[str, Any]:
    """Кнопка на наш сайт. По https открывает его как Mini App: сайт получает initData и входит сам.

    Обычная url-кнопка открывает встроенный браузер Telegram без данных входа — человек видел страницу
    «Войдите через Telegram». web_app-кнопки работают только в личных чатах; бот других и не обслуживает.
    TELEGRAM_SITE_BUTTONS=url возвращает прежние ссылки.
    """
    if url.startswith("https://") and os.getenv("TELEGRAM_SITE_BUTTONS", "webapp").strip().lower() != "url":
        btn: Dict[str, Any] = {"text": text, "web_app": {"url": url}}
    else:
        btn = {"text": text, "url": url}
    st = button_style(style)
    if st:
        btn["style"] = st
    return btn


def tg_time_tag(unix_ts: Optional[float] = None, format_spec: str = "t", fallback: str = "") -> str:
    """TG-13: формирует нативный тег времени Telegram <tg-time unix="..." format="..."> (Bot API 9.5).
    Если отключено через TELEGRAM_NATIVE_TIME=off, возвращает экранированный fallback.
    """
    ts = int(unix_ts if unix_ts is not None else time.time())
    fb = fallback or time.strftime("%H:%M", time.localtime(ts))
    if not config.native_time_enabled():
        return html.escape(fb)
    return f'<tg-time unix="{ts}" format="{html.escape(format_spec)}">{html.escape(fb)}</tg-time>'


def expandable_quote(text: str) -> str:
    """TG-03: сворачиваемый блок цитаты <blockquote expandable>...</blockquote> (Bot API 7.8+)."""
    if not text:
        return ""
    return f"<blockquote expandable>{text}</blockquote>"


def make_link_preview_options(
    is_disabled: bool = False,
    url: Optional[str] = None,
    prefer_small_media: bool = False,
    prefer_large_media: bool = False,
    show_above_text: bool = False,
) -> Dict[str, Any]:
    """TG-16: формирование объекта LinkPreviewOptions (Telegram Bot API 7.0/7.8).
    Заменяет устаревший disable_web_page_preview.
    """
    options: Dict[str, Any] = {}
    if is_disabled:
        options["is_disabled"] = True
        return options
    if url:
        options["url"] = url
    if prefer_small_media:
        options["prefer_small_media"] = True
    if prefer_large_media:
        options["prefer_large_media"] = True
    if show_above_text:
        options["show_above_text"] = True
    return options


def send_telegram_rich_message(
    chat_id: int,
    markdown: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> DeliveryResult:
    """TG-03: синхронная отправка структурированного сообщения Rich Message (Bot API 10.1+)."""
    if not config.rich_messages_enabled() or not markdown:
        return DeliveryResult("permanent", None, "rich_messages_disabled")
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown[:32768]},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        res = telegram_api("sendRichMessage", payload)
        return classify_telegram_response(res)
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


def save_prepared_inline_message(
    user_id: int,
    title: str,
    price: int,
    shop: str,
    url: str,
    city: str = "Астана",
    image_url: str = "",
) -> Optional[str]:
    """TG-12: подготавливает inline-сообщение (Bot API 8.0) для шеринга из Mini App через WebApp.shareMessage.
    Возвращает prepared_message_id или None при сбое.
    """
    token = get_bot_token()
    if not token or not config.inline_mode_enabled():
        return None

    emoji = _shop_emoji(shop)
    msg_lines = [
        f"🏷 <b>{html.escape(title)}</b>\n",
        f"{emoji} <b>Магазин:</b> {html.escape(shop)}",
        f"💰 <b>Цена:</b> <b>{format_price(price)}</b>",
        f"📍 <b>Регион:</b> {html.escape(city)}\n",
        f"⚡️ <i>Поделились из KZ Price Hunter</i>\n",
        f"⏱ {tg_time_tag(format_spec='r', fallback='только что')}"
    ]

    keyboard = []
    if url and url.startswith("http"):
        row = [url_button(f"⚡️ В {shop}", url, style="primary")]
        c_btn = copy_button("📋 Ссылка", url)
        if c_btn:
            row.append(c_btn)
        keyboard.append(row)
    if APP_URL:
        keyboard.append([site_button("📊 Сравнить цены", APP_URL)])

    article: Dict[str, Any] = {
        "type": "article",
        "id": f"share_{int(time.time())}",
        "title": f"{format_price(price)} — {title[:50]}",
        "input_message_content": {
            "message_text": "\n".join(msg_lines),
            "parse_mode": "HTML",
            "link_preview_options": make_link_preview_options(prefer_small_media=True, url=url if url else None)
        }
    }
    if is_safe_image_url(image_url):
        article["thumbnail_url"] = image_url
    if keyboard:
        article["reply_markup"] = {"inline_keyboard": keyboard}

    payload = {
        "user_id": user_id,
        "result": article,
        "allow_user_chats": True,
        "allow_group_chats": True,
        "allow_channel_chats": True,
    }
    try:
        res = telegram_api("savePreparedInlineMessage", payload)
        if res.status_code == 200:
            data = res.json()
            return (data.get("result") or {}).get("id")
        else:
            print(f"[Telegram] savePreparedInlineMessage HTTP {res.status_code}: {res.text[:120]}")
    except Exception as e:
        print(f"[Telegram] Ошибка savePreparedInlineMessage: {type(e).__name__}")
    return None


def note_delivery_result(user_id: int, result: DeliveryResult) -> None:
    """TG-04: 403 на отправку в личный чат — бот заблокирован, чат не начат или аккаунт удалён.

    Отметка снимается, когда человек снова пишет боту или разблокирует его; до тех пор очередь к нему
    не обращается. Сбой отметки доставке не мешает.
    """
    if result.status != "permanent" or result.error != "Telegram 403" or not config.bot_block_tracking_enabled():
        return
    try:
        from database import set_bot_blocked
        set_bot_blocked(user_id, True)
    except Exception as e:
        print(f"[Telegram] Не удалось отметить блокировку бота: {type(e).__name__}")


TOPIC_METADATA: Dict[str, Dict[str, Any]] = {
    "alerts": {"name": "⚡️ Горячие скидки", "icon_color": 0x6FB9F0},
    "watches": {"name": "🔔 Мои наблюдения", "icon_color": 0xFFD67E},
    "ai": {"name": "🤖 AI-Консультант", "icon_color": 0xCB86DB},
    "search": {"name": "🔍 Поиск товаров", "icon_color": 0x8EEE98},
}

_unsupported_topics_chats: Set[int] = set()


def get_or_create_forum_topic(chat_id: int, topic_type: str) -> Optional[int]:
    """TG-11: возвращает thread_id темы в личном чате пользователя (Bot API 9.3/9.4).
    Создает тему при первом обращении через createForumTopic.
    При отключенных темах или ошибке Telegram возвращает None (сообщение уходит в общий диалог).
    """
    if not config.topics_mode_enabled() or chat_id in _unsupported_topics_chats:
        return None
    from database import get_user_topic, save_user_topic
    try:
        existing = get_user_topic(chat_id, topic_type)
        if existing:
            return existing
    except Exception:
        pass

    token = get_bot_token()
    if not token or not chat_id:
        return None

    meta = TOPIC_METADATA.get(topic_type)
    if not meta:
        return None

    try:
        resp = telegram_api("createForumTopic", {
            "chat_id": chat_id,
            "name": meta["name"],
            "icon_color": meta["icon_color"]
        })
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                tid = (data.get("result") or {}).get("message_thread_id")
                if tid:
                    try:
                        save_user_topic(chat_id, topic_type, tid)
                    except Exception:
                        pass
                    return tid
        if resp.status_code in (400, 403):
            _unsupported_topics_chats.add(chat_id)
    except Exception as e:
        print(f"[Telegram Topics] Ошибка создания темы {topic_type}: {type(e).__name__}")
    return None


def send_telegram_alert(
    chat_id: int,
    product: Dict[str, Any],
    anomaly: Dict[str, Any],
    message_thread_id: Optional[int] = None
) -> DeliveryResult:
    """Отправляет карточку аномалии в Telegram через Bot API.
    Фото с fallback на текст — только если фото отклонено (400); при 429 и блокировке бота
    текст не отправляется следом.
    """
    if message_thread_id is None:
        message_thread_id = get_or_create_forum_topic(chat_id, "alerts")

    shop = product.get("shop", "Магазин")
    url = product.get("url", "")
    image_url = product.get("image_url", "")

    # Названия товаров приходят с сайтов магазинов — экранируем для HTML-разметки Telegram
    reason_raw = str(anomaly.get("reason") or "").strip()
    reason_html = expandable_quote(html.escape(reason_raw)) if len(reason_raw) > 70 else html.escape(reason_raw)
    time_badge = f"⏱ {tg_time_tag(format_spec='r', fallback='только что')}"
    message_text = (
        f"{html.escape(anomaly['emoji'])}\n\n"
        f"{_shop_emoji(shop)} <b>Магазин:</b> {html.escape(shop)}\n"
        f"🏷 <b>Товар:</b> {html.escape(product['title'])}\n"
        f"📁 <b>Категория:</b> {html.escape(product.get('category', ''))}\n"
        f"📍 <b>Регион:</b> {html.escape(product.get('city', 'Астана'))}\n\n"
        f"❌ <b>Старая цена:</b> <s>{format_price(anomaly['old_price'])}</s>\n"
        f"✅ <b>Новая цена:</b> <b>{format_price(anomaly['new_price'])}</b> (-{anomaly['drop_pct']}%)\n"
        f"💰 <b>Выгода:</b> <b>{format_price(anomaly['savings'])}</b>\n\n"
        f"ℹ️ {reason_html}\n"
        f"{time_badge}"
    )
    keyboard = [[url_button(f"⚡️ Открыть товар в {shop}", url, style="primary")]]
    second_row = []
    if APP_URL:
        second_row.append(site_button("📊 Дашборд цен", APP_URL))
    if url:
        c_btn = copy_button("📋 Скопировать ссылку", url)
        if c_btn:
            second_row.append(c_btn)
    if second_row:
        keyboard.append(second_row)
    reply_markup = {"inline_keyboard": keyboard}

    message_effect_id = None
    if config.message_effects_enabled():
        drop = anomaly.get("drop_pct") or 0
        if drop >= 25 or anomaly.get("is_anomaly"):
            message_effect_id = EFFECT_FIRE

    try:
        if is_safe_image_url(image_url):
            photo_payload: Dict[str, Any] = {
                "chat_id": chat_id, "photo": image_url, "caption": message_text,
                "parse_mode": "HTML", "reply_markup": reply_markup
            }
            if message_thread_id is not None:
                photo_payload["message_thread_id"] = message_thread_id
            if message_effect_id:
                photo_payload["message_effect_id"] = message_effect_id
            result = classify_telegram_response(telegram_api("sendPhoto", photo_payload))
            result.has_photo = bool(result)
            # Текстом — только если Telegram не смог взять фото (400); 429/403/5xx возвращаются как есть
            if result or result.error != "Telegram 400":
                if not result:
                    print(f"[Telegram Error] {result.error}")
                return result
        msg_payload: Dict[str, Any] = {
            "chat_id": chat_id, "text": message_text,
            "parse_mode": "HTML", "reply_markup": reply_markup,
            "link_preview_options": make_link_preview_options(prefer_small_media=True, show_above_text=True, url=url) if url else make_link_preview_options(is_disabled=True)
        }
        if message_thread_id is not None:
            msg_payload["message_thread_id"] = message_thread_id
        if message_effect_id:
            msg_payload["message_effect_id"] = message_effect_id
        result = classify_telegram_response(telegram_api("sendMessage", msg_payload))
        if not result:
            print(f"[Telegram Error] {result.error}")
        return result
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)

def send_watch_message(
    chat_id: int,
    payload: Dict[str, Any],
    message_thread_id: Optional[int] = None
) -> DeliveryResult:
    """Сообщение по личному наблюдению (P07 / V2): что сработало и почему — в первой же строке."""
    if message_thread_id is None:
        message_thread_id = get_or_create_forum_topic(chat_id, "watches")

    product = payload.get("product") or {}
    shop = product.get("shop", "Магазин")
    seller_name = product.get("seller_name") or shop
    channel_type = product.get("channel_type")
    condition = product.get("condition") or product.get("item_condition")
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
    elif "появилось б/у" in reason.lower() or "б/у предложение" in reason.lower():
        sub_header = "♻️ <b>Появилось б/у предложение!</b>\n"
    elif "новый продавец" in reason.lower():
        sub_header = "🏪 <b>Новый продавец для товара!</b>\n"
    elif "новое предложение" in reason.lower():
        sub_header = "✨ <b>Новое предложение!</b>\n"
    elif "более выгодный канал" in reason.lower() or "новый канал" in reason.lower():
        sub_header = "🚀 <b>Более выгодный канал покупки!</b>\n"
    elif "снизилась" in reason.lower():
        sub_header = "📉 <b>Цена снизилась!</b>\n"
    else:
        sub_header = ""

    details = [
        f"{_shop_emoji(shop)} <b>Магазин / Продавец:</b> {html.escape(seller_name)}",
        f"🏷 <b>Товар:</b> {html.escape(product.get('title') or '')}",
    ]
    if condition and condition != "new":
        cond_label = "Б/у" if condition == "used" else "Refurbished"
        details.append(f"📦 <b>Состояние:</b> {cond_label}")
    if channel_type:
        details.append(f"🌐 <b>Канал:</b> {html.escape(str(channel_type))}")
    details.append(f"📍 <b>Регион:</b> {html.escape(product.get('city') or 'Астана')}")
    details.append(f"✅ <b>Цена:</b> <b>{format_price(price)}</b>")

    body = "\n".join(details)
    reason_raw = str(reason or "").strip()
    reason_html = expandable_quote(html.escape(reason_raw)) if len(reason_raw) > 70 else html.escape(reason_raw)
    time_badge = f"⏱ {tg_time_tag(format_spec='r', fallback='только что')}"
    text = (
        f"🔔 <b>Сработало ваше наблюдение</b>\n"
        f"{sub_header}"
        f"{html.escape(desc)}\n\n"
        f"{body}\n\n"
        f"ℹ️ {reason_html}\n"
        f"{time_badge}"
    )
    prod_url = product.get("url") or APP_URL or ""
    keyboard = []
    if prod_url:
        keyboard.append([url_button("⚡️ Открыть предложение", prod_url, style="primary")])
    second_row = []
    if watch_id:
        second_row.append(callback_button("🔕 Не следить", f"unwatch:{watch_id}", style="danger"))
    if product.get("url"):
        c_btn = copy_button("📋 Скопировать ссылку", product["url"])
        if c_btn:
            second_row.append(c_btn)
    if second_row:
        keyboard.append(second_row)
    if APP_URL:
        keyboard.append([site_button("🔔 Мои наблюдения", APP_URL)])
    message_effect_id = None
    if config.message_effects_enabled():
        message_effect_id = EFFECT_CONFETTI

    try:
        preview_opts = make_link_preview_options(prefer_small_media=True, url=prod_url) if prod_url else make_link_preview_options(is_disabled=True)
        msg_payload: Dict[str, Any] = {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": keyboard},
            "link_preview_options": preview_opts
        }
        if message_thread_id is not None:
            msg_payload["message_thread_id"] = message_thread_id
        if message_effect_id:
            msg_payload["message_effect_id"] = message_effect_id
        return classify_telegram_response(telegram_api("sendMessage", msg_payload))
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


DIGEST_LINES = 10        # длинное письмо не читают: остальное сворачивается в «и ещё N»


def send_watch_digest(
    chat_id: int,
    payloads: List[Dict[str, Any]],
    message_thread_id: Optional[int] = None
) -> DeliveryResult:
    """Одно сообщение по наблюдению в режиме сводки: сколько сработало и что именно.

    Режим «сводка» обещает человеку одно письмо, а не отложенную пачку, поэтому накопленные
    срабатывания собираются в один текст.
    """
    if message_thread_id is None:
        message_thread_id = get_or_create_forum_topic(chat_id, "watches")

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
    keyboard = [[site_button("🔔 Мои наблюдения", APP_URL)]] if APP_URL else []
    try:
        msg_payload: Dict[str, Any] = {
            "chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML",
            "link_preview_options": make_link_preview_options(is_disabled=True),
            "reply_markup": {"inline_keyboard": keyboard}
        }
        if message_thread_id is not None:
            msg_payload["message_thread_id"] = message_thread_id
        return classify_telegram_response(telegram_api("sendMessage", msg_payload))
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


def _deliver_watch(item, payload, counts) -> tuple:
    """Отправка задания по наблюдению.

    Возвращает (отправлено ли сообщение, пауза Telegram по 429 или None).
    """
    from database import finish_notification, get_user, get_connection, bot_unreachable

    user = get_user(item["user_id"])
    with get_connection() as conn:
        watch = conn.execute("SELECT * FROM watches WHERE id = ? AND user_id = ?",
                             (payload.get("watch_id"), item["user_id"])).fetchone()
        current = conn.execute("SELECT current_price FROM products WHERE id = ?",
                               (str((payload.get("product") or {}).get("id") or ""),)).fetchone()
        if not current:
            # Поддержка Search Platform 2.0 (offers и canonical_products)
            pid = str((payload.get("product") or {}).get("id") or "")
            off_id = str((payload.get("product") or {}).get("offer_id") or "")
            target_price = int(payload.get("price") or 0)
            row = conn.execute(
                "SELECT price FROM offers WHERE (id = ? OR product_id = ?) AND is_active = 1 AND price = ?",
                (off_id or pid, pid, target_price)
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT MIN(price) FROM offers WHERE product_id = ? AND is_active = 1",
                    (pid,)
                ).fetchone()
            if row and row[0] is not None:
                current = (row[0],)
    # Наблюдение удалено или выключено, человек отключил Telegram, цена уже другая — сообщение неактуально
    if (not user or user["is_blocked"] or bot_unreachable(user)
            or not user["settings"].get("telegram_notify_enabled")
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
            if not price:
                pid = str((other_payload.get("product") or {}).get("id") or "")
                off_id = str((other_payload.get("product") or {}).get("offer_id") or "")
                p_val = int(other_payload.get("price") or 0)
                row = conn.execute(
                    "SELECT price FROM offers WHERE (id = ? OR product_id = ?) AND is_active = 1 AND price = ?",
                    (off_id or pid, pid, p_val)
                ).fetchone()
                if row and row[0] is not None:
                    price = (row[0],)
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
        if len(deliveries) == 1:     # сводку из нескольких срабатываний одной правкой не погасить
            _remember_message(deliveries[0]["id"], result)
        counts["sent"] += 1
        with get_connection() as conn:
            for done in payloads:
                conn.execute("UPDATE watch_events SET status = 'sent' WHERE id = ?", (done.get("event_id"),))
            # Одноразовое наблюдение выключается ПОСЛЕ отправки: своё сообщение оно должно успеть доставить
            conn.execute("UPDATE watches SET is_active = 0 WHERE id = ? AND repeat_mode = 0", (watch["id"],))
            conn.commit()
        return True, None
    if result.status == "permanent":
        note_delivery_result(user["id"], result)
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
    keyboard = [[site_button("🩺 Центр мониторинга", APP_URL.rstrip("/") + "/monitoring")]] if APP_URL else []
    try:
        return classify_telegram_response(telegram_api("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": make_link_preview_options(is_disabled=True),
            "reply_markup": {"inline_keyboard": keyboard}}))
    except Exception as e:
        print(f"[Telegram Exception] {type(e).__name__}")
        return DeliveryResult("retry", None, type(e).__name__)


def _deliver_daily_digest(item, payload, counts) -> tuple:
    """Отправка суточной сводки: та же очередь, те же повторы и та же пауза 429."""
    from database import finish_notification, get_user, bot_unreachable

    user = get_user(item["user_id"])
    # Сводка — админские цифры: если человек больше не администратор или заблокирован, письмо отменяется
    if (not user or user["is_blocked"] or bot_unreachable(user)
            or int(item["user_id"]) not in config.ADMIN_TELEGRAM_IDS):
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
        note_delivery_result(user["id"], result)
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
                          fresh_benchmark_clause, bot_unreachable)
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
            if (not user or user["is_blocked"] or bot_unreachable(user)
                or not user["settings"].get("telegram_notify_enabled")
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
                _remember_message(item["id"], result)
                sent += 1
                counts["sent"] += 1
            elif result.status == "permanent":
                note_delivery_result(user["id"], result)
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


def _remember_message(delivery_id: int, result: DeliveryResult) -> None:
    """Сбой записи номера сообщения не должен превращать доставленное уведомление в ошибку."""
    try:
        from database import record_sent_message
        record_sent_message(delivery_id, result.message_id, result.has_photo)
    except Exception as e:
        print(f"[Telegram] Номер сообщения не записан: {type(e).__name__}")


# --- TG-06: погашение устаревших находок -------------------------------------------------------------

STALE_PASS_INTERVAL_SECONDS = 300    # сверка раз в пять минут: цены меняются обходами, а не посекундно
STALE_EDITS_PER_PASS = 5             # правка считается отправленным сообщением — не забиваем лимит рассылки
_last_stale_pass = 0.0


def stale_verdict(item: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
    """Почему находка устарела: "gone" — товар снят, "price" — цена выше обещанной; None — ещё в силе
    или судить рано (цена товара давно не обновлялась — сбой обхода не делает находку ложной)."""
    kind = payload.get("kind")
    if kind == "watch":
        promised = int(payload.get("price") or 0)
    elif kind is None and isinstance(payload.get("anomaly"), dict):
        promised = int(payload["anomaly"].get("new_price") or 0)
    else:
        return None                      # сводки и служебные письма не гасятся
    if promised <= 0 or not item.get("product_found"):
        return None                      # товар из другой модели данных (offers) — фактов нет, не трогаем
    if not item.get("product_active"):
        return "gone"
    if item.get("product_fresh") and int(item.get("current_price") or 0) > promised:
        return "price"
    return None


def stale_message(payload: Dict[str, Any], verdict: str, current_price: int) -> tuple:
    """Текст и кнопки погашенной находки. Короткий: подпись к фото ограничена 1024 символами."""
    product = payload.get("product") or {}
    shop = product.get("shop") or "Магазин"
    promised = int(payload.get("price") or (payload.get("anomaly") or {}).get("new_price") or 0)
    lines = ["⚠️ <b>Находка уже неактуальна</b>", "",
             f"{_shop_emoji(shop)} <b>{html.escape(shop)}</b>",
             f"🏷 {html.escape(str(product.get('title') or '')[:300])}", "",
             f"Была цена: <s>{format_price(promised)}</s>"]
    lines.append("Сейчас: товара нет в продаже" if verdict == "gone"
                 else f"Сейчас: <b>{format_price(current_price)}</b>")
    keyboard = []
    url = str(product.get("url") or "")
    if url.startswith(("http://", "https://")):
        keyboard.append([{"text": "Открыть товар", "url": url}])
    if APP_URL:
        keyboard.append([site_button("🔎 Искать другие цены", APP_URL)])
    return "\n".join(lines), keyboard


def mark_stale_alerts(force: bool = False) -> int:
    """Правит отправленные находки, которые перестали быть правдой. Возвращает число правок.

    Проход редкий и короткий; пауза Telegram по 429 соблюдается так же, как при доставке.
    """
    global _last_stale_pass
    if not config.stale_alert_marking_enabled() or not get_bot_token() or telegram_paused_for() > 0:
        return 0
    if not force and time.monotonic() - _last_stale_pass < STALE_PASS_INTERVAL_SECONDS:
        return 0
    _last_stale_pass = time.monotonic()
    from database import stale_alert_candidates, finish_stale_check, get_user, bot_unreachable
    edited = 0
    for item in stale_alert_candidates():
        if edited >= STALE_EDITS_PER_PASS:
            break
        try:
            payload = json.loads(item["payload"])
        except (TypeError, ValueError):
            finish_stale_check(item["id"])
            continue
        verdict = stale_verdict(item, payload)
        if not verdict:
            continue                     # ещё в силе — посмотрим в следующий проход
        user = get_user(item["user_id"])
        if not user or user["is_blocked"] or bot_unreachable(user):
            finish_stale_check(item["id"])
            continue
        text, keyboard = stale_message(payload, verdict, item["current_price"])
        method, field = ("editMessageCaption", "caption") if item["tg_has_photo"] else ("editMessageText", "text")
        try:
            result = classify_telegram_response(telegram_api(method, {
                "chat_id": item["user_id"], "message_id": item["tg_message_id"], field: text,
                "parse_mode": "HTML", "reply_markup": {"inline_keyboard": keyboard}}))
        except Exception as e:
            print(f"[Telegram Exception] {type(e).__name__}")
            break                        # сеть недоступна — попробуем в следующий проход
        if result:
            edited += 1
            finish_stale_check(item["id"])
        elif result.status == "permanent":
            # Сообщение удалено человеком, бот заблокирован или править нельзя — возвращаться незачем
            note_delivery_result(item["user_id"], result)
            finish_stale_check(item["id"])
        elif result.retry_after is not None:
            pause_telegram(result.retry_after)
            break
    return edited


def _check_shop_notices() -> None:
    """Проверяет, не обратился ли магазин к тем, кто его парсит (shop_notices.py).

    Список магазинов берётся из реестра, чтобы новый магазин попадал под наблюдение сам собой,
    без отдельной правки. Проверка тяжёлая, поэтому внутри она сама решает, пора ли.
    """
    import re
    import shop_notices
    from web.server import SHOP_REGISTRY

    hosts, names = {}, {}
    for key, value in SHOP_REGISTRY.items():
        categories = value[1] if len(value) > 1 else []
        names[key] = value[2] if len(value) > 2 else key
        for row in categories or []:
            url = row.get("url") if isinstance(row, dict) else None
            match = re.match(r"https?://([^/]+)", url or "")
            if match:
                hosts[key] = match.group(1)
                break
    if not hosts:
        return
    result = shop_notices.check_and_queue(hosts, names)
    if result.get("skipped"):
        return
    print(f"[Notices] Проверено магазинов: {result['checked']}, "
          f"новых обращений: {len(result['changed'])}, "
          f"не открылись: {len(result['unreachable'])}")


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
            # Обращения магазинов к тем, кто их парсит: раз в неделю, см. shop_notices.py.
            # Отказ проверки не должен мешать доставке уведомлений — она здесь главная.
            try:
                await asyncio.to_thread(_check_shop_notices)
            except Exception as e:
                print(f"[Notices] Проверка обращений магазинов не выполнена: {type(e).__name__}")
            await asyncio.to_thread(deliver_pending)
            # TG-06: находки, переставшие быть правдой, правятся на месте; сбой не мешает доставке
            try:
                await asyncio.to_thread(mark_stale_alerts)
            except Exception as e:
                print(f"[Telegram] Погашение находок не выполнено: {type(e).__name__}")
            # Пульс очереди Telegram (P14): умерший воркер не должен выглядеть работающим
            await asyncio.to_thread(environment.heartbeat, environment.TELEGRAM, "цикл доставки")
        except Exception as e:
            print(f"[Telegram Queue] {type(e).__name__}")
            from telemetry import telemetry, COMPONENT_TELEGRAM
            telemetry.record_system_error(COMPONENT_TELEGRAM, "notification_worker", e)
        await asyncio.sleep(10)
