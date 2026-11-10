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
import itertools
import os
import time
import asyncio
import re
import base64
import urllib.parse
from typing import Dict, Any, List, Optional
import aiohttp

import config
from config import get_bot_token, APP_URL
from notifier import (
    _shop_emoji, format_price, site_button, url_button, callback_button, copy_button, button_style,
    tg_time_tag, expandable_quote, send_telegram_rich_message, is_safe_image_url, save_prepared_inline_message
)
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


def _site_search_url(query: str) -> str:
    """Адрес выдачи на сайте; запрос кодируется для URL (пробелы и кириллица ссылку не ломают)."""
    return f"{APP_URL}?tab=best-price&q={urllib.parse.quote(query, safe='')}"


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


async def send_tg_message(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    message_thread_id: Optional[int] = None,
):
    """Отправляет текстовое сообщение пользователю через Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
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


async def send_tg_rich_message(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: int,
    markdown: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    message_thread_id: Optional[int] = None,
) -> bool:
    """TG-03: отправляет структурированное Rich Message (Bot API 10.1+).
    Возвращает True при успехе, False при ошибке (вызывающий код делает fallback на send_tg_message).
    """
    if not config.rich_messages_enabled() or not markdown:
        return False
    url = f"https://api.telegram.org/bot{token}/sendRichMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown[:32768]},
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                return bool(data.get("ok"))
            err_text = await resp.text()
            print(f"[Telegram Bot] sendRichMessage отклонен ({resp.status}): {err_text[:160]}")
            return False
    except Exception as e:
        print(f"[Telegram Bot] Ошибка sendRichMessage: {type(e).__name__}")
        return False


_unsupported_topics_chats: Set[int] = set()


async def get_or_create_tg_topic(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: int,
    topic_type: str,
) -> Optional[int]:
    """TG-11: асинхронное получение или создание темы (форум-топика) в личном чате (Bot API 9.3/9.4)."""
    if not config.topics_mode_enabled() or chat_id in _unsupported_topics_chats:
        return None
    if not session or not hasattr(session, "post") or not token or not chat_id:
        return None
    from database import get_user_topic, save_user_topic
    try:
        existing = await asyncio.to_thread(get_user_topic, chat_id, topic_type)
        if existing:
            return existing
    except Exception:
        pass

    from notifier import TOPIC_METADATA
    meta = TOPIC_METADATA.get(topic_type)
    if not meta:
        return None

    url = f"https://api.telegram.org/bot{token}/createForumTopic"
    payload = {
        "chat_id": chat_id,
        "name": meta["name"],
        "icon_color": meta["icon_color"]
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if data.get("ok"):
                    tid = (data.get("result") or {}).get("message_thread_id")
                    if tid:
                        try:
                            await asyncio.to_thread(save_user_topic, chat_id, topic_type, tid)
                        except Exception:
                            pass
                        return tid
            if resp.status in (400, 403):
                _unsupported_topics_chats.add(chat_id)
    except Exception as e:
        print(f"[Telegram Bot] Ошибка создания темы {topic_type}: {type(e).__name__}")
    return None


async def send_tg_widget(session: aiohttp.ClientSession, token: str, chat_id: int, widget_html: str,
                         caption: str, reply_markup: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Отправляет HTML-виджет (Bot API 10.4). Возвращает message_id или None — тогда нужен обычный текст."""
    url = f"https://api.telegram.org/bot{token}/sendWidget"
    payload: Dict[str, Any] = {"chat_id": chat_id, "html": widget_html, "caption": caption,
                               "parse_mode": "HTML", "show_caption_above_media": True, "allow_queries": True}
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


