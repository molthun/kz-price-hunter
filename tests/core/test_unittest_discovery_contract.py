"""Штатный прогон выпуска — `unittest discover`, без pytest (A09, аудит 05.11.2026).

Две ловушки, которые зелёный локальный pytest не показывает:
- `import pytest` в модуле: в CI pytest не устанавливается, модуль падает при импорте;
- функции `test_*` на уровне модуля: unittest их не видит и молча пропускает весь файл.
Проверка статическая и стоит доли секунды.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import ast
import pathlib
import unittest

SKIP_DIRS = {"venv", ".git", "node_modules", "__pycache__", ".venv", "build", "dist"}
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _test_modules():
    for path in sorted(ROOT.rglob("test_*.py")):
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            yield path


class UnittestDiscoveryContractTests(unittest.TestCase):
    def test_no_pytest_imports(self):
        offenders = []
        for path in _test_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(n == "pytest" or n.startswith("pytest.") for n in names):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [], "pytest нет в зависимостях CI: используйте unittest")

    def test_no_module_level_test_functions(self):
        offenders = []
        for path in _test_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
        self.assertEqual(offenders, [], "unittest discover пропускает функции вне TestCase")


if __name__ == "__main__":
    unittest.main()
