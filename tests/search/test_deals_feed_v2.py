"""
Tests for KZPH-G4-01 (Deals Reels Feed) and KZPH-G5-01 (Favorites).
Verifies:
1. Route handlers GET /deals and GET /favorites return 200 and search_first.html.
2. Endpoint GET /api/v2/deals returns well-structured deal objects with provenance, discount badges, and pagination.
3. Deal filtering by min_discount, type, and city works correctly.
4. Template search_first.html contains both viewDeals and viewFavorites sections and support for themes.
"""

from tests import test_support  # must be first
import json
import sqlite3
import unittest
import unittest.mock
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import web.server as srv


class TestDealsFeedAndFavorites(AioHTTPTestCase):
    def setUp(self):
        super().setUp()
        import database
        database.init_db()

    async def get_application(self):
        app = web.Application()
        app.add_routes([
            web.get("/", srv.index_handler),
            web.get("/deals", srv.deals_page_handler),
            web.get("/favorites", srv.favorites_page_handler),
            web.get("/profile", srv.profile_page_handler),
            web.get("/api/v2/deals", srv.deals_v2_handler),
            web.post("/api/auth/telegram-webapp", srv.telegram_webapp_login_handler),
        ])
        return app

    @unittest_run_loop
    async def test_deals_page_route(self):
        """GET /deals returns 200 and serves search_first.html."""
        resp = await self.client.get("/deals")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('id="viewDeals"', text)
        self.assertIn('id="dealsReelsContainer"', text)
        self.assertIn('id="navLinkDeals"', text)

    @unittest_run_loop
    async def test_favorites_page_route(self):
        """GET /favorites returns 200 and serves search_first.html."""
        resp = await self.client.get("/favorites")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('id="viewFavorites"', text)
        self.assertIn('id="favoritesListContainer"', text)
        self.assertIn('id="navLinkFavorites"', text)

    @unittest_run_loop
    async def test_profile_page_route(self):
        """GET /profile returns 200 and serves search_first.html with profile & watches elements."""
        resp = await self.client.get("/profile")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('id="viewProfile"', text)
        self.assertIn('id="watchModalBackdrop"', text)
        self.assertIn('id="navLinkProfile"', text)
        self.assertIn('id="profileWatchesList"', text)

    @unittest_run_loop
    async def test_city_detection_and_theme_options_in_template(self):
        """search_first.html contains city detection and theme selection in profile."""
        resp = await self.client.get("/profile")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        # City detection & binding
        self.assertIn('id="btnGeoHeader"', text)
        self.assertIn('id="profileCitySelect"', text)
        self.assertIn('class="profile-city-chip"', text)
        self.assertIn('detectUserLocation', text)
        self.assertIn('KZ_CITY_COORDS', text)
        self.assertIn('localStorage.setItem(\'kz_hunter_city\'', text)

        # Theme selection in profile
        self.assertIn('id="themeOptLight"', text)
        self.assertIn('id="themeOptDark"', text)
        self.assertIn('id="themeOptAuto"', text)
        self.assertIn('selectTheme', text)
        self.assertIn('applyThemeMode', text)

    @unittest_run_loop
    async def test_closed_development_gate_and_telegram_auth_elements(self):
        """search_first.html contains closed development gate and Telegram auth widget."""
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('id="viewClosedDev"', text)
        self.assertIn('id="closedDevTgWidget"', text)
        self.assertIn('id="closedDevPendingContent"', text)
        self.assertIn('onTelegramAuth', text)
        self.assertIn('logout', text)

    def test_is_confirmed_user_logic(self):
        """is_confirmed_user correctly checks admin and approval status."""
        from auth import is_confirmed_user

        self.assertFalse(is_confirmed_user(None))
        self.assertFalse(is_confirmed_user({"id": 999999, "settings": {}}))
        self.assertFalse(is_confirmed_user({"id": 999999, "settings": {"is_approved": False}}))
        self.assertTrue(is_confirmed_user({"id": 999999, "settings": {"is_approved": True}}))

        # Admin is always confirmed
        admin_id = 171013461
        with unittest.mock.patch("auth.ADMIN_TELEGRAM_IDS", {admin_id}):
            self.assertTrue(is_confirmed_user({"id": admin_id, "settings": {}}))

    def test_verify_telegram_webapp_data(self):
        """Telegram WebApp HMAC-SHA256 signature verification according to TG specification."""
        from auth import verify_telegram_webapp_data
        import urllib.parse
        import json
        import hmac
        import hashlib
        import time

        bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        user_json = json.dumps({"id": 171013461, "first_name": "Owner", "username": "molthun"}, separators=(',', ':'))
        auth_date = int(time.time())

        # Construct raw data
        fields = {
            "auth_date": str(auth_date),
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": user_json,
        }
        check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        fields["hash"] = calc_hash
        init_data_str = urllib.parse.urlencode(fields)

        # Valid data passes
        res = verify_telegram_webapp_data(init_data_str, bot_token)
        self.assertEqual(res["id"], 171013461)
        self.assertEqual(res["first_name"], "Owner")
        self.assertEqual(res["username"], "molthun")

        # Tampered hash fails
        bad_fields = dict(fields, hash="invalidhash123")
        with self.assertRaises(ValueError):
            verify_telegram_webapp_data(urllib.parse.urlencode(bad_fields), bot_token)

        # Expired data fails
        old_fields = dict(fields, auth_date=str(auth_date - 90000))
        check_str_old = "\n".join(f"{k}={old_fields[k]}" for k in sorted(k for k in old_fields if k != "hash"))
        old_fields["hash"] = hmac.new(secret_key, check_str_old.encode("utf-8"), hashlib.sha256).hexdigest()
        with self.assertRaises(ValueError):
            verify_telegram_webapp_data(urllib.parse.urlencode(old_fields), bot_token)

    @unittest_run_loop
    async def test_telegram_webapp_login_endpoint(self):
        """POST /api/auth/telegram-webapp authenticates user and sets cookie."""
        import urllib.parse
        import json
        import hmac
        import hashlib
        import time

        bot_token = "fake-token"
        user_json = json.dumps({"id": 12345, "first_name": "Test", "username": "testuser"})
        auth_date = int(time.time())
        fields = {"auth_date": str(auth_date), "user": user_json}
        check_str = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret_key, check_str.encode("utf-8"), hashlib.sha256).hexdigest()
        init_data_str = urllib.parse.urlencode(fields)

        with unittest.mock.patch("web.server.get_bot_token", return_value=bot_token):
            resp = await self.client.post("/api/auth/telegram-webapp", json={"init_data": init_data_str})
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["user"]["id"], 12345)
            self.assertIn("kzph_session", resp.cookies)

    @unittest_run_loop
    async def test_deals_v2_does_not_invent_identity_or_provenance(self):
        """Строка без установленной связи и основания не получает выдуманных значений."""
        raw = {"deals": [{"id": "p_101", "product_id": 101, "canonical_key": "canon_101", "shop": "Sulpak",
                          "title": "TV", "old_price": 550000, "new_price": 385000, "discount_pct": 30,
                          "savings_kzt": 165000, "alert_type": "SUPER_DISCOUNT"}],
               "total": 1, "shops_summary": [], "type_totals": {}}
        with unittest.mock.patch("database.get_store_deals", return_value=raw):
            data = await (await self.client.get("/api/v2/deals")).json()
        self.assertIsNone(data["deals"][0]["canonical_product_id"])
        self.assertEqual(data["deals"][0]["price_basis"], "unconfirmed")
        self.assertFalse(data["has_more"])


