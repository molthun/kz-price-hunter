"""
telegram_bot.py - Интерактивный Telegram-бот для KZ Price Hunter.

Реализует:
1. Фоновый Long Polling через aiohttp (без внешних зависимостей типа aiogram/telebot).
2. Обработка команд:
   - /start, /help - приветствие и возможности бота
   - /ai <вопрос> или обычные текстовые сообщения - персональный AI-консультант по покупкам (RAG)
   - /search <запрос> - поиск лучших цен по подключённым магазинам Казахстана
   - /status - проверка состояния AI и базы данных
3. Карточки рекомендаций с кликабельными Inline-кнопками магазинов и цен.
4. Безопасная работа: фоновый таск веб-сервера, мгновенный выход при отсутствии токена,
   graceful shutdown при перезапуске сервера.
"""

import html
import os
import time
import asyncio
import re
from typing import Dict, Any, List, Optional
import aiohttp

import config
from config import get_bot_token, APP_URL
from notifier import _shop_emoji, format_price
import ai_service
from bounded_cache import BoundedTTLCache
from auth import RateLimiter
from database import get_user

_message_limiter = RateLimiter(max_calls=10, period=60)
from search_engine import search_in_database
import telegram_widgets

# Пилот TGW-01: карточка /search виджетом. Запросы карточки ограничены отдельно от сообщений,
# иначе нажатия в карточке съедали бы лимит переписки с консультантом.
_widget_limiter = RateLimiter(max_calls=20, period=60)
WIDGET_CARD_TTL_SECONDS = 6 * 60 * 60
WIDGET_REFRESH_COOLDOWN_SECONDS = 30
# (chat_id, message_id) -> запрос и предложения карточки. Только в памяти процесса: после перезапуска
# карточка честно отвечает «повторите поиск», а текст запроса не попадает в базу.
_widget_cards = BoundedTTLCache(maxsize=2000, ttl=WIDGET_CARD_TTL_SECONDS)


def _markdown_to_telegram_html(text: str) -> str:
    """Преобразует базовый Markdown (**жирный**, *курсив*, `код`) в валидный Telegram HTML."""
    if not text:
        return ""
    
    # 1. Экранируем HTML спецсимволы
    escaped = html.escape(text)

    # 2. Восстанавливаем заголовки ### Заголовок -> <b>Заголовок</b>
    escaped = re.sub(r'(?m)^#{1,6}\s*(.+)$', r'<b>\1</b>', escaped)

    # 3. Жирный: **текст** -> <b>текст</b>
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)

    # 4. Курсив: *текст* -> <i>текст</i>
    escaped = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', escaped)

    # 5. Код: `код` -> <code>код</code>
    escaped = re.sub(r'`([^`]+?)`', r'<code>\1</code>', escaped)

    return escaped


async def send_tg_message(session: aiohttp.ClientSession, token: str, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None):
    """Отправляет текстовое сообщение пользователю через Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                # Если Telegram не принял HTML из-за вложенных тегов, повторяем чистым текстом
                raw_text = re.sub(r'<[^>]+>', '', text)
                payload["text"] = raw_text
                payload.pop("parse_mode", None)
                await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10))
    except Exception as e:
        print(f"[Telegram Bot] Ошибка отправки сообщения: {type(e).__name__}")


async def send_tg_widget(session: aiohttp.ClientSession, token: str, chat_id: int, widget_html: str,
                         caption: str, reply_markup: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Отправляет HTML-виджет (Bot API 10.4). Возвращает message_id или None — тогда нужен обычный текст."""
    url = f"https://api.telegram.org/bot{token}/sendWidget"
    payload: Dict[str, Any] = {"chat_id": chat_id, "html": widget_html, "caption": caption,
                               "parse_mode": "HTML", "allow_queries": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None) if resp.status == 200 else {}
            message_id = (data.get("result") or {}).get("message_id") if data.get("ok") is True else None
            if not isinstance(message_id, int):
                print(f"[Telegram Bot] sendWidget отклонён: HTTP {resp.status}")
                return None
            return message_id
    except Exception as e:
        print(f"[Telegram Bot] Ошибка sendWidget: {type(e).__name__}")
        return None


