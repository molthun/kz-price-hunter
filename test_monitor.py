import unittest
import json
import os
import tempfile

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
        from database import invalidate_alerts_cache
        with get_connection() as conn:
            for table in ('notification_outbox', 'product_sources', 'alerts', 'products', 'shop_scans', 'users', 'sessions'):
                conn.execute(f'DELETE FROM {table}')
        invalidate_alerts_cache()

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

        # Page 1 has 3 items (< 24), so complete is True
        self.assertTrue(res.complete)

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
        from aiohttp.test_utils import TestClient, TestServer
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
        from aiohttp.test_utils import TestClient, TestServer
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
            with patch.object(ai_service, 'parse_natural_query', return_value=mock_ai_meta):
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
             patch.object(ai_service, 'call_gemini_api', new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = mock_ai_response
            res = await ai_service.ask_ai_consultant(message="Посоветуй айфон 15", city="Все")
            self.assertIn("iPhone 15", res["answer"])
            self.assertEqual(len(res["suggested_questions"]), 2)
            self.assertTrue(isinstance(res["products"], list))

    async def test_ai_consultant_endpoint(self):
        """Тест HTTP эндпоинта /api/ai/consultant."""
        from unittest.mock import patch, AsyncMock
        from aiohttp.test_utils import TestClient, TestServer
        from web.server import create_app
        import ai_service

        app = create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
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
                        "chat": {"id": 12345},
                        "from": {"first_name": "Тестер"},
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
                        "chat": {"id": 12345},
                        "from": {"first_name": "Тестер"},
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
                        "chat": {"id": 12345},
                        "from": {"first_name": "Тестер"},
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
                        "chat": {"id": 12345},
                        "from": {"first_name": "Тестер"},
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
        self.assertEqual(k_s1, "samsung:galaxy s24 ultra:1024gb")
        self.assertEqual(extract_canonical_key(s2), k_s1)
        self.assertEqual(extract_canonical_key(s3), k_s1)

        # 3. Видеокарта RTX 4060
        g1 = "Видеокарта Palit GeForce RTX 4060 Dual 8GB"
        g2 = "Palit RTX 4060 Dual 8 ГБ (NE64060019P1-1070D)"
        k_g1 = extract_canonical_key(g1)
        self.assertEqual(k_g1, "palit:rtx 4060:8gb")
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
             patch.object(ai_service, 'call_gemini_api', new_callable=AsyncMock) as mock_gemini:
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
        from aiohttp.test_utils import TestClient, TestServer
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


if __name__ == "__main__":
    unittest.main()
