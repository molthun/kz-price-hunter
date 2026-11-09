from tests import test_support  # noqa: F401
import unittest
import sqlite3
from unittest.mock import patch, MagicMock

from scrapers.showcase_scanners import (
    ShowcaseOffer,
    extract_marketplace_merchant_info,
    KaspiShowcaseScanner,
    HalykShowcaseScanner,
    ForteShowcaseScanner,
    WildberriesShowcaseScanner,
    OzonShowcaseScanner,
)
from domain.channel_sync import (
    match_or_create_canonical_product,
    upsert_showcase_offer,
    sync_channel_showcase,
)
from repositories import init_schema_v2
from repositories.catalog_expansion_repo import DDL as EXPANSION_DDL


def create_test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_v2(conn)
    conn.executescript(EXPANSION_DDL)
    return conn


class ShowcaseScannersTests(unittest.TestCase):
    def test_extract_marketplace_merchant_info(self):
        # Kaspi
        assert extract_marketplace_merchant_info("https://kaspi.kz/shop/info/merchant/tehnomart/") == ("kaspi", "tehnomart")
        assert extract_marketplace_merchant_info("https://kaspi.kz/shop/m/18430177/products/") == ("kaspi", "18430177")
        assert extract_marketplace_merchant_info("https://kaspi.kz/shop/info/merchant/103190") == ("kaspi", "103190")
        assert extract_marketplace_merchant_info("kaspi:103190") == ("kaspi", "103190")

        # Halyk
        assert extract_marketplace_merchant_info("https://halykmarket.kz/merchant/sulpak-123") == ("halyk", "sulpak-123")
        assert extract_marketplace_merchant_info("halyk:456") == ("halyk", "456")

        # Forte
        assert extract_marketplace_merchant_info("https://market.forte.kz/merchant/mechta") == ("forte", "mechta")
        assert extract_marketplace_merchant_info("https://forte.kz/merchant/222") == ("forte", "222")
        assert extract_marketplace_merchant_info("forte:789") == ("forte", "789")

        # Unknown
        assert extract_marketplace_merchant_info("https://example.com/shop") == (None, None)
        assert extract_marketplace_merchant_info("") == (None, None)

    def test_kaspi_showcase_scanner_parse(self):
        scanner = KaspiShowcaseScanner(city_code="750000000", city_name="Алматы")
        mock_payload = {
            "data": {
                "cards": [
                    {
                        "id": "1001",
                        "title": "Смартфон Apple iPhone 15 128GB Black",
                        "unitPrice": 420000,
                        "unitSalePrice": 399990,
                        "shopLink": "/shop/p/apple-iphone-15-128gb-black-1001/",
                        "stock": 5,
                        "rating": 4.9,
                        "reviewsQuantity": 150,
                        "categoryRu": ["Электроника", "Смартфоны"],
                        "previewImages": [{"large": "https://img.kaspi.kz/1001.jpg"}],
                    },
                    {
                        "id": "1002",
                        "title": "Товар не в наличии",
                        "unitPrice": 10000,
                        "stock": 0,
                    }
                ]
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload

        with patch("scrapers.showcase_scanners.requests.get", return_value=mock_resp):
            offers, is_last = scanner.fetch_merchant_page("test_seller", page=1)

        assert len(offers) == 2
        assert offers[0].external_sku == "1001"
        assert offers[0].price == 399990.0
        assert offers[0].old_price == 420000.0
        assert offers[0].rating == 4.9
        assert offers[0].reviews_count == 150
        assert offers[0].category == "Смартфоны"
        assert offers[0].in_stock is True
        assert offers[1].in_stock is False

    def test_halyk_showcase_scanner_parse(self):
        scanner = HalykShowcaseScanner(city_code="-2", city_name="Алматы")
        mock_payload = {
            "products": [
                {
                    "id": "2001",
                    "name": "Ноутбук ASUS Vivobook 15",
                    "price": 250000,
                    "old_price": 280000,
                    "url_slug": "noutbuk-asus-vivobook-15",
                    "picture": "https://halyk.kz/asus.jpg",
                }
            ],
            "products_total": 1
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload

        with patch("scrapers.showcase_scanners.requests.get", return_value=mock_resp):
            offers, is_last = scanner.fetch_merchant_page("test_seller", page=1)

        assert len(offers) == 1
        assert offers[0].external_sku == "2001"
        assert offers[0].price == 250000.0
        assert offers[0].old_price == 280000.0
        assert is_last is True

    def test_forte_showcase_scanner_parse(self):
        scanner = ForteShowcaseScanner(city_name="Алматы")
        mock_payload = {
            "hits": [
                {
                    "objectID": "3001",
                    "Name": "Планшет Samsung Galaxy Tab A9",
                    "Price": 85000,
                    "URL": "https://market.forte.kz/items/3001",
                    "Picture": "https://forte.kz/tab.jpg",
                }
            ],
            "nbPages": 1
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload

        with patch("scrapers.showcase_scanners.requests.post", return_value=mock_resp):
            offers, is_last = scanner.fetch_merchant_page("test_seller", page=1)

        assert len(offers) == 1
        assert offers[0].external_sku == "3001"
        assert offers[0].price == 85000.0
        assert is_last is True

    def test_channel_sync_upsert_offer_and_matching(self):
        conn = create_test_db()
        # Insert seller and channel
        conn.execute("INSERT INTO sellers (id, name, slug, created_at, updated_at) VALUES ('s1', 'Тест Продавец', 'test-seller', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, created_at) VALUES ('ch1', 's1', 'kaspi', 'Kaspi Витрина', '103190', '2026-01-01')")
        conn.commit()

        offer = ShowcaseOffer(
            marketplace="kaspi",
            merchant_id="103190",
            external_sku="sku_99",
            title="Смартфон Apple iPhone 15 256GB Blue",
            price=450000.0,
            old_price=480000.0,
            url="https://kaspi.kz/shop/p/iphone15-256",
            image_url="https://img.kaspi.kz/iphone15.jpg",
            city="Алматы",
            in_stock=True,
        )

        # 1. Matching & Canonical Product creation
        prod_id, is_new = match_or_create_canonical_product(conn, offer, "2026-01-01T00:00:00Z")
        assert is_new is True
        assert prod_id.startswith("prod_")

        # Second match should return existing product
        prod_id_2, is_new_2 = match_or_create_canonical_product(conn, offer, "2026-01-01T00:00:00Z")
        assert is_new_2 is False
        assert prod_id_2 == prod_id

        # 2. Upsert Offer
        offer_id = upsert_showcase_offer(conn, offer, prod_id, "s1", "ch1", "2026-01-01T00:00:00Z")
        assert offer_id.startswith("off_ch1_")

        # Verify offer in DB
        row = conn.execute("SELECT id, price, availability, is_active FROM offers WHERE id = ?", (offer_id,)).fetchone()
        assert row["price"] == 450000.0
        assert row["availability"] == "in_stock"
        assert row["is_active"] == 1

        # Verify offer price history
        hist = conn.execute("SELECT price FROM offer_price_history WHERE offer_id = ?", (offer_id,)).fetchall()
        assert len(hist) == 1
        assert hist[0]["price"] == 450000.0

        # Verify attributes extracted from title (storage: 256 ГБ, color: Синий)
        attrs = conn.execute("SELECT key, value FROM product_search_attributes WHERE product_id = ? ORDER BY key", (prod_id,)).fetchall()
        attr_map = {r["key"]: r["value"] for r in attrs}
        assert attr_map.get("storage") == "256 ГБ"
        assert attr_map.get("color") == "Синий"

    def test_sync_channel_showcase_end_to_end(self):
        import asyncio
        async def _run():
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, name, slug, created_at, updated_at) VALUES ('s1', 'Тест Магазин', 'test-mag', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, base_url, created_at) VALUES ('ch_kaspi', 's1', 'kaspi', 'Витрина Kaspi', 'https://kaspi.kz/shop/info/merchant/tehnomart/', '2026-01-01')")
            conn.commit()

            mock_offer = ShowcaseOffer(
                marketplace="kaspi",
                merchant_id="tehnomart",
                external_sku="sku_1",
                title="Телевизор LG 55 дюймов 4K",
                price=299990.0,
                url="https://kaspi.kz/shop/p/lg55",
                city="Алматы",
                in_stock=True,
            )

            with patch.object(KaspiShowcaseScanner, "scan_merchant_showcase", return_value=[mock_offer]):
                result = await sync_channel_showcase(conn, "ch_kaspi")

            assert result["ok"] is True
            assert result["scanned_count"] == 1
            assert result["offers_upserted"] == 1

            # Check that channel metadata_json updated
            ch_row = conn.execute("SELECT metadata_json FROM channels WHERE id = 'ch_kaspi'").fetchone()
            import json
            meta = json.loads(ch_row["metadata_json"])
            assert meta["products_count"] == 1
            assert meta["last_sync_status"] == "ok"
            assert "last_sync_at" in meta

        asyncio.run(_run())

    def test_sync_channel_showcase_wildberries_and_ozon(self):
        import asyncio
        async def _run():
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, name, slug, created_at, updated_at) VALUES ('s_tech', 'ТехноМагазин', 'techno-mag', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, base_url, created_at) VALUES ('ch_wb', 's_tech', 'wildberries', 'Витрина WB', 'https://www.wildberries.kz/seller/987654', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, base_url, created_at) VALUES ('ch_ozon', 's_tech', 'ozon', 'Витрина Ozon', 'https://ozon.kz/seller/tech-seller-1122/', '2026-01-01')")
            conn.commit()

            wb_mock_offer = ShowcaseOffer(
                marketplace="wildberries",
                merchant_id="987654",
                external_sku="wb_item_1",
                title="Ноутбук ASUS Vivobook 15 16GB",
                price=320000.0,
                old_price=350000.0,
                url="https://www.wildberries.kz/catalog/12345/detail.aspx",
                image_url="https://basket-01.wbbasket.ru/vol12/part123/12345/images/big/1.webp",
                city="Алматы",
                in_stock=True,
            )

            ozon_mock_offer = ShowcaseOffer(
                marketplace="ozon",
                merchant_id="tech-seller-1122",
                external_sku="oz_item_2",
                title="Смартфон Samsung Galaxy A55 128GB",
                price=189990.0,
                old_price=209990.0,
                url="https://ozon.kz/product/54321/",
                image_url="https://ir.ozon.kz/s3/multimedia-1/c1000/photo.jpg",
                city="Алматы",
                in_stock=True,
            )

            # Test Wildberries showcase sync
            with patch.object(WildberriesShowcaseScanner, "scan_merchant_showcase", return_value=[wb_mock_offer]):
                wb_res = await sync_channel_showcase(conn, "ch_wb")

            assert wb_res["ok"] is True
            assert wb_res["scanned_count"] == 1
            assert wb_res["offers_upserted"] == 1
            assert wb_res["marketplace"] == "wildberries"

            # Check WB offer in DB
            wb_row = conn.execute("SELECT price, old_price, availability FROM offers WHERE channel_id = 'ch_wb'").fetchone()
            assert wb_row["price"] == 320000.0
            assert wb_row["old_price"] == 350000.0
            assert wb_row["availability"] == "in_stock"

            # Test Ozon showcase sync
            with patch.object(OzonShowcaseScanner, "scan_merchant_showcase", return_value=[ozon_mock_offer]):
                oz_res = await sync_channel_showcase(conn, "ch_ozon")

            assert oz_res["ok"] is True
            assert oz_res["scanned_count"] == 1
            assert oz_res["offers_upserted"] == 1
            assert oz_res["marketplace"] == "ozon"

            # Check Ozon offer in DB
            oz_row = conn.execute("SELECT price, old_price, availability FROM offers WHERE channel_id = 'ch_ozon'").fetchone()
            assert oz_row["price"] == 189990.0
            assert oz_row["old_price"] == 209990.0

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