async def answer_widget_query(session: aiohttp.ClientSession, token: str, widget_query_id: str, text: str = ""):
    """Ответ карточке: до 1024 байт, не позже 15 секунд после нажатия."""
    payload: Dict[str, Any] = {"widget_query_id": str(widget_query_id)}
    if text:
        payload["data"] = text.encode("utf-8")[:1024].decode("utf-8", "ignore")
    try:
        async with session.post(f"https://api.telegram.org/bot{token}/answerWidgetQuery", json=payload,
                                timeout=aiohttp.ClientTimeout(total=5)):
            pass
    except Exception as e:
        print(f"[Telegram Bot] Ошибка answerWidgetQuery: {type(e).__name__}")


async def edit_tg_widget_data(session: aiohttp.ClientSession, token: str, chat_id: int, message_id: int,
                              chat_data: str) -> bool:
    """Меняет chat_data карточки. «Не изменилось» — тоже успех: в карточке уже эти данные."""
    payload = {"chat_id": chat_id, "message_id": message_id, "chat_data": chat_data}
    try:
        async with session.post(f"https://api.telegram.org/bot{token}/editMessageWidgetData", json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return True
            body = await resp.text()
            return "message is not modified" in body
    except Exception as e:
        print(f"[Telegram Bot] Ошибка editMessageWidgetData: {type(e).__name__}")
        return False


async def answer_callback_query(session: aiohttp.ClientSession, token: str, callback_query_id: str, text: str = "", show_alert: bool = False):
    """Отправляет подтверждение на нажатие inline-кнопки в Telegram."""
    if not session or not token or not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload: Dict[str, Any] = {"callback_query_id": str(callback_query_id)}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)):
            pass
    except Exception as e:
        print(f"[Telegram Bot] Ошибка answerCallbackQuery: {type(e).__name__}")


async def send_tg_chat_action(session: aiohttp.ClientSession, token: str, chat_id: int, action: str = "typing"):
    """Показывает статус 'набирает сообщение...' пока AI думает."""
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    try:
        await session.post(url, json={"chat_id": chat_id, "action": action}, timeout=aiohttp.ClientTimeout(total=5))
    except Exception:
        pass


async def handle_start_command(session: aiohttp.ClientSession, token: str, chat_id: int, user_first_name: str = ""):
    greeting = f"Привет, {html.escape(user_first_name)}! " if user_first_name else "Привет! "
    text = (
        f"👋 {greeting}Я умный бот-ассистент <b>KZ Price Hunter</b> 🇰🇿\n\n"
        f"Я отслеживаю цены, скидки и ошибки в крупнейших магазинах Казахстана (подключено: <b>{len(config.SHOP_KEYS)}</b>) "
        "(электроника, продукты, стройка, товары для дома: Kaspi, DNS, Белый Ветер, Лемана ПРО, Arbuz, 12 Месяцев, Мечта и др.) "
        "и помогаю сделать самую выгодную покупку с помощью AI!\n\n"
        "📌 <b>Что я умею:</b>\n"
        "• <b>Задайте любой вопрос</b> — напишите, например:\n"
        "  <i>«Посоветуй игровой ноутбук до 350к»</i>\n"
        "  <i>«Какой iPhone сейчас выгоднее брать?»</i>\n"
        "  <i>«Что выбрать: RTX 4060 или RX 7600?»</i>\n"
        "  Я проанализирую реальные цены в магазинах и дам совет эксперта!\n\n"
        "• <b>/search &lt;товар&gt;</b> — мгновенная проверка минимальной цены по магазинам Казахстана\n"
        "• <b>/ai &lt;вопрос&gt;</b> — вызов AI-консультанта. Он помнит разговор 30 минут: можно уточнять "
        "(<i>«а подешевле?»</i>, <i>«сравни первые два»</i>)\n"
        "• <b>/new</b> — начать новый разговор с консультантом\n"
        "• <b>/status</b> — проверка статуса базы и нейросети\n"
        "• <b>/watches</b> — просмотр и управление вашими наблюдениями за ценами\n\n"
        "🔒 Я отвечаю только в личном чате. Вопрос и контекст диалога передаются настроенному AI-провайдеру. "
        "Для поиска без AI используйте /search; /new очищает текущий контекст.\n\n"
        "🔔 Уведомления включаются в личных настройках сайта."
    )
    keyboard = []
    if APP_URL:
        keyboard.append([{"text": "📊 Открыть дашборд цен", "url": APP_URL}])
    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    await send_tg_message(session, token, chat_id, text, reply_markup)


