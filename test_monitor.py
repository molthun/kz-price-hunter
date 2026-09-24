import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import unittest
import json
import os
import tempfile

# Explicit test identities; production defaults must stay fail-closed.
os.environ["ALLOW_DEV_LOGIN"] = "0"
os.environ["ADMIN_TELEGRAM_IDS"] = "1"
os.environ["PUBLIC_ORIGIN"] = ""
os.environ["TRUSTED_PROXIES"] = ""

# Тесты работают с временной базой и настройками, не трогая рабочую prices.db / settings.json
_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP_DIR.name

import config

from database import (
    init_db,
    save_or_update_product,
    save_or_update_products_batch,
    get_price_history_batch,
    was_alert_sent_recently,
    record_alert,
    get_connection,
    upsert_telegram_user,
    save_user_settings,
    set_user_blocked,
    create_session,
    get_session_user,
    delete_session,
    get_notification_recipients
)
import hmac
import hashlib
import time
from auth import verify_telegram_auth
from detector import check_anomaly, is_junk_accessory, alert_matches_user, notify_level_allows

# Фиксированные пороги детекции, независимые от пользовательских настроек
TEST_SETTINGS = config.merge_user_settings({})

class TestDNSMonitor(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM products WHERE id LIKE 'test-%' OR id LIKE 'db-test-%'")
            conn.execute("DELETE FROM alerts WHERE product_id LIKE 'test-%' OR product_id LIKE 'db-test-%'")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM users")
            conn.commit()

    def test_junk_accessory_filter(self):
        self.assertTrue(is_junk_accessory("Чехол для Apple iPhone 15 Pro Max"))
        self.assertTrue(is_junk_accessory("Защитная пленка на экран"))
        self.assertTrue(is_junk_accessory("Кабель Type-C USB"))
        self.assertFalse(is_junk_accessory("15.6\" Ноутбук ASUS TUF Gaming"))
        self.assertFalse(is_junk_accessory("Смартфон Xiaomi 14T 512GB"))

    def test_zero_glitch_detection(self):
        """
        Тест кейса пользователя:
        Товар стоил 189 990 ₸, а стал стоить 18 990 ₸ (пропущен ноль при переоценке).
        """
        product = {
            "id": "test-12345",
            "title": "Телевизор Samsung 55\" 4K Smart TV",
            "price": 18990,
            "url": "https://www.dns-shop.kz/product/test-12345/",
            "city": "Астана"
        }
        history = {
            "old_price": 189990,
            "first_seen_price": 189990
        }
        anomaly = check_anomaly(product, history, custom_settings=TEST_SETTINGS)
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "ZERO_GLITCH")
        self.assertEqual(anomaly["new_price"], 18990)
        self.assertEqual(anomaly["old_price"], 189990)
        self.assertGreater(anomaly["savings"], 170000)

    def test_super_discount_detection(self):
        """Тест глубокой скидки (обвал цены на 70%)."""
        product = {
            "id": "test-67890",
            "title": "Ноутбук Lenovo IdeaPad 15",
            "price": 60000,
            "url": "https://www.dns-shop.kz/product/test-67890/",
            "city": "Астана"
        }
        history = {
            "old_price": 200000,
            "first_seen_price": 200000
        }
        anomaly = check_anomaly(product, history, custom_settings=TEST_SETTINGS)
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "SUPER_DISCOUNT")
        self.assertEqual(anomaly["drop_pct"], 70.0)

    def test_database_and_alert_dedup(self):
        """Тест сохранения в базу данных и защиты от повторных алертов."""
        p_data = {
            "id": "db-test-1",
            "title": "Видеокарта GeForce RTX 4070",
            "category": "Видеокарты",
            "url": "https://www.dns-shop.kz/product/db-test-1/",
            "image_url": "https://c.dns-shop.kz/test.jpg",
            "price": 350000
        }
        res1 = save_or_update_product(p_data)
        self.assertTrue(res1["is_new"])

        # Проверяем запись алерта
        self.assertFalse(was_alert_sent_recently("db-test-1", 35000))
        record_alert("db-test-1", "ZERO_GLITCH", 350000, 35000, 90.0, 315000)
        self.assertTrue(was_alert_sent_recently("db-test-1", 35000))

        # Повторное сохранение: товар уже есть, прежняя цена возвращается как история
        res2 = save_or_update_product(dict(p_data, price=35000))
        self.assertFalse(res2["is_new"])
        self.assertEqual(res2["old_price"], 350000)

    def test_batch_history_read_before_overwrite(self):
        """История цен для больших категорий читается до пакетной перезаписи."""
        item = {
            "id": "db-test-batch",
            "title": "Смартфон Apple iPhone 16 Pro",
            "url": "https://shop.kz/offer/db-test-batch/",
            "price": 599990
        }
        save_or_update_products_batch([item])
        history = get_price_history_batch(["db-test-batch", "db-test-missing"])
        save_or_update_products_batch([dict(item, price=59999)])

        self.assertNotIn("db-test-missing", history)
        self.assertEqual(history["db-test-batch"]["old_price"], 599990)
        anomaly = check_anomaly(dict(item, price=59999), history["db-test-batch"], custom_settings=TEST_SETTINGS)
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "ZERO_GLITCH")

    def test_settings_validation(self):
        """Личные настройки: ноль допустим, неизвестные и системные ключи отбрасываются, некорректные значения отклоняются."""
        clean = config.validate_user_settings({
            "min_item_price_kzt": "0",
            "price_glitch_drop_pct": "70.5",
            "unknown_key": 1,
            "scan_interval_minutes": 5,
            "alert_shops": {"dns": False, "unknown_shop": True}
        })
        self.assertEqual(clean["min_item_price_kzt"], 0)
        self.assertEqual(clean["price_glitch_drop_pct"], 70.5)
        self.assertNotIn("unknown_key", clean)
        self.assertNotIn("scan_interval_minutes", clean)
        self.assertEqual(clean["alert_shops"], {"dns": False})

        with self.assertRaises(ValueError):
            config.validate_user_settings({"min_item_price_kzt": -1})
        with self.assertRaises(ValueError):
            config.validate_user_settings({"detect_zero_glitch": "yes"})
        with self.assertRaises(ValueError):
            config.validate_user_settings({"telegram_notify_level": "EVERYTHING"})

        # Системные настройки не принимают личные ключи
        self.assertEqual(config._validate_settings({"min_item_price_kzt": 1, "candidate_drop_pct": 40}), {"candidate_drop_pct": 40.0})

    def test_scan_interval_range(self):
        """Интервал автообновления: минуты/часы/дни в пределах 5 минут — 30 дней."""
        self.assertEqual(config._validate_settings({"scan_interval_minutes": 3 * 1440})["scan_interval_minutes"], 4320)
        with self.assertRaises(ValueError):
            config._validate_settings({"scan_interval_minutes": 4})
        with self.assertRaises(ValueError):
            config._validate_settings({"scan_interval_minutes": 31 * 1440})
        with self.assertRaises(ValueError):
            config._validate_settings({"scan_interval_minutes": None})
        self.assertEqual(config.get_scan_interval_seconds({"scan_interval_minutes": 1}), 300)
        self.assertEqual(config.get_scan_interval_seconds({"scan_interval_minutes": 120}), 7200)

    def test_telegram_auth_signature(self):
        """Подпись Telegram Login Widget: верная принимается, подделка и устаревшие данные отклоняются."""
        token = "123456:TEST-TOKEN"
        data = {"id": 42, "first_name": "Иван", "username": "ivan", "auth_date": int(time.time())}
        check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret = hashlib.sha256(token.encode()).digest()
        signed = dict(data, hash=hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest())

        self.assertEqual(int(verify_telegram_auth(signed, token)["id"]), 42)
        with self.assertRaises(ValueError):
            verify_telegram_auth(dict(signed, id=43), token)
        with self.assertRaises(ValueError):
            verify_telegram_auth(signed, "999:OTHER")

        old = dict(data, auth_date=int(time.time()) - 2 * 86400)
        old_check = "\n".join(f"{k}={old[k]}" for k in sorted(old))
        old["hash"] = hmac.new(secret, old_check.encode(), hashlib.sha256).hexdigest()
        with self.assertRaises(ValueError):
            verify_telegram_auth(old, token)

    def test_dev_login_admin_only_locally(self):
        """Локально без ADMIN_TELEGRAM_IDS «Разработчик» — администратор; в контейнере (прод) — нет."""
        from unittest.mock import patch
        import auth
        dev = {"id": config.DEV_ADMIN_ID}
        with patch.object(auth, "ALLOW_DEV_LOGIN", True), patch.object(auth, "ADMIN_TELEGRAM_IDS", set()):
            self.assertTrue(auth.is_admin(dev))
            self.assertFalse(auth.is_admin({"id": 42}))
        with patch.object(auth, "ALLOW_DEV_LOGIN", False), patch.object(auth, "ADMIN_TELEGRAM_IDS", set()):
            self.assertFalse(auth.is_admin(dev))
        with patch.object(auth, "ALLOW_DEV_LOGIN", True), patch.object(auth, "ADMIN_TELEGRAM_IDS", {555}):
            self.assertFalse(auth.is_admin(dev))   # заданы настоящие администраторы — разработчик не админ
            self.assertTrue(auth.is_admin({"id": 555}))

    def test_user_sessions_and_blocking(self):
        """Сессия находит пользователя; блокировка и выход завершают ее."""
        user = upsert_telegram_user({"id": 777001, "username": "tester", "first_name": "Тест"})
        self.assertFalse(user["is_blocked"])
        self.assertEqual(user["settings"]["min_item_price_kzt"], config.USER_DEFAULTS["min_item_price_kzt"])

        token = create_session(777001)
        self.assertEqual(get_session_user(token)["id"], 777001)
        self.assertIsNone(get_session_user("wrong-token"))

        save_user_settings(777001, {"telegram_notify_enabled": True})
        self.assertIn(777001, [u["id"] for u in get_notification_recipients()])

        set_user_blocked(777001, True)
        self.assertIsNone(get_session_user(token))
        self.assertNotIn(777001, [u["id"] for u in get_notification_recipients()])

        set_user_blocked(777001, False)
        token2 = create_session(777001)
        delete_session(token2)
        self.assertIsNone(get_session_user(token2))

    def test_alert_filtering_by_user_thresholds(self):
        """Кандидат, записанный по мягким порогам, виден только пользователям, чьи личные пороги он проходит."""
        alert = {
            "alert_type": "SUPER_DISCOUNT", "new_price": 50000, "discount_pct": 50.0, "savings_kzt": 50000,
            "shop": "Sulpak", "title": "Ноутбук ASUS", "category": "Ноутбуки", "url": "https://sulpak.kz/x"
        }
        strict = config.merge_user_settings({"price_glitch_drop_pct": 65})
        relaxed = config.merge_user_settings({"price_glitch_drop_pct": 40})
        self.assertFalse(alert_matches_user(alert, strict))
        self.assertTrue(alert_matches_user(alert, relaxed))

        no_sulpak = config.merge_user_settings({"price_glitch_drop_pct": 40, "alert_shops": {"sulpak": False}})
        self.assertFalse(alert_matches_user(alert, no_sulpak))

        used = dict(alert, title="Ноутбук ASUS (уцененный товар)")
        self.assertFalse(alert_matches_user(used, relaxed))

        glitch = {"type": "ZERO_GLITCH", "drop_pct": 90, "savings": 170000}
        big = {"type": "SUPER_DISCOUNT", "drop_pct": 50, "savings": 150000}
        self.assertTrue(notify_level_allows(glitch, "CRITICAL_ONLY"))
        self.assertFalse(notify_level_allows(big, "CRITICAL_ONLY"))
        self.assertTrue(notify_level_allows(big, "HIGH_SAVINGS"))

    def test_candidate_settings_are_relaxed(self):
        """Кандидаты записываются по мягким системным порогам, а не по личным."""
        product = {"id": "test-cand", "title": "Смартфон Samsung Galaxy", "price": 60000, "url": "https://x", "city": "Астана"}
        history = {"old_price": 100000, "first_seen_price": 100000}  # скидка 40% — ниже личного порога 65%
        self.assertIsNone(check_anomaly(product, history, custom_settings=TEST_SETTINGS))
        anomaly = check_anomaly(product, history, custom_settings=config.get_candidate_settings())
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "SUPER_DISCOUNT")


