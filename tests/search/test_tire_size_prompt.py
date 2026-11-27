from tests import test_support  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path

import database
import local_search
from domain.models import CanonicalProduct, Offer
from repositories.catalog_expansion_repo import CatalogExpansionRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository


def _seed_tires_db() -> None:
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
            "VALUES ('seller_tyres', 'TurboTyres', 'turbotyres', 4.8, 'turbotyres.kz', 1, '{}', '2026-11-27', '2026-11-27')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
            "VALUES ('ch_tyres', 'seller_tyres', 'website', 'TurboTyres', '2026-11-27')"
        )
        pr = ProductRepository(conn)
        orf = OfferRepository(conn)
        exp = CatalogExpansionRepository(conn)
        products = [
            ("p_t1", "Шина зимняя Sailun Ice Blazer 205/55 R16", "Sailun", "205/55 R16", "Зимние", "Алматы", 28000.0),
            ("p_t2", "Шина зимняя Nokian Hakkapeliitta 205/55 R16", "Nokian", "205/55 R16", "Зимние", "Алматы", 42000.0),
            ("p_t3", "Шина зимняя Bridgestone Blizzak 215/65 R16", "Bridgestone", "215/65 R16", "Зимние", "Астана", 39000.0),
            ("p_t4", "Шина летняя Triangle 205/55 R16", "Triangle", "205/55 R16", "Летние", "Алматы", 22000.0),
            ("p_t5", "Шина зимняя Michelin X-Ice 235/45 R19", "Michelin", "235/45 R19", "Зимние", "Шымкент", 85000.0),
        ]
        for pid, title, brand, tire_size, season, city, price in products:
            pr.save_or_update(CanonicalProduct(
                id=pid, canonical_key=f"k_{pid}", title=title, category="tires", brand=brand,
            ))
            orf.save_or_update(Offer(
                id=f"off_{pid}", product_id=pid, seller_id="seller_tyres", channel_id="ch_tyres",
                external_sku=f"sku_{pid}", url=f"https://turbotyres.kz/{pid}",
                price=price, condition="new", city=city,
            ))
            exp.save_attributes(pid, title, {
                "tire_size": {"value": tire_size, "evidence": tire_size},
                "season": {"value": season, "evidence": "зимняя" if season == "Зимние" else "летняя"},
            })
        conn.commit()
    finally:
        conn.close()


class TireSizePromptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "test_tires.db"
        self.addCleanup(self._restore_db)
        _seed_tires_db()

    def _restore_db(self):
        database.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def _winter_tire_understanding(self, clarification="Какой размер шин вам нужен?"):
        return {
            "status": "ok",
            "plan": {
                "category_id": "tires",
                "category_name": "Шины и диски",
                "search_query": "зимн",
                "confidence": 0.95,
                "attributes": {"season": "Зимние"},
                "fields": [
                    {"key": "season", "label": "Сезонность"},
                    {"key": "tire_size", "label": "Типоразмер"},
                ],
                "clarification": clarification,
            },
        }

    def test_normalize_tire_size_variants(self):
        for raw in ("205/55 R16", "205/55r16", "205/55 р16", "205 55 16", "205/55-16", "205/55/16", "205/55 ZR16"):
            self.assertEqual(local_search.normalize_tire_size(raw), "205/55 R16", msg=raw)
        self.assertIsNone(local_search.normalize_tire_size("абракадабра"))
        self.assertIsNone(local_search.normalize_tire_size("99/55 R16"))

    def test_winter_tires_without_size_returns_products_and_facet_prompt_per_city(self):
        args_almaty = {"query": "зимняя резина", "city": "Алматы", "limit": 20, "page": 1}
        data_almaty = local_search.search_sync(args_almaty, self._winter_tire_understanding(), {})
        self.assertEqual(data_almaty["total_products"], 2)
        prompt = data_almaty["search_help"]["tire_size_prompt"]
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["action_label"], "Указать размер")
        self.assertEqual(prompt["attribute_key"], "tire_size")
        self.assertEqual(prompt["options"], [{"value": "205/55 R16", "count": 2}])

        # Смена города до выбора обновляет фасеты подсказки под Астану
        args_astana = {"query": "зимняя резина", "city": "Астана", "limit": 20, "page": 1}
        data_astana = local_search.search_sync(args_astana, self._winter_tire_understanding(), {})
        self.assertEqual(data_astana["total_products"], 1)
        prompt_astana = data_astana["search_help"]["tire_size_prompt"]
        self.assertEqual(prompt_astana["options"], [{"value": "215/65 R16", "count": 1}])

    def test_selected_tire_size_filters_preserves_constraints_and_hides_prompt(self):
        args = {"query": "зимняя резина", "city": "Алматы", "max_price": 35000, "limit": 20, "page": 1}
        data = local_search.search_sync(
            args,
            self._winter_tire_understanding(),
            {"tire_size": "205/55r16"},
        )
        self.assertEqual(data["total_products"], 1)
        self.assertEqual(data["items"][0]["product"]["id"], "p_t1")
        self.assertEqual(data["selected_attributes"]["tire_size"], "205/55 R16")
        self.assertEqual(data["selected_attributes"]["season"], "Зимние")
        self.assertIsNone(data["search_help"]["tire_size_prompt"])
        self.assertEqual(data["search_help"]["clarification"], "")

    def test_query_with_explicit_size_does_not_ask_again(self):
        args = {"query": "зимняя резина 205/55 R16", "city": "Алматы", "limit": 20, "page": 1}
        understanding = self._winter_tire_understanding()
        understanding["plan"]["search_query"] = "зимняя 205/55 R16"
        data = local_search.search_sync(args, understanding, {})
        self.assertIsNone(data["search_help"]["tire_size_prompt"])
        self.assertEqual(data["search_help"]["clarification"], "")

    def test_empty_after_selecting_size_preserves_size_in_watch_target_and_matches_watch(self):
        args = {"query": "зимняя резина", "city": "Алматы", "max_price": 90000, "limit": 20, "page": 1}
        data = local_search.search_sync(
            args,
            self._winter_tire_understanding(),
            {"tire_size": "235/45 R19"},
        )
        self.assertEqual(data["total_products"], 0)
        self.assertIsNone(data["search_help"]["tire_size_prompt"])
        watch_target = data["search_help"]["watch_target"]
        self.assertTrue(watch_target.startswith(local_search.WATCH_PREFIX))
        spec = json.loads(watch_target[len(local_search.WATCH_PREFIX):])
        self.assertEqual(spec["attributes"].get("tire_size"), "235/45 R19")
        self.assertEqual(spec["inferred_attributes"].get("season"), "Зимние")
        self.assertEqual(spec["max_price"], 90000)

        self.assertTrue(local_search.matches_watch(watch_target, {
            "product_id": "p_t5", "current_price": 85000, "seller_id": "seller_tyres",
        }))
        self.assertFalse(local_search.matches_watch(watch_target, {
            "product_id": "p_t1", "current_price": 28000, "seller_id": "seller_tyres",
        }))

    def test_ai_unavailable_still_returns_catalog_results_facets_and_size_filter(self):
        unavailable = {"status": "unavailable", "plan": None}
        for q in ("зимняя шина", "зимняя резина", "зимние шины"):
            args = {"query": q, "city": "Алматы", "limit": 20, "page": 1}
            data = local_search.search_sync(args, unavailable, {})
            prompt = data["search_help"]["tire_size_prompt"]
            self.assertIsNotNone(prompt, msg=q)
            self.assertEqual(prompt["action_label"], "Указать размер", msg=q)

        args = {"query": "зимняя шина", "city": "Алматы", "limit": 20, "page": 1}
        data = local_search.search_sync(args, unavailable, {})
        self.assertEqual(data["total_products"], 2)
        self.assertEqual(data["search_help"]["tire_size_prompt"]["options"], [{"value": "205/55 R16", "count": 2}])

        filtered = local_search.search_sync(args, unavailable, {"tire_size": "205/55 R16"})
        self.assertEqual(filtered["total_products"], 2)
        self.assertIsNone(filtered["search_help"]["tire_size_prompt"])

    def test_non_tire_query_has_no_tire_size_prompt(self):
        unavailable = {"status": "unavailable", "plan": None}
        for q in (
            "цепная бензопила шина",
            "швейная машинка",
            "копировальная машина",
            "машинка для стрижки",
        ):
            data = local_search.search_sync(
                {"query": q, "city": "Алматы", "limit": 20, "page": 1},
                unavailable,
                {},
            )
            self.assertIsNone(data["search_help"]["tire_size_prompt"], msg=q)


if __name__ == "__main__":
    unittest.main()
