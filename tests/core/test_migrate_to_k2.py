from tests import test_support  # noqa: F401  isolates DATA_DIR
"""P17-C4: миграция ключей в k2 на полной схеме базы (legacy + V2), аудит P17C-02/06/07."""
import asyncio
import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import config
import database
import product_key as pk
import scripts.migrate_to_k2 as mig
from database import DB_PATH

T1 = "SSD 128GB Transcend TS128GMTE110S M2"
T2 = "SSD 128GB Transcend MTE110S (TS128GMTE110S)"
FIELDS = {
    T1: {"brand": "Transcend", "model": "TS128GMTE110S", "sku": None,
         "variants": [{"value": 128, "unit": "ГБ"}], "unbranded": False},
    T2: {"brand": "Transcend", "model": "MTE110S", "sku": "TS128GMTE110S",
         "variants": [{"value": 128, "unit": "ГБ"}], "unbranded": False},
}


def _hash(title):
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


class _FullSchemaCase(unittest.TestCase):
    """Настоящая схема приложения: товары пишутся save_or_update_product с двойной записью V2."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db_path = str(self.data / "prices.db")
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(self.db_path)),
                        patch.object(config, "DATA_DIR", self.data),
                        patch.object(config, "SETTINGS_FILE", self.data / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def add_product(self, pid, title, shop, key, price=1000):
        database.save_or_update_product({"id": pid, "shop": shop, "city": "Алматы", "title": title,
                                         "price": price, "canonical_key": key, "url": f"https://example.kz/{pid}"})
        database.save_cached_canonical_keys_batch({title: key})

    def identities(self):
        with database.get_connection() as conn:
            legacy = {r[0]: r[1] for r in conn.execute("SELECT id, canonical_key FROM products")}
            v2 = {r[0]: r[1] for r in conn.execute(
                "SELECT o.external_sku, p.canonical_key FROM offers o JOIN canonical_products p ON p.id = o.product_id")}
        return legacy, v2

    def run_migration(self, **kw):
        return asyncio.run(mig.run_migration(self.db_path, "http://fake", "qwen3-vl:8b-instruct", **kw))


def fake_reply(*titles):
    async def call(prompt, base, model, key, timeout):
        return {"items": [{"title": t, **FIELDS[t]} for t in titles]}
    return call


class MigrateToK2Test(_FullSchemaCase):
    def test_find_titles_to_migrate_skips_k2(self):
        database.save_cached_canonical_keys_batch({"Item 1": "old:key1", "Item 2": "k2:brand:mod2",
                                                   "Item 3": "old:key3"})
        conn = mig.get_db_connection(self.db_path)
        try:
            self.assertEqual(sorted(t for t, _ in mig.find_titles_to_migrate(conn)), ["Item 1", "Item 3"])
            self.assertEqual(len(mig.find_titles_to_migrate(conn, limit=1)), 1)
        finally:
            conn.close()

    def test_migration_moves_legacy_and_v2_together(self):
        """P17C-02: после миграции предложения V2 на тех же канонических товарах, что и products."""
        self.add_product("a1", T1, "Sulpak", "transcend:mte110s:128gb")
        self.add_product("b1", T2, "DNS Казахстан", "transcend:ts128gmte110s:128gb")
        with patch("ai_service._call_local_api", fake_reply(T1, T2)):
            res = self.run_migration(check_pairs=True)
        self.assertEqual(res["with_k2_key"], 2)
        legacy, v2 = self.identities()
        self.assertEqual(len(set(legacy.values())), 1)
        self.assertTrue(next(iter(legacy.values())).startswith("k2:transcend:"))
        self.assertEqual(v2, legacy)
        # Повтор не меняет ничего и не требует модели
        with patch("ai_service._call_local_api", side_effect=AssertionError("модель не нужна")):
            self.assertEqual(self.run_migration()["status"], "nothing_to_migrate")
        self.assertEqual(self.identities(), (legacy, v2))

    def test_price_history_follows_offer(self):
        """История цен переезжает с предложением на новый канонический товар, а не теряется."""
        self.add_product("a1", T1, "Sulpak", "transcend:old:128gb", price=1000)
        self.add_product("a1", T1, "Sulpak", "transcend:old:128gb", price=900)
        with database.get_connection() as conn:
            before = conn.execute("SELECT COUNT(*) FROM offer_price_history WHERE offer_id = 'off_a1'").fetchone()[0]
        with patch("ai_service._call_local_api", fake_reply(T1)):
            self.run_migration()
        with database.get_connection() as conn:
            new_pid = conn.execute("SELECT product_id FROM offers WHERE id = 'off_a1'").fetchone()[0]
            moved = conn.execute("SELECT COUNT(*) FROM offer_price_history WHERE offer_id = 'off_a1' "
                                 "AND COALESCE(product_id, ?) = ?", (new_pid, new_pid)).fetchone()[0]
        self.assertGreaterEqual(before, 2)
        self.assertEqual(moved, before)

    def test_rescore_only_rebuilds_existing_k2(self):
        """P17C-06: пересборка видит и уже переведённые k2-записи и не вызывает модель."""
        self.add_product("a1", T1, "Sulpak", "k2:transcend:wrong")
        database.save_title_fields_batch({T1: FIELDS[T1]}, "k2", "local", "fake")
        with patch("ai_service._call_local_api", side_effect=AssertionError("модель не нужна")):
            res = self.run_migration(rescore_only=True)
        expected = pk.build_key(FIELDS[T1], T1)
        self.assertEqual(res["with_k2_key"], 1)
        self.assertEqual(database.get_cached_canonical_keys_batch([T1])[T1], expected)
        self.assertEqual(self.identities()[1]["a1"], expected)

    def test_dry_run_is_read_only_and_matches_real_pass(self):
        """Пробный прогон не пишет и не меняет режим журнала; прогноз совпадает с проходом при любом размере пачки."""
        self.add_product("a1", T1, "Sulpak", "old:a")
        self.add_product("b1", T2, "DNS Казахстан", "old:b")
        with sqlite3.connect(self.db_path) as raw:
            raw.execute("PRAGMA journal_mode = DELETE")
        mtime = os.path.getmtime(self.db_path)
        with patch("ai_service._call_local_api", AsyncMock(side_effect=lambda p, *a: {"items": [
                {"title": t, **FIELDS[t]} for t in (T1, T2) if t in p]})):
            dry = self.run_migration(dry_run=True, batch_size=1, check_pairs=True)
        with sqlite3.connect(self.db_path) as raw:
            self.assertEqual(raw.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(os.path.getmtime(self.db_path), mtime)
        self.assertEqual(database.get_cached_canonical_keys_batch([T1])[T1], "old:a")
        with patch("ai_service._call_local_api", AsyncMock(side_effect=lambda p, *a: {"items": [
                {"title": t, **FIELDS[t]} for t in (T1, T2) if t in p]})):
            real = self.run_migration(batch_size=1, check_pairs=True)
        self.assertEqual(dry["with_k2_key"], real["with_k2_key"])
        self.assertEqual(dry["pairs_comparison"]["new_pairs_count"], real["pairs_comparison"]["new_pairs_count"])
        keys = database.get_cached_canonical_keys_batch([T1, T2])
        self.assertEqual(keys[T1], keys[T2])

    def test_local_off_blocks_model_calls(self):
        """Миграция идёт через общий путь локальной модели: при «AI выкл» к модели не обращается (P17C-05)."""
        (self.data / "ai_mode.json").write_text('{"mode": "off"}')
        self.add_product("a1", T1, "Sulpak", "old:a")
        with patch("ai_service._call_local_api", side_effect=AssertionError("«AI выкл» нарушен")):
            res = self.run_migration()
        self.assertEqual((res["api_failures"], res["with_k2_key"]), (1, 0))
        self.assertEqual(database.get_cached_canonical_keys_batch([T1])[T1], "old:a")


class BatchOrderSafetyTest(unittest.TestCase):
    """P17CR-01: 128 и 256 ГБ не попадают в одну группу ни при каком порядке и размере пачки,
    в пробном и настоящем прогоне, при повторном запуске и с уже сохранённым ключом без объёма."""

    @staticmethod
    def phone(cap=None):
        return {"brand": "Samsung", "model": "SM-S911B", "sku": None, "unbranded": False,
                "variants": [] if cap is None else [{"value": cap, "unit": "gb"}]}

    def run_case(self, order, batch_size, dry_run, fixed_unknown=False):
        tmp = tempfile.TemporaryDirectory()
        data = Path(tmp.name)
        db_path = str(data / "prices.db")
        patches = [patch.object(database, "DB_PATH", type(DB_PATH)(db_path)),
                   patch.object(config, "DATA_DIR", data),
                   patch.object(config, "SETTINGS_FILE", data / "settings.json")]
        for p in patches:
            p.start()
        try:
            database.init_db()
            fields = {"Unknown capacity": self.phone(), "Phone 128": self.phone(128), "Phone 256": self.phone(256)}
            for i, title in enumerate(order):
                database.save_or_update_product({"id": f"p{i}", "shop": ["Sulpak", "DNS Казахстан", "Mechta"][i],
                                                 "city": "Алматы", "title": title, "price": 1000,
                                                 "url": f"https://example.kz/{i}", "canonical_key": f"old:p{i}"})
                database.save_cached_canonical_keys_batch({title: f"old:p{i}"})
                with database.get_connection() as conn:
                    conn.execute("UPDATE title_canonical_cache SET created_at = ? WHERE title = ?",
                                 (f"2026-01-01 00:00:0{i}", title))
                    conn.commit()
            database.save_title_fields_batch(fields, "k2", "local", "fake")
            if fixed_unknown:      # уже переведённое unknown с ключом без объёма, видимое через title_articles
                key = pk.build_key(fields["Unknown capacity"], "Unknown capacity")
                database.rekey_titles({"Unknown capacity": key})
                database.save_title_articles_batch([(a, _hash("Unknown capacity"), key)
                                                    for a in pk.article_tokens(fields["Unknown capacity"])])
            results = []
            for _ in range(1 if dry_run else 2):         # настоящий прогон — дважды (повтор/возобновление)
                with patch("ai_service._call_local_api", side_effect=AssertionError("модель не нужна")):
                    results.append(asyncio.run(mig.run_migration(db_path, "http://unused", "fake", batch_size=batch_size,
                                                                 rescore_only=True, dry_run=dry_run)))
            keys = database.get_cached_canonical_keys_batch(list(fields))
            with database.get_connection() as conn:
                v2 = {r[0]: r[1] for r in conn.execute(
                    "SELECT pr.title, cp.canonical_key FROM products pr JOIN offers o ON o.id = 'off_' || pr.id "
                    "JOIN canonical_products cp ON cp.id = o.product_id")}
            return keys, v2, results
        finally:
            for p in reversed(patches):
                p.stop()
            tmp.cleanup()

    def test_all_orders_batch_sizes_and_modes(self):
        import itertools
        titles = ["Unknown capacity", "Phone 128", "Phone 256"]
        for order in itertools.permutations(titles):
            for batch_size in (1, 2, 3):
                for fixed_unknown in (False, True):
                    with self.subTest(order=order, batch_size=batch_size, fixed_unknown=fixed_unknown):
                        keys, v2, _ = self.run_case(order, batch_size, dry_run=False, fixed_unknown=fixed_unknown)
                        self.assertNotEqual(keys["Phone 128"], keys["Phone 256"])
                        self.assertTrue(keys["Phone 128"].endswith(":128gb"), keys)
                        self.assertTrue(keys["Phone 256"].endswith(":256gb"), keys)
                        self.assertEqual(v2, keys, "V2 совпадает с кэшем")
                        _, _, (dry,) = self.run_case(order, batch_size, dry_run=True, fixed_unknown=fixed_unknown)
                        self.assertEqual(dry["with_k2_key"], 3)


class PairsMetricTest(unittest.TestCase):
    """P17C-07: единица метрики — пары названий с предложениями хотя бы в двух разных магазинах."""

    def test_moved_pairs(self):
        old = {"A": "gemini:iphone15", "B": "gemini:iphone15", "C": "gemini:other"}
        new = {"A": "k2:apple:iphone15", "B": "k2:apple:iphone15plus", "C": "k2:apple:iphone15plus"}
        comp = mig.check_pairs_comparison(old, new, {"A": ["shop1"], "B": ["shop2"], "C": ["shop3"]})
        self.assertEqual((comp["old_pairs_count"], comp["new_pairs_count"], comp["retained_pairs_count"],
                          comp["dropped_pairs_count"], comp["added_pairs_count"]), (1, 1, 0, 1, 1))

    def test_identical_and_overlapping_multishop_sets_count(self):
        for shops in ({"A": ["shop1", "shop2"], "B": ["shop1", "shop2"]},
                      {"A": ["shop1", "shop2"], "B": ["shop2", "shop3"]},
                      {"A": ["shop1"], "B": ["shop1", "shop2"]}):
            with self.subTest(shops=shops):
                comp = mig.check_pairs_comparison({"A": "old:g", "B": "old:g"}, {"A": "k2:a:1", "B": "k2:a:2"}, shops)
                self.assertEqual((comp["old_pairs_count"], comp["dropped_pairs_count"]), (1, 1))
                self.assertEqual(comp["retention_rate"], 0.0)

    def test_single_shop_is_not_a_pair_and_no_pairs_is_not_100(self):
        comp = mig.check_pairs_comparison({"A": "old:g", "B": "old:g"}, {"A": "k2:a:1", "B": "k2:a:2"},
                                          {"A": ["shop1"], "B": ["shop1"]})
        self.assertEqual(comp["old_pairs_count"], 0)
        self.assertIsNone(comp["retention_rate"])
        self.assertFalse(comp["valid"])

    def test_against_brute_force(self):
        import itertools
        import random
        rnd = random.Random(7)
        titles = [f"T{i}" for i in range(12)]
        shops = {t: rnd.sample(["s1", "s2", "s3"], rnd.randint(1, 2)) for t in titles}
        old = {t: f"old:{rnd.randint(0, 3)}" for t in titles}
        new = {t: f"k2:{rnd.randint(0, 3)}" for t in titles}

        def brute(keys):
            return {(a, b) for a, b in itertools.combinations(sorted(titles), 2)
                    if keys[a] == keys[b] and any(x != y for x in shops[a] for y in shops[b])}
        comp = mig.check_pairs_comparison(old, new, shops)
        self.assertEqual(comp["old_pairs_count"], len(brute(old)))
        self.assertEqual(comp["new_pairs_count"], len(brute(new)))
        self.assertEqual(comp["retained_pairs_count"], len(brute(old) & brute(new)))


if __name__ == "__main__":
    unittest.main()