async def send_tg_draft(session: aiohttp.ClientSession, token: str, chat_id: int, draft_id: int,
                        text: str = "", can_stop: bool = True, message_thread_id: Optional[int] = None) -> bool:
    """Черновик ответа (Bot API 9.3+): временный, живёт около 30 секунд. Пустой текст — заглушка «Думаю…»."""
    payload: Dict[str, Any] = {"chat_id": chat_id, "draft_id": draft_id, "text": text}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    if can_stop:
        payload["can_stop"] = True
    try:
        async with session.post(f"https://api.telegram.org/bot{token}/sendMessageDraft", json=payload,
                                timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return resp.status == 200
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[Telegram Bot] Ошибка sendMessageDraft: {type(e).__name__}")
        return False


# TG-02: ход работы консультанта черновиком. Модель отдаёт ответ целиком (строгий JSON через AI-роутер),
# поэтому в черновике не текст ответа по словам, а честные этапы: «Думаю…» → поиск → сравнение.
DRAFT_KEEPALIVE_SECONDS = 12      # черновик исчезает через ~30 секунд без обновления
DRAFT_STAGE_TEXT = {
    "search": "🔎 Ищу подходящие товары в магазинах…",
    "generate": "⚖️ Нашёл предложений: {count}. Сравниваю цены и готовлю ответ…",
}
_draft_ids = itertools.count(int(time.time()) % 1_000_000_000 + 1)
# (chat_id, draft_id) -> DraftProgress идущей генерации: по ним кнопка «Стоп» находит, что отменять
_active_generations: Dict[tuple, "DraftProgress"] = {}


class DraftProgress:
    """Показывает ход генерации одним черновиком и не даёт ему погаснуть, пока ответ готовится."""

    def __init__(self, session, token: str, chat_id: int, message_thread_id: Optional[int] = None):
        self.session, self.token, self.chat_id = session, token, chat_id
        self.message_thread_id = message_thread_id
        self.draft_id = next(_draft_ids)
        self.text = ""
        self.stopped = False
        self.generation: Optional[asyncio.Task] = None
        self._keepalive: Optional[asyncio.Task] = None
        self._pushes: set = set()

    def _draft_kwargs(self) -> Dict[str, Any]:
        return {"message_thread_id": self.message_thread_id} if self.message_thread_id is not None else {}

    async def start(self) -> bool:
        """Первый черновик. False — Telegram черновик не принял: вызывающий код работает как раньше."""
        if not await send_tg_draft(self.session, self.token, self.chat_id, self.draft_id, self.text, **self._draft_kwargs()):
            return False
        _active_generations[(self.chat_id, self.draft_id)] = self
        self._keepalive = asyncio.create_task(self._keep_alive())
        return True

    async def _keep_alive(self) -> None:
        while True:
            await asyncio.sleep(DRAFT_KEEPALIVE_SECONDS)
            await send_tg_draft(self.session, self.token, self.chat_id, self.draft_id, self.text, **self._draft_kwargs())

    def stage(self, name: str, count: int = 0) -> None:
        """Вызывается консультантом синхронно; отправка черновика уходит в фон и ответ не задерживает."""
        template = DRAFT_STAGE_TEXT.get(name)
        if not template or self.stopped:
            return
        self.text = template.format(count=count)
        push = asyncio.create_task(
            send_tg_draft(self.session, self.token, self.chat_id, self.draft_id, self.text, **self._draft_kwargs()))
        self._pushes.add(push)
        push.add_done_callback(self._pushes.discard)

    def stop(self) -> None:
        """Пользователь нажал «Стоп»: отменяем обращение к модели."""
        self.stopped = True
        if self.generation and not self.generation.done():
            self.generation.cancel()

    async def close(self) -> None:
        _active_generations.pop((self.chat_id, self.draft_id), None)
        tasks = [t for t in (self._keepalive, *self._pushes) if t and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def handle_generation_stopped(stopped: Dict[str, Any]) -> bool:
    """Обновление stopped_message_generation. Чужой или уже завершённый черновик ничего не отменяет."""
    chat = stopped.get("chat") or {}
    progress = _active_generations.get((chat.get("id"), stopped.get("draft_id")))
    if chat.get("type") != "private" or not progress:
        return False
    progress.stop()
    return True


async def _consult(session, token: str, chat_id: int, query: str, message_thread_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Ответ консультанта; с включённым TG-02 — с черновиком хода работы и кнопкой «Стоп».

    None — пользователь остановил генерацию: отвечать нечем, сообщение об этом уже отправлено.
    """
    history = _get_chat_history(chat_id)
    progress = None
    if telegram_widgets.feature_enabled("TELEGRAM_AI_DRAFTS", chat_id):
        progress = DraftProgress(session, token, chat_id, message_thread_id=message_thread_id)
        if not await progress.start():
            progress = None
    if not progress:
        await send_tg_chat_action(session, token, chat_id, "typing")
        return await ai_service.ask_ai_consultant(message=query, history=history, city="Все")

    progress.generation = asyncio.create_task(
        ai_service.ask_ai_consultant(message=query, history=history, city="Все", on_stage=progress.stage))
    try:
        return await progress.generation
    except asyncio.CancelledError:
        if not progress.stopped:        # отменяют сам обработчик (остановка бота), а не генерацию
            progress.generation.cancel()
            raise
        await send_tg_message(session, token, chat_id, "⏹ Остановлено. Задайте вопрос заново, когда будете готовы.", message_thread_id=message_thread_id)
        return None
    finally:
        await progress.close()


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


# --- TG-09: Глубокие ссылки с сайта (t.me/<bot>?start=<param>) ---

def encode_deep_link_param(action: str, payload: Dict[str, Any]) -> str:
    """TG-09: Генерирует параметр для t.me/<bot>?start=<param> (до 64 символов).
    Компактная полезная нагрузка кодируется в base64url без обращения к БД.
    Длинные запросы и сложные данные сохраняются в bot_deep_links."""
    if action == "search":
        q = str(payload.get("query") or "").strip()
        b64 = base64.urlsafe_b64encode(q.encode("utf-8")).decode("ascii").rstrip("=")
        param = f"s_{b64}"
        if len(param) <= 64:
            return param
    elif action == "watch_search":
        q = str(payload.get("query") or "").strip()
        b64 = base64.urlsafe_b64encode(q.encode("utf-8")).decode("ascii").rstrip("=")
        param = f"ws_{b64}"
        if len(param) <= 64:
            return param
    elif action == "watch_product":
        pid = str(payload.get("product_id") or "").strip()
        b64 = base64.urlsafe_b64encode(pid.encode("utf-8")).decode("ascii").rstrip("=")
        param = f"wp_{b64}"
        if len(param) <= 64 and not payload.get("title"):
            return param

    from database import create_bot_deep_link
    token = create_bot_deep_link(action, payload)
    return f"t_{token}"


def decode_deep_link_param(param: str) -> Optional[Dict[str, Any]]:
    """TG-09: Декодирует параметр start=<param> из Telegram-команды /start."""
    param = param.strip()
    if not param or len(param) > 64:
        return None

    if param.startswith("t_"):
        token = param[2:]
        from database import get_bot_deep_link
        return get_bot_deep_link(token)

    try:
        if param.startswith("s_"):
            raw = param[2:]
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            query = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            return {"action": "search", "payload": {"query": query}}

        if param.startswith("ws_"):
            raw = param[3:]
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            query = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            return {"action": "watch_search", "payload": {"query": query}}

        if param.startswith("wp_"):
            raw = param[3:]
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            product_id = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            return {"action": "watch_product", "payload": {"product_id": product_id}}
    except Exception:
        return None

    return None


async def handle_start_command(session: aiohttp.ClientSession, token: str, chat_id: int,
                               user_first_name: str = "", payload: str = "",
                               from_user: Optional[Dict[str, Any]] = None,
                               message_thread_id: Optional[int] = None):
    # Обеспечиваем регистрацию пользователя в users при переходе по глубокой ссылке
    if from_user:
        try:
            from database import upsert_user
            await asyncio.to_thread(upsert_user, from_user)
        except Exception as e:
            print(f"[Telegram Bot] Ошибка регистрации пользователя: {type(e).__name__}")

    # Обработка глубокой ссылки (TG-09)
    if payload:
        action_data = await asyncio.to_thread(decode_deep_link_param, payload)
        if action_data:
            action = action_data.get("action")
            action_payload = action_data.get("payload") or {}

            if action == "search":
                query = str(action_payload.get("query") or "").strip()
                if query:
                    s_kwargs = {"message_thread_id": message_thread_id} if message_thread_id is not None else {}
                    await handle_search_command(session, token, chat_id, query, **s_kwargs)
                    return

            elif action == "watch_search":
                query = str(action_payload.get("query") or "").strip()
                if query:
                    if message_thread_id is None:
                        message_thread_id = await get_or_create_tg_topic(session, token, chat_id, "watches")
                    from database import create_watch
                    watch = await asyncio.to_thread(create_watch, chat_id, {
                        "kind": "search", "target": query, "condition": "any_drop"})
                    if watch.get("already_existed"):
                        text = (
                            f"🔔 <b>Вы уже следите за этим запросом:</b>\n"
                            f"«<b>{html.escape(query)}</b>»\n\n"
                            "Я сообщу вам сюда, как только появятся новые предложения или снизится цена."
                        )
                    else:
                        text = (
                            f"🔔 <b>Наблюдение включено!</b>\n\n"
                            f"🔍 Поисковый запрос: «<b>{html.escape(query)}</b>»\n"
                            "Условие: <b>любое снижение цены</b>\n\n"
                            "Я пришлю уведомление прямо в этот чат, как только цена снизится."
                        )
                    keyboard = []
                    if APP_URL:
                        keyboard.append([site_button("⚙️ Мои наблюдения на сайте", APP_URL)])
                    keyboard.append([callback_button("🔕 Отключить наблюдение", f"unwatch:{watch['id']}", style="danger")])
                    reply_markup = {"inline_keyboard": keyboard}
                    await send_tg_message(session, token, chat_id, text, reply_markup, message_thread_id=message_thread_id)
                    return

            elif action == "watch_product":
                product_id = str(action_payload.get("product_id") or "").strip()
                if product_id:
                    if message_thread_id is None:
                        message_thread_id = await get_or_create_tg_topic(session, token, chat_id, "watches")
                    from database import get_connection, create_watch
                    def _find_and_watch():
                        with get_connection() as conn:
                            row = conn.execute("SELECT id, title, current_price, shop, url FROM products WHERE id = ?",
                                               (product_id,)).fetchone()
                            if not row:
                                row = conn.execute("SELECT id, title, current_price, shop, url FROM offers WHERE id = ?",
                                                   (product_id,)).fetchone()
                        row_dict = dict(row) if row else {}
                        p_title = row_dict.get("title") or action_payload.get("title") or product_id
                        watch_res = create_watch(chat_id, {
                            "kind": "product", "target": product_id, "title": p_title, "condition": "any_drop"})
                        return row_dict, p_title, watch_res

                    row_dict, p_title, watch = await asyncio.to_thread(_find_and_watch)
                    price = int(row_dict.get("current_price") or 0)
                    shop = str(row_dict.get("shop") or "")
                    url = str(row_dict.get("url") or "")

                    if watch.get("already_existed"):
                        text = (
                            f"🔔 <b>Вы уже следите за этим товаром:</b>\n\n"
                            f"📦 <b>{html.escape(p_title)}</b>\n"
                        )
                        if price > 0:
                            text += f"Текущая цена: <b>{format_price(price)}</b>" + (f" ({shop})" if shop else "") + "\n"
                        text += "\nЯ напишу вам сюда, как только цена снизится."
                    else:
                        text = (
                            f"🔔 <b>Наблюдение включено!</b>\n\n"
                            f"📦 <b>{html.escape(p_title)}</b>\n"
                        )
                        if price > 0:
                            text += f"Текущая цена: <b>{format_price(price)}</b>" + (f" ({shop})" if shop else "") + "\n"
                        text += "Условие: <b>любое снижение цены</b>\n\nЯ пришлю уведомление прямо в этот чат, как только цена снизится."

                    keyboard = []
                    if url and url.startswith("http"):
                        row1 = [url_button(f"🛒 Открыть в {shop or 'магазине'}", url, style="primary")]
                        c_btn = copy_button("📋 Скопировать ссылку", url)
                        if c_btn:
                            row1.append(c_btn)
                        keyboard.append(row1)
                    keyboard.append([callback_button("🔕 Отключить наблюдение", f"unwatch:{watch['id']}", style="danger")])
                    if APP_URL:
                        keyboard.append([site_button("⚙️ Все наблюдения на сайте", APP_URL)])
                    reply_markup = {"inline_keyboard": keyboard}
                    await send_tg_message(session, token, chat_id, text, reply_markup, message_thread_id=message_thread_id)
                    return

        # Некорректный или устаревший payload
        greeting = f"Привет, {html.escape(user_first_name)}! " if user_first_name else "Привет! "
        text = (
            f"👋 {greeting}\n\n"
            "Ссылка устарела или содержит некорректный код.\n"
            "Вы можете найти товар через <b>/search &lt;товар&gt;</b> или посмотреть свои активные наблюдения через <b>/watches</b>."
        )
        keyboard = []
        if APP_URL:
            keyboard.append([site_button("📊 Перейти в каталог", APP_URL)])
        reply_markup = {"inline_keyboard": keyboard} if keyboard else None
        await send_tg_message(session, token, chat_id, text, reply_markup, message_thread_id=message_thread_id)
        return

    # Стандартное приветствие без payload
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
        keyboard.append([site_button("📊 Открыть дашборд цен", APP_URL)])
    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    await send_tg_message(session, token, chat_id, text, reply_markup, message_thread_id=message_thread_id)


async def handle_status_command(session: aiohttp.ClientSession, token: str, chat_id: int, message_thread_id: Optional[int] = None):
    ai_conf = config.get_ai_config()
    ai_status = f"✅ Активен ({ai_conf['provider']})" if ai_conf["configured"] and ai_conf["enabled"] else "⚠️ Не настроен"
    time_badge = tg_time_tag(format_spec="wDT", fallback=time.strftime("%Y-%m-%d %H:%M"))
    
    text = (
        "📊 <b>Статус системы KZ Price Hunter</b>\n\n"
        f"🧠 <b>AI-консультант:</b> {ai_status}\n"
        f"🏪 <b>Подключенных сетей:</b> {len(config.SHOP_KEYS)}\n"
        "📍 <b>Основной регион сбора:</b> Астана / Алматы / Доставка по РК\n"
        f"🕒 <b>Время проверки:</b> {time_badge}\n\n"
        "Чтобы получить совет по выбору товара, просто напишите мне свой вопрос!"
    )
    await send_tg_message(session, token, chat_id, text, message_thread_id=message_thread_id)


async def handle_watches_command(session: aiohttp.ClientSession, token: str, chat_id: int, message_thread_id: Optional[int] = None):
    """Показывает список активных наблюдений за ценами пользователя и позволяет отключить их."""
    if message_thread_id is None:
        message_thread_id = await get_or_create_tg_topic(session, token, chat_id, "watches")

    from database import list_watches
    watches = list_watches(chat_id, only_active=True)
    if not watches:
        text = (
            "🔕 <b>У вас нет активных наблюдений за ценами.</b>\n\n"
            "Вы можете настроить отслеживание снижения цен в карточке любого товара на сайте или через поиск бота."
        )
        keyboard = []
        if APP_URL:
            keyboard.append([site_button("🔍 Перейти в каталог", APP_URL)])
        reply_markup = {"inline_keyboard": keyboard} if keyboard else None
        await send_tg_message(session, token, chat_id, text, reply_markup, message_thread_id=message_thread_id)
        return

    # TG-07: карточка-виджет наблюдений; при любом отказе — прежнее текстовое сообщение ниже
    if telegram_widgets.watches_widget_enabled(chat_id) and await _send_watches_widget(session, token, chat_id, watches):
        return

    text = f"🔔 <b>Ваши активные наблюдения ({len(watches)}):</b>\n\n"
    keyboard = []
    for idx, w in enumerate(watches[:15], 1):
        desc = w.get("description") or w.get("title") or f"Наблюдение #{w['id']}"
        text += f"{idx}. <b>{html.escape(desc)}</b>\n"
        w_title = (w.get("title") or f"#{w['id']}")[:24]
        keyboard.append([callback_button(f"🔕 Отключить: {w_title}", f"unwatch:{w['id']}", style="danger")])

    if len(watches) > 15:
        text += f"\n<i>(Показаны первые 15 из {len(watches)})</i>\n"

    if APP_URL:
        keyboard.append([site_button("⚙️ Все наблюдения на сайте", APP_URL)])

    reply_markup = {"inline_keyboard": keyboard}
    await send_tg_message(session, token, chat_id, text, reply_markup, message_thread_id=message_thread_id)


async def _set_bot_blocked(user_id: int, blocked: bool) -> None:
    if not config.bot_block_tracking_enabled():
        return
    try:
        from database import set_bot_blocked
        await asyncio.to_thread(set_bot_blocked, user_id, blocked)
    except Exception as e:
        print(f"[Telegram Bot] Не удалось записать блокировку бота: {type(e).__name__}")


async def handle_my_chat_member(membership: Dict[str, Any]) -> None:
    """TG-04: в личном чате это обновление приходит, только когда человек блокирует или разблокирует бота."""
    chat = membership.get("chat") or {}
    user_id = (membership.get("from") or {}).get("id")
    if (chat.get("type") != "private" or not isinstance(user_id, int) or isinstance(user_id, bool)
            or user_id <= 0 or chat.get("id") != user_id):
        return
    status = (membership.get("new_chat_member") or {}).get("status")
    if status == "kicked":
        await _set_bot_blocked(user_id, True)
    elif status == "member":
        await _set_bot_blocked(user_id, False)


async def _send_search_widget(session: aiohttp.ClientSession, token: str, chat_id: int, query: str,
                              items: List[Dict[str, Any]]) -> bool:
    """Карточка-виджет вместо текста. False — карточки нет, вызывающий код отправляет текст как раньше."""
    built = telegram_widgets.build_search_widget(query, items)
    if not built:
        return False
    widget_html, offers = built
    keyboard = []
    if APP_URL:
        keyboard.append([site_button("📊 Все результаты на сайте", _site_search_url(query))])
    message_id = await send_tg_widget(session, token, chat_id, widget_html,
                                      telegram_widgets.build_caption(query, offers),
                                      {"inline_keyboard": keyboard} if keyboard else None)
    if message_id is None:
        return False
    _widget_cards[(chat_id, message_id)] = {"type": "search", "query": query, "offers": offers, "refreshed": 0.0}
    return True


async def _send_watches_widget(session: aiohttp.ClientSession, token: str, chat_id: int,
                               watches: List[Dict[str, Any]]) -> bool:
    """Карточка-виджет наблюдений вместо текста. False — карточки нет, вызывающий код отправляет текст."""
    built = telegram_widgets.build_watches_widget(watches)
    if not built:
        return False
    widget_html, watch_items = built
    keyboard = []
    if APP_URL:
        keyboard.append([site_button("⚙️ Все наблюдения на сайте", APP_URL)])
    message_id = await send_tg_widget(session, token, chat_id, widget_html,
                                      telegram_widgets.build_watches_caption(watch_items, len(watches)),
                                      {"inline_keyboard": keyboard} if keyboard else None)
    if message_id is None:
        return False
    _widget_cards[(chat_id, message_id)] = {"type": "watches", "watches": watch_items, "refreshed": 0.0}
    return True


async def handle_widget_query(session: aiohttp.ClientSession, token: str, wq: Dict[str, Any]):
    """Нажатие в карточке: «Следить», «Обновить», «Отключить». Отвечаем всегда — иначе карточка ждёт 15 секунд."""
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
    card_type = card.get("type", "search")
    action = str(wq.get("data") or "")

    if card_type == "watches":
        if action.startswith(telegram_widgets.REQUEST_DELETE_PREFIX):
            watch_id_str = action[len(telegram_widgets.REQUEST_DELETE_PREFIX):]
            try:
                watch_id = int(watch_id_str)
            except ValueError:
                await answer_widget_query(session, token, wq_id, "Некорректный запрос")
                return
            try:
                from database import delete_watch, list_watches
                deleted = await asyncio.to_thread(delete_watch, user_id, watch_id)
            except Exception as e:
                print(f"[Telegram Bot] Ошибка отключения наблюдения из карточки: {type(e).__name__}")
                await answer_widget_query(session, token, wq_id, "Не получилось отключить")
                return
            if deleted:
                current_watches = await asyncio.to_thread(list_watches, user_id, only_active=True)
                active_ids = {w["id"] for w in current_watches}
                chat_data = telegram_widgets.build_watches_chat_data(card["watches"], active_ids)
                await edit_tg_widget_data(session, token, chat_id, message_id, chat_data)
                await answer_widget_query(session, token, wq_id, "Наблюдение отключено")
            else:
                await answer_widget_query(session, token, wq_id, "Наблюдение уже не активно")
            return

        if action == telegram_widgets.REQUEST_REFRESH:
            if time.monotonic() - card.get("refreshed", 0.0) < WIDGET_REFRESH_COOLDOWN_SECONDS:
                await answer_widget_query(session, token, wq_id, "Список только что обновлён")
                return
            try:
                from database import list_watches
                current_watches = await asyncio.to_thread(list_watches, user_id, only_active=True)
                active_ids = {w["id"] for w in current_watches}
                chat_data = telegram_widgets.build_watches_chat_data(card["watches"], active_ids)
            except Exception as e:
                print(f"[Telegram Bot] Ошибка обновления карточки наблюдений: {type(e).__name__}")
                await answer_widget_query(session, token, wq_id, "Не получилось обновить список")
                return
            updated = await edit_tg_widget_data(session, token, chat_id, message_id, chat_data)
            if updated:
                card["refreshed"] = time.monotonic()
            await answer_widget_query(session, token, wq_id, "Список обновлён" if updated else "Не получилось обновить")
            return

        await answer_widget_query(session, token, wq_id)
        return

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


def build_price_comparison_markdown(query: str, items: List[Dict[str, Any]]) -> str:
    """TG-03: строит Markdown таблицу сравнения цен по магазинам."""
    lines = [f"🔍 **Лучшие цены по запросу «{query}»:**\n"]
    lines.append("| Магазин | Цена | Товар |")
    lines.append("|---|---|---|")
    for it in items[:5]:
        shop = it.get("shop", "Магазин")
        price = format_price(it.get("current_price", 0))
        title = (it.get("title") or "")[:32].replace("|", " ")
        lines.append(f"| {shop} | **{price}** | {title} |")
    return "\n".join(lines)


async def handle_search_command(session: aiohttp.ClientSession, token: str, chat_id: int, query: str, message_thread_id: Optional[int] = None):
    if message_thread_id is None:
        message_thread_id = await get_or_create_tg_topic(session, token, chat_id, "search")

    q = query.strip()
    if not q:
        await send_tg_message(session, token, chat_id, "ℹ️ Пожалуйста, укажите название товара: <code>/search iPhone 15</code> или <code>/search RTX 4060</code>", message_thread_id=message_thread_id)
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
        await send_tg_message(session, token, chat_id, f"😕 По запросу «<b>{html.escape(q)}</b>» ничего не найдено в локальной базе. Попробуйте сократить запрос.", message_thread_id=message_thread_id)
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
            row = [url_button(f"{emoji} {shop}: {price}", it["url"], style="primary")]
            c_btn = copy_button("📋", it["url"])
            if c_btn:
                row.append(c_btn)
            keyboard.append(row)

    if APP_URL:
        keyboard.append([site_button("📊 Все результаты на сайте", _site_search_url(q))])

    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    rich_table = build_price_comparison_markdown(q, top)
    if not await send_tg_rich_message(session, token, chat_id, rich_table, reply_markup, message_thread_id=message_thread_id):
        await send_tg_message(session, token, chat_id, "\n".join(lines), reply_markup, message_thread_id=message_thread_id)


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


async def handle_ai_consultant_message(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: int,
    query: str,
    message_thread_id: Optional[int] = None,
):
    """Обработка вопроса AI-консультанту с подбором товаров."""
    if message_thread_id is None:
        message_thread_id = await get_or_create_tg_topic(session, token, chat_id, "ai")

    q = query.strip()
    if not q:
        await send_tg_message(session, token, chat_id, "ℹ️ Напишите ваш вопрос, например: <code>/ai посоветуй планшет до 150 тыс тенге</code>", message_thread_id=message_thread_id)
        return

    try:
        result = await _consult(session, token, chat_id, q, message_thread_id=message_thread_id)
    except Exception as e:
        print(f"[Telegram Bot] Ошибка AI-консультанта: {type(e).__name__}")
        await send_tg_message(session, token, chat_id, "⚠️ Не удалось получить ответ от AI. Пожалуйста, попробуйте еще раз.", message_thread_id=message_thread_id)
        return
    if result is None:
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
        keyboard.append([site_button("📊 Смотреть на сайте", APP_URL)])

    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    rich_md = f"✨ **AI-Консультант:**\n\n{answer_raw}"
    if not await send_tg_rich_message(session, token, chat_id, rich_md, reply_markup, message_thread_id=message_thread_id):
        await send_tg_message(session, token, chat_id, f"✨ <b>AI-Консультант:</b>\n\n{answer_html}", reply_markup, message_thread_id=message_thread_id)


_cached_bot_username: Optional[str] = None


async def get_bot_username(session: aiohttp.ClientSession, token: str) -> Optional[str]:
    """Получение username бота через getMe с кэшированием (Bot API)."""
    global _cached_bot_username
    if _cached_bot_username:
        return _cached_bot_username
    if not token:
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("ok"):
                    uname = (data.get("result") or {}).get("username")
                    if uname:
                        _cached_bot_username = uname
                        return uname
    except Exception:
        pass
    try:
        from auth import get_bot_username as sync_get_bot_username
        uname = await asyncio.to_thread(sync_get_bot_username)
        if uname:
            _cached_bot_username = uname
            return uname
    except Exception:
        pass
    return None


async def handle_inline_query(session: aiohttp.ClientSession, token: str, iq: Dict[str, Any]):
    """TG-12: обработка inline-поиска @бот <запрос> в любом чате (Bot API 8.0)."""
    if not config.inline_mode_enabled():
        return
    query_id = str(iq.get("id") or "")
    if not query_id:
        return
    query = str(iq.get("query") or "").strip()
    from_user = iq.get("from") or {}
    user_id = from_user.get("id")
    if isinstance(user_id, int) and user_id > 0 and not from_user.get("is_bot"):
        from database import upsert_telegram_user
        try:
            await asyncio.to_thread(upsert_telegram_user, user_id, from_user.get("username"),
                                    from_user.get("first_name"), from_user.get("last_name"))
        except Exception:
            pass

    bot_username = await get_bot_username(session, token) or "kz_price_hunter_bot"

    def _get_items():
        if not query:
            from database import get_alerts, get_products_list
            alerts = get_alerts(limit=8)
            if alerts:
                return alerts, True
            return get_products_list(limit=8), False
        else:
            found = search_in_database(query, city="Все", sort_by="price_asc")[:8]
            return found, False

    try:
        raw_items, is_deals = await asyncio.to_thread(_get_items)
    except Exception as e:
        print(f"[Telegram Bot] Ошибка поиска для inline: {type(e).__name__}")
        raw_items, is_deals = [], False

    results = []
    for idx, it in enumerate(raw_items, 1):
        item_id = str(it.get("id") or idx)
        title = str(it.get("title") or "Товар")
        price = int(it.get("current_price") or it.get("price") or 0)
        shop = str(it.get("shop") or "Магазин")
        emoji = _shop_emoji(shop)
        url = str(it.get("url") or "")
        image_url = str(it.get("image_url") or "")
        city = str(it.get("city") or "Астана")

        if is_deals and it.get("old_price") and it.get("drop_pct"):
            drop = int(it["drop_pct"])
            res_title = f"🔥 -{drop}% | {format_price(price)} — {title}"
            desc = f"{shop} | Старая: {format_price(int(it['old_price']))} | {city}"
        else:
            res_title = f"{emoji} {format_price(price)} — {title}"
            desc = f"Магазин: {shop} | Регион: {city}"

        msg_lines = [
            f"🏷 <b>{html.escape(title)}</b>\n",
            f"{emoji} <b>Магазин:</b> {html.escape(shop)}",
            f"💰 <b>Цена:</b> <b>{format_price(price)}</b>",
        ]
        if is_deals and it.get("old_price"):
            msg_lines.append(f"❌ <b>Старая цена:</b> <s>{format_price(int(it['old_price']))}</s>")
        msg_lines.append(f"📍 <b>Регион:</b> {html.escape(city)}\n")
        msg_lines.append(f"⚡️ <i>Найдено через @{bot_username}</i>\n")
        msg_lines.append(f"⏱ {tg_time_tag(format_spec='r', fallback='только что')}")

        keyboard = []
        first_row = []
        if url and url.startswith("http"):
            first_row.append(url_button(f"⚡️ В {shop}", url, style="primary"))
            c_btn = copy_button("📋 Ссылка", url)
            if c_btn:
                first_row.append(c_btn)
        if first_row:
            keyboard.append(first_row)
        if APP_URL:
            keyboard.append([site_button("📊 Сравнить цены на сайте", APP_URL)])

        article: Dict[str, Any] = {
            "type": "article",
            "id": f"kzph_{item_id[:32]}_{idx}",
            "title": res_title[:100],
            "description": desc[:100],
            "input_message_content": {
                "message_text": "\n".join(msg_lines),
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
        }
        if is_safe_image_url(image_url):
            article["thumbnail_url"] = image_url
        if keyboard:
            article["reply_markup"] = {"inline_keyboard": keyboard}
        results.append(article)

    payload: Dict[str, Any] = {
        "inline_query_id": query_id,
        "results": results,
        "cache_time": 30,
        "is_personal": True
    }
    if APP_URL:
        payload["button"] = {
            "text": "🔍 Открыть поиск KZ Price Hunter",
            "web_app": {"url": APP_URL}
        }

    url = f"https://api.telegram.org/bot{token}/answerInlineQuery"
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                err = await resp.text()
                print(f"[Telegram Bot] answerInlineQuery отклонен ({resp.status}): {err[:160]}")
    except Exception as e:
        print(f"[Telegram Bot] Ошибка answerInlineQuery: {type(e).__name__}")


async def process_telegram_update(session: aiohttp.ClientSession, token: str, update: Dict[str, Any]):
    """Обрабатывает одно входящее событие от Telegram."""
    iq = update.get("inline_query")
    if iq:
        await handle_inline_query(session, token, iq)
        return

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

    stopped = update.get("stopped_message_generation")
    if stopped:
        handle_generation_stopped(stopped)
        return

    membership = update.get("my_chat_member")
    if membership:
        await handle_my_chat_member(membership)
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
    thread_id = msg.get("message_thread_id")
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
    if user and user.get("bot_blocked"):
        # Человек снова пишет боту — значит, бот разблокирован, даже если обновление об этом не дошло
        await _set_bot_blocked(user_id, False)
    if len(text) > 4000 or _message_limiter.retry_after(str(user_id)):
        return

    # Команды
    cmd = text.split()[0].lower() if text.startswith("/") else ""
    kwargs = {"message_thread_id": thread_id} if thread_id is not None else {}

    if cmd in ("/start", "/help", "/new"):
        reset_chat_history(chat_id)
        payload = ""
        if cmd == "/start":
            parts = text.split(maxsplit=1)
            payload = parts[1].strip() if len(parts) > 1 else ""
        await handle_start_command(session, token, chat_id, first_name, payload=payload, from_user=from_user, **kwargs)
    elif cmd in ("/status", "/info"):
        await handle_status_command(session, token, chat_id, **kwargs)
    elif cmd in ("/watches", "/my_watches", "/subs", "/tracking"):
        await handle_watches_command(session, token, chat_id, **kwargs)
    elif cmd in ("/search", "/find", "/price"):
        query = text[len(cmd):].strip()
        await handle_search_command(session, token, chat_id, query, **kwargs)
    elif cmd in ("/ai", "/ask"):
        query = text[len(cmd):].strip()
        await handle_ai_consultant_message(session, token, chat_id, query, **kwargs)
    else:
        # Любое обычное сообщение без слеша считается вопросом к AI-консультанту
        await handle_ai_consultant_message(session, token, chat_id, text, **kwargs)


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
        # «Стоп» нельзя ставить в очередь чата: там его держит та самая генерация, которую он отменяет
        stopped = update.get("stopped_message_generation")
        if stopped:
            handle_generation_stopped(stopped)
            return
        await self.queue.put(update)  # backpressure instead of unbounded create_task

    async def _worker(self):
        while True:
            update = await self.queue.get()
            # У widget_query чат лежит в самом запросе: очередь нажатий идёт в общий порядок чата
            msg = (update.get("message") or (update.get("callback_query") or {}).get("message")
                   or update.get("widget_query") or update.get("my_chat_member"))
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
