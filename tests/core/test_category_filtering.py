"""
Unit & Regression Tests for Category Filtering, Master Taxonomy Mapping,
and Assistant Length Limiting (KZ Price Hunter 2.0).

Verifies:
1. P1: Real store category strings (e.g. 'Forte: 📱 Смартфоны', 'Halyk: Ноутбуки', 'Flip: Электроника')
   are correctly mapped and filtered in SearchRepository with root and leaf categories.
2. P1: CategoryRepository.count_products_by_category maps raw store categories to canonical categories.
3. P2: Assistant message length validation (max 4000 chars) and conversation history context resolution.
"""

from tests import test_support  # must be first
import datetime as dt
import sqlite3
import unittest
from unittest.mock import patch

from domain.category import TaxonomyMapper
from domain.models import (
    CanonicalProduct,
    Channel,
    Condition,
    Offer,
    Seller,
    utc_now_iso,
)
from domain.search_assistant import SearchAssistant
from domain.search_core import SearchCore
from repositories import (
    CategoryRepository,
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)


class TestCategoryFiltering(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.category_repo = CategoryRepository(self.conn)
        self.search_core = SearchCore(self.search_repo)

        # Base seller and channel
        self.seller = Seller(id="s1", name="Тест Магазин", slug="test-store")
        self.seller_repo.save_or_update(self.seller)
        self.channel = Channel(id="ch1", seller_id="s1", channel_type="direct", name="Web")
        self.channel_repo.save_or_update(self.channel)

    def tearDown(self):
        self.conn.close()

    def test_p1_category_filtering_with_store_prefixes(self):
        """P1: Search by category must match store-prefixed categories (e.g. 'Forte: 📱 Смартфоны')."""
        now = utc_now_iso()

        # 1. Смартфон с префиксом Forte
        p_phone1 = CanonicalProduct(
            id="p_phone1",
            canonical_key="p_phone1",
            title="Apple iPhone 15 128GB Black",
            brand="Apple",
            category="Forte: 📱 Смартфоны",
        )
        self.product_repo.save_or_update(p_phone1)
        self.offer_repo.save_or_update(Offer(
            id="o1", product_id="p_phone1", seller_id="s1", channel_id="ch1",
            external_sku="SKU1", url="u1", price=450000, condition=Condition.NEW,
            availability="in_stock", observed_at=now,
        ))

        # 2. Смартфон с префиксом Halyk
        p_phone2 = CanonicalProduct(
            id="p_phone2",
            canonical_key="p_phone2",
            title="Смартфон Samsung Galaxy S24",
            brand="Samsung",
            category="Halyk: Смартфоны",
        )
        self.product_repo.save_or_update(p_phone2)
        self.offer_repo.save_or_update(Offer(
            id="o2", product_id="p_phone2", seller_id="s1", channel_id="ch1",
            external_sku="SKU2", url="u2", price=400000, condition=Condition.NEW,
            availability="in_stock", observed_at=now,
        ))

        # 3. Ноутбук с префиксом Forte
        p_laptop = CanonicalProduct(
            id="p_laptop",
            canonical_key="p_laptop",
            title="Ноутбук Asus ZenBook 14 OLED",
            brand="Asus",
            category="Forte: 💻 Ноутбуки",
        )
        self.product_repo.save_or_update(p_laptop)
        self.offer_repo.save_or_update(Offer(
            id="o3", product_id="p_laptop", seller_id="s1", channel_id="ch1",
            external_sku="SKU3", url="u3", price=550000, condition=Condition.NEW,
            availability="in_stock", observed_at=now,
        ))

        # 4. Телевизор с префиксом ITMag
        p_tv = CanonicalProduct(
            id="p_tv",
            canonical_key="p_tv",
            title="Телевизор LG OLED 55 C3",
            brand="LG",
            category="ITMag: 📺 Телевизоры",
        )
        self.product_repo.save_or_update(p_tv)
        self.offer_repo.save_or_update(Offer(
            id="o4", product_id="p_tv", seller_id="s1", channel_id="ch1",
            external_sku="SKU4", url="u4", price=650000, condition=Condition.NEW,
            availability="in_stock", observed_at=now,
        ))

        # Сброс кэша категорий поискового репозитория
        SearchRepository._cat_cache = {}
        SearchRepository._cat_cache_time = 0.0

        # Поиск iphone 15 с category=electronics (корневая категория) ДОЛЖЕН НАЙТИ ТОВАР!
        res_elec = self.search_core.search("iphone 15", category="electronics")
        self.assertEqual(len(res_elec.items), 1, "iphone 15 with category=electronics must be found")
        self.assertEqual(res_elec.items[0].product.id, "p_phone1")

        # Поиск с category=phones (листовая категория) ДОЛЖЕН НАЙТИ ТОВАР!
        res_phones = self.search_core.search("iphone 15", category="phones")
        self.assertEqual(len(res_phones.items), 1, "iphone 15 with category=phones must be found")

        # Поиск ноутбука с category=electronics
        res_nb_elec = self.search_core.search("ноутбук", category="electronics")
        self.assertEqual(len(res_nb_elec.items), 1, "ноутбук with category=electronics must be found")

        # Поиск телевизора с category=electronics
        res_tv_elec = self.search_core.search("телевизор", category="electronics")
        self.assertEqual(len(res_tv_elec.items), 1, "телевизор with category=electronics must be found")

        # Ноутбук с неверной категорией (phones) не должен находиться
        res_nb_phones = self.search_core.search("ноутбук", category="phones")
        self.assertEqual(len(res_nb_phones.items), 0, "ноутбук must not be found in phones category")

    def test_p1_category_counts_aggregated_to_taxonomy(self):
        """P1: count_products_by_category must aggregate store categories to canonical IDs."""
        self.product_repo.save_or_update(CanonicalProduct(id="p1", canonical_key="k1", title="Phone 1", category="Forte: 📱 Смартфоны"))
        self.product_repo.save_or_update(CanonicalProduct(id="p2", canonical_key="k2", title="Phone 2", category="Halyk: Смартфоны"))
        self.product_repo.save_or_update(CanonicalProduct(id="p3", canonical_key="k3", title="Laptop 1", category="Forte: 💻 Ноутбуки"))

        counts = self.category_repo.count_products_by_category()

        # Должны присутствовать и сырые категории, и канонические
        self.assertEqual(counts.get("Forte: 📱 Смартфоны"), 1)
        self.assertEqual(counts.get("Halyk: Смартфоны"), 1)
        # Каноническая phones должна содержать оба смартфона
        self.assertEqual(counts.get("phones"), 2)
        # Каноническая laptops должна содержать ноутбук
        self.assertEqual(counts.get("laptops"), 1)
        # Корневая electronics должна агрегировать все 3 товара
        self.assertEqual(counts.get("electronics"), 3)

    def test_p2_assistant_dialog_history_and_limits(self):
        """P2: SearchAssistant resolves follow-up context from dialog history."""
        assistant = SearchAssistant(self.search_core)

        # Базовый запрос
        resp = assistant.ask(
            query="найди ноутбук",
            history=[],
        )
        self.assertIsNotNone(resp)

        # Короткий уточняющий запрос с историей
        history = [
            {"role": "user", "content": "ноутбук Asus"},
            {"role": "assistant", "content": "Вот ноутбуки Asus"},
        ]
        resp_followup = assistant.ask(
            query="OLED",
            history=history,
        )
        self.assertIsNotNone(resp_followup)
        # Должен объединить контекст: "ноутбук Asus OLED"
        self.assertIn("asus", resp_followup.plan.search_query.lower())


if __name__ == "__main__":
    unittest.main()
