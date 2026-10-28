"""Контракт журнала очереди записи: приём → захват → эффекты → подтверждение (R01–R06 повторной приёмки).

Каждый тест воспроизводит сценарий отказа из отчёта Codex 28.10.2026 на рабочем коде:
настоящий SQLite, настоящие процессы при kill -9, настоящий воркер и его стадии.
"""

from tests import test_support  # noqa: F401
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

import database
import queue_service
from queue_service import ProductQueueManager, QUEUE_OWNER, owner_is_dead
from web import server

REPO = test_support.REPO_DIR
WATCH_USER = 987654321


def prod(pid, price=100, title="Queue Contract Laptop"):
    return {"id": pid, "title": title, "shop": "Contract", "price": price,
            "url": "https://example.invalid/" + pid, "category": "laptops", "city": "Астана"}


def reset_queue_tables():
    with database.get_connection() as conn:
        conn.execute("DELETE FROM product_queue_spool")
        conn.execute("DELETE FROM product_queue_history")
        conn.execute("DELETE FROM scheduler_lease")
        conn.commit()


def spool_rows():
    with database.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT task_id, status, stage, core_done, attempts, claimed_by FROM product_queue_spool ORDER BY id")]


def product_price(pid):
    with database.get_connection() as conn:
        row = conn.execute("SELECT current_price FROM products WHERE id = ?", (pid,)).fetchone()
        return row[0] if row else None


class QueueContractBase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        reset_queue_tables()

    def tearDown(self):
        reset_queue_tables()  # общая тестовая база: отказы не должны просочиться в чужие проверки health

    def qm(self, **kwargs):
        return ProductQueueManager(**kwargs)

    async def initialize(self, qm):
        with patch.object(queue_service, "start_redis_daemon_if_available", return_value=False), \
                patch.object(queue_service, "_is_port_open", return_value=False):
            await qm.initialize()

    async def run_item(self, qm, item):
        with patch.object(server, "check_market_arbitrage", return_value=None):
            await server._execute_queue_item(item)
        return await qm.ack(item)


