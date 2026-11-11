"""6.36.0: открытый сайт с доступом по времени и утреннее напоминание о подтверждениях.

Гость — 10 минут, вошедший без подтверждения — 30, подтверждённый и администратор — без ограничений.
Без Telegram и внешних вызовов: сессии и пользователи подменяются, очередь — во временной БД.
"""
from tests import test_support  # noqa: F401
import datetime
import json
import time
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import auth
import config


def _app():
    async def page(r):
        return web.Response(text='APPLICATION')

    async def api(r):
        return web.json_response({'handler': True, 'access': r.get('access')})

    app = web.Application(middlewares=[auth.auth_middleware])
    app.router.add_get('/api/{tail:.*}', api)
    app.router.add_get('/{tail:.*}', page)
    return app


class TimedAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = [patch.object(auth, 'MAINTENANCE_DEV_MODE', False), patch.object(auth, 'ACCESS_MODE', 'timed'),
                        patch.object(auth, 'ACCESS_GUEST_MINUTES', 10), patch.object(auth, 'ACCESS_TRIAL_MINUTES', 30),
                        patch.object(auth, 'ADMIN_TELEGRAM_IDS', {9001}), patch.object(auth, 'get_bot_token', return_value='test-token')]
        for p in self.patches:
            p.start()
        auth._guest_key_cache.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    async def test_new_guest_gets_signed_cookie_and_ten_minutes(self):
        async with TestClient(TestServer(_app())) as client:
            res = await client.get('/search')
            self.assertEqual(await res.text(), 'APPLICATION')
            cookie = res.cookies.get(auth.GUEST_COOKIE)
            self.assertIsNotNone(cookie)
            self.assertTrue(cookie['httponly'])
            data = await (await client.get('/api/anything')).json()
            self.assertEqual(data['access']['level'], 'guest')
            self.assertTrue(590 <= data['access']['remaining_seconds'] <= 600)
            self.assertEqual(res.headers['Cache-Control'], 'no-store')

    async def test_expired_guest_gets_login_page_and_api_refusal(self):
        old = auth.sign_guest_start(int(time.time()) - 11 * 60)
        async with TestClient(TestServer(_app())) as client:
            client.session.cookie_jar.update_cookies({auth.GUEST_COOKIE: old})
            body = await (await client.get('/deals')).text()
            self.assertIn('id="closedLogin"', body)
            self.assertNotIn('APPLICATION', body)
            res = await client.get('/api/v2/search')
            self.assertEqual(res.status, 403)
            data = await res.json()
            self.assertTrue(data['access_expired'])
            self.assertIn('10 минут', data['message'])
            # Вход и «кто я» доступны всегда — иначе гость не смог бы войти
            self.assertEqual((await client.get('/api/me')).status, 200)

    async def test_forged_or_future_guest_cookie_starts_a_fresh_window_not_unlimited(self):
        now = int(time.time())
        self.assertIsNone(auth.parse_guest_start(f'{now}.deadbeef', now))
        self.assertIsNone(auth.parse_guest_start(auth.sign_guest_start(now + 3600), now))
        self.assertEqual(auth.parse_guest_start(auth.sign_guest_start(now - 5), now), now - 5)
        old = auth.sign_guest_start(now - 11 * 60)
        forged = str(now) + old[old.index('.'):]  # подпись от старого времени к новому не подходит
        self.assertIsNone(auth.parse_guest_start(forged, now))

    async def test_pending_user_trial_starts_on_first_visit_and_ends_after_thirty_minutes(self):
        user = {'id': 555, 'settings': {}}
        saved = []
        with patch.object(auth, 'get_session_user', return_value=user), \
             patch.object(auth, '_start_user_trial', side_effect=lambda u, now: saved.append(now)):
            async with TestClient(TestServer(_app())) as client:
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: 'pending'})
                data = await (await client.get('/api/x')).json()
                self.assertEqual(len(saved), 1)
                self.assertEqual(data['access']['level'], 'trial')
                self.assertTrue(1790 <= data['access']['remaining_seconds'] <= 1800)
        expired = {'id': 556, 'settings': {'trial_started_at': time.time() - 31 * 60}}
        with patch.object(auth, 'get_session_user', return_value=expired):
            async with TestClient(TestServer(_app())) as client:
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: 'pending'})
                res = await client.get('/api/x')
                self.assertEqual(res.status, 403)
                self.assertIn('30 минут', (await res.json())['message'])
                self.assertIn('id="closedLogin"', await (await client.get('/')).text())

    async def test_approved_and_admin_have_no_limit(self):
        long_ago = time.time() - 30 * 24 * 3600
        for user in ({'id': 777, 'settings': {'is_approved': True, 'trial_started_at': long_ago}},
                     {'id': 9001, 'settings': {'trial_started_at': long_ago}}):
            with patch.object(auth, 'get_session_user', return_value=user):
                async with TestClient(TestServer(_app())) as client:
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: 's'})
                    res = await client.get('/api/x')
                    self.assertEqual(res.status, 200)
                    self.assertEqual(await (await client.get('/')).text(), 'APPLICATION')
                    self.assertIsNone(res.cookies.get(auth.GUEST_COOKIE))

    async def test_open_mode_has_no_limits_and_closed_mode_still_closes(self):
        old = auth.sign_guest_start(int(time.time()) - 3600)
        with patch.object(auth, 'ACCESS_MODE', 'open'):
            async with TestClient(TestServer(_app())) as client:
                client.session.cookie_jar.update_cookies({auth.GUEST_COOKIE: old})
                self.assertEqual((await client.get('/api/x')).status, 200)
        with patch.object(auth, 'MAINTENANCE_DEV_MODE', True):
            async with TestClient(TestServer(_app())) as client:
                self.assertEqual((await client.get('/api/x')).status, 403)


