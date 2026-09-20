"""P06 Search Analytics: исход по качеству совпадения, приватность записи, формула успеха, срок хранения."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

import database
import monitoring
import search_analytics as sa
from config import DB_PATH


def offer(title, category="Видеокарты"):
    return {"title": title, "category": category, "current_price": 100000}


class ClassifyTest(unittest.TestCase):
    """Приёмка плана: реальные товары → FOUND, только кабели → WEAK, пусто → NOT_FOUND, сбой → ERROR."""

    def test_real_products_are_found(self):
        items = [offer("Видеокарта NVIDIA GeForce RTX 5090 32GB"), offer("Кабель для RTX 5090")]
        self.assertEqual(sa.classify("RTX 5090", items), sa.FOUND)

    def test_only_accessories_is_weak(self):
        items = [offer("Кабель для RTX 5090", "Кабели"), offer("Подставка для видеокарты RTX 5090", "Аксессуары")]
        self.assertEqual(sa.classify("RTX 5090", items), sa.WEAK)

    def test_unrelated_results_are_weak(self):
        # Строки есть, но совпадает лишь одно слово из трёх — это не ответ на запрос
        self.assertEqual(sa.classify("стиральная машина bosch", [offer("Машина радиоуправляемая", "Игрушки")]),
                         sa.WEAK)

    def test_empty_is_not_found_and_failure_is_error(self):
        self.assertEqual(sa.classify("RTX 5090", []), sa.NOT_FOUND)
        self.assertEqual(sa.classify("RTX 5090", None), sa.NOT_FOUND)
        self.assertEqual(sa.classify("RTX 5090", [offer("RTX 5090")], failed=True), sa.ERROR)

    def test_accessory_query_finds_accessories(self):
        """Если человек искал чехол, чехол — это успех, а не слабый ответ."""
        self.assertEqual(sa.classify("чехол iphone 15", [offer("Чехол для iPhone 15 силиконовый", "Чехлы")]),
                         sa.FOUND)

    def test_success_rate_formula_excludes_errors(self):
        counts = {sa.FOUND: 60, sa.WEAK: 20, sa.NOT_FOUND: 20, sa.ERROR: 100}
        self.assertEqual(sa.success_rate(counts), 60.0)          # ошибки не в знаменателе
        self.assertEqual(sa.error_rate(counts), 50.0)            # и показаны отдельно
        self.assertIsNone(sa.success_rate({sa.ERROR: 5}))        # без ответов доли успеха нет


class PrivacyTest(unittest.TestCase):
    def test_sensitive_queries_are_not_storable(self):
        for text in ("айфон +7 705 123 45 67", "заказ 1234567", "напиши на bogdanets@example.com",
                     "https://kaspi.kz/shop/p/telefon", "@molthun привет", "ул. Абая 15 доставка",
                     "а" * 100):
            with self.subTest(text=text):
                self.assertIsNone(sa.storable_text(text))

    def test_normal_queries_are_normalized(self):
        self.assertEqual(sa.storable_text("  RTX  5090!! "), "rtx 5090")
        self.assertEqual(sa.normalize("Стиральная   Машина, Bosch"), "стиральная машина bosch")

    def test_key_does_not_reveal_text(self):
        key = sa.query_key("iphone 15", "Астана")
        self.assertNotIn("iphone", key)
        self.assertEqual(key, sa.query_key("IPHONE  15", "астана"))      # один запрос — один ключ
        self.assertNotEqual(key, sa.query_key("iphone 15", "Алматы"))    # город разделяет счёт


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def stored_queries(self):
        with database.get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM search_queries")]

    def test_text_appears_only_from_the_third_repeat(self):
        for i in range(2):
            database.record_search("iphone 15", "Астана", "summary", sa.FOUND, 12)
        self.assertEqual(self.stored_queries(), [])                      # редкий запрос текстом не хранится
        self.assertEqual(database.search_totals(days=1)["total"], 2)     # но в общих числах он есть
        database.record_search("iphone 15", "Астана", "summary", sa.FOUND, 12)
        rows = self.stored_queries()
        self.assertEqual([r["normalized_query"] for r in rows], ["iphone 15"])
        self.assertEqual(rows[0]["searches"], 1)                         # считается с третьего раза

    def test_sensitive_text_never_stored_but_counted(self):
        for _ in range(5):
            database.record_search("позвоните +7 705 123 45 67", "Астана", "summary", sa.NOT_FOUND, 0)
        self.assertEqual(self.stored_queries(), [])
        totals = database.search_totals(days=1)
        self.assertEqual(totals["counts"][sa.NOT_FOUND], 5)

    def test_no_identifiers_in_stored_columns(self):
        for _ in range(3):
            database.record_search("наушники sony", "Алматы", "live", sa.WEAK, 3)
        with database.get_connection() as conn:
            stats_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_stats)")}
            query_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_queries)")}
        forbidden = {"user_id", "telegram_id", "ip", "session", "session_id", "chat_id"}
        self.assertEqual(stats_cols & forbidden, set())
        self.assertEqual(query_cols & forbidden, set())

    def test_totals_split_outcomes_and_sources(self):
        database.record_search("rtx 5090", "Астана", "summary", sa.FOUND, 4)
        database.record_search("rtx 5090", "Астана", "live", sa.ERROR, 0)
        database.record_search("тостер", "Астана", "summary", sa.NOT_FOUND, 0)
        totals = database.search_totals(days=1)
        self.assertEqual(totals["counts"], {sa.FOUND: 1, sa.WEAK: 0, sa.NOT_FOUND: 1, sa.ERROR: 1})
        self.assertEqual(totals["success_rate"], 50.0)        # ошибка не попала в «не найдено»
        self.assertEqual(totals["error_rate"], round(100 / 3, 1))
        self.assertEqual(totals["by_source"]["live"]["error"], 1)

    def test_city_scope(self):
        database.record_search("холодильник", "Астана", "summary", sa.FOUND, 5)
        database.record_search("холодильник", "Алматы", "summary", sa.NOT_FOUND, 0)
        self.assertEqual(database.search_totals(days=1, city="Астана")["counts"][sa.FOUND], 1)
        self.assertEqual(database.search_totals(days=1, city="Алматы")["counts"][sa.FOUND], 0)
        self.assertEqual(database.search_totals(days=1)["total"], 2)

    def test_bad_queries_are_sorted_by_trouble(self):
        for _ in range(4):
            database.record_search("нет такого товара", "Астана", "summary", sa.NOT_FOUND, 0)
        for _ in range(6):
            database.record_search("iphone 15", "Астана", "summary", sa.FOUND, 10)
        bad = database.search_queries(days=1, outcome="bad", limit=5)
        self.assertEqual(bad[0]["normalized_query"], "нет такого товара")
        self.assertEqual(bad[0]["success_rate"], 0.0)
        # Запрос, на который всегда есть ответ, в списке проблемных не появляется
        self.assertNotIn("iphone 15", [q["normalized_query"] for q in bad])
        top = database.search_queries(days=1, limit=5)
        self.assertEqual(top[0]["normalized_query"], "iphone 15")

    def test_retention_removes_old_rows(self):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=sa.RETENTION_DAYS + 5)
        for _ in range(3):
            database.record_search("старый запрос", "Астана", "summary", sa.FOUND, 1, now=old)
        database.record_search("свежий запрос", "Астана", "summary", sa.FOUND, 1)
        self.assertGreater(database.prune_search_analytics(), 0)
        with database.get_connection() as conn:
            left = [r["normalized_query"] for r in conn.execute("SELECT normalized_query FROM search_queries")]
            keys = conn.execute("SELECT COUNT(*) FROM search_query_seen").fetchone()[0]
        self.assertNotIn("старый запрос", left)
        self.assertEqual(keys, 1)                       # счётчик повторов тоже очищается
        self.assertEqual(database.search_totals(days=365)["total"], 1)

    def test_unknown_outcome_rejected(self):
        with self.assertRaises(ValueError):
            database.record_search("iphone", "Астана", "summary", "ok", 1)


class MonitoringSectionTest(RecordTest):
    def test_no_searches_is_unknown_not_green(self):
        section = monitoring.search_analytics()
        self.assertEqual(section["status"], monitoring.UNKNOWN)
        self.assertIsNone(section["success_rate"])

    def test_errors_make_the_section_degraded(self):
        for _ in range(3):
            database.record_search("iphone 15", "Астана", "summary", sa.FOUND, 5)
        for _ in range(3):
            database.record_search("iphone 15", "Астана", "live", sa.ERROR, 0)
        section = monitoring.search_analytics()
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("Ошибок", section["reason"])

    def test_weak_answers_make_the_section_limited(self):
        for _ in range(8):
            database.record_search("нет такого товара", "Астана", "summary", sa.NOT_FOUND, 0)
        for _ in range(2):
            database.record_search("iphone 15", "Астана", "summary", sa.FOUND, 5)
        section = monitoring.search_analytics()
        self.assertEqual(section["status"], monitoring.LIMITED)
        self.assertEqual(section["success_rate"], 20.0)

    def test_section_documents_the_formula(self):
        section = monitoring.search_analytics()
        self.assertIn("FOUND / (FOUND + WEAK + NOT_FOUND)", section["formula"])
        self.assertIn("ошибки", section["formula"].lower())


if __name__ == "__main__":
    unittest.main()
