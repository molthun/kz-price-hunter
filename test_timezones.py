"""Сервис обязан работать и без системной базы часовых поясов (регрессия 5.13.0)."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import unittest
import zoneinfo
from unittest.mock import patch

import timezones

UTC = datetime.timezone.utc


def _no_tzdata(name, *args, **kwargs):
    raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {name}")


class WithoutTzdataTest(unittest.TestCase):
    """В образе базы часовых поясов может не быть: ZoneInfo падает даже на «UTC»."""

    def setUp(self):
        self.patch = patch.object(zoneinfo, "ZoneInfo", _no_tzdata)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_zone_falls_back_to_utc(self):
        self.assertEqual(timezones.zone("Asia/Almaty"), UTC)
        self.assertEqual(timezones.zone("UTC"), UTC)
        self.assertEqual(timezones.zone(None), UTC)

    def test_availability_is_reported_honestly(self):
        self.assertFalse(timezones.available())
        self.assertIsNone(timezones.known("Asia/Almaty"), "«не могу проверить» — это не «неверно»")

    def test_settings_still_save(self):
        """Главное следствие регрессии: сохранение любых системных настроек переставало работать."""
        import config
        clean = config._validate_settings({"daily_digest_timezone": "Asia/Almaty"})
        self.assertEqual(clean["daily_digest_timezone"], "Asia/Almaty")

    def test_daily_report_window_is_computed_in_utc(self):
        import daily_digest
        start, end = daily_digest.day_window("2026-09-23", "Asia/Almaty")
        self.assertEqual(start.isoformat(), "2026-09-23T00:00:00+00:00")
        self.assertEqual(end - start, datetime.timedelta(days=1))

    def test_quiet_hours_do_not_break_notifications(self):
        import watches
        watch = {"quiet_from": "23:00", "quiet_to": "08:00", "timezone": "Asia/Almaty"}
        moment = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        self.assertFalse(watches.in_quiet_hours(watch, moment))

    def test_a_watch_with_a_timezone_can_still_be_created(self):
        import watches
        watch = watches.normalize({"kind": "product", "target": "p1", "condition": "any_drop",
                                   "timezone": "Asia/Almaty"})
        self.assertEqual(watch["timezone"], "Asia/Almaty")


class WithTzdataTest(unittest.TestCase):
    """Когда база есть, проверка имён остаётся строгой."""

    def test_known_names_are_confirmed(self):
        self.assertTrue(timezones.available())
        self.assertIs(timezones.known("Asia/Almaty"), True)

    def test_wrong_name_is_still_refused(self):
        import config
        import watches
        self.assertIs(timezones.known("Nowhere/Nothing"), False)
        with self.assertRaises(ValueError):
            config._validate_settings({"daily_digest_timezone": "Nowhere/Nothing"})
        with self.assertRaises(ValueError):
            watches.normalize({"kind": "product", "target": "p1", "condition": "any_drop",
                               "timezone": "Nowhere/Nothing"})

    def test_real_offset_is_used_when_available(self):
        import daily_digest
        start, _ = daily_digest.day_window("2026-09-23", "Asia/Almaty")
        self.assertEqual(start.isoformat(), "2026-09-22T19:00:00+00:00")

    def test_tzdata_is_declared_as_a_dependency(self):
        """База часовых поясов должна ставиться вместе с зависимостями, а не «как повезёт»."""
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
        with open(path, encoding="utf-8") as fh:
            self.assertIn("tzdata", fh.read())


if __name__ == "__main__":
    unittest.main()
