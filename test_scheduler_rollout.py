"""P11 Контролируемое включение планировщика: шаги, откат, отсутствие роста агрессивности."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

import database
import scheduler_rollout as rollout
import scheduler_shadow as sched
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def candidate(shop, profile=sched.NORMAL, category="Смартфоны", age_hours=40.0, **kw):
    base = {"shop": shop, "category": category, "profile": profile, "age_hours": age_hours,
            "last_quality": "ok", "searches": 0, "unmet_searches": 0, "watches": 0,
            "price_changes_per_day": 0.0, "target_hours": 6.0}
    base.update(kw)
    return base


class StagesTest(unittest.TestCase):
    def test_default_is_off(self):
        self.assertEqual(rollout.stage_of({}), rollout.OFF)
        self.assertEqual(rollout.stage_of({"adaptive_scheduler_stage": "мимо"}), rollout.OFF)

    def test_forward_only_one_step_at_a_time(self):
        self.assertTrue(rollout.can_switch(rollout.OFF, rollout.CANARY_ONE)[0])
        ok, why = rollout.can_switch(rollout.OFF, rollout.ALL)
        self.assertFalse(ok)
        self.assertIn("нельзя перепрыгнуть", why)
        self.assertTrue(rollout.can_switch(rollout.CANARY_ONE, rollout.CANARY_FEW)[0])
        self.assertTrue(rollout.can_switch(rollout.CANARY_FEW, rollout.ALL)[0])

    def test_switching_back_is_always_allowed(self):
        for stage in (rollout.CANARY_ONE, rollout.CANARY_FEW, rollout.ALL):
            with self.subTest(stage=stage):
                self.assertTrue(rollout.can_switch(stage, rollout.OFF)[0])
        self.assertTrue(rollout.can_switch(rollout.ALL, rollout.CANARY_ONE)[0])

    def test_first_canary_is_the_friendliest_shop(self):
        candidates = [candidate("dns", sched.DEGRADED), candidate("kaspi", sched.FRIENDLY),
                      candidate("alser", sched.NORMAL), candidate("mechta", sched.EXPENSIVE)]
        self.assertEqual(rollout.canary_shops(rollout.CANARY_ONE, candidates), ["kaspi"])
        few = rollout.canary_shops(rollout.CANARY_FEW, candidates)
        self.assertEqual(few[:2], ["kaspi", "alser"])
        self.assertLessEqual(len(few), rollout.CANARY_FEW_LIMIT)
        self.assertEqual(len(rollout.canary_shops(rollout.ALL, candidates)), 4)
        self.assertEqual(rollout.canary_shops(rollout.OFF, candidates), [])

    def test_canary_choice_is_reproducible(self):
        candidates = [candidate(f"shop{i}", sched.NORMAL) for i in range(8)]
        first = rollout.canary_shops(rollout.CANARY_FEW, candidates)
        self.assertEqual(first, rollout.canary_shops(rollout.CANARY_FEW, list(reversed(candidates))))


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.candidates = [candidate("kaspi", sched.FRIENDLY, age_hours=50.0, unmet_searches=100),
                           candidate("dns", age_hours=45.0),
                           candidate("alser", age_hours=8.0)]
        self.baseline = ["dns", "alser", "kaspi"]

    def test_off_keeps_the_old_order_exactly(self):
        result = rollout.select_targets(rollout.OFF, self.baseline, self.candidates, now=NOW)
        self.assertEqual(result["targets"], self.baseline)
        self.assertFalse(result["changed"])

    def test_adaptive_order_never_adds_a_shop(self):
        """Агрессивность не растёт: новый порядок — подмножество прежней волны."""
        for stage in (rollout.CANARY_ONE, rollout.CANARY_FEW, rollout.ALL):
            with self.subTest(stage=stage):
                result = rollout.select_targets(stage, self.baseline, self.candidates, now=NOW)
                self.assertTrue(set(result["targets"]) <= set(self.baseline))
                self.assertEqual(len(result["targets"]), len(set(result["targets"])))

    def test_canary_shop_goes_first_when_it_is_needed_most(self):
        result = rollout.select_targets(rollout.CANARY_ONE, self.baseline, self.candidates, now=NOW)
        self.assertEqual(result["canary"], ["kaspi"])
        self.assertEqual(result["targets"][0], "kaspi")
        self.assertEqual(sorted(result["targets"]), sorted(self.baseline))

    def test_canary_shop_too_fresh_is_skipped_not_hurried(self):
        candidates = [candidate("kaspi", sched.FRIENDLY, age_hours=1.0)]
        result = rollout.select_targets(rollout.CANARY_ONE, ["kaspi", "dns"], candidates, now=NOW)
        self.assertNotIn("kaspi", result["targets"])
        self.assertIn("kaspi", result["skipped"])
        self.assertIn("dns", result["targets"])

    def test_shops_outside_the_canary_keep_the_old_behaviour(self):
        result = rollout.select_targets(rollout.CANARY_ONE, self.baseline, self.candidates, now=NOW)
        others = [s for s in result["targets"] if s != "kaspi"]
        self.assertEqual(others, ["dns", "alser"])

    def test_shop_missing_from_this_wave_is_not_dragged_in(self):
        result = rollout.select_targets(rollout.CANARY_ONE, ["dns"], self.candidates, now=NOW)
        self.assertEqual(result["targets"], ["dns"])
        self.assertFalse(result["changed"])


class RollbackRulesTest(unittest.TestCase):
    def before(self, **kw):
        base = {"error_share": 0.10, "block_share": 0.02, "completeness": 0.90,
                "requests": 500, "scans": 50, "enough_data": True}
        base.update(kw)
        return base

    def after(self, **kw):
        return self.before(**kw)

    def test_thresholds_are_the_agreed_ones(self):
        self.assertEqual(rollout.ERROR_GROWTH_LIMIT, 1.33)
        self.assertEqual(rollout.BLOCK_GROWTH_LIMIT, 1.33)
        self.assertEqual(rollout.COMPLETENESS_DROP_LIMIT, 0.10)

    def test_errors_growing_by_a_third_trigger_rollback(self):
        needed, why = rollout.should_rollback(self.before(), self.after(error_share=0.20))
        self.assertTrue(needed)
        self.assertIn("ошибок", why)

    def test_small_growth_does_not_trigger_rollback(self):
        needed, _ = rollout.should_rollback(self.before(), self.after(error_share=0.12))
        self.assertFalse(needed)

    def test_blocks_growing_trigger_rollback(self):
        needed, why = rollout.should_rollback(self.before(), self.after(block_share=0.05))
        self.assertTrue(needed)
        self.assertIn("лимиту", why)

    def test_completeness_falling_triggers_rollback(self):
        needed, why = rollout.should_rollback(self.before(), self.after(completeness=0.75))
        self.assertTrue(needed)
        self.assertIn("полнота", why)

    def test_completeness_falling_slightly_does_not(self):
        needed, _ = rollout.should_rollback(self.before(), self.after(completeness=0.85))
        self.assertFalse(needed)

    def test_too_little_data_is_not_a_verdict(self):
        needed, why = rollout.should_rollback(self.before(), self.after(enough_data=False))
        self.assertFalse(needed)
        self.assertIn("мало", why)

    def test_errors_appearing_from_zero_are_caught(self):
        needed, why = rollout.should_rollback(self.before(error_share=0.0), self.after(error_share=0.05))
        self.assertTrue(needed)

    def test_next_step_needs_observation_time_and_clean_metrics(self):
        ready, why = rollout.ready_for_next_step(rollout.CANARY_ONE, self.before(), self.after(),
                                                 NOW - datetime.timedelta(hours=2), NOW)
        self.assertFalse(ready)
        self.assertIn("из 24", why)
        ready, why = rollout.ready_for_next_step(rollout.CANARY_ONE, self.before(), self.after(),
                                                 NOW - datetime.timedelta(hours=30), NOW)
        self.assertTrue(ready)
        ready, why = rollout.ready_for_next_step(rollout.CANARY_ONE, self.before(),
                                                 self.after(error_share=0.30),
                                                 NOW - datetime.timedelta(hours=30), NOW)
        self.assertFalse(ready)
        self.assertIn("ухудшения", why)

    def test_last_stage_has_no_next(self):
        ready, why = rollout.ready_for_next_step(rollout.ALL, self.before(), self.after(),
                                                 NOW - datetime.timedelta(hours=48), NOW)
        self.assertFalse(ready)
        self.assertIn("последний шаг", why)


class RolloutStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir),
                        patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()
        self.candidates = [candidate("kaspi", sched.FRIENDLY), candidate("dns")]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def http(self, shop, requests, errors, blocked, day=None):
        day = day or NOW.strftime("%Y-%m-%d")
        with database.get_connection() as conn:
            conn.execute("""INSERT INTO telemetry_http_aggregates
                            (bucket_type, bucket_start, host, shop, total_requests, errors, status_429,
                             status_4xx, status_5xx)
                            VALUES ('day', ?, ?, ?, ?, ?, ?, 0, 0)""",
                         (day, f"{shop}.kz", shop, requests, errors, blocked))
            conn.commit()

    def scans(self, shop, total, good, day=None):
        moment = day or NOW
        with database.get_connection() as conn:
            for i in range(total):
                conn.execute("""INSERT INTO source_scans (shop_key, source_url, category, scan_id,
                                started_at, finished_at, kind, quality, received, valid, rejected,
                                with_image, accepted)
                                VALUES (?, ?, ?, ?, ?, ?, 'category', ?, 10, 10, 0, 10, 1)""",
                             (shop, f"https://{shop}.kz/c", "Смартфоны", f"s{i}",
                              moment.isoformat(), moment.isoformat(),
                              "ok" if i < good else "degraded"))
            conn.commit()

    def test_starting_a_stage_remembers_the_before_picture(self):
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        started = rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertEqual(started["canary"], ["kaspi"])
        before = rollout.baseline_metrics()
        self.assertEqual(before["requests"], 200)
        self.assertAlmostEqual(before["error_share"], 0.05)
        self.assertAlmostEqual(before["completeness"], 0.9)
        self.assertIsNotNone(rollout.started_at(NOW))

    def test_metrics_cover_only_the_canary_shops(self):
        self.http("kaspi", 100, 5, 0)
        self.http("dns", 100, 50, 0)
        self.scans("kaspi", 10, 10)
        data = rollout.metrics(["kaspi"], days=1, now=NOW)
        self.assertEqual(data["requests"], 100)
        self.assertAlmostEqual(data["error_share"], 0.05)

    def test_rollback_returns_the_old_order_and_records_why(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)
        record = rollout.rollback("выросли отказы по лимиту", now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.OFF)
        self.assertEqual(record["from_stage"], rollout.CANARY_ONE)
        self.assertEqual(rollout.last_rollback()["reason"], "выросли отказы по лимиту")

    def test_automatic_rollback_on_worsening_metrics(self):
        import config
        self.http("kaspi", 200, 4, 0)
        self.scans("kaspi", 10, 10)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        # Всё стало хуже: ошибок втрое больше
        with database.get_connection() as conn:
            conn.execute("UPDATE telemetry_http_aggregates SET errors = 60 WHERE shop = 'kaspi'")
            conn.commit()
        record = rollout.check_and_rollback(self.candidates, now=NOW)
        self.assertIsNotNone(record)
        self.assertEqual(rollout.stage_of(), rollout.OFF)
        self.assertIn("ошибок", record["reason"])

    def test_no_rollback_while_metrics_hold(self):
        import config
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertIsNone(rollout.check_and_rollback(self.candidates, now=NOW))
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)

    def test_stage_survives_a_restart(self):
        import config
        import importlib
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_FEW})
        rollout.start_stage(rollout.CANARY_FEW, self.candidates, now=NOW)
        importlib.reload(rollout)                    # «перезапуск» процесса
        self.assertEqual(rollout.stage_of(), rollout.CANARY_FEW)
        self.assertIsNotNone(rollout.started_at(NOW))
        self.assertIsNotNone(rollout.baseline_metrics())

    def test_stage_without_candidates_says_so(self):
        """Шаг включён, а истории обходов ещё нет — это состояние названо словами, а не прочерком."""
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        state = rollout.status([], now=NOW)
        self.assertEqual(state["canary"], [])
        self.assertFalse(state["ready_for_next"])
        self.assertIn("ещё не определены", state["ready_reason"])

    def test_status_explains_itself(self):
        import config
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        state = rollout.status(self.candidates, now=NOW)
        self.assertEqual(state["stage"], rollout.CANARY_ONE)
        self.assertEqual(state["canary"], ["kaspi"])
        self.assertEqual(state["next_stage"], rollout.CANARY_FEW)
        self.assertFalse(state["ready_for_next"])
        self.assertIn("наблюдения", state["ready_reason"] + state["note"])
        self.assertEqual(state["thresholds"]["error_growth"], rollout.ERROR_GROWTH_LIMIT)
        self.assertIn("вручную", state["note"])


class WorkerIntegrationTest(RolloutStateTest):
    """Настоящий путь волны: порядок меняется только при включённом шаге и никогда не добавляет обходы."""

    def test_wave_order_is_untouched_while_off(self):
        import web.server as server
        targets, note = server.apply_adaptive_order(["dns", "kaspi"])
        self.assertEqual(targets, ["dns", "kaspi"])
        self.assertEqual(note, "")

    def test_wave_order_changes_only_for_the_canary(self):
        import config
        import web.server as server
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(database, "scheduler_candidates", return_value=self.candidates):
            targets, note = server.apply_adaptive_order(["dns", "kaspi"])
        self.assertEqual(sorted(targets), ["dns", "kaspi"])
        self.assertEqual(targets[0], "kaspi")
        self.assertIn("пробных магазинов", note)

    def test_failure_of_the_new_order_never_breaks_the_wave(self):
        import config
        import web.server as server
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(database, "scheduler_candidates", side_effect=RuntimeError("база занята")):
            targets, note = server.apply_adaptive_order(["dns", "kaspi"])
        self.assertEqual(targets, ["dns", "kaspi"])
        self.assertEqual(note, "")

    def test_worker_rollback_hook_switches_off(self):
        import config
        import web.server as server
        self.http("kaspi", 200, 4, 0)
        self.scans("kaspi", 10, 10)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        with database.get_connection() as conn:
            conn.execute("UPDATE telemetry_http_aggregates SET errors = 60 WHERE shop = 'kaspi'")
            conn.commit()
        with patch.object(database, "scheduler_candidates", return_value=self.candidates):
            record = server.check_adaptive_rollback()
        self.assertIsNotNone(record)
        self.assertEqual(rollout.stage_of(), rollout.OFF)


if __name__ == "__main__":
    unittest.main()
