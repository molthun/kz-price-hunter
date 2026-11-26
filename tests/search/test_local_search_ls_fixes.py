"""Регрессии повторного аудита Codex 26.11.2026: LS-01, LS-03, LS-04 (6.59.2)."""
from tests import test_support  # noqa: F401
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import local_search
from domain.models import CanonicalProduct, Offer
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
import tests.search.test_local_search_layout_and_plan_guard as base

SHOE_FIELDS = [{"key": "season", "label": "Сезон"}, {"key": "material", "label": "Материал"},
               {"key": "gender", "label": "Пол"}]


class _Fixture(unittest.TestCase):
    """Каталог базового теста (без его тестов) плюс ботинки Dr.Martens в Астане."""
    tearDown = base.LocalSearchLayoutAndPlanGuardIntegrationTest.tearDown
    _get_conn = base.LocalSearchLayoutAndPlanGuardIntegrationTest._get_conn

    def setUp(self):
        base.LocalSearchLayoutAndPlanGuardIntegrationTest.setUp(self)
        ProductRepository(self.conn).save_or_update(CanonicalProduct(
            id="p_drm", canonical_key="k_drm", title="Ботинки Dr.Martens 1460", category="shoes", brand="Dr.Martens"))
        OfferRepository(self.conn).save_or_update(Offer(
            id="o_drm", product_id="p_drm", seller_id="s1", channel_id="c1", external_sku="sku_drm",
            url="http://ex.com/drm", price=90000, condition="new", city="Астана"))
        self.conn.commit()

    def _search(self, query, understanding=None, **extra):
        args = {"query": query, "city": None, "category": None, "limit": 20, **extra}
        with patch("database.get_connection", side_effect=self._get_conn):
            return local_search.search_sync(args, dict(understanding or {"status": "unavailable"}), {})

    def _demand(self):
        return {r[0]: (r[1], json.loads(r[2])) for r in
                self.conn.execute("SELECT query, status, plan_json FROM catalog_expansions WHERE status!='deleted'")}

    def _watch_spec(self, data):
        target = data["search_help"]["watch_target"]
        return json.loads(target[len(local_search.WATCH_PREFIX):])

    # Тот же путь, что в web/server.py: выбор раскладки -> модель -> выдача.
    def _server_path(self, query, model_answer=None):
        prompts = []

        async def fake_call(prompt, base, model, api_key, timeout=18.0, **kw):
            prompts.append(prompt)
            return model_answer

        args = {"query": query, "city": None, "category": None, "limit": 20}
        categories = [{"id": "shoes", "name": "Обувь", "parent_id": None, "fields": {}},
                      {"id": "appliances", "name": "Бытовая техника", "parent_id": None, "fields": {}}]
        with patch("database.get_connection", side_effect=self._get_conn), \
             patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
             patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen"}), \
             patch("local_ai.pause_reason", return_value=None), \
             patch("ai_service.call_local_api", new=AsyncMock(side_effect=fake_call)):
            corrected = local_search.choose_layout_query(args, {})
            u = dict(asyncio.run(local_search.understand(query, categories, corrected_query=corrected)))
            u["corrected_query"] = corrected
            data = local_search.search_sync(args, u, {})
        return data, prompts


