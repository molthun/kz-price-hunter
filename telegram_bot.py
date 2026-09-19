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
        "• <b>/status</b> — проверка статуса базы и нейросети\n\n"
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
    elif cmd in ("/search", "/find", "/price"):
        query = text[len(cmd):].strip()
        await handle_search_command(session, token, chat_id, query)
    elif cmd in ("/ai", "/ask"):
        query = text[len(cmd):].strip()
        await handle_ai_consultant_message(session, token, chat_id, query)
    else:
        # Любое обычное сообщение без слеша считается вопросом к AI-консультанту
        await handle_ai_consultant_message(session, token, chat_id, text)


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
            chat_id = (update.get("message") or {}).get("chat", {}).get("id")
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
    token = get_bot_token()
    if not token:
        print("[Telegram Bot] ⚠️ TELEGRAM_BOT_TOKEN не задан — интерактивный бот отключен")
        return
    print("[Telegram Bot] 🤖 Интерактивный Telegram-бот запущен (личные чаты)")
    offset = 0
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        dispatcher = TelegramDispatcher(session, token)
        try:
            while True:
                try:
                    poll_url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=30"
                    async with session.get(poll_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for update in data.get("result", []):
                                await dispatcher.submit(update)
                                offset = max(offset, update.get("update_id", 0) + 1)
                        elif resp.status in (401, 404):
                            print("[Telegram Bot] Ошибка токена Telegram (HTTP 401/404). Бот остановлен.")
                            break
                        elif resp.status == 409:
                            print("[Telegram Bot] Конфликт polling/webhook (HTTP 409), ожидание 15 с")
                            await asyncio.sleep(15)
                        else:
                            await asyncio.sleep(5)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    from telemetry import telemetry, COMPONENT_TELEGRAM, SEVERITY_WARNING
                    telemetry.record_system_error(COMPONENT_TELEGRAM, "telegram_polling", e, severity=SEVERITY_WARNING)
                    await asyncio.sleep(5)
        finally:
            await dispatcher.close()
