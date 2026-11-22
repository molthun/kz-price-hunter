from tests import test_support  # noqa: F401

import unittest
from unittest.mock import AsyncMock, patch

import config
import database
import telegram_bot
from domain.models import Channel, ChannelType, Seller, utc_now_iso
from domain.telegram_price_upload import (
    PRICE_UPLOAD_DRAFTS,
    PriceUploadDraftStore,
    extract_photo_file_id,
    find_seller_for_price_upload,
    format_price_preview_html,
    format_publish_result_html,
    parse_price_command_args,
)
from repositories.channel_repo import ChannelRepository
from repositories.seller_repo import SellerRepository


def _create_test_seller(conn, *, seller_id: str, slug: str, name: str, city: str = "Алматы", is_active: bool = True):
    now = utc_now_iso()
    seller = Seller(
        id=seller_id,
        slug=slug,
        name=name,
        phone="+77001234567",
        is_active=is_active,
        metadata={"city": city, "whatsapp": "+77001234567"},
        created_at=now,
        updated_at=now,
    )
    SellerRepository(conn).save_or_update(seller)
    channel = Channel(
        id=f"ch_{seller_id}",
        seller_id=seller_id,
        channel_type=ChannelType.WHATSAPP,
        name=f"{name} WhatsApp",
        base_url="https://wa.me/77001234567",
        is_active=True,
        created_at=now,
    )
    ChannelRepository(conn).save_or_update(channel)
    conn.commit()
    return seller


