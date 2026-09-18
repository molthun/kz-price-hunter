"""Private-chat isolation and bounded Telegram worker regressions (no network)."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _TMP.name
for key in ('TELEGRAM_BOT_TOKEN','DNS_BOT_TOKEN','GEMINI_API_KEY','OPENAI_API_KEY','PUBLIC_ORIGIN','APP_URL','TRUSTED_PROXIES','ADMIN_TELEGRAM_IDS'):
    os.environ[key] = ''
os.environ['ALLOW_DEV_LOGIN'] = '0'
import auth
import telegram_bot as bot


class BotPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_groups_channels_mismatched_users_are_ignored(self):
        with patch.object(bot,'handle_ai_consultant_message',new_callable=AsyncMock) as ai,patch.object(bot,'get_user') as users:
            for kind,chat_id,uid in [('group',-1,123),('supergroup',-2,123),('channel',-3,123),('private',123,456),('',123,123)]:
                await bot.process_telegram_update(None,'fake',{'message':{'chat':{'id':chat_id,'type':kind},'from':{'id':uid},'text':'secret'}})
            ai.assert_not_awaited();users.assert_not_called()

    async def test_private_user_access_and_blocked_user(self):
        msg={'message':{'chat':{'id':777,'type':'private'},'from':{'id':777},'text':'question'}}
        with patch.object(bot,'_message_limiter',auth.RateLimiter(10,60)),patch.object(bot,'get_user',return_value=None),patch.object(bot,'handle_ai_consultant_message',new_callable=AsyncMock) as ai:
            await bot.process_telegram_update(None,'fake',msg);ai.assert_awaited_once()
            with patch.object(bot,'get_user',return_value={'is_blocked':True}):
                await bot.process_telegram_update(None,'fake',msg)
            self.assertEqual(ai.await_count,1)

    async def test_dispatcher_orders_dialog_and_shuts_down_workers(self):
        entered=asyncio.Event();release=asyncio.Event();seen=[]
        async def handle(session,token,update):
            n=update['n'];seen.append(('start',n))
            if n==1:entered.set();await release.wait()
            seen.append(('end',n))
        with patch.object(bot,'process_telegram_update',side_effect=handle):
            dispatcher=bot.TelegramDispatcher(None,'fake',workers=2,capacity=2)
            try:
                for n in (1,2):await dispatcher.submit({'n':n,'message':{'chat':{'id':10}}})
                await asyncio.wait_for(entered.wait(),1);await asyncio.sleep(0)
                self.assertEqual(seen,[('start',1)])
                release.set();await asyncio.wait_for(dispatcher.queue.join(),1)
                self.assertEqual(seen,[('start',1),('end',1),('start',2),('end',2)])
                self.assertEqual(dispatcher.chat_locks,{})
            finally:await dispatcher.close()
            self.assertTrue(all(t.done() for t in dispatcher.tasks))

    async def test_bounded_queue_and_cancellation(self):
        entered=asyncio.Event()
        async def handle(*args):entered.set();await asyncio.Event().wait()
        with patch.object(bot,'process_telegram_update',side_effect=handle):
            dispatcher=bot.TelegramDispatcher(None,'fake',workers=1,capacity=1)
            await dispatcher.submit({'message':{'chat':{'id':1}}})
            await asyncio.wait_for(entered.wait(),1)
            await dispatcher.submit({'message':{'chat':{'id':2}}})
            blocked=asyncio.create_task(dispatcher.submit({'message':{'chat':{'id':3}}}))
            await asyncio.sleep(0);self.assertFalse(blocked.done());self.assertEqual(dispatcher.queue.qsize(),1)
            blocked.cancel();await asyncio.gather(blocked,return_exceptions=True)
            await dispatcher.close();self.assertTrue(all(t.done() for t in dispatcher.tasks))
            self.assertEqual(dispatcher.chat_locks,{})


if __name__=='__main__':unittest.main()
