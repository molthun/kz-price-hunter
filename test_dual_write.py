"""
Tests for Dual-Write v2 mechanism in database.py.
"""

import test_support  # must be first
import os
import unittest

import database
from repositories import OfferRepository, ProductRepository, SellerRepository


class TestDualWrite(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_single_product_dual_write(self):
        sample = {
            "id": "test_dual_1",
            "shop": "Французский Дом",
            "city": "Алматы",
            "title": "Парфюмерная вода Dior Sauvage 100ml",
            "category": "beauty_health",
            "url": "https://french-house.kz/p/sauvage",
            "image_url": "https://french-house.kz/img/sauvage.jpg",
            "description": "Классический селективный аромат",
            "price": 89000,
            "old_price_on_site": 99000,
        }
        res = database.save_or_update_product(sample)
        self.assertTrue(res["is_new"])

        # Verify legacy table
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM products WHERE id = 'test_dual_1'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["current_price"], 89000)

            # Verify v2 repositories
            seller_repo = SellerRepository(conn)
            seller = seller_repo.get_by_slug("french_house")
            self.assertIsNotNone(seller)
            self.assertEqual(seller.name, "Французский Дом")

            offer_repo = OfferRepository(conn)
            offer = offer_repo.get_by_id("off_test_dual_1")
            self.assertIsNotNone(offer)
            self.assertEqual(offer.price, 89000.0)
            self.assertEqual(offer.old_price, 99000.0)
            self.assertEqual(offer.city, "Алматы")

            # Verify price history
            history = offer_repo.get_price_history(offer.id)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].price, 89000.0)

            # Verify seller domain and digital footprint
            self.assertEqual(seller.domain, "french-house.kz")
            from repositories import SellerIdentityRepository
            from domain import IdentityType
            ident_repo = SellerIdentityRepository(conn)
            idents = ident_repo.get_identities_by_seller(seller.id)
            self.assertTrue(any(i.identity_type == IdentityType.DOMAIN and i.identity_value == "french-house.kz" for i in idents))

    def test_batch_products_dual_write(self):
        items = [
            {
                "id": "test_batch_1",
                "shop": "DNS Казахстан",
                "city": "Астана",
                "title": "Монитор ASUS TUF 27 165Hz",
                "category": "monitors",
                "url": "https://www.dns-shop.kz/p/1",
                "price": 149990,
            },
            {
                "id": "test_batch_2",
                "shop": "4mobile",
                "city": "Астана",
                "title": "Смартфон Apple iPhone 15 128GB Black",
                "category": "smartphones",
                "url": "https://4mobile.kz/p/2",
                "price": 389000,
            },
        ]
        count = database.save_or_update_products_batch(items)
        self.assertEqual(count, 2)

        with database.get_connection() as conn:
            offer_repo = OfferRepository(conn)
            off1 = offer_repo.get_by_id("off_test_batch_1")
            off2 = offer_repo.get_by_id("off_test_batch_2")
            self.assertIsNotNone(off1)
            self.assertIsNotNone(off2)
            self.assertEqual(off1.price, 149990.0)
            self.assertEqual(off2.price, 389000.0)

    def test_dual_write_disable_flag(self):
        os.environ["DUAL_WRITE_V2"] = "0"
        try:
            sample = {
                "id": "test_disabled_v2",
                "shop": "Мечта",
                "city": "Астана",
                "title": "Чайник электрический Tefal 1.7L",
                "category": "appliances",
                "url": "https://mechta.kz/p/tefal",
                "price": 24990,
            }
            database.save_or_update_product(sample)

            with database.get_connection() as conn:
                # Should exist in legacy products
                cur = conn.cursor()
                cur.execute("SELECT * FROM products WHERE id = 'test_disabled_v2'")
                self.assertIsNotNone(cur.fetchone())

                # Should NOT exist in offers
                offer_repo = OfferRepository(conn)
                self.assertIsNone(offer_repo.get_by_id("off_test_disabled_v2"))
        finally:
            os.environ.pop("DUAL_WRITE_V2", None)

    def test_dual_write_failure_telemetry_and_stats(self):
        from unittest.mock import patch
        database.reset_dual_write_stats()
        self.assertEqual(database.get_dual_write_stats()["failures"], 0)

        sample = {
            "id": "test_failure_obs",
            "shop": "Kaspi Магазин",
            "city": "Алматы",
            "title": "Тестовый товар со сбоем v2",
            "url": "https://kaspi.kz/p/test",
            "price": 10000,
        }

        # Simulate exception inside sync_legacy_product_to_v2
        with patch("domain.adapter.sync_legacy_product_to_v2", side_effect=RuntimeError("Simulated v2 error")):
            res = database.save_or_update_product(sample)
            # Legacy write must still succeed (Fail-Open)
            self.assertTrue(res["is_new"])

        stats = database.get_dual_write_stats()
        self.assertGreaterEqual(stats["total"], 1)
        self.assertEqual(stats["failures"], 1)
        self.assertIn("Simulated v2 error", stats["last_error"])

        # Also verify legacy product is in database
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM products WHERE id = 'test_failure_obs'")
            self.assertIsNotNone(cur.fetchone())

        # Событие телеметрии — не менее важная половина наблюдаемости, чем счётчики.
        # Вызов обёрнут в except Exception: pass, поэтому его поломка (переименованная константа,
        # изменённая сигнатура) отключила бы телеметрию молча. Телеметрия копит события в буфере
        # и сбрасывает их фоновым потоком раз в 10 с, поэтому здесь сбрасываем явно.
        from telemetry import telemetry
        telemetry.flush()
        with database.get_connection() as conn:
            row = conn.execute(
                """SELECT severity, component, message FROM telemetry_events
                   WHERE type = 'dual_write_error' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        self.assertIsNotNone(row, "сбой двойной записи обязан попасть в журнал событий")
        self.assertEqual(row["severity"], "WARNING")
        self.assertEqual(row["component"], "system")
        self.assertIn("test_failure_obs", row["message"])


    def test_second_domain_of_same_seller_does_not_break_dual_write(self):
        """Идентификатор отпечатка строится из его значения, а не только из продавца.

        Раньше он собирался как ident_<slug>_domain, и второй домен того же магазина конфликтовал
        по первичному ключу. Предложение ON CONFLICT покрывает тройку (тип, значение, продавец) и
        такой конфликт не подавляет, поэтому товар вообще не попадал в v2. Сегодня это недостижимо
        (ни у одного магазина ссылки не ведут на разные домены), но переезд магазина на новый домен
        терял бы каждый товар на новом хосте.
        """
        database.reset_dual_write_stats()
        base = {"shop": "Мечта", "shop_key": "mechta", "city": "Астана",
                "title": "Товар", "category": "Тест", "price": 1000}
        database.save_or_update_product({**base, "id": "dom_1", "url": "https://mechta.kz/p/1"})
        database.save_or_update_product({**base, "id": "dom_2", "url": "https://shop.mechta.kz/p/2"})

        self.assertEqual(database.get_dual_write_stats()["failures"], 0,
                         "второй домен не должен ронять двойную запись")
        with database.get_connection() as conn:
            offers = conn.execute(
                "SELECT COUNT(*) FROM offers WHERE id IN ('off_dom_1','off_dom_2')").fetchone()[0]
            domains = {r[0] for r in conn.execute(
                "SELECT identity_value FROM seller_identities WHERE seller_id = 'seller_mechta'")}
        self.assertEqual(offers, 2, "оба товара обязаны попасть в v2")
        self.assertEqual(domains, {"mechta.kz", "shop.mechta.kz"},
                         "оба домена сохраняются как отдельные отпечатки")


if __name__ == "__main__":
    unittest.main()
