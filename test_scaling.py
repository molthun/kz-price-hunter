"""Этап 5 аудита: ограниченные кэши, планировщик, 100 синтетических источников, ресурсы, бюджет AI.
Без сети и без рабочей БД."""
import asyncio
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

from bounded_cache import BoundedTTLCache  # noqa: E402
from database import get_connection, init_db  # noqa: E402
from scrapers.base import ScanResult  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class BoundedCacheTest(unittest.TestCase):
    def test_lru_limit(self):
        cache = BoundedTTLCache(maxsize=3, ttl=60)
        for i in range(5):
            cache[i] = i
        self.assertEqual(sorted(cache), [2, 3, 4])
        cache[2]  # свежий доступ
        cache[5] = 5
        self.assertEqual(sorted(cache), [2, 4, 5])

    def test_ttl_expiry_without_new_access(self):
        clock = FakeClock()
        cache = BoundedTTLCache(maxsize=100, ttl=10, clock=clock)
        cache["a"] = 1
        clock.now += 11
        self.assertNotIn("a", cache)
        self.assertEqual(cache.get("a"), None)
        self.assertEqual(len(cache), 0)

    def test_unique_keys_do_not_grow_unbounded(self):
        cache = BoundedTTLCache(maxsize=50, ttl=3600)
        for i in range(10_000):
            cache[("query", i)] = i
        self.assertEqual(len(cache), 50)

    def test_thread_safety_smoke(self):
        cache = BoundedTTLCache(maxsize=100, ttl=60)
        errors = []

        def work(n):
            try:
                for i in range(2000):
                    cache[(n, i)] = i
                    cache.get((n, i - 1))
                    cache.pop((n, i - 2), None)
            except Exception as e:  # pragma: no cover - сообщение для диагностики
                errors.append(e)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertLessEqual(len(cache), 100)

    def test_app_caches_are_bounded(self):
        import ai_service
        import search_engine
        import telegram_bot
        for cache in (ai_service._QUERY_CACHE, search_engine._LIVE_CACHE, telegram_bot._chat_histories):
            self.assertIsInstance(cache, BoundedTTLCache)

    def test_rate_limiter_forgets_idle_keys(self):
        from auth import RateLimiter
        limiter = RateLimiter(max_calls=5, period=0.01)
        for i in range(RateLimiter.SWEEP_EVERY - 1):
            limiter.retry_after(f"ip-{i}")
        time.sleep(0.02)
        limiter.retry_after("trigger-sweep")
        self.assertLessEqual(len(limiter._calls), 1)


def _make_source(index, calls, active, lock, behaviour):
    class SyntheticScraper:
        closed = False

        async def scrape(self, category_name, category_url, max_pages=None):
            with lock:
                active[0] += 1
                calls["peak"] = max(calls["peak"], active[0])
            try:
                await asyncio.sleep(0.002 if behaviour != "slow" else 0.02)
                if behaviour == "error":
                    raise RuntimeError("HTTP 500")
                items = [{"id": f"syn{index}_{n}", "shop": f"Синтетика {index}", "title": f"Товар {index}-{n} модель X{n}",
                          "url": f"https://shop{index}.example/p/{n}", "price": 10000 + n, "city": "Астана",
                          "category": "Тест"} for n in range(10)]
                if behaviour == "blocked":
                    return ScanResult(items[:3], error="HTTP 429")
                if behaviour == "partial":
                    return ScanResult(items, limited=True)
                return ScanResult(items, complete=True)
            finally:
                with lock:
                    active[0] -= 1

        def close(self):
            calls["closed"] += 1

    return SyntheticScraper


