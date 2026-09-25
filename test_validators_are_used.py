"""Валидатор, который никто не вызывает, защищает только на бумаге.

В батче KZ Price Hunter 2.0 это повторилось трижды: частотное правило `shared_identity_values`
и проверка контрольной суммы БИН были написаны, покрыты тестами — и ниоткуда не вызывались.
Тест при этом проходил, потому что дёргал функцию напрямую. Пока такой валидатор не подключён,
два разных продавца склеивались по битому номеру с наибольшей возможной уверенностью.

Проверка дешёвая и ловит весь класс: каждая публичная функция вида is_*/validate_*/has_*
из `domain/` должна упоминаться хотя бы в одном рабочем (не тестовом) модуле, кроме себя самой.
"""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import ast
import pathlib
import re
import unittest

CHECKED_DIRS = ("domain", "repositories")
VALIDATOR_RE = re.compile(r"^(is_|validate_|has_|looks_like_|check_)")
SKIP_DIRS = {"__pycache__", "venv", "node_modules"}


def _public_validators(root: pathlib.Path):
    """Публичные функции-валидаторы модулей проекта: (модуль, имя)."""
    found = []
    for folder in CHECKED_DIRS:
        for path in sorted((root / folder).glob("*.py")):
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            # Валидаторы бывают и функциями модуля, и методами класса — обходим всё дерево
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_") and VALIDATOR_RE.match(node.name):
                        found.append((path, node.name))
    return found


class ValidatorsAreWiredTest(unittest.TestCase):
    def test_every_public_validator_is_called_from_working_code(self):
        root = pathlib.Path(__file__).resolve().parent
        sources = [p for folder in CHECKED_DIRS for p in (root / folder).rglob("*.py")
                   if not any(part in SKIP_DIRS for part in p.parts)]
        texts = {p: p.read_text(encoding="utf-8") for p in sources}

        orphans = []
        validators = _public_validators(root)
        names = {name for _, name in validators}
        self.assertIn("validate_kz_bin_checksum", names,
                      "проверка должна видеть валидаторы проекта — иначе она ничего не гарантирует")
        self.assertGreaterEqual(len(validators), 4)

        for path, name in validators:
            used_elsewhere = any(
                re.search(rf"\b{re.escape(name)}\s*\(", body)
                for other, body in texts.items() if other != path
            )
            # Вызов внутри собственного модуля тоже считается: функция может быть
            # вспомогательной для соседнего правила в том же файле.
            own = texts[path]
            calls_in_own = len(re.findall(rf"\b{re.escape(name)}\s*\(", own))
            if not used_elsewhere and calls_in_own <= 1:
                orphans.append(f"{path.relative_to(root)}:{name}")

        self.assertEqual(sorted(orphans), [],
                         "валидатор не вызывается ни из одного рабочего модуля — значит, "
                         "он защищает только в собственном тесте")


if __name__ == "__main__":
    unittest.main()
