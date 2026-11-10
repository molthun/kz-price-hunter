"""TG-02: черновик хода работы AI-консультанта и кнопка «Стоп» (без сети и без настоящего AI)."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

for key in ('TELEGRAM_BOT_TOKEN', 'DNS_BOT_TOKEN', 'APP_URL', 'PUBLIC_ORIGIN', 'TELEGRAM_AI_DRAFTS'):
    os.environ[key] = ''
import ai_service
import telegram_bot as bot

ANSWER = {'answer': 'Берите **первый**', 'products': [], 'history_turn': 'Берите первый'}


class DraftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._active_generations.clear()
        bot.reset_chat_history(777)
        self.drafts = []      # (draft_id, text) каждого отправленного черновика
        self.draft_ok = True

        async def fake_draft(session, token, chat_id, draft_id, text='', can_stop=True):
            self.drafts.append((draft_id, text))
            return self.draft_ok
        for target, replacement in (('send_tg_draft', fake_draft),):
            patcher = patch.object(bot, target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.message = patch.object(bot, 'send_tg_message', new_callable=AsyncMock).start()
        self.typing = patch.object(bot, 'send_tg_chat_action', new_callable=AsyncMock).start()
        self.addCleanup(patch.stopall)

    async def _ask(self, mode, consultant):
        with patch.dict(os.environ, {'TELEGRAM_AI_DRAFTS': mode}), \
                patch.object(ai_service, 'ask_ai_consultant', side_effect=consultant):
            await bot.handle_ai_consultant_message(None, 'fake', 777, 'ноутбук')

    async def test_switch_off_keeps_typing_and_sends_no_draft(self):
        seen = {}

        async def consultant(**kwargs):
            seen.update(kwargs)
            return ANSWER
        await self._ask('off', consultant)
        self.assertEqual(self.drafts, [])
        self.typing.assert_awaited_once()
        self.assertNotIn('on_stage', seen)
        self.message.assert_awaited_once()

    async def test_draft_starts_empty_then_shows_stages_and_final_message_follows(self):
        async def consultant(message, history, city, on_stage):
            on_stage('search', 0)
            await asyncio.sleep(0)
            on_stage('generate', 12)
            await asyncio.sleep(0)
            on_stage('unknown-stage', 1)     # неизвестный этап текст не меняет
            return ANSWER
        await self._ask('all', consultant)
        texts = [text for _, text in self.drafts]
        self.assertEqual(texts[0], '')                      # пустой черновик = «Думаю…»
        self.assertIn('Ищу подходящие товары', texts[1])
        self.assertIn('Нашёл предложений: 12', texts[2])
        self.assertEqual(len(texts), 3)
        self.assertEqual(len({draft_id for draft_id, _ in self.drafts}), 1)   # один черновик, а не три
        self.assertNotEqual(self.drafts[0][0], 0)
        self.typing.assert_not_awaited()
        self.assertIn('Берите <b>первый</b>', self.message.call_args[0][3])
        self.assertEqual(bot._active_generations, {})

    async def test_rejected_draft_falls_back_to_typing(self):
        self.draft_ok = False
        seen = {}

        async def consultant(**kwargs):
            seen.update(kwargs)
            return ANSWER
        await self._ask('all', consultant)
        self.assertEqual(len(self.drafts), 1)
        self.typing.assert_awaited_once()
        self.assertNotIn('on_stage', seen)
        self.message.assert_awaited_once()
        self.assertEqual(bot._active_generations, {})

    async def test_keepalive_resends_current_text_while_model_is_slow(self):
        release = asyncio.Event()

        async def consultant(message, history, city, on_stage):
            on_stage('search', 0)
            await release.wait()
            return ANSWER
        with patch.object(bot, 'DRAFT_KEEPALIVE_SECONDS', 0.01):
            task = asyncio.create_task(self._ask('all', consultant))
            await asyncio.sleep(0.08)
            release.set()
            await task
        repeats = [text for _, text in self.drafts if 'Ищу подходящие товары' in text]
        self.assertGreaterEqual(len(repeats), 3)
        sent = len(self.drafts)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.drafts), sent)            # после ответа черновик больше не продлевается

    async def test_stop_cancels_model_call_and_does_not_remember_turn(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def consultant(message, history, city, on_stage):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return ANSWER
        task = asyncio.create_task(self._ask('all', consultant))
        await asyncio.wait_for(started.wait(), 1)
        draft_id = self.drafts[0][0]
        update = {'stopped_message_generation': {'chat': {'id': 777, 'type': 'private'}, 'draft_id': draft_id}}
        dispatcher = bot.TelegramDispatcher(None, 'fake', workers=1, capacity=1)
        try:
            await dispatcher.submit(update)                 # мимо очереди чата
            self.assertTrue(dispatcher.queue.empty())
        finally:
            await dispatcher.close()
        await asyncio.wait_for(task, 1)
        self.assertTrue(cancelled.is_set())
        self.message.assert_awaited_once()
        self.assertIn('Остановлено', self.message.call_args[0][3])
        self.assertEqual(bot._get_chat_history(777), [])
        self.assertEqual(bot._active_generations, {})

    async def test_stop_for_unknown_or_foreign_draft_is_ignored(self):
        progress = bot.DraftProgress(None, 'fake', 777)
        await progress.start()
        try:
            for stopped in ({'chat': {'id': 778, 'type': 'private'}, 'draft_id': progress.draft_id},
                            {'chat': {'id': 777, 'type': 'private'}, 'draft_id': progress.draft_id + 1},
                            {'chat': {'id': 777, 'type': 'group'}, 'draft_id': progress.draft_id}):
                self.assertFalse(bot.handle_generation_stopped(stopped))
            self.assertFalse(progress.stopped)
        finally:
            await progress.close()

    async def test_bot_shutdown_cancels_handler_instead_of_answering(self):
        started = asyncio.Event()

        async def consultant(message, history, city, on_stage):
            started.set()
            await asyncio.sleep(30)
        task = asyncio.create_task(self._ask('all', consultant))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.message.assert_not_awaited()
        self.assertEqual(bot._active_generations, {})


class ConsultantStageHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_stages_are_reported_and_broken_hook_does_not_break_answer(self):
        stages = []

        def hook(name, count):
            stages.append((name, count))
            raise RuntimeError('сбой в наблюдателе не должен мешать ответу')
        items = [{'id': f'p{i}', 'title': f'Ноутбук {i}', 'shop': 'S', 'current_price': 100 + i} for i in range(15)]
        with patch.object(ai_service, '_get_api_credentials', return_value={'has_ai': True}), \
                patch.object(ai_service, 'parse_natural_query', new_callable=AsyncMock, return_value=None), \
                patch('search_engine.search_in_database', return_value=items), \
                patch('ai_router.run', new_callable=AsyncMock,
                      return_value={'result': {'answer': 'Ответ', 'recommended_product_ids': ['p1']}}):
            result = await ai_service.ask_ai_consultant('ноутбук', on_stage=hook)
        self.assertEqual(stages, [('search', 0), ('generate', 15)])
        self.assertEqual(result['answer'], 'Ответ')


if __name__ == '__main__':
    unittest.main()