class LayoutLiteralPriorityTest(_Fixture):
    def test_brand_tokens_are_not_layout_typos(self):
        for raw in ("Dr.Martens", "G.Skill", "NZXT", "Dr.Martens 1460"):
            with self.subTest(raw=raw):
                self.assertIsNone(local_search.fix_wrong_keyboard_layout(raw))

    def test_dr_martens_found_literally_without_correction(self):
        data = self._search("Dr.Martens")
        self.assertEqual([i["product"]["id"] for i in data["items"]], ["p_drm"])
        self.assertIsNone(data["local_ai"].get("corrected_query"))

    def test_literal_match_wins_over_heuristic_correction(self):
        # Строчная «dr.martens» проходит эвристику («вкюьфкеуты»), но буквально находит товар.
        self.assertEqual(local_search.fix_wrong_keyboard_layout("dr.martens"), "вкюьфкеуты")
        data = self._search("dr.martens")
        self.assertEqual(data["total_products"], 1)
        self.assertIsNone(data["local_ai"].get("corrected_query"))
        self.assertEqual(self._watch_spec(data)["query"], "dr.martens")
        self.assertIn("dr.martens", self._demand())
        self.assertNotIn("вкюьфкеуты", self._demand())

    def test_literal_check_respects_city_and_filters(self):
        args = {"query": "dr.martens", "city": "Астана", "category": None, "limit": 20}
        with patch("database.get_connection", side_effect=self._get_conn):
            self.assertIsNone(local_search.choose_layout_query(args, {}))
            self.assertEqual(local_search.choose_layout_query({**args, "max_price": 1000}, {}), "вкюьфкеуты")

    def test_server_path_keeps_literal_query_for_model(self):
        data, prompts = self._server_path("dr.martens", {"search_query": "dr.martens", "category_id": "shoes",
                                                         "category_name": "Обувь", "confidence": 0.95,
                                                         "fields": SHOE_FIELDS, "attributes": {}})
        self.assertTrue(prompts)
        self.assertIn('"dr.martens"', prompts[0])
        self.assertNotIn("вкюьфкеуты", prompts[0])
        self.assertEqual(data["total_products"], 1)

    def test_layout_recovery_with_model_available(self):
        data, prompts = self._server_path("rhjccjdrb 42", {"search_query": "кроссовки 42", "category_id": "shoes",
                                                           "category_name": "Обувь", "confidence": 0.95,
                                                           "fields": SHOE_FIELDS, "attributes": {}})
        self.assertIn('"кроссовки 42"', prompts[0])
        self.assertEqual([i["product"]["id"] for i in data["items"]], ["p_shoe"])
        self.assertEqual(data["local_ai"]["corrected_query"], "кроссовки 42")
        self.assertEqual(data["query"], "rhjccjdrb 42")
        self.assertNotIn("rhjccjdrb", self._watch_spec(data)["query"])
        self.assertIn("кроссовки 42", self._demand())
        self.assertNotIn("rhjccjdrb 42", self._demand())

    def test_layout_recovery_with_model_unavailable(self):
        local_search._failures[("http://127.0.0.1:11434/v1", "qwen")] = True
        data, prompts = self._server_path("rhjccjdrb 42")
        self.assertEqual(prompts, [])
        self.assertEqual(data["local_ai"]["status"], "unavailable")
        self.assertEqual([i["product"]["id"] for i in data["items"]], ["p_shoe"])
        self.assertEqual(data["selected_attributes"].get("size"), "42")
        self.assertEqual(data["local_ai"]["corrected_query"], "кроссовки 42")

    def test_layout_recovery_without_any_match_records_corrected_demand(self):
        for avail in (True, False):
            with self.subTest(model_available=avail):
                local_search._failures.clear()
                if not avail:
                    local_search._failures[("http://127.0.0.1:11434/v1", "qwen")] = True
                data, prompts = self._server_path("rjvgm.nth", None)
                self.assertEqual(data["total_products"], 0)
                self.assertEqual(data["local_ai"]["corrected_query"], "компьютер")
                self.assertEqual(self._watch_spec(data)["query"], "компьютер")
                self.assertIn("компьютер", self._demand())
                self.assertNotIn("rjvgm.nth", self._demand())
                if avail:
                    self.assertIn('"компьютер"', prompts[0])


