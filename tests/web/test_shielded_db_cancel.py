"""S01 (приёмка 6.4.13): отмена фонового разбора watch_refresh дожидается текущей страницы.

Операции идут через настоящий _run_db_shielded и настоящий пул _db_executor; управляемое
событие лишь задерживает синхронную функцию, сама запись не подменяется.
"""

from tests import test_support  # noqa: F401
import asyncio
import threading
import time
import unittest
from unittest.mock import patch

import database
from web import server


class ShieldedDbCancelTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_refresh")
            conn.commit()

    def tearDown(self):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_refresh")
            conn.commit()

    async def test_cancel_waits_for_running_operation(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(10)
            finished.set()
            return 42

        task = asyncio.create_task(server._run_db_shielded(slow))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        self.assertFalse(task.done(), "отменённый вызывающий завершился, пока операция идёт")
        task.cancel()  # повторная отмена тоже не прерывает ожидание
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())

    async def test_operation_error_during_cancel_is_reported_not_swallowed_silently(self):
        started, release = threading.Event(), threading.Event()

        def failing():
            started.set()
            release.wait(10)
            raise RuntimeError("page failed")

        task = asyncio.create_task(server._run_db_shielded(failing))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()
        with patch("builtins.print") as printed:
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(any("RuntimeError" in str(c) for c in printed.call_args_list))

    async def test_cancel_during_page_waits_for_page_and_keeps_the_rest(self):
        with database.get_connection() as conn:
            conn.executemany("INSERT INTO watch_refresh (product_id, created_at, phase, gen) VALUES (?, 0, 'retire', 0)",
                             [(f"s01-{n}",) for n in range(600)])
            conn.commit()
        original = database.drain_watch_refresh
        entered, release = threading.Event(), threading.Event()
        pages = []

        def gated_page(*args):
            pages.append(args)
            entered.set()
            release.wait(10)
            return original(*args)

        with patch.object(database, "drain_watch_refresh", side_effect=gated_page):
            worker = asyncio.create_task(server.startup_watch_refresh_worker())
            await asyncio.to_thread(entered.wait, 5)
            worker.cancel()
            await asyncio.sleep(0.05)
            self.assertFalse(worker.done(), "воркер завершился до окончания страницы")
            self.assertEqual(database.watch_refresh_count(), 600)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await worker
            remaining = database.watch_refresh_count()
            time.sleep(0.1)  # после завершения воркера в базу больше никто не пишет
            self.assertEqual(database.watch_refresh_count(), remaining)
        self.assertEqual(len(pages), 1, "после отмены стартовала новая страница")
        self.assertEqual(remaining, 100, "страница не дописала свои 500 заданий или потеряла остаток")


if __name__ == "__main__":
    unittest.main()
