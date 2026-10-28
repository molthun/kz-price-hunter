"""Тесты для queue_service и Single DB-Writer Worker."""

from tests import test_support  # noqa: F401
import asyncio
import os
import shutil
import tempfile
import time
import unittest
from queue_service import MiniRedisClient, ProductQueueManager


class TestQueueService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmp_dir

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_in_memory_queue_enqueue_dequeue(self):
        qm = ProductQueueManager()
        # По умолчанию без Redis
        self.assertFalse(qm.use_redis)
        self.assertEqual(await qm.qsize(), 0)

        sample_prods = [{"id": "p1", "title": "Товар 1", "price": 1000, "url": "http://x"}]
        await qm.enqueue(sample_prods, "TestShop", {"min_discount": 10}, shop_key="test_shop")

        self.assertEqual(await qm.qsize(), 1)
        item = await qm.dequeue(timeout=0.1)
        self.assertIsNotNone(item)
        self.assertEqual(item["shop_name"], "TestShop")
        self.assertEqual(item["shop_key"], "test_shop")
        self.assertEqual(len(item["prods"]), 1)
        self.assertEqual(item["prods"][0]["id"], "p1")
        self.assertEqual(await qm.qsize(), 0)

    async def test_in_memory_cache_ttl(self):
        qm = ProductQueueManager()
        await qm.set_cached("my_key", {"foo": "bar"}, ttl_seconds=1)
        res = await qm.get_cached("my_key")
        self.assertEqual(res, {"foo": "bar"})

        # Истечение TTL
        time.sleep(1.05)
        res_expired = await qm.get_cached("my_key")
        self.assertIsNone(res_expired)

    async def test_mini_redis_client_resp(self):
        """Проверка MiniRedisClient на mock RESP-сервере."""
        lines_to_send = []

        async def redis_mock_handler(reader, writer):
            while not reader.at_eof():
                try:
                    line = await reader.readline()
                    if not line:
                        break
                    if line.startswith(b"*"):
                        # Command args count
                        count = int(line[1:-2])
                        args = []
                        for _ in range(count):
                            arg_len_line = await reader.readline()
                            arg_len = int(arg_len_line[1:-2])
                            arg_val = await reader.readexactly(arg_len)
                            await reader.readexactly(2) # \r\n
                            args.append(arg_val.decode("utf-8").upper())

                        cmd = args[0]
                        if cmd == "PING":
                            writer.write(b"+PONG\r\n")
                        elif cmd == "RPUSH":
                            writer.write(b":1\r\n")
                        elif cmd == "LLEN":
                            writer.write(b":5\r\n")
                        elif cmd == "LPOP":
                            writer.write(b"$5\r\nhello\r\n")
                        elif cmd == "SETEX":
                            writer.write(b"+OK\r\n")
                        elif cmd == "GET":
                            writer.write(b"$4\r\n1234\r\n")
                        elif cmd == "DEL":
                            writer.write(b":1\r\n")
                        await writer.drain()
                except Exception:
                    break
            writer.close()

        server = await asyncio.start_server(redis_mock_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        client = MiniRedisClient("127.0.0.1", port)
        try:
            connected = await client.connect()
            self.assertTrue(connected)
            self.assertTrue(client.is_connected())

            ping_res = await client.ping()
            self.assertTrue(ping_res)

            rpush_res = await client.rpush("k", "v")
            self.assertEqual(rpush_res, 1)

            llen_res = await client.llen("k")
            self.assertEqual(llen_res, 5)

            lpop_res = await client.lpop("k")
            self.assertEqual(lpop_res, "hello")

            setex_res = await client.setex("k", 10, "val")
            self.assertTrue(setex_res)

            get_res = await client.get("k")
            self.assertEqual(get_res, "1234")

            del_res = await client.delete("k")
            self.assertEqual(del_res, 1)
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    async def test_single_db_writer_worker_execution(self):
        from database import init_db, get_connection
        from web.server import _execute_queue_item

        init_db()

        test_item = {
            "prods": [{
                "id": "item_q_1",
                "shop": "Тестовый магазин",
                "title": "Тестовый товар очереди",
                "price": 25000,
                "url": "https://example.com/p1",
                "category": "Тест"
            }],
            "shop_name": "Тестовый магазин",
            "candidate_settings": {},
            "shop_key": "test_shop",
        }

        # Запускаем обработку элемента через функцию воркера
        await _execute_queue_item(test_item)

        # Проверяем, что товар появился в базе SQLite
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM products WHERE id = 'item_q_1'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "Тестовый товар очереди")
            self.assertEqual(row["current_price"], 25000)
            self.assertEqual(row["shop"], "Тестовый магазин")


if __name__ == "__main__":
    unittest.main()