async def handle_status_command(session: aiohttp.ClientSession, token: str, chat_id: int):
    ai_conf = config.get_ai_config()
    ai_status = f"✅ Активен ({ai_conf['provider']})" if ai_conf["configured"] and ai_conf["enabled"] else "⚠️ Не настроен"
    
    text = (
        "📊 <b>Статус системы KZ Price Hunter</b>\n\n"
        f"🧠 <b>AI-консультант:</b> {ai_status}\n"
        f"🏪 <b>Подключенных сетей:</b> {len(config.SHOP_KEYS)}\n"
        "📍 <b>Основной регион сбора:</b> Астана / Алматы / Доставка по РК\n\n"
        "Чтобы получить совет по выбору товара, просто напишите мне свой вопрос!"
    )
    await send_tg_message(session, token, chat_id, text)


async def handle_watches_command(session: aiohttp.ClientSession, token: str, chat_id: int):
    """Показывает список активных наблюдений за ценами пользователя и позволяет отключить их."""
    from database import list_watches
    watches = list_watches(chat_id, only_active=True)
    if not watches:
        text = (
            "🔕 <b>У вас нет активных наблюдений за ценами.</b>\n\n"
            "Вы можете настроить отслеживание снижения цен в карточке любого товара на сайте или через поиск бота."
        )
        keyboard = []
        if APP_URL:
            keyboard.append([{"text": "🔍 Перейти в каталог", "url": APP_URL}])
        reply_markup = {"inline_keyboard": keyboard} if keyboard else None
        await send_tg_message(session, token, chat_id, text, reply_markup)
        return

    text = f"🔔 <b>Ваши активные наблюдения ({len(watches)}):</b>\n\n"
    keyboard = []
    for idx, w in enumerate(watches[:15], 1):
        desc = w.get("description") or w.get("title") or f"Наблюдение #{w['id']}"
        text += f"{idx}. <b>{html.escape(desc)}</b>\n"
        w_title = (w.get("title") or f"#{w['id']}")[:24]
        keyboard.append([{"text": f"🔕 Отключить: {w_title}", "callback_data": f"unwatch:{w['id']}"}])

    if len(watches) > 15:
        text += f"\n<i>(Показаны первые 15 из {len(watches)})</i>\n"

    if APP_URL:
        keyboard.append([{"text": "⚙️ Все наблюдения на сайте", "url": APP_URL}])

    reply_markup = {"inline_keyboard": keyboard}
    await send_tg_message(session, token, chat_id, text, reply_markup)


async def _send_search_widget(session: aiohttp.ClientSession, token: str, chat_id: int, query: str,
                              items: List[Dict[str, Any]]) -> bool:
    """Карточка-виджет вместо текста. False — карточки нет, вызывающий код отправляет текст как раньше."""
    built = telegram_widgets.build_search_widget(query, items)
    if not built:
        return False
    widget_html, offers = built
    keyboard = []
    if APP_URL:
        keyboard.append([{"text": "📊 Все результаты на сайте", "url": f"{APP_URL}?tab=best-price&q={html.escape(query)}"}])
    message_id = await send_tg_widget(session, token, chat_id, widget_html,
                                      telegram_widgets.build_caption(query, offers),
                                      {"inline_keyboard": keyboard} if keyboard else None)
    if message_id is None:
        return False
    _widget_cards[(chat_id, message_id)] = {"query": query, "offers": offers, "refreshed": 0.0}
    return True


