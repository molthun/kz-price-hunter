"""Голодание волн (стенд 6.42.6, 21.11.2026): добор отставших источников без сдвига ротации.

Было: устаревший магазин каждые 20 с запускал обычную волну. Она сдвигала wave_index и
wave_last_run_at и обходила только категории текущей волны. У Kari всегда первой подходила
household, а одежда (clothes) и дом не обходились сутками; плановая волна не наступала ни разу.
"""
from tests import test_support  # noqa: F401
import asyncio
import datetime
import unittest
from unittest.mock import patch

from catchup_plan import RETRY_MIN_AGE, plan_catchup

NOW = 1_800_000_000.0
WAVE = 160 * 60
CYCLE = 24 * 3600
KARI = [
    {"name": "Игрушки", "url": "https://kz.kari.com/catalog/detyam/igrushki/", "master": "household"},
    {"name": "Женская обувь", "url": "https://kz.kari.com/catalog/zhenschinam/zhenskaya-obuv/", "master": "clothes"},
    {"name": "Для дома", "url": "https://kz.kari.com/catalog/tovaryi-dlya-doma/dlya-doma/", "master": "home_furniture"},
]
ENABLED = {"household", "clothes", "home_furniture", "actions"}
HOT = {"actions"}


def _ago(seconds):
    return datetime.datetime.fromtimestamp(NOW - seconds, datetime.timezone.utc).isoformat()


def _plan(last, cats=KARI, **kw):
    return plan_catchup({"kari": cats}, kw.pop("enabled", ENABLED), HOT, last, NOW, WAVE, CYCLE, **kw)


class PlanCatchupTest(unittest.TestCase):
    def test_kari_starvation_picks_only_skipped_sections(self):
        """Свежая household не повторяется; одежда (30 ч) и дом (8 сут) — в доборе."""
        last = {("kari", KARI[0]["url"]): (_ago(600), "ok"),
                ("kari", KARI[1]["url"]): (_ago(30 * 3600), "ok"),
                ("kari", KARI[2]["url"]): (_ago(8 * 86400), "ok")}
        self.assertEqual(_plan(last), {"kari": [KARI[1]["url"], KARI[2]["url"]]})

    def test_sources_within_their_rotation_slot_are_left_to_waves(self):
        last = {("kari", c["url"]): (_ago(CYCLE), "ok") for c in KARI}  # ровно круг: место ещё не пропущено
        self.assertEqual(_plan(last), {})

    def test_hot_and_feed_sources_have_short_slot(self):
        cats = [{"name": "Акции", "url": "u-hot", "master": "actions"},
                {"name": "Фид", "url": "u-feed", "master": "all"}]
        fresh = {("kari", "u-hot"): (_ago(WAVE), "ok"), ("kari", "u-feed"): (_ago(WAVE), "ok")}
        self.assertEqual(_plan(fresh, cats), {})
        late = {("kari", "u-hot"): (_ago(2 * WAVE), "ok"), ("kari", "u-feed"): (_ago(2 * WAVE + 1), "ok")}
        self.assertEqual(_plan(late, cats), {"kari": ["u-hot", "u-feed"]})

    def test_failed_source_retried_after_floor_only(self):
        for quality in ("failed", "degraded"):
            with self.subTest(quality=quality):
                early = {("kari", c["url"]): (_ago(60), quality if i == 1 else "ok") for i, c in enumerate(KARI)}
                self.assertEqual(_plan(early), {})
                due = {("kari", c["url"]): (_ago(RETRY_MIN_AGE if i == 1 else 60), quality if i == 1 else "ok")
                       for i, c in enumerate(KARI)}
                self.assertEqual(_plan(due), {"kari": [KARI[1]["url"]]})
        warn = {("kari", c["url"]): (_ago(RETRY_MIN_AGE), "warning") for c in KARI}
        self.assertEqual(_plan(warn), {}, "предупреждение — не ошибка, повтора нет")

    def test_never_scanned_disabled_and_paused(self):
        self.assertEqual(_plan({}), {"kari": [c["url"] for c in KARI]})
        self.assertEqual(_plan({}, enabled={"clothes"}), {"kari": [KARI[1]["url"]]})
        self.assertEqual(_plan({}, blocked_shops=["kari"]), {})

    def test_bad_timestamps_count_as_never_scanned(self):
        last = {("kari", c["url"]): ("not-a-date", "ok") for c in KARI}
        self.assertEqual(len(_plan(last)["kari"]), 3)


