"""P15 Копии и сроки хранения: настоящая проверка копии, повреждение, просрочка, репетиция восстановления."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import backup_health as bh
import database
import monitoring
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class BackupVerificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir)]
        for p in self.patches:
            p.start()
        database.init_db()
        database.save_or_update_products_batch([
            {"id": "p1", "title": "Смартфон Apple iPhone 15 128GB", "price": 400000, "shop": "Kaspi",
             "city": "Астана", "url": "https://k/1", "category": "Смартфоны"}])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def make_backup(self):
        path = database.backup_database("test")
        self.assertIsNotNone(path)
        return path

    def test_real_backup_passes_verification(self):
        path = self.make_backup()
        result = bh.verify_backup(path)
        self.assertTrue(result["ok"], result["error"])
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(result["counts"]["products"], 1)
        self.assertIsNotNone(result["schema_version"])

    def test_corrupted_backup_is_caught(self):
        """Испорченный файл не должен считаться копией."""
        path = self.make_backup()
        with open(path, "r+b") as f:
            f.seek(200)
            f.write(b"\x00" * 4096)
        result = bh.verify_backup(path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_not_a_database_is_caught(self):
        fake = os.path.join(self.tmp.name, "backups", "prices-fake.db")
        os.makedirs(os.path.dirname(fake), exist_ok=True)
        with open(fake, "w", encoding="utf-8") as f:
            f.write("это просто текст, а не база")
        result = bh.verify_backup(fake)
        self.assertFalse(result["ok"])
        self.assertIn("не читается как база", result["error"])

    def test_empty_backup_is_not_useful(self):
        empty = os.path.join(self.tmp.name, "backups", "prices-empty.db")
        os.makedirs(os.path.dirname(empty), exist_ok=True)
        conn = sqlite3.connect(empty)
        conn.execute("CREATE TABLE products (id TEXT)")
        conn.execute("CREATE TABLE schema_metadata (name TEXT, value TEXT)")
        conn.commit()
        conn.close()
        result = bh.verify_backup(empty)
        self.assertFalse(result["ok"])
        self.assertIn("нет товаров", result["error"])

    def test_missing_file(self):
        result = bh.verify_backup(os.path.join(self.tmp.name, "нет-такого.db"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "файла нет")

    def test_restore_rehearsal_actually_restores_and_cleans_up(self):
        """Копия разворачивается во временную базу, читается — и временная база удаляется."""
        self.make_backup()
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*a, **kw):
            path = real_mkdtemp(*a, **kw)
            created.append(path)
            return path

        with patch.object(bh.tempfile, "mkdtemp", spy):
            result = bh.restore_rehearsal()
        self.assertTrue(result["restored"], result["error"])
        self.assertTrue(result["sample_readable"])
        self.assertEqual(result["checks"]["counts"]["products"], 1)
        self.assertTrue(created)
        for path in created:
            self.assertFalse(os.path.exists(path), "временная база должна быть удалена")

    def test_restore_rehearsal_reports_a_broken_backup(self):
        path = self.make_backup()
        with open(path, "r+b") as f:
            f.seek(200)
            f.write(b"\x00" * 4096)
        result = bh.restore_rehearsal()
        self.assertFalse(result["restored"])
        self.assertTrue(result["error"])

    def test_no_backups_is_said_plainly(self):
        result = bh.restore_rehearsal()
        self.assertFalse(result["restored"])
        self.assertEqual(result["error"], "копий нет")

    def test_full_disk_does_not_crash_the_check(self):
        self.make_backup()
        with patch.object(bh.shutil, "copy2", side_effect=OSError("No space left on device")):
            result = bh.restore_rehearsal()
        self.assertFalse(result["restored"])
        self.assertIn("восстановить не удалось", result["error"])

    def test_verification_runs_once_a_day(self):
        self.make_backup()
        self.assertTrue(bh.due_for_verification(checks=[], now=NOW))
        recent = [{"checked_at": (NOW - datetime.timedelta(hours=2)).isoformat()}]
        self.assertFalse(bh.due_for_verification(checks=recent, now=NOW))
        old = [{"checked_at": (NOW - datetime.timedelta(hours=30)).isoformat()}]
        self.assertTrue(bh.due_for_verification(checks=old, now=NOW))

    def test_periodic_check_records_result_and_heartbeat(self):
        import environment as env
        self.make_backup()
        result = bh.verify_backups_if_due(now=NOW)
        self.assertTrue(result["restored"])
        checks = database.backup_checks(limit=5)
        self.assertEqual(checks[0]["ok"], 1)
        self.assertIn(env.BACKUP, database.heartbeats())
        # Второй раз в те же сутки проверка не запускается
        self.assertIsNone(bh.verify_backups_if_due(now=NOW))

    def test_check_failure_never_breaks_maintenance(self):
        self.make_backup()
        with patch.object(bh, "restore_rehearsal", side_effect=RuntimeError("диск отвалился")):
            bh.verify_backups_if_due(now=NOW)          # не должно бросить
        self.assertEqual(database.backup_checks(limit=1)[0]["ok"], 0)


class RetentionTest(BackupVerificationTest):
    def test_policy_follows_the_code_not_a_separate_list(self):
        import data_quality
        import search_analytics
        import telemetry
        policy = {row["data"]: row["days"] for row in bh.retention_policy()}
        self.assertEqual(policy["События телеметрии"], telemetry.RETENTION_DAYS["events"])
        self.assertEqual(policy["Аналитика поиска"], search_analytics.RETENTION_DAYS)
        self.assertEqual(policy["История качества обходов"], data_quality.SOURCE_SCANS_RETENTION_DAYS)
        self.assertEqual(policy["История цен"], database.PRICE_HISTORY_RETENTION_DAYS)
        for row in bh.retention_policy():
            self.assertTrue(row["why"], "у каждого срока должна быть причина")
        # Политика и фактическое потребление связаны таблицей, а не совпадением названий
        tables = {row["table"] for row in database.retention_usage()}
        for row in bh.retention_policy():
            if row.get("table"):
                with self.subTest(data=row["data"]):
                    self.assertIn(row["table"], tables)

    def test_usage_shows_what_the_data_actually_costs(self):
        usage = {row["data"]: row for row in database.retention_usage()}
        self.assertIn("История цен", usage)
        self.assertIsInstance(usage["История цен"]["rows"], int)


class BackupSectionTest(BackupVerificationTest):
    def test_no_backups_is_unknown_not_green(self):
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.UNKNOWN)
        self.assertIn("Копий пока нет", section["reason"])

    def test_backup_without_a_check_is_not_green_either(self):
        """«Копия есть» ещё не значит «копия пригодна»."""
        self.make_backup()
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.UNKNOWN)
        self.assertIn("ни одна ещё не проверялась", section["reason"])

    def test_passed_check_makes_it_healthy(self):
        self.make_backup()
        bh.verify_backups_if_due(now=NOW)
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.HEALTHY)
        self.assertIn("проверка пройдена", section["reason"])

    def test_failed_check_is_degraded(self):
        self.make_backup()
        database.record_backup_check("restore", False, detail="файл не читается как база")
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("не прошла", section["reason"])

    def test_stale_backup_is_degraded(self):
        path = self.make_backup()
        old = datetime.datetime.now().timestamp() - bh.BACKUP_STALE_HOURS * 3600 - 3600
        os.utime(path, (old, old))
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("Свежей копии нет", section["reason"])

    def test_section_admits_what_it_cannot_see(self):
        section = monitoring.backup_section(now=NOW)
        self.assertIn("не заметит полную остановку процесса", section["note"])
        self.assertIn("retention", section)
        self.assertIn("usage", section)


if __name__ == "__main__":
    unittest.main()
