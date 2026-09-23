"""P10 Теневой планировщик: профили, приоритеты, ограничения, симуляция и отсутствие побочных действий."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

import database
import scheduler_shadow as sched
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def candidate(shop="kaspi", category="Смартфоны", **kw):
    base = {"shop": shop, "category": category, "age_hours": 24.0, "last_quality": "complete",
            "searches": 0, "unmet_searches": 0, "watches": 0, "price_changes_per_day": 0.0,
            "profile": sched.NORMAL, "target_hours": 6.0}
    base.update(kw)
    return base


class ProfileTest(unittest.TestCase):
    def test_no_observations_is_normal_not_a_verdict(self):
        profile, why = sched.source_profile({})
        self.assertEqual(profile, sched.NORMAL)
        self.assertIn("нет наблюдений", why)

    def test_errors_and_blocks_make_the_source_degraded(self):
        profile, why = sched.source_profile({"requests": 100, "errors": 30})
        self.assertEqual(profile, sched.DEGRADED)
        self.assertIn("30", why)
        profile, why = sched.source_profile({"requests": 100, "blocked": 10})
        self.assertEqual(profile, sched.DEGRADED)
        self.assertIn("притормозить", why)

    def test_slow_or_heavy_source_is_expensive(self):
        self.assertEqual(sched.source_profile({"requests": 50, "latency_p95_ms": 9000})[0], sched.EXPENSIVE)
        self.assertEqual(sched.source_profile({"requests": 50, "bytes_total": 500_000_000})[0], sched.EXPENSIVE)

    def test_fast_and_light_source_is_friendly(self):
        profile, _ = sched.source_profile({"requests": 200, "latency_p95_ms": 800, "bytes_total": 20_000_000})
        self.assertEqual(profile, sched.FRIENDLY)

    def test_degraded_source_is_pushed_back_not_forward(self):
        signals = dict(searches=100, unmet_searches=50, age_hours=48.0)
        healthy = sched.score(candidate(**signals), sched.NORMAL)["score"]
        degraded = sched.score(candidate(**signals), sched.DEGRADED)["score"]
        self.assertLess(degraded, healthy)


class ScoreTest(unittest.TestCase):
    def test_unmet_demand_weighs_more_than_plain_demand(self):
        wanted = sched.score(candidate(searches=50, unmet_searches=50))["score"]
        satisfied = sched.score(candidate(searches=50, unmet_searches=0))["score"]
        self.assertGreater(wanted, satisfied)

    def test_waiting_people_raise_priority(self):
        with_watches = sched.score(candidate(watches=10))["score"]
        without = sched.score(candidate(watches=0))["score"]
        self.assertGreater(with_watches, without)

    def test_stale_data_raises_priority(self):
        old = sched.score(candidate(age_hours=48.0))["score"]
        fresh = sched.score(candidate(age_hours=1.0))["score"]
        self.assertGreater(old, fresh)

    def test_incomplete_previous_scan_raises_priority(self):
        broken = sched.score(candidate(last_quality="degraded"))["score"]
        fine = sched.score(candidate(last_quality="complete"))["score"]
        self.assertGreater(broken, fine)

    def test_reason_names_the_strongest_signals(self):
        result = sched.score(candidate(searches=200, unmet_searches=150, age_hours=8.0))
        self.assertIn("не нашли", result["reason"])
        self.assertEqual(sorted(result["parts"]), sorted(result["parts"]))   # разбор доступен целиком

    def test_never_scanned_source_is_not_forgotten(self):
        result = sched.score(candidate(age_hours=None))
        self.assertIn("давно не обходили", result["parts"])


class PlanTest(unittest.TestCase):
    def test_minimum_interval_and_pause_are_respected(self):
        fresh = candidate(category="Свежая", age_hours=1.0)
        paused = candidate(shop="dns", category="Пауза", age_hours=50.0,
                           next_retry_at=NOW + datetime.timedelta(hours=2))
        due = candidate(shop="alser", category="Пора", age_hours=30.0)
        result = sched.plan([fresh, paused, due], now=NOW)
        self.assertEqual([i["category"] for i in result["plan"]], ["Пора"])
        reasons = {i["category"]: i["skip_reason"] for i in result["skipped"]}
        self.assertIn("минимальный интервал", reasons["Свежая"])
        self.assertIn("на паузе", reasons["Пауза"])

    def test_one_shop_does_not_take_the_whole_cycle(self):
        items = [candidate(category=f"Категория {i}", age_hours=40.0 + i) for i in range(6)]
        result = sched.plan(items, now=NOW, per_shop_limit=2)
        self.assertEqual(len(result["plan"]), 2)
        self.assertTrue(all("уже выбрано 2" in s["skip_reason"] for s in result["skipped"]))

    def test_plan_is_capped_and_explains_every_skip(self):
        items = [candidate(shop=f"shop{i}", category=f"Кат {i}", age_hours=40.0) for i in range(10)]
        result = sched.plan(items, now=NOW, max_sources=3)
        self.assertEqual(len(result["plan"]), 3)
        self.assertEqual(len(result["skipped"]), 7)
        self.assertTrue(all(s["skip_reason"] for s in result["skipped"]))

    def test_every_suggestion_explains_itself(self):
        result = sched.plan([candidate(searches=10, age_hours=40.0)], now=NOW)
        item = result["plan"][0]
        self.assertTrue(item["reason"])
        self.assertIn("не действие", result["note"])


class SimulationTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            candidate(shop="kaspi", category="Смартфоны", searches=300, unmet_searches=200, age_hours=10.0),
            candidate(shop="kaspi", category="Ноутбуки", searches=50, age_hours=20.0),
            candidate(shop="dns", category="Телевизоры", watches=15, age_hours=30.0),
            candidate(shop="alser", category="Холодильники", age_hours=100.0),
            candidate(shop="mechta", category="Игрушки", age_hours=5.0),
        ]

    def test_simulation_is_reproducible(self):
        first = sched.simulate(self.items, cycles=5, limit=2)
        second = sched.simulate(self.items, cycles=5, limit=2)
        self.assertEqual(first, second)

    def test_adaptive_covers_unmet_demand_and_waiting_people(self):
        result = sched.compare(self.items, cycles=5, limit=2)
        self.assertGreaterEqual(result["verdict"]["unmet_demand_covered"], 0)
        self.assertEqual(result["verdict"]["violations"], 0)
        self.assertIn("не доказывает", result["note"])

    def test_adaptive_never_breaks_the_minimum_interval(self):
        result = sched.simulate(self.items, cycles=8, limit=3, strategy="adaptive")
        self.assertEqual(result["violations"], [])

    def test_nobody_starves(self):
        """Источник без спроса всё равно обходится: иначе он не обновится никогда."""
        # 5 ч + 12 интервалов по 6 ч: порог 72 ч пересекается только после 12-го решения.
        result = sched.simulate(self.items, cycles=16, limit=2, strategy="adaptive")
        self.assertEqual(result["never_visited"], [])

    def test_baseline_strategy_is_measured_too(self):
        result = sched.compare(self.items, cycles=5, limit=2)
        self.assertIn("round_robin", result["results"])
        self.assertIn("adaptive", result["results"])


class NoSideEffectsTest(unittest.TestCase):
    """Приёмка этапа: теневой режим не совершает побочных действий."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()
        database.save_or_update_products_batch([
            {"id": "p1", "title": "Смартфон Apple iPhone 15 128GB", "price": 400000, "shop": "Kaspi",
             "city": "Астана", "url": "https://k/1", "category": "Смартфоны"}])
        database.record_source_scan(
            shop_key="kaspi", source_url="https://kaspi.kz/c", category="Смартфоны", scan_id="s1",
            started_at=NOW.isoformat(), kind="category",
            assessment={"quality": "ok", "reasons": [], "warnings": [], "learn": True,
                        "baseline": 10, "basis": "median"},
            metrics={"received": 10, "valid": 10, "rejected": 0, "duplicates": 0, "with_image": 10})
        # Обход был давно: планировщику важен возраст данных
        with database.get_connection() as conn:
            conn.execute("UPDATE source_scans SET finished_at = ?",
                         ((NOW - datetime.timedelta(hours=30)).isoformat(),))
            conn.commit()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def snapshot(self):
        tables = ("products", "alerts", "notification_outbox", "source_scans", "shop_scans",
                  "watch_events", "price_observations", "telemetry_events")
        with database.get_connection() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    def test_building_a_plan_changes_nothing(self):
        before = self.snapshot()
        candidates = database.scheduler_candidates(days=7, now=NOW)
        result = sched.plan(candidates, now=NOW)
        sched.compare(candidates, cycles=3, limit=2)
        self.assertEqual(self.snapshot(), before)
        self.assertIsInstance(result["plan"], list)

    def test_candidates_carry_real_signals(self):
        candidates = database.scheduler_candidates(days=7, now=NOW)
        self.assertTrue(candidates)
        item = candidates[0]
        self.assertEqual(item["shop"], "kaspi")
        self.assertGreater(item["age_hours"], 24.0)
        self.assertIn(item["profile"], sched.PROFILES)
        self.assertIn("target_hours", item)

    def test_no_network_calls_are_made(self):
        """Планирование не обходит магазины: сетевой слой не должен вызываться вовсе."""
        import scrapers.http as http_layer
        with patch.object(http_layer, "_limited", side_effect=AssertionError("сеть не должна вызываться")):
            candidates = database.scheduler_candidates(days=7, now=NOW)
            sched.plan(candidates, now=NOW)