class TelegramPriceUploadUnitTest(unittest.TestCase):
    def test_parse_price_command_args(self):
        self.assertEqual(
            parse_price_command_args("/прайс Мобильник\niPhone 16 Pro 256GB — 549 000 тг"),
            ("Мобильник", "iPhone 16 Pro 256GB — 549 000 тг"),
        )
        self.assertEqual(
            parse_price_command_args("/upload_price@mybot mobilnik | AirPods 4 — 89 000 тг"),
            ("mobilnik", "AirPods 4 — 89 000 тг"),
        )
        self.assertEqual(parse_price_command_args("/прайс"), ("", ""))

    def test_extract_photo_file_id(self):
        msg_photo = {
            "photo": [
                {"file_id": "small_id", "width": 100, "height": 100, "file_size": 1000},
                {"file_id": "large_id", "width": 1200, "height": 1600, "file_size": 95000},
            ]
        }
        self.assertEqual(extract_photo_file_id(msg_photo), "large_id")
        self.assertEqual(
            extract_photo_file_id({"document": {"file_id": "doc_img", "mime_type": "image/png"}}),
            "doc_img",
        )
        self.assertIsNone(
            extract_photo_file_id({"document": {"file_id": "doc_pdf", "mime_type": "application/pdf"}})
        )

    def test_draft_store_owner_atomic_pop_ttl_and_limit(self):
        store = PriceUploadDraftStore(max_items=2, ttl_seconds=60)
        d1 = store.create(
            admin_user_id=100,
            seller_id="s1",
            seller_name="Мобильник",
            city="Алматы",
            publishable=[{"title": "iPhone 16", "price": 549000}],
            now=1000.0,
        )
        # Чужой админ не может извлечь черновик
        self.assertIsNone(store.pop_for_admin(d1, 200, now=1010.0))
        # Владелец извлекает один раз (повторное извлечение даёт None)
        popped = store.pop_for_admin(d1, 100, now=1010.0)
        self.assertIsNotNone(popped)
        self.assertEqual(popped["seller_id"], "s1")
        self.assertIsNone(store.pop_for_admin(d1, 100, now=1011.0))

        # Истечение по TTL
        d2 = store.create(
            admin_user_id=100,
            seller_id="s1",
            seller_name="Мобильник",
            city="Алматы",
            publishable=[],
            now=2000.0,
        )
        self.assertIsNone(store.pop_for_admin(d2, 100, now=2061.0))

        # Ограничение max_items вытесняет самый старый
        a = store.create(admin_user_id=1, seller_id="a", seller_name="A", city="Алматы", publishable=[], now=3000.0)
        b = store.create(admin_user_id=1, seller_id="b", seller_name="B", city="Алматы", publishable=[], now=3001.0)
        c = store.create(admin_user_id=1, seller_id="c", seller_name="C", city="Алматы", publishable=[], now=3002.0)
        self.assertIsNone(store.pop_for_admin(a, 1, now=3003.0))
        self.assertIsNotNone(store.pop_for_admin(b, 1, now=3003.0))
        self.assertIsNotNone(store.pop_for_admin(c, 1, now=3003.0))

    def test_format_html_escapes_untrusted_input(self):
        preview = format_price_preview_html(
            seller_name="<script>alert(1)</script>",
            city="Алматы",
            publishable=[
                {"raw_title": "<b>iPhone 16</b>", "price": 549000, "old_price": 599000},
                {"raw_title": "AirPods 4", "price": 89000},
            ],
            rejected=[],
            max_shown=1,
        )
        self.assertNotIn("<script>", preview)
        self.assertIn("&lt;script&gt;", preview)
        self.assertIn("&lt;b&gt;iPhone 16&lt;/b&gt;", preview)
        self.assertIn("<s>599 000 ₸</s>", preview)
        self.assertIn("ещё 1 поз.", preview)

        pub = format_publish_result_html(
            seller_name="A&B <shop>",
            city="Астана",
            published_count=3,
            skipped_count=1,
        )
        self.assertIn("A&amp;B &lt;shop&gt;", pub)
        self.assertIn("Сохранено предложений: <b>3</b>", pub)
        self.assertIn("Пропущено при повторной проверке: <b>1</b>", pub)

    def test_find_seller_exact_ambiguous_and_inactive(self):
        database.init_db()
        with database.get_connection() as conn:
            _create_test_seller(conn, seller_id="s_mob_1", slug="mobilnik_almaty", name="Мобильник Алматы")
            _create_test_seller(conn, seller_id="s_mob_2", slug="mobilnik_astana", name="Мобильник Астана")
            _create_test_seller(conn, seller_id="s_off", slug="offline_shop", name="Закрытый Магазин", is_active=False)

            s, cands = find_seller_for_price_upload(conn, "mobilnik_almaty")
            self.assertIsNotNone(s)
            self.assertEqual(s.id, "s_mob_1")
            self.assertEqual(len(cands), 1)

            s_amb, cands_amb = find_seller_for_price_upload(conn, "Мобильник")
            self.assertIsNone(s_amb)
            self.assertGreaterEqual(len(cands_amb), 2)

            s_off, cands_off = find_seller_for_price_upload(conn, "offline_shop")
            self.assertIsNone(s_off)
            self.assertEqual(cands_off, [])


class TelegramPriceUploadFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.init_db()
        PRICE_UPLOAD_DRAFTS.clear()
        self._limiter_patch = patch.object(telegram_bot._message_limiter, "retry_after", return_value=0)
        self._limiter_patch.start()
        with database.get_connection() as conn:
            _create_test_seller(conn, seller_id="s_tg_mob", slug="tg_mobilnik", name="Мобильник Тест", city="Алматы")

    def tearDown(self):
        self._limiter_patch.stop()
        PRICE_UPLOAD_DRAFTS.clear()

    async def test_non_admin_is_rejected(self):
        send_mock = AsyncMock()
        with (
            patch.object(config, "ADMIN_TELEGRAM_IDS", {999001}),
            patch.object(telegram_bot, "get_user", return_value={"id": 12345, "is_admin": False}),
            patch.object(telegram_bot, "send_tg_message", send_mock),
        ):
            update = {
                "message": {
                    "message_id": 10,
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 12345, "is_bot": False, "first_name": "User"},
                    "text": "/прайс tg_mobilnik\niPhone 16 Pro 256GB — 549 000 тг",
                }
            }
            await telegram_bot.process_telegram_update(None, "tok", update)

        send_mock.assert_awaited_once()
        self.assertIn("только администраторам", send_mock.call_args.args[3])

    async def test_plain_text_price_still_triggers_search(self):
        search_mock = AsyncMock()
        react_mock = AsyncMock()
        with (
            patch.object(telegram_bot, "get_user", return_value={"id": 22222, "is_admin": False}),
            patch.object(telegram_bot, "handle_search_command", search_mock),
            patch.object(telegram_bot, "set_tg_message_reaction", react_mock),
        ):
            update = {
                "message": {
                    "message_id": 11,
                    "chat": {"id": 22222, "type": "private"},
                    "from": {"id": 22222, "is_bot": False, "first_name": "Buyer"},
                    "text": "/price iPhone 16",
                }
            }
            await telegram_bot.process_telegram_update(None, "tok", update)

        search_mock.assert_awaited_once()
        self.assertEqual(search_mock.call_args.args[3], "iPhone 16")

    async def test_random_photo_caption_is_ignored(self):
        ai_mock = AsyncMock()
        send_mock = AsyncMock()
        with (
            patch.object(telegram_bot, "get_user", return_value={"id": 22223, "is_admin": False}),
            patch.object(telegram_bot, "handle_ai_consultant_message", ai_mock),
            patch.object(telegram_bot, "send_tg_message", send_mock),
        ):
            update = {
                "message": {
                    "message_id": 12,
                    "chat": {"id": 22223, "type": "private"},
                    "from": {"id": 22223, "is_bot": False, "first_name": "Buyer"},
                    "photo": [{"file_id": "f1", "width": 500, "height": 500}],
                    "caption": "Смотри какая красивая кошка",
                }
            }
            await telegram_bot.process_telegram_update(None, "tok", update)

        ai_mock.assert_not_awaited()
        send_mock.assert_not_awaited()

    async def test_admin_text_preview_and_publish_callback_flow(self):
        send_mock = AsyncMock()
        edit_mock = AsyncMock()
        answer_mock = AsyncMock()
        react_mock = AsyncMock()
        admin_id = 999001

        with (
            patch.object(config, "ADMIN_TELEGRAM_IDS", {admin_id}),
            patch.object(telegram_bot, "get_user", return_value={"id": admin_id, "is_admin": True}),
            patch.object(telegram_bot, "send_tg_message", send_mock),
            patch.object(telegram_bot, "edit_tg_message_text", edit_mock),
            patch.object(telegram_bot, "answer_callback_query", answer_mock),
            patch.object(telegram_bot, "set_tg_message_reaction", react_mock),
        ):
            # 1. Админ отправляет текстовый прайс
            update = {
                "message": {
                    "message_id": 20,
                    "chat": {"id": admin_id, "type": "private"},
                    "from": {"id": admin_id, "is_bot": False, "first_name": "Admin"},
                    "text": "/прайс tg_mobilnik\niPhone 16 Pro 256GB — 549 000 тг\nAirPods 4 — 89 000 тг",
                }
            }
            await telegram_bot.process_telegram_update(None, "tok", update)

            send_mock.assert_awaited_once()
            preview_html = send_mock.call_args.args[3]
            markup = send_mock.call_args.kwargs.get("reply_markup")
            self.assertIn("Мобильник Тест", preview_html)
            self.assertIn("549 000 ₸", preview_html)
            self.assertIsNotNone(markup)
            pub_cb = markup["inline_keyboard"][0][0]["callback_data"]
            self.assertTrue(pub_cb.startswith("pricepub:"))

            # 2. Другой админ не может опубликовать чужой черновик
            other_admin = 999002
            with patch.object(config, "ADMIN_TELEGRAM_IDS", {admin_id, other_admin}):
                await telegram_bot.process_telegram_update(
                    None,
                    "tok",
                    {
                        "callback_query": {
                            "id": "cq_other",
                            "from": {"id": other_admin, "is_bot": False},
                            "message": {"message_id": 21, "chat": {"id": other_admin, "type": "private"}},
                            "data": pub_cb,
                        }
                    },
                )
            self.assertIn("истёк", answer_mock.call_args.kwargs["text"])

            # 3. Владелец черновика публикует его
            await telegram_bot.process_telegram_update(
                None,
                "tok",
                {
                    "callback_query": {
                        "id": "cq_pub",
                        "from": {"id": admin_id, "is_bot": False},
                        "message": {"message_id": 21, "chat": {"id": admin_id, "type": "private"}},
                        "data": pub_cb,
                    }
                },
            )
            edit_mock.assert_awaited_once()
            self.assertIn("Прайс опубликован", edit_mock.call_args.args[4])
            self.assertIn("Сохранено предложений: <b>2</b>", edit_mock.call_args.args[4])

            # 4. Повторное нажатие на ту же кнопку не дублирует публикацию
            await telegram_bot.process_telegram_update(
                None,
                "tok",
                {
                    "callback_query": {
                        "id": "cq_pub_dup",
                        "from": {"id": admin_id, "is_bot": False},
                        "message": {"message_id": 21, "chat": {"id": admin_id, "type": "private"}},
                        "data": pub_cb,
                    }
                },
            )
            self.assertIn("истёк", answer_mock.call_args.kwargs["text"])

        with database.get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM offers WHERE seller_id = ?", ("s_tg_mob",)).fetchone()[0]
        self.assertGreaterEqual(cnt, 2)

    async def test_admin_photo_caption_and_cancel_callback(self):
        send_mock = AsyncMock()
        edit_mock = AsyncMock()
        answer_mock = AsyncMock()
        react_mock = AsyncMock()
        action_mock = AsyncMock()
        admin_id = 999001

        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        fake_extracted = {
            "mime_type": "image/jpeg",
            "has_image": True,
            "has_text": False,
            "city": "Алматы",
            "publishable": [
                {"raw_title": "Galaxy S25 Ultra 256GB", "price": 629000, "currency": "KZT", "confidence": 0.95}
            ],
            "rejected": [],
            "total": 1,
        }

        with (
            patch.object(config, "ADMIN_TELEGRAM_IDS", {admin_id}),
            patch.object(telegram_bot, "get_user", return_value={"id": admin_id, "is_admin": True}),
            patch.object(telegram_bot, "send_tg_message", send_mock),
            patch.object(telegram_bot, "edit_tg_message_text", edit_mock),
            patch.object(telegram_bot, "answer_callback_query", answer_mock),
            patch.object(telegram_bot, "set_tg_message_reaction", react_mock),
            patch.object(telegram_bot, "send_tg_chat_action", action_mock),
            patch("domain.telegram_price_upload.download_telegram_image", AsyncMock(return_value=fake_jpeg)),
            patch("domain.seller_vision_upload.extract_seller_price_payload", AsyncMock(return_value=fake_extracted)),
        ):
            update = {
                "message": {
                    "message_id": 30,
                    "chat": {"id": admin_id, "type": "private"},
                    "from": {"id": admin_id, "is_bot": False, "first_name": "Admin"},
                    "photo": [{"file_id": "photo_123", "width": 1000, "height": 1200, "file_size": 50000}],
                    "caption": "/price tg_mobilnik",
                }
            }
            await telegram_bot.process_telegram_update(None, "tok", update)

            send_mock.assert_awaited_once()
            markup = send_mock.call_args.kwargs.get("reply_markup")
            cancel_cb = markup["inline_keyboard"][0][1]["callback_data"]
            self.assertTrue(cancel_cb.startswith("pricecan:"))

            await telegram_bot.process_telegram_update(
                None,
                "tok",
                {
                    "callback_query": {
                        "id": "cq_can",
                        "from": {"id": admin_id, "is_bot": False},
                        "message": {"message_id": 31, "chat": {"id": admin_id, "type": "private"}},
                        "data": cancel_cb,
                    }
                },
            )
            edit_mock.assert_awaited_once()
            self.assertIn("отменена", edit_mock.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
