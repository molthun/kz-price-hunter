"""Правило приватности P06 в новом контуре поиска (S2-E13). Офлайн.

У правила две половины, и раньше новый контур соблюдал только первую.
  1. Чувствительный текст (телефон, почта, адрес) вырезается — это работало.
  2. Текст запроса хранится только начиная с третьего обращения — этого не было.
Вторая половина не менее важна: единичный запрос опознаёт человека и без телефона.
Поэтому «редкий уникальный запрос про мою болезнь» не должен попадать в базу дословно.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import sqlite3
import unittest

import search_analytics as sa
from domain.search_analytics import SearchEvent
from repositories import init_schema_v2
from repositories.search_analytics_repo import SearchAnalyticsRepository


class SearchQueryPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.repo = SearchAnalyticsRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def _stored(self):
        return [r[0] for r in self.conn.execute("SELECT query FROM search_events ORDER BY rowid")]

    def test_sensitive_text_is_redacted_on_first_sight(self):
        self.repo.record_event(SearchEvent(query="телефон +7 701 123 45 67", city="Алматы"))
        self.assertEqual(self._stored(), ["[redacted]"])

    def test_rare_query_text_is_not_stored_until_third_time(self):
        query = "редкий уникальный запрос про мою болезнь"
        for _ in range(2):
            self.repo.record_event(SearchEvent(query=query, city="Алматы"))
        self.assertEqual(self._stored(), ["[rare]", "[rare]"],
                         "единичный запрос опознаёт человека и без телефона")

        self.repo.record_event(SearchEvent(query=query, city="Алматы"))
        self.assertEqual(self._stored()[-1], query,
                         f"с {sa.MIN_OCCURRENCES_TO_STORE_TEXT}-го раза запрос перестаёт быть единичным")

    def test_permutations_count_as_one_query(self):
        """Ключ счётчика инвариантен к порядку слов.

        Иначе «iphone 16» и «16 iphone» копились бы порознь, порога не достигали никогда,
        и правило превратилось бы в запрет на хранение текста вообще.
        """
        for query in ("iphone 16", "16 iphone", "IPHONE 16"):
            self.repo.record_event(SearchEvent(query=query, city="Алматы"))
        # Первые два обращения обезличены, третье сохранено: перестановки — один и тот же запрос
        self.assertEqual(self._stored(), ["[rare]", "[rare]", "IPHONE 16"])
        normalized = [r[0] for r in self.conn.execute(
            "SELECT normalized_query FROM search_events ORDER BY rowid")]
        self.assertEqual(normalized[-1], "16 iphone",
                         "нормализованная форма инвариантна к порядку слов")

    def test_anonymised_rows_keep_metrics_but_not_text(self):
        """Обезличивается текст, а не факт поиска: метрики обязаны остаться."""
        self.repo.record_event(SearchEvent(query="какой-то редкий запрос", city="Алматы",
                                           outcome="NOT_FOUND", total_products=0))
        row = self.conn.execute(
            "SELECT query, city, outcome FROM search_events").fetchone()
        self.assertEqual(row[0], "[rare]")
        self.assertEqual(row[1], "Алматы")
        self.assertEqual(row[2], "NOT_FOUND")

    def test_rare_and_redacted_never_appear_in_analytics(self):
        self.repo.record_event(SearchEvent(query="телефон +7 701 123 45 67", city="Алматы"))
        self.repo.record_event(SearchEvent(query="одинокий запрос", city="Алматы"))
        self.conn.commit()
        texts = {row["normalized_query"] for row in self.repo.get_top_queries(limit=50)}
        self.assertNotIn("[rare]", texts)
        self.assertNotIn("[redacted]", texts)


if __name__ == "__main__":
    unittest.main()
