"""Unit tests for matching precision, accessory exclusion, and price ingestion guards."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import unittest

from model_matching import extract_canonical_key
from database import init_db, save_or_update_product


class MatchingPrecisionTest(unittest.TestCase):
    def test_accessories_do_not_get_phone_canonical_keys(self):
        # Cases, screen protectors, straps, cables must return None
        accessories = [
            "Чехол на iPhone 14 с подставкой пластик",
            "Чехол-накладка для iPhone 11 силикон Krutoff",
            "Защитное стекло для Apple iPhone 15 Pro",
            "Ремешок для смарт-часов Apple Watch 42mm",
            "Кабель UGREEN USB-C to Lightning 1m",
            "Чехол силиконовый для Samsung Galaxy S24 Ultra",
        ]
        for title in accessories:
            self.assertIsNone(extract_canonical_key(title), f"Accessory '{title}' should not have canonical key")

    def test_real_devices_preserve_canonical_keys(self):
        # Genuine devices must still match accurately
        phones = {
            "Смартфон Apple iPhone 14 128GB": "apple:iphone 14:128gb",
            "Смартфон Apple iPhone 15 Plus 256GB Yellow": "apple:iphone 15 plus:256gb",
            "Смартфон Apple iPhone 15 Pro Max 256GB White Titanium": "apple:iphone 15 pro max:256gb",
        }
        for title, expected_prefix in phones.items():
            key = extract_canonical_key(title)
            self.assertIsNotNone(key, f"Phone '{title}' must have canonical key")
            self.assertTrue(key.startswith(expected_prefix), f"Expected '{key}' to start with '{expected_prefix}'")

    def test_samsung_monitors_not_classified_as_galaxy(self):
        # Essential S3 monitor must not be classified as a Galaxy phone
        title = "Монитор 27\" Samsung Essential S3 LS27D364GAIXCII, Black"
        key = extract_canonical_key(title)
        if key:
            self.assertNotIn("galaxy", key.lower(), "Monitor should not be marked as galaxy")

    def test_save_or_update_product_price_validation(self):
        init_db()
        # Non-numeric or <= 0 prices must be safely rejected
        bad_products = [
            {"id": "bad_1", "title": "Test", "price": 0, "url": "https://example.com/1"},
            {"id": "bad_2", "title": "Test", "price": -500, "url": "https://example.com/2"},
            {"id": "bad_3", "title": "Test", "price": "not-a-number", "url": "https://example.com/3"},
            {"id": "bad_4", "title": "Test", "price": 15_000_000, "url": "https://example.com/4"},
        ]
        for p in bad_products:
            res = save_or_update_product(p)
            self.assertFalse(res["is_new"])
            self.assertEqual(res["current_price"], 0)


if __name__ == "__main__":
    unittest.main()
