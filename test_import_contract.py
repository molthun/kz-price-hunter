"""Модули должны импортироваться и на Python старых версий, а не только на текущем.

Зачем отдельная проверка: на 3.14 аннотации вычисляются лениво (PEP 649), а на 3.13 и старше
аннотация модульного уровня вычисляется прямо при импорте. Пока локальная машина была на 3.14,
а CI и образ на 3.12, забытый импорт из `typing` выглядел локально безупречно и ронял приложение
на старте: именно так `_model_cooldown: Dict[Tuple[str, str], float]` без импорта `Tuple`
завалил все четыре проверки сборки 5.17.4 при зелёных тестах на машине разработчика.

С 5.18.0 версии выровнены (3.14 везде), но проверка остаётся: она стоит доли секунды и ловит
класс ошибок, который вернётся при любом откате образа или запуске на другой версии Python.

Проверка статическая: имена в аннотациях, которые старый Python вычисляет при импорте, должны быть
объявлены в модуле. Аннотации локальных переменных внутри функций не вычисляются никогда — их
пропускаем.
"""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import ast
import builtins
import pathlib
import unittest

SKIP_DIRS = {"venv", ".git", "node_modules", "__pycache__", ".venv", "build", "dist"}


def _eager_annotations(tree):
    """Аннотации, которые Python <= 3.13 вычисляет прямо при импорте модуля."""
    found = []

    def walk(node, inside_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = child.args
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                            args.vararg, args.kwarg]:
                    if arg is not None and arg.annotation is not None:
                        found.append(arg.annotation)
                if child.returns is not None:
                    found.append(child.returns)
                walk(child, True)
            elif isinstance(child, ast.ClassDef):
                walk(child, False)
            else:
                if isinstance(child, ast.AnnAssign) and not inside_function:
                    found.append(child.annotation)
                walk(child, inside_function)

    walk(tree, False)
    return found


def _declared_names(tree):
    names = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in ast.walk(node):
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _has_future_annotations(tree):
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body)


class AnnotationImportTest(unittest.TestCase):
    def test_annotations_evaluated_at_import_have_their_names_imported(self):
        root = pathlib.Path(__file__).resolve().parent
        missing = []
        checked = 0
        for path in sorted(root.rglob("*.py")):
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            if _has_future_annotations(tree):
                continue
            checked += 1
            declared = _declared_names(tree)
            for annotation in _eager_annotations(tree):
                for node in ast.walk(annotation):
                    if isinstance(node, ast.Name) and node.id not in declared:
                        missing.append(f"{path.relative_to(root)}:{node.lineno}: {node.id}")
        self.assertGreater(checked, 10, "проверка должна видеть модули проекта")
        self.assertEqual(sorted(set(missing)), [],
                         "имя из аннотации не объявлено в модуле — на Python 3.13 и старше это NameError "
                         "при импорте, хотя на 3.14 тесты зелёные")


if __name__ == "__main__":
    unittest.main()