class AdmissionTest(QueueContractBase):
    async def test_spool_failure_is_explicit_refusal_not_memory_fallback(self):
        # R02: отказ журнала — явный отказ, без task_id и без задачи «в памяти»
        qm = self.qm()
        with patch.object(database, "spool_admit", side_effect=sqlite3.OperationalError("synthetic")):
            admission = await qm.enqueue([prod("refused")], "Contract", {}, shop_key="refused")
        self.assertFalse(admission)
        self.assertTrue(admission.reason.startswith("spool_unavailable"))
        self.assertIsNone(admission.task_id)
        self.assertEqual(spool_rows(), [])
        self.assertIsNone(await qm.dequeue(timeout=0.05))

    async def test_concurrent_admission_respects_budget(self):
        # R02: проверка бюджета и вставка — одна транзакция; восемь потоков при бюджете 1
        barrier = threading.Barrier(8)
        results = []

        def admit(n):
            barrier.wait()
            results.append(database.spool_admit(f"race-{n}", "race", "Contract", None, {"prods": []},
                                                None, 1, 100))

        threads = [threading.Thread(target=admit, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("full"), 7)
        self.assertEqual(len(spool_rows()), 1)

    async def test_full_budget_waits_then_refuses(self):
        qm = self.qm(maxsize=1)
        self.assertTrue(await qm.enqueue([prod("b1")], "Contract", {}, shop_key="budget"))
        started = time.monotonic()
        admission = await qm.enqueue([prod("b2")], "Contract", {}, shop_key="budget", wait_seconds=0.6)
        self.assertFalse(admission)
        self.assertEqual(admission.reason, "full")
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        # Другой магазин в свой бюджет помещается
        self.assertTrue(await qm.enqueue([prod("b3")], "Contract", {}, shop_key="other", wait_seconds=0))

    async def test_admission_is_fenced_by_scheduler_lease_in_insert_transaction(self):
        # R04: результаты обхода принимаются, только пока аренда у поставщика
        qm = self.qm()
        owner = server.SCHEDULER_OWNER
        no_lease = await qm.enqueue([prod("f1")], "Contract", {}, shop_key="fence", lease_owner=owner)
        self.assertEqual(no_lease.reason, "fenced")
        self.assertTrue(database.acquire_scheduler_lease("other-process", 60))
        foreign = await qm.enqueue([prod("f2")], "Contract", {}, shop_key="fence", lease_owner=owner)
        self.assertEqual(foreign.reason, "fenced")
        reset_queue_tables()
        self.assertTrue(database.acquire_scheduler_lease(owner, 60))
        own = await qm.enqueue([prod("f3")], "Contract", {}, shop_key="fence", lease_owner=owner)
        self.assertTrue(own)
        self.assertEqual([r["task_id"] for r in spool_rows()], [own.task_id])


class AckTest(QueueContractBase):
    async def test_ack_while_sqlite_writer_holds_lock_does_not_block_event_loop(self):
        # R01: ACK ждёт блокировку в пуле журнала, цикл событий свободен
        qm = self.qm()
        await qm.enqueue([prod("ack")], "Contract", {}, shop_key="ack")
        item = await qm.dequeue(timeout=0.05)
        await server._execute_queue_item(item)
        holder = sqlite3.connect(database.DB_PATH, check_same_thread=False)
        holder.execute("BEGIN IMMEDIATE")

        def release():
            time.sleep(0.45)
            holder.rollback()
            holder.close()

        t = threading.Thread(target=release)
        t.start()
        lags = []

        async def ticker():
            for _ in range(15):
                started = time.monotonic()
                await asyncio.sleep(0.02)
                lags.append(time.monotonic() - started)

        tick = asyncio.create_task(ticker())
        acked = await qm.ack(item)
        await tick
        t.join()
        self.assertTrue(acked)
        self.assertLess(max(lags), 0.2, lags)
        self.assertEqual(spool_rows(), [])

    async def test_ack_with_lost_claim_is_reported_and_keeps_task(self):
        qm = self.qm()
        await qm.enqueue([prod("lost-ack")], "Contract", {}, shop_key="lost")
        item = await qm.dequeue(timeout=0.05)
        stolen = dict(item, claim_token="not-ours")
        self.assertFalse(await qm.ack(stolen))
        self.assertEqual(len(spool_rows()), 1)


class ClaimOwnershipTest(QueueContractBase):
    def test_owner_liveness(self):
        host = __import__("socket").gethostname()
        self.assertFalse(owner_is_dead(QUEUE_OWNER))
        self.assertTrue(owner_is_dead(f"{host}:{os.getpid()}:previous"))  # рестарт с тем же PID
        self.assertFalse(owner_is_dead(f"other-host:1:x"))               # чужой хост — ждём срока
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.assertTrue(owner_is_dead(f"{host}:{dead.pid}:x"))

    async def test_second_instance_does_not_steal_live_claim(self):
        # R04: восстановление при старте не забирает задачу живого владельца
        first = self.qm()
        await first.enqueue([prod("live")], "Contract", {}, shop_key="live")
        item = await first.dequeue(timeout=0.05)
        second = self.qm(owner="other-host:42:second")
        await self.initialize(second)
        self.assertIsNone(await second.dequeue(timeout=0.05))
        self.assertEqual(spool_rows()[0]["claimed_by"], QUEUE_OWNER)
        self.assertTrue(await self.run_item(first, item))

    async def test_commit_after_claim_lost_writes_nothing(self):
        # R04: проверка владельца — в транзакции записи, а не один раз до await
        first = self.qm()
        await first.enqueue([prod("fenced-commit", 70)], "Contract", {}, shop_key="fc")
        item = await first.dequeue(timeout=0.05)
        with database.get_connection() as conn:  # владелец «завис», срок захвата истёк
            conn.execute("UPDATE product_queue_spool SET claim_expires_at = 0")
            conn.commit()
        second = self.qm(owner="other-host:7:second")
        taken = await second.dequeue(timeout=0.05)
        self.assertEqual(taken["task_id"], item["task_id"])
        with self.assertRaises(server.ClaimLost):
            await server._execute_queue_item(item)
        self.assertIsNone(product_price("fenced-commit"))
        self.assertFalse(await first.ack(item))
        self.assertTrue(await self.run_item(second, taken))
        self.assertEqual(product_price("fenced-commit"), 70)
        self.assertEqual(spool_rows(), [])

    async def test_tasks_of_one_shop_run_in_admission_order(self):
        qm = self.qm()
        await qm.enqueue([prod("order", 100)], "Contract", {}, shop_key="order")
        await qm.enqueue([prod("order", 90)], "Contract", {}, shop_key="order")
        await qm.enqueue([prod("other-order", 50)], "Contract", {}, shop_key="order-2")
        first = await qm.dequeue(timeout=0.05)
        parallel = await self.qm(owner="other-host:9:x").dequeue(timeout=0.05)
        self.assertEqual(parallel["shop_key"], "order-2")  # второй задачи магазина нет, пока первая не завершена
        self.assertIsNone(await self.qm(owner="other-host:9:y").dequeue(timeout=0.05))
        self.assertTrue(await self.run_item(qm, first))
        second = await qm.dequeue(timeout=0.05)
        self.assertTrue(await self.run_item(qm, second))
        self.assertEqual(product_price("order"), 90)


CHILD = textwrap.dedent("""
    import asyncio, os, signal, sys
    sys.path.insert(0, {repo!r})
    import database, queue_service
    from queue_service import ProductQueueManager
    from web import server

    def prod(pid, price):
        return {{"id": pid, "title": "Crash Laptop", "shop": "Contract", "price": price,
                "url": "https://example.invalid/" + pid, "category": "laptops", "city": "Астана"}}

    async def main(mode, prefix, price):
        database.SPOOL_CHUNK_SIZE = 1
        qm = ProductQueueManager()
        prods = [prod(f"{{prefix}}-{{n}}", price) for n in range(3)]
        await qm.enqueue(prods, "Contract", {{}}, shop_key=prefix)
        item = await qm.dequeue(timeout=0.1)
        if mode == "after_chunk":
            database.spool_apply_chunk(item["task_id"], item["claim_token"], 0, prods[:1])
        elif mode == "after_core":
            for n in range(3):
                database.spool_apply_chunk(item["task_id"], item["claim_token"], n, prods[n:n + 1])
            database.spool_finish_core(item["task_id"], item["claim_token"], [p["id"] for p in prods], None, None)
        elif mode == "before_ack":
            await server._execute_queue_item(item)
        print("CLAIMED", item["task_id"], flush=True)
        os.kill(os.getpid(), signal.SIGKILL)

    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
""").format(repo=str(REPO))


class SigkillRecoveryTest(QueueContractBase):
    async def crash_child(self, mode, prefix, price):
        proc = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-c", CHILD, mode, prefix, str(price)],
            env=dict(os.environ, DATA_DIR=str(database.DB_PATH.parent)), cwd=str(REPO), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr[-2000:])
        claimed = [line.split()[1] for line in proc.stdout.splitlines() if line.startswith("CLAIMED ")]
        self.assertEqual(len(claimed), 1, proc.stdout[-2000:])
        return claimed[0]

    async def recover_and_finish(self, task_id):
        qm = self.qm()
        await self.initialize(qm)  # тот же хост, процесса нет — захват освобождается сразу
        item = await qm.dequeue(timeout=0.1)
        self.assertIsNotNone(item, spool_rows())
        self.assertEqual(item["task_id"], task_id)
        with patch.object(database, "SPOOL_CHUNK_SIZE", 1):
            self.assertTrue(await self.run_item(qm, item))
        self.assertEqual(spool_rows(), [])
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM product_queue_history").fetchone()[0], 0)

    async def test_real_sigkill_at_each_stage_completes_accepted_task(self):
        # R03: настоящий kill -9 после захвата, после порции, после обязательной стадии и до ACK
        for n, mode in enumerate(("after_claim", "after_chunk", "after_core", "before_ack")):
            with self.subTest(mode=mode):
                prefix = f"crash-{mode}"
                task_id = await self.crash_child(mode, prefix, 500 + n)
                self.assertEqual(spool_rows()[0]["status"], "processing")
                await self.recover_and_finish(task_id)
                for k in range(3):
                    self.assertEqual(product_price(f"{prefix}-{k}"), 500 + n)

    async def test_repeated_crashes_across_generations(self):
        # Несколько поколений процессов подряд: ни одна принятая задача не отклонена
        for generation, price in enumerate((300, 280, 260)):
            task_id = await self.crash_child("after_chunk", "gen", price)
            await self.recover_and_finish(task_id)
            self.assertEqual(product_price("gen-0"), price, generation)
        with database.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM product_queue_spool WHERE status IN ('rejected','dead_letter')").fetchone()[0], 0)


