from tests import test_support  # noqa: F401
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import config
import database
import service_monitor as sm


class ServiceMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.patches=[patch.object(config,'SETTINGS_FILE',root/'settings.json'),patch.object(database,'DB_PATH',root/'prices.db'),patch.object(config,'DATA_DIR',root)]
        for p in self.patches:p.start()
        database.init_db();config.save_settings({'local_api_base':'http://fixture/v1','local_model':'fixture:8b','local_api_key':'fixture-private'})
        sm._cache.clear()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def snapshot(self,**runtime):
        return sm.snapshot({'fixture':('Fixture shop',1)},['fixture'],{},10800,runtime)
    def test_empty_observations_are_unknown_not_healthy(self):
        d=self.snapshot()
        self.assertEqual(d['status'],'unknown')
        self.assertEqual(d['sections']['local']['status'],'unknown')
        self.assertEqual(d['sections']['shops']['counts']['unknown'],1)
        self.assertIsNone(d['sections']['shops']['items'][0]['last_items'])
        self.assertIsNone(d['sections']['shops']['items'][0]['categories_problem'])
        self.assertEqual(d['sections']['notifications']['status'],'unknown')
        self.assertIn('unknown',sm.issue_codes(d))
        self.assertNotIn('fixture-private',json.dumps(d))
    def test_unreadable_queue_is_not_zero_or_healthy_other_blocks_survive(self):
        with patch.object(database,'spool_stats',side_effect=sqlite3.OperationalError('fixture-private')):
            d=self.snapshot(worker_running=True)
        self.assertEqual(d['sections']['queue'],{'available':False,'status':'unknown'})
        self.assertTrue(d['sections']['shops']['available'])
        self.assertIn('unreadable',sm.issue_codes(d));self.assertNotIn('fixture-private',json.dumps(d))
    def test_pending_queue_is_not_assumed_stuck_and_worker_stop_is_distinct(self):
        counts=dict(pending=12,processing=3,dead_letter=0,rejected=0)
        with patch.object(database,'spool_stats',return_value=counts):
            self.assertEqual(sm.queue({'worker_running':True})['status'],'healthy')
            self.assertEqual(sm.queue({'worker_running':False})['status'],'degraded')
            self.assertEqual(sm.queue({})['status'],'unknown')
    def test_disabled_telemetry_cannot_turn_empty_error_log_green(self):
        with patch('telemetry.telemetry_enabled',return_value=False):
            e=sm.events({'telemetry_flusher_alive':True})
        self.assertEqual(e['status'],'unknown');self.assertFalse(e['enabled'])
    def test_observed_ai_aggregates_only_local_actual_calls_and_no_raw_data(self):
        import datetime
        at=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        with database.get_connection() as c:
            for n,(provider,outcome,called) in enumerate([('local','ok',True),('local','timeout',True),('local','local_paused',False),('local','busy',True),('openai','ok',True)]):
                c.execute('INSERT INTO telemetry_events(event_id,timestamp,type,severity,component,message,data_json) VALUES(?,?,?,?,?,?,?)',(str(n),at,'ai_query','INFO','ai','fixture-private',json.dumps(dict(provider=provider,outcome=outcome,provider_called=called))))
            c.commit()
        d=sm.local();self.assertEqual(d['calls_24h'],2);self.assertEqual(d['errors_24h'],1);self.assertFalse(d['live_probe']);self.assertEqual(d['status'],'limited');self.assertNotIn('fixture-private',json.dumps(d))
    def test_mixed_unknown_and_healthy_shops_stays_unknown(self):
        self.assertEqual(sm.worst(['healthy','unknown','disabled']),'unknown')
        self.assertEqual(sm.worst(['disabled']),'unknown')
    async def test_local_only_validated_cached_advice_has_no_source_or_secrets(self):
        d=self.snapshot();codes=sm.issue_codes(d)
        with patch('ai_service.call_local_api',new_callable=AsyncMock,return_value={'order':codes[::-1]}) as local:
            r=await sm.explain(d);self.assertEqual(r['status'],'ready');self.assertEqual([a['code'] for a in r['actions']],codes[::-1])
            self.assertTrue((await sm.explain(d))['cached']);self.assertEqual(local.await_count,1)
            prompt=local.call_args.args[0]
            for text in ['fixture-private','Fixture shop','http://fixture','username','user_id']:self.assertNotIn(text,prompt)
    async def test_invalid_or_paused_model_preserves_confirmed_actions(self):
        d=self.snapshot()
        with patch('ai_service.call_local_api',new_callable=AsyncMock,return_value={'order':['invented']}):
            r=await sm.explain(d);self.assertEqual(r['status'],'unavailable');self.assertEqual([a['code'] for a in r['actions']],sm.issue_codes(d))
        with patch('local_ai.pause_reason',return_value='game'),patch('ai_service.call_local_api',new_callable=AsyncMock) as local:
            self.assertEqual((await sm.explain(d))['status'],'unavailable');local.assert_not_called()
    async def test_http_roles_and_rate_limit_do_not_call_model(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        from web import server
        @web.middleware
        async def role(req,handler):
            if req.headers.get('X-Test-Role'):req['user']={'id':1,'is_admin':req.headers['X-Test-Role']=='admin'}
            return await handler(req)
        app=web.Application(middlewares=[role]);app.router.add_get('/overview',server.service_overview_handler);app.router.add_post('/explain',server.service_overview_explain_handler)
        with patch('auth.is_admin',side_effect=lambda u:bool(u and u.get('is_admin'))),patch.object(server,'_service_monitor_arguments',return_value=({'fixture':('Fixture',1)},['fixture'],{},10800,{})):
            async with TestClient(TestServer(app)) as c:
                for method,path in [('GET','/overview'),('POST','/explain')]:
                    for role,status in [(None,401),('user',403)]:
                        self.assertEqual((await c.request(method,path,headers={'X-Test-Role':role} if role else {})).status,status)
                h={'X-Test-Role':'admin'}
                self.assertEqual((await c.get('/overview',headers=h)).status,200)
                with patch.object(server.service_monitor_ai_limiter,'retry_after',return_value=2),patch('ai_service.call_local_api',new_callable=AsyncMock) as local:
                    self.assertEqual((await c.post('/explain',headers=h)).status,429);local.assert_not_called()
    async def test_busy_model_is_not_called_twice(self):
        d=self.snapshot();started=asyncio.Event();release=asyncio.Event()
        async def run(*a,**k):started.set();await release.wait();return {'order':sm.issue_codes(d)}
        with patch('ai_service.call_local_api',side_effect=run) as local:
            t=asyncio.create_task(sm.explain(d));await started.wait()
            self.assertEqual((await sm.explain(d))['status'],'busy');release.set();await t;self.assertEqual(local.call_count,1)