class TestReliability(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_db()
        import auth
        import config
        auth.ADMIN_TELEGRAM_IDS = {1}
        config.ADMIN_TELEGRAM_IDS = {1}
        from database import invalidate_alerts_cache
        with get_connection() as conn:
            for table in ('notification_outbox', 'product_sources', 'alerts', 'products', 'shop_scans', 'users', 'sessions', 'title_ai_attempts', 'title_canonical_cache', 'scheduler_lease'):
                conn.execute(f'DELETE FROM {table}')
        invalidate_alerts_cache()
        # Флаг потери аренды — состояние модуля; тесты вызывают _scan_shop напрямую, без _do_scan_task
        import web.server as server
        server._lease_state.update(lost=False, last_ok=time.monotonic())

    def product(self, pid='audit', **kwargs):
        return dict(id=pid, title='Apple iPhone 16 256 ГБ', price=150000,
                    shop='Shop A', city='Астана', url='https://example.invalid/p', **kwargs)

    async def test_failed_and_partial_scan_do_not_advance_success(self):
        import asyncio
        from unittest.mock import patch
        from scrapers.base import PagedScraper, ScanResult
        from web import server
        from database import get_shop_scans, get_stale_shops
        product = self.product()
        class Broken(PagedScraper):
            PAGE_DELAY_SECONDS = 0
            def _fetch_page(self, name, url, page):
                raise RuntimeError('simulated failure')
        registry = {'audit': (Broken, [{'name': 'Audit', 'url': 'https://example.invalid'}], 'Shop A')}
        with patch.dict(server.SHOP_REGISTRY, registry):
            await server._scan_shop('audit', {}, asyncio.Semaphore(1))
        row = get_shop_scans()['audit']
        self.assertEqual(row['status'], 'failed')
        self.assertIsNone(row['last_success_at'])
        self.assertNotIn('audit', get_stale_shops(['audit'], 10))
        with get_connection() as conn:
            conn.execute('UPDATE shop_scans SET next_retry_at=0')
        self.assertIn('audit', get_stale_shops(['audit'], 10))
        class Partial(Broken):
            def _fetch_page(self, name, url, page):
                if page == 1: return [product]
                raise RuntimeError('second page failure')
        with patch.dict(server.SHOP_REGISTRY, {'audit': (Partial, registry['audit'][1], 'Shop A')}):
            await server._scan_shop('audit', config.get_candidate_settings(), asyncio.Semaphore(1))
        row = get_shop_scans()['audit']
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['last_items'], 1)
        self.assertIsNone(row['last_success_at'])

    async def test_arbitrage_is_checked_beyond_first_hundred_offers(self):
        from web.server import _save_and_detect
        competitor=dict(self.product('competitor'),shop='Shop B',price=200000)
        save_or_update_product(competitor)
        products=[dict(self.product(f'bulk-{i}'),title=f'Unknown Model {i}',price=12000) for i in range(100)]
        products.append(self.product('target'))
        await _save_and_detect(products,'Shop A',config.get_candidate_settings())
        with get_connection() as conn:
            rows=conn.execute("SELECT product_id FROM alerts WHERE alert_type='MARKET_ARBITRAGE'").fetchall()
        self.assertEqual([r[0] for r in rows],['target'])

    def test_fourmobile_missing_old_price_does_not_crash_detector(self):
        p=dict(self.product(),old_price_on_site=None)
        history={'old_price':300000,'first_seen_price':300000}
        anomaly=check_anomaly(p,history,custom_settings=config.get_candidate_settings())
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly['type'],'SUPER_DISCOUNT')
        self.assertEqual(anomaly['new_price'],150000)

    async def test_fourmobile_all_groups_scanned_once(self):
        import asyncio
        from unittest.mock import patch, Mock
        from web import server
        from database import get_shop_scans, get_products_count
        payload={'price':[{'cat':'iPhone','items':[['iPhone 16 256GB','150 000 ₸']]},
                          {'cat':'Apple Watch','items':[['Watch Series 10','200 000 ₸']]}],
                 'wiwu':[{'cat':'WiWU','items':[['Wireless Keyboard','30 000 ₸']]}]}
        response=Mock(status_code=200)
        response.json.return_value=payload
        with patch('scrapers.fourmobile.requests.get',return_value=response) as request:
            await server._scan_shop('fourmobile',config.get_candidate_settings(),asyncio.Semaphore(1))
            self.assertEqual(request.call_count,1)
        self.assertEqual(get_products_count(),3)
        row=get_shop_scans()['fourmobile']
        self.assertEqual(row['status'],'complete')
        self.assertIsNone(row['last_error'])

    def test_pagination_limit_and_confirmed_end(self):
        from scrapers.base import PagedScraper, ScanResult
        p = self.product()
        class Pages(PagedScraper):
            PAGE_DELAY_SECONDS = 0
            def _fetch_page(self, name, url, page):
                return [p] if page == 1 else ScanResult(complete=True)
        self.assertTrue(Pages()._scrape_sync('x','x', 3).complete)
        self.assertTrue(Pages()._scrape_sync('x','x', 1).limited)
        class UnknownEnd(Pages):
            def _fetch_page(self, name, url, page):
                return [p] if page == 1 else []
        self.assertFalse(UnknownEnd()._scrape_sync('x','x',3).complete)
        self.assertIsNotNone(UnknownEnd()._scrape_sync('x','x',3).error)

    def test_html_404_after_data_is_limited_not_complete(self):
        from unittest.mock import patch, Mock
        from scrapers.forcecom import ForcecomScraper
        from scrapers.base import ScanResult
        scraper=ForcecomScraper()
        scraper.PAGE_DELAY_SECONDS=0
        original=scraper._fetch_page
        def fetch(name,url,page):
            if page==1: return [self.product()]
            return original(name,url,page)
        with patch.object(scraper,'_fetch_page',side_effect=fetch), patch('scrapers.forcecom.requests.get',return_value=Mock(status_code=404)):
            result=scraper._scrape_sync('x','https://example.invalid',3)
        self.assertTrue(result.limited)
        self.assertFalse(result.complete)
        self.assertIsNone(result.error)

    def test_http_errors_are_not_catalog_end(self):
        from unittest.mock import patch, Mock
        from scrapers.kaspi import KaspiScraper
        with patch('scrapers.kaspi.requests.get', return_value=Mock(status_code=503)):
            result = KaspiScraper()._scrape_sync('x','https://kaspi.kz/shop/c/phones/',2)
        self.assertTrue(result.error)
        self.assertFalse(result.complete)

    def test_retirement_only_after_full_source_and_reactivation(self):
        from database import reconcile_source, get_products_count
        a,b = self.product('a'), self.product('b')
        save_or_update_products_batch([a,b])
        reconcile_source('shop','category',[a,b],True)
        reconcile_source('shop','category',[a],False)
        self.assertEqual(get_products_count(),2)
        reconcile_source('shop','category',[],True)
        self.assertEqual(get_products_count(),2)  # whole-catalog collapse is suspicious
        reconcile_source('shop','category',[a],True)
        self.assertEqual(get_products_count(),1)
        save_or_update_products_batch([b])
        reconcile_source('shop','category',[b],False)
        self.assertEqual(get_products_count(),2)

    def test_other_source_keeps_product_active(self):
        from database import reconcile_source, get_products_count
        a,b=self.product('a'),self.product('b')
        save_or_update_products_batch([a,b])
        reconcile_source('shop','first',[a,b],True)
        reconcile_source('shop','second',[b],True)
        reconcile_source('shop','first',[a],True)
        self.assertEqual(get_products_count(),2)

    def test_stale_prices_excluded_everywhere(self):
        from database import get_products_count, find_market_comparisons, get_alerts
        from search_engine import search_in_database
        p=self.product()
        save_or_update_product(p)
        record_alert(p['id'],'SUPER_DISCOUNT',300000,150000,50,150000)
        with get_connection() as conn:
            conn.execute("UPDATE products SET updated_at='2000-01-01'")
        self.assertEqual(get_products_count(),0)
        self.assertEqual(search_in_database('iPhone'),[])
        self.assertEqual(get_alerts(),[])
        self.assertIsNone(find_market_comparisons(p['title'],'Other',100000,'Астана'))

    def test_variant_capacity_city_and_matching(self):
        from database import find_market_comparisons
        from model_matching import same_model
        self.assertTrue(same_model('Смартфон Apple iPhone 16 Pro 256GB Black', 'Apple iPhone 16 Pro 256 ГБ Черный'))
        for other in ('Apple iPhone 16 Pro Max 256 ГБ', 'Apple iPhone 16 128 ГБ', 'Apple iPhone 15 256 ГБ'):
            self.assertFalse(same_model('Apple iPhone 16 256 ГБ', other))
        self.assertTrue(same_model('Samsung Galaxy S24 Ultra 1TB', 'Samsung Galaxy S24 Ultra 1024 ГБ'))
        p=self.product()
        save_or_update_product(dict(p,city='Алматы'))
        self.assertIsNone(find_market_comparisons(p['title'],'Other',100000,'Астана'))
        save_or_update_product(p)
        self.assertIsNotNone(find_market_comparisons(p['title'],'Other',100000,'Астана'))
        save_or_update_product(dict(p,title='Apple iPhone 16 Pro Max 256 ГБ'))
        self.assertIsNone(find_market_comparisons(p['title'],'Other',100000,'Астана'))

    def enqueue(self):
        from notifier import prepare_deliveries
        p=self.product()
        save_or_update_product(p)
        upsert_telegram_user({'id':42,'first_name':'Audit'})
        save_user_settings(42,{'telegram_notify_enabled':True,'price_glitch_drop_pct':30})
        anomaly={'type':'SUPER_DISCOUNT','old_price':300000,'new_price':150000,'drop_pct':50,
                 'savings':150000,'emoji':'Sale','reason':'Test'}
        return record_alert(p['id'],'SUPER_DISCOUNT',300000,150000,50,150000,
                            deliveries=prepare_deliveries(p,anomaly))

    def test_notification_retry_survives_reinitialization(self):
        from unittest.mock import patch
        from notifier import deliver_pending
        from database import notification_stats
        self.enqueue()
        with patch('notifier.get_bot_token',return_value='test'), patch('notifier.send_telegram_alert',return_value=False) as send:
            self.assertEqual(deliver_pending(),0)
            self.assertEqual(send.call_count,1)
            deliver_pending()
            self.assertEqual(send.call_count,1)
        init_db()
        with get_connection() as conn:
            conn.execute('UPDATE notification_outbox SET next_attempt_at=0')
        with patch('notifier.get_bot_token',return_value='test'), patch('notifier.send_telegram_alert',return_value=True) as send:
            self.assertEqual(deliver_pending(),1)
            deliver_pending()
            self.assertEqual(send.call_count,1)
        self.assertEqual(notification_stats(),{'sent':1})

    def test_blocked_user_notification_cancelled(self):
        from unittest.mock import patch
        from notifier import deliver_pending
        from database import notification_stats
        self.enqueue()
        set_user_blocked(42,True)
        with patch('notifier.get_bot_token',return_value='test'), patch('notifier.send_telegram_alert') as send:
            deliver_pending()
            send.assert_not_called()
        self.assertEqual(notification_stats(),{'cancelled':1})

    def test_duplicate_window_expires(self):
        self.enqueue()
        self.assertTrue(was_alert_sent_recently('audit',150000))
        with get_connection() as conn:
            conn.execute("UPDATE alerts SET created_at=datetime('now','-2 days')")
        self.assertFalse(was_alert_sent_recently('audit',150000))

    def test_flip_prices_availability_pagination(self):
        from scrapers.flip import FlipScraper
        html = '<div class="new-product"><a class="product" href="/catalog?prod=123"><img class="image" src="//s.f.kz/test.jpg"><div class="product-data" data-available="1"><div class="title">Phone</div><div class="price"><span>150 000 ₸</span><span class="old">200 000 ₸</span></div></div></a></div>'
        result=FlipScraper.parse_page(html,'Electronics','https://www.flip.kz/catalog?subsection=5319',1)
        self.assertTrue(result.complete)
        self.assertEqual(result[0]['price'],150000)
        self.assertEqual(result[0]['old_price_on_site'],200000)
        self.assertEqual(result[0]['image_url'],'https://s.f.kz/test.jpg')
        paged=FlipScraper.parse_page(html+'<a href="/catalog?subsection=5319&page=2">2</a>','Electronics','https://www.flip.kz/catalog?subsection=5319',1)
        self.assertFalse(paged.complete)
        absent=FlipScraper.parse_page(html.replace('data-available="1"','data-available="0"'),'x','x',1)
        self.assertEqual(len(absent),0)
        self.assertFalse(absent.complete)
        with self.assertRaises(ValueError):
            FlipScraper.parse_page('<html>Challenge</html>','x','x',1)

    def test_tgrad_parse_page(self):
        from scrapers.tgrad import TgradScraper
        ga = lambda pid, name, price: ('[{&quot;id&quot;:&quot;%s&quot;,&quot;name&quot;:&quot;%s&quot;,&quot;price&quot;:&quot;%s&quot;,&quot;brand&quot;:&quot;X&quot;}]' % (pid, name, price))
        card = lambda pid, name, price, old, href: (
            '<div class="product__block"><div class="product__img"><img src="/upload/%s.jpg"></div>'
            '<a href="%s" class="product__name">%s</a><div class="product__block_inf-bottom">'
            '<a href="%s" class="product__block_price">%s<span class="new__price">%s ₸</span></a>'
            '<button class="btn btn__cart catalog-item-button" data-ga="%s"></button></div></div>'
            % (pid, href, name, href, ('<span class="old__price">%s ₸</span>' % old) if old else '', price, ga(pid, name, price)))
        menu = ('<div class="menu-right__product"><div class="product__block">'
                '<button class="btn catalog-item-button" data-ga="%s"></button></div></div>' % ga("999", "Чужой товар", "1000"))
        html = menu + card("43011", "Смартфон Blackview A85", "74890", "89 990", "/smartfon-blackview-a85/") \
            + card("46705", "Стиральная машина Samsung", "239990", None, "/stiralnaya-mashina-samsung/") \
            + '<a href="/smartfony/page-2/">2</a>'
        res = TgradScraper.parse_page(html, "Tgrad: Смартфоны", 1)
        self.assertEqual([p["id"] for p in res], ["tgrad_43011", "tgrad_46705"])  # блок меню пропущен
        first = res[0]
        self.assertEqual(first["price"], 74890)
        self.assertEqual(first["old_price_on_site"], 89990)
        self.assertEqual(first["url"], "https://tgrad.kz/smartfon-blackview-a85/")
        self.assertEqual(first["image_url"], "https://tgrad.kz/upload/43011.jpg")
        self.assertEqual(first["city"], "Алматы")
        self.assertEqual(res[1]["old_price_on_site"], 0)
        self.assertFalse(res.complete)  # есть ссылка на следующую страницу
        last = TgradScraper.parse_page(html.replace('/smartfony/page-2/', '/smartfony/'), "Tgrad: Смартфоны", 1)
        self.assertTrue(last.complete)

    def test_schema_listing_ants_itmag(self):
        from scrapers.ants import AntsScraper
        from scrapers.itmag import ItmagScraper
        def block(name, url, price, avail="InStock"):
            return ('<div itemscope itemtype="http://schema.org/Product">'
                    f'<meta itemprop="name" content="{name}"><a itemprop="url" href="{url}"></a>'
                    f'<img itemprop="image" src="/upload/{price}.webp">'
                    f'<div itemprop="offers"><meta itemprop="price" content="{price}">'
                    f'<link itemprop="availability" href="http://schema.org/{avail}"></div></div>')
        html = (block("Видеокарта A", "https://itmag.kz/p/108707-gpu/", 86554)
                + block("Видеокарта B", "https://itmag.kz/p/129111-rx570/", 92087, "OutOfStock")
                + '<a href="/catalog/videokarty/?PAGEN_1=2">2</a>')
        res = ItmagScraper.parse_page(html, "ITMag: Видеокарты", 1)
        self.assertEqual([p["id"] for p in res], ["itmag_108707"])   # нет в наличии — пропущен
        self.assertEqual(res[0]["price"], 86554)
        self.assertEqual(res[0]["image_url"], "https://itmag.kz/upload/86554.webp")
        self.assertEqual(res[0]["city"], "Алматы")
        self.assertFalse(res.complete)                                 # есть следующая страница
        # ANTS сортирует «сначала в наличии»: первый отсутствующий товар завершает обход
        ants_html = (block("Смартфон A", "https://ants.kz/catalog/smartfony/tovar-113105/", 64631)
                     + block("Смартфон B", "https://ants.kz/catalog/smartfony/tovar-113106/", 70000, "OutOfStock")
                     + '<a href="/catalog/smartfony/?PAGEN_1=6">6</a>')
        ants = AntsScraper.parse_page(ants_html, "ANTS: Смартфоны", 5)
        self.assertEqual([p["id"] for p in ants], ["ants_113105"])
        self.assertTrue(ants.complete)

    def test_schema_listing_unlinked_items_and_last_page(self):
        from scrapers.ants import AntsScraper
        from scrapers.itmag import ItmagScraper
        # Товар без своей страницы (ссылка на категорию) сохраняется со стабильным ID и ссылкой на страницу выдачи
        unlinked = ('<div itemscope itemtype="http://schema.org/Product"><meta itemprop="name" content="Ноутбук Asus ROG">'
                    '<a itemprop="url" href="https://ants.kz/catalog/noutbuki/"></a><div itemprop="offers">'
                    '<meta itemprop="price" content="2834660"><link itemprop="availability" href="http://schema.org/InStock"></div></div>'
                    '<a href="/catalog/noutbuki/?PAGEN_1=12">12</a>')
        page_url = "https://ants.kz/catalog/noutbuki/?PAGEN_1=11"
        res = AntsScraper.parse_page(unlinked, "ANTS: Ноутбуки", 11, page_url=page_url)
        again = AntsScraper.parse_page(unlinked, "ANTS: Ноутбуки", 11, page_url=page_url)
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["id"].startswith("ants_u"))
        self.assertEqual(res[0]["id"], again[0]["id"])
        self.assertEqual(res[0]["url"], page_url)
        self.assertFalse(res.complete)
        # Последняя страница ITMag: служебный блок без цены и без ссылки дальше — конец выдачи подтвержден
        last = ItmagScraper.parse_page('<div itemscope itemtype="http://schema.org/Product"><meta itemprop="name" content="x"></div>',
                                       "ITMag: Планшеты", 3)
        self.assertEqual(len(last), 0)
        self.assertTrue(last.complete)
        # Страница-заглушка без карточек (например, антибот) концом не считается
        self.assertFalse(ItmagScraper.parse_page("<html>Проверка браузера</html>", "ITMag: Планшеты", 3).complete)

    def test_ispace_listing_and_product(self):
        from scrapers.ispace import ISpaceScraper
        listing = ('<div class="entity-card"><a class="entity-card_name" href="/product/iphone-15-128-gb-cernyi-mtp03hx-a">iPhone 15</a></div>'
                   '<div class="entity-card"><a class="entity-card_name" href="/product/iphone-15-128-gb-cernyi-mtp03hx-a?x=1">iPhone 15</a></div>')
        self.assertEqual(ISpaceScraper.parse_listing(listing), ["https://ispace.kz/product/iphone-15-128-gb-cernyi-mtp03hx-a"])
        ld = {"@context": "https://schema.org", "@type": "Product", "name": "iPhone 15, 128 ГБ, Чёрный", "sku": "MTP03HX/A",
              "image": ["https://cdn/img.webp"],
              "offers": {"@type": "Offer", "price": "430990", "availability": "https://schema.org/InStock"}}
        page = '<script type="application/ld+json">%s</script>' % json.dumps(ld, ensure_ascii=False)
        p = ISpaceScraper.parse_product(page, "https://ispace.kz/product/x", "iSpace: iPhone")
        self.assertEqual(p["id"], "ispace_MTP03HXA")
        self.assertEqual(p["title"], "iPhone 15, 128 ГБ, Чёрный (MTP03HX/A)")
        self.assertEqual(p["price"], 430990)
        self.assertEqual(p["image_url"], "https://cdn/img.webp")
        ld["offers"]["availability"] = "https://schema.org/OutOfStock"
        page_oos = '<script type="application/ld+json">%s</script>' % json.dumps(ld, ensure_ascii=False)
        self.assertIsNone(ISpaceScraper.parse_product(page_oos, "https://ispace.kz/product/x", "iSpace: iPhone"))

    def test_halyk_parse_response(self):
        from scrapers.halyk import HalykScraper
        raw_data = {
            "products_total": 45,
            "products": [
                {
                    "id": "12345",
                    "name": "Смартфон Apple iPhone 15 128Gb Black",
                    "price": 380000,
                    "oldprice": 420000,
                    "url": "/smartfony/smartfon-apple-iphone-15/128gb_black?sku=128gb_black",
                    "picture": "https://cdn.halykmarket.kz/img1.jpg",
                },
                {
                    "id": "67890",
                    "name": "Ноутбук Acer Nitro 15",
                    "price": 450000,
                    "oldprice": 0,
                    "url": "https://halykmarket.kz/category/noutbuki/acer-15",
                    "picture": "",
                },
                {
                    "id": "99999",
                    "name": "Невалидный товар без цены",
                    "price": 0,
                    "oldprice": 100000,
                }
            ]
        }
        res = HalykScraper.parse_response(raw_data, "Смартфоны", page=1)
        self.assertEqual(len(res), 2)
        # First product with valid discount
        p1 = res[0]
        self.assertEqual(p1["id"], "halyk_12345")
        self.assertEqual(p1["shop"], "Halyk Market")
        self.assertEqual(p1["title"], "Смартфон Apple iPhone 15 128Gb Black")
        self.assertEqual(p1["price"], 380000)
        self.assertEqual(p1["old_price_on_site"], 420000)
        self.assertEqual(p1["city"], "Алматы")
        self.assertEqual(p1["url"], "https://halykmarket.kz/category/smartfony/smartfon-apple-iphone-15/128gb_black?sku=128gb_black")
        self.assertEqual(p1["image_url"], "https://cdn.halykmarket.kz/img1.jpg")
        
        # Second product without discount
        p2 = res[1]
        self.assertEqual(p2["old_price_on_site"], 0)
        self.assertEqual(p2["url"], "https://halykmarket.kz/category/noutbuki/acer-15")

        # products_total=45 при трёх товарах на странице 1 — конец не подтверждён
        self.assertFalse(res.complete)
        self.assertTrue(HalykScraper.parse_response(dict(raw_data, products_total=3), "Смартфоны", page=1).complete)

        # Incomplete page: 24 items with total 100 on page 1
        full_page = {"products_total": 100, "products": [{"id": str(i), "name": f"P{i}", "price": 1000} for i in range(24)]}
        res_full = HalykScraper.parse_response(full_page, "Смартфоны", page=1)
        self.assertFalse(res_full.complete)

        # Last page: page 5 * 24 >= 100
        res_last = HalykScraper.parse_response(full_page, "Смартфоны", page=5)
        self.assertTrue(res_last.complete)

    def test_outbox_and_alert_are_atomic(self):
        with self.assertRaises(TypeError):
            record_alert('audit','SUPER_DISCOUNT',300000,150000,50,150000,
                         deliveries=[(42,{'invalid': object()})])
        with get_connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM alerts').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM notification_outbox').fetchone()[0],0)

    def test_retry_lease_and_price_change(self):
        from database import claim_notification, notification_stats
        from notifier import deliver_pending
        from unittest.mock import patch
        self.enqueue()
        self.assertIsNotNone(claim_notification())
        self.assertIsNone(claim_notification())
        with get_connection() as conn:
            conn.execute('UPDATE notification_outbox SET next_attempt_at=0')
        save_or_update_product(dict(self.product(),price=160000))
        with patch('notifier.get_bot_token',return_value='test'), patch('notifier.send_telegram_alert') as send:
            deliver_pending()
            send.assert_not_called()
        self.assertEqual(notification_stats(),{'cancelled':1})

    async def test_admin_api_permissions_and_shop_validation(self):
        from unittest.mock import patch
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app
        upsert_telegram_user({'id':42,'first_name':'Audit'})
        token=create_session(42)
        app=create_app()
        app.cleanup_ctx.clear()  # no external requests or scans during API tests
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get('/api/admin/shops')).status,401)
            client.session.cookie_jar.update_cookies({'kzph_session':token})
            self.assertEqual((await client.get('/api/admin/shops')).status,403)
            with patch('auth.ADMIN_TELEGRAM_IDS',{42}):
                response=await client.get('/api/admin/shops')
                self.assertEqual(response.status,200)
                self.assertEqual(len((await response.json())['shops']),len(config.SHOP_KEYS))
                for shops in ([],['unknown'],'kaspi',[{}]):
                    response=await client.post('/api/scan/start',json={'shops':shops})
                    self.assertEqual(response.status,400)

    def test_smart_search_engine(self):
        """Тест умного поиска цен: синонимы, опечатки, категории, цифры и фильтры."""
        from search_engine import (
            search_in_database,
            is_accessory_query,
            stem_russian_word,
            expand_token_fts
        )

        # 1. Проверка вспомогательных функций
        self.assertTrue(is_accessory_query("чехол для iphone 15"))
        self.assertTrue(is_accessory_query("защитное стекло samsung"))
        self.assertFalse(is_accessory_query("iphone 15 pro max"))
        self.assertFalse(is_accessory_query("ноутбук asus"))

        self.assertEqual(stem_russian_word("видеокарты"), "видеокарт")
        self.assertEqual(stem_russian_word("смартфона"), "смартфон")

        # 2. Проверка токенизации цифр (не обрезаются и не добавляют wildcard для точных цифр)
        tokens = expand_token_fts("5")
        self.assertIn('"5"', tokens)

        # 3. Наполнение базы тестовыми товарами
        save_or_update_products_batch([
            {
                "id": "test-se-1",
                "shop": "Kaspi Магазин",
                "city": "Астана",
                "title": "Смартфон Apple iPhone 15 128Gb Black",
                "category": "Смартфоны",
                "price": 380000,
                "url": "https://kaspi.kz/1",
                "image_url": "https://img.kz/1"
            },
            {
                "id": "test-se-2",
                "shop": "Белый Ветер",
                "city": "Алматы",
                "title": "Видеокарта Palit GeForce RTX 4060 Dual 8GB",
                "category": "Видеокарты",
                "price": 165000,
                "url": "https://shop.kz/2",
                "image_url": "https://img.kz/2"
            },
            {
                "id": "test-se-3",
                "shop": "DNS Казахстан",
                "city": "Астана / Казахстан",
                "title": "Игровая консоль Sony PlayStation 5 Slim 1TB",
                "category": "Игровые приставки",
                "price": 270000,
                "url": "https://dns-shop.kz/3",
                "image_url": "https://img.kz/3"
            },
            {
                "id": "test-se-4",
                "shop": "Forcecom",
                "city": "Все",
                "title": "Чехол силиконовый для Apple iPhone 15 прозрачный",
                "category": "Чехлы для телефонов",
                "price": 2500,
                "url": "https://forcecom.kz/4",
                "image_url": "https://img.kz/4"
            }
        ])

        # 4. Поиск по кириллическому синониму: "айфон 15" должен найти iPhone 15
        res_iphone = search_in_database("айфон 15", exclude_accessories=True)
        self.assertGreaterEqual(len(res_iphone), 1)
        self.assertEqual(res_iphone[0]["id"], "test-se-1")

        # 5. Поиск с категорией в запросе: "видеокарта 4060" находит RTX 4060
        res_gpu = search_in_database("видеокарта 4060")
        self.assertGreaterEqual(len(res_gpu), 1)
        self.assertEqual(res_gpu[0]["id"], "test-se-2")

        # 6. Поиск консоли: "ps5 slim" находит PlayStation 5 Slim
        res_ps5 = search_in_database("ps5 slim")
        self.assertGreaterEqual(len(res_ps5), 1)
        self.assertEqual(res_ps5[0]["id"], "test-se-3")

        # 7. Запрос на аксессуар: "чехол iphone 15" НЕ должен быть заблокирован фильтром аксессуаров
        res_case = search_in_database("чехол iphone 15", exclude_accessories=True)
        self.assertGreaterEqual(len(res_case), 1)
        self.assertEqual(res_case[0]["id"], "test-se-4")

        # 8. Раскладка клавиатуры: "шзрщту" (iphone) находит iPhone 15
        res_typo = search_in_database("шзрщту 15")
        self.assertGreaterEqual(len(res_typo), 1)
        self.assertEqual(res_typo[0]["id"], "test-se-1")

        # 9. Фильтр по категории: category="Видеокарты"
        res_cat = search_in_database("4060", category="Видеокарты")
        self.assertEqual(len(res_cat), 1)
        self.assertEqual(res_cat[0]["id"], "test-se-2")

    def test_ai_service_heuristics(self):
        from ai_service import should_use_ai_parsing, clean_ai_json_response

        # 1. Простые точные запросы НЕ требуют AI
        self.assertFalse(should_use_ai_parsing("RTX 4060"))
        self.assertFalse(should_use_ai_parsing("iPhone 16"))
        self.assertFalse(should_use_ai_parsing("PS5 Slim"))
        self.assertFalse(should_use_ai_parsing("MacBook Air"))

        # 2. Естественные фразы с ценовыми рамками или намерениями требуют AI
        self.assertTrue(should_use_ai_parsing("ноутбук для учебы до 300к"))
        self.assertTrue(should_use_ai_parsing("посоветуй смартфон до 200000"))
        self.assertTrue(should_use_ai_parsing("видеокарта дешевле 150 000"))
        self.assertTrue(should_use_ai_parsing("айфон со скидкой"))
        self.assertTrue(should_use_ai_parsing("игровой комп в пределах 500 тыс"))

        # 3. Очистка JSON ответа с markdown блоками
        raw_md = "```json\n{\"clean_query\": \"ноутбук asus\", \"max_price\": 300000}\n```"
        cleaned = clean_ai_json_response(raw_md)
        self.assertIn('"clean_query"', cleaned)
        self.assertNotIn("```", cleaned)

    async def test_ai_service_parsing_mock(self):
        import ai_service
        from unittest.mock import patch

        mock_payload = {
            "clean_query": "ноутбук ASUS",
            "category": "Ноутбуки",
            "brand": "ASUS",
            "min_price": None,
            "max_price": 300000,
            "only_discount": False,
            "keywords": ["ноутбук", "asus"],
            "negative_keywords": ["чехол", "сумка"],
            "sort": "price_asc",
            "explanation": "Поиск ноутбуков ASUS до 300 000 ₸"
        }

        mock_creds = {
            "gemini_api_key": "test_ai_key_mock",
            "openai_api_key": "",
            "ai_search_enabled": True,
            "has_ai": True
        }

        from unittest.mock import AsyncMock
        # Mock direct REST call in call_gemini_api and credentials
        with patch.object(ai_service, '_get_api_credentials', return_value=mock_creds), \
             patch.object(ai_service, 'call_gemini_api', new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = mock_payload
            parsed = await ai_service.parse_natural_query("подбери ноутбук asus до 300к без чехлов")
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["clean_query"], "ноутбук ASUS")
            self.assertEqual(parsed["category"], "Ноутбуки")
            self.assertEqual(parsed["brand"], "ASUS")
            self.assertEqual(parsed["max_price"], 300000)
            self.assertEqual(parsed["negative_keywords"], ["чехол", "сумка"])

    async def test_ai_best_price_endpoint_integration(self):
        from unittest.mock import patch
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app
        import ai_service

        mock_ai_meta = {
            "clean_query": "iPhone 15",
            "category": "Смартфоны",
            "brand": "Apple",
            "min_price": None,
            "max_price": 400000,
            "only_discount": True,
            "keywords": ["iphone", "15"],
            "negative_keywords": [],
            "sort": "price_asc",
            "explanation": "Поиск iPhone 15 со скидкой до 400 000 ₸"
        }

        app = create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            with patch.object(ai_service, 'parse_natural_query', return_value=mock_ai_meta) as parser:
                # Гость: AI-разбор не вызывается (расходует квоту владельца)
                guest = await client.get('/api/best-price?q=айфон+15+со+скидкой+до+400к&ai=1')
                self.assertEqual(guest.status, 200)
                guest_meta = (await guest.json()).get("ai_meta")
                parser.assert_not_called()
                # Гостю — разбор по правилам, без обращения к AI
                self.assertEqual(guest_meta["source"], "rules")
                self.assertEqual(guest_meta["clean_query"], "iphone 15")
                self.assertEqual(guest_meta["max_price"], 400000)
                self.assertTrue(guest_meta["only_discount"])

                upsert_telegram_user({'id': 4242, 'first_name': 'Buyer'})
                client.session.cookie_jar.update_cookies({'kzph_session': create_session(4242)})
                res = await client.get('/api/best-price?q=айфон+15+со+скидкой+до+400к&ai=1')
                self.assertEqual(res.status, 200)
                data = await res.json()
                self.assertIn("ai_meta", data)
                self.assertEqual(data["ai_meta"]["clean_query"], "iPhone 15")
                self.assertEqual(data["ai_meta"]["max_price"], 400000)

            # Проверка статуса AI
            status_res = await client.get('/api/ai/status')
            self.assertEqual(status_res.status, 200)
            status_data = await status_res.json()
            self.assertEqual(status_data["status"], "ok")
            self.assertIn("configured", status_data)

    async def test_ai_consultant_rag_mock(self):
        """Тест RAG-пайплайна AI-консультанта."""
        import ai_service
        from unittest.mock import patch, AsyncMock

        # Тест 1: Режим без ключа API возвращает вежливое предупреждение
        with patch.object(ai_service, '_get_api_credentials', return_value={"has_ai": False}):
            res = await ai_service.ask_ai_consultant(message="Посоветуй ноутбук")
            self.assertIn("AI-сервис не настроен", res["answer"])
            self.assertEqual(res["products"], [])

        # Тест 2: Режим с настроенным AI и извлечением контекста из базы
        mock_creds = {
            "gemini_api_key": "test_ai_key",
            "openai_api_key": "",
            "ai_search_enabled": True,
            "has_ai": True,
            "provider": "gemini"
        }
        mock_ai_response = {
            "answer": "Для ваших задач отлично подойдет **iPhone 15** в магазине Kaspi!",
            "suggested_questions": ["Какая гарантия?", "Есть ли чехлы в наличии?"]
        }

        with patch.object(ai_service, '_get_api_credentials', return_value=mock_creds), \
             patch.object(ai_service, 'call_gemini_api', autospec=True) as mock_gemini, \
             patch('search_engine.search_in_database', return_value=[{'id':'rag-test','title':'iPhone 15','shop':'Kaspi','current_price':300000}]):
            mock_gemini.return_value = mock_ai_response
            res = await ai_service.ask_ai_consultant(message="Посоветуй айфон 15", city="Все")
            self.assertIn("iPhone 15", res["answer"])
            self.assertEqual(len(res["suggested_questions"]), 2)
            self.assertTrue(isinstance(res["products"], list))

    async def test_ai_consultant_endpoint(self):
        """Тест HTTP эндпоинта /api/ai/consultant."""
        from unittest.mock import patch, AsyncMock
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app
        import ai_service

        app = create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            upsert_telegram_user({'id': 765, 'first_name': 'Test'})
            client.session.cookie_jar.update_cookies({'kzph_session': create_session(765)})
            mock_result = {
                "answer": "Рекомендую монитор LG UltraGear.",
                "products": [{"shop": "Kaspi", "title": "LG 27GP850", "price": 180000}],
                "suggested_questions": ["Есть ли в Алматы?"]
            }
            with patch.object(ai_service, 'ask_ai_consultant', new_callable=AsyncMock, return_value=mock_result):
                # Успешный запрос
                res = await client.post('/api/ai/consultant', json={"message": "Какой игровой монитор выбрать?"})
                self.assertEqual(res.status, 200)
                data = await res.json()
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["answer"], "Рекомендую монитор LG UltraGear.")
                self.assertEqual(len(data["products"]), 1)

            # Ошибка при пустом сообщении
            res_bad = await client.post('/api/ai/consultant', json={"message": ""})
            self.assertEqual(res_bad.status, 400)

    async def test_telegram_bot_process_update(self):
        """Тест интерактивного Telegram-бота: Markdown конвертация, команды и сообщения."""
        from unittest.mock import patch, AsyncMock
        import aiohttp
        import telegram_bot

        # 1. Проверка конвертации Markdown в Telegram HTML
        md_text = "### Лучший выбор\n**iPhone 15** и *AirPods* с `код`"
        html_text = telegram_bot._markdown_to_telegram_html(md_text)
        self.assertIn("<b>Лучший выбор</b>", html_text)
        self.assertIn("<b>iPhone 15</b>", html_text)
        self.assertIn("<i>AirPods</i>", html_text)
        self.assertIn("<code>код</code>", html_text)

        # 2. Обработка команд бота
        async with aiohttp.ClientSession() as session:
            with patch.object(telegram_bot, 'send_tg_message', new_callable=AsyncMock) as mock_send:
                # Команда /start
                upd_start = {
                    "update_id": 101,
                    "message": {
                        "chat": {"id": 12345, "type": "private"},
                        "from": {"id": 12345, "first_name": "Тестер"},
                        "text": "/start"
                    }
                }
                await telegram_bot.process_telegram_update(session, "dummy_token", upd_start)
                mock_send.assert_called_once()
                self.assertIn("KZ Price Hunter", mock_send.call_args[0][3])
                mock_send.reset_mock()

                # Команда /status
                upd_status = {
                    "update_id": 102,
                    "message": {
                        "chat": {"id": 12345, "type": "private"},
                        "from": {"id": 12345, "first_name": "Тестер"},
                        "text": "/status"
                    }
                }
                await telegram_bot.process_telegram_update(session, "dummy_token", upd_status)
                mock_send.assert_called_once()
                self.assertIn("Статус системы", mock_send.call_args[0][3])
                mock_send.reset_mock()

                # Команда /search
                upd_search = {
                    "update_id": 103,
                    "message": {
                        "chat": {"id": 12345, "type": "private"},
                        "from": {"id": 12345, "first_name": "Тестер"},
                        "text": "/search iPhone 15"
                    }
                }
                await telegram_bot.process_telegram_update(session, "dummy_token", upd_search)
                mock_send.assert_called_once()
                self.assertIn("iPhone 15", mock_send.call_args[0][3])
                mock_send.reset_mock()

                # Обычное текстовое сообщение (AI-консультант)
                upd_ai = {
                    "update_id": 104,
                    "message": {
                        "chat": {"id": 12345, "type": "private"},
                        "from": {"id": 12345, "first_name": "Тестер"},
                        "text": "Какой планшет купить ребенку?"
                    }
                }
                mock_ai_ans = {"answer": "Советую iPad 9", "products": []}
                with patch('ai_service.ask_ai_consultant', new_callable=AsyncMock, return_value=mock_ai_ans):
                    await telegram_bot.process_telegram_update(session, "dummy_token", upd_ai)
                    mock_send.assert_called_once()
                    self.assertIn("iPad 9", mock_send.call_args[0][3])

    def test_canonical_key_extraction(self):
        """Тест извлечения канонического ключа модели из разнородных названий магазинов."""
        from model_matching import extract_canonical_key

        # 1. Варианты iPhone 15 128GB из 6 разных торговых сетей
        t1 = "Смартфон Apple iPhone 15 128Gb черный"
        t2 = "Смартфон Apple iPhone 15 128GB Black Nano-Sim + eSim (MTP03RX/A)"
        t3 = '6.1" Смартфон Apple iPhone 15 128 ГБ черный [2544256]'
        t4 = "Смартфон Apple iPhone 15 128GB Black"
        t5 = "Смартфон Apple iPhone 15 128GB, Черный (MTP03ZD/A)"
        t6 = "Apple iPhone 15 128GB Black"

        k1 = extract_canonical_key(t1)
        self.assertEqual(k1, "apple:iphone 15:128gb")
        for t in (t2, t3, t4, t5, t6):
            self.assertEqual(extract_canonical_key(t), k1)

        # 2. Samsung Galaxy S24 Ultra 1TB
        s1 = "Samsung Galaxy S24 Ultra 1TB"
        s2 = "Samsung Galaxy S24 Ultra 1024 ГБ"
        s3 = "Смартфон Samsung Galaxy S24 Ultra 12/1024Gb Titanium Black (SM-S928B)"
        k_s1 = extract_canonical_key(s1)
        self.assertTrue(k_s1.startswith("samsung:galaxy s24 ultra:1024gb|spec:"))
        self.assertEqual(extract_canonical_key(s2), k_s1)
        self.assertNotEqual(extract_canonical_key(s3), k_s1)  # RAM must be explicit on both offers

        # 3. Видеокарта RTX 4060
        g1 = "Видеокарта Palit GeForce RTX 4060 Dual 8GB"
        g2 = "Palit RTX 4060 Dual 8 ГБ (NE64060019P1-1070D)"
        k_g1 = extract_canonical_key(g1)
        self.assertTrue(k_g1.startswith("palit:rtx 4060:8gb|spec:"))
        self.assertEqual(extract_canonical_key(g2), k_g1)

        # 4. Негативные проверки: разные модели не должны давать один ключ
        self.assertNotEqual(extract_canonical_key("Apple iPhone 15 128GB"), extract_canonical_key("Apple iPhone 15 Pro 128GB"))
        self.assertNotEqual(extract_canonical_key("Apple iPhone 15 128GB"), extract_canonical_key("Apple iPhone 15 256GB"))

    def test_same_model_cross_store_integration(self):
        """Тест сопоставления моделей между разными магазинами с артикулами и тегами."""
        from model_matching import same_model

        # Ранее strict a == b не матчил эти названия из-за артикулов, теперь они матчатся 100%
        self.assertTrue(same_model(
            "Смартфон Apple iPhone 15 128Gb черный",
            "Смартфон Apple iPhone 15 128GB Black Nano-Sim + eSim (MTP03RX/A)"
        ))
        self.assertTrue(same_model(
            '6.1" Смартфон Apple iPhone 15 128 ГБ черный [2544256]',
            "Смартфон Apple iPhone 15 128GB, Черный (MTP03ZD/A)"
        ))
        self.assertTrue(same_model(
            "Игровая приставка Sony PlayStation 5 Slim 1TB",
            "Sony PlayStation 5 Slim 1024 ГБ"
        ))

    async def test_ai_batch_normalization_and_cache(self):
        """Тест пакетной AI-нормализации и персистентного кэширования в SQLite."""
        import ai_service
        from database import get_cached_canonical_key
        from unittest.mock import patch, AsyncMock

        title_mock = "Кастомный игровой ПК SuperPC Ultra Edition"
        mock_creds = {
            "gemini_api_key": "test_key",
            "openai_api_key": "",
            "ai_search_enabled": True,
            "has_ai": True
        }
        mock_ai_resp = {
            "items": [
                {"title": title_mock, "canonical_key": "custom:superpc:ultra"}
            ]
        }

        with patch.object(ai_service, '_get_api_credentials', return_value=mock_creds), \
             patch.object(ai_service, 'call_gemini_api', autospec=True) as mock_gemini:
            mock_gemini.return_value = mock_ai_resp

            res = await ai_service.normalize_product_titles_batch([title_mock])
            self.assertEqual(res.get(title_mock), "custom:superpc:ultra")

            # Проверяем, что ключ сохранился в постоянный кэш SQLite
            cached = get_cached_canonical_key(title_mock)
            self.assertEqual(cached, "custom:superpc:ultra")

            # Повторный вызов не обращается к API, а берет из кэша
            mock_gemini.reset_mock()
            res_cached = await ai_service.normalize_product_titles_batch([title_mock])
            self.assertEqual(res_cached.get(title_mock), "custom:superpc:ultra")
            mock_gemini.assert_not_called()

    async def test_models_compare_endpoint_and_arbitrage(self):
        """Тест кросс-магазинного сравнения цен и арбитража через /api/models/compare."""
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app
        from database import save_or_update_products_batch
        from detector import check_market_arbitrage
        import config

        # Создаем 2 предложения одного товара в разных магазинах с вилкой цен
        p_cheap = {
            "id": "comp-iphone-cheap",
            "shop": "Kaspi",
            "city": "Астана",
            "title": "Смартфон Apple iPhone 15 128Gb черный",
            "category": "Смартфоны",
            "price": 350000,
            "url": "https://kaspi.kz/cheap",
            "image_url": "https://img/cheap"
        }
        p_expensive = {
            "id": "comp-iphone-exp",
            "shop": "Мечта",
            "city": "Астана",
            "title": "Смартфон Apple iPhone 15 128GB Black Nano-Sim (MTP03RX/A)",
            "category": "Смартфоны",
            "price": 460000,
            "url": "https://mechta.kz/exp",
            "image_url": "https://img/exp"
        }

        save_or_update_products_batch([p_cheap, p_expensive])

        # 1. Проверяем обнаружение арбитража детектора
        arb = check_market_arbitrage(p_cheap, custom_settings=config.get_candidate_settings())
        self.assertIsNotNone(arb)
        self.assertEqual(arb["type"], "MARKET_ARBITRAGE")
        self.assertEqual(arb["new_price"], 350000)
        self.assertEqual(arb["old_price"], 460000)
        self.assertEqual(arb["competitor_shop"], "Мечта")
        self.assertEqual(arb["canonical_key"], "apple:iphone 15:128gb")

        # 2. Проверяем API эндпоинт сравнения цен /api/models/compare
        app = create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            res = await client.get('/api/models/compare?canonical_key=apple:iphone 15:128gb&city=Астана')
            self.assertEqual(res.status, 200)
            data = await res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["total_offers"], 2)
            self.assertEqual(data["min_price"], 350000)
            self.assertEqual(data["max_price"], 460000)
            self.assertEqual(data["arbitrage_savings"], 110000)
            self.assertGreater(data["arbitrage_pct"], 20.0)

    def test_distinct_specifications_never_match(self):
        from model_matching import same_model, extract_canonical_key
        pairs = [
            ('Apple MacBook Air M2 8GB 256GB', 'Apple MacBook Air M2 16GB 256GB'),
            ('Apple MacBook Pro M3 Pro 16GB 512GB', 'Apple MacBook Pro M3 Max 16GB 512GB'),
            ('Sony PlayStation 5 Slim Digital 1TB', 'Sony PlayStation 5 Slim 1TB'),
            ('Apple iPad Air M1 64GB', 'Apple iPad Air M2 64GB'),
            ('Samsung Galaxy A15 4G 128GB', 'Samsung Galaxy A15 5G 128GB'),
            ('Palit RTX 4070 Ti 12GB', 'Palit RTX 4070 Ti Super 12GB'),
            ('Apple iPhone SE (2020) 64GB', 'Apple iPhone SE (2022) 64GB'),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertFalse(same_model(a, b))
                self.assertNotEqual(extract_canonical_key(a), extract_canonical_key(b))

    async def test_consultant_access_limits_and_validation(self):
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from unittest.mock import patch, AsyncMock
        import web.server as server
        app = server.create_app(); app.cleanup_ctx.clear()
        upsert_telegram_user({'id': 876, 'first_name': 'Test'})
        with patch.object(server, 'consultant_limiter', server.RateLimiter(2, 60)), \
             patch('ai_service.ask_ai_consultant', new_callable=AsyncMock, return_value={'answer':'ok'}) as ai:
            async with TestClient(TestServer(app)) as client:
                self.assertEqual((await client.post('/api/ai/consultant', json={'message':'test'})).status, 401)
                ai.assert_not_called()
                client.session.cookie_jar.update_cookies({'kzph_session': create_session(876)})
                for body in ([], {'message':'test','history':'bad'}, {'message':'x'*4001}):
                    self.assertEqual((await client.post('/api/ai/consultant', json=body)).status, 400)
                for _ in range(2):
                    self.assertEqual((await client.post('/api/ai/consultant', json={'message':'test'})).status, 200)
                self.assertEqual((await client.post('/api/ai/consultant', json={'message':'test'})).status, 429)
                self.assertEqual(ai.await_count, 2)
                set_user_blocked(876, True)
                self.assertEqual((await client.post('/api/ai/consultant', json={'message':'test'})).status, 401)

    async def test_consultant_preserves_empty_search_constraints(self):
        from unittest.mock import patch, AsyncMock
        import ai_service
        with patch.object(ai_service, '_get_api_credentials', return_value={'has_ai':True}), \
             patch.object(ai_service, 'parse_natural_query', new_callable=AsyncMock, return_value={'clean_query':'iPad','max_price':100000,'category':'Планшеты','only_discount':True}), \
             patch('search_engine.search_in_database', return_value=[]) as search, \
             patch.object(ai_service, 'call_gemini_api', autospec=True) as api:
            result = await ai_service.ask_ai_consultant('iPad до 100000', city='Астана')
            self.assertEqual(result['products'], [])
            search.assert_called_once_with(query='iPad', city='Астана', category='Планшеты', min_price=None, max_price=100000, only_discount=True, sort_by='price_asc')
            api.assert_not_called()

    async def test_normalization_openai_signature_and_scan_integration(self):
        from unittest.mock import patch, AsyncMock
        import ai_service
        import web.server as server
        title = 'Unknown custom device abc'
        creds = {'has_ai':True, 'ai_search_enabled':True, 'gemini_api_key':'', 'openai_api_key':'fake', 'openai_api_base':'https://example.invalid/v1'}
        with patch.object(ai_service, '_get_api_credentials', return_value=creds), \
             patch.object(ai_service, 'call_openai_api', autospec=True, return_value={'items':[{'title':title,'canonical_key':'custom:abc'}]}) as api:
            result = await ai_service.normalize_product_titles_batch([title])
            self.assertEqual(result[title], 'custom:abc')
            self.assertEqual(api.call_args.args[1:], ('fake','https://example.invalid/v1'))
        product = {'id':'norm-scan','title':title,'shop':'Test','city':'Астана','price':100000,'url':'https://example.invalid'}
        server._ai_pending.clear()
        with patch.object(ai_service, 'normalize_product_titles_batch', new_callable=AsyncMock, return_value={title:'custom:abc'}) as normalizer, \
             patch.object(server, '_process_anomaly', new_callable=AsyncMock):
            # Сохранение категории не ждет AI: товар сохраняется сразу, название уходит в фоновую очередь
            await server._save_and_detect([product], 'Test', TEST_SETTINGS)
            normalizer.assert_not_awaited()
            self.assertIn(title, server._ai_pending)
            # Фоновый проход проставляет ключ
            self.assertEqual(await server.process_ai_pending(), 1)
            normalizer.assert_awaited_once_with([title], for_scan=True, max_ai_calls=1)
        self.assertNotIn(title, server._ai_pending)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT canonical_key FROM products WHERE id='norm-scan'").fetchone()[0], 'custom:abc')

    async def test_ai_normalization_budget_and_retry_memory(self):
        """Неудачи не повторяются 7 дней, число вызовов за проход ограничено, пользователю остается резерв."""
        from unittest.mock import patch, AsyncMock
        import ai_service
        creds = {'has_ai':True, 'ai_search_enabled':True, 'gemini_api_key':'fake', 'openai_api_key':'', 'openai_api_base':''}
        titles = [f'Непонятный товар номер {i} без модели' for i in range(60)]  # 3 пакета по 20
        with patch.object(ai_service, '_get_api_credentials', return_value=creds), \
             patch.object(ai_service, 'call_gemini_api', new_callable=AsyncMock, return_value={'items': []}) as api:
            first = await ai_service.normalize_product_titles_batch(titles, for_scan=True, max_ai_calls=2)
            self.assertEqual(api.await_count, 2)                       # лимит вызовов за проход
            self.assertEqual(sum(1 for t in titles if t in first), 40)  # третий пакет не тронут — вернется позже
            self.assertTrue(all(api.call_args.kwargs.get('scan') for _ in [0]))
            api.reset_mock()
            second = await ai_service.normalize_product_titles_batch(titles[:40], for_scan=True)
            api.assert_not_awaited()                                    # неудачи помнятся AI_RETRY_DAYS дней
            self.assertTrue(all(second[t] == '' for t in titles[:40]))
        # Резерв: при двух активных обращениях фоновой задаче отказано, пользовательский вызов проходит
        inner = AsyncMock(return_value={'ok': True})
        with patch.object(ai_service, '_provider_active', 2):
            self.assertIsNone(await ai_service._limited_provider_call(inner, scan=True))
            self.assertEqual(await ai_service._limited_provider_call(inner), {'ok': True})

    async def test_consultant_keeps_conversation_context(self):
        """Уточняющий вопрос разбирается с учетом прошлого диалога; ответ содержит реплику для истории с товарами."""
        from unittest.mock import patch, AsyncMock
        import ai_service
        creds = {'has_ai': True, 'ai_search_enabled': True, 'gemini_api_key': 'fake', 'openai_api_key': '', 'openai_api_base': ''}
        history = [
            {'role': 'user', 'content': 'Посоветуй игровой ноутбук до 400к'},
            {'role': 'model', 'content': 'Рекомендую ASUS TUF.\nПоказанные товары:\n1) ASUS TUF A15 — 389 990 ₸ (Kaspi Магазин)'},
        ]
        prompts = []
        async def fake_gemini(prompt, key, **kwargs):
            prompts.append(prompt)
            if '"clean_query"' in prompt:
                return {'clean_query': 'игровой ноутбук', 'max_price': 300000}
            return {'answer': 'Дешевле подойдет Lenovo LOQ.', 'recommended_product_ids': [], 'suggested_questions': []}
        items = [{'id': 'p1', 'title': 'Ноутбук Lenovo LOQ 15', 'shop': 'Sulpak', 'city': 'Астана', 'current_price': 289990, 'url': 'https://x'}]
        with patch.object(ai_service, '_get_api_credentials', return_value=creds), \
             patch.object(ai_service, 'call_gemini_api', side_effect=fake_gemini), \
             patch('search_engine.search_in_database', return_value=items):
            res = await ai_service.ask_ai_consultant('а подешевле?', history=history, city='Астана')
        parse_prompt, answer_prompt = prompts[0], prompts[1]
        # Разбор уточнения видит прошлый вопрос и показанный товар
        self.assertIn('игровой ноутбук до 400к', parse_prompt)
        self.assertIn('ASUS TUF A15', parse_prompt)
        # Консультант получает прошлый диалог
        self.assertIn('Посоветуй игровой ноутбук до 400к', answer_prompt)
        # Реплика для истории: ответ + показанные товары
        self.assertIn('Дешевле подойдет Lenovo LOQ.', res['history_turn'])
        self.assertIn('Показанные товары', res['history_turn'])
        self.assertIn('Lenovo LOQ 15', res['history_turn'])
        self.assertLessEqual(len(res['history_turn']), ai_service.HISTORY_TURN_CHARS)

    async def test_consultant_long_history_is_trimmed_not_rejected(self):
        from unittest.mock import patch, AsyncMock
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        import web.server as server
        app = server.create_app(); app.cleanup_ctx.clear()
        upsert_telegram_user({'id': 4545, 'first_name': 'Buyer'})
        long_answer = 'Очень подробный ответ. ' * 400   # > 4000 символов
        with patch.object(server.ai_service, 'ask_ai_consultant', new_callable=AsyncMock,
                          return_value={'answer': 'ok', 'products': []}) as ask:
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({'kzph_session': create_session(4545)})
                res = await client.post('/api/ai/consultant', json={
                    'message': 'а подешевле?',
                    'history': [{'role': 'user', 'content': 'ноутбук'}, {'role': 'model', 'content': long_answer}]})
                self.assertEqual(res.status, 200)
        sent = ask.await_args.kwargs['history']
        self.assertEqual(len(sent), 2)
        self.assertLessEqual(len(sent[1]['content']), 1500)

    async def test_telegram_consultant_remembers_dialog(self):
        from unittest.mock import patch, AsyncMock
        import telegram_bot as bot
        bot._chat_histories.clear()
        with patch.object(bot.ai_service, 'ask_ai_consultant', new_callable=AsyncMock,
                          return_value={'answer': 'Берите ASUS TUF', 'history_turn': 'Берите ASUS TUF\nПоказанные товары:\n1) ASUS TUF', 'products': []}) as ask, \
             patch.object(bot, 'send_tg_message', new_callable=AsyncMock), patch.object(bot, 'send_tg_chat_action', new_callable=AsyncMock):
            await bot.handle_ai_consultant_message(None, 't', 777, 'игровой ноутбук до 400к')
            self.assertEqual(ask.await_args.kwargs['history'], [])
            await bot.handle_ai_consultant_message(None, 't', 777, 'а подешевле?')
            second = ask.await_args.kwargs['history']
            self.assertEqual([t['role'] for t in second], ['user', 'model'])
            self.assertEqual(second[0]['content'], 'игровой ноутбук до 400к')
            self.assertIn('ASUS TUF', second[1]['content'])
            # Другой чат — своя история
            await bot.handle_ai_consultant_message(None, 't', 888, 'телевизор')
            self.assertEqual(ask.await_args.kwargs['history'], [])
            # /new сбрасывает разговор
            bot.reset_chat_history(777)
            await bot.handle_ai_consultant_message(None, 't', 777, 'планшет')
            self.assertEqual(ask.await_args.kwargs['history'], [])

    def test_query_rules_parser(self):
        """Разбор фраз без AI: цена, скидка, назначение, синонимы; обычные запросы не трогаются."""
        from query_parser import parse_query_rules as parse
        r = parse('ноутбук для игр до 400к')
        self.assertEqual((r['clean_query'], r['min_price'], r['max_price']), ('ноутбук', None, 400000))
        self.assertIn('rtx', r['prefer_keywords'])
        self.assertEqual(parse('игровой ноут до 400')['max_price'], 400000)          # «до 400» — тысячи
        self.assertEqual(parse('видеокарта rtx 4060 дешевле 200000 тенге')['max_price'], 200000)
        r = parse('телевизор 55 от 150 до 300 тыс')
        self.assertEqual((r['clean_query'], r['min_price'], r['max_price']), ('телевизор 55', 150000, 300000))
        self.assertEqual(parse('холодильник 100-200к')['min_price'], 100000)
        self.assertEqual(parse('стиралка до 1,5 млн')['max_price'], 1500000)
        self.assertEqual(parse('монитор 27 дюймов до 150т')['clean_query'], 'монитор 27')
        r = parse('айфон 15 со скидкой')
        self.assertEqual((r['clean_query'], r['only_discount']), ('iphone 15', True))
        self.assertEqual(parse('посоветуй недорогой пылесос')['clean_query'], 'пылесос')
        for plain in ('SSD 512', 'RTX 4060', 'iPhone 15 Pro 256'):
            self.assertIsNone(parse(plain), plain)                                  # характеристики — не цена

    def test_guest_natural_phrase_search_finds_products(self):
        """Гость ищет фразой: цена и назначение применяются, игровые модели предпочитаются."""
        from search_engine import search_in_database
        from query_parser import parse_query_rules
        items = [
            {'id': 'nb-office', 'title': 'Ноутбук Lenovo IdeaPad 3 15', 'price': 250000},
            {'id': 'nb-gaming', 'title': 'Ноутбук ASUS TUF Gaming A15 RTX 4050', 'price': 390000},
            {'id': 'nb-expensive', 'title': 'Ноутбук ASUS ROG Strix G16 RTX 4070', 'price': 900000},
            {'id': 'nb-bag', 'title': 'Сумка для игрового ноутбука ASUS ROG 15.6"', 'price': 9990},
        ]
        for it in items:
            save_or_update_product(dict(it, shop='Sulpak', city='Астана', category='Ноутбуки', url='https://x/' + it['id']))
        self.assertEqual(search_in_database('ноутбук для игр до 400к'), [])          # фраза целиком не находится
        meta = parse_query_rules('ноутбук для игр до 400к')
        found = search_in_database(meta['clean_query'], max_price=meta['max_price'],
                                   prefer_keywords=meta['prefer_keywords'], product_nouns=meta['product_nouns'])
        self.assertEqual([r['id'] for r in found], ['nb-gaming'])      # сумка «для ноутбука» отсечена
        # Без игровых моделей в бюджете выдача не сужается
        found_all = search_in_database('ноутбук', max_price=300000, prefer_keywords=meta['prefer_keywords'],
                                       product_nouns=meta['product_nouns'])
        self.assertEqual([r['id'] for r in found_all], ['nb-office'])
        from search_engine import _is_accessory_for
        self.assertTrue(_is_accessory_for('Кронштейн для двух мониторов 17-27"', ['монитор']))
        self.assertTrue(_is_accessory_for('Сумка для документов и ноутбука 13.3"', ['ноутбук']))
        self.assertFalse(_is_accessory_for('Монитор Samsung 27" для работы', ['монитор']))

    async def test_ai_consultant_error_hides_details(self):
        from unittest.mock import patch, AsyncMock
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        import web.server as server
        app = server.create_app(); app.cleanup_ctx.clear()
        upsert_telegram_user({'id': 4343, 'first_name': 'Buyer'})
        with patch.object(server.ai_service, 'ask_ai_consultant', new_callable=AsyncMock, side_effect=RuntimeError('секретная-деталь')):
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({'kzph_session': create_session(4343)})
                res = await client.post('/api/ai/consultant', json={'message': 'Посоветуй ноутбук'})
                self.assertEqual(res.status, 500)
                self.assertNotIn('секретная-деталь', await res.text())

    async def test_admin_config_never_returns_raw_keys(self):
        from unittest.mock import patch
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        import web.server as server
        app=server.create_app(); app.cleanup_ctx.clear()
        upsert_telegram_user({'id':987,'first_name':'Admin'})
        keys={'gemini_api_key':'fake-gemini-secret', 'openai_api_key':'fake-openai-secret'}
        with patch('auth.ADMIN_TELEGRAM_IDS',{987}), patch.object(server,'get_bot_username',return_value=None), \
             patch.object(server,'load_settings',return_value=keys), patch.object(server,'get_ai_config',return_value=keys), \
             patch.object(server,'save_settings',return_value=keys):
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({'kzph_session':create_session(987)})
                for response in (await client.get('/api/admin/config'), await client.post('/api/admin/config',json={})):
                    self.assertEqual(response.status,200)
                    body=await response.text()
                    for secret in keys.values(): self.assertNotIn(secret,body)

    async def test_telegram_block_and_limit(self):
        from unittest.mock import patch, AsyncMock
        import telegram_bot as bot
        from auth import RateLimiter
        update={'message':{'chat':{'id':678,'type':'private'},'from':{'id':678},'text':'test'}}
        with patch.object(bot,'get_user',return_value={'is_blocked':True}), \
             patch.object(bot,'handle_ai_consultant_message',new_callable=AsyncMock) as ai:
            await bot.process_telegram_update(None,'fake',update)
            ai.assert_not_called()
        with patch.object(bot,'get_user',return_value=None), patch.object(bot,'_message_limiter',RateLimiter(1,60)), \
             patch.object(bot,'handle_ai_consultant_message',new_callable=AsyncMock) as ai:
            await bot.process_telegram_update(None,'fake',update)
            await bot.process_telegram_update(None,'fake',update)
            self.assertEqual(ai.await_count,1)

    def test_identity_migration_preserves_prices(self):
        from model_matching import extract_canonical_key
        title='Apple MacBook Air M2 16GB 256GB'
        save_or_update_product({'id':'migration-spec','title':title,'shop':'Test','price':123456,'url':'https://example.invalid'})
        with get_connection() as conn:
            conn.execute("UPDATE products SET canonical_key='old-collision' WHERE id='migration-spec'")
            conn.execute("DELETE FROM schema_metadata WHERE name='identity_v2'")
            # Миграция 1 (identity_v2) — разовая: вызывается напрямую, как для старой базы
            from database import _migration_legacy_identity_v2
            _migration_legacy_identity_v2(conn)
            conn.commit()
        with get_connection() as conn:
            row=conn.execute("SELECT canonical_key,current_price FROM products WHERE id='migration-spec'").fetchone()
            self.assertEqual(row['canonical_key'],extract_canonical_key(title))
            self.assertEqual(row['current_price'],123456)

    async def test_provider_shared_budget_and_concurrency(self):
        import asyncio
        import ai_service as ai
        from auth import RateLimiter
        from unittest.mock import patch, AsyncMock
        with patch.object(ai, '_provider_limiter', RateLimiter(1, 60)), \
             patch.object(ai, '_call_gemini_api', new_callable=AsyncMock, return_value={'ok':True}) as gemini, \
             patch.object(ai, '_call_openai_api', new_callable=AsyncMock) as openai:
            self.assertEqual(await ai.call_gemini_api('prompt','fake'), {'ok':True})
            self.assertIsNone(await ai.call_openai_api('prompt','fake','https://example.invalid'))
            openai.assert_not_called()
        entered=asyncio.Event(); release=asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
        with patch.object(ai, '_provider_limiter', RateLimiter(60,60)), patch.object(ai, '_call_gemini_api', side_effect=slow):
            tasks=[asyncio.create_task(ai.call_gemini_api('p','fake')) for _ in range(3)]
            await entered.wait()
            self.assertIsNone(await ai.call_gemini_api('p','fake'))
            release.set()
            await asyncio.gather(*tasks)
            self.assertEqual(ai._provider_active,0)

    async def test_scan_survives_normalization_failure(self):
        import web.server as server
        from unittest.mock import patch, AsyncMock
        product={'id':'normalization-failure','title':'Device 99','shop':'Test','price':123000,'url':'https://example.invalid'}
        with patch('ai_service.normalize_product_titles_batch', new_callable=AsyncMock, side_effect=RuntimeError('simulated')), \
             patch.object(server,'_process_anomaly',new_callable=AsyncMock):
            await server._save_and_detect([product],'Test',TEST_SETTINGS)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT current_price FROM products WHERE id='normalization-failure'").fetchone()[0],123000)

    async def test_cached_collision_cannot_create_comparison(self):
        from model_matching import extract_canonical_key
        from database import find_market_comparisons
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app
        title='Apple MacBook Air M2 8GB 256GB'
        key=extract_canonical_key(title)
        save_or_update_products_batch([
            {'id':'collision-a','title':title,'shop':'Shop1','city':'Астана','price':250000,'url':'https://example.invalid/a','canonical_key':key},
            {'id':'collision-b','title':'Apple MacBook Air M2 16GB 256GB','shop':'Shop2','city':'Астана','price':500000,'url':'https://example.invalid/b','canonical_key':key}])
        self.assertIsNone(find_market_comparisons(title,'Shop1',250000,'Астана'))
        app=create_app(); app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            response=await client.get('/api/models/compare',params={'canonical_key':key,'title':title,'city':'Астана'})
            data=await response.json()
            self.assertEqual(data['total_offers'],1)

    def test_watch_accessories_and_fitness_trackers(self):
        from detector import is_junk_accessory
        self.assertTrue(is_junk_accessory('Браслет S&M для Apple Watch сталь нержавеющая'))
        self.assertTrue(is_junk_accessory('WiWU Comf Secur', 'WiWU Ремешки для Apple Watch'))
        self.assertTrue(is_junk_accessory('Внешний аккумулятор для Apple Watch 2.5 Вт'))
        self.assertFalse(is_junk_accessory('Фитнес-браслет Xiaomi Smart Band 9'))

    async def test_broad_search_does_not_invent_savings(self):
        from unittest.mock import patch
        from search_engine import get_best_price_summary
        items=[{'id':'watch-a','title':'Apple Watch SE 40mm','current_price':100000,'shop':'A','city':'Астана','url':'https://example.invalid/a'},
               {'id':'watch-b','title':'Apple Watch Ultra 49mm','current_price':450000,'shop':'B','city':'Астана','url':'https://example.invalid/b'}]
        with patch('search_engine.search_in_database',return_value=items):
            result=await get_best_price_summary('Apple Watch')
            self.assertEqual(result['best_deal']['savings_vs_max'],0)
        items.append(dict(items[0],id='watch-c',shop='C',current_price=120000))
        with patch('search_engine.search_in_database',return_value=items):
            result=await get_best_price_summary('Apple Watch')
            self.assertEqual(result['best_deal']['savings_vs_max'],20000)

    async def test_kaspi_live_search_city_and_query(self):
        from unittest.mock import patch, Mock
        from scrapers.kaspi import KaspiScraper
        scraper=KaspiScraper(city_code='750000000')
        response=Mock(status_code=200)
        response.json.return_value={'data':[{'id':777,'title':'Apple Watch SE','unitPrice':100000,'stock':1}]}
        with patch('scrapers.kaspi.requests.get',return_value=response) as get:
            products=await scraper.search('Apple Watch',max_items=1)
            self.assertEqual(products[0]['city'],'Алматы')
            self.assertEqual(get.call_args.kwargs['params']['text'],'Apple Watch')
            self.assertEqual(get.call_args.kwargs['params']['c'],'750000000')
            self.assertEqual(get.call_args.kwargs['params']['q'],'')

    def test_sulpak_retries_timeout_once(self):
        from unittest.mock import patch, Mock
        from scrapers.sulpak import SulpakScraper, requests
        from scrapers.base import UnconfirmedEnd
        response=Mock(status_code=200,text='<html></html>')
        with patch('scrapers.sulpak.requests.get',side_effect=[requests.exceptions.Timeout('simulated'),response]) as get, patch('scrapers.sulpak.time.sleep'):
            with self.assertRaises(UnconfirmedEnd):
                SulpakScraper()._fetch_page('Часы','https://www.sulpak.kz/f/smart_chasiy',1)
            self.assertEqual(get.call_count,2)

    async def test_provider_selection_and_fallback(self):
        import ai_service as ai
        from unittest.mock import patch, AsyncMock
        for selection in ('auto', 'gemini', 'openai'):
            settings={**config.SYSTEM_DEFAULTS,'ai_provider':selection}
            with patch.object(config,'load_settings',return_value=settings), \
                 patch.object(config,'GEMINI_API_KEY','fake-gemini'), patch.object(config,'OPENAI_API_KEY','fake-openai'), \
                 patch.object(ai,'call_gemini_api',new_callable=AsyncMock,return_value=None) as gemini, \
                 patch.object(ai,'call_openai_api',new_callable=AsyncMock,return_value={'clean_query':'iPhone'}) as openai:
                ai._QUERY_CACHE.clear()
                await ai.parse_natural_query('подбери iPhone',force=True)
                self.assertEqual(gemini.await_count, 0 if selection=='openai' else 1)
                self.assertEqual(openai.await_count, 0 if selection=='gemini' else 1)
        with patch.object(config,'load_settings',return_value={**config.SYSTEM_DEFAULTS,'ai_provider':'gemini'}), \
             patch.object(config,'GEMINI_API_KEY',''), patch.object(config,'OPENAI_API_KEY','fake'):
            self.assertFalse(config.get_ai_config()['has_ai'])

    async def test_manual_model_used_in_provider_request(self):
        import ai_service as ai
        from unittest.mock import patch
        captured=[]
        class Response:
            status=200
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def json(self):
                return {'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]}}],
                        'choices':[{'message':{'content':'{"ok":true}'}}]}
        class Session:
            def __init__(self,*args,**kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            def post(self,url,**kwargs):
                captured.append((url,kwargs['json']))
                return Response()
        settings={**config.SYSTEM_DEFAULTS,'gemini_model_mode':'manual','gemini_model':'gemini-test',
                  'openai_model_mode':'manual','openai_model':'custom-model'}
        with patch.object(config,'load_settings',return_value=settings), patch.object(ai.aiohttp,'ClientSession',Session):
            await ai._call_gemini_api('test','fake')
            await ai._call_openai_api('test','fake','https://example.invalid/v1')
        self.assertIn('/models/gemini-test:generateContent',captured[0][0])
        self.assertEqual(captured[1][1]['model'],'custom-model')

    def test_model_settings_validation_and_persistence(self):
        from unittest.mock import patch
        with patch.object(config,'load_settings',return_value=dict(config.SYSTEM_DEFAULTS)):
            for invalid in ({'ai_provider':'unknown'},{'gemini_model_mode':'other'},
                            {'openai_model_mode':'manual','openai_model':''}):
                with self.assertRaises(ValueError): config._validate_settings(invalid)
        old=config.load_settings()
        try:
            config.save_settings({'ai_provider':'openai','openai_model_mode':'manual','openai_model':'custom-model'})
            current=config.load_settings()
            self.assertEqual(current['ai_provider'],'openai')
            self.assertEqual(current['openai_model'],'custom-model')
            self.assertEqual(config.get_ai_config()['openai_model'],'custom-model')
            config.save_settings({'openai_model_mode':'auto'})
            self.assertEqual(config.get_ai_config()['openai_model'],'gpt-4o-mini')
        finally:
            config.save_settings(old)

    def test_master_categories_definition_and_coverage(self):
        """Проверка целостности мастер-категорий и их охвата по всем магазинам."""
        self.assertEqual(len(config.MASTER_CATEGORIES), 19)
        required_keys = {"name", "icon", "description"}
        for cat_id, meta in config.MASTER_CATEGORIES.items():
            self.assertTrue(required_keys.issubset(meta.keys()))
            self.assertTrue(len(meta["name"]) > 0)
            self.assertTrue(len(meta["icon"]) > 0)

        for hot_cat in config.DEFAULT_HOT_CATEGORIES:
            self.assertIn(hot_cat, config.MASTER_CATEGORIES)

        # Проверяем все 17 магазинов: каждая категория должна иметь "master"
        from web.server import SHOP_REGISTRY
        for shop_key, (scraper_cls, categories, shop_name) in SHOP_REGISTRY.items():
            for c in categories:
                self.assertIn("master", c, f"Магазин {shop_key}, категория {c.get('name')} не имеет поля 'master'")
                m = c["master"]
                self.assertTrue(m == "all" or m in config.MASTER_CATEGORIES,
                                f"Некорректный master '{m}' в магазине {shop_key}, категория {c.get('name')}")

    def test_wave_plan_rotation_and_modes(self):
        """Проверка работы волнового планировщика: Hot-категории, размер волны, ротация."""
        enabled = {k: True for k in config.MASTER_CATEGORIES}
        hot = ["smartphones", "laptops"]

        # 1. Режим all
        plan_all = config.get_wave_plan(enabled_categories=enabled, hot_categories=hot, wave_index=0, wave_size=2, wave_mode="all")
        self.assertEqual(len(plan_all["wave_categories"]), len(config.MASTER_CATEGORIES))
        self.assertEqual(plan_all["total_waves"], 1)

        # 2. Волновой режим rolling
        plan0 = config.get_wave_plan(enabled_categories=enabled, hot_categories=hot, wave_index=0, wave_size=2, wave_mode="rolling")
        self.assertEqual(plan0["hot_categories"], hot)
        self.assertEqual(len(plan0["wave_categories"]), 2)
        # Hot-категории не дублируются в ротируемых волнах
        for c in plan0["wave_categories"]:
            self.assertNotIn(c, hot)

        # 3. Ротация на следующую волну
        plan1 = config.get_wave_plan(enabled_categories=enabled, hot_categories=hot, wave_index=1, wave_size=2, wave_mode="rolling")
        self.assertNotEqual(plan0["wave_categories"], plan1["wave_categories"])
        self.assertEqual(plan0["next_wave_categories"], plan1["wave_categories"])

        # 4. Круговая ротация (цикличность)
        total_waves = plan0["total_waves"]
        plan_wrap = config.get_wave_plan(enabled_categories=enabled, hot_categories=hot, wave_index=total_waves, wave_size=2, wave_mode="rolling")
        self.assertEqual(plan_wrap["wave_categories"], plan0["wave_categories"])

        # 5. Исключение выключенных категорий
        enabled_partial = dict(enabled)
        enabled_partial["monitors"] = False
        plan_partial = config.get_wave_plan(enabled_categories=enabled_partial, hot_categories=hot, wave_index=0, wave_size=10, wave_mode="rolling")
        self.assertNotIn("monitors", plan_partial["wave_categories"])

    async def test_admin_category_endpoints(self):
        """Проверка API категорий: обзор, сохранение настроек волн и точечный on-demand запуск."""
        from unittest.mock import patch
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app

        upsert_telegram_user({'id': 100, 'first_name': 'CategoryAdmin'})
        token = create_session(100)
        app = create_app()
        app.cleanup_ctx.clear()

        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'kzph_session': token})

            # Без прав админа доступ запрещен (403)
            res = await client.get('/api/admin/categories')
            self.assertEqual(res.status, 403)

            with patch('auth.ADMIN_TELEGRAM_IDS', {100}):
                # 1. GET /api/admin/categories
                res = await client.get('/api/admin/categories')
                self.assertEqual(res.status, 200)
                data = await res.json()
                self.assertIn("categories", data)
                self.assertIn("wave_plan", data)
                self.assertEqual(len(data["categories"]), len(config.MASTER_CATEGORIES))

                # 2. POST /api/admin/categories (сохранение настроек)
                res = await client.post('/api/admin/categories', json={
                    "enabled_categories": {"smartphones": True, "laptops": False},
                    "hot_categories": ["smartphones"],
                    "wave_mode": "rolling",
                    "wave_size": 3
                })
                self.assertEqual(res.status, 200)
                save_data = await res.json()
                self.assertEqual(save_data["status"], "ok")

                # 3. POST /api/scan/category валидация
                res_err = await client.post('/api/scan/category', json={"category": "invalid_cat"})
                self.assertEqual(res_err.status, 400)

                # 4. POST /api/scan/category успешный запуск
                with patch('web.server._do_scan_task'):
                    res_ok = await client.post('/api/scan/category', json={"category": "smartphones"})
                    self.assertEqual(res_ok.status, 200)
                    resp_json = await res_ok.json()
                    self.assertEqual(resp_json["status"], "started")
                    self.assertEqual(resp_json["category"], "smartphones")

    def test_kaspi_url_normalization_and_scraping(self):
        """Проверка исправления ссылок Kaspi: kaspi.kz/p/ -> kaspi.kz/shop/p/ в БД и скрапере."""
        # 1. Проверка save_or_update_product
        p1 = {
            "id": "test-kaspi-1",
            "title": "Тестовый Kaspi Товар 1",
            "price": 100000,
            "url": "https://kaspi.kz/p/test-laptop-102715483/?c=710000000",
            "shop": "Kaspi",
            "city": "Астана"
        }
        save_or_update_product(p1)

        with get_connection() as conn:
            row = conn.execute("SELECT url FROM products WHERE id = 'test-kaspi-1'").fetchone()
            self.assertEqual(row["url"], "https://kaspi.kz/shop/p/test-laptop-102715483/?c=710000000")

        # 2. Проверка save_or_update_products_batch
        p2 = {
            "id": "test-kaspi-2",
            "title": "Тестовый Kaspi Товар 2",
            "price": 120000,
            "url": "https://kaspi.kz/p/test-phone-99999/?c=710000000",
            "shop": "Kaspi",
            "city": "Астана"
        }
        save_or_update_products_batch([p2])
        with get_connection() as conn:
            row = conn.execute("SELECT url FROM products WHERE id = 'test-kaspi-2'").fetchone()
            self.assertEqual(row["url"], "https://kaspi.kz/shop/p/test-phone-99999/?c=710000000")

        # 3. Проверка автоматической миграции в init_db
        with get_connection() as conn:
            # Насильно вставляем некорректный URL в обход методов
            conn.execute("UPDATE products SET url = 'https://kaspi.kz/p/raw-bad-url-123' WHERE id = 'test-kaspi-1'")
            conn.commit()
            check_bad = conn.execute("SELECT url FROM products WHERE id = 'test-kaspi-1'").fetchone()
            self.assertEqual(check_bad["url"], "https://kaspi.kz/p/raw-bad-url-123")

        # Разовая миграция чисток (раньше выполнялась при каждом старте init_db)
        from database import _migration_legacy_cleanups
        with get_connection() as conn:
            _migration_legacy_cleanups(conn)
            conn.commit()
        with get_connection() as conn:
            check_fixed = conn.execute("SELECT url FROM products WHERE id = 'test-kaspi-1'").fetchone()
            self.assertEqual(check_fixed["url"], "https://kaspi.kz/shop/p/raw-bad-url-123")

        # 4. Проверка скрапера KaspiScraper
        from scrapers.kaspi import KaspiScraper
        from unittest.mock import Mock, patch
        scraper = KaspiScraper()
        fake_data = {
            "data": {
                "cards": [
                    {
                        "id": "102715483",
                        "title": "Lenovo IdeaPad 3",
                        "unitPrice": 150000,
                        "shopLink": "/p/lenovo-ideapad-3-102715483/?c=710000000",
                        "category": ["Ноутбуки"]
                    }
                ]
            }
        }
        mock_resp = Mock(status_code=200, json=lambda: fake_data)
        with patch('scrapers.kaspi.requests.get', return_value=mock_resp):
            items = scraper._fetch_page("Ноутбуки", "https://kaspi.kz/shop/c/notebooks/", 1)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["url"], "https://kaspi.kz/shop/p/lenovo-ideapad-3-102715483/?c=710000000")

    def test_price_glitch_protection_and_alert_dismissal(self):
        """Тест защиты от склейки цен, копеек, абсурдных выбросов и проверка скрытия алертов."""
        from scrapers.base import parse_price
        from database import dismiss_alert, get_alerts

        # 1. Проверка надежного парсинга цен (parse_price)
        self.assertEqual(parse_price("72 228 ₸"), 72228)
        self.assertEqual(parse_price("72 228.00 ₸"), 72228)
        self.assertEqual(parse_price("72 228,0 ₸"), 72228)
        self.assertEqual(parse_price("72228.0"), 72228)
        # Склейка двух цен (текущая и зачеркнутая в одном блоке, например DNS)
        self.assertEqual(parse_price("179 990 ₸ 200 650 ₸"), 179990)
        # Абсурдный выброс > 10 млн тенге отсекается
        self.assertEqual(parse_price("179990200650"), 0)
        self.assertEqual(parse_price("нет в наличии"), 0)

        # 2. Проверка детектора аномалий: защита от абсурдных цен (179 млрд тенге)
        glitched_product = {
            "id": "test-glitch-tv",
            "title": "Телевизор Xiaomi TV A 55",
            "price": 179990,
            "url": "https://dns-shop.kz/test/",
            "city": "Астана"
        }
        corrupt_history = {
            "old_price": 179990200650,
            "first_seen_price": 179990200650
        }
        # Не должно считать падение с 179 миллиардов аномалией/скидкой!
        anomaly = check_anomaly(glitched_product, corrupt_history, custom_settings=TEST_SETTINGS)
        self.assertIsNone(anomaly)

        # 3. Проверка защиты БД: save_or_update_product и record_alert отклоняют > 10 млн
        bad_save = save_or_update_product({
            "id": "test-bad-price-1",
            "title": "Сбойный товар",
            "price": 179990200650,
            "url": "https://dns-shop.kz/test/"
        })
        self.assertEqual(bad_save["current_price"], 0)

        bad_alert_id = record_alert("test-bad-price-1", "SUPER_DISCOUNT", 179990200650, 179990, 100.0, 179990020660)
        self.assertEqual(bad_alert_id, 0)

        # 4. Проверка скрытия алерта (dismiss_alert)
        # Создаем валидный товар и алерт
        save_or_update_product({
            "id": "test-dismiss-prod",
            "title": "Тестовый товар для скрытия",
            "price": 60000,
            "url": "https://dns-shop.kz/dismiss/"
        })
        valid_alert_id = record_alert("test-dismiss-prod", "SUPER_DISCOUNT", 200000, 60000, 70.0, 140000)
        self.assertGreater(valid_alert_id, 0)

        # Алерт виден в выдаче get_alerts
        active_alerts = get_alerts(limit=50)
        self.assertTrue(any(a["id"] == valid_alert_id for a in active_alerts))

        # Скрываем алерт
        dismiss_ok = dismiss_alert(valid_alert_id)
        self.assertTrue(dismiss_ok)

        # Алерт больше не возвращается в get_alerts
        active_alerts_after = get_alerts(limit=50)
        self.assertFalse(any(a["id"] == valid_alert_id for a in active_alerts_after))

    async def test_alert_dismiss_http_api(self):
        """Проверка HTTP API /api/alerts/dismiss и DELETE /api/alerts/{id}."""
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app

        save_or_update_product({
            "id": "test-http-dismiss",
            "title": "Товар для проверки API скрытия",
            "price": 60000,
            "url": "https://dns-shop.kz/dismiss-api/"
        })
        a_id = record_alert("test-http-dismiss", "SUPER_DISCOUNT", 200000, 60000, 70.0, 140000)
        self.assertGreater(a_id, 0)

        app = create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            upsert_telegram_user({'id': 1, 'first_name': 'Admin'})
            client.session.cookie_jar.update_cookies({'kzph_session': create_session(1)})
            # 1. Проверка POST /api/alerts/dismiss с невалидным ID
            bad_res = await client.post('/api/alerts/dismiss', json={"id": 0})
            self.assertEqual(bad_res.status, 400)

            # 2. Проверка успешного скрытия через POST /api/alerts/dismiss
            ok_res = await client.post('/api/alerts/dismiss', json={"id": a_id})
            self.assertEqual(ok_res.status, 200)
            data = await ok_res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["dismissed_id"], a_id)

            # Проверка, что алерт исчез из выдачи GET /api/alerts
            get_res = await client.get('/api/alerts')
            self.assertEqual(get_res.status, 200)
            alerts_data = await get_res.json()
            self.assertFalse(any(a["id"] == a_id for a in alerts_data))

    def test_fourmobile_scraper_image_description_and_url(self):
        """Проверка парсинга изображений высокого разрешения, описания и ссылки заказа 4mobile."""
        from scrapers.fourmobile import FourMobileScraper
        from unittest.mock import patch, MagicMock

        mock_data = {
            "price": [
                {
                    "cat": "MacBook",
                    "items": [
                        [
                            'MacBook Air 13.6" M5 16/512Gb',
                            '680 000 ₸',
                            '/api/img/c1af2d4610714201825849dd93d4dc36',
                            'Apple MacBook Air M5 - ультратонкий и тихий ноутбук на базе мощного процессора M5.'
                        ]
                    ]
                }
            ]
        }

        scraper = FourMobileScraper()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data

        with patch("scrapers.fourmobile.requests.get", return_value=mock_resp):
            res = scraper._scrape_sync("4mobile: 🔥 Все товары", scraper.api_url, 1)

        self.assertEqual(len(res), 1)
        p = res[0]
        self.assertEqual(p["title"], 'MacBook Air 13.6" M5 16/512Gb')
        self.assertEqual(p["price"], 680000)
        self.assertEqual(p["image_url"], "https://4mobile.pages.dev/api/img/c1af2d4610714201825849dd93d4dc36")
        self.assertIn("ультратонкий", p["description"])
        self.assertTrue(p["url"].startswith("https://wa.me/77007654321?text="))
        self.assertIn("MacBook", p["url"])

    async def test_product_description_storage_and_api(self):
        """Проверка сохранения description в БД, получения через get_product_by_id и API /api/products/{id}."""
        from database import get_product_by_id, _fetch_filtered_alerts
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from web.server import create_app

        prod_id = "test-prod-desc-440"
        save_or_update_product({
            "id": prod_id,
            "title": "Ноутбук Тестовый Pro 16",
            "price": 450000,
            "url": "https://example.kz/item/1",
            "image_url": "https://example.kz/img.jpg",
            "description": "Полноразмерный ноутбук с 16-дюймовым OLED дисплеем и 32 ГБ ОЗУ.",
            "category": "Ноутбуки",
            "shop": "Kaspi Магазин",
            "city": "Астана"
        })

        p = get_product_by_id(prod_id)
        self.assertIsNotNone(p)
        self.assertEqual(p["description"], "Полноразмерный ноутбук с 16-дюймовым OLED дисплеем и 32 ГБ ОЗУ.")

        # Проверка включения description в выборку алертов
        record_alert(prod_id, "SUPER_DISCOUNT", 1500000, 450000, 70.0, 1050000)
        alerts = _fetch_filtered_alerts(config.SYSTEM_DEFAULTS)
        alert_item = next((a for a in alerts if a.get("product_id") == prod_id), None)
        self.assertIsNotNone(alert_item)
        self.assertIn("OLED дисплеем", alert_item.get("description", ""))

        # Проверка HTTP API GET /api/products/{id}
        app = create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            res = await client.get(f'/api/products/{prod_id}')
            self.assertEqual(res.status, 200)
            data = await res.json()
            self.assertEqual(data["id"], prod_id)
            self.assertEqual(data["description"], "Полноразмерный ноутбук с 16-дюймовым OLED дисплеем и 32 ГБ ОЗУ.")

            bad_res = await client.get('/api/products/non-existent-id')
            self.assertEqual(bad_res.status, 404)

    async def test_search_engine_stale_refresh_requires_explicit_live(self):
        """Старые данные не запускают сеть без явного live=True."""
        import datetime
        from database import get_connection
        from search_engine import get_best_price_summary
        from unittest.mock import patch

        stale_id = "test-stale-laptop-12h"
        save_or_update_product({
            "id": stale_id,
            "title": "Ультрабук Stale Refresh 14",
            "price": 300000,
            "url": "https://example.kz/stale",
            "category": "Ноутбуки",
            "shop": "Kaspi Магазин",
            "city": "Астана"
        })

        # Искусственно сдвигаем updated_at на 15 часов назад
        old_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=15)).isoformat()
        with get_connection() as conn:
            conn.execute("UPDATE products SET updated_at = ? WHERE id = ?", (old_time, stale_id))
            conn.commit()

        live_called = []

        async def fake_live_search(q, city="Астана"):
            live_called.append((q, city))
            # Симулируем обновление товара в процессе live-поиска
            save_or_update_product({
                "id": stale_id,
                "title": "Ультрабук Stale Refresh 14",
                "price": 280000,
                "url": "https://example.kz/stale",
                "category": "Ноутбуки",
                "shop": "Kaspi Магазин",
                "city": "Астана"
            })
            return []

        with patch("search_engine.search_live_stores", side_effect=fake_live_search):
            # Поиск без флага live (live=False)
            result = await get_best_price_summary("Ультрабук Stale Refresh 14", live=False)

        self.assertEqual(live_called, [])
        self.assertEqual(result["items"][0]["current_price"], 300000)
        with patch("search_engine.search_live_stores", side_effect=fake_live_search):
            result = await get_best_price_summary("Ультрабук Stale Refresh 14", live=True)
        self.assertEqual(len(live_called), 1)
        self.assertEqual(result["items"][0]["current_price"], 280000)

    async def test_product_description_live_enrichment(self):
        """Проверка живого извлечения и кэширования описания товара при запросе к API."""
        from unittest.mock import patch
        from web.server import create_app
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from database import save_or_update_product, get_product_by_id

        # Сохраняем тестовый товар без описания
        test_prod_id = "test_desc_live_99"
        save_or_update_product({
            "id": test_prod_id,
            "title": "Тестовая видеокарта Pro 16GB",
            "price": 250000,
            "shop": "Белый Ветер",
            "url": "https://shop.kz/offer/test-videokarta-pro-16gb/",
            "category": "Видеокарты",
            "city": "Астана"
        })

        p_before = get_product_by_id(test_prod_id)
        self.assertFalse(p_before.get("description"))

        fake_html = """
        <html>
            <body>
                <div class="bx_item_description">
                    <h2>Описание</h2>
                    <p>Высокопроизводительная видеокарта нового поколения с 16 ГБ GDDR7 памяти.</p>
                </div>
                <div class="dotted-item">
                    <span class="dotted-item__name">Частота GPU</span>
                    <span class="dotted-item__value">2500 МГц</span>
                </div>
            </body>
        </html>
        """
        class FakeResponse:
            status_code = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}
            encoding = "utf-8"
            def iter_content(self):
                yield fake_html.encode()
            def close(self):
                pass

        with patch("curl_cffi.requests.Session.request", return_value=FakeResponse()), \
             patch("product_details._resolve_public"):
            app = create_app()
            app.cleanup_ctx.clear()
            async with TestClient(TestServer(app)) as client:
                resp = await client.get(f"/api/products/{test_prod_id}")
                self.assertEqual(resp.status, 200)
                data = await resp.json()
                self.assertIn("Высокопроизводительная видеокарта нового поколения", data.get("description", ""))
                # Проверяем, что в БД описание также сохранилось
                p_after = get_product_by_id(test_prod_id)
                self.assertIn("Высокопроизводительная видеокарта нового поколения", p_after.get("description", ""))

    async def test_search_saves_tracked_category(self):
        """Проверка, что поиск лучшей цены определяет и сохраняет категорию в tracked_categories."""
        from unittest.mock import patch
        from search_engine import search_live_stores, _LIVE_CACHE
        from database import get_tracked_categories, delete_tracked_category
        _LIVE_CACHE.clear()

        # Мокаем KaspiScraper.search так, чтобы он возвращал найденный товар
        fake_kaspi_item = {
            "id": "test_rtx_live_1",
            "title": "Видеокарта Gigabyte GeForce RTX 5070 12GB",
            "price": 380000,
            "shop": "Kaspi Магазин",
            "category": "Видеокарты",
            "url": "https://kaspi.kz/shop/p/test-5070",
            "city": "Астана"
        }

        with patch("scrapers.kaspi.KaspiScraper.search", return_value=[fake_kaspi_item]), \
             patch("curl_cffi.requests.get", side_effect=Exception("skip shopkz")), \
             patch("scrapers.fourmobile.FourMobileScraper.search_live", return_value=[]), \
             patch("scrapers.fortemarket.ForteMarketScraper.search_live", return_value=[]):
            
            # Первый поиск
            await search_live_stores("rtx 5070", city="Астана")
            _LIVE_CACHE.clear()
            # Второй поиск того же товара
            await search_live_stores("rtx 5070", city="Астана")

        cats = get_tracked_categories(active_only=False, limit=50)
        found = next((c for c in cats if c["name"] == "Видеокарты"), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["master_category"], "pc_components")
        self.assertGreaterEqual(found["search_count"], 2)

        # Очистка
        delete_tracked_category(found["id"])

    async def test_tracked_categories_api_and_wave_refresh(self):
        """Проверка API эндпоинтов управления отслеживаемыми категориями и их готовности к волне."""
        from web.server import create_app
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from database import save_tracked_category, get_due_tracked_categories, mark_tracked_category_scanned, delete_tracked_category

        cat = save_tracked_category("Роботы-пылесосы", "робот пылесос", "appliances_small")
        cat_id = cat["id"]

        try:
            # Проверяем get_due_tracked_categories
            due = get_due_tracked_categories(limit=10)
            self.assertTrue(any(c["id"] == cat_id for c in due))
            mark_tracked_category_scanned(cat_id)

            # Проверяем API
            upsert_telegram_user({"id": 1, "first_name": "Admin"})
            token = create_session(1)

            app = create_app()
            app.cleanup_ctx.clear()
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({"kzph_session": token})

                # GET
                resp = await client.get("/api/categories/tracked")
                self.assertEqual(resp.status, 200)
                items = await resp.json()
                self.assertTrue(any(it["id"] == cat_id for it in items))

                # TOGGLE
                resp_toggle = await client.post(f"/api/categories/tracked/{cat_id}/toggle", json={"is_active": False})
                self.assertEqual(resp_toggle.status, 200)

                # DELETE
                resp_del = await client.delete(f"/api/categories/tracked/{cat_id}")
                self.assertEqual(resp_del.status, 200)

        finally:
            delete_tracked_category(cat_id)


