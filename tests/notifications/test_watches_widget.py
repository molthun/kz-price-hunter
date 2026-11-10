"""TG-07: карточка /watches виджетом Telegram — пределы, выключатель, запросы карточки (без сети)."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import os
import random
import unittest
from unittest.mock import AsyncMock, patch

for key in ('TELEGRAM_BOT_TOKEN', 'DNS_BOT_TOKEN', 'APP_URL', 'PUBLIC_ORIGIN',
            'TELEGRAM_SEARCH_WIDGET', 'TELEGRAM_WATCHES_WIDGET'):
    os.environ[key] = ''
import auth
import telegram_bot as bot
import telegram_widgets as widgets


def _watches(count):
    return [{
        'id': i,
        'user_id': 777,
        'kind': 'product' if i % 2 == 0 else 'search',
        'target': f'Товар {i}',
        'title': f'Название товара {i}',
        'condition': 'target_price' if i % 2 == 0 else 'any_drop',
        'threshold': 150000 + i * 1000 if i % 2 == 0 else None,
        'is_active': 1,
    } for i in range(1, count + 1)]


class WatchesWidgetBuildTests(unittest.TestCase):
    def test_watches_card_fits_telegram_limits_and_keeps_order(self):
        items = _watches(20)
        built = widgets.build_watches_widget(items, now=1_794_300_000)
        self.assertIsNotNone(built)
        html_text, picked = built
        self.assertEqual([w['id'] for w in picked], list(range(1, widgets.MAX_WATCHES + 1)))
        self.assertLessEqual(len(html_text.encode('utf-8')), widgets.HTML_LIMIT)
        self.assertLessEqual(widgets.compressed_size(html_text), widgets.COMPRESSED_LIMIT)
        self.assertNotIn('\x00', html_text)

    def test_format_watch_condition(self):
        w1 = {'condition': 'any_drop'}
        self.assertEqual(widgets._format_watch_condition(w1), 'любое снижение цены')
        w2 = {'condition': 'target_price', 'threshold': 250000}
        self.assertEqual(widgets._format_watch_condition(w2), 'цена ниже 250 000 ₸')
        w3 = {'condition': 'drop_pct', 'threshold': 15}
        self.assertEqual(widgets._format_watch_condition(w3), 'скидка от 15%')
        w4 = {'condition': 'drop_amount', 'threshold': 5000}
        self.assertEqual(widgets._format_watch_condition(w4), 'снижение от 5 000 ₸')
        w5 = {'condition': 'in_stock'}
        self.assertEqual(widgets._format_watch_condition(w5), 'появление в наличии')
        w6 = {'condition': 'best_price'}
        self.assertEqual(widgets._format_watch_condition(w6), 'лучшая цена')

    def test_kind_icons(self):
        self.assertEqual(widgets._kind_icon('search'), '🔍')
        self.assertEqual(widgets._kind_icon('product'), '📦')
        self.assertEqual(widgets._kind_icon('category'), '🏷')
        self.assertEqual(widgets._kind_icon('unknown'), '🔔')

    def test_text_cannot_break_out_of_script(self):
        items = [{
            'id': 1,
            'title': '</script><img src=x onerror=alert(1)> ',
            'target': 'test',
            'condition': 'any_drop',
            'is_active': 1
        }]
        built = widgets.build_watches_widget(items)
        self.assertIsNotNone(built)
        html_text, _ = built
        self.assertEqual(html_text.count('</script>'), 1)
        self.assertNotIn(' ', html_text)

    def test_empty_watches_returns_none(self):
        self.assertIsNone(widgets.build_watches_widget([]))
        self.assertIsNone(widgets.build_watches_widget([{'id': 0}]))

    def test_caption_escaped_and_shows_counts(self):
        items = _watches(5)
        built = widgets.build_watches_widget(items)
        self.assertIsNotNone(built)
        _, picked = built
        caption = widgets.build_watches_caption(picked, 12)
        self.assertIn('12 активных, показано 5', caption)
        caption_full = widgets.build_watches_caption(picked, 5)
        self.assertIn('5 активных', caption_full)
        self.assertNotIn('показано', caption_full)

    def test_chat_data_tracks_active_ids(self):
        items = _watches(4)
        built = widgets.build_watches_widget(items)
        self.assertIsNotNone(built)
        _, picked = built
        data_str = widgets.build_watches_chat_data(picked, {1, 3}, now=100)
        data = json.loads(data_str)
        self.assertEqual(data, {'t': 100, 'a': [1, 3]})

    def test_switch_defaults_off_and_respects_modes(self):
        with patch.dict(os.environ, {'TELEGRAM_WATCHES_WIDGET': ''}):
            self.assertFalse(widgets.watches_widget_enabled(1))
        with patch.dict(os.environ, {'TELEGRAM_WATCHES_WIDGET': 'admins'}), \
                patch.object(widgets.config, 'ADMIN_TELEGRAM_IDS', {777}):
            self.assertTrue(widgets.watches_widget_enabled(777))
            self.assertFalse(widgets.watches_widget_enabled(888))
        with patch.dict(os.environ, {'TELEGRAM_WATCHES_WIDGET': 'all'}):
            self.assertTrue(widgets.watches_widget_enabled(888))


class WatchesCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._widget_cards.clear()

    async def _watches_cmd(self, mode, widget_result, watches_list):
        with patch.dict(os.environ, {'TELEGRAM_WATCHES_WIDGET': mode}), \
                patch('database.list_watches', return_value=watches_list), \
                patch.object(bot, 'send_tg_widget', new_callable=AsyncMock, return_value=widget_result) as widget, \
                patch.object(bot, 'send_tg_message', new_callable=AsyncMock) as text:
            await bot.handle_watches_command(None, 'fake', 777)
        return widget, text

    async def test_switch_off_sends_text_with_inline_buttons(self):
        widget, text = await self._watches_cmd('off', 55, _watches(3))
        widget.assert_not_awaited()
        text.assert_awaited_once()
        sent_text = text.call_args[0][3]
        self.assertIn('Ваши активные наблюдения (3)', sent_text)

    async def test_empty_watches_sends_text_even_if_widget_enabled(self):
        widget, text = await self._watches_cmd('all', 55, [])
        widget.assert_not_awaited()
        text.assert_awaited_once()
        self.assertIn('нет активных наблюдений', text.call_args[0][3])

    async def test_switch_on_sends_widget_and_saves_card(self):
        widget, text = await self._watches_cmd('all', 55, _watches(4))
        widget.assert_awaited_once()
        text.assert_not_awaited()
        card = bot._widget_cards.get((777, 55))
        self.assertIsNotNone(card)
        self.assertEqual(card['type'], 'watches')
        self.assertEqual([w['id'] for w in card['watches']], [1, 2, 3, 4])

    async def test_rejected_widget_falls_back_to_text(self):
        widget, text = await self._watches_cmd('all', None, _watches(3))
        widget.assert_awaited_once()
        text.assert_awaited_once()
        self.assertEqual(len(bot._widget_cards), 0)


class WatchesQueryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._widget_cards.clear()
        built = widgets.build_watches_widget(_watches(3))
        _, picked = built
        bot._widget_cards[(777, 55)] = {
            'type': 'watches',
            'watches': picked,
            'refreshed': 0.0,
        }
        limiter = patch.object(bot, '_widget_limiter', auth.RateLimiter(20, 60))
        limiter.start()
        self.addCleanup(limiter.stop)

    @staticmethod
    def _update(data, chat_id=777, user_id=777, kind='private', message_id=55):
        return {'widget_query': {'id': 'wq1', 'from': {'id': user_id}, 'chat': {'id': chat_id, 'type': kind},
                                 'message_id': message_id, 'data': data}}

    async def _run(self, update, user={'id': 777}):
        with patch.object(bot, 'get_user', return_value=user), \
                patch.object(bot, 'answer_widget_query', new_callable=AsyncMock) as answer:
            await bot.process_telegram_update(None, 'fake', update)
        return answer

    async def test_delete_watch_via_widget_query(self):
        with patch('database.delete_watch', return_value=True) as del_mock, \
                patch('database.list_watches', return_value=[_watches(3)[0], _watches(3)[2]]), \
                patch.object(bot, 'edit_tg_widget_data', new_callable=AsyncMock, return_value=True) as edit_mock:
            answer = await self._run(self._update('d2'))
        del_mock.assert_called_once_with(777, 2)
        edit_mock.assert_awaited_once()
        chat_data = json.loads(edit_mock.call_args[0][4])
        self.assertEqual(chat_data['a'], [1, 3])
        self.assertIn('отключено', answer.call_args[0][3])

    async def test_delete_inactive_watch_reports_already_inactive(self):
        with patch('database.delete_watch', return_value=False):
            answer = await self._run(self._update('d999'))
        self.assertIn('уже не активно', answer.call_args[0][3])

    async def test_invalid_delete_id_reports_error(self):
        answer = await self._run(self._update('dxyz'))
        self.assertIn('Некорректный', answer.call_args[0][3])

    async def test_refresh_watches_updates_chat_data_and_cools_down(self):
        with patch('database.list_watches', return_value=[_watches(3)[0]]), \
                patch.object(bot, 'edit_tg_widget_data', new_callable=AsyncMock, return_value=True) as edit_mock:
            answer = await self._run(self._update('r'))
            self.assertEqual(answer.call_args[0][3], 'Список обновлён')
            chat_data = json.loads(edit_mock.call_args[0][4])
            self.assertEqual(chat_data['a'], [1])

            # Immediate repeat triggers cooldown
            answer_repeat = await self._run(self._update('r'))
            self.assertIn('только что обновлён', answer_repeat.call_args[0][3])


if __name__ == '__main__':
    unittest.main()
