"""P15 Копии и сроки хранения: настоящая проверка копии, повреждение, просрочка, репетиция восстановления."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import sqlite3
import tempfile
import threading
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

    def test_file_with_one_table_is_not_a_database(self):
        """Файл SQLite с таблицей товаров — ещё не рабочая база приложения (аудит L02)."""
        empty = os.path.join(self.tmp.name, "backups", "prices-empty.db")
        os.makedirs(os.path.dirname(empty), exist_ok=True)
        conn = sqlite3.connect(empty)
        conn.execute("CREATE TABLE products (id TEXT, title TEXT)")
        conn.execute("INSERT INTO products VALUES ('p1', 'Товар')")
        conn.execute("CREATE TABLE schema_metadata (name TEXT, value TEXT)")
        conn.commit()
        conn.close()
        result = bh.verify_backup(empty)
        self.assertFalse(result["ok"])
        self.assertIn("нет обязательных таблиц", result["error"])
        self.assertIn("users", result["missing_tables"])
        self.assertFalse(bh.restore_rehearsal(empty)["restored"])

    def test_backup_without_products_is_not_useful(self):
        path = self.make_backup()
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM products")
        conn.commit()
        conn.close()
        result = bh.verify_backup(path)
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
        with patch.object(bh, "_copy_with_deadline", side_effect=OSError("No space left on device")):
            result = bh.restore_rehearsal()
        self.assertFalse(result["restored"])
        self.assertIn("восстановить не удалось", result["error"])

    def test_verification_runs_once_a_day_for_the_same_copy(self):
        """Пауза в сутки считается по проверке этой же копии, а не по любой прошлой (аудит L01)."""
        path = self.make_backup()
        identity = bh.file_identity(path)
        self.assertTrue(bh.due_for_verification(checks=[], now=NOW))
        recent = [{**identity, "checked_at": (NOW - datetime.timedelta(hours=2)).isoformat(), "ok": 1}]
        self.assertFalse(bh.due_for_verification(checks=recent, now=NOW))
        old = [{**identity, "checked_at": (NOW - datetime.timedelta(hours=30)).isoformat(), "ok": 1}]
        self.assertTrue(bh.due_for_verification(checks=old, now=NOW))
        # Проверка другой копии паузу не даёт
        other = [{"file": "prices-другая.db", "file_size": 1, "file_mtime": 1.0,
                  "checked_at": (NOW - datetime.timedelta(hours=2)).isoformat(), "ok": 1}]
        self.assertTrue(bh.due_for_verification(checks=other, now=NOW))

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
        self.assertIn("ещё не проверялась", section["reason"])

    def test_passed_check_makes_it_healthy(self):
        self.make_backup()
        bh.verify_backups_if_due(now=NOW)
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.HEALTHY)
        self.assertIn("проверка пройдена", section["reason"])

    def test_failed_check_is_degraded(self):
        path = self.make_backup()
        identity = bh.file_identity(path)
        database.record_backup_check("restore", False, detail="файл не читается как база",
                                     file=identity["file"], file_size=identity["file_size"],
                                     file_mtime=identity["file_mtime"])
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


class AuditFixesTest(BackupVerificationTest):
    """Сценарии приёмки из аудита: L01 (чужая проверка), L02 (неполная схема), L03 (предел времени)."""

    def newer_file(self, name, content=b"not a database"):
        path = os.path.join(self.tmp.name, "backups", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        future = datetime.datetime.now().timestamp() + 60
        os.utime(path, (future, future))
        return path

    def test_new_broken_copy_does_not_inherit_an_old_success(self):
        """Успешная проверка прежней копии не делает зелёной новую испорченную (аудит L01)."""
        self.make_backup()
        self.assertTrue(bh.verify_backups_if_due(now=NOW)["restored"])
        self.assertEqual(monitoring.backup_section(now=NOW)["status"], monitoring.HEALTHY)

        self.newer_file("prices-latest-bad.db")
        section = monitoring.backup_section(now=NOW)
        self.assertNotEqual(section["status"], monitoring.HEALTHY)
        self.assertIn("ещё не проверялась", section["reason"])
        self.assertTrue(bh.due_for_verification(now=NOW), "новая копия должна вызвать проверку")
        # Последняя подтверждённая копия остаётся видимой отдельно
        self.assertTrue(section["last_confirmed"]["ok"])

    def test_replacing_a_copy_under_the_same_name_invalidates_the_check(self):
        path = self.make_backup()
        bh.verify_backups_if_due(now=NOW)
        self.assertEqual(monitoring.backup_section(now=NOW)["status"], monitoring.HEALTHY)
        with open(path, "wb") as f:
            f.write("подменили содержимое под тем же именем".encode("utf-8"))
        future = datetime.datetime.now().timestamp() + 60
        os.utime(path, (future, future))
        section = monitoring.backup_section(now=NOW)
        self.assertNotEqual(section["status"], monitoring.HEALTHY)

    def test_a_stale_check_of_a_fresh_copy_is_not_green(self):
        path = self.make_backup()
        identity = bh.file_identity(path)
        database.record_backup_check("restore", True, file=identity["file"],
                                     file_size=identity["file_size"], file_mtime=identity["file_mtime"],
                                     detail="давняя проверка",
                                     now=NOW - datetime.timedelta(days=5))
        section = monitoring.backup_section(now=NOW)
        self.assertEqual(section["status"], monitoring.UNKNOWN)
        self.assertIn("устарела", section["reason"])

    def test_unsupported_schema_version_is_rejected(self):
        """Копия неизвестной версии схемы не считается пригодной (аудит L02)."""
        path = self.make_backup()
        conn = sqlite3.connect(path)
        conn.execute("UPDATE schema_metadata SET value = '999' WHERE name = 'schema_version'")
        conn.commit()
        conn.close()
        result = bh.verify_backup(path)
        self.assertFalse(result["ok"])
        self.assertIn("не поддерживается", result["error"])

    def test_missing_schema_version_is_rejected(self):
        path = self.make_backup()
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM schema_metadata WHERE name = 'schema_version'")
        conn.commit()
        conn.close()
        result = bh.verify_backup(path)
        self.assertFalse(result["ok"])
        self.assertIn("не записана версия схемы", result["error"])

    def test_missing_required_tables_are_named(self):
        path = self.make_backup()
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE watches")
        conn.commit()
        conn.close()
        result = bh.verify_backup(path)
        self.assertFalse(result["ok"])
        self.assertIn("watches", result["missing_tables"])

    def test_missing_product_columns_are_named(self):
        broken = os.path.join(self.tmp.name, "backups", "prices-no-columns.db")
        os.makedirs(os.path.dirname(broken), exist_ok=True)
        source = self.make_backup()
        import shutil as _shutil
        _shutil.copy2(source, broken)
        conn = sqlite3.connect(broken)
        conn.execute("ALTER TABLE products RENAME TO products_old")
        conn.execute("CREATE TABLE products (id TEXT, title TEXT)")
        conn.execute("INSERT INTO products SELECT id, title FROM products_old")
        conn.commit()
        conn.close()
        result = bh.verify_backup(broken)
        self.assertFalse(result["ok"])
        self.assertIn("не хватает колонок", result["error"])
        self.assertIn("products", result["missing_columns"])

    def test_a_good_copy_answers_the_queries_the_app_makes(self):
        self.make_backup()
        result = bh.restore_rehearsal()
        self.assertTrue(result["restored"], result["error"])
        self.assertEqual(set(result["queries"]), {label for label, _ in bh.SMOKE_QUERIES})
        self.assertTrue(all(result["queries"].values()))

    def test_verification_respects_its_time_limit(self):
        """Заявленный предел времени действительно прерывает проверку (аудит L03)."""
        self.make_backup()
        real_copy = bh._copy_with_deadline

        def slow_copy(source, dest, deadline, chunk=8 * 1024 * 1024):
            import time as _time
            _time.sleep(0.05)
            return real_copy(source, dest, deadline, chunk)

        with patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.001), \
             patch.object(bh, "_copy_with_deadline", slow_copy):
            result = bh.restore_rehearsal()
        self.assertFalse(result["restored"])
        self.assertTrue(result["timed_out"])
        self.assertIn("не уложилась", result["error"])

    def test_slow_queries_are_interrupted_too(self):
        self.make_backup()
        with patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.0):
            result = bh.verify_backup(bh.list_backups()[0]["path"])
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("timed_out") or "не уложилась" in (result["error"] or ""))

    def test_timeout_is_recorded_as_a_failed_check(self):
        self.make_backup()
        with patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.0):
            bh.verify_backups_if_due(now=NOW)
        check = database.backup_checks(limit=1)[0]
        self.assertEqual(check["ok"], 0)
        self.assertIn("не уложилась", check["detail"])


class SchemaContractTest(BackupVerificationTest):
    """L02: таблица есть, а рабочих колонок нет — такая копия не годится."""

    def break_table(self, path, table, columns="id INTEGER"):
        conn = sqlite3.connect(path)
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"CREATE TABLE {table} ({columns})")
        conn.commit()
        conn.close()

    def test_missing_columns_of_every_critical_table_are_rejected(self):
        for table in ("watches", "users", "alerts", "sessions", "notification_outbox"):
            with self.subTest(table=table):
                self.tearDown()
                self.setUp()
                path = self.make_backup()
                self.break_table(path, table)
                result = bh.verify_backup(path)
                self.assertFalse(result["ok"], f"{table} без колонок должна отклоняться")
                self.assertIn(table, result["missing_columns"])
                self.assertFalse(bh.restore_rehearsal(path)["restored"])

    def test_watches_query_that_the_app_runs_is_checked(self):
        """Ровно тот случай из аудита: list_watches падал бы на такой копии."""
        path = self.make_backup()
        self.break_table(path, "watches")
        rehearsal = bh.restore_rehearsal(path)
        self.assertFalse(rehearsal["restored"])
        self.assertIn("колонок", rehearsal["error"])

    def test_empty_tables_are_not_a_failure(self):
        """У нового сервиса нет ни пользователей, ни наблюдений — это нормально."""
        path = self.make_backup()
        rehearsal = bh.restore_rehearsal(path)
        self.assertTrue(rehearsal["restored"], rehearsal["error"])
        self.assertTrue(all(v is True for v in rehearsal["queries"].values()))
        self.assertIn("наблюдения человека", rehearsal["queries"])


class HangingReadTest(BackupVerificationTest):
    """L03: зависшее чтение не должно удерживать обслуживание базы."""

    def test_caller_is_released_even_if_the_read_hangs(self):
        import time as _time
        self.make_backup()
        released = threading.Event()

        def hang(source, dest, deadline, chunk=8 * 1024 * 1024):
            released.wait(timeout=30)          # «зависшее» чтение

        with patch.object(bh, "_copy_with_deadline", hang):
            started = _time.monotonic()
            result = bh.restore_rehearsal_bounded(timeout=0.2)
            elapsed = _time.monotonic() - started
        self.assertLess(elapsed, 5, "обслуживание должно освободиться вовремя")
        self.assertFalse(result["restored"])
        self.assertTrue(result["timed_out"])
        self.assertTrue(result["still_running"])

        # Пока зависший поток жив, новая проверка не запускается
        second = bh.restore_rehearsal_bounded(timeout=0.2)
        self.assertTrue(second["still_running"])
        released.set()
        bh._running_thread.join(timeout=10)

    def test_timeout_is_recorded_as_a_failed_check_and_the_cycle_continues(self):
        import time as _time
        self.make_backup()
        released = threading.Event()

        def hang(source, dest, deadline, chunk=8 * 1024 * 1024):
            released.wait(timeout=30)

        with patch.object(bh, "_copy_with_deadline", hang), \
             patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.2):
            bh.verify_backups_if_due(now=NOW)
        check = database.backup_checks(limit=1)[0]
        self.assertEqual(check["ok"], 0)
        self.assertIn("не уложилась", check["detail"])
        released.set()
        if bh._running_thread:
            bh._running_thread.join(timeout=10)

    def test_normal_verification_still_runs_in_the_bounded_path(self):
        self.make_backup()
        result = bh.restore_rehearsal_bounded(timeout=30)
        self.assertTrue(result["restored"], result["error"])


class TimeoutIsNotHiddenTest(BackupVerificationTest):
    """L04: неудачная попытка проверки не прячется за прежним успехом."""

    def hang_copy(self, released):
        def hang(source, dest, deadline, chunk=8 * 1024 * 1024):
            released.wait(timeout=30)
        return hang

    def test_timeout_is_attributed_to_the_copy_it_tried(self):
        path = self.make_backup()
        bh.verify_backups_if_due(now=NOW)
        self.assertEqual(monitoring.backup_section(now=NOW)["status"], monitoring.HEALTHY)

        released = threading.Event()
        later = NOW + datetime.timedelta(hours=30)
        with patch.object(bh, "_copy_with_deadline", self.hang_copy(released)), \
             patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.2):
            bh.verify_backups_if_due(now=later)
        check = database.backup_checks(limit=1)[0]
        self.assertEqual(check["ok"], 0)
        self.assertEqual(check["file"], os.path.basename(path), "отказ привязан к проверявшейся копии")
        self.assertIsNotNone(check["file_size"])

        section = monitoring.backup_section(now=later)
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("не прошла", section["reason"])
        released.set()
        if bh._running_thread:
            bh._running_thread.join(timeout=10)

    def test_failed_attempt_without_identity_still_degrades(self):
        """Даже если о файле ничего не известно, свежий отказ виден, а не скрыт прошлым успехом."""
        self.make_backup()
        bh.verify_backups_if_due(now=NOW)
        database.record_backup_check("restore", False, detail="перечисление копий не ответило",
                                     now=NOW + datetime.timedelta(hours=1))
        section = monitoring.backup_section(now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("Последняя попытка", section["reason"])
        self.assertTrue(section["last_confirmed"]["ok"], "подтверждённая копия всё ещё видна")

    def test_a_later_success_clears_the_earlier_failure(self):
        self.make_backup()
        database.record_backup_check("restore", False, detail="временный сбой", now=NOW)
        bh.verify_backups_if_due(now=NOW + datetime.timedelta(hours=30))
        section = monitoring.backup_section(now=NOW + datetime.timedelta(hours=30))
        self.assertEqual(section["status"], monitoring.HEALTHY)


class BoundedDiscoveryTest(BackupVerificationTest):
    """L03: зависнуть может уже перечисление каталога — обслуживание всё равно освобождается."""

    def test_hanging_listing_does_not_hold_the_cycle(self):
        import time as _time
        self.make_backup()
        released = threading.Event()
        real_list = bh.list_backups

        def slow_list():
            released.wait(timeout=30)
            return real_list()

        with patch.object(bh, "list_backups", slow_list), \
             patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.2):
            started = _time.monotonic()
            bh.verify_backups_if_due(now=NOW)
            elapsed = _time.monotonic() - started
        self.assertLess(elapsed, 5, "перечисление копий тоже под ограничением")
        check = database.backup_checks(limit=1)[0]
        self.assertEqual(check["ok"], 0)
        released.set()
        if bh._running_thread:
            bh._running_thread.join(timeout=10)

    def test_repeated_calls_do_not_pile_up_tasks(self):
        self.make_backup()
        released = threading.Event()
        real_list = bh.list_backups

        def slow_list():
            released.wait(timeout=30)
            return real_list()

        with patch.object(bh, "list_backups", slow_list), \
             patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.2):
            for _ in range(3):
                bh.verify_backups_if_due(now=NOW)
        self.assertEqual(threading.active_count() - threading.active_count(), 0)
        alive = [t for t in threading.enumerate() if t.name == "backup-verify"]
        self.assertLessEqual(len(alive), 1, "одновременно идёт не больше одной проверки")
        released.set()
        if bh._running_thread:
            bh._running_thread.join(timeout=10)

    def test_access_restored_lets_later_checks_work(self):
        self.make_backup()
        released = threading.Event()
        real_list = bh.list_backups

        def slow_list():
            released.wait(timeout=30)
            return real_list()

        with patch.object(bh, "list_backups", slow_list), \
             patch.object(bh, "VERIFY_TIMEOUT_SECONDS", 0.2):
            bh.verify_backups_if_due(now=NOW)
        released.set()
        if bh._running_thread:
            bh._running_thread.join(timeout=10)
        result = bh.verify_backups_if_due(now=NOW + datetime.timedelta(hours=30))
        self.assertTrue(result["restored"], result.get("error"))
