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


if __name__ == "__main__":
    unittest.main()