class SelfApprovalTests(unittest.TestCase):
    def test_user_cannot_approve_self_or_reset_trial_through_settings(self):
        clean = config.validate_user_settings({'is_approved': True, 'trial_started_at': 0, 'telegram_notify_enabled': True}, {})
        self.assertNotIn('is_approved', clean)
        self.assertNotIn('trial_started_at', clean)
        self.assertTrue(clean['telegram_notify_enabled'])

    def test_access_mode_default_is_timed_outside_tests(self):
        import subprocess, sys, os
        env = {k: v for k, v in os.environ.items() if k not in ('ACCESS_MODE', 'MAINTENANCE_DEV_MODE')}
        env['MAINTENANCE_DEV_MODE'] = '1'  # прежний переключатель больше не закрывает сайт
        out = subprocess.run([sys.executable, '-c', 'import config;print(config.ACCESS_MODE, config.MAINTENANCE_DEV_MODE)'],
                             capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        self.assertEqual(out.stdout.strip().splitlines()[-1], 'timed False')


class PendingApprovalNotifyTests(unittest.TestCase):
    def setUp(self):
        import database
        database.init_db()
        self.database = database
        self.ids = [880001, 880002, 880003, 880004, 9001]
        with database.get_connection() as conn:
            for uid in self.ids:
                conn.execute('DELETE FROM users WHERE id=?', (uid,))
            conn.execute('DELETE FROM notification_outbox WHERE alert_id <= ?', (-2_000_000_000,))
            recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
            stale = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)).strftime('%Y-%m-%d %H:%M:%S')
            rows = [(880001, 'Аня', 'anya', '{}', 0, recent),
                    (880002, 'Подтверждён', 'ok', json.dumps({'is_approved': True}), 0, recent),
                    (880003, 'Заблокирован', 'bad', '{}', 1, recent),
                    (880004, 'Давний', 'old', '{}', 0, stale),
                    (9001, 'Владелец', 'owner', '{}', 0, recent)]
            for uid, name, uname, settings, blocked, seen in rows:
                conn.execute('INSERT INTO users (id, first_name, username, settings, is_blocked, created_at, last_login_at) '
                             'VALUES (?,?,?,?,?,?,?)', (uid, name, uname, settings, blocked, seen, seen))
            conn.commit()
        own = set(self.ids)
        all_users = database.list_users
        # Другие тесты оставляют в общей БД своих пользователей — считаем только своих
        self.patches = [patch.object(database, 'list_users', side_effect=lambda: [u for u in all_users() if u['id'] in own]),
                        patch.object(config, 'ADMIN_TELEGRAM_IDS', {9001}), patch.object(config, 'ACCESS_MODE', 'timed'),
                        patch.object(config, 'ACCESS_PENDING_NOTIFY_HOUR', 9),
                        patch.object(config, 'ACCESS_PENDING_NOTIFY_TZ', 'Asia/Almaty')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        with self.database.get_connection() as conn:
            for uid in self.ids:
                conn.execute('DELETE FROM users WHERE id=?', (uid,))
            conn.execute('DELETE FROM notification_outbox WHERE alert_id <= ?', (-2_000_000_000,))
            conn.commit()

    def _at(self, hour_almaty):
        return datetime.datetime(2026, 11, 13, hour_almaty - 5, 30, tzinfo=datetime.timezone.utc)

    def test_only_recent_unapproved_unblocked_non_admins_are_pending(self):
        import access_notify
        ids = [p['id'] for p in access_notify.pending_users()]
        self.assertIn(880001, ids)
        for uid in (880002, 880003, 880004, 9001):
            self.assertNotIn(uid, ids)

    def test_queued_once_in_the_morning_only(self):
        import access_notify
        self.assertEqual(access_notify.queue_if_due(self._at(8)), 0, 'до 9:00 не пишем')
        self.assertEqual(access_notify.queue_if_due(self._at(13)), 0, 'после полудня не пишем')
        self.assertEqual(access_notify.queue_if_due(self._at(9)), 1)
        self.assertEqual(access_notify.queue_if_due(self._at(10)), 0, 'второе сообщение за день не ставится')
        with self.database.get_connection() as conn:
            row = conn.execute('SELECT user_id, payload FROM notification_outbox WHERE alert_id=?',
                               (access_notify.pending_alert_id('2026-11-13'),)).fetchone()
        self.assertEqual(row[0], 9001)
        payload = json.loads(row[1])
        self.assertEqual(payload['kind'], 'pending_approvals')
        text = '\n'.join(payload['lines'])
        self.assertIn('Аня @anya', text)
        self.assertNotIn('Подтверждён', text)

    def test_nothing_when_nobody_waits(self):
        import access_notify
        with self.database.get_connection() as conn:
            conn.execute('DELETE FROM users WHERE id=?', (880001,))
            conn.commit()
        self.assertEqual(access_notify.queue_if_due(self._at(9)), 0)

    def test_delivery_goes_to_admin_with_admin_button(self):
        import notifier
        sent = []
        with patch.object(notifier, 'telegram_api', side_effect=lambda m, p, timeout=10: sent.append(p) or type('R', (), {'status_code': 200, 'json': lambda self: {'ok': True, 'result': {'message_id': 1}}, 'headers': {}})()), \
             patch.object(notifier, 'APP_URL', 'https://example.invalid'):
            result = notifier.send_pending_approvals(9001, {'lines': ['👥 <b>Ждут подтверждения доступа: 1</b>']})
        self.assertTrue(result)
        self.assertEqual(sent[0]['chat_id'], 9001)
        self.assertIn('/admin', json.dumps(sent[0]['reply_markup']))


if __name__ == '__main__':
    unittest.main()
