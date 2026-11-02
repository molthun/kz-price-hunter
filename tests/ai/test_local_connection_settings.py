from tests import test_support  # noqa: F401
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
import config,local_ai_settings as ls

class LocalConnectionSettingsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'settings.json'
  self.file.write_text(json.dumps(dict(local_api_base='http://localhost:11434/v1',local_model='fixture:8b',local_api_key='fixture-private-key',scan_interval_minutes=120,local_normalize_enabled=False,ai_provider='auto',openai_api_key='fixture-other-key')))
  self.p=patch.object(config,'SETTINGS_FILE',self.file);self.p.start()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def body(self,**extra):return {**dict(revision=ls.view()['revision'],local_api_base='http://localhost:11434/v1',local_model='fixture:new',key_action='preserve'),**extra}
 def test_read_hides_key_and_save_preserves_other_settings(self):
  before=config.load_settings();public=ls.view();self.assertNotIn('fixture-private-key',json.dumps(public))
  ls.save(self.body());after=config.load_settings()
  self.assertEqual(after['local_model'],'fixture:new');self.assertEqual(after['local_api_key'],before['local_api_key'])
  self.assertEqual({k:v for k,v in before.items() if k not in ls.FIELDS},{k:v for k,v in after.items() if k not in ls.FIELDS})
 def test_explicit_key_replacement_removal_and_stale_revision(self):
  old=self.body();ls.save(self.body(key_action='replace',local_api_key='fixture-new-key'))
  with self.assertRaises(config.LocalSettingsConflict):ls.save(old)
  self.assertEqual(config.load_settings()['local_api_key'],'fixture-new-key')
  ls.save(self.body(key_action='remove'));self.assertEqual(config.load_settings()['local_api_key'],'')
 def test_other_fields_overposting_invalid_url_and_bad_secret_do_not_write(self):
  before=self.file.read_bytes()
  for values in [dict(scan_interval_minutes=1),dict(local_api_base='file:///tmp'),dict(local_api_base='http://name:pass@localhost/v1'),dict(local_model='bad model'),dict(key_action='replace',local_api_key='secret\nheader')]:
   body=self.body();body.update(values)
   with self.assertRaises(ValueError):ls.save(body)
  self.assertEqual(self.file.read_bytes(),before)
 def test_check_uses_preserved_or_removed_key_and_does_not_save(self):
  before=self.file.read_bytes();self.assertEqual(ls.for_check(self.body())[2],'fixture-private-key')
  self.assertEqual(ls.for_check(self.body(key_action='remove'))[2],'');self.assertEqual(self.file.read_bytes(),before)
  self.assertNotIn('fixture-private-key',json.dumps(ls.public_check({'models':['fixture-private-key'],'error':'echo fixture-private-key'},'fixture-private-key')))

class LocalConnectionHttpTests(unittest.IsolatedAsyncioTestCase):
 async def test_roles_patch_and_check_without_secret_echo(self):
  from aiohttp import web
  from aiohttp.test_utils import TestClient,TestServer
  from web import server
  with tempfile.TemporaryDirectory() as directory,patch.object(config,'SETTINGS_FILE',Path(directory)/'settings.json'):
   config.save_settings(dict(local_api_base='http://localhost:11434/v1',local_model='fixture:8b',local_api_key='fixture-private-key'))
   @web.middleware
   async def role(req,handler):
    if req.headers.get('X-Test-Role'):req['user']={'id':1,'is_admin':req.headers['X-Test-Role']=='admin'}
    return await handler(req)
   app=web.Application(middlewares=[role]);app.router.add_get('/settings',server.local_connection_settings_handler);app.router.add_post('/settings',server.save_local_connection_handler);app.router.add_post('/check',server.check_local_connection_handler)
   with patch('auth.is_admin',side_effect=lambda u:bool(u and u.get('is_admin'))),patch('ai_service.check_local_model',new=AsyncMock(return_value={'answered':True,'error':'fixture-private-key'})) as provider:
    async with TestClient(TestServer(app)) as client:
     for path,method in [('/settings',client.get),('/settings',client.post),('/check',client.post)]:
      self.assertEqual((await method(path)).status,401);self.assertEqual((await method(path,headers={'X-Test-Role':'user'})).status,403)
     headers={'X-Test-Role':'admin'};data=await (await client.get('/settings',headers=headers)).json();self.assertNotIn('fixture-private-key',json.dumps(data))
     body=dict(revision=data['revision'],local_api_base='http://localhost:11434/v1',local_model='fixture:8b',key_action='preserve')
     response=await client.post('/check',headers=headers,json=body);self.assertEqual(response.status,200);self.assertNotIn('fixture-private-key',await response.text());provider.assert_awaited_once()
     response=await client.post('/settings',headers=headers,json={**body,'local_model':'fixture:next'});self.assertEqual(response.status,200)
     self.assertEqual((await client.post('/settings',headers=headers,json=body)).status,409)
     self.assertEqual((await client.post('/check',headers=headers,json=body)).status,409)
