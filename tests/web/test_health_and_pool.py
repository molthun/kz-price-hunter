from tests import test_support  # noqa: F401
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
import sqlite3

from aiohttp.test_utils import AioHTTPTestCase
from web import server
from queue_service import product_queue, MiniRedisClient
import database


class HealthAndPoolTest(AioHTTPTestCase):
    async def get_application(self):
        app = server.create_app()
        app.cleanup_ctx.clear()
        return app

    async def test_live_probe_returns_200_instantly(self):
        resp = await self.client.get("/api/live")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data.get("status"), "live")

    async def test_health_probe_returns_200_when_worker_and_db_ready(self):
        # При запущенном воркере и инициализированной БД readiness возвращает 200
        try:
            product_queue.is_worker_running = True
            resp = await self.client.get("/api/health")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["db"], "ok")
            self.assertTrue(data["worker_running"])
        finally:
            product_queue.is_worker_running = False

    async def test_readiness_alias_skips_session_lookup(self):
        # C06: /api/readiness — alias /api/health, сессию в базе не ищет даже с cookie
        from auth import SESSION_COOKIE
        with patch("auth.get_session_user", side_effect=AssertionError("session lookup")) as lookup:
            for path in ("/api/live", "/api/health", "/api/readiness"):
                resp = await self.client.get(path, cookies={SESSION_COOKIE: "x" * 32})
                self.assertIn(resp.status, (200, 503), path)
        lookup.assert_not_called()

    async def test_readiness_probe_returns_503_when_worker_stopped(self):
        # B07: Остановленный пишущий воркер возвращает HTTP 503
        product_queue.is_worker_running = False
        resp = await self.client.get("/api/health")
        self.assertEqual(resp.status, 503)
        data = await resp.json()
        self.assertEqual(data["status"], "unavailable")
        self.assertFalse(data["worker_running"])

    async def test_readiness_probe_returns_503_on_empty_db(self):
        # B07: Пустая база без обязательных таблиц не считается готовой
        original_db = database.DB_PATH
        empty_path = Path(tempfile.mkdtemp()) / "empty_test.db"
        try:
            database.DB_PATH = empty_path
            self.assertFalse(server._check_db_readiness_sync())
            # Проверяем, что файл не создался
            self.assertFalse(empty_path.exists())
        finally:
            database.DB_PATH = original_db

    async def test_redis_commands_have_deadline(self):
        # B05: Silent peer, не отвечающий на команды, прерывается по дедлайну
        handlers = []
        stop = asyncio.Event()

        async def silent_peer(reader, writer):
            handlers.append(asyncio.current_task())
            try:
                await reader.read(4096)
                await stop.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        srv = await asyncio.start_server(silent_peer, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        client = MiniRedisClient("127.0.0.1", port)
        try:
            await client.connect()
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                await client.get("key", timeout=0.1)
            duration = time.monotonic() - start
            self.assertLess(duration, 0.3)
        finally:
            await client.close()
            stop.set()
            srv.close()
            await srv.wait_closed()
            await asyncio.gather(*handlers, return_exceptions=True)

    async def test_db_executor_keeps_event_loop_free(self):
        # B01: Удержание SQLite блокировки сторонним потоком не блокирует event loop
        hold = sqlite3.connect(database.DB_PATH, check_same_thread=False)
        hold.execute("BEGIN IMMEDIATE")

        def release():
            time.sleep(0.3)
            hold.rollback()
            hold.close()

        import threading
        t = threading.Thread(target=release)
        t.start()

        async def tick():
            t0 = time.monotonic()
            await asyncio.sleep(0.02)
            return time.monotonic() - t0

        tick_task = asyncio.create_task(tick())
        await asyncio.sleep(0)

        with patch.object(server, "_scan_task_body", new=AsyncMock()):
            scan_task = asyncio.create_task(server._do_scan_task([]))
            delay = await tick_task
            await scan_task

        t.join()
        # Таймер на 20мс должен сработать практически без задержки (< 60мс), а не ждать 300мс
        self.assertLess(delay, 0.08, f"Event loop был заблокирован на {delay}с")


if __name__ == "__main__":
    unittest.main()
