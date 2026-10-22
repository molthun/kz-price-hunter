"""Обслуживание FTS5: optimize_fts не ломает индекс и действительно подключён к рабочим путям."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import pathlib
import unittest

import database


class OptimizeFtsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def test_optimize_runs_on_empty_and_repeats_safely(self):
        database.optimize_fts()
        database.optimize_fts()
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # FTS-таблица доступна и после обслуживания; заведомо отсутствующее слово даёт 0
            self.assertEqual(
                conn.execute("SELECT count(*) FROM products_fts "
                             "WHERE products_fts MATCH 'zzz_no_such_word_zzz'")
                .fetchone()[0], 0)

    def test_optimize_is_wired_into_working_paths(self):
        """Функция, которую никто не зовёт, ничего не оптимизирует: проверяем сами вызовы."""
        root = pathlib.Path(test_support.REPO_DIR)
        main_py = (root / "main.py").read_text(encoding="utf-8")
        server_py = (root / "web" / "server.py").read_text(encoding="utf-8")
        self.assertIn("optimize_fts()", main_py)
        self.assertIn("optimize_fts", server_py)


if __name__ == "__main__":
    unittest.main()
