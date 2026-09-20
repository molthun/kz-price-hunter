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
            database.record_search("iphone 15", "Астана", "catalog", sa.FOUND, 12)
        self.assertEqual(self.stored_queries(), [])                      # редкий запрос текстом не хранится
        self.assertEqual(database.search_totals(days=1)["total"], 2)     # но в общих числах он есть
        database.record_search("iphone 15", "Астана", "catalog", sa.FOUND, 12)
        rows = self.stored_queries()
        self.assertEqual([r["normalized_query"] for r in rows], ["iphone 15"])
        self.assertEqual(rows[0]["searches"], 1)                         # считается с третьего раза

    def test_sensitive_text_never_stored_but_counted(self):
        for _ in range(5):
            database.record_search("позвоните +7 705 123 45 67", "Астана", "catalog", sa.NOT_FOUND, 0)
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
        database.record_search("rtx 5090", "Астана", "catalog", sa.FOUND, 4)
        database.record_search("rtx 5090", "Астана", "live", sa.ERROR, 0)
        database.record_search("тостер", "Астана", "catalog", sa.NOT_FOUND, 0)
        totals = database.search_totals(days=1)
        self.assertEqual(totals["counts"], {sa.FOUND: 1, sa.WEAK: 0, sa.NOT_FOUND: 1, sa.ERROR: 1})
        self.assertEqual(totals["success_rate"], 50.0)        # ошибка не попала в «не найдено»
        self.assertEqual(totals["error_rate"], round(100 / 3, 1))
        self.assertEqual(totals["by_source"]["live"]["error"], 1)

    def test_city_scope(self):
        database.record_search("холодильник", "Астана", "catalog", sa.FOUND, 5)
        database.record_search("холодильник", "Алматы", "catalog", sa.NOT_FOUND, 0)
        self.assertEqual(database.search_totals(days=1, city="Астана")["counts"][sa.FOUND], 1)
        self.assertEqual(database.search_totals(days=1, city="Алматы")["counts"][sa.FOUND], 0)
        self.assertEqual(database.search_totals(days=1)["total"], 2)

    def test_bad_queries_are_sorted_by_trouble(self):
        for _ in range(4):
            database.record_search("нет такого товара", "Астана", "catalog", sa.NOT_FOUND, 0)
        for _ in range(6):
            database.record_search("iphone 15", "Астана", "catalog", sa.FOUND, 10)
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
            database.record_search("старый запрос", "Астана", "catalog", sa.FOUND, 1, now=old)
        database.record_search("свежий запрос", "Астана", "catalog", sa.FOUND, 1)
        self.assertGreater(database.prune_search_analytics(), 0)
        with database.get_connection() as conn:
            left = [r["normalized_query"] for r in conn.execute("SELECT normalized_query FROM search_queries")]
            keys = conn.execute("SELECT COUNT(*) FROM search_query_seen").fetchone()[0]
        self.assertNotIn("старый запрос", left)
        self.assertEqual(keys, 1)                       # счётчик повторов тоже очищается
        self.assertEqual(database.search_totals(days=365)["total"], 1)

    def test_unknown_outcome_rejected(self):
        with self.assertRaises(ValueError):
            database.record_search("iphone", "Астана", "catalog", "ok", 1)


class MonitoringSectionTest(RecordTest):
    def test_no_searches_is_unknown_not_green(self):
        section = monitoring.search_analytics()
        self.assertEqual(section["status"], monitoring.UNKNOWN)
        self.assertIsNone(section["success_rate"])

    def test_errors_make_the_section_degraded(self):
        for _ in range(3):
            database.record_search("iphone 15", "Астана", "catalog", sa.FOUND, 5)
        for _ in range(3):
            database.record_search("iphone 15", "Астана", "live", sa.ERROR, 0)
        section = monitoring.search_analytics()
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("Ошибок", section["reason"])

    def test_weak_answers_make_the_section_limited(self):
        for _ in range(8):
            database.record_search("нет такого товара", "Астана", "catalog", sa.NOT_FOUND, 0)
        for _ in range(2):
            database.record_search("iphone 15", "Астана", "catalog", sa.FOUND, 5)
        section = monitoring.search_analytics()
        self.assertEqual(section["status"], monitoring.LIMITED)
        self.assertEqual(section["success_rate"], 20.0)

    def test_section_documents_the_formula(self):
        section = monitoring.search_analytics()
        self.assertIn("FOUND / (FOUND + WEAK + NOT_FOUND)", section["formula"])
        self.assertIn("ошибки", section["formula"].lower())


