"""B13: предыдущий релиз для репетиции обновления. Без сети, на временных репозиториях.

Выбор «старшего semver-тега» давал сборке по main сверять выпуск сам с собой: тег текущего
релиза указывает на тот же коммит, репетиция проходила и ничего не проверяла. Исключение по
GITHUB_REF_NAME это не закрывало — в main-сборке имя ref'а «main», а не имя тега.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import subprocess
import tempfile
import unittest
from pathlib import Path

import release_gate


class PreviousReleaseTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = self._dir.name
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "test")

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True, check=False)

    def _commit(self, message):
        Path(self.root, "file.txt").write_text(message, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_picks_the_previous_release(self):
        self._commit("первый")
        self._git("tag", "v6.3.0")
        self._commit("второй")
        self._git("tag", "v6.4.0")
        head = self._commit("невыпущенная работа")
        report = release_gate.previous_release(self.root, head)
        self.assertTrue(report["ok"])
        self.assertEqual(report["tag"], "v6.4.0")

    def test_tag_of_the_released_commit_is_skipped(self):
        """Главный сценарий B13: собирается коммит, на котором уже стоит тег выпуска."""
        self._commit("первый")
        self._git("tag", "v6.3.1")
        head = self._commit("выпуск")
        self._git("tag", "v6.4.0")
        report = release_gate.previous_release(self.root, head)
        self.assertEqual(report["tag"], "v6.3.1",
                         "базой должен стать предыдущий релиз, а не тег выпускаемого коммита")
        self.assertIn("указывает на выпускаемый коммит",
                      [s["why"] for s in report["skipped"]])

    def test_several_tags_on_the_released_commit(self):
        self._commit("первый")
        self._git("tag", "v6.2.0")
        head = self._commit("выпуск")
        for tag in ("v6.3.0", "v6.4.0"):
            self._git("tag", tag)
        report = release_gate.previous_release(self.root, head)
        self.assertEqual(report["tag"], "v6.2.0")
        self.assertEqual(len(report["skipped"]), 2)

    def test_tag_from_another_branch_is_not_a_baseline(self):
        base = self._commit("общий")
        self._git("checkout", "-q", "-b", "side")
        self._commit("чужая ветка")
        self._git("tag", "v9.9.9")
        self._git("checkout", "-q", "main")
        self._git("reset", "-q", "--hard", base)
        self._git("tag", "v6.1.0")
        head = self._commit("выпуск с main")
        report = release_gate.previous_release(self.root, head)
        self.assertEqual(report["tag"], "v6.1.0", "старший тег с чужой ветки предыдущим релизом не является")
        self.assertIn("не является предком выпускаемого коммита",
                      [s["why"] for s in report["skipped"]])

    def test_versions_compare_numerically(self):
        """v6.10.0 новее v6.9.0, хотя по алфавиту наоборот."""
        self._commit("первый")
        self._git("tag", "v6.9.0")
        self._commit("второй")
        self._git("tag", "v6.10.0")
        head = self._commit("работа")
        self.assertEqual(release_gate.previous_release(self.root, head)["tag"], "v6.10.0")

    def test_non_release_tags_are_ignored(self):
        self._commit("первый")
        for tag in ("rc-1", "v6.4.0-rc1", "latest"):
            self._git("tag", tag)
        head = self._commit("работа")
        report = release_gate.previous_release(self.root, head)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "подходящего предыдущего релиза нет")

    def test_first_release_has_no_baseline(self):
        """Первый выпуск проекта: сверяться не с чем, и это не повод врать об успехе."""
        head = self._commit("первый выпуск")
        self._git("tag", "v1.0.0")
        report = release_gate.previous_release(self.root, head)
        self.assertFalse(report["ok"])
        self.assertIsNone(report["tag"])

    def test_report_names_both_commits(self):
        """Оба SHA нужны разбору: с чем сверяли и что выпускали."""
        first = self._commit("первый")
        self._git("tag", "v6.3.0")
        head = self._commit("работа")
        report = release_gate.previous_release(self.root, head)
        self.assertEqual(report["current_sha"], head)
        self.assertEqual(report["sha"], first)

    def test_cli_returns_failure_code_without_baseline(self):
        self._commit("первый выпуск")
        self.assertEqual(release_gate.main(["baseline", "--root", self.root]), 1)

    def test_cli_succeeds_with_baseline(self):
        self._commit("первый")
        self._git("tag", "v6.3.0")
        self._commit("работа")
        self.assertEqual(release_gate.main(["baseline", "--root", self.root]), 0)


if __name__ == "__main__":
    unittest.main()