async def handle_widget_query(session: aiohttp.ClientSession, token: str, wq: Dict[str, Any]):
    """Нажатие в карточке: «Следить» или «Обновить». Отвечаем всегда — иначе карточка ждёт 15 секунд."""
    wq_id = str(wq.get("id") or "")
    from_user = wq.get("from") or {}
    user_id = from_user.get("id")
    chat = wq.get("chat") or {}
    chat_id = chat.get("id")
    message_id = wq.get("message_id")
    # Тот же контур, что у сообщений и кнопок: только личный чат самого пользователя
    if (not wq_id or chat.get("type") != "private" or not isinstance(user_id, int) or isinstance(user_id, bool)
            or user_id <= 0 or chat_id != user_id or from_user.get("is_bot") or not isinstance(message_id, int)):
        return
    user = get_user(user_id)
    if user and user.get("is_blocked"):
        return
    if _widget_limiter.retry_after(str(user_id)):
        await answer_widget_query(session, token, wq_id, "Слишком часто, подождите минуту")
        return
    card = _widget_cards.get((chat_id, message_id))
    if not card:
        await answer_widget_query(session, token, wq_id, "Карточка устарела — повторите /search")
        return
    action = str(wq.get("data") or "")

    if action == telegram_widgets.REQUEST_WATCH:
        if not user:
            await answer_widget_query(session, token, wq_id, "Сначала войдите на сайте — там включаются уведомления")
            return
        try:
            from database import create_watch
            watch = await asyncio.to_thread(create_watch, user_id, {
                "kind": "search", "target": card["query"], "condition": "any_drop"})
        except ValueError as e:
            await answer_widget_query(session, token, wq_id, str(e))
            return
        except Exception as e:
            print(f"[Telegram Bot] Ошибка создания наблюдения из карточки: {type(e).__name__}")
            await answer_widget_query(session, token, wq_id, "Не получилось включить наблюдение")
            return
        await answer_widget_query(session, token, wq_id,
                                  "Вы уже следите за этим запросом" if watch.get("already_existed")
                                  else "Наблюдение включено — напишу, когда цена снизится")
        return

    if action == telegram_widgets.REQUEST_REFRESH:
        if time.monotonic() - card["refreshed"] < WIDGET_REFRESH_COOLDOWN_SECONDS:
            await answer_widget_query(session, token, wq_id, "Цены только что обновлены")
            return
        try:
            items = await asyncio.to_thread(search_in_database, card["query"], city="Все", sort_by="price_asc")
            prices = {str(it.get("id") or ""): int(it.get("current_price") or 0) for it in items}
            chat_data = telegram_widgets.build_chat_data(card["offers"], prices)
        except Exception as e:
            print(f"[Telegram Bot] Ошибка обновления карточки: {type(e).__name__}")
            await answer_widget_query(session, token, wq_id, "Не получилось обновить цены")
            return
        updated = await edit_tg_widget_data(session, token, chat_id, message_id, chat_data)
        if updated:
            card["refreshed"] = time.monotonic()
        await answer_widget_query(session, token, wq_id, "Цены обновлены" if updated else "Не получилось обновить цены")
        return

    await answer_widget_query(session, token, wq_id)


async def handle_search_command(session: aiohttp.ClientSession, token: str, chat_id: int, query: str):
    q = query.strip()
    if not q:
        await send_tg_message(session, token, chat_id, "ℹ️ Пожалуйста, укажите название товара: <code>/search iPhone 15</code> или <code>/search RTX 4060</code>")
        return

    await send_tg_chat_action(session, token, chat_id, "typing")
    
    started = time.monotonic()
    items, outcome = [], "error"
    try:
        items = search_in_database(q, city="Все", sort_by="price_asc")
        outcome = "found" if items else "not_found"
    finally:
        from search_engine import _record_search
        _record_search("telegram", q, started, outcome, len(items))
    if not items:
        await send_tg_message(session, token, chat_id, f"😕 По запросу «<b>{html.escape(q)}</b>» ничего не найдено в локальной базе. Попробуйте сократить запрос.")
        return

    # Пилот TGW-01: карточка-виджет; при любом отказе — прежнее текстовое сообщение ниже
    if telegram_widgets.search_widget_enabled(chat_id) and await _send_search_widget(session, token, chat_id, q, items):
        return

    top = items[:5]
    lines = [f"🔍 <b>Лучшие цены по запросу «{html.escape(q)}»:</b>\n"]
    keyboard = []

    for idx, it in enumerate(top, 1):
        price = format_price(it.get("current_price", 0))
        shop = it.get("shop", "Магазин")
        emoji = _shop_emoji(shop)
        title = html.escape(it.get("title", ""))
        lines.append(f"{idx}. {emoji} <b>{price}</b> — {title} (<i>{shop}</i>)")
        if it.get("url") and it["url"].startswith("http"):
            keyboard.append([{"text": f"{emoji} {shop}: {price}", "url": it["url"]}])

    if APP_URL:
        keyboard.append([{"text": "📊 Все результаты на сайте", "url": f"{APP_URL}?tab=best-price&q={html.escape(q)}"}])

    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    await send_tg_message(session, token, chat_id, "\n".join(lines), reply_markup)