class HundredSourcesTest(unittest.IsolatedAsyncioTestCase):
    """Приёмка аудита: 100 источников, включая медленные, заблокированные и падающие."""

    async def asyncSetUp(self):
        init_db()
        import web.server as server
        self.server = server
        server.scan_state["is_running"] = False
        with get_connection() as conn:
            for table in ("products", "product_sources", "shop_scans", "scheduler_lease", "alerts", "tracked_categories"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        # Конец обхода обновляет отслеживаемые категории live-поиском — в тестах без сети
        live = patch("search_engine.search_live_stores", new_callable=AsyncMock, return_value=[])
        self.live_search = live.start()
        self.addCleanup(live.stop)

    @staticmethod
    def settings(**overrides):
        import config
        return {**config.load_settings(), "enabled_shops": {}, **overrides}

    async def test_bounded_concurrency_state_and_no_mass_retirement(self):
        server = self.server
        calls, active, lock = {"peak": 0, "closed": 0}, [0], threading.Lock()
        behaviours = ["ok"] * 70 + ["slow"] * 10 + ["partial"] * 8 + ["blocked"] * 6 + ["error"] * 6
        registry = {f"syn{i}": (_make_source(i, calls, active, lock, b),
                                [{"name": f"Синт {i}", "url": f"https://shop{i}.example/c", "master": "all"}],
                                f"Синтетика {i}") for i, b in enumerate(behaviours)}
        # Ранее найденные товары «заблокированного» источника не должны сниматься
        blocked_index = behaviours.index("blocked")
        with get_connection() as conn:
            conn.execute("""INSERT INTO products (id, shop, city, title, url, current_price, first_seen_price, min_price, max_price, is_active)
                            VALUES ('syn%d_old@astana', 'Синтетика', 'Астана', 'Старый', 'https://x', 1, 1, 1, 1, 1)""" % blocked_index)
            conn.execute("INSERT INTO product_sources VALUES ('syn%d_old@astana', 'syn%d', 'https://shop%d.example/c', 1)"
                         % (blocked_index, blocked_index, blocked_index))
            conn.commit()

        started = time.monotonic()
        with patch.dict(server.SHOP_REGISTRY, registry, clear=True), \
             patch.object(server, "load_settings", return_value=self.settings()), \
             patch.object(server, "queue_titles_for_ai"):
            await server._do_scan_task(list(registry), scan_type="manual")
        duration = time.monotonic() - started

        self.assertLessEqual(calls["peak"], server.SHOP_CONCURRENCY)
        self.assertEqual(calls["closed"], 100)  # каждая сессия адаптера закрыта
        with get_connection() as conn:
            statuses = dict(conn.execute("SELECT status, count(*) FROM shop_scans GROUP BY status").fetchall())
            products = conn.execute("SELECT count(*) FROM products WHERE id LIKE 'syn%@astana'").fetchone()[0]
            old_active = conn.execute("SELECT is_active FROM products WHERE id = ?",
                                      (f"syn{blocked_index}_old@astana",)).fetchone()[0]
            lease = conn.execute("SELECT count(*) FROM scheduler_lease").fetchone()[0]
        self.assertEqual(sum(statuses.values()), 100)
        self.assertEqual(statuses.get("failed"), 6)
        self.assertEqual(statuses.get("partial"), 6)
        self.assertEqual(statuses.get("complete"), 80)
        self.assertEqual(statuses.get("limited"), 8)
        self.assertEqual(products, (80 + 8) * 10 + 6 * 3 + 1)
        self.assertEqual(old_active, 1)
        self.assertEqual(lease, 0)  # аренда освобождена
        self.assertFalse(server.scan_state["is_running"])
        self.assertLess(duration, 60)

    async def test_second_process_does_not_scan_while_lease_is_held(self):
        server = self.server
        with get_connection() as conn:
            conn.execute("INSERT INTO scheduler_lease VALUES ('scan', 'other-host:1:x', ?)", (time.time() + 120,))
            conn.commit()
        scraper = MagicMock()
        registry = {"syn0": (scraper, [{"name": "c", "url": "https://s.example/c", "master": "all"}], "Синтетика")}
        with patch.dict(server.SHOP_REGISTRY, registry, clear=True), \
             patch.object(server, "load_settings", return_value=self.settings()):
            await server._do_scan_task(["syn0"], scan_type="manual")
        scraper.assert_not_called()
        self.assertFalse(server.scan_state["is_running"])

    async def test_expired_lease_is_taken_over(self):
        from database import acquire_scheduler_lease
        with get_connection() as conn:
            conn.execute("INSERT INTO scheduler_lease VALUES ('scan', 'dead-host:1:x', ?)", (time.time() - 1,))
            conn.commit()
        self.assertTrue(acquire_scheduler_lease("me", 60))
        self.assertFalse(acquire_scheduler_lease("someone-else", 60))
        self.assertTrue(acquire_scheduler_lease("me", 60))  # продление своей аренды

    async def test_wave_index_survives_restart(self):
        server = self.server
        from database import get_metadata, set_metadata
        set_metadata("wave_index", 2)
        server.wave_state["current_wave_index"] = 0  # как после перезапуска процесса
        registry = {"syn0": (_make_source(0, {"peak": 0, "closed": 0}, [0], threading.Lock(), "ok"),
                             [{"name": "c", "url": "https://s.example/c", "master": "all"}], "Синтетика")}
        with patch.dict(server.SHOP_REGISTRY, registry, clear=True), \
             patch.object(server, "load_settings", return_value=self.settings(wave_mode="rolling")), \
             patch.object(server, "queue_titles_for_ai"):
            await server._do_scan_task(["syn0"], scan_type="auto")
        self.assertEqual(server.scan_state["wave_info"]["wave_index"], 2)
        self.assertNotEqual(get_metadata("wave_index"), "2")


class ResourcesTest(unittest.IsolatedAsyncioTestCase):
    async def test_dns_browser_closed_on_error(self):
        from scrapers import dns
        browser = MagicMock()
        browser.close = AsyncMock()
        browser.new_context = AsyncMock(side_effect=RuntimeError("context failed"))
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=playwright)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(dns, "async_playwright", return_value=manager):
            with self.assertRaises(RuntimeError):
                await dns.DNSScraper().scrape("c", "https://www.dns-shop.kz/catalog/x/")
        browser.close.assert_awaited_once()

    def test_feed_size_cap(self):
        from scrapers import shopkz

        class Resp:
            status_code = 200
            closed = False

            def iter_content(self, chunk_size=None):
                while True:
                    yield b"x" * 1024

            def close(self):
                Resp.closed = True

        class Session:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, *a, **k):
                return Resp()

        with patch.object(shopkz, "MAX_FEED_BYTES", 10 * 1024), patch.object(shopkz.requests, "Session", Session):
            result = shopkz.ShopKzScraper().scrape_yml("https://shop.kz/bitrix/catalog_export/yandex.php")
        self.assertEqual(list(result), [])
        self.assertEqual(result.error, "RuntimeError")
        self.assertTrue(Resp.closed)

    def test_base_close_closes_session(self):
        from scrapers.halyk import HalykScraper
        scraper = HalykScraper()
        session = MagicMock()
        scraper._session = session
        scraper.close()
        session.close.assert_called_once()
        self.assertIsNone(scraper._session)


