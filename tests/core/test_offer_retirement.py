"""G01 (аудит 6.4.5): снятие и возврат товара доходят до V2-предложения, поиска и наблюдений.

Все сценарии идут рабочим путём записи: enqueue → захват → _execute_queue_item (порции, сверка
источника, производные эффекты) → ACK. Поиск — настоящий SearchCore по той же базе.
"""

from tests import test_support  # noqa: F401
import os
import sqlite3
import threading
import time
from unittest.mock import patch

import database
from domain.search_core import SearchCore
from repositories import SearchRepository
from tests.core.test_queue_contract import QueueContractBase, WATCH_USER, prod


class OfferRetirementTest(QueueContractBase):
    def setUp(self):
        super().setUp()
        self.tag = os.urandom(3).hex()  # своя строка товаров: база общая для всего прогона
        self.title = f"Retire {self.tag} Laptop"
        self.shop = f"retire-{self.tag}"
        database.upsert_telegram_user({"id": WATCH_USER, "first_name": "Retire"})
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_events")
            conn.execute("DELETE FROM watch_state")
            conn.execute("DELETE FROM watches")
            conn.execute("DELETE FROM notification_outbox WHERE user_id = ?", (WATCH_USER,))
            conn.commit()

    def pid(self, name):
        return f"{self.tag}-{name}"

    def item(self, name, price=100):
        return prod(self.pid(name), price, self.title)

    async def scan(self, names, url="https://example.invalid/cat", complete=True, fail=False):
        """Один обход категории: пачка товаров и сверка источника; fail — задача уходит в отказ."""
        qm = self.qm()
        admission = await qm.enqueue([self.item(n) for n in names], "Contract", {}, shop_key=self.shop,
                                     reconcile={"key": self.shop, "url": url, "complete": complete})
        self.assertTrue(admission)
        item = await qm.dequeue(timeout=0.05)
        self.assertEqual(item["task_id"], admission.task_id)
        if fail:
            self.assertEqual(await qm.nack(item, error="synthetic", max_retries=0), "dead_letter")
        else:
            self.assertTrue(await self.run_item(qm, item))
        time.sleep(0.01)  # следующий обход принят строго позже
        return admission.task_id

    def state(self, name):
        pid = self.pid(name)
        with database.get_connection() as conn:
            product = conn.execute("SELECT is_active FROM products WHERE id = ?", (pid,)).fetchone()[0]
            offer = conn.execute("SELECT is_active FROM offers WHERE id = ?", (f"off_{pid}",)).fetchone()[0]
        offers = database.watched_offers([pid])
        with database.get_connection() as conn:
            found = SearchCore(SearchRepository(conn)).search(self.title, limit=50)
        card = next((r for r in found.items if any(o.id == f"off_{pid}" for o in r.offers)), None)
        purchasable = bool(card and any(o.is_purchasable for o in card.offers if o.id == f"off_{pid}"))
        return {"product": product, "offer": offer, "watch_available": offers[0]["is_available"],
                "search_purchasable": purchasable}

    def assert_available(self, name, available):
        flag = 1 if available else 0
        self.assertEqual(self.state(name), {"product": flag, "offer": flag, "watch_available": flag,
                                            "search_purchasable": bool(available)})

    def watch(self, name, condition, **extra):
        return database.create_watch(WATCH_USER, {"kind": "product", "target": self.pid(name),
                                                  "condition": condition, "quiet_from": "", "quiet_to": "",
                                                  "cooldown_hours": 0, **extra})

    def events(self):
        return database.watch_events(WATCH_USER)

    async def test_full_scan_without_item_retires_it_everywhere_and_return_restores(self):
        await self.scan(["kept", "gone"])
        self.assert_available("gone", True)
        self.watch("gone", "back_in_stock")
        self.watch("gone", "target_price", threshold=1000)
        target_events = len(self.events())
        await self.scan(["kept"])
        self.assert_available("gone", False)
        self.assert_available("kept", True)
        self.assertEqual(len(self.events()), target_events, "снятый товар дал уведомление")
        await self.scan(["kept", "gone"])
        self.assert_available("gone", True)
        reasons = [e["reason"] for e in self.events()]
        self.assertIn("товар снова в продаже", reasons)

    async def test_item_confirmed_by_another_active_source_stays(self):
        await self.scan(["shared"], url="https://example.invalid/cat-a")
        await self.scan(["shared"], url="https://example.invalid/cat-b")
        await self.scan(["other"], url="https://example.invalid/cat-a")  # в категории A товара больше нет
        self.assert_available("shared", True)

    async def test_limited_and_failed_scans_do_not_retire(self):
        await self.scan(["kept", "maybe"])
        await self.scan(["kept"], complete=False)
        self.assert_available("maybe", True)
        await self.scan(["kept"], fail=True)
        self.assert_available("maybe", True)

    async def test_old_retry_after_newer_full_scan_does_not_retire(self):
        await self.scan(["kept", "late"])
        old = await self.scan(["kept"], fail=True)          # давний полный обход без товара — отказ
        await self.scan(["kept", "late"])                   # свежий полный обход товар подтвердил
        self.assertEqual(database.spool_resolve_failures("retry", [old]), 1)
        qm = self.qm()
        item = await qm.dequeue(timeout=0.05)
        self.assertEqual(item["task_id"], old)
        self.assertTrue(await self.run_item(qm, item))
        self.assert_available("late", True)

    async def test_independent_seller_channel_is_not_retired(self):
        await self.scan(["kept", "gone"])
        with database.get_connection() as conn:
            base = dict(conn.execute("SELECT * FROM offers WHERE id = ?", (f"off_{self.pid('gone')}",)).fetchone())
            social = dict(base, id=f"social_{self.tag}", external_sku=f"social-{self.tag}")
            cols = ",".join(social)
            conn.execute(f"INSERT INTO offers ({cols}) VALUES ({','.join('?' * len(social))})", list(social.values()))
            conn.commit()
        await self.scan(["kept"])
        with database.get_connection() as conn:
            row = conn.execute("SELECT is_active FROM offers WHERE id = ?", (f"social_{self.tag}",)).fetchone()
        self.assertEqual(row[0], 1, "снятие legacy-товара задело независимый канал продавца")

    async def test_startup_repairs_offers_retired_before_the_fix(self):
        await self.scan(["kept", "drift"])
        with database.get_connection() as conn:  # состояние баз до 6.4.6: снят только в products
            conn.execute("UPDATE products SET is_active = 0 WHERE id = ?", (self.pid("drift"),))
            conn.commit()
        self.assertEqual(self.state("drift")["offer"], 1)
        self.assertGreaterEqual(database.sync_retired_offers(), 1)
        self.assert_available("drift", False)
        self.assert_available("kept", True)
        self.assertEqual(database.sync_retired_offers(), 0, "починка не идемпотентна")

    async def test_startup_repair_updates_watch_memory_so_return_notifies(self):
        """G02: старая база + наблюдение → init_db → возврат по прежней цене → одно «снова в продаже»."""
        await self.scan(["kept", "drift"])
        self.watch("drift", "back_in_stock")
        database.evaluate_watches(database.watched_offers([self.pid("drift")]))  # память: доступен
        with database.get_connection() as conn:  # рассинхронизация 6.4.5: снят только в products
            conn.execute("UPDATE products SET is_active = 0 WHERE id = ?", (self.pid("drift"),))
            conn.commit()
        database.init_db()
        self.assert_available("drift", False)
        self.assertEqual(self.events(), [])
        await self.scan(["kept", "drift"])
        self.assert_available("drift", True)
        self.assertEqual([e["reason"] for e in self.events()], ["товар снова в продаже"])

    def refresh_jobs(self, name):
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM watch_refresh WHERE product_id = ?",
                                (self.pid(name),)).fetchone()[0]

    async def prepare_old_base(self):
        await self.scan(["kept", "drift"])
        self.watch("drift", "back_in_stock")
        database.evaluate_watches(database.watched_offers([self.pid("drift")]))  # память: доступен
        with database.get_connection() as conn:
            conn.execute("UPDATE products SET is_active = 0 WHERE id = ?", (self.pid("drift"),))
            conn.commit()

    async def assert_return_notifies_once(self):
        await self.scan(["kept", "drift"])
        self.assert_available("drift", True)
        self.assertEqual([e["reason"] for e in self.events()], ["товар снова в продаже"])

    async def startup(self):
        """Полный старт сервиса: быстрая синхронная init_db + фоновый разбор watch_refresh."""
        from web import server
        database.init_db()
        await server.startup_watch_refresh_worker()

    async def test_memory_repair_survives_failure_and_restart(self):
        """G03-A: сбой обновления памяти при старте повторяется следующим стартом."""
        await self.prepare_old_base()
        with patch.object(database, "_apply_retire_transition", side_effect=sqlite3.OperationalError("synthetic")):
            await self.startup()
        self.assert_available("drift", False)
        self.assertEqual(self.refresh_jobs("drift"), 1, "задание потеряно после сбоя")
        await self.startup()
        self.assertEqual(self.refresh_jobs("drift"), 0)
        await self.assert_return_notifies_once()

    async def test_base_already_repaired_by_646_gets_memory_fixed(self):
        """G03-B: offers уже сняла 6.4.6, память осталась «в продаже» — разовый досев заданий."""
        await self.prepare_old_base()
        with database.get_connection() as conn:  # состояние после 6.4.6: offer снят, заданий нет, метки нет
            conn.execute("UPDATE offers SET is_active = 0 WHERE id = ?", (f"off_{self.pid('drift')}",))
            conn.execute("DELETE FROM watch_refresh")
            conn.execute("DELETE FROM schema_metadata WHERE name = ?", (database.WATCH_REFRESH_SEED,))
            conn.commit()
        await self.startup()
        self.assertEqual(self.refresh_jobs("drift"), 0)
        await self.startup()  # повторный старт ничего не досевает заново
        await self.assert_return_notifies_once()

    async def test_failed_retired_watch_refresh_in_queue_is_retried(self):
        """Сбой обновления памяти в очереди: задача и задание остаются, повтор доводит до конца."""
        await self.scan(["kept", "late"])
        self.watch("late", "back_in_stock")
        database.evaluate_watches(database.watched_offers([self.pid("late")]))
        qm = self.qm()
        await qm.enqueue([self.item("kept")], "Contract", {}, shop_key=self.shop,
                         reconcile={"key": self.shop, "url": "https://example.invalid/cat", "complete": True})
        item = await qm.dequeue(timeout=0.05)
        with patch.object(database, "_apply_retire_transition", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.run_item(qm, item)
        self.assertEqual(self.refresh_jobs("late"), 1)
        self.assertEqual(await qm.nack(item, error="synthetic"), "pending")
        item = await qm.dequeue(timeout=0.05)
        self.assertTrue(await self.run_item(qm, item))
        self.assertEqual(self.refresh_jobs("late"), 0)
        time.sleep(0.01)
        await self.scan(["kept", "late"])
        self.assertEqual([e["reason"] for e in self.events()], ["товар снова в продаже"])

    async def test_return_before_deferred_refresh_still_notifies_once(self):
        """G04: сбой при старте → товар вернулся раньше повтора → повтор; одно «снова в продаже»."""
        await self.prepare_old_base()
        with patch.object(database, "_apply_retire_transition", side_effect=sqlite3.OperationalError("synthetic")):
            await self.startup()
        self.assertEqual(self.refresh_jobs("drift"), 1)
        await self.assert_return_notifies_once()        # возврат до повтора задания
        self.assertEqual(self.refresh_jobs("drift"), 0, "переход снятия не применён при возврате")
        await self.startup()                             # повтор заданий: дубля нет
        self.assertEqual([e["reason"] for e in self.events()], ["товар снова в продаже"])

    async def test_deferred_refresh_after_return_applies_transition_in_order(self):
        """G04: задание выполняется уже после возврата (возврат без проверки наблюдений) — порядок сохранён."""
        await self.prepare_old_base()
        with patch.object(database, "_apply_retire_transition", side_effect=sqlite3.OperationalError("synthetic")):
            database.init_db()
        with database.get_connection() as conn:  # товар снова в каталоге, наблюдения ещё не проверялись
            conn.execute("UPDATE products SET is_active = 1 WHERE id = ?", (self.pid("drift"),))
            conn.execute("UPDATE offers SET is_active = 1 WHERE id = ?", (f"off_{self.pid('drift')}",))
            conn.commit()
        self.assertEqual(database.drain_watch_refresh(), 1)
        self.assertEqual([e["reason"] for e in self.events()], ["товар снова в продаже"])
        self.assertEqual(database.drain_watch_refresh(), 0)
        self.assertEqual(len(self.events()), 1)

    def outbox(self):
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM notification_outbox WHERE user_id = ? AND alert_id < 0",
                                (WATCH_USER,)).fetchone()[0]

    async def test_failed_job_delete_after_event_does_not_duplicate(self):
        """G05: событие и outbox записаны → удаление задания падает → повтор → ровно одно событие."""
        await self.prepare_old_base()
        with patch.object(database, "_apply_retire_transition", side_effect=sqlite3.OperationalError("synthetic")):
            database.init_db()
        with database.get_connection() as conn:  # товар вернулся без проверки наблюдений
            conn.execute("UPDATE products SET is_active = 1 WHERE id = ?", (self.pid("drift"),))
            conn.execute("UPDATE offers SET is_active = 1 WHERE id = ?", (f"off_{self.pid('drift')}",))
            conn.execute("CREATE TRIGGER g05_block BEFORE DELETE ON watch_refresh "
                         "BEGIN SELECT RAISE(ABORT, 'synthetic delete failure'); END")
            conn.commit()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                database.drain_watch_refresh()
        finally:
            with database.get_connection() as conn:
                conn.execute("DROP TRIGGER g05_block")
                conn.commit()
        self.assertEqual((len(self.events()), self.outbox()), (1, 1))
        self.assertEqual(self.refresh_jobs("drift"), 1, "задание должно остаться до подтверждения")
        database.init_db()                                   # повтор: рестарт с тем же заданием
        database.drain_watch_refresh()
        self.assertEqual(self.refresh_jobs("drift"), 0)
        self.assertEqual((len(self.events()), self.outbox()), (1, 1), "повтор создал второе уведомление")
        # Новый настоящий цикл снятия и возврата — новое событие
        await self.scan(["kept"])
        await self.scan(["kept", "drift"])
        self.assertEqual([e["reason"] for e in self.events()], ["товар снова в продаже"] * 2)
        self.assertEqual(self.outbox(), 2)

    async def prepare_deferred_return(self):
        """Отложенное снятие (сбой при старте), товар уже вернулся без проверки наблюдений."""
        await self.prepare_old_base()
        with patch.object(database, "_apply_retire_transition", side_effect=sqlite3.OperationalError("synthetic")):
            database.init_db()
        with database.get_connection() as conn:
            conn.execute("UPDATE products SET is_active = 1 WHERE id = ?", (self.pid("drift"),))
            conn.execute("UPDATE offers SET is_active = 1 WHERE id = ?", (f"off_{self.pid('drift')}",))
            conn.commit()
        self.assertEqual(self.refresh_jobs("drift"), 1)

    def run_two_refreshes(self, second_waits_for_first):
        """Два настоящих refresh по одному заданию; барьер ставится, когда оба уже прочли фазу retire."""
        original = database._apply_retire_transition
        barrier = threading.Barrier(2, timeout=10)
        first_done = threading.Event()
        errors = []

        def scheduled(*args):
            barrier.wait()
            if threading.current_thread().name == "second" and second_waits_for_first:
                first_done.wait(10)
            return original(*args)

        def worker():
            try:
                database.refresh_retired_watches([self.pid("drift")])
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                if threading.current_thread().name == "first":
                    first_done.set()

        with patch.object(database, "_apply_retire_transition", side_effect=scheduled):
            threads = [threading.Thread(target=worker, name=n) for n in ("first", "second")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(20)
        self.assertEqual(errors, [])

    async def test_concurrent_refresh_after_first_completes_gives_one_event(self):
        """G05: оба прочли retire → первый завершил событие и удаление → второй продолжил → одно событие."""
        await self.prepare_deferred_return()
        self.run_two_refreshes(second_waits_for_first=True)
        self.assertEqual((len(self.events()), self.outbox()), (1, 1))
        self.assertEqual(self.refresh_jobs("drift"), 0)

    async def test_concurrent_refresh_interleaved_gives_one_event(self):
        """G05, обратное чередование: оба идут одновременно после чтения retire — одно событие."""
        await self.prepare_deferred_return()
        self.run_two_refreshes(second_waits_for_first=False)
        self.assertEqual((len(self.events()), self.outbox()), (1, 1))
        self.assertEqual(self.refresh_jobs("drift"), 0)

    async def test_stale_refresh_does_not_confirm_a_newer_retirement(self):
        """Поколение: новое снятие во время старой обработки не подтверждается старым вызовом."""
        await self.prepare_deferred_return()
        original = database._apply_retire_transition

        def retire_again_then_apply(*args):
            applied = original(*args)
            with database.get_connection() as conn:  # тем временем — новое настоящее снятие
                database._queue_watch_refresh_conn(conn, [self.pid("drift")])
                conn.commit()
            return applied

        with patch.object(database, "_apply_retire_transition", side_effect=retire_again_then_apply):
            database.refresh_retired_watches([self.pid("drift")])
        with database.get_connection() as conn:
            phase = conn.execute("SELECT phase FROM watch_refresh WHERE product_id = ?",
                                 (self.pid("drift"),)).fetchone()
        self.assertEqual(phase[0] if phase else None, "retire", "новое снятие потеряно старым вызовом")

    async def test_startup_with_20000_watch_refresh_jobs_opens_port_before_background_drain_finishes(self):
        """P-6.4.12 / Задача 1: база с 20 000+ заданиями watch_refresh не держит порт закрытым при старте."""
        from aiohttp.test_utils import TestClient, TestServer
        from web import server

        await self.prepare_old_base()
        bulk_ids = [f"bulk-{self.tag}-{i}" for i in range(20_500)]
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_refresh")
            database._queue_watch_refresh_conn(conn, [self.pid("drift"), *bulk_ids])
            conn.commit()
        total_seeded = len(bulk_ids) + 1

        orig_refresh = database.refresh_retired_watches
        first_page_started = threading.Event()
        release_drain = threading.Event()
        pages_seen = []

        def gated_refresh(product_ids):
            pages_seen.append(len(product_ids))
            if len(pages_seen) == 1:
                first_page_started.set()
                release_drain.wait(timeout=2.0)
            return orig_refresh(product_ids)

        async def noop_worker(*_args, **_kwargs):
            return

        with (
            patch.object(database, "refresh_retired_watches", side_effect=gated_refresh),
            patch.object(server, "auto_scan_background_worker", side_effect=noop_worker),
            patch.object(server, "ai_normalize_background_worker", side_effect=noop_worker),
            patch.object(server, "notification_worker", side_effect=noop_worker),
        ):
            t0 = time.monotonic()
            app = server.create_app()
            create_app_elapsed = time.monotonic() - t0
            with database.get_connection() as conn:
                after_create_app = conn.execute("SELECT COUNT(*) FROM watch_refresh").fetchone()[0]
            self.assertEqual(
                after_create_app,
                total_seeded,
                "create_app()/init_db() не должен синхронно разбирать watch_refresh до открытия порта",
            )
            self.assertLess(create_app_elapsed, 1.5)

            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/live")
                self.assertEqual(resp.status, 200)
                self.assertEqual(await resp.json(), {"status": "live"})
                with database.get_connection() as conn:
                    pending_at_live = conn.execute("SELECT COUNT(*) FROM watch_refresh").fetchone()[0]
                self.assertGreater(
                    pending_at_live,
                    0,
                    "/api/live должен отвечать 200 до окончания фонового разбора 20 000+ заданий",
                )
                release_drain.set()
                task = app.get("startup_watch_refresh_task")
                self.assertIsNotNone(task)
                drained = await task
                self.assertEqual(drained, total_seeded)

        with database.get_connection() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM watch_refresh").fetchone()[0]
        self.assertEqual(remaining, 0)
        self.assertGreaterEqual(len(pages_seen), 41)
        await self.assert_return_notifies_once()

    async def test_background_watch_refresh_failure_keeps_remaining_jobs_for_retry(self):
        """P-6.4.12 / Задача 1: при сбое фонового drain невыполненные задания остаются и повторяются."""
        from aiohttp.test_utils import TestClient, TestServer
        from web import server

        await self.prepare_old_base()
        bulk_ids = [f"failbulk-{self.tag}-{i}" for i in range(1_200)]
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_refresh")
            database._queue_watch_refresh_conn(conn, [*bulk_ids, self.pid("drift")])
            conn.commit()

        orig_refresh = database.refresh_retired_watches
        calls = 0

        def fail_second_page(product_ids):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("synthetic page 2 failure")
            return orig_refresh(product_ids)

        async def noop_worker(*_args, **_kwargs):
            return

        with (
            patch.object(database, "refresh_retired_watches", side_effect=fail_second_page),
            patch.object(server, "auto_scan_background_worker", side_effect=noop_worker),
            patch.object(server, "ai_normalize_background_worker", side_effect=noop_worker),
            patch.object(server, "notification_worker", side_effect=noop_worker),
        ):
            app = server.create_app()
            self.assertEqual(calls, 0, "create_app() не должен вызывать refresh_retired_watches синхронно")
            async with TestClient(TestServer(app)) as client:
                task = app.get("startup_watch_refresh_task")
                self.assertIsNotNone(task)
                done_before_fail = await task
                self.assertEqual(done_before_fail, 500)
                resp = await client.get("/api/live")
                self.assertEqual(resp.status, 200)

        with database.get_connection() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM watch_refresh").fetchone()[0]
        self.assertEqual(remaining, 701, "оставшиеся страницы должны сохраниться для повтора")

        # Повтор (суточное обслуживание или следующий старт) доводит остаток до конца
        self.assertEqual(database.drain_watch_refresh(), 701)
        self.assertEqual(self.refresh_jobs("drift"), 0)
        await self.assert_return_notifies_once()