class AuditRegressionTest(unittest.TestCase):
    def setUp(self):
        NoSideEffectsTest.setUp(self)

    def tearDown(self):
        NoSideEffectsTest.tearDown(self)

    def test_oldest_queue_and_elapsed_time(self):
        items = [candidate(shop='a', category='old', age_hours=100),
                 candidate(shop='b', category='other', age_hours=90)]
        result = sched.simulate(items, now=NOW, cycles=4, limit=1, strategy='round_robin')
        self.assertEqual(result['timeline'], [['a/old'], ['b/other'], ['a/old'], ['b/other']])
        self.assertEqual(result['avg_age_hours'], 9)
        self.assertEqual(result['violations'], [])
        self.assertEqual(items[0]['age_hours'], 100)

    def test_both_strategies_obey_cooldown_interval_and_shop_cap(self):
        items = [candidate(shop='busy', category=str(i), age_hours=100) for i in range(5)]
        items += [candidate(shop='paused', next_retry_at=NOW + datetime.timedelta(hours=30)),
                  candidate(shop='fresh', age_hours=0)]
        for strategy in sched.STRATEGIES:
            result = sched.simulate(items, now=NOW, cycles=2, limit=10, strategy=strategy)
            self.assertEqual(result['violations'], [])
            self.assertNotIn('paused/Смартфоны', result['visits'])
            self.assertNotIn('fresh/Смартфоны', result['timeline'][0])
            self.assertIn('fresh/Смартфоны', result['timeline'][1])
            self.assertLessEqual(sum(x.startswith('busy/') for x in result['timeline'][0]), 3)

    def test_overdue_sources_get_service_despite_persistent_demand(self):
        items = [candidate(shop=str(i), age_hours=72, searches=1000, unmet_searches=1000)
                 for i in range(2)]
        items += [candidate(shop='cold', age_hours=100), candidate(shop='never', age_hours=None)]
        result = sched.simulate(items, now=NOW, cycles=100, limit=1)
        self.assertEqual(result['never_visited'], [])
        cold_cycles = [i for i, chosen in enumerate(result['timeline']) if 'cold/Смартфоны' in chosen]
        self.assertGreater(len(cold_cycles), 1)
        self.assertLessEqual(max(b-a for a,b in zip(cold_cycles, cold_cycles[1:])), 16)
        self.assertEqual(result['violations'], [])

    def test_price_transitions_are_scoped_and_include_previous_price(self):
        with database.get_connection() as conn:
            conn.execute('DELETE FROM price_observations')
            for days, price, old in [(8,100,120), (6,100,130), (5,90,130), (4,90,140), (3,80,140)]:
                conn.execute('INSERT INTO price_observations VALUES (?, ?, ?, ?)',
                             ('p1', price, old, (NOW-datetime.timedelta(days=days)).isoformat()))
            conn.commit()
        for shop, category in [('sulpak','Смартфоны'), ('kaspi','Ноутбуки')]:
            database.record_source_scan(shop_key=shop, source_url='https://example.test/c', category=category,
                scan_id='audit', started_at=NOW.isoformat(), kind='category',
                assessment={'quality':'ok','reasons':[],'warnings':[],'learn':True,'baseline':10,'basis':'median'},
                metrics={'received':10,'valid':10,'rejected':0,'duplicates':0,'with_image':10})
        found = {(c['shop'],c['category']):c['price_changes_per_day']
                 for c in database.scheduler_candidates(now=NOW)}
        self.assertEqual(found[('kaspi','Смартфоны')], round(2/7,2))
        self.assertIsNone(found[('sulpak','Смартфоны')])
        self.assertIsNone(found[('kaspi','Ноутбуки')])


if __name__ == "__main__":
    unittest.main()
