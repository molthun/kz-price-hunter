"""Stage 2 synthetic regression suite. No real DB, credentials, or provider calls."""
import asyncio
import hashlib
import hmac
import ipaddress
import os
import tempfile
import time
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _TMP.name
for key in ('TELEGRAM_BOT_TOKEN','DNS_BOT_TOKEN','GEMINI_API_KEY','OPENAI_API_KEY','PUBLIC_ORIGIN','APP_URL','TRUSTED_PROXIES','ADMIN_TELEGRAM_IDS'):
    os.environ[key] = ''
os.environ['ALLOW_DEV_LOGIN'] = '0'

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
import auth
import config
import telegram_bot as bot
import web.server as server
from database import init_db, get_user, upsert_telegram_user, create_session, set_user_blocked


def request(peer='127.0.0.1', headers=None, scheme='http'):
    return make_mocked_request('POST', '/', headers={'Host':'localhost:8080', **(headers or {})}).clone(remote=peer, scheme=scheme)


def signed(uid=123, age=0):
    data = {'id':uid,'first_name':'Test','auth_date':int(time.time())-age}
    check = '\n'.join(f'{key}={data[key]}' for key in sorted(data))
    data['hash'] = hmac.new(hashlib.sha256(b'fake-bot-token').digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


class AuthBoundaryTests(unittest.TestCase):
    def test_default_dev_login_is_disabled(self):
        env = dict(os.environ);env.pop('ALLOW_DEV_LOGIN',None)
        result = subprocess.run([sys.executable,'-B','-c','import config; assert config.ALLOW_DEV_LOGIN is False; assert config.DEV_ADMIN_ID < 0'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_loopback_dev_boundary_and_headers(self):
        with patch.object(auth,'ALLOW_DEV_LOGIN',True), patch.object(auth,'ADMIN_TELEGRAM_IDS',set()), patch.object(auth,'PUBLIC_ORIGIN',''):
            self.assertTrue(auth.dev_login_allowed(request()))
            self.assertTrue(auth.dev_login_allowed(request('::1',{'Host':'[::1]:8080'})))
            for req in (request('192.168.28.20'),request('198.51.100.1'),request(headers={'Host':'attacker.example'}),
                        request(headers={'X-Forwarded-For':'127.0.0.1'}),request(headers={'X-Forwarded-Proto':'https'}),request(headers={'Forwarded':'for=127.0.0.1'})):
                self.assertFalse(auth.dev_login_allowed(req))
            with patch.object(auth,'ADMIN_TELEGRAM_IDS',{55}):
                self.assertFalse(auth.dev_login_allowed(request()))
            with patch.object(auth,'PUBLIC_ORIGIN','https://shop.molthun.ru'):
                self.assertFalse(auth.dev_login_allowed(request()))

    def test_untrusted_proxy_headers_do_not_change_ip_or_secure(self):
        req=request('198.51.100.1',{'X-Forwarded-For':'127.0.0.1','X-Forwarded-Proto':'https','X-Forwarded-Host':'shop.molthun.ru'})
        self.assertEqual(auth.client_ip(req),'198.51.100.1')
        self.assertFalse(auth.is_secure_request(req))

    def test_trusted_proxy_chain_and_malformed_headers(self):
        networks=tuple(ipaddress.ip_network(n) for n in ('192.168.28.10/32','10.0.0.2/32'))
        with patch.object(auth,'_TRUSTED_PROXY_NETWORKS',networks):
            req=request('192.168.28.10',{'X-Forwarded-For':'1.1.1.1, 198.51.100.7, 10.0.0.2','X-Forwarded-Proto':'https'})
            self.assertEqual(auth.client_ip(req),'198.51.100.7')
            self.assertTrue(auth.is_secure_request(req))
            self.assertEqual(auth.client_ip(request('192.168.28.10',{'X-Forwarded-For':'garbage, 198.51.100.7'})),'192.168.28.10')
            self.assertFalse(auth.is_secure_request(request('192.168.28.10',{'X-Forwarded-Proto':'https,http'})))

    def test_full_origin_and_cookie_security(self):
        self.assertEqual(auth.normalized_origin('https://SHOP.molthun.ru'),auth.normalized_origin('https://shop.molthun.ru:443'))
        for bad in ('https://shop.molthun.ru:0','null','https://user:pass@shop.molthun.ru','https://shop.molthun.ru/extra','https://shop.molthun.ru?x=1','https://shop.molthun.ru\\@evil.test'):
            self.assertIsNone(auth.normalized_origin(bad))
        with patch.object(auth,'PUBLIC_ORIGIN','https://shop.molthun.ru'):
            res=web.Response();auth.set_session_cookie(res,request('192.168.28.10'),'fake-session')
            self.assertTrue(res.cookies[auth.SESSION_COOKIE]['secure'])
            self.assertTrue(res.cookies[auth.SESSION_COOKIE]['httponly'])
            self.assertEqual(res.cookies[auth.SESSION_COOKIE]['samesite'],'Lax')

    def test_login_freshness_future_and_positive_id(self):
        for age in (0,299,-20):
            self.assertEqual(auth.verify_telegram_auth(signed(age=age),'fake-bot-token')['id'],123)
        for payload in (signed(age=301),signed(age=-60),signed(uid=-1),signed(uid=0),dict(signed(),id=999)):
            with self.assertRaises(ValueError):auth.verify_telegram_auth(payload,'fake-bot-token')


class AuthHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        init_db()

    async def test_csrf_origin_matrix_before_any_mutation(self):
        calls=[]
        async def handler(req):
            calls.append(1);return web.json_response({'ok':True})
        app=web.Application(middlewares=[auth.auth_middleware]);app.router.add_post('/api/action',handler)
        with patch.object(auth,'PUBLIC_ORIGIN','https://shop.molthun.ru'):
            async with TestClient(TestServer(app)) as client:
                for headers in ({},{'Origin':'null'},{'Origin':'http://shop.molthun.ru'},{'Origin':'https://shop.molthun.ru:8443'},
                                {'Origin':'https://evil.test','X-Forwarded-Host':'evil.test'},
                                {'Origin':'https://shop.molthun.ru.evil.test'},
                                {'Origin':'https://shop.molthun.ru','Sec-Fetch-Site':'cross-site'}):
                    self.assertEqual((await client.post('/api/action',headers=headers)).status,403)
                self.assertEqual(calls,[])
                res=await client.post('/api/action',headers={'Origin':'https://shop.molthun.ru'})
                self.assertEqual(res.status,200);self.assertEqual(res.headers['Cache-Control'],'no-store')
                self.assertEqual(len(calls),1)

    async def test_local_dev_login_does_not_overwrite_real_admin(self):
        upsert_telegram_user({'id':55,'first_name':'Real admin','username':'real'})
        app=server.create_app();app.cleanup_ctx.clear()
        with patch.object(auth,'ALLOW_DEV_LOGIN',True),patch.object(server,'ALLOW_DEV_LOGIN',True),patch.object(auth,'PUBLIC_ORIGIN',''),patch.object(auth,'ADMIN_TELEGRAM_IDS',set()),patch.object(server,'ADMIN_TELEGRAM_IDS',set()):
            async with TestClient(TestServer(app)) as client:
                origin=str(client.make_url('/')).rstrip('/')
                res=await client.post('/api/auth/dev-login',headers={'Origin':origin})
                self.assertEqual(res.status,200)
                self.assertEqual((await res.json())['user']['id'],config.DEV_ADMIN_ID)
                self.assertFalse(get_user(config.DEV_ADMIN_ID)['settings']['telegram_notify_enabled'])
                self.assertEqual(get_user(55)['username'],'real')
                with patch.object(auth,'ADMIN_TELEGRAM_IDS',{55}):
                    self.assertEqual((await client.post('/api/auth/dev-login',headers={'Origin':origin})).status,403)
                    with patch.object(server,'get_bot_username',return_value=None):
                        self.assertIsNone((await (await client.get('/api/me')).json())['user'])
                self.assertEqual(get_user(55)['first_name'],'Real admin')

    async def test_dev_session_cannot_be_used_over_lan(self):
        req=request('192.168.28.20');req._cookies={auth.SESSION_COOKIE:'fake'}
        async def handler(r):return web.json_response({'user':r['user']})
        with patch.object(auth,'get_session_user',return_value={'id':config.DEV_ADMIN_ID}),patch.object(auth,'ALLOW_DEV_LOGIN',True):
            # GET avoids conflating the dev-session boundary with CSRF rejection.
            res=await auth.auth_middleware(req.clone(method='GET'),handler)
            self.assertIn('"user": null',res.text)

    async def test_test_telegram_rate_limit_is_per_user(self):
        app=server.create_app();app.cleanup_ctx.clear()
        for uid in (91001,91002):upsert_telegram_user({'id':uid,'first_name':'Test'})
        with patch.object(server,'telegram_test_limiter',auth.RateLimiter(1,60)),patch.object(server,'telegram_api',return_value=Mock(status_code=200)) as send:
            async with TestClient(TestServer(app)) as client:
                headers={'Origin':str(client.make_url('/')).rstrip('/')}
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE:create_session(91001)})
                self.assertEqual((await client.post('/api/me/test-telegram',headers=headers)).status,200)
                denied=await client.post('/api/me/test-telegram',headers=headers)
                self.assertEqual(denied.status,429);self.assertIn('Retry-After',denied.headers)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE:create_session(91002)})
                self.assertEqual((await client.post('/api/me/test-telegram',headers=headers)).status,200)
                self.assertEqual(send.call_count,2)

    async def test_telegram_login_session_and_spoofed_ip_limit(self):
        app=server.create_app();app.cleanup_ctx.clear()
        with patch.object(server,'get_bot_token',return_value='fake-bot-token'),patch.object(server,'auth_limiter',auth.RateLimiter(10,60)),patch.object(server,'get_bot_username',return_value=None):
            async with TestClient(TestServer(app)) as client:
                headers={'Origin':str(client.make_url('/')).rstrip('/')}
                for payload in (signed(age=301),signed(age=-60),dict(signed(),hash='bad')):
                    self.assertEqual((await client.post('/api/auth/telegram',json=payload,headers=headers)).status,401)
                response=await client.post('/api/auth/telegram',json=signed(uid=92001),headers=headers)
                self.assertEqual(response.status,200)
                self.assertEqual((await (await client.get('/api/me')).json())['user']['id'],92001)
                set_user_blocked(92001,True)
                self.assertEqual((await client.post('/api/auth/telegram',json=signed(uid=92001),headers=headers)).status,403)
                with patch.object(server,'auth_limiter',auth.RateLimiter(1,60)):
                    for fake_ip,status in [('198.51.100.1',200),('198.51.100.2',429)]:
                        response=await client.post('/api/auth/telegram',json=signed(uid=92002),headers={**headers,'X-Forwarded-For':fake_ip})
                        self.assertEqual(response.status,status)

    async def test_live_get_requires_browser_header(self):
        app=server.create_app();app.cleanup_ctx.clear();upsert_telegram_user({'id':91003,'first_name':'Test'})
        with patch('search_engine.search_live_stores',new_callable=AsyncMock,return_value=[]) as live,patch.object(server,'live_search_limiter',auth.RateLimiter(1,600)):
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE:create_session(91003)})
                path='/api/best-price?q=empty&live=1&ai=0'
                self.assertEqual((await client.get(path)).status,403);live.assert_not_awaited()
                self.assertEqual((await client.get(path,headers={'X-KZPH-Request':'1'})).status,200);live.assert_awaited_once()


if __name__=='__main__':unittest.main()
