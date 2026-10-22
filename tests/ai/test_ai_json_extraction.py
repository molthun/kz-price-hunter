"""Устойчивый разбор JSON из ответа LLM (ai_service._extract_json_from_text).

Модель может добавить пояснение до/после объекта или markdown-обёртку: ответ всё равно
должен разбираться, а не превращаться в «нет ответа» и повторный вызов провайдера.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import unittest

from ai_service import _extract_json_from_text


class JsonExtractionTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(_extract_json_from_text('{"clean_query": "iphone 16"}'),
                         {"clean_query": "iphone 16"})

    def test_prose_around_json(self):
        text = ('Вот результат разбора:\n'
                '{"clean_query": "ноутбук asus", "max_price": 300000}\n'
                'Если нужно, уточните город.')
        self.assertEqual(_extract_json_from_text(text),
                         {"clean_query": "ноутбук asus", "max_price": 300000})

    def test_markdown_fence(self):
        text = '```json\n{"clean_query": "видеокарта", "brand": "NVIDIA"}\n```'
        self.assertEqual(_extract_json_from_text(text),
                         {"clean_query": "видеокарта", "brand": "NVIDIA"})

    def test_nested_multiline_json(self):
        text = ('Ответ модели:\n{\n  "query": "rtx 4070",\n'
                '  "filters": {"city": "Алматы", "max_price": 400000}\n}\n'
                'Проверьте на сайте.')
        parsed = _extract_json_from_text(text)
        self.assertEqual(parsed["filters"]["city"], "Алматы")
        self.assertEqual(parsed["filters"]["max_price"], 400000)

    def test_no_json_returns_none(self):
        self.assertIsNone(_extract_json_from_text("Модель не смогла разобрать запрос"))
        self.assertIsNone(_extract_json_from_text(""))
        self.assertIsNone(_extract_json_from_text(None))

    def test_broken_json_returns_none_not_exception(self):
        self.assertIsNone(_extract_json_from_text('{"clean_query": "iphone", }'))


if __name__ == "__main__":
    unittest.main()