# Память диалога консультанта в Telegram: chat_id -> (время последней реплики, реплики)
CHAT_HISTORY_TTL_SECONDS = 30 * 60
# Истёкшие диалоги удаляются сами, а не при следующем сообщении того же чата (M03)
_chat_histories = BoundedTTLCache(maxsize=5000, ttl=CHAT_HISTORY_TTL_SECONDS)


def _get_chat_history(chat_id: int) -> List[Dict[str, str]]:
    entry = _chat_histories.get(chat_id)
    if not entry or time.monotonic() - entry[0] > CHAT_HISTORY_TTL_SECONDS:
        _chat_histories.pop(chat_id, None)
        return []
    return list(entry[1])


def _remember_turn(chat_id: int, question: str, history_turn: str) -> None:
    turns = _get_chat_history(chat_id)
    turns += [{"role": "user", "content": question}, {"role": "model", "content": history_turn}]
    _chat_histories[chat_id] = (time.monotonic(), ai_service.normalize_history(turns))


def reset_chat_history(chat_id: int) -> None:
    _chat_histories.pop(chat_id, None)


async def handle_ai_consultant_message(session: aiohttp.ClientSession, token: str, chat_id: int, query: str):
    """Обработка вопроса AI-консультанту с подбором товаров."""
    q = query.strip()
    if not q:
        await send_tg_message(session, token, chat_id, "ℹ️ Напишите ваш вопрос, например: <code>/ai посоветуй планшет до 150 тыс тенге</code>")
        return

    await send_tg_chat_action(session, token, chat_id, "typing")

    try:
        result = await ai_service.ask_ai_consultant(message=q, history=_get_chat_history(chat_id), city="Все")
    except Exception as e:
        print(f"[Telegram Bot] Ошибка AI-консультанта: {type(e).__name__}")
        await send_tg_message(session, token, chat_id, "⚠️ Не удалось получить ответ от AI. Пожалуйста, попробуйте еще раз.")
        return

    answer_raw = result.get("answer", "")
    answer_html = _markdown_to_telegram_html(answer_raw)
    products = result.get("products") or []
    if answer_raw:
        _remember_turn(chat_id, q, result.get("history_turn") or answer_raw)

    keyboard = []
    # Добавляем до 4 кнопок со ссылками на рекомендованные товары
    for it in products[:4]:
        shop = it.get("shop", "Магазин")
        p_str = format_price(it.get("price", 0))
        emoji = _shop_emoji(shop)
        url = it.get("url")
        if url and url.startswith("http"):
            label = f"{emoji} {shop}: {p_str}"
            keyboard.append([{"text": label, "url": url}])

    if APP_URL and result.get("query_used"):
        keyboard.append([{"text": "📊 Смотреть на сайте", "url": APP_URL}])

    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    await send_tg_message(session, token, chat_id, f"✨ <b>AI-Консультант:</b>\n\n{answer_html}", reply_markup)


