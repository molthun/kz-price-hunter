"""Tests for marketplace channels integration (Wildberries KZ, Ozon KZ, Kaspi, Forte, Halyk)."""
from tests import test_support  # noqa: F401  isolates DATA_DIR
import unittest

from domain.channel_pricing import (
    ChannelSavings,
    ProductChannelMatrix,
    SellerChannelPricing,
    build_channel_matrix,
)
from domain.channel_scheduler import is_marketplace_channel
from domain.models import ChannelType, Condition, Offer, Seller
from domain.seller_onboarding import _extract_marketplace_seller_id, extract_marketplace_info
from scrapers.showcase_scanners import extract_marketplace_merchant_info


class MarketplaceChannelsTest(unittest.TestCase):
    def test_channel_types_include_wb_and_ozon(self):
        self.assertEqual(ChannelType.WILDBERRIES, "wildberries")
        self.assertEqual(ChannelType.OZON, "ozon")
        self.assertIn(ChannelType.WILDBERRIES, ChannelType.ALL)
        self.assertIn(ChannelType.OZON, ChannelType.ALL)

    def test_is_marketplace_channel_detection(self):
        # By channel_type string
        self.assertTrue(is_marketplace_channel("wildberries", None))
        self.assertTrue(is_marketplace_channel("wb", None))
        self.assertTrue(is_marketplace_channel("ozon", None))
        self.assertTrue(is_marketplace_channel("kaspi", None))
        self.assertTrue(is_marketplace_channel("forte", None))
        self.assertTrue(is_marketplace_channel("marketplace", None))

        # By base_url
        self.assertTrue(is_marketplace_channel("store", "https://www.wildberries.kz/seller/123"))
        self.assertTrue(is_marketplace_channel("store", "https://ozon.kz/seller/alser"))
        self.assertTrue(is_marketplace_channel("store", "https://kaspi.kz/shop/c/smartphones/"))

        # Direct / standard shops
        self.assertFalse(is_marketplace_channel("direct", "https://alser.kz"))
        self.assertFalse(is_marketplace_channel("website", "https://sulpak.kz"))
        self.assertFalse(is_marketplace_channel("physical_store", "https://technodom.kz"))

    def test_seller_onboarding_extract_marketplace_info(self):
        # Wildberries
        wb_url = "https://www.wildberries.kz/seller/123456"
        p_wb, id_wb = extract_marketplace_info(wb_url)
        self.assertEqual(p_wb, "Wildberries")
        self.assertEqual(id_wb, "123456")
        self.assertEqual(_extract_marketplace_seller_id([wb_url]), "123456")

        # Ozon
        ozon_url = "https://ozon.kz/seller/my-tech-store-7788/"
        p_oz, id_oz = extract_marketplace_info(ozon_url)
        self.assertEqual(p_oz, "Ozon")
        self.assertEqual(id_oz, "my-tech-store-7788")
        self.assertEqual(_extract_marketplace_seller_id([ozon_url]), "my-tech-store-7788")

        # Explicit id overrides links
        self.assertEqual(_extract_marketplace_seller_id([ozon_url], explicit_id="custom-99"), "custom-99")

    def test_build_channel_matrix_with_wb_and_ozon(self):
        # Seller 1: sells directly on website (250,000) and on WB (275,000)
        s1_id = "seller_techno"
        s1_name = "ТехноМаркет"

        # Seller 2: sells directly on website (280,000) and on Ozon (320,000)
        s2_id = "seller_electro"
        s2_name = "ЭлектроМир"

        sellers_map = {
            s1_id: Seller(id=s1_id, slug="technomarket", name=s1_name),
            s2_id: Seller(id=s2_id, slug="electromir", name=s2_name),
        }

        channel_types = {
            "ch_s1_web": ChannelType.WEBSITE,
            "ch_s1_wb": ChannelType.WILDBERRIES,
            "ch_s2_web": ChannelType.WEBSITE,
            "ch_s2_ozon": ChannelType.OZON,
        }

        offers = [
            Offer(
                id="off_s1_web",
                product_id="prod_tv_samsung",
                seller_id=s1_id,
                channel_id="ch_s1_web",
                external_sku="sku_s1_w",
                url="https://technomarket.kz/p/tv",
                price=250000.0,
            ),
            Offer(
                id="off_s1_wb",
                product_id="prod_tv_samsung",
                seller_id=s1_id,
                channel_id="ch_s1_wb",
                external_sku="sku_s1_wb",
                url="https://wildberries.kz/catalog/123/detail.aspx",
                price=275000.0,
            ),
            Offer(
                id="off_s2_web",
                product_id="prod_tv_samsung",
                seller_id=s2_id,
                channel_id="ch_s2_web",
                external_sku="sku_s2_w",
                url="https://electromir.kz/p/tv",
                price=280000.0,
            ),
            Offer(
                id="off_s2_ozon",
                product_id="prod_tv_samsung",
                seller_id=s2_id,
                channel_id="ch_s2_ozon",
                external_sku="sku_s2_oz",
                url="https://ozon.kz/product/456",
                price=320000.0,
            ),
        ]

        matrix = build_channel_matrix(
            product_id="prod_tv_samsung",
            canonical_key="samsung-tv-55",
            title="Телевизор Samsung 55",
            offers=offers,
            sellers_map=sellers_map,
            channel_types=channel_types,
        )

        self.assertEqual(matrix.product_id, "prod_tv_samsung")
        self.assertEqual(matrix.best_overall_price, 250000.0)
        self.assertEqual(matrix.best_direct_price, 250000.0)
        self.assertEqual(matrix.best_marketplace_price, 275000.0)

        # Cross-channel opportunities:
        # s1 has direct vs WB savings
        # s2 has direct vs Ozon savings
        self.assertEqual(len(matrix.cross_channel_opportunities), 2)
        wb_savings = next(
            (s for s in matrix.cross_channel_opportunities if s.marketplace_channel_type == ChannelType.WILDBERRIES),
            None,
        )
        self.assertIsNotNone(wb_savings)
        self.assertEqual(wb_savings.seller_id, s1_id)
        self.assertEqual(wb_savings.savings_amount, 25000.0)
        self.assertEqual(wb_savings.savings_percent, 9.1)

        ozon_savings = next(
            (s for s in matrix.cross_channel_opportunities if s.marketplace_channel_type == ChannelType.OZON),
            None,
        )
        self.assertIsNotNone(ozon_savings)
        self.assertEqual(ozon_savings.seller_id, s2_id)
        self.assertEqual(ozon_savings.savings_amount, 40000.0)
        self.assertEqual(ozon_savings.savings_percent, 12.5)
