"""LS-05 (приёмка Codex 26.11, `f76c5d5`): наблюдение «Galaxy S24 Plus» срабатывает только на вариант Plus.

Выдача с 6.59.4 ограничена «S24+» и «S24 Plus» условием `_plus_variant_filter`, а `matches_watch`
его не применял: FTS выбрасывает токен Plus, и штатный `evaluate_watches` ставил в очередь уведомление
о простом S24. Здесь — цель наблюдения из настоящей выдачи (в том числе пустой) и семантический
поиск с подтверждёнными моделью товарами, через очередь `notification_outbox`.
"""
from tests import test_support  # noqa: F401

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import local_search
from repositories.schema_v2 import init_schema_v2
from tests.search.test_card_attributes_consistency import seed

PHONES = {
    "s24": "Смартфон Samsung Galaxy S24 8/128GB Cobalt",
    "s24p": "Смартфон Samsung Galaxy S24+ 12/512GB Onyx Black",
    "s24pw": "Смартфон Samsung Galaxy S24 Plus 8/256GB Violet",
    "s24u": "Смартфон Samsung Galaxy S24 Ultra 12/256GB Gray",
}
PLUS = {"s24p", "s24pw"}


class PlusVariantWatchTest(unittest.TestCase):
    USER = 405

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ls05.db"
        self._p = patch.object(database, "DB_PATH", self.db_path)
        self._p.start()
        database.init_db()
        with database.get_connection() as conn:
            init_schema_v2(conn)
            for pid, title in PHONES.items():
                seed(conn, pid=pid, title=title, category="Смартфоны", brand="Samsung", price=400000)
            conn.commit()
        local_search._plans.clear()
        local_search._failures.clear()
        self.now = datetime.datetime(2026, 11, 26, 12, 0, tzinfo=datetime.timezone.utc)
        self.n = 0

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def _search(self, query, city, understanding=None):
        args = {"query": query, "city": city, "category": None, "limit": 20}
        return local_search.search_sync(args, dict(understanding or {"status": "unavailable"}), {})

    def _offer(self, pid, city):
        self.n += 1
        return {"id": f"o{pid}_{self.n}", "offer_id": f"o{pid}_{self.n}", "canonical_product_id": pid,
                "product_id": pid, "title": PHONES[pid], "shop": "Shop", "city": city,
                "current_price": 400000 - self.n, "is_available": True, "condition": "new",
                "item_condition": "new", "seller_slug": "shop", "seller_id": "s1", "seller_name": "Shop"}

    def _outbox(self):
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0]

    def _watch(self, target, city):
        database.create_watch(self.USER, {
            "kind": "search", "target": target, "title": "Galaxy S24 Plus", "condition": "new_offer",
            "repeat": False, "item_conditions": [], "city": city,
            "cooldown_hours": 0, "quiet_from": "00:00", "quiet_to": "00:00"})

    def _reset_watches(self):
        with database.get_connection() as conn:
            for t in ("notification_outbox", "watch_events", "watch_state", "watches"):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()

    def _queued_for(self, target, city):
        """Свежее наблюдение на каждый товар: одно наблюдение без повтора срабатывает один раз."""
        queued = set()
        for pid in PHONES:
            self._reset_watches()
            self._watch(target, city)
            database.evaluate_watches([self._offer(pid, city)], now=self.now)
            if self._outbox():
                queued.add(pid)
        return queued

    def test_watch_from_results_and_from_empty_results_queues_only_plus(self):
        for query in ("Galaxy S24 Plus", "Galaxy S24+", "Galaxy S24 +"):
            for city in ("Алматы", "Астана"):  # в Астане выдача пустая
                with self.subTest(query=query, city=city):
                    data = self._search(query, city)
                    found = {i["product"]["id"] for i in data["items"]}
                    self.assertEqual(found, PLUS if city == "Алматы" else set())
                    target = data["search_help"]["watch_target"]
                    for pid in PHONES:
                        self.assertEqual(local_search.matches_watch(target, self._offer(pid, city)), pid in PLUS, pid)
                    self.assertEqual(self._queued_for(target, city), PLUS)

    def test_plain_s24_watch_still_matches_every_s24(self):
        # Без слова Plus поведение прежнее: вариант не навязывается
        target = self._search("Galaxy S24", "Алматы")["search_help"]["watch_target"]
        self.assertEqual({pid for pid in PHONES if local_search.matches_watch(target, self._offer(pid, "Алматы"))},
                         set(PHONES))

    def test_semantic_scope_with_verified_products_keeps_variant(self):
        from repositories.catalog_expansion_repo import CatalogExpansionRepository
        from repositories.category_repo import CategoryRepository
        with database.get_connection() as conn:
            CategoryRepository(conn)
            conn.execute("INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, "
                         "attributes_schema_json, search_priority, is_active, created_at, updated_at) "
                         "VALUES ('phones', 'Смартфоны', NULL, '', '[]', '{}', 0, 1, '2026-11-26', '2026-11-26')")
            conn.execute("UPDATE canonical_products SET category='phones'")
            plan = {"search_query": "Galaxy S24 Plus", "category_id": "phones", "category_name": "Смартфоны",
                    "parent_id": None, "confidence": 0.95, "fields": [], "attributes": {}, "clarification": ""}
            repo = CatalogExpansionRepository(conn)
            for query in ("Galaxy S24 Plus", "Galaxy S24+"):
                req = repo.propose(query, None, plan)
                repo.add_match(req["id"], "s24p")
                repo.add_match(req["id"], "s24")  # старое ошибочное подтверждение
            conn.commit()
        data = self._search("Galaxy S24 Plus", "Алматы", {"status": "ok", "plan": plan})
        self.assertEqual({i["product"]["id"] for i in data["items"]}, PLUS)
        target = data["search_help"]["watch_target"]
        self.assertEqual(self._queued_for(target, "Алматы"), PLUS)

        data = self._search("Galaxy S24+", "Алматы", {
            "status": "ok", "plan": {**plan, "search_query": "Galaxy S24"},
        })
        self.assertEqual({i["product"]["id"] for i in data["items"]}, PLUS)
        self.assertEqual(self._queued_for(data["search_help"]["watch_target"], "Алматы"), PLUS)


if __name__ == "__main__":
    unittest.main()