class DemandStubTest(_Fixture):
    def _categories(self):
        return {r[0] for r in self.conn.execute("SELECT id FROM categories")}

    def test_unclear_stub_is_polled_literally_but_never_classified_or_categorised(self):
        before = self._categories()
        data = self._search("квантовый синхрофазотрон", {"status": "unclear"})
        self.assertEqual(data["total_products"], 0)
        req = data["search_help"]["expansion"]
        self.assertEqual(req["status"], "approved")
        self.assertIsNone(self.repo.claim())
        batch = self.repo.claim_batch_for_crawl(limit=5)
        self.assertEqual([t["id"] for t in batch], [req["id"]])
        self.repo.finish_crawl_poll(req["id"], 0, "нет", status="needs_source")
        approved = self.repo.review(req["id"], True)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self._categories(), before)
        self.assertIsNone(self.repo.claim())

    def test_legacy_stubs_are_neither_classified_nor_categorised(self):
        before = self._categories()
        legacy = [
            {"category_id": "appliances", "category_name": "Каталог", "search_query": "x", "confidence": 1.0,
             "fields": [], "attributes": {}},
            {"category_id": "shoes", "category_name": "Обувь", "search_query": "y", "confidence": 1.0,
             "fields": [], "attributes": {}},
            {"category_id": "uncategorized", "category_name": "Каталог", "search_query": "z", "confidence": 0.0,
             "unverified_fallback": True, "fields": [], "attributes": {}},
        ]
        for n, plan in enumerate(legacy):
            req = self.repo.propose(f"старая заглушка {n}", None, plan)
            self.repo.review(req["id"], True)
        self.assertIsNone(self.repo.claim())
        self.assertEqual(self._categories(), before)
        self.assertNotIn("uncategorized", self._categories())

    def test_model_plan_is_still_classified(self):
        plan = {"category_id": "shoes", "category_name": "Обувь", "search_query": "ботинки", "parent_id": None,
                "confidence": 0.95, "fields": SHOE_FIELDS, "attributes": {}, "clarification": ""}
        stub = self.repo.propose("заглушка", None, {"category_id": "uncategorized", "category_name": "Каталог",
                                                   "search_query": "заглушка", "confidence": 0.0,
                                                   "unverified_fallback": True, "fields": [], "attributes": {}},
                                 auto_approve=True)
        for _ in range(3):
            self.repo.propose("заглушка", None, {**self.repo.get(stub["id"])["plan"]}, auto_approve=True)
        real = self.repo.propose("ботинки", None, plan, auto_approve=True)
        self.assertEqual(self.repo.claim()["id"], real["id"])

    def test_stub_does_not_overwrite_model_plan_and_model_plan_replaces_stub(self):
        plan = {"category_id": "shoes", "category_name": "Обувь", "search_query": "ботинки", "parent_id": None,
                "confidence": 0.95, "fields": SHOE_FIELDS, "attributes": {}, "clarification": ""}
        stub = {"category_id": "uncategorized", "category_name": "Каталог", "search_query": "ботинки",
                "confidence": 0.0, "unverified_fallback": True, "fields": [], "attributes": {}}
        req = self.repo.propose("ботинки", None, plan)
        self.repo.propose("ботинки", None, stub)
        self.assertEqual(self.repo.get(req["id"])["plan"], plan)
        other = self.repo.propose("сапоги", None, stub, auto_approve=True)
        self.repo.propose("сапоги", None, {**plan, "search_query": "сапоги"}, auto_approve=True)
        self.assertEqual(self.repo.get(other["id"])["plan"]["category_id"], "shoes")