class CatchupScanDoesNotShiftRotationTest(unittest.TestCase):
    """Добор не трогает wave_index/wave_last_run_at и обходит только переданные источники."""

    def _run(self, catchup):
        import web.server as server
        calls, meta = [], {}

        async def fake_scan_shop(key, candidate_settings, semaphore, target_categories=None, only_urls=None):
            calls.append((key, target_categories, only_urls))
            return 0

        async def noop(*a, **kw):
            return None

        with patch.object(server, "_scan_shop", fake_scan_shop), \
             patch.object(server, "set_metadata", lambda k, v: meta.__setitem__(k, v)), \
             patch.object(server, "get_metadata", lambda k, d=None: d), \
             patch.object(server, "_poll_pending_demand_queries", noop), \
             patch.object(server, "_record_wave_outcome", lambda *a: meta.__setitem__("outcome", a)), \
             patch.dict(server.scan_state, {"wave_info": None}):
            asyncio.run(server._scan_task_body(["kari", "sulpak"], None, "auto", catchup))
        return calls, meta

    def test_catchup_scan_keeps_rotation_and_filters_sources(self):
        calls, meta = self._run({"kari": [KARI[1]["url"]]})
        self.assertEqual(calls, [("kari", None, [KARI[1]["url"]])])
        self.assertNotIn("wave_index", meta)
        self.assertNotIn("wave_last_run_at", meta)
        self.assertNotIn("outcome", meta)

    def test_planned_wave_still_advances_rotation(self):
        calls, meta = self._run(None)
        self.assertIn("wave_index", meta)
        self.assertIn("wave_last_run_at", meta)
        self.assertEqual({c[0] for c in calls}, {"kari", "sulpak"})
        self.assertTrue(all(c[2] is None and c[1] for c in calls))

    def _run_with_tail_spies(self, catchup):
        import database
        import search_engine
        import web.server as server

        tail_calls = {"tracked_queries": [], "demand_polls": 0}

        async def fake_scan_shop(key, candidate_settings, semaphore, target_categories=None, only_urls=None):
            return 0

        async def fake_search_live(query, city="Астана"):
            tail_calls["tracked_queries"].append((query, city))
            return []

        async def fake_demand_poll(max_queries=5):
            tail_calls["demand_polls"] += 1

        database.init_db()
        with patch.object(server, "_scan_shop", fake_scan_shop), \
             patch.object(server, "set_metadata", lambda k, v: None), \
             patch.object(server, "get_metadata", lambda k, d=None: d), \
             patch.object(server, "_failed_shop_keys", lambda ks: []), \
             patch.object(server, "_record_wave_outcome", lambda *a: None), \
             patch.object(server, "_poll_pending_demand_queries", fake_demand_poll), \
             patch.object(database, "get_tracked_categories_counts", lambda: {"rolling": 10, "hot": 0}), \
             patch.object(database, "get_due_tracked_categories", lambda limit, include_hot=True: [{"id": 1, "name": "Смартфоны", "query": "iphone"}]), \
             patch.object(database, "mark_tracked_category_scanned", lambda cid: None), \
             patch.object(search_engine, "search_live_stores", fake_search_live), \
             patch.dict(server.scan_state, {"wave_info": None}):
            asyncio.run(server._scan_task_body(["kari", "sulpak"], None, "auto", catchup))
        return tail_calls

    def test_catchup_skips_tracked_categories_and_demand_poll(self):
        tail = self._run_with_tail_spies({"kari": [KARI[1]["url"]]})
        self.assertEqual(tail["tracked_queries"], [])
        self.assertEqual(tail["demand_polls"], 0)

    def test_planned_wave_runs_tracked_categories_and_demand_poll(self):
        tail = self._run_with_tail_spies(None)
        self.assertEqual(tail["tracked_queries"], [("iphone", "Астана")])
        self.assertEqual(tail["demand_polls"], 1)

    def test_only_urls_filters_shop_categories(self):
        import web.server as server
        seen = []

        async def fake_categories(key, scraper, categories, shop_name, candidate_settings):
            seen.extend(c["url"] for c in categories)
            return len(categories)

        class FakeScraper:
            pass

        registry = {"kari": (FakeScraper, KARI, "Kari")}
        with patch.dict(server.SHOP_REGISTRY, registry), patch.object(server, "_scan_shop_categories", fake_categories):
            n = asyncio.run(server._scan_shop("kari", {}, asyncio.Semaphore(1), only_urls=[KARI[2]["url"]]))
        self.assertEqual((n, seen), (1, [KARI[2]["url"]]))


class SourceLastScansTest(unittest.TestCase):
    def test_returns_latest_row_per_source(self):
        import database
        database.init_db()
        with database.get_connection() as conn:
            conn.execute("DELETE FROM source_scans WHERE shop_key='catchup_t'")
            for url, started, quality in (("u1", "2026-11-20T01:00:00+00:00", "failed"),
                                          ("u1", "2026-11-20T02:00:00+00:00", "ok"),
                                          ("u2", "2026-11-19T00:00:00+00:00", "degraded")):
                conn.execute("""INSERT INTO source_scans (shop_key, source_url, started_at, finished_at, kind, quality)
                                VALUES ('catchup_t', ?, ?, ?, 'complete', ?)""", (url, started, started, quality))
            conn.commit()
        self.assertEqual(database.get_source_last_scans(["catchup_t"]),
                         {("catchup_t", "u1"): ("2026-11-20T02:00:00+00:00", "ok"),
                          ("catchup_t", "u2"): ("2026-11-19T00:00:00+00:00", "degraded")})
        self.assertEqual(database.get_source_last_scans([]), {})


class MonitorSourceTest(unittest.TestCase):
    def test_monitor_no_longer_runs_waves_for_stale_shops(self):
        import inspect
        import web.server as server
        src = inspect.getsource(server.auto_scan_background_worker)
        rolling = src.split('if wave_mode == "rolling":', 1)[1].split("else:\n                max_age_seconds", 1)[0]
        self.assertNotIn("get_stale_shops", rolling)
        self.assertIn("catchup_sources=catchup", rolling)


if __name__ == "__main__":
    unittest.main()
