"""TGW-01: карточка /search виджетом Telegram — пределы, выключатель, запросы карточки (без сети)."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import os
import random
import unittest
from unittest.mock import AsyncMock, patch

for key in ('TELEGRAM_BOT_TOKEN', 'DNS_BOT_TOKEN', 'APP_URL', 'PUBLIC_ORIGIN', 'TELEGRAM_SEARCH_WIDGET'):
    os.environ[key] = ''
import auth
import telegram_bot as bot
import telegram_widgets as widgets


def _items(count, title='Смартфон', url='https://shop.example.kz/p/{i}'):
    return [{'id': f'p{i}', 'shop': f'Магазин {i}', 'title': f'{title} {i}', 'current_price': 1000 + i,
             'old_price_on_site': 2000 if i == 1 else None, 'first_seen_price': 500,
             'url': url.format(i=i)} for i in range(count)]


class WidgetBuildTests(unittest.TestCase):
    def test_card_fits_telegram_limits_and_keeps_offer_order(self):
        html_text, offers = widgets.build_search_widget('iphone 15', _items(20), now=1_794_300_000)
        self.assertEqual([o['id'] for o in offers], [f'p{i}' for i in range(widgets.MAX_OFFERS)])
        self.assertLessEqual(len(html_text.encode('utf-8')), widgets.HTML_LIMIT)
        self.assertLessEqual(widgets.compressed_size(html_text), widgets.COMPRESSED_LIMIT)
        self.assertNotIn('\x00', html_text)
        # Скидка — только когда прежняя цена выше текущей: first_seen_price 500 < 1000 скидкой не считается
        self.assertEqual([o['old'] for o in offers[:2]], [0, 2000])

    def test_one_cheapest_offer_per_shop(self):
        items = [
            {'id': 'h2', 'shop': 'Halyk Market', 'title': 'Sage', 'current_price': 472_900},
            {'id': 'h1', 'shop': 'Halyk  market', 'title': 'Black', 'current_price': 470_000},
            {'id': 'k1', 'shop': 'Kaspi Магазин', 'title': 'Blue', 'current_price': 492_896},
            {'id': 'h3', 'shop': 'Halyk Market', 'title': 'Lavender', 'current_price': 472_900},
            {'id': 'd0', 'shop': 'DNS Казахстан', 'title': 'Нет цены', 'current_price': 0},
            {'id': 'd1', 'shop': 'DNS Казахстан', 'title': 'White', 'current_price': 498_290},
        ]
        _, offers = widgets.build_search_widget('iphone 17', items)
        self.assertEqual([o['id'] for o in offers], ['h1', 'k1', 'd1'])
        self.assertIn('от 470 000 ₸', widgets.build_caption('iphone 17', offers))

    def test_incompressible_offers_are_dropped_until_card_fits(self):
        rng = random.Random(1)   # разные иероглифы в каждом названии: такой текст почти не сжимается
        items = [{'id': f'p{i}', 'shop': f'S{i}', 'current_price': 10 + i, 'url': '',
                  'title': ''.join(chr(rng.randrange(0x4E00, 0x9FFF)) for _ in range(widgets.MAX_TITLE))}
                 for i in range(40)]
        with patch.object(widgets, 'MAX_OFFERS', 40):
            html_text, offers = widgets.build_search_widget('q', items)
        self.assertTrue(0 < len(offers) < 40)
        self.assertLessEqual(widgets.compressed_size(html_text), widgets.COMPRESSED_LIMIT)

    def test_shop_text_cannot_break_out_of_script(self):
        items = _items(2, title='</script><img src=x onerror=alert(1)> ')
        html_text, _ = widgets.build_search_widget('</script><b>q', items)
        self.assertEqual(html_text.count('</script>'), 1)
        self.assertNotIn('<b>q', html_text)   # запрос живёт в подписи, в код карточки не попадает
        self.assertNotIn(' ', html_text)

    def test_unsafe_or_long_urls_become_plain_rows(self):
        items = _items(3)
        items[0]['url'] = 'http://shop.example.kz/insecure'
        items[1]['url'] = 'https://shop.example.kz/' + 'x' * 300
        _, offers = widgets.build_search_widget('q', items)
        self.assertEqual([bool(o['url']) for o in offers], [False, False, True])

    def test_nothing_to_show_returns_none(self):
        self.assertIsNone(widgets.build_search_widget('q', []))
        self.assertIsNone(widgets.build_search_widget('q', [{'id': 'a', 'current_price': 0}]))

    def test_caption_is_one_escaped_line_with_best_price(self):
        _, offers = widgets.build_search_widget('q', _items(8, title='<b>' + 'я' * 200))
        caption = widgets.build_caption('<i>запрос</i>' + 'ж' * 300, offers)
        self.assertLessEqual(len(caption), widgets.CAPTION_LIMIT)
        self.assertNotIn('\n', caption)
        self.assertIn('&lt;i&gt;запрос&lt;/i&gt;', caption)
        self.assertTrue(caption.endswith('— от 1 000 ₸'))

    def test_chat_data_follows_card_indexes_and_marks_missing_offers(self):
        _, offers = widgets.build_search_widget('q', _items(3))
        data = json.loads(widgets.build_chat_data(offers, {'p0': 900, 'p2': 1500}, now=5))
        self.assertEqual(data, {'t': 5, 'p': [900, 0, 1500]})

    def test_switch_defaults_off_and_limits_admin_mode_to_admins(self):
        with patch.dict(os.environ, {'TELEGRAM_SEARCH_WIDGET': ''}):
            self.assertFalse(widgets.search_widget_enabled(1))
        with patch.dict(os.environ, {'TELEGRAM_SEARCH_WIDGET': 'да'}):
            self.assertFalse(widgets.search_widget_enabled(1))
        with patch.dict(os.environ, {'TELEGRAM_SEARCH_WIDGET': 'admins'}), \
                patch.object(widgets.config, 'ADMIN_TELEGRAM_IDS', {7}):
            self.assertTrue(widgets.search_widget_enabled(7))
            self.assertFalse(widgets.search_widget_enabled(8))
        with patch.dict(os.environ, {'TELEGRAM_SEARCH_WIDGET': ' ALL '}):
            self.assertTrue(widgets.search_widget_enabled(8))


class SearchCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._widget_cards.clear()

    async def _search(self, mode, widget_result):
        with patch.dict(os.environ, {'TELEGRAM_SEARCH_WIDGET': mode}), \
                patch.object(bot, 'search_in_database', return_value=_items(3)), \
                patch('search_engine._record_search'), \
                patch.object(bot, 'send_tg_chat_action', new_callable=AsyncMock), \
                patch.object(bot, 'send_tg_widget', new_callable=AsyncMock, return_value=widget_result) as widget, \
                patch.object(bot, 'send_tg_message', new_callable=AsyncMock) as text:
            await bot.handle_search_command(None, 'fake', 777, 'iphone')
        return widget, text

    async def test_switch_off_sends_text_only(self):
        widget, text = await self._search('off', 55)
        widget.assert_not_awaited()
        text.assert_awaited_once()

    async def test_switch_on_sends_card_and_remembers_it(self):
        widget, text = await self._search('all', 55)
        widget.assert_awaited_once()
        text.assert_not_awaited()
        card = bot._widget_cards[(777, 55)]
        self.assertEqual(card['query'], 'iphone')
        self.assertEqual([o['id'] for o in card['offers']], ['p0', 'p1', 'p2'])

    async def test_rejected_card_falls_back_to_text(self):
        widget, text = await self._search('all', None)
        widget.assert_awaited_once()
        text.assert_awaited_once()
        self.assertEqual(len(bot._widget_cards), 0)


class WidgetQueryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._widget_cards.clear()
        _, offers = widgets.build_search_widget('iphone', _items(3))
        bot._widget_cards[(777, 55)] = {'query': 'iphone', 'offers': offers, 'refreshed': 0.0}
        limiter = patch.object(bot, '_widget_limiter', auth.RateLimiter(20, 60))
        limiter.start()
        self.addCleanup(limiter.stop)

    @staticmethod
    def _update(data, chat_id=777, user_id=777, kind='private', message_id=55):
        return {'widget_query': {'id': 'wq1', 'from': {'id': user_id}, 'chat': {'id': chat_id, 'type': kind},
                                 'message_id': message_id, 'data': data}}

    async def _run(self, update, user={'id': 777}, **patches):
        with patch.object(bot, 'get_user', return_value=user), \
                patch.object(bot, 'answer_widget_query', new_callable=AsyncMock) as answer:
            await bot.process_telegram_update(None, 'fake', update)
        return answer

    async def test_foreign_chat_group_and_blocked_user_get_no_answer(self):
        with patch('database.create_watch') as create:
            for update in (self._update('w', kind='group', chat_id=-5), self._update('w', chat_id=778)):
                answer = await self._run(update)
                answer.assert_not_awaited()
            answer = await self._run(self._update('w'), user={'id': 777, 'is_blocked': True})
            answer.assert_not_awaited()
            create.assert_not_called()

    async def test_watch_creates_search_watch_for_card_query(self):
        with patch('database.create_watch', return_value={'id': 1}) as create:
            answer = await self._run(self._update('w'))
        create.assert_called_once_with(777, {'kind': 'search', 'target': 'iphone', 'condition': 'any_drop'})
        self.assertIn('включено', answer.call_args[0][3])

    async def test_watch_reports_existing_and_validation_errors(self):
        with patch('database.create_watch', return_value={'id': 1, 'already_existed': True}):
            answer = await self._run(self._update('w'))
        self.assertIn('уже следите', answer.call_args[0][3])
        with patch('database.create_watch', side_effect=ValueError('Больше 50 наблюдений не поддерживается')):
            answer = await self._run(self._update('w'))
        self.assertIn('Больше 50', answer.call_args[0][3])

    async def test_watch_without_site_account_is_not_created(self):
        with patch('database.create_watch') as create:
            answer = await self._run(self._update('w'), user=None)
        create.assert_not_called()
        self.assertIn('войдите на сайте', answer.call_args[0][3])

    async def test_unknown_card_asks_to_repeat_search(self):
        answer = await self._run(self._update('r', message_id=56))
        self.assertIn('повторите /search', answer.call_args[0][3])

    async def test_refresh_edits_card_prices_then_cools_down(self):
        fresh = [{'id': 'p0', 'current_price': 950}, {'id': 'p2', 'current_price': 1002}]
        with patch.object(bot, 'search_in_database', return_value=fresh) as search, \
                patch.object(bot, 'edit_tg_widget_data', new_callable=AsyncMock, return_value=True) as edit:
            answer = await self._run(self._update('r'))
            self.assertEqual(answer.call_args[0][3], 'Цены обновлены')
            chat_id, message_id, chat_data = edit.call_args[0][2:5]
            self.assertEqual((chat_id, message_id), (777, 55))
            self.assertEqual(json.loads(chat_data)['p'], [950, 0, 1002])
            answer = await self._run(self._update('r'))
            self.assertEqual(answer.call_args[0][3], 'Цены только что обновлены')
            self.assertEqual(search.call_count, 1)
            edit.assert_awaited_once()

    async def test_failed_edit_is_reported_and_does_not_start_cooldown(self):
        with patch.object(bot, 'search_in_database', return_value=[]), \
                patch.object(bot, 'edit_tg_widget_data', new_callable=AsyncMock, return_value=False):
            answer = await self._run(self._update('r'))
        self.assertIn('Не получилось', answer.call_args[0][3])
        self.assertEqual(bot._widget_cards[(777, 55)]['refreshed'], 0.0)

    async def test_rate_limit_answers_instead_of_leaving_card_waiting(self):
        with patch.object(bot, '_widget_limiter', auth.RateLimiter(1, 60)), \
                patch('database.create_watch', return_value={'id': 1}) as create:
            await self._run(self._update('w'))
            answer = await self._run(self._update('w'))
        self.assertEqual(create.call_count, 1)
        self.assertIn('Слишком часто', answer.call_args[0][3])

    async def test_dispatcher_takes_chat_from_widget_query(self):
        seen = []

        async def handle(session, token, update):
            seen.append(update['widget_query']['chat']['id'])
        with patch.object(bot, 'process_telegram_update', side_effect=handle):
            dispatcher = bot.TelegramDispatcher(None, 'fake', workers=1, capacity=2)
            try:
                await dispatcher.submit(self._update('r'))
                await dispatcher.queue.join()
                self.assertEqual(seen, [777])
                self.assertEqual(dispatcher.chat_locks, {})
            finally:
                await dispatcher.close()


class BotMenuTests(unittest.TestCase):
    def test_menu_is_untouched_unless_enabled(self):
        for value in ('', 'off', 'yes'):
            with patch.dict(os.environ, {'TELEGRAM_MENU_BUTTON': value}):
                self.assertEqual(bot.menu_setup_calls(bot.menu_button_mode(), 'https://shop.example.kz'), [])

    def test_commands_mode_registers_handled_commands_and_default_button(self):
        calls = dict(bot.menu_setup_calls('commands', 'https://shop.example.kz'))
        commands = [c['command'] for c in calls['setMyCommands']['commands']]
        self.assertEqual(commands, ['search', 'ai', 'watches', 'new', 'status', 'help'])
        self.assertEqual(calls['setChatMenuButton']['menu_button'], {'type': 'commands'})
        source = open(bot.__file__, encoding='utf-8').read()
        for command in commands:  # в меню только команды, которые бот действительно обрабатывает
            self.assertIn(f'"/{command}"', source)

    def test_webapp_button_opens_section_and_needs_https_origin(self):
        with patch.dict(os.environ, {'TELEGRAM_MENU_BUTTON_PATH': '', 'TELEGRAM_MENU_BUTTON_TEXT': ''}):
            calls = dict(bot.menu_setup_calls('webapp', 'https://shop.example.kz/'))
            self.assertEqual(calls['setChatMenuButton']['menu_button'],
                             {'type': 'web_app', 'text': 'Скидки', 'web_app': {'url': 'https://shop.example.kz/deals'}})
            for origin in ('', 'http://192.168.1.5:8080'):
                calls = dict(bot.menu_setup_calls('webapp', origin))
                self.assertEqual(calls['setChatMenuButton']['menu_button'], {'type': 'commands'})

    def test_menu_button_section_is_configurable_and_validated(self):
        with patch.dict(os.environ, {'TELEGRAM_MENU_BUTTON_PATH': '/favorites', 'TELEGRAM_MENU_BUTTON_TEXT': ' Избранное '}):
            self.assertEqual(bot.menu_button_target(), ('/favorites', 'Избранное'))
        for bad in ('https://evil.example', '//evil.example', '/deals?x=1', 'deals'):
            with patch.dict(os.environ, {'TELEGRAM_MENU_BUTTON_PATH': bad, 'TELEGRAM_MENU_BUTTON_TEXT': ''}):
                self.assertEqual(bot.menu_button_target(), ('/deals', 'Скидки'))

if __name__ == '__main__':
    unittest.main()
