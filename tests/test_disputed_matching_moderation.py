"""Тесты функционала ручной модерации спорных склеек товаров (human review for disputed product pairs)."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports

import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock

import database
import product_key
import product_verify


class TestDisputedMatchingModeration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "test_prices.db"
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.orig_db_path
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except OSError:
                pass
        try:
            os.rmdir(self.tmp_dir)
        except OSError:
            pass

    def test_enqueue_disputed_pair_basic_and_force(self):
        t1 = "Смартфон Apple iPhone 15 128GB Черный"
        t2 = "Apple iPhone 15 128GB Black A3090"
        ph = product_verify.pair_hash(t1, t2)

        # 1. Первая постановка в очередь
        ok1 = database.enqueue_disputed_pair(t1, t2, "test_variant_diff")
        self.assertTrue(ok1)

        # Проверяем наличие в очереди
        data = database.get_disputed_product_pairs(status="pending")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["pair_hash"], ph)
        self.assertEqual(data["items"][0]["status"], "pending")

        # 2. Повторная постановка без force не дублирует
        ok2 = database.enqueue_disputed_pair(t1, t2, "duplicate")
        self.assertFalse(ok2)

        # 3. Закрываем пару (например, skipped)
        database.update_disputed_pair_status(ph, "skipped", "manual_test")

        # 4. Постановка с force=True снова открывает пару в статус pending
        ok3 = database.enqueue_disputed_pair(t1, t2, "reopen_by_admin", force=True)
        self.assertTrue(ok3)

        data_reopened = database.get_disputed_product_pairs(status="pending")
        self.assertEqual(data_reopened["total"], 1)
        self.assertEqual(data_reopened["items"][0]["status"], "pending")
        self.assertEqual(data_reopened["items"][0]["reason"], "reopen_by_admin")

    def test_resolve_disputed_pair_same(self):
        t1 = "Ноутбук ASUS Vivobook 15 X1504VA 16/512GB"
        t2 = "ASUS VivoBook 15 Core i5 16GB 512GB"
        ph = product_verify.pair_hash(t1, t2)

        # Присваиваем разные начальные ключи
        database.rekey_titles({
            t1: "k2:asus_vivobook_15_x1504va",
            t2: "k2:asus_vivobook_15_x1504",
        })
        database.enqueue_disputed_pair(t1, t2, "variant_conflict", force=True)

        # Человек решает "SAME"
        res = database.resolve_disputed_pair_human(
            pair_hash=ph,
            decision="same",
            reviewer="operator_anna",
            notes="Один и тот же ноутбук, проверено по артикулу",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["decision"], "same")
        common_key = res["canonical_key"]

        # Проверяем, что оба товара теперь имеют единый ключ
        cached = database.get_cached_canonical_keys_batch([t1, t2])
        self.assertEqual(cached[t1], common_key)
        self.assertEqual(cached[t2], common_key)

        # Проверяем вердикт в product_pair_verdicts
        verdict = database.get_pair_verdict(ph)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict["decision"], "same")
        self.assertEqual(verdict["reviewer"], "operator_anna")
        self.assertEqual(verdict["source"], "human")

        # Проверяем статус в disputed_product_pairs
        pairs = database.get_disputed_product_pairs(status="resolved")
        self.assertEqual(pairs["total"], 1)
        self.assertEqual(pairs["items"][0]["status"], "resolved")
        self.assertTrue(pairs["items"][0]["same_key"])

    def test_resolve_disputed_pair_different_unglues(self):
        t1 = "Монитор LG UltraGear 27GP850-B 27\" QHD"
        t2 = "Монитор LG UltraGear 27GP950-B 27\" 4K"
        ph = product_verify.pair_hash(t1, t2)

        # Изначально ошибочно склеены под один ключ
        shared_key = "k2:lg_ultragear_27"
        database.rekey_titles({
            t1: shared_key,
            t2: shared_key,
        })
        database.enqueue_disputed_pair(t1, t2, "mistaken_merge", force=True)

        cached_before = database.get_cached_canonical_keys_batch([t1, t2])
        self.assertEqual(cached_before[t1], cached_before[t2])

        # Человек решает "DIFFERENT"
        res = database.resolve_disputed_pair_human(
            pair_hash=ph,
            decision="different",
            reviewer="operator_oleg",
            notes="Разные матрицы: один 2K, другой 4K",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["decision"], "different")
        self.assertIsNotNone(res.get("split"))

        # Проверяем, что ключи разделились!
        cached_after = database.get_cached_canonical_keys_batch([t1, t2])
        self.assertNotEqual(cached_after[t1], cached_after[t2])
        self.assertEqual(cached_after[t1], shared_key)
        self.assertIn("diff_", cached_after[t2])

        # Проверяем вердикт
        verdict = database.get_pair_verdict(ph)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict["decision"], "different")
        self.assertEqual(verdict["reviewer"], "operator_oleg")

        # Проверяем статус
        pairs = database.get_disputed_product_pairs(status="resolved")
        self.assertEqual(pairs["total"], 1)
        self.assertEqual(pairs["items"][0]["status"], "resolved")
        self.assertFalse(pairs["items"][0]["same_key"])

    def test_resolve_disputed_pair_skip(self):
        t1 = "Чехол для смартфона A"
        t2 = "Чехол для смартфона B"
        ph = product_verify.pair_hash(t1, t2)
        database.enqueue_disputed_pair(t1, t2, "uncertain_accessory", force=True)

        res = database.resolve_disputed_pair_human(
            pair_hash=ph,
            decision="skip",
            reviewer="admin",
            notes="Отложено до поступления фото",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["decision"], "skipped")

        pairs = database.get_disputed_product_pairs(status="skipped")
        self.assertEqual(pairs["total"], 1)
        self.assertEqual(pairs["items"][0]["status"], "skipped")

    def test_disputed_pairs_stats(self):
        t1 = "Товар 1"
        t2 = "Товар 2"
        t3 = "Товар 3"
        t4 = "Товар 4"
        database.enqueue_disputed_pair(t1, t2, "r1", force=True)
        database.enqueue_disputed_pair(t3, t4, "r2", force=True)

        ph1 = product_verify.pair_hash(t1, t2)
        database.resolve_disputed_pair_human(ph1, "same", reviewer="tester")

        stats = database.get_disputed_pairs_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["human_same"], 1)
        self.assertEqual(stats["human_different"], 0)


class TestHumanVerdictDurability(unittest.TestCase):
    """Аудит MM-01–03: решение оператора не шире пары, переживает пересборку и применяется целиком."""
    FIELDS = {"brand": "Apple", "model": "EarPods", "sku": None, "variants": [], "unbranded": False}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "m.db"
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self._old
        self._tmp.cleanup()

    def _catalog(self, keys, fields=None):
        database.save_cached_canonical_keys_batch(keys)
        if fields:
            database.save_title_fields_batch({t: fields for t in keys}, "k2", "local", "test")
        for i, (title, key) in enumerate(keys.items()):
            database.save_or_update_product({"id": f"p{i}", "title": title, "canonical_key": key, "shop": f"shop{i}",
                                             "city": "Алматы", "price": 1000, "url": f"https://example.kz/{i}"})

    def _state(self):
        with database.get_connection() as conn:
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                    for t in ("title_canonical_cache", "title_key_pins", "products", "offers", "offer_price_history",
                              "identity_aliases", "product_pair_verdicts", "disputed_product_pairs")}

    def _offer_product(self, pid):
        with database.get_connection() as conn:
            return conn.execute("SELECT product_id FROM offers WHERE id = ?", (f"off_{pid}",)).fetchone()[0]

    def _rescore(self, batch_size):
        import asyncio
        from scripts import migrate_to_k2
        asyncio.run(migrate_to_k2.run_migration(str(database.DB_PATH), "", "", rescore_only=True, batch_size=batch_size,
                                                run_id=f"t{batch_size}"))

    def test_same_refused_when_third_member_of_either_group_conflicts(self):
        a, b, c = "Samsung Galaxy S24 128GB black", "Samsung Galaxy S24 128GB white", "Samsung Galaxy S24 256GB blue"
        for keys in ({a: "s:a", b: "s:b", c: "s:b"}, {a: "s:a", c: "s:a", b: "s:b"}):
            database.enqueue_disputed_pair(a, b, "r", force=True)
            self._catalog(keys)
            before = self._state()
            res = database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "same", "op")
            self.assertFalse(res["ok"])
            self.assertEqual(res["conflicts"], [c])
            self.assertEqual(self._state(), before)

    def test_same_refused_by_saved_different_of_third_member(self):
        a, b, c = "Товар Альфа", "Товар Бета", "Товар Гамма"
        self._catalog({a: "x:a", b: "x:b", c: "x:b"})
        database.save_pair_verdict(product_key.pair_hash(a, c), a, c, {"decision": "different"}, "human:op")
        database.enqueue_disputed_pair(a, b, "r", force=True)
        res = database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "same", "op")
        self.assertEqual(res["conflicts"], [c])

    def test_operator_may_merge_the_pair_itself_despite_its_titles(self):
        a, b = "Samsung Galaxy S24 128GB", "Samsung Galaxy S24 256GB"
        self._catalog({a: "s:a", b: "s:b"})
        database.enqueue_disputed_pair(a, b, "r", force=True)
        self.assertTrue(database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "same", "op")["ok"])

    def test_different_survives_rescore_in_any_batch_and_order(self):
        a, b = "Apple EarPods USB-C", "Apple EarPods Lightning"
        old = product_key.build_key(self.FIELDS, a)
        self._catalog({a: old, b: old}, self.FIELDS)
        database.enqueue_disputed_pair(a, b, "r", force=True)
        database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "different", "op")
        expected = database.get_cached_canonical_keys_batch([a, b])
        self.assertNotEqual(expected[a], expected[b])
        for size in (1, 2, 50):
            self._rescore(size)
            self.assertEqual(database.get_cached_canonical_keys_batch([a, b]), expected)
            self.assertNotEqual(self._offer_product("p0"), self._offer_product("p1"))

    def test_different_splits_keys_that_differ_now_but_would_match_after_rescore(self):
        a, b = "Apple EarPods USB-C", "Apple EarPods Lightning"
        self._catalog({a: "apple:earpods:old", b: "apple:earpods:older"}, self.FIELDS)
        database.enqueue_disputed_pair(a, b, "r", force=True)
        database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "different", "op")
        self._rescore(2)
        keys = database.get_cached_canonical_keys_batch([a, b])
        self.assertNotEqual(keys[a], keys[b])

    def test_pinned_key_survives_rescan_and_cache_writes(self):
        a, b = "Apple EarPods USB-C", "Apple EarPods Lightning"
        old = product_key.build_key(self.FIELDS, a)
        self._catalog({a: old, b: old}, self.FIELDS)
        database.enqueue_disputed_pair(a, b, "r", force=True)
        separated = database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "different", "op")["split"]
        title, pinned = separated["separated_title"], separated["new_key"]
        pid = "p0" if title == a else "p1"
        database.save_cached_canonical_keys_batch({title: old})
        database.save_cached_canonical_key(title, old)
        database.update_products_canonical_keys([(old, pid)])
        new_offer = {"id": "p9", "title": title, "canonical_key": old, "shop": "shop9", "city": "Алматы", "price": 900, "url": "https://example.kz/9"}
        database.save_or_update_product(new_offer)
        database.save_or_update_products_batch([{**new_offer, "id": "p10", "shop": "shop10", "url": "https://example.kz/10"}])
        self.assertEqual(database.get_cached_canonical_keys_batch([title])[title], pinned)
        with database.get_connection() as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT canonical_key FROM products WHERE title = ?", (title,))}, {pinned})
        self.assertEqual(self._offer_product("p9"), self._offer_product(pid))
        self.assertEqual(self._offer_product("p10"), self._offer_product(pid))

    def test_same_survives_rescore(self):
        a, b = "Apple EarPods гарнитура", "Наушники EarPods"
        other = dict(self.FIELDS, model="EarPods Other")
        self._catalog({a: "k2:apple:earpods", b: "k2:apple:earpodsother"})
        database.save_title_fields_batch({a: self.FIELDS, b: other}, "k2", "local", "test")
        database.enqueue_disputed_pair(a, b, "r", force=True)
        self.assertTrue(database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "same", "op")["ok"])
        self._rescore(1)
        keys = database.get_cached_canonical_keys_batch([a, b])
        self.assertEqual(keys[a], keys[b])
        self.assertEqual(self._offer_product("p0"), self._offer_product("p1"))

    def test_same_refused_by_shadow_only_fields_of_third_member_in_either_group(self):
        a, b, c = "Samsung Galaxy S24 black", "Samsung Galaxy S24 white", "Samsung Galaxy S24 blue"
        small = {"brand": "Samsung", "model": "Galaxy S24", "sku": None, "variants": [{"value": 128, "unit": "gb"}], "unbranded": False}
        big = dict(small, variants=[{"value": 256, "unit": "gb"}])
        database.save_title_fields_batch({a: small, b: small}, "k2", "local", "test")
        database.save_shadow_fields_batch({c: big}, {c: product_key.build_key(big, c)}, "k2", "local", "test")
        database.enqueue_disputed_pair(a, b, "r", force=True)
        for keys in ({a: "s:a", b: "s:b", c: "s:b"}, {a: "s:a", c: "s:a", b: "s:b"}):
            self._catalog(keys)
            before = self._state()
            res = database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "same", "op")
            self.assertEqual(res.get("conflicts"), [c])
            self.assertEqual(self._state(), before)

    def test_different_with_distinct_keys_survives_refreshed_fields(self):
        a, b = "Apple EarPods Lightning", "Apple EarPods USB-C"
        first = dict(self.FIELDS, model="EarPods Lightning")
        second = dict(self.FIELDS, model="EarPods USB-C")
        self._catalog({a: product_key.build_key(first, a), b: product_key.build_key(second, b)})
        database.save_title_fields_batch({a: first, b: second}, "k2", "local", "test")
        database.enqueue_disputed_pair(a, b, "r", force=True)
        self.assertIsNone(database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "different", "op")["split"])
        expected = database.get_cached_canonical_keys_batch([a, b])
        for refreshed in (self.FIELDS, dict(self.FIELDS, model=None, sku="EarPods"), dict(self.FIELDS, sku="MWTY3")):
            database.save_title_fields_batch({a: refreshed, b: refreshed}, "k2", "local", "refresh")
            for size in (1, 2):
                self._rescore(size)
                self.assertEqual(database.get_cached_canonical_keys_batch([a, b]), expected)
        database.save_or_update_product({"id": "p9", "title": b, "canonical_key": product_key.build_key(self.FIELDS, b),
                                         "shop": "shop9", "city": "Алматы", "price": 900, "url": "https://example.kz/9"})
        self.assertEqual(self._offer_product("p9"), self._offer_product("p1"))
        self.assertNotEqual(self._offer_product("p0"), self._offer_product("p1"))

    def _split_during(self, target, write):
        """Оператор разделяет пару в момент target: подготовка записи уже идёт со старым ключом."""
        a, b = "Apple EarPods Lightning", "Apple EarPods USB-C"
        old = product_key.build_key(self.FIELDS, a)
        self._catalog({a: old, b: old}, self.FIELDS)
        database.enqueue_disputed_pair(a, b, "r", force=True)
        real, split = getattr(database, target), {}

        def racing(*args, **kwargs):
            result = real(*args, **kwargs)
            if not split:
                split.update(database.resolve_disputed_pair_human(product_key.pair_hash(a, b), "different", "op")["split"])
            return result
        with unittest.mock.patch.object(database, target, racing):
            write({"id": "p1", "title": b, "canonical_key": old, "shop": "shop1", "city": "Алматы", "price": 900, "url": "https://example.kz/1"})
        with database.get_connection() as conn:
            row = conn.execute("""SELECT p.canonical_key, cp.canonical_key FROM products p JOIN offers o ON o.id = 'off_' || p.id
                                  JOIN canonical_products cp ON cp.id = o.product_id WHERE p.id = 'p1'""").fetchone()
        self.assertEqual(split["separated_title"], b)
        self.assertEqual(tuple(row), (split["new_key"], split["new_key"]))
        self.assertEqual(database.get_cached_canonical_keys_batch([b])[b], split["new_key"])

    def test_prepared_batch_keeps_pin_set_before_its_write(self):
        self._split_during("_prepare_products_chunk", lambda p: database.save_or_update_products_batch([p]))

    def test_single_save_keeps_pin_set_before_its_write(self):
        self._split_during("site_old_price", database.save_or_update_product)

    def test_prepared_spool_chunk_keeps_pin_set_before_its_write(self):
        def write(p):
            database.spool_admit("t1", "kaspi", "Kaspi", None, {"prods": [p]})
            item = database.spool_claim("test-worker")
            self.assertTrue(database.spool_apply_chunk(item["task_id"], item["claim_token"], 0, [p]))
        self._split_during("_prepare_products_chunk", write)

    def test_failure_at_each_step_leaves_previous_state(self):
        a, b = "Товар Альфа", "Товар Бета"
        ph = product_key.pair_hash(a, b)
        self._catalog({a: "x:shared", b: "x:shared"})
        database.enqueue_disputed_pair(a, b, "r", force=True)
        before = self._state()
        for target in ("save_pair_verdict", "rekey_titles_conn", "_pin_title_keys_conn"):
            with unittest.mock.patch.object(database, target, side_effect=RuntimeError("сбой")):
                with self.assertRaises(RuntimeError):
                    database.resolve_disputed_pair_human(ph, "different", "op")
            self.assertEqual(self._state(), before, target)

    def test_second_operator_with_stale_card_is_refused(self):
        a, b = "Товар Альфа", "Товар Бета"
        ph = product_key.pair_hash(a, b)
        self._catalog({a: "x:a", b: "x:b"})
        database.enqueue_disputed_pair(a, b, "r", force=True)
        self.assertTrue(database.resolve_disputed_pair_human(ph, "same", "first")["ok"])
        state = self._state()
        res = database.resolve_disputed_pair_human(ph, "different", "second")
        self.assertFalse(res["ok"])
        self.assertEqual(self._state(), state)
        self.assertEqual(database.get_pair_verdict(ph)["reviewer"], "first")

    def test_machine_verdict_does_not_replace_operator(self):
        a, b = "Товар Альфа", "Товар Бета"
        ph = product_key.pair_hash(a, b)
        database.save_pair_verdict(ph, a, b, {"decision": "different", "source": "human"}, "human:op")
        database.save_pair_verdict(ph, a, b, {"decision": "same"}, "local")
        self.assertEqual(database.get_pair_verdict(ph)["decision"], "different")
        database.save_pair_verdict(ph, a, b, {"decision": "same", "source": "human"}, "human:second")
        self.assertEqual(database.get_pair_verdict(ph)["decision"], "same")

    def test_worker_does_not_apply_result_when_operator_decided_during_check(self):
        import asyncio
        a, b = "Товар Альфа", "Товар Бета"
        ph = product_key.pair_hash(a, b)
        self._catalog({a: "x:a", b: "x:b"}, self.FIELDS)
        database.enqueue_disputed_pair(a, b, "r", force=True)

        async def racing(*args, **kwargs):
            database.resolve_disputed_pair_human(ph, "different", "op")
            return {"decision": "same", "confidence": 1.0}
        with unittest.mock.patch.object(product_verify, "verify_pair_cached", racing):
            out = asyncio.run(product_verify.process_disputed_queue_tick("http://search", None))
        self.assertEqual(out["status"], "stale")
        self.assertEqual(database.get_pair_verdict(ph)["decision"], "different")
        keys = database.get_cached_canonical_keys_batch([a, b])
        self.assertNotEqual(keys[a], keys[b])


class TestDisputedMatchingWebAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "test_web_prices.db"
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        database.init_db()

        import auth
        from web import server
        self.patches = [
            unittest.mock.patch.object(auth, "ALLOW_DEV_LOGIN", True),
            unittest.mock.patch.object(server, "ALLOW_DEV_LOGIN", True),
            unittest.mock.patch.object(auth, "ADMIN_TELEGRAM_IDS", set()),
            unittest.mock.patch.object(server, "ADMIN_TELEGRAM_IDS", set()),
            unittest.mock.patch.object(auth, "PUBLIC_ORIGIN", ""),
        ]
        for p in self.patches:
            p.start()

        from aiohttp.test_utils import TestClient, TestServer
        app = server.create_app()
        app.cleanup_ctx.clear()
        self.server = TestServer(app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for p in reversed(self.patches):
            p.stop()
        database.DB_PATH = self.orig_db_path
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except OSError:
                pass
        try:
            os.rmdir(self.tmp_dir)
        except OSError:
            pass

    async def _login_dev_admin(self):
        origin = str(self.client.make_url("/")).rstrip("/")
        res = await self.client.post("/api/auth/dev-login", headers={"Origin": origin})
        self.assertEqual(res.status, 200)

    async def test_api_disputed_pairs_flow(self):
        # 1. Без авторизации эндпоинты требуют прав администратора
        resp_unauth = await self.client.get("/api/admin/matching/disputed")
        self.assertEqual(resp_unauth.status, 401)

        # 2. Логинимся под dev admin
        await self._login_dev_admin()

        origin = str(self.client.make_url("/")).rstrip("/")
        headers = {"Origin": origin}

        # 3. Ручная постановка пары в очередь
        t1 = "Колонка JBL Charge 5 Black"
        t2 = "JBL Charge 5 Черная (JBLCHARGE5BLK)"
        res_enqueue = await self.client.post(
            "/api/admin/matching/enqueue",
            json={"title_a": t1, "title_b": t2, "reason": "color_translation"},
            headers=headers,
        )
        self.assertEqual(res_enqueue.status, 200)
        data_enqueue = await res_enqueue.json()
        self.assertEqual(data_enqueue["status"], "ok")
        ph = data_enqueue["pair_hash"]
        self.assertTrue(ph)

        # 4. Проверяем получение списка
        res_list = await self.client.get("/api/admin/matching/disputed?status=pending")
        self.assertEqual(res_list.status, 200)
        data_list = await res_list.json()
        self.assertEqual(data_list["total"], 1)
        self.assertEqual(data_list["items"][0]["pair_hash"], ph)

        # 5. Проверяем статистику
        res_stats = await self.client.get("/api/admin/matching/stats")
        self.assertEqual(res_stats.status, 200)
        stats = (await res_stats.json())["stats"]
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["resolved"], 0)

        # 6. Применяем вердикт SAME
        res_verdict = await self.client.post(
            "/api/admin/matching/verdict",
            json={"pair_hash": ph, "decision": "same", "notes": "Та же колонка, цвет переведен"},
            headers=headers,
        )
        self.assertEqual(res_verdict.status, 200)
        data_verdict = await res_verdict.json()
        self.assertEqual(data_verdict["status"], "ok")
        self.assertEqual(data_verdict["decision"], "same")

        # 7. Проверяем, что пара переместилась в resolved
        res_after = await self.client.get("/api/admin/matching/disputed?status=resolved")
        data_after = await res_after.json()
        self.assertEqual(data_after["total"], 1)
        self.assertEqual(data_after["items"][0]["status"], "resolved")
        self.assertEqual(data_after["items"][0]["verdict"]["decision"], "same")

    async def test_api_disputed_pair_different_and_skip(self):
        await self._login_dev_admin()
        origin = str(self.client.make_url("/")).rstrip("/")
        headers = {"Origin": origin}

        # Создаем две пары
        t_a1, t_a2 = "Флешка Kingston DataTraveler 64GB", "Флешка Kingston DataTraveler 128GB"
        t_b1, t_b2 = "Мышь беспроводная Logitech M185 Серый", "Мышь беспроводная Logitech M185 Синий"

        await self.client.post("/api/admin/matching/enqueue", json={"title_a": t_a1, "title_b": t_a2, "reason": "diff_capacity"}, headers=headers)
        await self.client.post("/api/admin/matching/enqueue", json={"title_a": t_b1, "title_b": t_b2, "reason": "diff_color"}, headers=headers)

        ph_a = product_verify.pair_hash(t_a1, t_a2)
        ph_b = product_verify.pair_hash(t_b1, t_b2)

        # Решаем пару A как DIFFERENT
        res_diff = await self.client.post(
            "/api/admin/matching/verdict",
            json={"pair_hash": ph_a, "decision": "different", "notes": "Разный объём памяти"},
            headers=headers,
        )
        self.assertEqual(res_diff.status, 200)
        self.assertEqual((await res_diff.json())["decision"], "different")

        # Решаем пару B как SKIP
        res_skip = await self.client.post(
            "/api/admin/matching/verdict",
            json={"pair_hash": ph_b, "decision": "skip", "notes": "Пока отложим"},
            headers=headers,
        )
        self.assertEqual(res_skip.status, 200)
        self.assertEqual((await res_skip.json())["decision"], "skipped")

        # Проверяем фильтры
        res_skipped = await self.client.get("/api/admin/matching/disputed?status=skipped")
        self.assertEqual((await res_skipped.json())["total"], 1)

        res_search = await self.client.get("/api/admin/matching/disputed?search=Kingston")
        self.assertEqual((await res_search.json())["total"], 1)

        # Проверяем валидацию
        res_bad = await self.client.post(
            "/api/admin/matching/verdict",
            json={"pair_hash": ph_a, "decision": "invalid_choice"},
            headers=headers,
        )
        self.assertEqual(res_bad.status, 400)

    async def test_monitoring_page_includes_matching_section(self):
        resp = await self.client.get("/monitoring")
        self.assertEqual(resp.status, 200)
        html = await resp.text()
        self.assertIn("Склейка товаров", html)
        self.assertIn("matchingPairsContainer", html)
        self.assertIn("loadMatchingDisputes", html)
        self.assertIn("submitMatchingVerdict", html)


if __name__ == "__main__":
    unittest.main()