class ConfidentWrongCategoryTest(_Fixture):
    def _plan(self, query, cat, name):
        return {"status": "ok", "plan": {"search_query": query, "category_id": cat, "category_name": name,
                                         "parent_id": None, "confidence": 0.95, "fields": [], "attributes": {},
                                         "clarification": ""}}

    def test_confident_wrong_category_does_not_show_unrelated_products(self):
        data = self._search("квантовый синхрофазотрон", self._plan("квантовый синхрофазотрон", "appliances", "Бытовая техника"))
        self.assertEqual(data["total_products"], 0)
        self.assertFalse(data["search_help"]["is_fallback"])
        self.assertNotIn("p_app", [i["product"]["id"] for i in data["items"]])

    def test_grounded_general_query_keeps_category_hint(self):
        data = self._search("удобная обувь", self._plan("удобная обувь", "shoes", "Обувь"))
        self.assertTrue(data["search_help"]["is_fallback"])
        self.assertIn("p_shoe", [i["product"]["id"] for i in data["items"]])

    def test_grounding_by_category_titles_respects_city(self):
        # «мартинсы» нет ни в каталоге, ни в разделе; «ботинки» есть в названиях раздела только в Астане.
        q = "мартинсы ботинки"
        astana = self._search(q, self._plan(q, "shoes", "Обувь"), city="Астана")
        self.assertTrue(astana["search_help"]["is_fallback"])
        almaty = self._search(q, self._plan(q, "shoes", "Обувь"), city="Алматы")
        self.assertFalse(almaty["search_help"]["is_fallback"])


class ExactModelVariantRankingTest(_Fixture):
    def setUp(self):
        super().setUp()
        titles = {"ip15pm": ("Apple iPhone 15 Pro Max 256GB Natural Titanium", 650000),
                  "ip15p": ("Apple iPhone 15 Pro 256GB Natural Titanium", 560000),
                  "ip15": ("Apple iPhone 15 256GB Black", 420000)}
        for n in (2, 3):
            self.conn.execute("INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, "
                              "created_at, updated_at) VALUES (?, ?, ?, 4.5, ?, 1, '{}', '2026-11-26', '2026-11-26')",
                              (f"s{n}", f"Shop{n}", f"shop{n}", f"shop{n}.kz"))
            self.conn.execute("INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
                              "VALUES (?, ?, 'website', ?, '2026-11-26')", (f"c{n}", f"s{n}", f"Shop{n}"))
        for pid, (title, price) in titles.items():
            ProductRepository(self.conn).save_or_update(CanonicalProduct(
                id=pid, canonical_key="k_" + pid, title=title, category="phones", brand="Apple"))
            for n in range(3 if pid == "ip15pm" else 1):  # у Pro Max больше продавцов, как на живом сайте
                OfferRepository(self.conn).save_or_update(Offer(
                    id=f"o_{pid}_{n}", product_id=pid, seller_id=f"s{n + 1}", channel_id=f"c{n + 1}", external_sku=f"sku_{pid}_{n}",
                    url=f"http://ex.com/{pid}/{n}", price=price + n, condition="new", city="Астана"))
        self.conn.commit()

    def _order(self, query):
        return [i["product"]["id"] for i in self._search(query)["items"] if i["product"]["id"].startswith("ip15")]

    def test_exact_variant_ranks_first(self):
        self.assertEqual(self._order("iPhone 15 Pro 256")[0], "ip15p")
        self.assertEqual(self._order("iPhone 15 Pro Max 256")[0], "ip15pm")
        self.assertEqual(self._order("iPhone 15 256")[0], "ip15")

    def test_exact_variant_ranks_first_in_model_verified_scope(self):
        # Живой 6.58.4: товары подтверждены моделью, apply_semantics очищает текст, и «Pro Max» шёл первым.
        self.conn.execute("INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, "
                          "attributes_schema_json, search_priority, is_active, created_at, updated_at) "
                          "VALUES ('phones', 'Смартфоны', NULL, '', '[]', '{}', 0, 1, '2026-11-26', '2026-11-26')")
        self.conn.execute("UPDATE canonical_products SET model=? WHERE id='ip15pm'", ("iPhone 15 Pro Max",))
        self.conn.execute("UPDATE canonical_products SET model=? WHERE id='ip15p'", ("iPhone 15 Pro",))
        self.conn.commit()
        plan = {"search_query": "iPhone 15 Pro 256", "category_id": "phones", "category_name": "Смартфоны",
                "parent_id": None, "confidence": 0.95, "fields": [], "attributes": {}, "clarification": ""}
        req = self.repo.propose("iPhone 15 Pro 256", None, plan)
        for pid in ("ip15pm", "ip15p"):
            self.repo.add_match(req["id"], pid)
        data = self._search("iPhone 15 Pro 256", {"status": "ok", "plan": plan})
        ids = [i["product"]["id"] for i in data["items"]]
        self.assertEqual(ids[0], "ip15p", ids)

    def test_plus_suffix_is_a_variant(self):
        # Живой сайт 26.11: «Samsung Galaxy S24» показывал первым S24+.
        for pid, title in (("s24p", "Смартфон Samsung Galaxy S24+ 5G 12/512GB Onyx Black"),
                           ("s24", "Смартфон Samsung Galaxy S24 8/128GB Cobalt")):
            ProductRepository(self.conn).save_or_update(CanonicalProduct(
                id=pid, canonical_key="k_" + pid, title=title, category="phones", brand="Samsung"))
            for n in range(3 if pid == "s24p" else 1):
                OfferRepository(self.conn).save_or_update(Offer(
                    id=f"o_{pid}_{n}", product_id=pid, seller_id=f"s{n + 1}", channel_id=f"c{n + 1}",
                    external_sku=f"sku_{pid}_{n}", url=f"http://ex.com/{pid}/{n}", price=400000 + n,
                    condition="new", city="Астана"))
        self.conn.commit()
        order = lambda q: [i["product"]["id"] for i in self._search(q)["items"] if i["product"]["id"].startswith("s24")]
        self.assertEqual(order("Samsung Galaxy S24")[0], "s24")
        self.assertEqual(order("Samsung Galaxy S24+")[0], "s24p")


