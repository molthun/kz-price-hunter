from tests import test_support  # noqa: F401
import tempfile
import unittest
from pathlib import Path

import database
import local_search
from domain.models import CanonicalProduct, Offer
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository


def _seed_shoes_db() -> None:
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
            "VALUES ('seller_shoes', 'Kari', 'kari', 4.7, 'kari.com', 1, '{}', '2026-11-28', '2026-11-28')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
            "VALUES ('ch_shoes', 'seller_shoes', 'website', 'Kari', '2026-11-28')"
        )
        pr = ProductRepository(conn)
        orf = OfferRepository(conn)
        shoes = [
            ("p_s1", "Кроссовки Nike Air Max", "Nike", "Алматы", 35000.0, ["41", "42"]),
            ("p_s2", "Кроссовки Nike Pegasus", "Nike", "Алматы", 45000.0, ["42", "43"]),
            ("p_s3", "Кроссовки Nike Revolution", "Nike", "Астана", 29000.0, ["39", "40"]),
        ]
        for pid, title, brand, city, price, sizes in shoes:
            pr.save_or_update(CanonicalProduct(
                id=pid, canonical_key=f"k_{pid}", title=title, category="shoes", brand=brand,
            ))
            oid = f"off_{pid}"
            orf.save_or_update(Offer(
                id=oid, product_id=pid, seller_id="seller_shoes", channel_id="ch_shoes",
                external_sku=f"sku_{pid}", url=f"https://kari.com/{pid}",
                price=price, condition="NEW", city=city,
            ))
            for sz in sizes:
                conn.execute(
                    "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES (?, ?, ?, '2026-11-28')",
                    (oid, pid, sz),
                )

        # Неразмерный товар (смартфон) — не должен вызывать size_prompt
        pr.save_or_update(CanonicalProduct(
            id="p_phone", canonical_key="k_p_phone", title="Смартфон Samsung Galaxy S24", category="phones", brand="Samsung",
        ))
        orf.save_or_update(Offer(
            id="off_p_phone", product_id="p_phone", seller_id="seller_shoes", channel_id="ch_shoes",
            external_sku="sku_p_phone", url="https://kari.com/phone",
            price=350000.0, condition="NEW", city="Алматы",
        ))

        # Одежда (куртки с размерами 42/44 — SZ-01: обувной 42 не должен путаться с одеждой)
        jackets = [
            ("p_j1", "Куртка зимняя Nike", "Nike", "Алматы", 52000.0, ["42", "44"]),
            ("p_j2", "Куртка демисезонная Nike", "Nike", "Алматы", 48000.0, ["42"]),
        ]
        for pid, title, brand, city, price, sizes in jackets:
            pr.save_or_update(CanonicalProduct(
                id=pid, canonical_key=f"k_{pid}", title=title, category="women_clothing", brand=brand,
            ))
            oid = f"off_{pid}"
            orf.save_or_update(Offer(
                id=oid, product_id=pid, seller_id="seller_shoes", channel_id="ch_shoes",
                external_sku=f"sku_{pid}", url=f"https://kari.com/{pid}",
                price=price, condition="NEW", city=city,
            ))
            for sz in sizes:
                conn.execute(
                    "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES (?, ?, ?, '2026-11-28')",
                    (oid, pid, sz),
                )

        # Товар с неподтверждённым типом размерной сетки
        pr.save_or_update(CanonicalProduct(
            id="p_misc", canonical_key="k_p_misc", title="Набор Универсал", category="sale", brand="BrandX",
        ))
        orf.save_or_update(Offer(
            id="off_p_misc", product_id="p_misc", seller_id="seller_shoes", channel_id="ch_shoes",
            external_sku="sku_p_misc", url="https://kari.com/misc",
            price=15000.0, condition="NEW", city="Алматы",
        ))
        conn.execute(
            "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES ('off_p_misc', 'p_misc', '42', '2026-11-28')"
        )
        conn.commit()
    finally:
        conn.close()


class SizePromptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "test_size_prompt.db"
        self.addCleanup(self._restore_db)
        _seed_shoes_db()

    def _restore_db(self):
        database.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def _shoes_understanding(self, clarification="Какой размер вам нужен?"):
        return {
            "status": "ok",
            "plan": {
                "category_id": "shoes",
                "category_name": "Обувь",
                "search_query": "кроссовки nike",
                "confidence": 0.95,
                "attributes": {},
                "fields": [{"key": "size", "label": "Размер"}],
                "clarification": clarification,
            },
        }

    def test_shoes_without_size_returns_products_and_city_scoped_size_prompt(self):
        data_almaty = local_search.search_sync(
            {"query": "кроссовки nike", "city": "Алматы", "limit": 20, "page": 1},
            self._shoes_understanding(),
            {},
        )
        self.assertEqual(data_almaty["total_products"], 2)
        prompt = data_almaty["search_help"]["size_prompt"]
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["action_label"], "Указать размер")
        self.assertEqual(prompt["attribute_key"], "size")
        self.assertEqual(prompt["size_kind"], "shoe")
        self.assertEqual(
            prompt["options"],
            [{"value": "41", "count": 1}, {"value": "42", "count": 2}, {"value": "43", "count": 1}],
        )

        # Смена города на Астану обновляет размеры под Астану
        data_astana = local_search.search_sync(
            {"query": "кроссовки nike", "city": "Астана", "limit": 20, "page": 1},
            self._shoes_understanding(),
            {},
        )
        self.assertEqual(data_astana["total_products"], 1)
        self.assertEqual(
            data_astana["search_help"]["size_prompt"]["options"],
            [{"value": "39", "count": 1}, {"value": "40", "count": 1}],
        )

    def test_selected_size_filters_preserves_constraints_and_hides_prompt(self):
        data = local_search.search_sync(
            {"query": "кроссовки nike", "city": "Алматы", "max_price": 40000, "limit": 20, "page": 1},
            self._shoes_understanding(),
            {"size": "42"},
        )
        self.assertEqual(data["total_products"], 1)
        self.assertEqual(data["items"][0]["product"]["id"], "p_s1")
        self.assertIsNone(data["search_help"]["size_prompt"])

    def test_query_with_explicit_size_suppresses_size_prompt(self):
        understanding = self._shoes_understanding()
        understanding["plan"]["search_query"] = "кроссовки nike 42"
        understanding["plan"]["attributes"] = {"size": "42"}
        data = local_search.search_sync(
            {"query": "кроссовки nike 42", "city": "Алматы", "limit": 20, "page": 1},
            understanding,
            {},
        )
        self.assertEqual(data["total_products"], 2)
        self.assertIsNone(data["search_help"]["size_prompt"])

    def test_ai_unavailable_still_returns_catalog_results_and_size_prompt(self):
        unavailable = {"status": "unavailable", "plan": None}
        data = local_search.search_sync(
            {"query": "кроссовки nike", "city": "Алматы", "limit": 20, "page": 1},
            unavailable,
            {},
        )
        self.assertEqual(data["total_products"], 2)
        prompt = data["search_help"]["size_prompt"]
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["size_kind"], "shoe")
        self.assertEqual([o["value"] for o in prompt["options"]], ["41", "42", "43"])

    def test_apparel_jackets_emit_apparel_size_kind(self):
        data = local_search.search_sync(
            {"query": "куртка", "city": "Алматы", "limit": 20, "page": 1},
            {"status": "unavailable", "plan": None},
            {},
        )
        self.assertEqual(data["total_products"], 2)
        prompt = data["search_help"]["size_prompt"]
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["size_kind"], "apparel")
        self.assertEqual(prompt["options"], [{"value": "42", "count": 2}, {"value": "44", "count": 1}])

    def test_mixed_shoe_and_apparel_results_emit_none_size_kind(self):
        data = local_search.search_sync(
            {"query": "Nike", "city": "Алматы", "limit": 20, "page": 1},
            {"status": "unavailable", "plan": None},
            {},
        )
        self.assertEqual(data["total_products"], 4)
        prompt = data["search_help"]["size_prompt"]
        self.assertIsNotNone(prompt)
        self.assertIsNone(prompt["size_kind"])

    def test_unconfirmed_category_and_title_emit_none_size_kind(self):
        data = local_search.search_sync(
            {"query": "Набор Универсал", "city": "Алматы", "limit": 20, "page": 1},
            {"status": "unavailable", "plan": None},
            {},
        )
        self.assertEqual(data["total_products"], 1)
        prompt = data["search_help"]["size_prompt"]
        self.assertIsNotNone(prompt)
        self.assertIsNone(prompt["size_kind"])

    def test_non_sized_products_never_return_size_prompt(self):
        data = local_search.search_sync(
            {"query": "Samsung Galaxy S24", "city": "Алматы", "limit": 20, "page": 1},
            {"status": "unavailable", "plan": None},
            {},
        )
        self.assertEqual(data["total_products"], 1)
        self.assertIsNone(data["search_help"]["size_prompt"])

    def test_preview_phase_does_not_emit_size_prompt(self):
        data = local_search.search_sync(
            {"query": "кроссовки nike", "city": "Алматы", "limit": 20, "page": 1},
            {"status": "pending", "plan": None},
            {},
            preview=True,
        )
        self.assertIsNone(data["search_help"]["size_prompt"])


if __name__ == "__main__":
    unittest.main()
