"""C-DB-01: открытие соединения не должно падать на занятой базе. Без сети.

Прод 6.2.1 лёг именно здесь: get_connection() на каждом соединении выполнял
PRAGMA journal_mode = WAL, а смена режима требует исключительного доступа.
В работающем приложении всегда открыто чужое соединение (автоскан, телеметрия,
нотифаер, планировщик), поэтому база так и не переходила в WAL, и каждое
обращение к ней падало с "database is locked" — при держащем читателе ещё и
через 15 секунд ожидания, что выедало пул потоков asyncio.to_thread.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
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

import database  # noqa: E402


class ConnectionOnBusyDatabaseTest(unittest.TestCase):
    """База намеренно НЕ в WAL: так выглядела рабочая база на проде."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / "prices.db"
        seed = sqlite3.connect(self.db)
        seed.execute("PRAGMA journal_mode = DELETE;")
        seed.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, title TEXT)")
        seed.execute("INSERT INTO products (title) VALUES ('iPhone 15')")
        seed.commit()
        seed.close()
        patcher = patch.object(database, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _holder(self, statement):
        """Чужое соединение, удерживающее базу, — как идущее сканирование магазина."""
        conn = sqlite3.connect(self.db, timeout=15)
        self.addCleanup(conn.close)
        conn.execute(statement)
        conn.execute("SELECT count(*) FROM products").fetchone()
        return conn

    def test_reader_holding_the_database_does_not_break_new_connections(self):
        self._holder("BEGIN")
        started = time.monotonic()
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM products").fetchone()[0], 1)
        self.assertLess(time.monotonic() - started, 5,
                        "открытие соединения не должно выстаивать таймаут блокировки")

    def test_writer_holding_the_database_does_not_break_new_connections(self):
        holder = self._holder("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO products (title) VALUES ('скан')")
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM products").fetchone()[0], 1)

    def test_connection_does_not_change_journal_mode(self):
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode;").fetchone()[0].lower(), "delete")

    def test_connection_waits_for_locks_instead_of_failing(self):
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA busy_timeout;").fetchone()[0], 15000)

    def test_connection_uses_normal_synchronous(self):
        """synchronous не хранится в файле базы: без установки на каждом соединении оно
        работало бы в FULL и делало полный fsync на каждом коммите (C-DB-03)."""
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA synchronous;").fetchone()[0], 1)

    def test_synchronous_is_set_even_while_a_writer_holds_the_database(self):
        """Эта прагма блокировки не требует, поэтому возвращать C-DB-01 она не должна."""
        holder = self._holder("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO products (title) VALUES ('скан')")
        started = time.monotonic()
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA synchronous;").fetchone()[0], 1)
        self.assertLess(time.monotonic() - started, 5)


class JournalModeSetupTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / "prices.db"
        patcher = patch.object(database, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_free_database_switches_to_wal(self):
        seed = sqlite3.connect(self.db)
        seed.execute("PRAGMA journal_mode = DELETE;")
        seed.execute("CREATE TABLE products (id INTEGER PRIMARY KEY)")
        seed.commit()
        seed.close()
        self.assertEqual(database.ensure_journal_mode(), "wal")
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode;").fetchone()[0].lower(), "wal")

    def test_busy_database_keeps_working_without_wal(self):
        seed = sqlite3.connect(self.db)
        seed.execute("PRAGMA journal_mode = DELETE;")
        seed.execute("CREATE TABLE products (id INTEGER PRIMARY KEY)")
        seed.commit()
        seed.close()
        holder = sqlite3.connect(self.db, timeout=15)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO products (id) VALUES (1)")
        self.assertEqual(database.ensure_journal_mode(), "delete",
                         "перейти в WAL нельзя, но это не отказ")
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM products").fetchone()[0], 0)

    def test_database_already_in_wal_is_left_alone(self):
        seed = sqlite3.connect(self.db)
        seed.execute("PRAGMA journal_mode = WAL;")
        seed.execute("CREATE TABLE products (id INTEGER PRIMARY KEY)")
        seed.commit()
        seed.close()
        holder = sqlite3.connect(self.db, timeout=15)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO products (id) VALUES (1)")
        self.assertEqual(database.ensure_journal_mode(), "wal")


if __name__ == "__main__":
    unittest.main()