async def process_telegram_update(session: aiohttp.ClientSession, token: str, update: Dict[str, Any]):
    """Обрабатывает одно входящее событие от Telegram."""
    cq = update.get("callback_query")
    if cq:
        cq_id = str(cq.get("id") or "")
        from_user = cq.get("from") or {}
        user_id = from_user.get("id")
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")

        if chat.get("type") != "private" or not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0 or chat_id != user_id or from_user.get("is_bot"):
            return
        user = get_user(user_id)
        if user and user.get("is_blocked"):
            return
        data = str(cq.get("data") or "").strip()
        if data.startswith("unwatch:"):
            try:
                watch_id = int(data.split(":", 1)[1])
                from database import delete_watch
                deleted = delete_watch(user_id, watch_id)
                if deleted:
                    await answer_callback_query(session, token, cq_id, text="🔕 Наблюдение отключено")
                    await send_tg_message(session, token, chat_id, "🔕 <b>Наблюдение отключено</b>\nВы больше не будете получать сообщения по этому товару.")
                else:
                    await answer_callback_query(session, token, cq_id, text="Наблюдение уже не активно")
            except Exception as e:
                await answer_callback_query(session, token, cq_id, text="Ошибка при отключении")
        return

    wq = update.get("widget_query")
    if wq:
        await handle_widget_query(session, token, wq)
        return

    msg = update.get("message")
    if not msg:
        return

    text = str(msg.get("text") or "").strip()
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    from_user = msg.get("from", {})
    first_name = from_user.get("first_name", "")

    # Never share a group/channel context or accept sender_chat on behalf of a user.
    user_id = from_user.get("id")
    if chat.get("type") != "private" or not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0 or chat_id != user_id or from_user.get("is_bot"):
        return
    if not text:
        return
    user = get_user(user_id)
    if user and user.get("is_blocked"):
        return
    if len(text) > 4000 or _message_limiter.retry_after(str(user_id)):
        return

    # Команды
    cmd = text.split()[0].lower() if text.startswith("/") else ""

    if cmd in ("/start", "/help", "/new"):
        reset_chat_history(chat_id)
        await handle_start_command(session, token, chat_id, first_name)
    elif cmd in ("/status", "/info"):
        await handle_status_command(session, token, chat_id)
    elif cmd in ("/watches", "/my_watches", "/subs", "/tracking"):
        await handle_watches_command(session, token, chat_id)
    elif cmd in ("/search", "/find", "/price"):
        query = text[len(cmd):].strip()
        await handle_search_command(session, token, chat_id, query)
    elif cmd in ("/ai", "/ask"):
        query = text[len(cmd):].strip()
        await handle_ai_consultant_message(session, token, chat_id, query)
    else:
        # Любое обычное сообщение без слеша считается вопросом к AI-консультанту
        await handle_ai_consultant_message(session, token, chat_id, text)


# TG-05: меню бота. Команды видны по «/», кнопка меню открывает раздел сайта внутри Telegram (Mini App):
# вход там происходит сам — сайт принимает initData через /api/auth/telegram-webapp.
BOT_COMMANDS = [
    ("search", "Лучшие цены на товар"),
    ("ai", "Спросить AI-консультанта"),
    ("watches", "Мои наблюдения за ценами"),
    ("new", "Новый разговор с консультантом"),
    ("status", "Состояние сервиса"),
    ("help", "Что умеет бот"),
]
# Main App (кнопка «Открыть» в профиле бота) уже ведёт на главную сайта, поэтому кнопка меню открывает
# отдельный раздел. Разделы сайта, доступные по прямому адресу: /deals, /search, /favorites, /profile.
MENU_BUTTON_DEFAULT_PATH = "/deals"
MENU_BUTTON_DEFAULT_TEXT = "Скидки"
_MENU_PATH_RE = re.compile(r"^/[A-Za-z0-9/_\-]{0,60}$")


def menu_button_target() -> tuple:
    """Раздел и подпись кнопки меню; непригодные значения заменяются умолчаниями."""
    path = os.getenv("TELEGRAM_MENU_BUTTON_PATH", "").strip() or MENU_BUTTON_DEFAULT_PATH
    text = " ".join(os.getenv("TELEGRAM_MENU_BUTTON_TEXT", "").split())[:16] or MENU_BUTTON_DEFAULT_TEXT
    return (path if _MENU_PATH_RE.match(path) else MENU_BUTTON_DEFAULT_PATH), text


def menu_button_mode() -> str:
    """off (по умолчанию) | commands | webapp. Неизвестное значение — off: меню бота видят все пользователи."""
    mode = os.getenv("TELEGRAM_MENU_BUTTON", "").strip().lower()
    return mode if mode in ("commands", "webapp") else "off"