class TestForteMarketScraper(unittest.TestCase):
    """Тесты интеграции магазина Forte Market."""

    def setUp(self):
        from scrapers.fortemarket import ForteMarketScraper
        self.scraper = ForteMarketScraper(city="Астана")

    def test_registry_contains_fortemarket(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, FORTE_CATEGORIES
        self.assertIn("fortemarket", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["fortemarket"]
        self.assertEqual(name, "Forte Market")
        self.assertEqual(cats, FORTE_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("fortemarket"), "Forte Market")
        for c in FORTE_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)

    def test_registry_contains_twelve_months(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, TWELVE_MONTHS_CATEGORIES
        self.assertIn("twelve_months", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["twelve_months"]
        self.assertEqual(name, "12 Месяцев")
        self.assertEqual(cats, TWELVE_MONTHS_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("twelve_months"), "12 Месяцев")
        for c in TWELVE_MONTHS_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_zeta(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, ZETA_CATEGORIES
        self.assertIn("zeta", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["zeta"]
        self.assertEqual(name, "Zeta")
        self.assertEqual(cats, ZETA_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("zeta"), "Zeta")
        for c in ZETA_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_komfort(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, KOMFORT_CATEGORIES
        self.assertIn("komfort", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["komfort"]
        self.assertEqual(name, "Комфорт")
        self.assertEqual(cats, KOMFORT_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("komfort"), "Комфорт")
        for c in KOMFORT_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_lemanapro(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, LEMANA_PRO_CATEGORIES
        self.assertIn("lemanapro", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["lemanapro"]
        self.assertEqual(name, "Лемана ПРО")
        self.assertEqual(cats, LEMANA_PRO_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("lemanapro"), "Лемана ПРО")
        for c in LEMANA_PRO_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_arbuz(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, ARBUZ_CATEGORIES
        self.assertIn("arbuz", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["arbuz"]
        self.assertEqual(name, "Arbuz")
        self.assertEqual(cats, ARBUZ_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("arbuz"), "Arbuz")
        for c in ARBUZ_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_masterok(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, MASTEROK_CATEGORIES
        self.assertIn("masterok", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["masterok"]
        self.assertEqual(name, "MasterOK")
        self.assertEqual(cats, MASTEROK_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("masterok"), "MasterOK")
        for c in MASTEROK_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_magnum(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, MAGNUM_CATEGORIES
        self.assertIn("magnum", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["magnum"]
        self.assertEqual(name, "Магнум")
        self.assertEqual(cats, MAGNUM_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("magnum"), "Магнум")
        for c in MAGNUM_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_registry_contains_intertop(self):
        from web.server import SHOP_REGISTRY
        from config import SHOP_KEYS, INTERTOP_CATEGORIES
        self.assertIn("intertop", SHOP_REGISTRY)
        scraper_cls, cats, name = SHOP_REGISTRY["intertop"]
        self.assertEqual(name, "Интертоп")
        self.assertEqual(cats, INTERTOP_CATEGORIES)
        self.assertEqual(SHOP_KEYS.get("intertop"), "Интертоп")
        for c in INTERTOP_CATEGORIES:
            self.assertIn("master", c)
            self.assertIn("url", c)
            self.assertTrue(c["master"] in config.MASTER_CATEGORIES)

    def test_parse_response_and_regional_pricing(self):
        from scrapers.fortemarket import ForteMarketScraper

        sample_data = {
            "nbHits": 1,
            "hits": [
                {
                    "objectID": "test-uuid-123",
                    "Name": "Смартфон Тестовый 256GB",
                    "Price": 500000,
                    "Picture": "https://img.test/pic.jpg",
                    "URL": "https://market.forte.kz/items/test-item",
                    "Locations": {
                        "Location": [
                            {"ID": "KZ", "Price": 510000},
                            {"ID": "KZ-AST", "Price": 490000},
                            {"ID": "KZ-ALA", "Price": 480000},
                        ]
                    },
                    "ParamMap": {
                        "NFC": "Есть",
                        "Объём_памяти": "256 ГБ",
                        "Мерчант": "secret_merchant_id",
                    }
                }
            ]
        }

        # 1. Астана
        res_ast = ForteMarketScraper.parse_response(sample_data, "Смартфоны", 1, city="Астана")
        self.assertEqual(len(res_ast), 1)
        item_ast = res_ast[0]
        self.assertEqual(item_ast["id"], "forte_test-uuid-123")
        self.assertEqual(item_ast["shop"], "Forte Market")
        self.assertEqual(item_ast["price"], 490000)
        self.assertEqual(item_ast["city"], "Астана")
        self.assertIn("• NFC: Есть", item_ast["description"])
        self.assertIn("• Объём памяти: 256 ГБ", item_ast["description"])
        self.assertNotIn("secret_merchant_id", item_ast["description"])

        # 2. Алматы
        res_ala = ForteMarketScraper.parse_response(sample_data, "Смартфоны", 1, city="Алматы")
        self.assertEqual(res_ala[0]["price"], 480000)
        self.assertEqual(res_ala[0]["city"], "Алматы")

        # 3. Неизвестный город -> fallback к KZ с честной меткой «Казахстан», а не запрошенного города
        res_other = ForteMarketScraper.parse_response(sample_data, "Смартфоны", 1, city="Кокшетау")
        self.assertEqual(res_other[0]["price"], 510000)
        self.assertEqual(res_other[0]["city"], "Казахстан")

    def test_search_live_mocked(self):
        import asyncio
        from unittest.mock import patch, MagicMock

        sample_resp = MagicMock()
        sample_resp.status_code = 201
        sample_resp.json.return_value = {
            "nbHits": 1,
            "hits": [
                {
                    "objectID": "live-hit-1",
                    "Name": "iPhone 15 128GB Black",
                    "Price": 380000,
                    "Picture": "https://img.test/iphone.jpg",
                    "URL": "https://market.forte.kz/items/iphone-15",
                }
            ]
        }

        with patch("curl_cffi.requests.Session.post", return_value=sample_resp):
            items = asyncio.run(self.scraper.search_live("iPhone 15", city="Астана"))
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["title"], "iPhone 15 128GB Black")
            self.assertEqual(items[0]["shop"], "Forte Market")
            self.assertEqual(items[0]["price"], 380000)



class TestStageOneSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        init_db()

    async def test_admin_only_global_dismiss_and_tracked_queries(self):
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from unittest.mock import patch
        from web.server import create_app
        from database import save_tracked_category
        product = dict(id="stage1-permissions", title="Test", price=100, url="https://example.invalid")
        save_or_update_product(product)
        alert_id = record_alert(product['id'], 'SUPER_DISCOUNT', 200, 100, 50, 100)
        save_tracked_category("Stage1", "private-test-query")
        app = create_app(); app.cleanup_ctx.clear()
        with patch('auth.ADMIN_TELEGRAM_IDS', {90001}), patch('auth.ALLOW_DEV_LOGIN', False):
            async with TestClient(TestServer(app)) as client:
                for uid, expected in [(None, 401), (90002, 403), (90001, 200)]:
                    client.session.cookie_jar.clear()
                    if uid:
                        upsert_telegram_user({'id': uid, 'first_name': 'Test'})
                        client.session.cookie_jar.update_cookies({'kzph_session': create_session(uid)})
                    tracked = await client.get('/api/categories/tracked')
                    self.assertEqual(tracked.status, expected)
                    if uid != 90001:
                        self.assertNotIn('private-test-query', await tracked.text())
                    else:
                        self.assertIn('private-test-query', await tracked.text())
                    for method, path, kwargs in [
                        ('post', '/api/alerts/dismiss', {'json': {'id': alert_id}}),
                        ('delete', f'/api/alerts/{alert_id}', {}),
                    ]:
                        with get_connection() as conn:
                            conn.execute('UPDATE alerts SET is_dismissed=0 WHERE id=?', (alert_id,));conn.commit()
                        response = await getattr(client, method)(path, **kwargs)
                        self.assertEqual(response.status, expected)
                        with get_connection() as conn:
                            self.assertEqual(conn.execute('SELECT is_dismissed FROM alerts WHERE id=?', (alert_id,)).fetchone()[0], int(uid == 90001))

    async def test_guest_empty_and_stale_search_never_calls_sources(self):
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from unittest.mock import patch, AsyncMock
        import web.server as server
        from auth import RateLimiter
        app = server.create_app(); app.cleanup_ctx.clear()
        stale = dict(id='stage1-stale', title='Test', shop='Test', city='Астана', url='https://example.invalid', current_price=100, updated_at='2000-01-01')
        with patch.object(server, 'search_limiter', RateLimiter(100, 60)), patch('search_engine.search_live_stores', new_callable=AsyncMock) as live:
            async with TestClient(TestServer(app)) as client:
                for rows in ([], [stale]):
                    with patch('search_engine.search_in_database', return_value=rows):
                        response = await client.get('/api/best-price?q=test&live=false&ai=0')
                        self.assertEqual(response.status, 200)
                response = await client.get('/api/best-price?q=test&live=true&ai=0')
                self.assertEqual(response.status, 401)
                live.assert_not_awaited()

    async def test_explicit_live_requires_user_and_obeys_user_limit(self):
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        from unittest.mock import patch, AsyncMock
        import web.server as server
        from auth import RateLimiter
        app = server.create_app();app.cleanup_ctx.clear()
        upsert_telegram_user({'id': 90003, 'first_name': 'Test'})
        with patch.object(server, 'live_search_limiter', RateLimiter(1, 600)), patch.object(server, 'search_limiter', RateLimiter(100, 60)), patch('search_engine.search_in_database', return_value=[]), patch('search_engine.search_live_stores', new_callable=AsyncMock) as live:
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({'kzph_session': create_session(90003)})
                self.assertEqual((await client.get('/api/best-price?q=test&live=1&ai=0')).status, 200)
                self.assertEqual((await client.get('/api/best-price?q=test2&live=1&ai=0')).status, 429)
                live.assert_awaited_once()

    async def test_disabled_live_sources_are_not_called_or_served_from_old_cache(self):
        from unittest.mock import patch, AsyncMock
        import search_engine as se
        from scrapers.fourmobile import FourMobileScraper
        from scrapers.fortemarket import ForteMarketScraper
        se._LIVE_CACHE.clear()
        product = dict(id='stage1-live', title='Test', price=100, shop='Kaspi Магазин', url='https://example.invalid')
        enabled = dict(kaspi=True, shopkz=False, fourmobile=False, fortemarket=False)
        with patch.object(se, 'load_settings', return_value={'enabled_shops': enabled}), patch.object(se.KaspiScraper, 'search', new_callable=AsyncMock, return_value=[product]) as kaspi, patch('curl_cffi.requests.get') as http, patch.object(FourMobileScraper, 'search_live', new_callable=AsyncMock) as mobile, patch.object(ForteMarketScraper, 'search_live', new_callable=AsyncMock) as forte:
            self.assertEqual(len(await se.search_live_stores('stage1-disabled')), 1)
            enabled['kaspi'] = False
            self.assertEqual(await se.search_live_stores('stage1-disabled'), [])
            kaspi.assert_awaited_once();http.assert_not_called();mobile.assert_not_awaited();forte.assert_not_awaited()
        se._LIVE_CACHE.clear()


    async def test_telegram_errors_do_not_expose_transport_or_provider_body(self):
        from unittest.mock import patch, Mock
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        import web.server as server
        app = server.create_app(); app.cleanup_ctx.clear()
        upsert_telegram_user({'id': 90004, 'first_name': 'Test'})
        secret = 'synthetic-private-token'
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'kzph_session': create_session(90004)})
            with patch.object(server, 'telegram_api', side_effect=RuntimeError('https://api.telegram.org/bot' + secret)):
                response = await client.post('/api/me/test-telegram')
                self.assertEqual(response.status, 500)
                self.assertNotIn(secret, await response.text())
            with patch.object(server, 'telegram_api', return_value=Mock(status_code=400, text=secret)):
                response = await client.post('/api/me/test-telegram')
                self.assertEqual(response.status, 400)
                self.assertNotIn(secret, await response.text())



class TestTrackedCategoriesEqualFunctionality(unittest.IsolatedAsyncioTestCase):
    """Тесты равноправия функционала отслеживаемых категорий из поиска с мастер-группами."""

    def setUp(self):
        init_db()
        import auth
        import config
        auth.ADMIN_TELEGRAM_IDS = {1}
        config.ADMIN_TELEGRAM_IDS = {1}
        with get_connection() as conn:
            conn.execute("DELETE FROM scheduler_lease")

    async def test_tracked_category_hot_and_due_rotation(self):
        from database import (
            save_tracked_category,
            toggle_tracked_category_hot,
            get_tracked_categories,
            get_due_tracked_categories,
            delete_tracked_category,
        )

        cat1 = save_tracked_category("Тестовый чайный сервиз", "чайный сервиз")
        cat2 = save_tracked_category("Тестовые подгузники", "подгузники")
        cid1, cid2 = cat1["id"], cat2["id"]

        try:
            # 1. Помечаем cat1 как Hot
            toggle_tracked_category_hot(cid1, True)

            cats = get_tracked_categories(active_only=True)
            t1 = next(c for c in cats if c["id"] == cid1)
            t2 = next(c for c in cats if c["id"] == cid2)
            self.assertEqual(t1["is_hot"], 1)
            self.assertEqual(t2["is_hot"], 0)
            self.assertIn("products_count", t1)

            # 2. get_due_tracked_categories должен отдавать Hot-категорию первой
            due = get_due_tracked_categories(limit=5)
            due_ids = [c["id"] for c in due]
            self.assertIn(cid1, due_ids)
            self.assertEqual(due[0]["id"], cid1)
        finally:
            delete_tracked_category(cid1)
            delete_tracked_category(cid2)

    async def test_scan_category_endpoint_with_tracked_and_create_query(self):
        from unittest.mock import patch, AsyncMock
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        import web.server as server
        from database import save_tracked_category, delete_tracked_category

        app = server.create_app(); app.cleanup_ctx.clear()
        upsert_telegram_user({'id': 1, 'first_name': 'Admin'})
        token = create_session(1)

        cat = save_tracked_category("Тестовая посуда", "посуда")
        cid = cat["id"]

        fake_products = [
            {"id": "test_1", "shop": "Kaspi Магазин", "title": "Чайный сервиз фарфор", "price": 15000, "city": "Астана"}
        ]

        try:
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({"kzph_session": token})

                with patch("search_engine.search_live_stores", new_callable=AsyncMock, return_value=fake_products):
                    # 1. Запуск сбора по существующей tracked категории
                    resp1 = await client.post("/api/scan/category", json={"category": f"tracked:{cid}"})
                    self.assertEqual(resp1.status, 200)
                    data1 = await resp1.json()
                    self.assertEqual(data1["status"], "completed")
                    self.assertEqual(data1["items_found"], 1)

                    # 2. Запуск сбора с мгновенным созданием новой категории по запросу
                    resp2 = await client.post("/api/scan/category", json={
                        "category": "create_query:автомасла",
                        "query": "автомасла",
                        "name": "Автомасла"
                    })
                    self.assertEqual(resp2.status, 200)
                    data2 = await resp2.json()
                    self.assertEqual(data2["status"], "completed")
                    self.assertEqual(data2["category_name"], "Автомасла")
                    self.assertEqual(data2["items_found"], 1)
        finally:
            delete_tracked_category(cid)
            from database import get_connection
            with get_connection() as conn:
                conn.execute("DELETE FROM tracked_categories WHERE name = 'Автомасла'")
                conn.commit()


    async def test_strict_wave_cycle_and_24h_budget(self):
        """Проверка строгой очередности волн, завершения полного круга, 24-часового лимита и Hot-исключения."""
        import config
        from database import (
            save_tracked_category,
            toggle_tracked_category_hot,
            mark_tracked_category_scanned,
            delete_tracked_category,
            get_due_tracked_categories,
            get_metadata,
            set_metadata,
            get_connection
        )
        import web.server as server

        # 1. Проверка математики 24-часового лимита для разных размеров волн
        for wave_size in [1, 2, 3, 4, 6]:
            plan = config.get_wave_plan(wave_size=wave_size, wave_mode="rolling")
            total_waves = plan["total_waves"]
            interval_sec = config.get_wave_interval_seconds(settings={"scan_interval_minutes": 500}, total_waves=total_waves)
            # Суммарное время всех волн круга не должно превышать 24 часа (86400 сек)
            total_cycle_sec = total_waves * interval_sec
            self.assertLessEqual(total_cycle_sec, 24 * 3600, f"Круг из {total_waves} волн превысил 24 часа: {total_cycle_sec}с")

        # 2. Проверка строгой поочередной ротации без повторов до завершения круга
        hot_cats = ["smartphones", "laptops"]
        plan0 = config.get_wave_plan(hot_categories=hot_cats, wave_index=0, wave_size=2, wave_mode="rolling")
        total_waves = plan0["total_waves"]

        seen_rotating = []
        for w in range(total_waves):
            p = config.get_wave_plan(hot_categories=hot_cats, wave_index=w, wave_size=2, wave_mode="rolling")
            # Hot-категории обязаны быть в каждой волне
            self.assertEqual(p["hot_categories"], hot_cats)
            for h in hot_cats:
                self.assertIn(h, p["active_categories"])
            # Внутри одного круга ни одна не-Hot категория не должна повторяться
            for c in p["wave_categories"]:
                self.assertNotIn(c, seen_rotating, f"Категория {c} повторилась до окончания круга!")
                seen_rotating.append(c)

        # Все не-Hot категории должны быть обойдены ровно 1 раз
        all_expected_rotating = [k for k in config.MASTER_CATEGORIES if k not in hot_cats]
        self.assertEqual(sorted(seen_rotating), sorted(all_expected_rotating))

        # На шаге total_waves начинается новый круг (wave_index сбрасывается в 0)
        p_next_cycle = config.get_wave_plan(hot_categories=hot_cats, wave_index=total_waves, wave_size=2, wave_mode="rolling")
        self.assertEqual(p_next_cycle["wave_index"], 0)
        self.assertEqual(p_next_cycle["wave_categories"], plan0["wave_categories"])

        # 3. Проверка отслеживаемых поисковых категорий: Hot каждую волну, остальные по очереди
        with get_connection() as conn:
            conn.execute("DELETE FROM tracked_categories")
            conn.commit()

        t_hot = save_tracked_category("Тест Hot Запрос", "запрос_hot", "smartphones")
        t_rot1 = save_tracked_category("Тест Ротация 1", "запрос_rot1", "audio")
        t_rot2 = save_tracked_category("Тест Ротация 2", "запрос_rot2", "audio")
        t_rot3 = save_tracked_category("Тест Ротация 3", "запрос_rot3", "tvs")
        t_rot4 = save_tracked_category("Тест Ротация 4", "запрос_rot4", "tvs")
        toggle_tracked_category_hot(t_hot["id"], True)

        try:
            # Волна 1: берем Hot + порцию из 2 ротируемых
            wave1_due = get_due_tracked_categories(limit=2, include_all_hot=True)
            wave1_ids = [c["id"] for c in wave1_due]
            self.assertIn(t_hot["id"], wave1_ids, "Hot-категория должна быть включена в волну")
            # Фиксируем сканирование категорий волны 1
            for cid in wave1_ids:
                mark_tracked_category_scanned(cid)

            # Волна 2: Hot снова присутствует, а ротируемые берутся следующие из очереди!
            wave2_due = get_due_tracked_categories(limit=2, include_all_hot=True)
            wave2_ids = [c["id"] for c in wave2_due]
            self.assertIn(t_hot["id"], wave2_ids, "Hot-категория обязана быть и во второй волне!")
            # Ни одна ротируемая категория волны 1 не должна попасть в волну 2, пока очередь не исчерпана
            hot_ids = {c["id"] for c in wave1_due if c.get("is_hot")}
            for cid in wave1_ids:
                if cid not in hot_ids:
                    self.assertNotIn(cid, wave2_ids, "Не-Hot категория повторилась раньше завершения круга!")
        finally:
            for item in [t_hot, t_rot1, t_rot2, t_rot3, t_rot4]:
                if item and item.get("id"):
                    delete_tracked_category(item["id"])

        # 4. Проверка инкремента номера круга (wave_cycle) в _do_scan_task
        with get_connection() as conn:
            conn.execute("DELETE FROM scheduler_lease")
            conn.commit()

        set_metadata("wave_index", str(total_waves - 1))  # устанавливаем последнюю волну круга
        set_metadata("wave_cycle", "1")
        server.wave_state["current_wave_index"] = total_waves - 1
        server.wave_state["current_cycle"] = 1

        from unittest.mock import patch, MagicMock, AsyncMock
        dummy_scraper_cls = MagicMock()
        dummy_instance = dummy_scraper_cls.return_value
        from scrapers.base import ScanResult
        item = {"id": "wave_1", "shop": "ТестШоп", "title": "Смартфон Тест X1", "url": "https://test.kz/p/1",
                "price": 100000, "city": "Астана", "category": "Phones"}
        fake_registry = {"test_shop": (dummy_scraper_cls, [{"name": "Phones", "url": "https://test.kz", "master": "smartphones"}], "ТестШоп")}
        settings = {**config.SYSTEM_DEFAULTS, "enabled_categories": config._all_categories_enabled(), "hot_categories": hot_cats, "wave_size": 2, "wave_mode": "rolling", "scan_interval_minutes": 180}

        # 4а. Последняя волна с упавшим магазином (пустая выдача без подтверждения) — круг не засчитывается (R-M04)
        dummy_instance.scrape = AsyncMock(return_value=[])
        with patch.dict(server.SHOP_REGISTRY, fake_registry, clear=True), \
             patch.object(server, "load_settings", return_value=settings), \
             patch.object(server, "queue_titles_for_ai"):
            await server._do_scan_task(["test_shop"], scan_type="auto")
        self.assertEqual(int(get_metadata("wave_index")), 0)   # ротация всё равно продвигается
        self.assertEqual(int(get_metadata("wave_cycle")), 1)   # но круг не объявлен завершённым

        # 4б. Успешная последняя волна — круг засчитан
        with get_connection() as conn:
            conn.execute("DELETE FROM scheduler_lease")
            conn.commit()

        set_metadata("wave_index", str(total_waves - 1))
        server.wave_state["current_wave_index"] = total_waves - 1
        dummy_instance.scrape = AsyncMock(return_value=ScanResult([dict(item)], complete=True))
        with patch.dict(server.SHOP_REGISTRY, fake_registry, clear=True), \
             patch.object(server, "load_settings", return_value=settings), \
             patch.object(server, "queue_titles_for_ai"):
            await server._do_scan_task(["test_shop"], scan_type="auto")

        # После успешной последней волны: wave_index сбрасывается в 0, а wave_cycle увеличивается до 2
        self.assertEqual(int(get_metadata("wave_index")), 0)
        self.assertEqual(int(get_metadata("wave_cycle")), 2)
        self.assertEqual(server.wave_state["current_cycle"], 2)


if __name__ == "__main__":
    unittest.main()

