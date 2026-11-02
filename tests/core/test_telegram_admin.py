from tests import test_support  # noqa: F401
import asyncio
import copy
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import config
import database
import telegram_admin as ta


class TelegramAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(config, 'SETTINGS_FILE', root/'settings.json'),
                        patch.object(database, 'DB_PATH', root/'prices.db'),
                        patch.object(config, 'DATA_DIR', root),
                        patch.object(config, 'get_bot_token', return_value='fixture-private-token')]
        for p in self.patches: p.start()
        database.init_db()
        config.save_settings({'local_api_base': 'http://fixture/v1', 'local_model': 'fixture:8b', 'local_api_key': 'fixture-private-key'})
        ta._diagnoses.clear()
        self.state = copy.deepcopy(ta._polling)
        self.check = copy.deepcopy(ta._check), ta._check_token

    def tearDown(self):
        ta._polling = self.state
        ta._check, ta._check_token = self.check
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def body(self):
        v = ta.view()
        return dict(revision=v['revision'], **v['settings'])

    def test_scoped_save_conflict_preserves_keys_and_unrelated_changes(self):
        before = config.load_settings()
        body = self.body();body['daily_digest_hour'] = 0
        config.save_settings({'scan_interval_minutes': 187})
        ta.save(body)
        after = config.load_settings()
        self.assertEqual(after['scan_interval_minutes'], 187)
        self.assertEqual(after['local_api_key'], before['local_api_key'])
        self.assertEqual(after['daily_digest_hour'], 0)
        with self.assertRaises(config.TelegramSettingsConflict): ta.save(body)
        for bad in [dict(daily_digest_hour=True),dict(daily_digest_hour=24),dict(daily_digest_telegram_enabled='false'),dict(telegram_bot_token='new')]:
            body=self.body();body.update(bad)
            with self.assertRaises(ValueError): ta.save(body)

    def test_timezone_change_invalidates_hour_draft(self):
        body = self.body()
        config.save_settings({'daily_digest_timezone': 'UTC'})
        with self.assertRaises(config.TelegramSettingsConflict): ta.save(body)

    def test_snapshot_no_network_secrets_or_personal_data(self):
        with patch('notifier.telegram_api') as telegram, patch('ai_service.call_local_api') as ai:
            data = ta.snapshot()
            telegram.assert_not_called();ai.assert_not_called()
        encoded = json.dumps(data)
        self.assertNotIn('fixture-private', encoded)
        self.assertNotIn('user_id', encoded)
        self.assertIsNone(data['notifications']['last_delivery_at'])

    def test_check_only_getme_and_safe_error_classification(self):
        response=Mock(status_code=200);response.json.return_value={'ok':True,'result':{'username':'fixture_bot','id':123}}
        with patch('notifier.telegram_api', return_value=response) as telegram:
            result=ta.check_connection();telegram.assert_called_once_with('getMe',{},timeout=10)
            self.assertEqual(result['check']['state'],'ok')
            response.status_code=401
            self.assertEqual(ta.check_connection()['check']['state'],'unauthorized')
        with patch('notifier.telegram_api', side_effect=RuntimeError('fixture-private-token')):
            result=ta.check_connection()
            self.assertEqual(result['check']['state'],'network_error')
            self.assertNotIn('fixture-private-token',json.dumps(result))
        with patch.object(config,'get_bot_token',return_value='changed-token'):
            self.assertEqual(ta.connection_view()['check']['state'],'not_checked')

    def test_polling_freshness_and_last_success_are_distinct_from_delivery(self):
        ta.mark_polling('ok');successful=ta.polling_view()['last_success_at']
        ta.mark_polling('conflict');self.assertEqual(ta.polling_view()['last_success_at'],successful)
        self.assertTrue(ta.polling_view()['fresh'])
        ta._polling['at']='2020-01-01T00:00:00+00:00'
        self.assertFalse(ta.polling_view()['fresh'])

    async def test_local_only_diagnosis_validated_cached_no_secrets(self):
        data=ta.snapshot();codes=ta.issues(data)
        with patch('ai_service.call_local_api',new_callable=AsyncMock,return_value={'order':codes[::-1]}) as local:
            r=await ta.explain(data);self.assertEqual(r['status'],'ready')
            self.assertEqual([a['code'] for a in r['actions']],codes[::-1])
            prompt=local.call_args.args[0]
            for secret in ['fixture-private-token','fixture-private-key','username','user_id']:self.assertNotIn(secret,prompt)
            self.assertTrue((await ta.explain(data))['cached']);self.assertEqual(local.await_count,1)

    async def test_invalid_or_unavailable_model_keeps_confirmed_actions(self):
        data=ta.snapshot()
        with patch('ai_service.call_local_api',new_callable=AsyncMock,return_value={'order':['invented']}) as local:
            r=await ta.explain(data);self.assertEqual(r['status'],'unavailable')
            self.assertEqual([a['code'] for a in r['actions']],ta.issues(data))
        with patch('local_ai.pause_reason',return_value='game'),patch('ai_service.call_local_api',new_callable=AsyncMock) as local:
            self.assertEqual((await ta.explain(data))['status'],'unavailable');local.assert_not_called()

    async def test_busy_diagnosis_does_not_duplicate_model_call(self):
        data=ta.snapshot();started=asyncio.Event();release=asyncio.Event()
        async def answer(*a,**k):started.set();await release.wait();return {'order':ta.issues(data)}
        with patch('ai_service.call_local_api',side_effect=answer) as local:
            task=asyncio.create_task(ta.explain(data));await started.wait()
            self.assertEqual((await ta.explain(data))['status'],'busy')
            release.set();await task;self.assertEqual(local.call_count,1)

    async def test_actual_polling_loop_records_success_then_token_failure(self):
        import telegram_bot as bot
        from unittest.mock import MagicMock
        session = MagicMock()
        session.__aenter__.return_value = session
        good, bad = MagicMock(), MagicMock()
        good.status = 200
        good.json = AsyncMock(return_value={'ok': True, 'result': []})
        bad.status = 401
        contexts = []
        for response in (good, bad):
            context = MagicMock();context.__aenter__.return_value = response;contexts.append(context)
        session.get.side_effect = contexts
        with patch.object(bot, 'get_bot_token', return_value='fixture-token'), patch.object(bot.aiohttp, 'ClientSession', return_value=session), patch.object(bot, 'send_tg_message', new_callable=AsyncMock) as sends:
            await bot.run_telegram_bot_task()
            self.assertEqual(ta.polling_view()['state'], 'unauthorized')
            self.assertIsNotNone(ta.polling_view()['last_success_at'])
            sends.assert_not_called()

    async def test_http_roles_revision_and_rate_limit(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        from web import server
        @web.middleware
        async def role(req,handler):
            if req.headers.get('X-Test-Role'):req['user']={'id':1,'is_admin':req.headers['X-Test-Role']=='admin'}
            return await handler(req)
        app=web.Application(middlewares=[role])
        for method,path,handler in [('GET','/settings',server.telegram_admin_handler),('POST','/settings',server.save_telegram_admin_handler),('POST','/check',server.check_telegram_admin_handler),('POST','/explain',server.explain_telegram_admin_handler)]:app.router.add_route(method,path,handler)
        with patch('auth.is_admin',side_effect=lambda u:bool(u and u.get('is_admin'))):
            async with TestClient(TestServer(app)) as client:
                for method,path in [('GET','/settings'),('POST','/settings'),('POST','/check'),('POST','/explain')]:
                    for role,status in [(None,401),('user',403)]:
                        r=await client.request(method,path,headers={'X-Test-Role':role} if role else {})
                        self.assertEqual(r.status,status)
                h={'X-Test-Role':'admin'};body=self.body();body['daily_digest_hour']=7
                self.assertEqual((await client.post('/settings',headers=h,json=body)).status,200)
                self.assertEqual((await client.post('/settings',headers=h,json=body)).status,409)
                with patch.object(server.telegram_admin_check_limiter,'retry_after',return_value=3),patch('notifier.telegram_api') as tg:
                    self.assertEqual((await client.post('/check',headers=h)).status,429);tg.assert_not_called()