class SearchOrderTest(unittest.TestCase):
    def test_price_desc_sorted_before_limit(self):
        init_db()
        from database import save_or_update_products_batch
        from search_engine import search_in_database
        items = [{"id": f"ord{n}@astana", "shop": "Тест", "title": f"Монитор тестовый {n}", "url": "https://x",
                  "price": 10000 + n, "city": "Астана", "category": "Мониторы"} for n in range(300)]
        items.append({"id": "ord-top@astana", "shop": "Тест", "title": "Монитор тестовый премиум", "url": "https://x",
                      "price": 2_000_000, "city": "Астана", "category": "Мониторы"})
        save_or_update_products_batch(items)
        top = search_in_database("монитор тестовый", city="Астана", sort_by="price_desc", limit=5)
        self.assertEqual(top[0]["id"], "ord-top@astana")
        self.assertEqual([p["current_price"] for p in top], sorted((p["current_price"] for p in top), reverse=True))


class AiBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM schema_metadata WHERE name LIKE 'ai_calls:%'")
            conn.commit()

    async def test_daily_budget_stops_calls_and_scan_gets_a_share(self):
        import ai_service
        from auth import RateLimiter
        provider = AsyncMock(return_value={"ok": True})
        with patch.object(ai_service, "DAILY_AI_CALL_LIMIT", 10), \
             patch.object(ai_service, "_provider_limiter", RateLimiter(1000, 60)), \
             patch.object(ai_service, "_scan_limiter", RateLimiter(1000, 60)):
            for _ in range(7):
                self.assertEqual(await ai_service._limited_provider_call(provider, scan=True), {"ok": True})
            self.assertIsNone(await ai_service._limited_provider_call(provider, scan=True))  # 70% для фона
            for _ in range(3):
                self.assertEqual(await ai_service._limited_provider_call(provider), {"ok": True})
            self.assertIsNone(await ai_service._limited_provider_call(provider))
        self.assertEqual(provider.await_count, 10)
        self.assertEqual(ai_service.ai_calls_today(), 10)

    async def test_consultant_answer_is_bounded(self):
        import ai_service
        creds = {"gemini_api_key": "k", "openai_api_key": "", "ai_search_enabled": True, "has_ai": True, "provider": "gemini"}
        response = {"answer": "x" * 50_000, "suggested_questions": ["q" * 500, 5, None, "a", "b", "c", "d", "e"],
                    "recommended_product_ids": "not-a-list"}
        with patch.object(ai_service, "_get_api_credentials", return_value=creds), \
             patch.object(ai_service, "call_gemini_api", AsyncMock(return_value=response)), \
             patch("search_engine.search_in_database", return_value=[{"id": "p1", "title": "iPhone 15", "shop": "Kaspi", "current_price": 300000}]):
            res = await ai_service.ask_ai_consultant(message="Посоветуй айфон", city="Все")
        self.assertEqual(len(res["answer"]), ai_service.MAX_ANSWER_CHARS)
        self.assertEqual(len(res["suggested_questions"]), ai_service.MAX_SUGGESTIONS)
        self.assertTrue(all(isinstance(q, str) and len(q) <= ai_service.MAX_SUGGESTION_CHARS for q in res["suggested_questions"]))