class RetryAfterPartialCommitTest(QueueContractBase):
    def setUp(self):
        super().setUp()
        database.upsert_telegram_user({"id": WATCH_USER, "first_name": "Contract"})
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_events")
            conn.execute("DELETE FROM watch_state")
            conn.execute("DELETE FROM watches")
            conn.commit()

    def watch_events(self):
        return database.watch_events(WATCH_USER)

    async def test_retry_after_anomaly_failure_keeps_price_drop_event(self):
        # R05: цена 100 → 80, сбой после записи товара; повтор обязан дать событие наблюдения
        pid = "retry-drop"
        database.save_or_update_products_batch([prod(pid, 100, "Retry Drop Laptop")])
        database.create_watch(WATCH_USER, {"kind": "search", "target": "Retry Drop Laptop", "condition": "any_drop",
                                           "quiet_from": "", "quiet_to": "", "cooldown_hours": 0})
        database.evaluate_watches(database.watched_offers([pid]))
        qm = self.qm()
        await qm.enqueue([prod(pid, 80, "Retry Drop Laptop")], "Contract", {}, shop_key="retry")
        item = await qm.dequeue(timeout=0.05)
        with patch.object(server, "check_anomaly", side_effect=RuntimeError("post-commit failure")):
            with self.assertRaises(RuntimeError):
                await server._execute_queue_item(item)
        self.assertEqual(product_price(pid), 80)  # обязательная стадия уже записана
        self.assertEqual(await qm.nack(item, error="post-commit failure"), "pending")
        retry = await qm.dequeue(timeout=0.05)
        self.assertEqual(retry["stage"], "derived")
        with patch.object(server, "check_anomaly", return_value=None):
            self.assertTrue(await self.run_item(qm, retry))
        events = self.watch_events()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]["price"], 80)

    async def test_retry_after_watch_failure_does_not_duplicate_event(self):
        pid = "retry-watch-fail"
        database.save_or_update_products_batch([prod(pid, 100, "Retry Watch Laptop")])
        database.create_watch(WATCH_USER, {"kind": "search", "target": "Retry Watch Laptop", "condition": "any_drop",
                                           "quiet_from": "", "quiet_to": "", "cooldown_hours": 0})
        database.evaluate_watches(database.watched_offers([pid]))
        qm = self.qm()
        await qm.enqueue([prod(pid, 70, "Retry Watch Laptop")], "Contract", {}, shop_key="retry-w")
        item = await qm.dequeue(timeout=0.05)
        real = database.evaluate_watches
        calls = []

        def fail_after_write(offers, now=None):
            calls.append(1)
            real(offers, now)
            raise RuntimeError("after watch commit")

        with patch.object(database, "evaluate_watches", side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):
                await server._execute_queue_item(item)
        await qm.nack(item, error="watch failure")
        retry = await qm.dequeue(timeout=0.05)
        self.assertTrue(await self.run_item(qm, retry))
        self.assertEqual(len(self.watch_events()), 1)

    async def test_failures_exhaust_to_dead_letter_with_payload(self):
        qm = self.qm()
        await qm.enqueue([prod("poison")], "Contract", {}, shop_key="poison")
        for attempt in range(4):
            item = await qm.dequeue(timeout=0.05)
            status = await qm.nack(item, error=f"boom {attempt}", max_retries=3)
        self.assertEqual(status, "dead_letter")
        failures = database.spool_failures()
        self.assertEqual([f["last_error"] for f in failures], ["boom 3"])
        self.assertIsNone(await qm.dequeue(timeout=0.05))
        self.assertEqual(database.spool_resolve_failures("retry"), 1)
        item = await qm.dequeue(timeout=0.05)
        self.assertTrue(await self.run_item(qm, item))
        self.assertEqual(product_price("poison"), 100)


