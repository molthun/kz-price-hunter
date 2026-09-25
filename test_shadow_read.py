"""
Tests for Shadow Read verification: comparing v1 and v2 retrieval results.
"""

import test_support  # must be first
import unittest

import database
from domain.models import Condition
from repositories import OfferRepository, ProductRepository


class TestShadowReadVerification(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_shadow_read_price_parity(self):
        # Insert test products via database dual-write
        p1 = {
            "id": "shadow_iphone_1",
            "shop": "4mobile",
            "city": "Астана",
            "title": "Apple iPhone 16 128GB Black",
            "category": "smartphones",
            "url": "https://4mobile.kz/p/iphone16",
            "price": 450000,
        }
        p2 = {
            "id": "shadow_iphone_2",
            "shop": "Мечта",
            "city": "Астана",
            "title": "Apple iPhone 16 128GB Black",
            "category": "smartphones",
            "url": "https://mechta.kz/p/iphone16",
            "price": 465000,
        }
        database.save_or_update_product(p1)
        database.save_or_update_product(p2)

        with database.get_connection() as conn:
            # 1. Query via legacy SQL
            cur = conn.cursor()
            cur.execute(
                """
                SELECT MIN(current_price) FROM products
                WHERE title = 'Apple iPhone 16 128GB Black' AND is_active = 1
                """
            )
            v1_best_price = cur.fetchone()[0]

            # 2. Query via v2 OfferRepository
            prod_repo = ProductRepository(conn)
            offer_repo = OfferRepository(conn)

            # Find canonical product
            prod = prod_repo.get_by_canonical_key("apple:iphone 16:128gb")
            self.assertIsNotNone(prod)

            v2_offers = offer_repo.get_active_offers_for_product(
                product_id=prod.id, condition=Condition.NEW
            )
            self.assertTrue(len(v2_offers) >= 2)
            v2_best_price = v2_offers[0].price

            # Verification: Parity between v1 and v2
            self.assertEqual(float(v1_best_price), float(v2_best_price))
            self.assertEqual(v2_best_price, 450000.0)
            self.assertEqual(v2_offers[0].seller_id, "seller_fourmobile")


if __name__ == "__main__":
    unittest.main()
