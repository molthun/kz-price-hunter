"""R-H01: обязательный префикс магазина в id и миграция 5 без пересборки каталога. Без сети."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""

import config  # noqa: E402
import database  # noqa: E402
from database import get_connection  # noqa: E402
from offer_identity import assign_offer_ids, id_prefix, offer_id  # noqa: E402


class BoundaryTest(unittest.TestCase):
    def test_same_raw_id_in_two_shops_gives_two_offers(self):
        a, b = assign_offer_ids([{"id": "12345", "shop": "Zeta", "city": "Казахстан", "price": 20000},
                                 {"id": "12345", "shop": "12 Месяцев", "city": "Казахстан", "price": 10000}])
        self.assertEqual((a["id"], b["id"]), ("zeta_12345@kz", "twelve_months_12345@kz"))

    def test_every_registered_shop_is_namespaced(self):
        seen = set()
        for key, name in config.SHOP_KEYS.items():
            with self.subTest(shop=key):
                oid = offer_id({"id": "777", "shop": name, "city": "Астана"})
                self.assertTrue(oid.startswith(id_prefix(key) + "_"), oid)
                self.assertNotIn(oid, seen)
                seen.add(oid)

    def test_legacy_prefixed_ids_unchanged(self):
        for raw, shop in (("td_77", "Технодом"), ("fc_1", "Forcecom"), ("4mobile_ab", "4mobile"),
                          ("forte_x", "Forte Market"), ("kaspi_1", "Kaspi Магазин"), ("shopkz_9", "Белый Ветер")):
            self.assertEqual(offer_id({"id": raw, "shop": shop, "city": "Астана"}), f"{raw}@astana")

    def test_idempotent(self):
        p = {"id": "5", "shop": "Arbuz", "city": "Алматы"}
        first = offer_id(p)
        self.assertEqual(offer_id(dict(p, id=first)), first)


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "prices.db"
        self.patches = [patch.object(database, "DB_PATH", db), patch("config.DATA_DIR", Path(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()  # схема 5 на пустой базе
        with get_connection() as conn:
            conn.execute("UPDATE schema_metadata SET value = '4' WHERE name = 'schema_version'")  # как база до 5.5.0
            now = time.time()

            def product(pid, shop, city, price, first):
                conn.execute("""INSERT INTO products (id, shop, city, title, url, current_price, first_seen_price,
                                min_price, max_price) VALUES (?, ?, ?, ?, 'https://x', ?, ?, ?, ?)""",
                             (pid, shop, city, f"Товар {pid}", price, first, min(price, first), max(price, first)))
            product("555@kz", "Лемана ПРО", "Казахстан", 7000, 7000)          # чистая запись нового магазина
            product("12345@kz", "12 Месяцев", "Казахстан", 10000, 20000)      # смешанная: писали Zeta и 12 Месяцев
            product("td_77@astana", "Технодом", "Астана", 300000, 300000)     # старый префикс — не трогать
            conn.executemany("INSERT INTO product_sources VALUES (?, ?, ?, 1)", [
                ("555@kz", "lemanapro", "https://lemanapro.kz/c"),
                ("12345@kz", "zeta", "zeta-cat"), ("12345@kz", "twelve_months", "https://12.kz/c"),
                ("td_77@astana", "technodom", "https://www.technodom.kz/c")])
            conn.executemany("INSERT INTO price_observations VALUES (?, ?, 0, ?)", [
                ("555@kz", 7000, "2026-09-19T10:00:00+00:00"), ("12345@kz", 20000, "2026-09-19T10:00:00+00:00"),
                ("12345@kz", 10000, "2026-09-19T11:00:00+00:00")])
            clean_alert = conn.execute("INSERT INTO alerts (product_id, alert_type, new_price) VALUES ('555@kz', 'SUPER_DISCOUNT', 7000)").lastrowid
            mixed_alert = conn.execute("INSERT INTO alerts (product_id, alert_type, new_price) VALUES ('12345@kz', 'SUPER_DISCOUNT', 10000)").lastrowid
            for alert_id, pid in ((clean_alert, "555@kz"), (mixed_alert, "12345@kz")):
                conn.execute("INSERT INTO notification_outbox (alert_id, user_id, payload, created_at) VALUES (?, 7, ?, ?)",
                             (alert_id, json.dumps({"product": {"id": pid}, "anomaly": {}}), now))
            conn.commit()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def rows(self, sql, *args):
        with get_connection() as conn:
            return [tuple(r) for r in conn.execute(sql, args)]

    def test_plan_is_read_only_and_finds_collision(self):
        with get_connection() as conn:
            plan = database.offer_namespace_plan(conn)
        self.assertEqual(sorted(new for _, new, _ in plan["renames"]), ["lemanapro_555@kz", "twelve_months_12345@kz"])
        self.assertEqual(plan["collided"], ["12345@kz"])
        self.assertEqual(self.rows("SELECT count(*) FROM products WHERE id = '555@kz'"), [(1,)])

    def test_migration_renames_links_and_resets_only_mixed_history(self):
        self.assertEqual(database.run_migrations(), [5])
        ids = sorted(r[0] for r in self.rows("SELECT id FROM products"))
        self.assertEqual(ids, ["lemanapro_555@kz", "td_77@astana", "twelve_months_12345@kz"])
        # Чистая запись: история, алерт и уведомление переехали
        self.assertEqual(self.rows("SELECT price FROM price_observations WHERE product_id = 'lemanapro_555@kz'"), [(7000,)])
        self.assertEqual(self.rows("SELECT count(*) FROM alerts WHERE product_id = 'lemanapro_555@kz'"), [(1,)])
        payloads = [json.loads(r[0]) for r in self.rows("SELECT payload FROM notification_outbox")]
        self.assertEqual([p["product"]["id"] for p in payloads], ["lemanapro_555@kz"])
        # Смешанная запись: история и алерты сброшены, цены — от текущей
        self.assertEqual(self.rows("SELECT count(*) FROM price_observations WHERE product_id = 'twelve_months_12345@kz'"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM alerts WHERE product_id = 'twelve_months_12345@kz'"), [(0,)])
        self.assertEqual(self.rows("SELECT first_seen_price, min_price, max_price FROM products WHERE id = 'twelve_months_12345@kz'"),
                         [(10000, 10000, 10000)])
        # Источники — каждый со своим префиксом магазина
        self.assertEqual(sorted(self.rows("SELECT product_id, shop_key FROM product_sources")), [
            ("lemanapro_555@kz", "lemanapro"), ("td_77@astana", "technodom"),
            ("twelve_months_12345@kz", "twelve_months"), ("zeta_12345@kz", "zeta")])
        # Старые префиксы не тронуты, повторный запуск ничего не делает
        self.assertEqual(database.run_migrations(), [])
        self.assertTrue(list(Path(self.tmp.name, "backups").glob("prices-pre-v5-*.db")))



class SchemaSafetyTest(unittest.TestCase):
    """R-M12: защита от более новой схемы, бэкап до изменений схемы, уникальные имена, проверка бэкапа."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "prices.db"
        self.patches = [patch.object(database, "DB_PATH", self.db), patch("config.DATA_DIR", self.dir)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _legacy_db(self, version):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE schema_metadata (name TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO schema_metadata VALUES ('schema_version', ?)", (str(version),))
        conn.execute("INSERT INTO schema_metadata VALUES ('identity_v2', '1')")
        conn.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
                        photo_url TEXT, settings TEXT NOT NULL DEFAULT '{}', is_blocked INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP, last_login_at TIMESTAMP)""")
        conn.execute("INSERT INTO users (id, first_name) VALUES (7, 'Юзер')")
        conn.commit()
        conn.close()

    def test_newer_schema_refuses_to_start(self):
        self._legacy_db(database.SCHEMA_VERSION + 1)
        with self.assertRaises(database.SchemaTooNew):
            database.init_db()
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertNotIn("products", tables)  # база не тронута

    def test_backup_taken_before_schema_changes(self):
        self._legacy_db(4)
        database.init_db()
        backups = list((self.dir / "backups").glob("prices-pre-v*.db"))
        self.assertEqual(len(backups), 1)
        conn = sqlite3.connect(backups[0])
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertNotIn("scheduler_lease", tables)   # копия исходной базы, до _create_schema
        self.assertIn("scheduler_lease", {r[0] for r in sqlite3.connect(self.db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")})

    def test_backup_names_unique_and_backup_restorable(self):
        database.init_db()
        with get_connection() as conn:
            conn.execute("INSERT INTO users (id, first_name) VALUES (8, 'Б')")
            conn.commit()
        first, second = database.backup_database("manual"), database.backup_database("manual")
        self.assertNotEqual(first, second)
        sys_path = str(Path(__file__).resolve().parent / "scripts")
        import sys
        sys.path.insert(0, sys_path)
        try:
            from backup_check import check_backup
        finally:
            sys.path.remove(sys_path)
        report = check_backup(first)
        self.assertTrue(report["ok"])
        self.assertEqual(report["schema_version"], database.SCHEMA_VERSION)
        self.assertEqual(report["counts"]["users"], 1)


if __name__ == "__main__":
    unittest.main()