if __name__ == "__main__":
    unittest.main()


class AuditFixesTest(RecordTest):
    """Сквозные регрессии на замечания аудита F01–F04: настоящие обёртки поиска, без сети."""

    def test_f01_arbitrary_city_never_stored(self):
        """Город из запроса приводится к справочнику: произвольный текст никуда не попадает."""
        for city in ("audit@example.invalid", "+7 705 123 45 67", "ул. Абая 15", "Мордор"):
            with self.subTest(city=city):
                for _ in range(3):
                    database.record_search("rtx 5090", city, "catalog", sa.FOUND, 3)
        with database.get_connection() as conn:
            dump = " | ".join(str(r[0]) for r in conn.execute("SELECT city FROM search_stats")) + " | " + \
                   " | ".join(str(r[0]) for r in conn.execute("SELECT city FROM search_queries"))
        for part in ("audit@example.invalid", "705", "Абая", "Мордор"):
            self.assertNotIn(part, dump)
        self.assertEqual(set(dump.split(" | ")), {sa.CITY_UNKNOWN})
        # Известный город сохраняется читаемым названием из справочника
        database.record_search("rtx 5090", "almaty", "catalog", sa.FOUND, 3)
        self.assertIn("Алматы", [r["city"] for r in database.search_queries(days=1, limit=10)] +
                      [sa.canonical_city("almaty")])

    def test_f01_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            database.record_search("rtx 5090", "Астана", "telegram-bot", sa.FOUND, 1)

    def test_f02_wrong_model_is_not_success(self):
        """Названная человеком модель обязана совпасть, иначе это не успех."""
        cases = [
            ("nvidia rtx 5090", "Видеокарта NVIDIA RTX 4060", sa.WEAK),
            ("nvidia rtx 5090", "Видеокарта NVIDIA GeForce RTX 5090 32GB", sa.FOUND),
            ("iphone 15 pro 256gb", "Apple iPhone 15 128GB", sa.WEAK),
            ("iphone 15 pro 256gb", "Apple iPhone 15 Pro 256GB", sa.FOUND),
            ("iphone 15 pro", "Apple iPhone 15 128GB", sa.WEAK),
            ("наушники sony wh-1000xm5", "Наушники Sony WH-1000XM4", sa.WEAK),
            ("наушники sony wh-1000xm5", "Наушники Sony WH-1000XM5 чёрные", sa.FOUND),
            ("стиральная машина bosch", "Стиральная машина Bosch WAN 24", sa.FOUND),
            ("чехол iphone 15", "Чехол для iPhone 15 силиконовый", sa.FOUND),
        ]
        for query, title, expected in cases:
            with self.subTest(query=query, title=title):
                self.assertEqual(sa.classify(query, [offer(title)]), expected)

    def test_f03_live_failure_is_error_not_absence(self):
        """Все источники упали — это ошибка, а не «товара нет»; сбой не кэшируется как пустая выдача."""
        import asyncio
        import search_engine
        from bounded_cache import BoundedTTLCache

        async def boom(query, city):
            raise TimeoutError("источник не ответил")

        calls = {"n": 0}

        async def live(query, city):
            calls["n"] += 1
            return [], False, {"attempted": 1, "failed": 1}

        with patch.object(search_engine, "_LIVE_CACHE", BoundedTTLCache(maxsize=10, ttl=60)), \
             patch.object(search_engine, "_search_live_stores", live):
            items = asyncio.run(search_engine.search_live_stores("rtx 5090", city="Астана",
                                                                 analytics_source="live"))
            self.assertEqual(items, [])
            self.assertEqual(database.search_totals(days=1)["counts"][sa.ERROR], 1)
            self.assertEqual(database.search_totals(days=1)["counts"][sa.NOT_FOUND], 0)

        # Источники ответили, товара действительно нет — это «не найдено»
        async def empty(query, city):
            return [], False, {"attempted": 2, "failed": 0}

        with patch.object(search_engine, "_search_live_stores", empty):
            asyncio.run(search_engine.search_live_stores("редкий товар", city="Астана", analytics_source="live"))
        self.assertEqual(database.search_totals(days=1)["counts"][sa.NOT_FOUND], 1)

    def test_f03_failed_source_is_not_cached_as_empty(self):
        """Пустой ответ из-за сбоя не попадает в кэш: следующий поиск снова опрашивает источники."""
        import asyncio
        import search_engine
        from bounded_cache import BoundedTTLCache

        attempts = {"n": 0}

        class Boom:
            def __init__(self, *a, **kw):
                pass

            async def search(self, query, max_items=15):
                attempts["n"] += 1
                raise TimeoutError("источник не ответил")

            def close(self):
                pass

        with patch.object(search_engine, "_LIVE_CACHE", BoundedTTLCache(maxsize=10, ttl=600)), \
             patch.object(search_engine, "KaspiScraper", Boom), \
             patch.object(search_engine, "load_settings",
                          lambda: {"enabled_shops": {"kaspi": True, "fourmobile": False, "fortemarket": False}}):
            for _ in range(2):
                items, cached, health = asyncio.run(search_engine._search_live_stores("rtx 5090", "Астана"))
                self.assertEqual((items, cached), ([], False))
                self.assertEqual((health["attempted"], health["failed"]), (1, 1))
        self.assertEqual(attempts["n"], 2)    # второй раз источник опрошен снова, а не отдан из кэша

    def test_f04_one_user_search_is_counted_once(self):
        """Одно обращение человека — одна запись, даже когда внутри обновляются живые цены."""
        import asyncio
        import search_engine

        async def fake_summary(query, live=False, **kw):
            if live:
                await search_engine.search_live_stores(query, city=kw.get("city") or "Астана")
            return {"total_found": 1, "items": [{"title": "Видеокарта NVIDIA RTX 5090 32GB"}]}

        async def live(query, city):
            return [{"title": "Видеокарта NVIDIA RTX 5090 32GB"}], False, {"attempted": 1, "failed": 0}

        with patch.object(search_engine, "_get_best_price_summary", fake_summary), \
             patch.object(search_engine, "_search_live_stores", live):
            for _ in range(2):
                asyncio.run(search_engine.get_best_price_summary("rtx 5090", live=True, user_search=True,
                                                                city="Астана"))
            totals = database.search_totals(days=1)
            self.assertEqual(totals["total"], 2)                     # два обращения — два учёта, не четыре
            self.assertEqual(self.stored_queries(), [])              # порог третьего повтора не обойдён
            asyncio.run(search_engine.get_best_price_summary("rtx 5090", live=True, user_search=True,
                                                            city="Астана"))
            self.assertEqual(database.search_totals(days=1)["total"], 3)
            self.assertEqual([r["normalized_query"] for r in self.stored_queries()], ["rtx 5090"])

    def test_f04_background_calls_are_not_demand(self):
        """Фоновые и служебные обходы не копят пользовательский спрос и не раскрывают текст."""
        import asyncio
        import search_engine

        async def live(query, city):
            return [{"title": "Видеокарта NVIDIA RTX 5090 32GB"}], False, {"attempted": 1, "failed": 0}

        with patch.object(search_engine, "_search_live_stores", live):
            for _ in range(5):
                asyncio.run(search_engine.search_live_stores("редкий запрос", city="Астана"))
        self.assertEqual(database.search_totals(days=1)["total"], 0)
        self.assertEqual(self.stored_queries(), [])
