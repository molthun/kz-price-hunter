"""
Tests for Repository Layer (Stage 29, Search Platform 2.0).
Verifies:
1. ProductRepository: lifecycle, canonical key, catalog queries.
2. SellerRepository: registration, slug resolution, channel lookup.
3. OfferRepository: multi-channel persistence, query by product, deactivation.
4. SearchRepository: FTS search, facets, condition filtering.
5. HistoryRepository: isolated time-series price lines, point recording, stats.
6. DiscoveryRepository: unified facade for tasks, candidates, social sources.
7. TelemetryRepository: search analytics KPI, fail-open system telemetry events.
"""

from tests import test_support  # must be first
import datetime
import sqlite3
import unittest

from domain.adaptive_discovery import DiscoveryTaskStatus
from domain.discovery import CandidateStatus, SellerCandidate
from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    OfferPriceHistory,
    Seller,
)
from domain.search_analytics import SearchEvent
from domain.search_core import SearchOutcome
from domain.social_connector import SocialSource
from repositories import (
    DiscoveryRepository,
    HistoryRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    TelemetryRepository,
    init_schema_v2,
)


class TestRepositoryLayer(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        # Инициализируем v2 схему и таблицы телеметрии
        init_schema_v2(self.conn)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                severity TEXT NOT NULL,
                component TEXT,
                scan_id TEXT,
                message TEXT NOT NULL,
                data_json TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)

        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.history_repo = HistoryRepository(self.conn)
        self.discovery_repo = DiscoveryRepository(self.conn)
        self.telemetry_repo = TelemetryRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    # -------------------------------------------------------------------------
    # 1. ProductRepository
    # -------------------------------------------------------------------------
    def test_product_repository(self):
        prod = CanonicalProduct(
            id="prod_macbook_m3",
            canonical_key="apple:macbook_air_m3_16gb",
            title="Apple MacBook Air 13 M3 16GB 512GB Space Gray",
            category="laptops",
            brand="Apple",
            model="MacBook Air M3",
        )
        self.product_repo.save_or_update(prod)

        loaded = self.product_repo.get_by_id("prod_macbook_m3")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.brand, "Apple")

        by_key = self.product_repo.get_by_canonical_key("apple:macbook_air_m3_16gb")
        self.assertIsNotNone(by_key)
        self.assertEqual(by_key.id, "prod_macbook_m3")

        all_prods = self.product_repo.list_all()
        self.assertEqual(len(all_prods), 1)

    # -------------------------------------------------------------------------
    # 2. SellerRepository
    # -------------------------------------------------------------------------
    def test_seller_repository(self):
        seller = Seller(
            id="s_technodom",
            slug="technodom",
            name="Технодом",
            domain="technodom.kz",
            phone="+77271112233",
            bin="010203040506",
        )
        self.seller_repo.save_or_update(seller)

        loaded = self.seller_repo.get_by_id("s_technodom")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.slug, "technodom")

        by_slug = self.seller_repo.get_by_slug("technodom")
        self.assertIsNotNone(by_slug)
        self.assertEqual(by_slug.domain, "technodom.kz")

        self.assertEqual(self.seller_repo.count(), 1)

    # -------------------------------------------------------------------------
    # 3. OfferRepository
    # -------------------------------------------------------------------------
    def test_offer_repository(self):
        # Подготовим связи
        self.product_repo.save_or_update(
            CanonicalProduct(
                id="p_ps5",
                canonical_key="sony:playstation_5_slim",
                title="Sony PlayStation 5 Slim 1TB",
                category="gaming",
                brand="Sony",
            )
        )
        self.seller_repo.save_or_update(
            Seller(id="s_marwin", slug="marwin", name="Marwin KZ")
        )
        from repositories.channel_repo import ChannelRepository
        ch_repo = ChannelRepository(self.conn)
        ch_repo.save_or_update(
            Channel(id="ch_m_dir", seller_id="s_marwin", channel_type=ChannelType.DIRECT, name="Marwin Online")
        )

        off = Offer(
            id="off_ps5_1",
            product_id="p_ps5",
            seller_id="s_marwin",
            channel_id="ch_m_dir",
            external_sku="sku_ps5_marwin",
            price=279990.0,
            url="https://marwin.kz/p/ps5",
            condition=Condition.NEW,
            availability=Availability.IN_STOCK,
        )
        self.offer_repo.save_or_update(off)

        loaded = self.offer_repo.get_by_id("off_ps5_1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.price, 279990.0)

        active = self.offer_repo.get_active_offers_for_product("p_ps5")
        self.assertEqual(len(active), 1)

        # Deactivation
        self.offer_repo.deactivate_offer("off_ps5_1")
        self.assertEqual(len(self.offer_repo.get_active_offers_for_product("p_ps5")), 0)

    # -------------------------------------------------------------------------
    # 4. SearchRepository
    # -------------------------------------------------------------------------
    def test_search_repository(self):
        # Создаем товар с оффером для поиска
        self.product_repo.save_or_update(
            CanonicalProduct(
                id="p_dyson",
                canonical_key="dyson:supersonic_hd07",
                title="Фен Dyson Supersonic HD07 Nickel/Copper",
                category="hair_care",
                brand="Dyson",
            )
        )
        self.seller_repo.save_or_update(Seller(id="s_mechta", slug="mechta", name="Мечта"))
        from repositories.channel_repo import ChannelRepository
        ChannelRepository(self.conn).save_or_update(
            Channel(id="ch_m_web", seller_id="s_mechta", channel_type=ChannelType.WEBSITE, name="Мечта Web")
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_dyson_1",
                product_id="p_dyson",
                seller_id="s_mechta",
                channel_id="ch_m_web",
                external_sku="dyson_hd07",
                price=199990.0,
                url="https://mechta.kz/dyson",
                condition=Condition.NEW,
                availability=Availability.IN_STOCK,
            )
        )

        res = self.search_repo.search_products(search_text="Dyson Supersonic")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["product"].id, "p_dyson")
        self.assertEqual(len(res[0]["offers"]), 1)

    # -------------------------------------------------------------------------
    # 5. HistoryRepository
    # -------------------------------------------------------------------------
    def test_history_repository(self):
        # 1. Запись единичных наблюдений и батчей
        self.history_repo.record_point("off_h1", 10000.0, 12000.0, "2026-10-31T10:00:00Z")
        self.history_repo.record_batch([
            {"offer_id": "off_h1", "price": 9500.0, "observed_at": "2026-10-31T12:00:00Z"},
            {"offer_id": "off_h2", "price": 50000.0, "observed_at": "2026-10-31T11:00:00Z"},
        ])

        pts = self.history_repo.get_offer_price_history("off_h1")
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0].price, 10000.0)
        self.assertEqual(pts[1].price, 9500.0)

        stats = self.history_repo.get_history_stats()
        self.assertEqual(stats["total_points"], 3)
        self.assertEqual(stats["unique_offers"], 2)

        # 2. Проверка изолированных рядов цен по товару
        self.product_repo.save_or_update(
            CanonicalProduct(id="p_hist", canonical_key="key:hist", title="Тестовый товар", category="c")
        )
        self.seller_repo.save_or_update(Seller(id="s_h1", slug="sh1", name="Магазин H1"))
        from repositories.channel_repo import ChannelRepository
        ChannelRepository(self.conn).save_or_update(
            Channel(id="ch_h1", seller_id="s_h1", channel_type=ChannelType.DIRECT, name="Direct")
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_hist_prod",
                product_id="p_hist",
                seller_id="s_h1",
                channel_id="ch_h1",
                external_sku="sku_h",
                price=15000.0,
                url="http://hist",
            )
        )
        report = self.history_repo.get_product_price_history("p_hist")
        self.assertIsNotNone(report)
        self.assertEqual(report.product_id, "p_hist")
        self.assertEqual(len(report.series), 1)
        self.assertEqual(report.series[0].seller_id, "s_h1")

    # -------------------------------------------------------------------------
    # 6. DiscoveryRepository
    # -------------------------------------------------------------------------
    def test_discovery_repository(self):
        # 1. Задачи адаптивного поиска
        task = self.discovery_repo.create_or_update_task(
            signal_query="купить квадрокоптер dji",
            city="Алматы",
            priority_score=8.5,
        )
        self.assertIsNotNone(task)
        self.assertEqual(task.status, DiscoveryTaskStatus.PENDING)

        tasks = self.discovery_repo.list_tasks(status=DiscoveryTaskStatus.PENDING)
        self.assertEqual(len(tasks), 1)

        self.discovery_repo.update_task_status(task.id, DiscoveryTaskStatus.COMPLETED, notes="Найдено 2 продавца")
        updated_task = self.discovery_repo.get_task_by_id(task.id)
        self.assertEqual(updated_task.status, DiscoveryTaskStatus.COMPLETED)

        # 2. Кандидаты продавцов
        cand = SellerCandidate(
            id="cand_dji_store",
            name="DJI Almaty Store",
            source_type="discovery_search",
            source_url="https://dji-almaty.kz",
            status=CandidateStatus.DISCOVERED,
            phone="+77019998877",
            domain="dji-almaty.kz",
        )
        self.discovery_repo.save_candidate(cand)

        loaded_cand = self.discovery_repo.get_candidate_by_id("cand_dji_store")
        self.assertIsNotNone(loaded_cand)
        self.assertEqual(loaded_cand.domain, "dji-almaty.kz")

        # 3. Источники соцсетей
        src = SocialSource(
            id="src_insta_dji",
            platform="instagram",
            handle="dji_kazakhstan",
            url="https://instagram.com/dji_kazakhstan",
        )
        self.discovery_repo.save_social_source(src)
        loaded_src = self.discovery_repo.get_social_source("instagram", "dji_kazakhstan")
        self.assertIsNotNone(loaded_src)

        # 4. Сводная статистика Discovery
        d_stats = self.discovery_repo.get_discovery_stats()
        self.assertEqual(d_stats["tasks"]["total"], 1)
        self.assertEqual(d_stats["candidates"]["total"], 1)
        self.assertEqual(len(d_stats["social_sources"]), 1)

    # -------------------------------------------------------------------------
    # 7. TelemetryRepository
    # -------------------------------------------------------------------------
    def test_telemetry_repository(self):
        # 1. Поисковая аналитика
        ev = SearchEvent(
            id="ev_search_1",
            query="macbook m3",
            normalized_query="macbook m3",
            outcome=SearchOutcome.FOUND,
            total_products=1,
            total_offers=3,
            latency_ms=15.4,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self.telemetry_repo.record_search_event(ev)

        summary = self.telemetry_repo.get_search_quality_summary()
        self.assertEqual(summary.total_searches, 1)
        self.assertEqual(summary.found_count, 1)

        kpi = self.telemetry_repo.get_search_kpi(hours=24)
        self.assertEqual(kpi["total_searches"], 1)
        self.assertIn("outcomes", kpi)
        self.assertEqual(kpi["latency_p50_ms"], 15.4)

        # 2. Системные события телеметрии
        ev_id = self.telemetry_repo.record_system_event(
            event_type="crawler_finished",
            severity="INFO",
            component="discovery",
            message="Discovery crawl finished",
            data={"count": 5},
            correlation_id="scan_1234",
        )
        self.assertIsNotNone(ev_id)

        events = self.telemetry_repo.list_system_events(event_type="crawler_finished")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message"], "Discovery crawl finished")
        self.assertEqual(events[0]["data"]["count"], 5)

        telemetry_sum = self.telemetry_repo.get_telemetry_summary()
        self.assertEqual(telemetry_sum["total_events"], 1)


if __name__ == "__main__":
    unittest.main()
