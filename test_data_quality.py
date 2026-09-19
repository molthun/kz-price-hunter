"""P02 Data Quality & Freshness: пороги, baseline, защита каталога, свежесть, контракт адаптеров."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import datetime
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import data_quality as dq
import database
from config import DB_PATH

UTC = datetime.timezone.utc


def offers(n, prefix="p", image=True, price=100):
    return [{"id": f"{prefix}{i}", "title": f"Товар {i}", "url": f"https://shop.example/{prefix}{i}",
             "price": price, "image_url": "https://img.example/x.jpg" if image else ""} for i in range(n)]


def base(valid, image_share=1.0, basis="history"):
    return {"valid": float(valid), "image_share": image_share, "basis": basis, "samples": 5}


class AssessTest(unittest.TestCase):
    """Пороги, утверждённые владельцем до реализации (карточка P02)."""

    def verdict(self, items, baseline, complete=True, error=None):
        return dq.assess(dq.measure(items), complete=complete, error=error, baseline=baseline)

    def test_plan_volumes_5000_5100_4900_normal_83_degraded(self):
        for n in (5000, 5100, 4900):
            v = dq.assess({"received": n, "valid": n, "rejected": 0, "with_image": n}, complete=True, error=None,
                          baseline=base(5000))
            self.assertEqual((v["quality"], v["may_retire"], v["learn"]), ("ok", True, True), n)
        v = dq.assess({"received": 83, "valid": 83, "rejected": 0, "with_image": 83}, complete=True, error=None,
                      baseline=base(5000))
        self.assertEqual((v["quality"], v["may_retire"], v["learn"]), ("degraded", False, False))
        self.assertIn("83", v["reasons"][0])

    def test_warning_zone_accepted_but_not_learned(self):
        v = dq.assess({"received": 3500, "valid": 3500, "rejected": 0, "with_image": 3500}, complete=True,
                      error=None, baseline=base(5000))
        self.assertEqual((v["quality"], v["may_retire"], v["learn"]), ("warning", True, False))

    def test_70_percent_empty_prices_degraded(self):
        items = offers(30) + offers(70, "np", price=0)
        v = self.verdict(items, None)
        self.assertEqual(v["quality"], "degraded")
        self.assertIn("70%", v["reasons"][0])
        self.assertEqual(self.verdict(offers(95) + offers(5, "np", price=0), None)["quality"], "unknown")

    def test_small_or_missing_baseline_is_unknown_and_learns(self):
        self.assertEqual(self.verdict(offers(3), base(10))["quality"], "unknown")
        v = self.verdict(offers(3), None)
        self.assertEqual((v["quality"], v["learn"], v["may_retire"]), ("unknown", True, True))

    def test_image_drop_warning(self):
        v = self.verdict(offers(40) + offers(60, "n", image=False), base(100, image_share=0.95))
        self.assertEqual(v["quality"], "warning")
        self.assertTrue(any("фото" in w for w in v["warnings"]))

    def test_failed_scan_never_retires_or_learns(self):
        v = self.verdict(offers(100), base(100), complete=False, error="Страница 2: Timeout")
        self.assertEqual((v["quality"], v["may_retire"], v["learn"]), ("failed", False, False))

    def test_limited_never_retires(self):
        self.assertFalse(self.verdict(offers(100), base(100), complete=False)["may_retire"])

    def test_validity_rules(self):
        bad = [{"id": "", "title": "x", "url": "https://a.b/", "price": 1},
               {"id": "1", "title": " ", "url": "https://a.b/", "price": 1},
               {"id": "1", "title": "x", "url": "ftp://a.b/", "price": 1},
               {"id": "1", "title": "x", "url": "https://a.b/", "price": dq.MAX_PRICE + 1},
               {"id": "1", "title": "x", "url": "https://a.b/", "price": "abc"}, "not a dict"]
        self.assertEqual(dq.measure(bad)["valid"], 0)


class BaselineTest(unittest.TestCase):
    def test_median_of_last_accepted(self):
        hist = [{"valid": v, "with_image": v} for v in (5100, 4900, 5000, 10, 9999, 1)]
        b = dq.baseline_from_history(hist)
        self.assertEqual(b["valid"], 5000.0)  # медиана первых 5: 5100, 4900, 5000, 10, 9999
        self.assertIsNone(dq.baseline_from_history(hist[:2]))


class FreshnessTest(unittest.TestCase):
    """Переходы свежести на управляемом времени."""
    NOW = datetime.datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    def state(self, hours_ago):
        seen = self.NOW - datetime.timedelta(hours=hours_ago)
        return dq.freshness(seen.isoformat(), self.NOW)["freshness"]

    def test_transitions(self):
        self.assertEqual([self.state(h) for h in (0, 26, 26.01, 72, 72.01, 24 * 40)],
                         ["fresh", "fresh", "aging", "aging", "stale", "stale"])
        self.assertEqual(dq.freshness(None)["freshness"], "unknown")
        self.assertEqual(dq.freshness("not a date")["freshness"], "unknown")
        # формат CURRENT_TIMESTAMP SQLite (UTC без зоны)
        self.assertEqual(dq.freshness("2026-09-19 10:00:00", self.NOW)["freshness"], "fresh")


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "prices.db")
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(self.db)),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def sql(self, query, *args):
        conn = sqlite3.connect(self.db)
        try:
            with conn:
                return conn.execute(query, args).fetchall()
        finally:
            conn.close()

    def age(self, product_id, hours):
        ts = (datetime.datetime.now(UTC) - datetime.timedelta(hours=hours)).isoformat()
        self.sql("UPDATE products SET updated_at = ? WHERE id = ?", ts, product_id)


class PipelineCase(DbCase):
    """Сквозной путь _scan_shop_categories на временной БД (адаптер и детектор алертов подменены, без сети)."""
    URL = "https://shop.example/c"

    def scan(self, result):
        import web.server as server

        class Fake:
            async def scrape(self, name, url, max_pages=None):
                return result

        with patch.object(server, "queue_titles_for_ai"), \
             patch.object(server, "check_anomaly", return_value=None), \
             patch.object(server, "check_market_arbitrage", return_value=None), \
             patch.object(server, "_ensure_lease"), \
             patch.object(server.asyncio, "sleep", new=lambda *_, _real=asyncio.sleep: _real(0)):
            asyncio.run(server._scan_shop_categories(
                "syn", Fake(), [{"name": "Кат", "url": self.URL, "master": "all"}], "Синтетика", {}))

    def result(self, n, complete=True, error=None, prefix="p"):
        from scrapers.base import ScanResult
        items = [dict(o, shop="Синтетика", city="Астана") for o in offers(n, prefix)]
        return ScanResult(items, complete=complete, error=error)

    def active(self):
        return self.sql("SELECT COUNT(*) FROM products WHERE is_active = 1")[0][0]

    def history(self):
        return self.sql("SELECT quality, valid, accepted FROM source_scans ORDER BY id")


class ScanPipelineTest(PipelineCase):
    """Качество до снятия товаров, baseline, FAILED сохраняет каталог."""

    def test_silent_breakage_detected_catalog_kept(self):
        for _ in range(3):
            self.scan(self.result(200))
        self.assertEqual(self.active(), 200)
        self.scan(self.result(3))  # «тихая поломка»: complete, но 3 товара вместо 200
        self.assertEqual(self.active(), 200)  # каталог не снят
        quality, valid, accepted = self.history()[-1]
        self.assertEqual((quality, valid, accepted), ("degraded", 3, 0))
        row = database.get_shop_scans()["syn"]
        self.assertEqual(row["status"], "partial")
        self.assertIn("Качество", row["last_error"])
        # норма не испорчена деградировавшим результатом
        self.assertEqual(database.get_source_baseline("syn", self.URL, "complete")["valid"], 200.0)

    def test_first_scan_uses_active_offers_as_baseline(self):
        self.scan(self.result(200))
        self.scan(self.result(5))  # истории 1 результат — норма по активным предложениям источника (200)
        self.assertEqual(self.active(), 200)
        self.assertEqual(self.history()[-1][0], "degraded")

    def test_normal_changes_retire_disappeared(self):
        for n in (200, 205, 196):
            self.scan(self.result(n))
        self.assertEqual([q for q, *_ in self.history()], ["unknown", "ok", "ok"])
        self.assertEqual(self.active(), 196)  # нормальный полный обход снимает исчезнувшие

    def test_failed_scan_keeps_offers_and_does_not_refresh(self):
        self.scan(self.result(200))
        self.age("p0@astana", 30)
        self.scan(self.result(50, complete=False, error="Страница 2: Timeout"))
        self.assertEqual(self.active(), 200)
        self.assertEqual(self.history()[-1][0], "failed")
        # p0 пришёл в частичной выдаче — его цена наблюдалась заново; p199 не пришёл — время не обновлено
        seen = dict(self.sql("SELECT id, updated_at FROM products WHERE id IN ('p0@astana', 'p199@astana')"))
        self.assertEqual(dq.freshness(seen["p0@astana"])["freshness"], "fresh")
        self.assertLess(seen["p199@astana"], seen["p0@astana"])  # не пришёл в частичной выдаче — не освежён

    def test_quality_in_telemetry_and_shop_report(self):
        import telemetry as tm
        svc = tm.TelemetryService()
        with patch.object(tm, "telemetry", svc):
            for _ in range(3):
                self.scan(self.result(200))
            self.scan(self.result(3))
        cats = [e for e in svc.get_recent_events(limit=100) if e["type"] == tm.EVENT_SCAN_CATEGORY]
        self.assertIn('"quality": "degraded"', cats[-1]["data_json"])
        (report,) = database.get_shops_scan_report(["syn"])
        self.assertEqual(report["last_quality"], "degraded")
        self.assertEqual(report["freshness"], "fresh")  # последний полный обход был недавно


class VisibilityCase(DbCase):
    def add(self, pid, price, hours_ago, old_price=0, shop="S"):
        database.save_or_update_products_batch([{"id": pid, "title": "Смартфон Test X 128GB", "url": f"https://s.example/{pid}",
                                                 "price": price, "shop": shop, "city": "Астана",
                                                 "old_price_on_site": old_price}])
        self.age(pid, hours_ago)


class VisibilityTest(VisibilityCase):
    """Решение владельца: устаревшие видны с бейджем, скрываются снятые и не виденные > 30 дней;
    Stale не даёт лучшую цену, алерты и скидки."""

    def test_catalog_search_and_best_price(self):
        import search_engine
        self.add("fresh1", 1000, 1, shop="A")
        self.add("aging1", 900, 48, shop="B")
        self.add("stale1", 500, 100, shop="C")
        self.add("gone1", 400, 24 * 31, shop="D")
        listed = {p["id"]: p["freshness"] for p in database.get_products_list(limit=10)}
        self.assertEqual(listed, {"fresh1": "fresh", "aging1": "aging", "stale1": "stale"})
        with patch.object(search_engine, "DB_PATH", type(DB_PATH)(self.db)):
            res = asyncio.run(search_engine.get_best_price_summary("Смартфон Test"))
        self.assertEqual(res["total_found"], 3)
        self.assertEqual(res["best_deal"]["id"], "aging1")  # stale дешевле, но не лучшая цена
        self.assertEqual(res["stale_count"], 1)
        self.assertEqual({s["shop"] for s in res["store_comparison"]}, {"A", "B"})
        self.assertEqual({i["id"]: i["freshness"] for i in res["items"]}["stale1"], "stale")

    def test_all_stale_has_no_best_deal(self):
        import search_engine
        self.add("stale1", 500, 100)
        with patch.object(search_engine, "DB_PATH", type(DB_PATH)(self.db)):
            res = asyncio.run(search_engine.get_best_price_summary("Смартфон Test"))
        self.assertEqual((res["total_found"], res["best_deal"], res["all_stale"]), (1, None, True))

    def test_store_deals_only_fresh_prices(self):
        self.add("deal_fresh", 800, 2, old_price=1000)
        self.add("deal_stale", 700, 100, old_price=1000)
        stats = database.get_stats()
        self.assertEqual(stats["total_store_deals"], 1)

    def test_notification_skipped_for_stale_price(self):
        self.add("n1", 800, 100)
        conn = database.get_connection()
        with conn:
            row = conn.execute("SELECT current_price FROM products WHERE id='n1' AND " + database.fresh_price_clause()).fetchone()
        self.assertIsNone(row)


class DuplicatesTest(PipelineCase):
    """C01: объём и брак — по уникальным id; повторы не снимают каталог и не обучают норму."""

    def dup_result(self, n_rows, prefix="p", complete=True):
        from scrapers.base import ScanResult
        one = dict(offers(1, prefix)[0], shop="Синтетика", city="Астана")
        return ScanResult([dict(one) for _ in range(n_rows)], complete=complete)

    def test_codex_repro_200_copies_of_one_id(self):
        for _ in range(3):
            self.scan(self.result(200))
        self.scan(self.dup_result(200))
        self.assertEqual(self.active(), 200)
        quality, valid, accepted = self.history()[-1]
        self.assertEqual((quality, valid, accepted), ("degraded", 1, 0))
        reason = self.sql("SELECT reason, duplicates FROM source_scans ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual(reason[1], 199)
        self.assertIn("повторов 199", reason[0])
        self.assertEqual(database.get_source_baseline("syn", self.URL, "complete")["valid"], 200.0)

    def test_duplicates_do_not_inflate_baseline(self):
        from scrapers.base import ScanResult
        for _ in range(3):
            rows = [dict(o, shop="Синтетика", city="Астана") for o in offers(100)]
            self.scan(ScanResult(rows + rows[:20], complete=True))  # 20 % повторов — ниже порога брака
        self.assertEqual([v for _, v, _ in self.history()], [100, 100, 100])
        self.assertEqual(database.get_source_baseline("syn", self.URL, "complete")["valid"], 100.0)
        self.assertEqual(self.active(), 100)

    def test_first_row_wins_on_conflicting_price(self):
        from scrapers.base import ScanResult
        rows = [dict(o, shop="Синтетика", city="Астана") for o in offers(30)]
        self.scan(ScanResult(rows + [dict(rows[0], price=1)], complete=True))
        price = self.sql("SELECT current_price FROM products WHERE id = 'p0@astana'")[0][0]
        self.assertEqual(price, 100)
        self.assertIn("другой ценой", self.sql("SELECT reason FROM source_scans ORDER BY id DESC LIMIT 1")[0][0])

    def test_same_item_other_city_or_shop_not_merged(self):
        from offer_identity import assign_offer_ids
        from scrapers.base import ScanResult
        rows = ScanResult([dict(offers(1)[0], shop="Синтетика", city="Астана"),
                           dict(offers(1)[0], shop="Синтетика", city="Алматы")])
        assign_offer_ids(rows)
        self.assertEqual(dq.measure(rows)["duplicates"], 0)
        self.assertEqual(len(dq.dedupe(rows)), 2)


class StaleBenchmarkTest(DbCase):
    """C02: устаревшая цена конкурента не основание арбитража; алерт гаснет, когда основание устарело."""
    TITLE = "Apple iPhone 15 128GB Black"

    def add(self, pid, shop, price, hours_ago, title=None):
        database.save_or_update_products_batch([{"id": pid, "title": title or self.TITLE, "price": price, "shop": shop,
                                                 "city": "Астана", "url": f"https://{shop.lower()}.example/{pid}"}])
        self.age(pid, hours_ago)

    def arbitrage(self, price=200000):
        from detector import check_market_arbitrage
        return check_market_arbitrage({"id": "new", "title": self.TITLE, "price": price, "shop": "Current",
                                       "city": "Астана", "url": "https://cur.example/new"},
                                      custom_settings={"detect_market_arbitrage": True})

    def test_codex_repro_single_stale_competitor(self):
        self.add("old", "Other", 400000, 96)
        self.assertIsNone(self.arbitrage())

    def test_mixed_competitors_use_only_fresh_and_aging(self):
        self.add("stale", "S1", 900000, 80)
        self.add("aging", "S2", 400000, 48)
        self.add("fresh", "S3", 420000, 1)
        a = self.arbitrage()
        self.assertEqual((a["old_price"], a["competitor_shop"]), (400000, "S2"))
        self.assertEqual(dq.freshness(a["competitor_seen_at"])["freshness"], "aging")
        m = database.find_market_comparisons(self.TITLE, "Current", 200000, "Астана")
        self.assertEqual(sorted(c["shop"] for c in m["competitors"]), ["S2", "S3"])

    def test_fts_branch_without_canonical_key(self):
        title = "Кофемашина Delonghi Magnifica Evo ECAM290"
        with patch("model_matching.extract_canonical_key", return_value=None), \
             patch.object(database, "get_cached_canonical_key", return_value=None):
            self.add("fts_old", "Other", 400000, 80, title=title)
            self.assertIsNone(database.find_market_comparisons(title, "Current", 200000, "Астана"))
            self.add("fts_new", "Other2", 390000, 2, title=title)
            m = database.find_market_comparisons(title, "Current", 200000, "Астана")
        self.assertEqual([c["shop"] for c in m["competitors"]], ["Other2"])

    def test_boundary_72h(self):
        self.add("edge", "Other", 400000, 71.5)
        self.assertIsNotNone(self.arbitrage())
        self.age("edge", 72.5)
        self.assertIsNone(self.arbitrage())

    def test_existing_alert_hidden_and_not_delivered_when_benchmark_stale(self):
        self.add("cur", "Current", 200000, 0)
        seen_fresh = datetime.datetime.now(UTC).isoformat()
        seen_stale = (datetime.datetime.now(UTC) - datetime.timedelta(hours=80)).isoformat()
        for seen in (seen_fresh, seen_stale):
            database.record_alert("cur", "MARKET_ARBITRAGE", 400000, 200000, 50.0, 200000,
                                  shop="Current", competitor_shop="Other", competitor_seen_at=seen)
            self.sql("UPDATE alerts SET created_at = datetime('now', '-1 hour')")
            database.invalidate_alerts_cache()
            visible = database._fetch_filtered_alerts({})
            self.assertEqual(len(visible), 1 if seen == seen_fresh else 0, seen)
            self.sql("DELETE FROM alerts")
        # Старые алерты без competitor_seen_at — по времени создания
        database.record_alert("cur", "MARKET_ARBITRAGE", 400000, 200000, 50.0, 200000, shop="Current")
        self.sql("UPDATE alerts SET created_at = datetime('now', '-4 days')")
        database.invalidate_alerts_cache()
        self.assertEqual(database._fetch_filtered_alerts({}), [])
        import notifier
        self.assertTrue(notifier._stale_benchmark({"type": "MARKET_ARBITRAGE", "competitor_seen_at": seen_stale}))
        self.assertFalse(notifier._stale_benchmark({"type": "MARKET_ARBITRAGE", "competitor_seen_at": seen_fresh}))
        self.assertFalse(notifier._stale_benchmark({"type": "ZERO_GLITCH", "competitor_seen_at": seen_stale}))


class AllStaleComparisonTest(VisibilityCase):
    """C03: без актуальных цен нет статистики, сравнения магазинов и разницы с лучшей."""

    def summary(self, q="Смартфон Test"):
        import search_engine
        with patch.object(search_engine, "DB_PATH", type(DB_PATH)(self.db)):
            return asyncio.run(search_engine.get_best_price_summary(q))

    def test_all_stale_single_and_multiple_shops(self):
        self.add("s1", 500, 100, shop="A")
        r = self.summary()
        self.assertEqual((r["all_stale"], r["best_deal"], r["price_stats"], r["store_comparison"]), (True, None, None, []))
        self.assertEqual([(i["id"], i["diff_from_best"], i["freshness"]) for i in r["items"]], [("s1", None, "stale")])
        self.add("s2", 700, 90, shop="B")
        r = self.summary()
        self.assertEqual((r["total_found"], r["store_comparison"], r["shop_counts"]), (2, [], {"A": 1, "B": 1}))

    def test_mixed_stale_rows_have_no_diff(self):
        self.add("s1", 500, 100, shop="A")
        self.add("f1", 800, 1, shop="B")
        r = self.summary()
        diffs = {i["id"]: i["diff_from_best"] for i in r["items"]}
        self.assertEqual(diffs, {"s1": None, "f1": 0})
        self.assertEqual(r["price_stats"]["min"], 800)

    def test_empty(self):
        r = self.summary("ничего такого нет")
        self.assertEqual((r["total_found"], r["best_deal"]), (0, None))


CHALLENGE_HTML = ("<html><head><title>Just a moment...</title></head><body><div id='challenge-form'>"
                  "Checking your browser before accessing</div></body></html>")


class AdapterContractTest(unittest.TestCase):
    """Повтор первой страницы и HTML challenge не дают complete (план P02)."""

    def test_repeated_first_page_is_not_complete(self):
        from scrapers.base import PagedScraper

        class Repeat(PagedScraper):
            PAGE_DELAY_SECONDS = 0

            def _fetch_page(self, category_name, category_url, page):
                return offers(40)  # сайт игнорирует номер страницы

        result = Repeat()._scrape_sync("c", "https://shop.example/c", max_pages=5)
        self.assertEqual((len(result), result.complete), (40, False))

    def test_challenge_page_never_complete_for_any_adapter(self):
        import web.server as server
        from scrapers import http

        def challenge(method, url, send, stream=False):
            resp = Mock(status_code=200, text=CHALLENGE_HTML, content=CHALLENGE_HTML.encode(), headers={}, url=url)
            resp.json.side_effect = ValueError("not json")
            resp.iter_content.return_value = [CHALLENGE_HTML.encode()]
            return resp

        checked = []
        for key, (cls, categories, _name) in server.SHOP_REGISTRY.items():
            if key == "dns" or not categories:
                continue  # DNS — браузер (Playwright); Cloudflare у DNS покрыт test_scraper_reliability
            scraper = cls()
            try:
                with patch.object(http, "_limited", challenge), patch("time.sleep"):
                    try:
                        result = asyncio.run(asyncio.wait_for(
                            scraper.scrape(categories[0]["name"], categories[0]["url"], max_pages=2), 20))
                    except Exception:
                        result = None  # отказ адаптера — тоже не complete
            finally:
                getattr(scraper, "close", lambda: None)()
            if result is not None:
                self.assertFalse(getattr(result, "complete", False), key)
                self.assertEqual(len(result), 0, key)
            checked.append(key)
        self.assertGreaterEqual(len(checked), 20)


if __name__ == "__main__":
    unittest.main()