class BarrierAndReadinessTest(QueueContractBase):
    async def test_unreadable_spool_is_not_drained(self):
        # R06: ошибка наблюдения — не успех
        qm = self.qm()
        admission = await qm.enqueue([prod("unreadable")], "Contract", {}, shop_key="unreadable")
        with patch.object(database, "spool_task_states", side_effect=sqlite3.OperationalError("synthetic")):
            self.assertFalse(await qm.wait_drained([admission.task_id], timeout=0.2))

    async def test_old_failure_of_same_shop_does_not_poison_new_scan(self):
        qm = self.qm()
        old = await qm.enqueue([prod("old-fail")], "Contract", {}, shop_key="same-shop")
        item = await qm.dequeue(timeout=0.05)
        await qm.nack(item, error="old failure", max_retries=0)
        new = await qm.enqueue([prod("new-scan")], "Contract", {}, shop_key="same-shop")
        item = await qm.dequeue(timeout=0.05)
        self.assertTrue(await self.run_item(qm, item))
        self.assertTrue(await qm.wait_drained([new.task_id], timeout=0.5))
        self.assertFalse(await qm.wait_drained([old.task_id, new.task_id], timeout=0.5))

    async def test_worker_loop_drains_scan_tasks(self):
        qm = self.qm()
        with patch.object(queue_service, "product_queue", qm), \
                patch.object(server, "check_market_arbitrage", return_value=None):
            worker = asyncio.create_task(server.single_db_writer_worker())
            try:
                ids = []
                for n in range(3):
                    admission = await qm.enqueue([prod(f"worker-{n}", 10 + n)], "Contract", {}, shop_key="worker")
                    ids.append(admission.task_id)
                self.assertTrue(await qm.wait_drained(ids, timeout=10))
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(product_price("worker-2"), 12)
        self.assertEqual(spool_rows(), [])

    async def health(self, qm):
        from aiohttp.test_utils import make_mocked_request
        qm.is_worker_running = True
        with patch.object(queue_service, "product_queue", qm):
            resp = await server.health_handler(make_mocked_request("GET", "/api/readiness"))
        return resp.status, json.loads(resp.text)

    async def test_readiness_reports_unresolved_failures_and_fails_closed(self):
        qm = self.qm()
        self.assertEqual((await self.health(qm))[1]["status"], "ok")
        database.spool_admit("dl", "dl-shop", "Contract", None, {"prods": []})
        with database.get_connection() as conn:
            conn.execute("UPDATE product_queue_spool SET status = 'dead_letter' WHERE task_id = 'dl'")
            conn.commit()
        status, body = await self.health(qm)
        self.assertEqual((status, body["status"], body["dead_letters"]), (200, "degraded", 1))
        with database.get_connection() as conn:
            conn.execute("UPDATE product_queue_spool SET status = 'rejected' WHERE task_id = 'dl'")
            conn.commit()
        status, body = await self.health(qm)
        self.assertEqual((body["status"], body["rejected"]), ("degraded", 1))
        self.assertEqual(database.spool_resolve_failures("archive"), 1)
        self.assertEqual((await self.health(qm))[1]["status"], "ok")
        with patch.object(database, "spool_stats", side_effect=sqlite3.OperationalError("synthetic")):
            status, body = await self.health(qm)
        self.assertEqual((status, body["status"], body["queue"]), (503, "unavailable", "unreadable"))


if __name__ == "__main__":
    unittest.main()
