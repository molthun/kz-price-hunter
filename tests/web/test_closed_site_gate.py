"""Closed-mode server gate, isolated data; no Telegram/provider calls."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import auth
from web import server


class ClosedSiteGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_and_pending_only_receive_login_document_on_every_page(self):
        calls=[]
        async def handler(r):calls.append(r.path);return web.Response(text='PRIVATE APPLICATION')
        paths=['/','/search','/product/example','/deals','/favorites','/profile','/dashboard','/admin','/monitoring','/privacy','/unknown']
        app=web.Application(middlewares=[auth.auth_middleware]);app.router.add_route('*','/{tail:.*}',handler)
        with patch.object(auth,'MAINTENANCE_DEV_MODE',True),patch.object(auth,'get_session_user',return_value={'id':777,'settings':{}}),patch.object(auth,'ADMIN_TELEGRAM_IDS',set()):
            async with TestClient(TestServer(app)) as client:
                for headers in ({},{'Cookie':'kzph_session=fake-pending'}):
                    for path in paths:
                        res=await client.get(path,headers=headers);body=await res.text()
                        self.assertEqual(res.status,200);self.assertIn('id="closedLogin"',body)
                        self.assertNotIn('PRIVATE APPLICATION',body);self.assertNotIn('<nav',body);self.assertNotIn('<footer',body)
                        self.assertEqual(res.headers['Cache-Control'],'no-store');self.assertIn('Cookie',res.headers['Vary'])
                self.assertEqual((await client.head('/search')).status,200)
                self.assertEqual(calls,[])

    async def test_exact_api_allowlist_and_no_prefix_bypass(self):
        async def handler(r):return web.json_response({'handler':True})
        app=web.Application(middlewares=[auth.auth_middleware]);app.router.add_route('*','/{tail:.*}',handler)
        with patch.object(auth,'MAINTENANCE_DEV_MODE',True):
            async with TestClient(TestServer(app)) as client:
                for path in ['/api/products','/api/v2/search','/api/categories','/api/changelog','/api/me/export','/api/me/watches','/api/me-anything','/api/diag-extra','/api/auth/unknown']:
                    res=await client.get(path);self.assertEqual(res.status,403,path)
                    self.assertTrue((await res.json())['maintenance'])
                for path in ['/api/me','/api/diag','/api/live','/api/health','/api/readiness']:
                    self.assertEqual((await client.get(path)).status,200,path)
                origin=str(client.make_url('/')).rstrip('/')
                for path in ['/api/auth/telegram','/api/auth/telegram-webapp','/api/auth/dev-login','/api/auth/logout']:
                    self.assertEqual((await client.post(path,headers={'Origin':origin})).status,200,path)
                self.assertEqual((await client.post('/api/auth/logout')).status,403)

    async def test_confirmed_and_admin_keep_application_public_mode_unchanged(self):
        async def handler(r):return web.Response(text='APPLICATION')
        app=web.Application(middlewares=[auth.auth_middleware]);app.router.add_get('/search',handler)
        with patch.object(auth,'MAINTENANCE_DEV_MODE',True),patch.object(auth,'ADMIN_TELEGRAM_IDS',{888}):
            async with TestClient(TestServer(app)) as client:
                for user in ({'id':777,'settings':{'is_confirmed':True}},{'id':888,'settings':{}}):
                    with patch.object(auth,'get_session_user',return_value=user):
                        res=await client.get('/search',headers={'Cookie':'kzph_session=fake'})
                        self.assertEqual(await res.text(),'APPLICATION');self.assertEqual(res.headers['Cache-Control'],'no-store')
                with patch.object(auth,'MAINTENANCE_DEV_MODE',False):
                    self.assertEqual(await (await client.get('/search')).text(),'APPLICATION')

    async def test_me_has_only_login_metadata_for_restricted_users(self):
        app=server.create_app();app.cleanup_ctx.clear()
        with patch.object(auth,'MAINTENANCE_DEV_MODE',True),patch.object(server,'MAINTENANCE_DEV_MODE',True),patch.object(server,'get_bot_username',return_value='fixture_bot'):
            async with TestClient(TestServer(app)) as client:
                data=await (await client.get('/api/me')).json()
                self.assertEqual(set(data),{'user','auth','maintenance_mode','access'})
                self.assertIsNone(data['user']);self.assertEqual(data['auth']['bot_username'],'fixture_bot')
