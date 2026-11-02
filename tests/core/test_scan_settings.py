from tests import test_support  # noqa: F401
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import config,scan_settings as ss

class ScanSettingsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'settings.json';self.p=patch.object(config,'SETTINGS_FILE',self.file);self.p.start()
  config.save_settings(dict(scan_interval_minutes=187,enabled_shops={k:i%2==0 for i,k in enumerate(config.SHOP_KEYS)},local_model='fixture:8b',local_api_key='fixture-private',local_normalize_enabled=False))
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def body(self):
  v=ss.view();return dict(revision=v['revision'],scan_interval_minutes=v['settings']['scan_interval_minutes'],enabled_shops={s['id']:s['enabled'] for s in v['settings']['shops']})
 def test_exact_period_and_shop_choices_other_settings_preserved(self):
  before=config.load_settings();v=ss.view();self.assertEqual(v['settings']['scan_interval_minutes'],187)
  self.assertEqual(len(v['settings']['shops']),len(config.SHOP_KEYS));self.assertNotIn('fixture-private',json.dumps(v))
  body=self.body();first=next(iter(config.SHOP_KEYS));body['enabled_shops'][first]=not body['enabled_shops'][first];ss.save(body)
  after=config.load_settings();self.assertEqual(after['scan_interval_minutes'],187)
  self.assertEqual({k:v for k,v in before.items() if k not in ss.FIELDS},{k:v for k,v in after.items() if k not in ss.FIELDS})
 def test_stale_scan_revision_rejected_but_local_changes_independent(self):
  body=self.body();local_rev=config.local_connection_revision(config.load_settings())
  config.save_settings({'scan_interval_minutes':180})
  self.assertEqual(config.local_connection_revision(config.load_settings()),local_rev)
  with self.assertRaises(config.ScanSettingsConflict):ss.save(body)
  body=self.body();config.save_settings({'local_model':'fixture:other'});ss.save(body)
  self.assertEqual(config.load_settings()['local_model'],'fixture:other')
 def test_invalid_partial_or_unknown_map_and_overposting_never_write(self):
  before=self.file.read_bytes()
  for extra in [dict(scan_interval_minutes=True),dict(scan_interval_minutes=0),dict(scan_interval_minutes=43201),dict(enabled_shops={}),dict(enabled_shops={k:'false' for k in config.SHOP_KEYS}),dict(ai_search_enabled=False)]:
   body=self.body();body.update(extra)
   with self.assertRaises(ValueError):ss.save(body)
  self.assertEqual(self.file.read_bytes(),before)
 def test_all_off_allowed_and_schedule_summary_uses_existing_policy(self):
  body=self.body();body['enabled_shops']={k:False for k in config.SHOP_KEYS};ss.save(body)
  self.assertFalse(any(s['enabled'] for s in ss.view()['settings']['shops']))
  self.assertGreaterEqual(ss.view()['schedule']['base_wave_interval_minutes'],5)
  self.assertEqual(ss.runtime({'is_running':True,'progress_pct':200,'error':'secret provider text'})['progress_pct'],100)
  self.assertNotIn('secret provider text',json.dumps(ss.runtime({'error':'secret provider text'})))

class ScanSettingsHttpTests(unittest.IsolatedAsyncioTestCase):
 async def test_admin_only_and_scoped_response(self):
  from aiohttp import web
  from aiohttp.test_utils import TestClient,TestServer
  from web import server
  with tempfile.TemporaryDirectory() as directory,patch.object(config,'SETTINGS_FILE',Path(directory)/'settings.json'):
   @web.middleware
   async def role(req,handler):
    if req.headers.get('X-Test-Role'):req['user']={'id':1,'is_admin':req.headers['X-Test-Role']=='admin'}
    return await handler(req)
   app=web.Application(middlewares=[role]);app.router.add_get('/settings',server.scan_settings_handler);app.router.add_post('/settings',server.save_scan_settings_handler)
   with patch('auth.is_admin',side_effect=lambda u:bool(u and u.get('is_admin'))):
    async with TestClient(TestServer(app)) as client:
     for method in (client.get,client.post):
      self.assertEqual((await method('/settings')).status,401);self.assertEqual((await method('/settings',headers={'X-Test-Role':'user'})).status,403)
     headers={'X-Test-Role':'admin'};v=await (await client.get('/settings',headers=headers)).json()
     body=dict(revision=v['revision'],scan_interval_minutes=187,enabled_shops={s['id']:s['enabled'] for s in v['settings']['shops']})
     self.assertEqual((await client.post('/settings',headers=headers,json=body)).status,200)
     self.assertEqual((await client.post('/settings',headers=headers,json=body)).status,409)