def menu_setup_calls(mode: str, app_url: str) -> List[tuple]:
    """Какие методы Bot API вызвать для выбранного режима. Mini App открывается только по https."""
    if mode == "off":
        return []
    calls = [("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]})]
    if mode == "webapp" and app_url.startswith("https://"):
        path, text = menu_button_target()
        button = {"type": "web_app", "text": text, "web_app": {"url": app_url.rstrip("/") + path}}
    else:
        button = {"type": "commands"}
    calls.append(("setChatMenuButton", {"menu_button": button}))
    return calls


async def setup_bot_menu(session: aiohttp.ClientSession, token: str) -> None:
    """Настраивает меню при запуске. Отказ не мешает работе бота: меню остаётся прежним."""
    for method, payload in menu_setup_calls(menu_button_mode(), config.PUBLIC_ORIGIN or APP_URL):
        try:
            async with session.post(f"https://api.telegram.org/bot{token}/{method}", json=payload,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    print(f"[Telegram Bot] {method} отклонён: HTTP {resp.status}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Telegram Bot] Ошибка {method}: {type(e).__name__}")


class TelegramDispatcher:
    """Bounded work and ordered turns per private chat; all workers owned by polling."""
    def __init__(self, session, token, workers=4, capacity=64):
        self.session, self.token = session, token
        self.queue = asyncio.Queue(maxsize=capacity)
        self.chat_locks = {}
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(workers)]

    async def submit(self, update):
        await self.queue.put(update)  # backpressure instead of unbounded create_task

    async def _worker(self):
        while True:
            update = await self.queue.get()
            # У widget_query чат лежит в самом запросе: очередь нажатий идёт в общий порядок чата
            msg = (update.get("message") or (update.get("callback_query") or {}).get("message")
                   or update.get("widget_query"))
            chat_id = (msg or {}).get("chat", {}).get("id")
            lock, count = self.chat_locks.get(chat_id, (asyncio.Lock(), 0))
            self.chat_locks[chat_id] = (lock, count + 1)
            try:
                async with lock:
                    await process_telegram_update(self.session, self.token, update)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(f"[Telegram Bot] Ошибка обработки сообщения: {type(error).__name__}")
            finally:
                lock, count = self.chat_locks[chat_id]
                if count == 1:
                    del self.chat_locks[chat_id]
                else:
                    self.chat_locks[chat_id] = (lock, count - 1)
                self.queue.task_done()

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.chat_locks.clear()


async def run_telegram_bot_task():
    """One polling owner; cancel/await message workers before closing HTTP session."""
    from telegram_admin import mark_polling
    token = get_bot_token()
    if not token:
        mark_polling("unconfigured")
        print("[Telegram Bot] ⚠️ TELEGRAM_BOT_TOKEN не задан — интерактивный бот отключен")
        return
    mark_polling("starting")
    print("[Telegram Bot] 🤖 Интерактивный Telegram-бот запущен (личные чаты)")
    offset = 0
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        dispatcher = TelegramDispatcher(session, token)
        await setup_bot_menu(session, token)
        try:
            while True:
                try:
                    poll_url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=30"
                    async with session.get(poll_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            mark_polling("ok" if data.get("ok") is True else "remote_error")
                            for update in data.get("result", []):
                                await dispatcher.submit(update)
                                offset = max(offset, update.get("update_id", 0) + 1)
                        elif resp.status in (401, 404):
                            mark_polling("unauthorized")
                            print("[Telegram Bot] Ошибка токена Telegram (HTTP 401/404). Бот остановлен.")
                            break
                        elif resp.status == 409:
                            mark_polling("conflict")
                            print("[Telegram Bot] Конфликт polling/webhook (HTTP 409), ожидание 15 с")
                            await asyncio.sleep(15)
                        else:
                            mark_polling("remote_error")
                            await asyncio.sleep(5)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    mark_polling("network_error")
                    from telemetry import telemetry, COMPONENT_TELEGRAM, SEVERITY_WARNING
                    telemetry.record_system_error(COMPONENT_TELEGRAM, "telegram_polling", e, severity=SEVERITY_WARNING)
                    await asyncio.sleep(5)
        finally:
            if __import__("telegram_admin").polling_view()["state"] != "unauthorized":
                mark_polling("stopped")
            await dispatcher.close()