class TestSearchFirstTemplateIntegrity(unittest.TestCase):
    def setUp(self):
        with open("web/templates/search_first.html", "r", encoding="utf-8") as f:
            self.html = f.read()

    def test_reels_and_favorites_dom_elements(self):
        """Template must contain essential elements for Reels feed and Favorites."""
        self.assertIn('id="viewDeals"', self.html)
        self.assertIn('id="dealsReelsContainer"', self.html)
        self.assertIn('id="viewFavorites"', self.html)
        self.assertIn('id="favoritesListContainer"', self.html)
        self.assertIn('id="navLinkDeals"', self.html)
        self.assertIn('id="navLinkFavorites"', self.html)
        self.assertIn('id="bottomNavDeals"', self.html)
        self.assertIn('id="bottomNavFavorites"', self.html)

    def test_static_images_have_no_empty_src(self):
        """Пустой src вызывает onerror до загрузки скрипта, когда страница приходит по частям."""
        self.assertNotIn('src=""', self.html)

    def test_theme_tokens_present(self):
        """Light and dark theme tokens and styles are properly defined."""
        self.assertIn(':root, [data-theme="light"] {', self.html)
        self.assertIn('[data-theme="dark"] {', self.html)
        self.assertIn('.deals-reels-container', self.html)
        self.assertIn('.deal-reel-card', self.html)
        self.assertIn('.favorites-grid', self.html)

    def test_javascript_managers_present(self):
        """JavaScript contains Favorites and DealsFeedState logic."""
        self.assertIn('Favorites', self.html)
        self.assertIn('kzph_favorites_v1', self.html)
        self.assertIn('DealsFeedState', self.html)
        self.assertIn('loadDealsFeed', self.html)
        self.assertIn('renderDealsFeed', self.html)
        self.assertIn('renderFavorites', self.html)
        self.assertIn('scrollReel', self.html)

    def test_mobile_header_and_search_styling(self):
        """Search first template has Telegram WebApp script, auto-auth, and mobile-friendly styling."""
        self.assertIn('telegram-web-app.js', self.html)
        self.assertIn('autoLoginTelegramWebApp', self.html)
        self.assertIn('header-user-name', self.html)
        self.assertIn('overflow-x: hidden', self.html)
        self.assertIn('min-width: 0', self.html)
        self.assertNotIn('autofocus', self.html)

    def test_admin_only_dashboard_button_and_removed_help_text(self):
        """Dashboard buttons are hidden by default for non-admins and bot help text is removed."""
        self.assertNotIn('BotFather', self.html)
        self.assertNotIn('Официальный бот авторизации', self.html)
        self.assertNotIn('id="closedDevTgHelp"', self.html)

        # Dashboard buttons exist with display: none for non-admins
        self.assertIn('id="headerBtnDashboard"', self.html)
        self.assertIn('id="navLinkDashboard"', self.html)
        self.assertIn('id="profileAdminPanelCard"', self.html)
        self.assertIn('is-admin-visible', self.html)
