"""Этап 3 аудита: достоверность цен и сбора. Без сети и без рабочей БД."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import datetime
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
os.environ.setdefault("ALLOW_DEV_LOGIN", "0")

from scrapers import http  # noqa: E402
from scrapers.base import parse_price, price_value  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class LimiterTest(unittest.TestCase):
    def setUp(self):
        http.reset_limiter()
        self.clock = FakeClock()
        self.patches = [patch.object(http, "_clock", self.clock.monotonic),
                        patch.object(http, "_sleep", self.clock.sleep),
                        patch.object(http, "_random", lambda a, b: 0.0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        http.reset_limiter()

    def test_default_limits_are_minimal(self):
        # Решение владельца: без пауз лимитера, потолок 4 запроса на домен, пауза только по 429
        self.assertEqual((http.MAX_CONCURRENCY_PER_HOST, http.MIN_INTERVAL_SECONDS, http.JITTER_SECONDS), (4, 0.0, 0.0))
        ok = Mock(status_code=200, headers={})
        for _ in range(3):
            http._limited("GET", "https://fast.example/", lambda: ok)
        self.assertEqual(self.clock.sleeps, [])

    @patch.object(http, "MIN_INTERVAL_SECONDS", 0.5)
    def test_min_interval_between_requests_to_same_host(self):
        ok = Mock(status_code=200, headers={})
        for _ in range(3):
            http._limited("GET", "https://shop.example/a", lambda: ok)
        self.assertEqual(self.clock.sleeps, [http.MIN_INTERVAL_SECONDS, http.MIN_INTERVAL_SECONDS])
        # Другой домен не ждёт очереди первого
        self.clock.sleeps.clear()
        http._limited("GET", "https://other.example/", lambda: ok)
        self.assertEqual(self.clock.sleeps, [])

    def test_retry_after_short_waits_long_fails_fast(self):
        http._limited("GET", "https://shop.example/", lambda: Mock(status_code=429, headers={"Retry-After": "10"}))
        self.clock.sleeps.clear()
        http._limited("GET", "https://shop.example/", lambda: Mock(status_code=200, headers={}))
        self.assertAlmostEqual(self.clock.sleeps[0], 10.0)

        http._limited("GET", "https://shop.example/", lambda: Mock(status_code=429, headers={"Retry-After": "600"}))
        send = Mock()
        with self.assertRaises(http.HostCooldown):
            http._limited("GET", "https://shop.example/x", send)
        send.assert_not_called()

    def test_429_without_header_uses_default_and_503_without_header_is_ignored(self):
        http._limited("GET", "https://a.example/", lambda: Mock(status_code=503, headers={}))
        http._limited("GET", "https://a.example/", lambda: Mock(status_code=200, headers={}))
        http._limited("GET", "https://b.example/", lambda: Mock(status_code=429, headers={}))
        with self.assertRaises(http.HostCooldown) as ctx:
            http._limited("GET", "https://b.example/", Mock())
        self.assertGreater(ctx.exception.retry_after, http.MAX_COOLDOWN_WAIT_SECONDS)

    def test_retry_after_formats(self):
        now = datetime.datetime(2026, 9, 18, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(http.parse_retry_after("120", now), 120.0)
        self.assertEqual(http.parse_retry_after("Fri, 18 Sep 2026 12:01:00 GMT", now), 60.0)
        self.assertEqual(http.parse_retry_after("Fri, 18 Sep 2026 11:00:00 GMT", now), 0.0)
        for bad in (None, "", "soon", "-5", "1.5"):
            self.assertIsNone(http.parse_retry_after(bad, now), bad)

    def test_slot_released_after_transport_error(self):
        def boom():
            raise ConnectionError("simulated")
        for _ in range(http.MAX_CONCURRENCY_PER_HOST + 1):
            with self.assertRaises(ConnectionError):
                http._limited("GET", "https://shop.example/", boom)
        http._limited("GET", "https://shop.example/", lambda: Mock(status_code=200, headers={}))

    def test_concurrency_cap_per_host(self):
        http.reset_limiter()
        active, peak, lock = [0], [0], threading.Lock()
        gate = threading.Event()

        def send():
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            gate.wait(0.2)
            with lock:
                active[0] -= 1
            return Mock(status_code=200, headers={})

        with patch.object(http, "_sleep", lambda s: None):
            threads = [threading.Thread(target=http._limited, args=("GET", "https://c.example/", send)) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertLessEqual(peak[0], http.MAX_CONCURRENCY_PER_HOST)

    def test_adapters_use_shared_transport(self):
        import scrapers.kaspi, scrapers.halyk, scrapers.moon, scrapers.ispace, scrapers.tgrad
        for module in (scrapers.kaspi, scrapers.halyk, scrapers.moon, scrapers.ispace, scrapers.tgrad):
            self.assertIs(module.requests, http, module.__name__)
        self.assertTrue(issubclass(scrapers.halyk.requests.Session, http._curl.Session))


class TlsTest(unittest.TestCase):
    def test_no_adapter_disables_tls_verification(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent
        for path in list((root / "scrapers").glob("*.py")) + [root / "search_engine.py", root / "product_details.py"]:
            self.assertNotIn("verify=False", path.read_text(), path.name)

    def test_moon_bundle_has_certifi_and_intermediate(self):
        import certifi
        from pathlib import Path
        from scrapers.moon import MOON_INTERMEDIATE_CA
        bundle = Path(http.ca_bundle_with(MOON_INTERMEDIATE_CA)).read_text()
        self.assertIn(Path(certifi.where()).read_text()[:500], bundle)
        self.assertIn("MIIGHjCCBAagAwIBAgIRAKmWRHPh0AHswhf4RLEmCdIwDQYJKoZIhvcNAQEMBQAw", bundle)


class PriceParsingTest(unittest.TestCase):
    def test_parse_price_contract(self):
        cases = {
            "179 990 ₸ 200 650 ₸": 179990, "72 228.00": 72228, "72228,0 ₸": 72228,
            "-20 000 ₸ 150 000 ₸": 150000, "−15%": 0, "15% 99 990": 99990,
            "5 990 ₸/мес": 0, "от 199 990 ₸ или 16 666 ₸/мес": 199990, "9 990 тг в мес": 0,
            "$1 200": 0, "1 200 USD": 0, "Нет в наличии": 0, "": 0, None: 0,
            "20 000 000": 0, "Цена: 45 000 тг": 45000,
            # Flip: узкий неразрывный пробел U+202F между разрядами
            "1\u202f080\u202f₸": 1080, "1\u202f310\u202f₸ 1\u202f872\u202f₸ 4.8 12": 1310, "12\u2009990 ₸": 12990,
        }
        for raw, expected in cases.items():
            self.assertEqual(parse_price(raw), expected, raw)

    def test_price_value_from_json_numbers(self):
        cases = [(199990, 199990), (199990.0, 199990), (199990.99, 199990), ("199990.50", 199990),
                 ("199 990", 199990), ("199\u202f990", 199990), ("199990,5", 199990), (0, 0), (-5, 0), ("-5", 0),
                 (float("inf"), 0), (float("nan"), 0), ("abc", 0), (True, 0), (None, 0), (10_000_001, 0)]
        for raw, expected in cases:
            self.assertEqual(price_value(raw), expected, repr(raw))

    def test_technodom_float_price_is_not_multiplied(self):
        from scrapers.technodom import TechnodomScraper
        self.assertEqual(TechnodomScraper._price(199990.0), 199990)

    def test_kaspi_and_halyk_decimal_strings_do_not_crash(self):
        from scrapers.halyk import HalykScraper
        res = HalykScraper.parse_response({"products_total": 1, "products": [
            {"id": "1", "name": "Товар", "price": "150000.50", "oldprice": "180000.00", "url": "/x"}]}, "C", 1)
        self.assertEqual((res[0]["price"], res[0]["old_price_on_site"]), (150000, 180000))


class CompletenessTest(unittest.TestCase):
    def _halyk(self, **data):
        from scrapers.halyk import HalykScraper
        return HalykScraper.parse_response(data, "C", 1)

    def test_halyk_requires_total(self):
        full = [{"id": str(i), "name": f"P{i}", "price": 1000} for i in range(24)]
        self.assertFalse(self._halyk(products=full).complete)
        self.assertFalse(self._halyk(products=full[:3]).complete)
        self.assertFalse(self._halyk(products=full, products_total="24").complete)
        self.assertTrue(self._halyk(products=full, products_total=24).complete)
        with self.assertRaises(ValueError):
            self._halyk(items=full)

    def test_forte_requires_nbhits(self):
        from scrapers.fortemarket import ForteMarketScraper
        hits = [{"objectID": str(i), "Name": f"P{i}", "Price": 1000} for i in range(3)]
        self.assertFalse(ForteMarketScraper.parse_response({"hits": hits}, "C", 1).complete)
        self.assertTrue(ForteMarketScraper.parse_response({"hits": hits, "nbHits": 3}, "C", 1).complete)
        with self.assertRaises(ValueError):
            ForteMarketScraper.parse_response({"message": "blocked"}, "C", 1)

    def test_halyk_page_after_missing_total_is_partial_not_complete(self):
        from scrapers.halyk import HalykScraper
        scraper = HalykScraper()
        pages = [{"products": [{"id": str(i), "name": f"P{i}", "price": 1000} for i in range(24)]}, {"products": []}]
        with patch.object(scraper, "resolve_category_id", return_value="1"), \
             patch.object(scraper, "_get_session") as session, patch("scrapers.base.time.sleep"):
            session.return_value.get.side_effect = [Mock(status_code=200, json=Mock(return_value=p)) for p in pages]
            result = scraper._scrape_sync("C", "https://halykmarket.kz/category/x")
        self.assertEqual(len(result), 24)
        self.assertFalse(result.complete)
        self.assertTrue(result.error)

    def test_tgrad_redirect_must_point_to_first_page(self):
        from scrapers.tgrad import TgradScraper
        scraper = TgradScraper()
        category = "https://tgrad.kz/smartfony/"
        session = Mock()
        with patch.object(scraper, "_get_session", return_value=session):
            for location in ("/smartfony/", "https://tgrad.kz:443/smartfony/", "https://TGRAD.kz/smartfony"):
                session.get.return_value = Mock(status_code=302, headers={"Location": location})
                self.assertTrue(scraper._fetch_page("C", category, 3).complete, location)
            session.get.return_value = Mock(status_code=302, headers={"Location": "http://tgrad.kz/smartfony/"})
            with self.assertRaises(RuntimeError):
                scraper._fetch_page("C", category, 3)
            session.get.return_value = Mock(status_code=302, headers={"Location": "https://tgrad.kz/auth/"})
            with self.assertRaises(RuntimeError):
                scraper._fetch_page("C", category, 3)
            session.get.return_value = Mock(status_code=302, headers={})
            with self.assertRaises(RuntimeError):
                scraper._fetch_page("C", category, 3)


def _ispace_listing(links, last_page=None, path="/category/ipad"):
    cards = "".join(f'<div class="entity-card"><a class="entity-card_name" href="/product/{l}">x</a></div>' for l in links)
    pager = f'<a href="{path}?page=2">2</a><a href="{path}?page={last_page}">{last_page}</a>' if last_page else ""
    return f"<html>{cards}{pager}</html>"


def _ispace_card(sku, stock="InStock"):
    import json
    ld = {"@type": "Product", "name": f"iPad {sku}", "sku": sku,
          "offers": {"price": "300000", "availability": f"https://schema.org/{stock}"}}
    return '<script type="application/ld+json">%s</script>' % json.dumps(ld)


class ISpaceTest(unittest.TestCase):
    def _run(self, pages, cards):
        from scrapers.ispace import ISpaceScraper
        scraper = ISpaceScraper()

        def get(url):
            if "/product/" in url:
                return Mock(status_code=200, text=cards[url.rsplit("/", 1)[-1]])
            page = int(url.split("page=")[1]) if "page=" in url else 1
            return Mock(status_code=200, text=pages[page - 1] if page <= len(pages) else "<html></html>")

        with patch.object(scraper, "_get", side_effect=get), patch("scrapers.ispace.time.sleep"):
            return scraper._scrape_sync("iSpace: iPad", "https://ispace.kz/category/ipad")

    def test_complete_when_last_page_from_pagination_reached(self):
        result = self._run([_ispace_listing(["a"], 2), _ispace_listing(["b"], 2)],
                           {"a": _ispace_card("A1"), "b": _ispace_card("B1", "OutOfStock")})
        self.assertTrue(result.complete)
        self.assertEqual([p["id"] for p in result], ["ispace_A1"])

    def test_empty_page_before_last_page_is_not_complete(self):
        result = self._run([_ispace_listing(["a"], 5)], {"a": _ispace_card("A1")})
        self.assertFalse(result.complete)

    def test_card_without_product_data_is_error_not_out_of_stock(self):
        result = self._run([_ispace_listing(["a", "b"])], {"a": _ispace_card("A1"), "b": "<html>blocked</html>"})
        self.assertFalse(result.complete)
        self.assertIn("1 из 2", result.error)


class OldPriceStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from database import init_db
        init_db()

    def _stored(self, pid):
        from database import get_connection
        with get_connection() as conn:
            return conn.execute("SELECT current_price, old_price_on_site FROM products WHERE id=?", (pid,)).fetchone()

    def _product(self, pid, price, old):
        return {"id": pid, "shop": "Тест", "title": "Телевизор Samsung QE55", "url": "https://shop.kz/x",
                "price": price, "old_price_on_site": old}

    def test_single_writer_roundtrip_and_reset(self):
        from database import save_or_update_product
        save_or_update_product(self._product("h12_single", 100000, 150000))
        self.assertEqual(tuple(self._stored("h12_single")), (100000, 150000))
        save_or_update_product(self._product("h12_single", 100000, 0))  # магазин снял зачёркнутую цену
        self.assertEqual(tuple(self._stored("h12_single")), (100000, 0))
        save_or_update_product(self._product("h12_single", 120000, 110000))  # «старая» ниже текущей
        self.assertEqual(tuple(self._stored("h12_single")), (120000, 0))

    def test_batch_writer_roundtrip(self):
        from database import save_or_update_products_batch
        save_or_update_products_batch([self._product("h12_batch", 100000, "150000.00")])
        self.assertEqual(tuple(self._stored("h12_batch")), (100000, 150000))
        save_or_update_products_batch([self._product("h12_batch", 90000, None)])
        self.assertEqual(tuple(self._stored("h12_batch")), (90000, 0))

    def test_detector_names_basis(self):
        from detector import check_anomaly
        settings = {"junk_keywords": [], "exclude_used_goods": True, "min_item_price_kzt": 30000,
                    "max_item_price_kzt": 3000000, "detect_zero_glitch": True, "detect_super_discount": True,
                    "price_glitch_drop_pct": 50, "min_savings_kzt": 40000}
        site = check_anomaly({"title": "Телевизор", "price": 100000, "old_price_on_site": 300000},
                             {"old_price": 100000, "first_seen_price": 100000}, custom_settings=settings)
        self.assertEqual(site["basis"], "зачёркнутой цены на сайте")
        self.assertIn("зачёркнутой цены", site["reason"])
        history = check_anomaly({"title": "Телевизор", "price": 30000},
                                {"old_price": 300000, "first_seen_price": 300000}, custom_settings=settings)
        self.assertEqual(history["type"], "ZERO_GLITCH")
        self.assertEqual(history["basis"], "прошлой цены в базе")
        self.assertIn("ВОЗМОЖНАЯ", history["emoji"])


class FakeStream:
    def __init__(self, status=200, headers=None, chunks=(b"<html></html>",)):
        self.status_code = status
        self.headers = {"Content-Type": "text/html"} if headers is None else headers
        self.encoding = "utf-8"
        self._chunks = chunks
        self.closed = False

    def iter_content(self):
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeSession:
    """Подмена http.Session: отдаёт заранее заданные ответы."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.closed = False

    def __call__(self, **kwargs):
        self.options = kwargs.get("curl_options")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def get(self, url, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


class ProductDetailsTest(unittest.TestCase):
    def setUp(self):
        import product_details
        self.pd = product_details
        product_details._negative.clear()
        self.public = lambda host, port: None

    def test_url_policy(self):
        for bad in ("http://127.0.0.1/", "https://evil.example/", "https://kaspi.kz.evil.com/",
                    "https://user:p@kaspi.kz/", "https://kaspi.kz:8443/", "file:///etc/passwd", "", None):
            with self.assertRaises(self.pd.UnsafeUrl, msg=bad):
                self.pd.check_url(bad, self.public)
        self.assertEqual(self.pd.check_url("https://www.sulpak.kz/g/x", self.public), "https://www.sulpak.kz/g/x")

    def test_private_resolution_is_blocked(self):
        with patch("product_details.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]):
            with self.assertRaises(self.pd.UnsafeUrl):
                self.pd.check_url("https://kaspi.kz/shop/p/x")
        with patch("product_details.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            self.pd.check_url("https://kaspi.kz/shop/p/x")

    def test_redirect_to_foreign_domain_is_not_followed(self):
        redirect = FakeStream(302, {"Location": "http://169.254.169.254/latest/"})
        session = FakeSession([redirect])
        with patch.object(self.pd.http, "Session", session):
            with self.assertRaises(self.pd.UnsafeUrl):
                self.pd.fetch_html("https://kaspi.kz/shop/p/x", self.public)
        self.assertEqual(session.calls, 1)
        self.assertTrue(redirect.closed)
        self.assertTrue(session.closed)

    def test_redirect_within_store_and_size_limit(self):
        ok = FakeStream(chunks=(b"<html>ok</html>",))
        with patch.object(self.pd.http, "Session", FakeSession([FakeStream(301, {"Location": "/shop/p/y"}), ok])):
            self.assertEqual(self.pd.fetch_html("https://kaspi.kz/shop/p/x", self.public), "<html>ok</html>")
        huge = FakeStream(chunks=(b"x" * (self.pd.MAX_RESPONSE_BYTES // 2 + 1),) * 3)
        with patch.object(self.pd.http, "Session", FakeSession([huge])):
            self.assertIsNone(self.pd.fetch_html("https://kaspi.kz/shop/p/x", self.public))
        self.assertTrue(huge.closed)
        pdf = FakeStream(headers={"Content-Type": "application/pdf"})
        with patch.object(self.pd.http, "Session", FakeSession([pdf])):
            self.assertIsNone(self.pd.fetch_html("https://kaspi.kz/shop/p/x", self.public))
        loop = [FakeStream(302, {"Location": "/shop/p/x"}) for _ in range(self.pd.MAX_REDIRECTS + 1)]
        with patch.object(self.pd.http, "Session", FakeSession(loop)):
            self.assertIsNone(self.pd.fetch_html("https://kaspi.kz/shop/p/x", self.public))

    def test_kaspi_specs_are_returned(self):
        html = ('<ul class="specifications-list"><li class="specifications-list__spec">'
                '<span class="specifications-list__spec-term">Диагональ</span>'
                '<span class="specifications-list__spec-definition">55"</span></li></ul>')
        self.assertEqual(self.pd.extract_description(html, "https://kaspi.kz/shop/p/x", "Kaspi Магазин"), '• Диагональ: 55"')

    def test_forte_uses_matching_object_only(self):
        hits = {"hits": [{"objectID": "999", "Params": {"Цвет": "Белый"}}]}
        with patch.object(self.pd.http, "post", return_value=Mock(status_code=200, json=Mock(return_value=hits))):
            self.assertEqual(self.pd.forte_description({"id": "forte_123", "title": "Товар"}), "")

    def test_negative_cache_and_single_flight(self):
        calls = []

        def slow(prod):
            calls.append(prod["id"])
            import time
            time.sleep(0.05)
            return ""

        async def scenario():
            with patch.object(self.pd, "fetch_description_sync", side_effect=slow):
                prod = {"id": "p1", "url": "https://kaspi.kz/shop/p/x"}
                first = await asyncio.gather(*(self.pd.get_description(prod) for _ in range(5)))
                again = await self.pd.get_description(prod)
                return first, again

        first, again = asyncio.run(scenario())
        self.assertEqual(first, [""] * 5)
        self.assertEqual(again, "")
        self.assertEqual(calls, ["p1"])


if __name__ == "__main__":
    unittest.main()


class CoverageConfigTest(unittest.TestCase):
    """Полнота каталога: Forte по 100 товаров до конца категории, DNS — одна страница (дальше Cloudflare)."""

    def test_forte_categories_and_page_size(self):
        import config
        from scrapers.fortemarket import ForteMarketScraper, PAGE_SIZE
        self.assertEqual(PAGE_SIZE, 100)
        urls = " ".join(c["url"] for c in config.FORTE_CATEGORIES)
        # Эти фасеты раньше были неверными и давали 0 товаров
        for facet in ("CategoryMap.Lvl2:Ноутбуки и ультрабуки", "CategoryMap.Lvl3:Смарт-часы и браслеты",
                      "CategoryMap.Lvl3:Игровые консоли"):
            self.assertIn(facet, urls)
        smartphones = next(c for c in config.FORTE_CATEGORIES if "Смартфоны" in c["name"])
        self.assertGreaterEqual(smartphones["max_pages"] * PAGE_SIZE, 10_000)
        hits = [{"objectID": str(i), "Name": f"P{i}", "Price": 1000} for i in range(50)]
        self.assertFalse(ForteMarketScraper.parse_response({"hits": hits, "nbHits": 250}, "C", 2).complete)
        self.assertTrue(ForteMarketScraper.parse_response({"hits": hits, "nbHits": 250}, "C", 3).complete)

    def test_dns_requests_only_first_page(self):
        import config
        self.assertTrue(config.DNS_CATEGORIES)
        self.assertTrue(all(c["max_pages"] == 1 for c in config.DNS_CATEGORIES))


class DnsCloudflareTest(unittest.IsolatedAsyncioTestCase):
    async def test_cloudflare_challenge_is_reported_not_bypassed(self):
        from unittest.mock import AsyncMock, MagicMock
        from scrapers import dns
        page = MagicMock()
        page.goto = AsyncMock(return_value=MagicMock(status=403))
        page.title = AsyncMock(return_value="Один момент…")
        context = MagicMock()
        context.add_cookies = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=playwright)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(dns, "async_playwright", return_value=manager):
            result = await dns.DNSScraper().scrape("c", "https://www.dns-shop.kz/catalog/x/", 1)
        self.assertEqual(list(result), [])
        self.assertIn("Cloudflare", result.error)
        self.assertEqual(page.goto.await_count, 1)  # ни повторов, ни обходных запросов
        browser.close.assert_awaited_once()


class ArbitrageBenchmarkTest(unittest.TestCase):
    """R-L02: явно переданный ориентир рынка не роняет проверку; запятые текста не теряются."""

    SETTINGS = {"detect_market_arbitrage": True, "junk_keywords": [], "exclude_used_goods": True,
                "arbitrage_min_drop_pct": 25, "arbitrage_min_diff_kzt": 25000}

    def test_explicit_benchmark(self):
        from detector import check_market_arbitrage
        res = check_market_arbitrage({"title": "Телевизор Samsung QE55", "price": 100000, "shop": "Sulpak"},
                                     other_stores_avg=200000, custom_settings=self.SETTINGS)
        self.assertEqual((res["old_price"], res["new_price"], res["canonical_key"]), (200000, 100000, None))
        self.assertIn("дешевле, чем", res["reason"])
        self.assertIn("200 000 ₸", res["reason"])

    def test_zero_glitch_reason_keeps_punctuation(self):
        from detector import check_anomaly
        settings = {"junk_keywords": [], "exclude_used_goods": True, "min_item_price_kzt": 30000,
                    "max_item_price_kzt": 3000000, "detect_zero_glitch": True, "detect_super_discount": True,
                    "price_glitch_drop_pct": 50, "min_savings_kzt": 40000}
        res = check_anomaly({"title": "Телевизор", "price": 30000}, {"old_price": 300000, "first_seen_price": 300000},
                            custom_settings=settings)
        self.assertIn("300 000 ₸", res["reason"])
        self.assertIn("), возможно, пропущен ноль", res["reason"])


class CooldownRaceTest(unittest.TestCase):
    """R-M02: запрос, ждавший слот, пока другой получил 429, не уходит во время паузы."""

    def setUp(self):
        http.reset_limiter()
        self.addCleanup(http.reset_limiter)

    def test_waiting_request_sees_cooldown_set_while_it_waited(self):
        started = threading.Event()
        release = threading.Event()
        sent = []

        def slow_429():
            started.set()
            release.wait(2)
            return Mock(status_code=429, headers={"Retry-After": "120"})

        with patch.object(http, "MAX_CONCURRENCY_PER_HOST", 1):
            http.reset_limiter()
            first = threading.Thread(target=lambda: http._limited("GET", "https://race.example/a", slow_429))
            first.start()
            started.wait(2)
            errors = []

            def second():
                try:
                    http._limited("GET", "https://race.example/b", lambda: sent.append("b") or Mock(status_code=200, headers={}))
                except http.HostCooldown as e:
                    errors.append(e)

            waiter = threading.Thread(target=second)
            waiter.start()
            time.sleep(0.05)   # второй запрос уже ждёт слот
            release.set()
            first.join(2)
            waiter.join(2)
        self.assertEqual(sent, [])
        self.assertEqual(len(errors), 1)
        self.assertGreater(errors[0].retry_after, 100)


class DetailsHardeningTest(unittest.TestCase):
    """R-M06 закрепление адреса, R-M07 Forte с составным id, R-M05 общий лимит догрузок."""

    def setUp(self):
        import product_details
        self.pd = product_details
        product_details._negative.clear()
        from auth import RateLimiter
        patcher = patch.object(product_details, "_fetch_rate", RateLimiter(product_details.MAX_FETCHES_PER_MINUTE, 60))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_connection_is_pinned_to_checked_address(self):
        session = FakeSession([FakeStream(chunks=(b"<html>ok</html>",))])
        with patch.object(self.pd.http, "Session", session):
            self.pd.fetch_html("https://kaspi.kz/shop/p/x", lambda host, port: ["93.184.216.34"])
        from curl_cffi import CurlOpt
        self.assertEqual(session.options, {CurlOpt.RESOLVE: ["kaspi.kz:443:93.184.216.34"]})

    def test_forte_description_with_composite_id(self):
        hits = {"hits": [{"objectID": "999", "Params": {}}, {"objectID": "123", "ParamMap": {"Цвет": "Белый"}}]}
        with patch.object(self.pd.http, "post", return_value=Mock(status_code=200, json=Mock(return_value=hits))):
            self.assertIn("Цвет: Белый", self.pd.forte_description({"id": "forte_123@kz", "title": "Товар"}))

    def test_new_shop_domains_allowed(self):
        for host in ("vkusmart.vmv.kz", "12.kz", "www.zeta.kz", "komfort.kz", "lemanapro.kz", "arbuz.kz"):
            self.assertTrue(self.pd.store_host_allowed(host), host)

    def test_concurrency_and_rate_limit(self):
        active, peak, calls = [0], [0], []
        lock = threading.Lock()

        def slow(prod):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                calls.append(prod["id"])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return "описание"

        async def scenario(n):
            with patch.object(self.pd, "fetch_description_sync", side_effect=slow):
                return await asyncio.gather(*(self.pd.get_description({"id": f"d{i}", "url": "https://kaspi.kz/x"})
                                              for i in range(n)))

        results = asyncio.run(scenario(12))
        self.assertLessEqual(peak[0], self.pd.MAX_CONCURRENT_FETCHES)
        self.assertEqual(results, ["описание"] * 12)
        # Лимит новых загрузок в минуту: сверх него — без внешнего запроса
        more = asyncio.run(scenario(self.pd.MAX_FETCHES_PER_MINUTE))
        self.assertEqual(len(calls), self.pd.MAX_FETCHES_PER_MINUTE)
        self.assertEqual(more.count(""), 12)


class NewSourcesEndProofTest(unittest.TestCase):
    """R-M01: конец каталога новых источников — по доказательству, иначе «ограничен», не ошибка."""

    @staticmethod
    def _zeta_items(n, start=0):
        return [{"id": f"z{i}", "name": {"ru": f"Ведро {i}"}, "price": 1000 + i, "slug": f"vedro-{i}", "available": True}
                for i in range(start, start + n)]

    def _run(self, scraper, responses, url="https://x.kz/catalog/c/", max_pages=10):
        session = Mock()
        session.get.side_effect = responses
        with patch.object(scraper, "_get_session", return_value=session), patch("scrapers.base.time.sleep"):
            return scraper._scrape_sync("C", url, max_pages)

    def test_zeta_complete_by_result_count(self):
        from scrapers.zeta import ZetaScraper
        s = ZetaScraper()
        size = s.PAGE_SIZE
        pages = [Mock(status_code=200, json=Mock(return_value={"results": self._zeta_items(size), "resultCount": size + 5})),
                 Mock(status_code=200, json=Mock(return_value={"results": self._zeta_items(5, size), "resultCount": size + 5}))]
        result = self._run(s, pages, "6851938995dd04035cad42d6")
        self.assertTrue(result.complete)
        self.assertEqual(len(result), size + 5)

    def test_zeta_without_count_is_not_complete(self):
        from scrapers.zeta import ZetaScraper
        s = ZetaScraper()
        pages = [Mock(status_code=200, json=Mock(return_value={"results": self._zeta_items(3)})),
                 Mock(status_code=200, json=Mock(return_value={"results": []}))]
        result = self._run(s, pages, "6851938995dd04035cad42d6")
        self.assertFalse(result.complete)
        with self.assertRaises(RuntimeError):
            s._get_session = Mock(return_value=Mock(get=Mock(return_value=Mock(status_code=200, json=Mock(return_value={"error": "x"})))))
            s._fetch_page("C", "6851938995dd04035cad42d6", 1)

    def test_html_source_without_end_proof_is_limited_not_error(self):
        from scrapers.base import UnconfirmedEnd
        from scrapers.vkusmart import VkusmartScraper
        s = VkusmartScraper()
        calls = []

        def fetch(name, url, page):
            calls.append(page)
            if page == 1:
                return [{"id": "v1", "title": "Товар", "price": 100, "url": "https://vkusmart.vmv.kz/p/1"}]
            raise UnconfirmedEnd("карточки не найдены")

        with patch.object(s, "_fetch_page", side_effect=fetch), patch("scrapers.base.time.sleep"):
            result = s._scrape_sync("C", "https://vkusmart.vmv.kz/catalog/x/", 5)
        self.assertEqual((len(result), result.complete, result.limited, result.error), (1, False, True, None))
        # Пустая первая страница — видимая ошибка, а не тихий ноль
        session = Mock(get=Mock(return_value=Mock(status_code=200, text="<html><body>нет товаров</body></html>")))
        with patch.object(s, "_get_session", return_value=session):
            with self.assertRaises(UnconfirmedEnd):
                s._fetch_page("C", "https://vkusmart.vmv.kz/catalog/x/", 1)
            empty = s._scrape_sync("C", "https://vkusmart.vmv.kz/catalog/x/", 5)
        self.assertTrue(empty.error)

    def test_arbuz_http_error_is_not_empty_page(self):
        from scrapers.arbuz import ArbuzScraper
        s = ArbuzScraper()
        session = Mock(get=Mock(return_value=Mock(status_code=503, text="")))
        with patch.object(s, "_get_session", return_value=session):
            with self.assertRaises(RuntimeError):
                s._fetch_page("C", "https://arbuz.kz/ru/almaty/catalog/cat/1-x", 1)

    def test_lemanapro_complete_on_last_page_from_pager(self):
        from scrapers.base import pagination_last_page
        html = ('<a href="/catalogue/instr/?page=2">2</a><a href="/catalogue/instr/?page=7">7</a>'
                '<a href="/catalogue/other/?page=99">99</a>')
        self.assertEqual(pagination_last_page(html, "https://lemanapro.kz/catalogue/instr/", "page"), 7)
        self.assertIsNone(pagination_last_page("<a href='/x'>x</a>", "https://lemanapro.kz/catalogue/instr/", "page"))
