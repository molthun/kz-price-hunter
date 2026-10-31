"""
Tests for Trust & Provenance (Stage 28).
Verifies:
1. Rule 28: No generic "Проверенный продавец" label, but objective verifiable facts.
2. Seller facts collection:
   - Physical address
   - Website
   - Instagram / Telegram / WhatsApp
   - Marketplace presence
   - Phone
   - BIN
   - Active today
3. Offer provenance contract:
   - source
   - observed_at
   - published_at
   - freshness
4. Clean JSON serialization in SearchCore.search response.
"""

from tests import test_support  # must be first
import datetime
import sqlite3
import unittest

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.provenance import build_offer_provenance, build_seller_provenance
from domain.search_core import SearchCore
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)


class TestSearchProvenanceFacts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.core = SearchCore(self.search_repo)

        # 1. Продавец с полным набором проверяемых данных
        self.seller_1 = Seller(
            id="s_megastore",
            slug="megastore",
            name="MegaStore KZ",
            rating=4.9,
            domain="megastore.kz",
            phone="+77011234567",
            bin="123456789012",
            legal_name="ТОО MegaStore Kazakhstan",
            metadata={"address": "г. Алматы, ул. Абая 10", "instagram": "megastore_kz"},
        )
        self.seller_repo.save_or_update(self.seller_1)

        self.ch_web = Channel(
            id="ch_ms_web",
            seller_id="s_megastore",
            channel_type=ChannelType.WEBSITE,
            name="MegaStore Web",
            base_url="https://megastore.kz",
        )
        self.ch_kaspi = Channel(
            id="ch_ms_kaspi",
            seller_id="s_megastore",
            channel_type=ChannelType.KASPI,
            name="MegaStore Kaspi",
        )
        self.ch_insta = Channel(
            id="ch_ms_insta",
            seller_id="s_megastore",
            channel_type=ChannelType.INSTAGRAM,
            name="MegaStore Insta",
        )
        self.channel_repo.save_or_update(self.ch_web)
        self.channel_repo.save_or_update(self.ch_kaspi)
        self.channel_repo.save_or_update(self.ch_insta)

        # 2. Канонический товар
        self.prod = CanonicalProduct(
            id="prod_s24",
            canonical_key="samsung-s24-ultra-256gb",
            title="Samsung Galaxy S24 Ultra 256GB Titanium Gray",
            brand="Samsung",
            category="smartphones",
        )
        self.product_repo.save_or_update(self.prod)

        # 3. Офферы
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.off1 = Offer(
            id="off_ms_1",
            product_id="prod_s24",
            seller_id="s_megastore",
            channel_id="ch_ms_web",
            external_sku="ms_s24_1",
            price=450000.0,
            condition=Condition.NEW,
            availability=Availability.IN_STOCK,
            observed_at=now_iso,
            published_at=now_iso,
            raw_payload={"source": "megastore_crawler"},
            url="https://megastore.kz/p/s24",
        )
        self.off2 = Offer(
            id="off_ms_2",
            product_id="prod_s24",
            seller_id="s_megastore",
            channel_id="ch_ms_kaspi",
            external_sku="ms_s24_2",
            price=460000.0,
            condition=Condition.NEW,
            availability=Availability.IN_STOCK,
            observed_at=now_iso,
            raw_payload={"source": "kaspi_api"},
            url="https://kaspi.kz/shop/p/s24",
        )
        self.offer_repo.save_or_update(self.off1)
        self.offer_repo.save_or_update(self.off2)

    def tearDown(self):
        self.conn.close()

    def test_seller_provenance_facts_and_no_generic_badge(self):
        """Проверяет правило Этапа 28: объективные факты без надписи 'Проверенный продавец'."""
        res = self.core.search("Samsung Galaxy S24")
        self.assertEqual(len(res.items), 1)

        item = res.items[0]
        self.assertEqual(len(item.sellers), 1)
        sg = item.sellers[0]

        # 1. Проверяем наличие объективных фактов
        facts = sg.facts
        self.assertIn("Физический адрес найден", facts)
        self.assertIn("Сайт найден", facts)
        self.assertIn("Instagram найден", facts)
        self.assertIn("Присутствие на маркетплейсах (kaspi)", facts)
        self.assertIn("Контактный телефон найден", facts)
        self.assertIn("Публичный БИН указан", facts)
        self.assertIn("Последняя активность сегодня", facts)

        # 2. Строгое правило: никакой голословной надписи «Проверенный продавец»
        for f in facts:
            self.assertNotIn("Проверенный продавец", f)
        sg_dict = sg.to_dict()
        self.assertNotIn("is_verified_seller", sg_dict)

    def test_offer_provenance_passport(self):
        """Проверяет паспорт происхождения и свежести предложения (source, observed_at, freshness)."""
        res = self.core.search("Samsung Galaxy S24")
        item = res.items[0]
        self.assertEqual(len(item.offers), 2)

        for off in item.offers:
            # Обязательные атрибуты оффера по Этапу 28
            self.assertTrue(bool(off.source))
            self.assertIsNotNone(off.observed_at)
            self.assertIn(off.freshness, ("fresh", "moderate", "stale"))
            self.assertIsNotNone(off.provenance)
            self.assertIn("freshness", off.provenance)
            self.assertIn("source", off.provenance)
            self.assertIn("channel_type", off.provenance)

    def test_offer_provenance_function_directly(self):
        """Прямой контракт функции build_offer_provenance."""
        row = {
            "id": "off_direct_1",
            "channel_type": "telegram",
            "source": "tg_channel_scanner",
            "observed_at": "2026-10-31T12:00:00+00:00",
            "published_at": "2026-10-31T10:00:00+00:00",
            "condition": "used",
        }
        prov = build_offer_provenance(row)
        self.assertEqual(prov.offer_id, "off_direct_1")
        self.assertEqual(prov.source, "tg_channel_scanner")
        self.assertEqual(prov.channel_type, "telegram")
        self.assertEqual(prov.published_at, "2026-10-31T10:00:00+00:00")
        self.assertEqual(prov.observed_at, "2026-10-31T12:00:00+00:00")

    async def test_search_v2_api_returns_facts_and_provenance(self):
        """Проверяет сериализацию facts и provenance в /api/v2/search."""
        import json
        from aiohttp.test_utils import make_mocked_request
        import database
        from web.server import search_v2_handler

        orig_conn = database.get_connection
        database.get_connection = lambda: self.conn
        try:
            req = make_mocked_request("GET", "/api/v2/search?q=Samsung+S24")
            resp = await search_v2_handler(req)
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.text)
            self.assertEqual(len(data["items"]), 1)
            item = data["items"][0]
            self.assertTrue(len(item["sellers"]) > 0)
            seller = item["sellers"][0]
            self.assertIn("facts", seller)
            self.assertTrue(len(seller["facts"]) > 0)
            self.assertIn("Физический адрес найден", seller["facts"])
            self.assertIn("provenance", seller)

            self.assertTrue(len(item["offers"]) > 0)
            off = item["offers"][0]
            self.assertIn("source", off)
            self.assertIn("observed_at", off)
            self.assertIn("freshness", off)
            self.assertIn("provenance", off)
        finally:
            database.get_connection = orig_conn


if __name__ == "__main__":
    unittest.main()
