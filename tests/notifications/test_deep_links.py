"""TG-09: глубокие ссылки в Telegram-бот (t.me/<bot>?start=<param>) — кодирование, декодирование, обработка /start и API."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import os
import unittest
from unittest.mock import AsyncMock, patch

for key in ('TELEGRAM_BOT_TOKEN', 'DNS_BOT_TOKEN', 'APP_URL', 'PUBLIC_ORIGIN'):
    os.environ[key] = ''
import telegram_bot as bot
import database as db


class DeepLinkEncodingTests(unittest.TestCase):
    def test_encode_decode_inline_search(self):
        param = bot.encode_deep_link_param('search', {'query': 'RTX 4060'})
        self.assertTrue(param.startswith('s_'))
        self.assertLessEqual(len(param), 64)
        decoded = bot.decode_deep_link_param(param)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded['action'], 'search')
        self.assertEqual(decoded['payload']['query'], 'RTX 4060')

    def test_encode_decode_inline_watch_search_cyrillic(self):
        param = bot.encode_deep_link_param('watch_search', {'query': 'айфон 15'})
        self.assertTrue(param.startswith('ws_'))
        self.assertLessEqual(len(param), 64)
        decoded = bot.decode_deep_link_param(param)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded['action'], 'watch_search')
        self.assertEqual(decoded['payload']['query'], 'айфон 15')

    def test_encode_decode_inline_watch_product(self):
        param = bot.encode_deep_link_param('watch_product', {'product_id': 'kaspi_10928374'})
        self.assertTrue(param.startswith('wp_'))
        self.assertLessEqual(len(param), 64)
        decoded = bot.decode_deep_link_param(param)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded['action'], 'watch_product')
        self.assertEqual(decoded['payload']['product_id'], 'kaspi_10928374')

    def test_encode_decode_db_token_fallback(self):
        long_query = 'очень длинный поисковый запрос со множеством подробных слов превышающий лимит шестьдесят четыре символа'
        param = bot.encode_deep_link_param('watch_search', {'query': long_query})
        self.assertTrue(param.startswith('t_'))
        self.assertLessEqual(len(param), 64)
        decoded = bot.decode_deep_link_param(param)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded['action'], 'watch_search')
        self.assertEqual(decoded['payload']['query'], long_query)

    def test_invalid_or_expired_deep_link(self):
        self.assertIsNone(bot.decode_deep_link_param(''))
        self.assertIsNone(bot.decode_deep_link_param('x' * 65))
        self.assertIsNone(bot.decode_deep_link_param('unknown_prefix_123'))
        self.assertIsNone(bot.decode_deep_link_param('t_non_existent_token'))


class DeepLinkBotHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_with_search_payload_triggers_search(self):
        param = bot.encode_deep_link_param('search', {'query': 'iphone 16'})
        with patch.object(bot, 'handle_search_command', new_callable=AsyncMock) as search_mock:
            await bot.handle_start_command(None, 'fake', 777, 'Алекс', payload=param)
        search_mock.assert_awaited_once_with(None, 'fake', 777, 'iphone 16')

    async def test_start_with_watch_search_creates_watch_and_confirms(self):
        param = bot.encode_deep_link_param('watch_search', {'query': 'RTX 5090'})
        with patch('database.create_watch', return_value={'id': 42, 'already_existed': False}) as create_mock, \
                patch.object(bot, 'send_tg_message', new_callable=AsyncMock) as send_mock:
            await bot.handle_start_command(None, 'fake', 777, 'Алекс', payload=param)
        create_mock.assert_called_once_with(777, {'kind': 'search', 'target': 'RTX 5090', 'condition': 'any_drop'})
        send_mock.assert_awaited_once()
        sent_text = send_mock.call_args[0][3]
        self.assertIn('Наблюдение включено', sent_text)
        self.assertIn('RTX 5090', sent_text)
        # Check inline keyboard has unwatch button
        reply_markup = send_mock.call_args[0][4]
        self.assertIsNotNone(reply_markup)
        btn_data = reply_markup['inline_keyboard'][-1][0]['callback_data']
        self.assertEqual(btn_data, 'unwatch:42')

    async def test_start_with_watch_product_creates_watch_and_confirms(self):
        param = bot.encode_deep_link_param('watch_product', {'product_id': 'prod_99'})
        fake_row = {'id': 'prod_99', 'title': 'Ноутбук Asus Zenbook', 'current_price': 450000, 'shop': 'Белый Ветер', 'url': 'https://shop.kz/p'}
        with patch('database.create_watch', return_value={'id': 105, 'already_existed': False}) as create_mock, \
                patch('database.get_connection') as conn_mock, \
                patch.object(bot, 'send_tg_message', new_callable=AsyncMock) as send_mock:
            conn_mock.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = fake_row
            await bot.handle_start_command(None, 'fake', 777, 'Алекс', payload=param)
        create_mock.assert_called_once_with(777, {'kind': 'product', 'target': 'prod_99', 'title': 'Ноутбук Asus Zenbook', 'condition': 'any_drop'})
        send_mock.assert_awaited_once()
        sent_text = send_mock.call_args[0][3]
        self.assertIn('Наблюдение включено', sent_text)
        self.assertIn('Ноутбук Asus Zenbook', sent_text)
        self.assertIn('450 000 ₸', sent_text)
        self.assertIn('Белый Ветер', sent_text)

    async def test_start_with_invalid_payload_gives_friendly_message(self):
        with patch.object(bot, 'send_tg_message', new_callable=AsyncMock) as send_mock:
            await bot.handle_start_command(None, 'fake', 777, 'Алекс', payload='invalid_code')
        send_mock.assert_awaited_once()
        sent_text = send_mock.call_args[0][3]
        self.assertIn('устарела или содержит некорректный код', sent_text)

    async def test_start_registers_new_user_if_from_user_passed(self):
        from_user = {'id': 999, 'first_name': 'Тест', 'username': 'testuser'}
        with patch('database.upsert_user') as upsert_mock, \
                patch.object(bot, 'send_tg_message', new_callable=AsyncMock):
            await bot.handle_start_command(None, 'fake', 999, 'Тест', payload='', from_user=from_user)
        upsert_mock.assert_called_once_with(from_user)


class DeepLinkApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_deep_link_endpoint_validation_and_url_generation(self):
        from aiohttp.test_utils import make_mocked_request
        from web.server import telegram_deep_link_handler

        # Missing action or target -> 400
        req_missing = make_mocked_request('GET', '/api/telegram/deep-link')
        with patch('web.server.get_bot_username', return_value='kz_price_hunter_bot'):
            res_missing = await telegram_deep_link_handler(req_missing)
            self.assertEqual(res_missing.status, 400)

        # Bot unconfigured -> 503
        with patch('web.server.get_bot_username', return_value=None):
            req_valid = make_mocked_request('GET', '/api/telegram/deep-link?action=watch_search&target=iphone')
            res_503 = await telegram_deep_link_handler(req_valid)
            self.assertEqual(res_503.status, 503)

        # Valid request -> 200 with url
        with patch('web.server.get_bot_username', return_value='kz_price_hunter_bot'):
            req_ok = make_mocked_request('GET', '/api/telegram/deep-link?action=watch_search&target=iphone+16')
            res_ok = await telegram_deep_link_handler(req_ok)
            self.assertEqual(res_ok.status, 200)
            data = res_ok.body.decode('utf-8')
            self.assertIn('https://t.me/kz_price_hunter_bot?start=', data)


if __name__ == '__main__':
    unittest.main()