class LiveSiteFindingsTest(_Fixture):
    def test_ps5_finds_playstation_5(self):
        ProductRepository(self.conn).save_or_update(CanonicalProduct(
            id="ps5", canonical_key="k_ps5", title="Игровая консоль Sony PlayStation 5 Slim", category="consoles", brand="Sony"))
        OfferRepository(self.conn).save_or_update(Offer(
            id="o_ps5", product_id="ps5", seller_id="s1", channel_id="c1", external_sku="sku_ps5",
            url="http://ex.com/ps5", price=250000, condition="new", city="Астана"))
        self.conn.commit()
        self.assertIn("ps5", [i["product"]["id"] for i in self._search("PS5")["items"]])

    def test_descriptive_monitor_query_is_grounded_by_synonym(self):
        # В названиях мониторов живого каталога слова «экран» нет; подсказка раздела сохраняется через синоним.
        self.conn.execute("INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, "
                          "attributes_schema_json, search_priority, is_active, created_at, updated_at) "
                          "VALUES ('monitors', 'Мониторы', NULL, '', '[]', '{}', 0, 1, '2026-11-26', '2026-11-26')")
        ProductRepository(self.conn).save_or_update(CanonicalProduct(
            id="mon", canonical_key="k_mon", title="Монитор 27 Wintek WCH2742B Black", category="monitors"))
        OfferRepository(self.conn).save_or_update(Offer(
            id="o_mon", product_id="mon", seller_id="s1", channel_id="c1", external_sku="sku_mon",
            url="http://ex.com/mon", price=60000, condition="new", city="Астана"))
        self.conn.commit()
        q = "хороший экран для работы"
        plan = {"status": "ok", "plan": {"search_query": q, "category_id": "monitors", "category_name": "Мониторы",
                                         "parent_id": None, "confidence": 0.95, "fields": [], "attributes": {},
                                         "clarification": ""}}
        data = self._search(q, plan)
        self.assertTrue(data["search_help"]["is_fallback"])
        self.assertEqual([i["product"]["id"] for i in data["items"]], ["mon"])


if __name__ == "__main__":
    unittest.main()
