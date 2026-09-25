"""
Tests for Domain Models in KZ Price Hunter 2.0.
"""

import test_support  # must be first
import unittest

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


class TestDomainModels(unittest.TestCase):
    def test_condition_normalization(self):
        # S2-E04 (B2): Default without context is UNKNOWN (absence of data != in order)
        self.assertEqual(Condition.normalize(None), Condition.UNKNOWN)
        self.assertEqual(Condition.normalize(""), Condition.UNKNOWN)
        # Contextual default for trusted retailers
        self.assertEqual(Condition.normalize(None, default=Condition.NEW), Condition.NEW)

        self.assertEqual(Condition.normalize("NEW"), Condition.NEW)
        self.assertEqual(Condition.normalize("новый"), Condition.NEW)
        self.assertEqual(Condition.normalize("Новое"), Condition.NEW)
        self.assertEqual(Condition.normalize("запечатанный"), Condition.NEW)

        # Used market
        self.assertEqual(Condition.normalize("б/у в отличном состоянии"), Condition.USED)
        self.assertEqual(Condition.normalize("БУ"), Condition.USED)
        self.assertEqual(Condition.normalize("used"), Condition.USED)
        self.assertEqual(Condition.normalize("бывший в употреблении"), Condition.USED)
        self.assertEqual(Condition.normalize("с пробегом"), Condition.USED)

        # B1 check: ensure "бумага", "тумбу", "обувь" are not treated as USED
        self.assertNotEqual(Condition.normalize("бумага офисная"), Condition.USED)
        self.assertNotEqual(Condition.normalize("тумбу под тв"), Condition.USED)
        self.assertNotEqual(Condition.normalize("обувь мужская"), Condition.USED)

        # Refurbished
        self.assertEqual(Condition.normalize("Refurbished"), Condition.REFURBISHED)
        self.assertEqual(Condition.normalize("восстановленный"), Condition.REFURBISHED)
        self.assertEqual(Condition.normalize("после ремонта"), Condition.REFURBISHED)

        # Open Box & уценка
        self.assertEqual(Condition.normalize("open box"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("уценка"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("витринный образец"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("повреждена упаковка"), Condition.OPEN_BOX)

    def test_availability_normalization(self):
        # S2-E04 (B2): Default without context is UNKNOWN
        self.assertEqual(Availability.normalize(None), Availability.UNKNOWN)
        self.assertEqual(Availability.normalize(""), Availability.UNKNOWN)
        # Contextual default for trusted retailers
        self.assertEqual(Availability.normalize(None, default=Availability.IN_STOCK), Availability.IN_STOCK)

        self.assertEqual(Availability.normalize("in_stock"), Availability.IN_STOCK)
        self.assertEqual(Availability.normalize("В наличии"), Availability.IN_STOCK)
        self.assertEqual(Availability.normalize("instock"), Availability.IN_STOCK)
        self.assertEqual(Availability.normalize("out_of_stock"), Availability.OUT_OF_STOCK)
        self.assertEqual(Availability.normalize("нет в наличии"), Availability.OUT_OF_STOCK)
        self.assertEqual(Availability.normalize("предзаказ"), Availability.PREORDER)
        # B2 check: "нет данных"
        self.assertEqual(Availability.normalize("нет данных"), Availability.UNKNOWN)
        self.assertEqual(Availability.normalize("неизвестно"), Availability.UNKNOWN)


    def test_seller_serialization(self):
        seller = Seller(
            id="seller_4mobile",
            slug="4mobile",
            name="4mobile",
            legal_name="ТОО 4Mobile",
            bin="123456789010",
            domain="4mobile.kz",
            phone="+77001234567",
            rating=4.9,
            metadata={"city": "Астана", "verified": True},
        )
        data = seller.to_dict()
        self.assertEqual(data["id"], "seller_4mobile")
        self.assertEqual(data["slug"], "4mobile")
        self.assertEqual(data["metadata"]["city"], "Астана")

        # Roundtrip from row
        row = {
            "id": seller.id,
            "slug": seller.slug,
            "name": seller.name,
            "legal_name": seller.legal_name,
            "bin": seller.bin,
            "domain": seller.domain,
            "phone": seller.phone,
            "rating": seller.rating,
            "is_active": 1,
            "metadata_json": '{"city": "Астана", "verified": true}',
            "created_at": seller.created_at,
            "updated_at": seller.updated_at,
        }
        loaded = Seller.from_row(row)
        self.assertEqual(loaded.slug, "4mobile")
        self.assertEqual(loaded.metadata["verified"], True)

    def test_canonical_product_and_offer_serialization(self):
        prod = CanonicalProduct(
            id="prod_test123",
            canonical_key="apple:iphone:16:pro:256gb",
            title="Apple iPhone 16 Pro 256GB",
            category="smartphones",
            brand="Apple",
            model="iPhone 16 Pro",
            attributes={"ram": "8GB", "storage": "256GB"},
        )
        self.assertEqual(prod.attributes["storage"], "256GB")

        offer = Offer(
            id="off_kaspi_12345",
            product_id=prod.id,
            seller_id="seller_4mobile",
            channel_id="chan_4mobile_kaspi",
            external_sku="SKU-12345",
            url="https://kaspi.kz/shop/p/12345",
            price=539000.0,
            old_price=579000.0,
            condition=Condition.NEW,
            city="Астана",
            payment_methods=["installment", "card"],
            installment_months=24,
        )
        self.assertEqual(offer.price, 539000.0)
        self.assertEqual(offer.installment_months, 24)
        self.assertIn("installment", offer.payment_methods)

        history = OfferPriceHistory(
            offer_id=offer.id,
            price=offer.price,
            old_price=offer.old_price,
            observed_at=offer.observed_at,
        )
        self.assertEqual(history.price, 539000.0)

    def test_all_shop_keys_seller_resolution(self):
        from config import SHOP_KEYS
        from domain.adapter import resolve_seller_and_channel_meta

        self.assertEqual(len(SHOP_KEYS), 37)
        slugs_from_name = set()
        slugs_from_key = set()

        for shop_key, display_name in SHOP_KEYS.items():
            # 1. Resolve by display_name
            slug1, name1, ctype1 = resolve_seller_and_channel_meta(display_name)
            self.assertTrue(slug1, f"Empty slug for display_name: {display_name}")
            self.assertNotEqual(slug1, "unknown_seller", f"Resolved to unknown_seller: {display_name}")
            self.assertTrue(name1, f"Empty name for display_name: {display_name}")
            self.assertTrue(ctype1, f"Empty channel_type for display_name: {display_name}")
            slugs_from_name.add(slug1)

            # 2. Resolve by shop_key
            slug2, name2, ctype2 = resolve_seller_and_channel_meta("", shop_key=shop_key)
            self.assertEqual(slug2, shop_key)
            slugs_from_key.add(slug2)

            # Both should match or resolve to stable slug
            self.assertEqual(slug1, slug2, f"Slug mismatch between name ({slug1}) and key ({slug2}) for {display_name}")

        # Ensure all 37 shops have completely unique slugs (no collisions)
        self.assertEqual(len(slugs_from_name), 37, "Duplicate slugs found among 37 shops!")
        self.assertEqual(len(slugs_from_key), 37)

        # 3. Fallback for completely unknown shop
        fallback_slug, fallback_name, fallback_ctype = resolve_seller_and_channel_meta("Новый Неизвестный Магазин 2026")
        self.assertTrue(fallback_slug)
        self.assertNotEqual(fallback_slug, "unknown_seller")


    def test_foreign_names_do_not_stick_to_known_chains(self):
        """Посторонняя организация не должна приклеиваться к сети по совпадению букв.

        Раньше известные написания искались вхождением подстроки, и получалось:
        «Форте Банк» -> fortemarket, «Мир Каспия» и «Каспийский Берег» -> kaspi,
        «Технодом Партнёр» -> technodom. Этап 2 плана это запрещает прямо: нельзя объединять
        продавцов только потому, что названия похожи. Если это действительно та же сеть,
        её сведёт SellerMatcher по сильным ключам, а не догадка по буквам.
        """
        from domain.adapter import resolve_seller_and_channel_meta

        for name, wrong_slug in (("Форте Банк", "fortemarket"),
                                 ("Мир Каспия", "kaspi"),
                                 ("Каспийский Берег", "kaspi"),
                                 ("Alser Plus", "alser"),
                                 ("Технодом Партнёр", "technodom")):
            slug, _, _ = resolve_seller_and_channel_meta(name)
            self.assertNotEqual(slug, wrong_slug, f"«{name}» не должен считаться сетью {wrong_slug}")
            self.assertTrue(slug, f"«{name}» обязан получить собственный идентификатор")


if __name__ == "__main__":
    unittest.main()