if __name__ == "__main__":
    unittest.main()


class LeaseLossTest(unittest.IsolatedAsyncioTestCase):
    """R-H03: потеря аренды во время обхода останавливает запись; подготовка под try/finally."""

    async def asyncSetUp(self):
        init_db()
        import web.server as server
        self.server = server
        server.scan_state["is_running"] = False
        with get_connection() as conn:
            for table in ("products", "product_sources", "shop_scans", "scheduler_lease", "tracked_categories"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        live = patch("search_engine.search_live_stores", new_callable=AsyncMock, return_value=[])
        live.start()
        self.addCleanup(live.stop)

    @staticmethod
    def settings():
        import config
        return {**config.load_settings(), "enabled_shops": {}}

    async def test_lease_taken_over_mid_scan_stops_writes(self):
        server = self.server
        calls, active, lock = {"peak": 0, "closed": 0}, [0], threading.Lock()
        registry = {f"syn{i}": (_make_source(i, calls, active, lock, "slow"),
                                [{"name": f"Синт {i}", "url": f"https://shop{i}.example/c", "master": "all"}],
                                f"Синтетика {i}") for i in range(40)}

        async def steal_lease():
            await asyncio.sleep(0.15)
            with get_connection() as conn:
                conn.execute("UPDATE scheduler_lease SET owner = 'other-host:9:x', expires_at = ?", (time.time() + 600,))
                conn.commit()

        with patch.dict(server.SHOP_REGISTRY, registry, clear=True), \
             patch.object(server, "load_settings", return_value=self.settings()), \
             patch.object(server, "queue_titles_for_ai"), \
             patch.object(server, "SCHEDULER_LEASE_RENEW_SECONDS", 0.05):
            thief = asyncio.create_task(steal_lease())
            await server._do_scan_task(list(registry), scan_type="manual")
            await thief

        self.assertIn("аренда", server.scan_state["error"])
        self.assertFalse(server.scan_state["is_running"])
        with get_connection() as conn:
            written = conn.execute("SELECT count(*) FROM products WHERE id LIKE 'syn%'").fetchone()[0]
            lease = conn.execute("SELECT owner FROM scheduler_lease").fetchall()
        self.assertLess(written, 40 * 10)                         # обход не дошёл до конца
        self.assertEqual([r[0] for r in lease], ["other-host:9:x"])  # чужая аренда не удалена
        # После остановки новые записи не появляются
        await asyncio.sleep(0.2)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM products WHERE id LIKE 'syn%'").fetchone()[0], written)

    async def test_exception_in_preparation_releases_everything(self):
        server = self.server
        with patch.object(server, "load_settings", side_effect=RuntimeError("settings broken")):
            await server._do_scan_task(["kaspi"], scan_type="manual")
        self.assertFalse(server.scan_state["is_running"])
        self.assertIn("RuntimeError", server.scan_state["error"])
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM scheduler_lease").fetchone()[0], 0)

    def test_stale_reset_respects_foreign_lease(self):
        from database import reset_stale_running_scans
        with get_connection() as conn:
            conn.execute("INSERT INTO shop_scans (shop_key, status) VALUES ('kaspi', 'running')")
            conn.execute("INSERT INTO scheduler_lease VALUES ('scan', 'other', ?)", (time.time() + 60,))
            conn.commit()
        self.assertEqual(reset_stale_running_scans(), 0)
        with get_connection() as conn:
            conn.execute("DELETE FROM scheduler_lease")
            conn.commit()
        self.assertEqual(reset_stale_running_scans(), 1)


class CatalogSearchTest(unittest.TestCase):
    """R-M08: поиск каталога через FTS (регистр кириллицы, префикс слова), запасной LIKE для фрагментов."""

    def setUp(self):
        init_db()
        from database import save_or_update_products_batch
        with get_connection() as conn:
            conn.execute("DELETE FROM products")
            conn.commit()
        save_or_update_products_batch([
            {"id": "cs1@astana", "shop": "Sulpak", "title": "Смартфон Samsung Galaxy A26", "url": "https://x", "price": 100000, "city": "Астана"},
            {"id": "cs2@astana", "shop": "Sulpak", "title": "Смартфоны Apple iPhone 15", "url": "https://x", "price": 400000, "city": "Астана"},
            {"id": "cs3@astana", "shop": "Sulpak", "title": "Пылесос Dyson V15", "url": "https://x", "price": 300000, "city": "Астана"},
        ])

    def test_cyrillic_case_and_word_prefix(self):
        from database import get_products_count, get_products_list
        self.assertEqual(sorted(p["id"] for p in get_products_list(search="смартфон")), ["cs1@astana", "cs2@astana"])
        self.assertEqual(get_products_count(search="смартфон"), 2)
        self.assertEqual([p["id"] for p in get_products_list(search="galaxy смартф")], ["cs1@astana"])

    def test_fragment_inside_word_falls_back_to_like(self):
        from database import get_products_count, get_products_list
        self.assertEqual([p["id"] for p in get_products_list(search="phone")], ["cs2@astana"])
        self.assertEqual(get_products_count(search="phone"), 1)
        self.assertEqual(get_products_list(search="несуществующее"), [])
