from tests import test_support  # noqa: F401  isolates DATA_DIR
import datetime
import unittest

import database
import watches as w


class TestWatchesV2(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_seller_kind_normalization(self):
        """Проверка нормализации наблюдения за продавцом (SELLER)."""
        data = {
            "kind": "seller",
            "target": "technodom",
            "condition": "new_offer",
            "city": "Алматы",
        }
        clean = w.normalize(data)
        self.assertEqual(clean["kind"], "seller")
        self.assertEqual(clean["target"], "technodom")
        self.assertEqual(clean["condition"], "new_offer")
        self.assertEqual(clean["city"], "Алматы")

    def test_v2_conditions_and_filters_normalization(self):
        """Проверка нормализации новых триггеров и фильтров (item_conditions, sellers, channels)."""
        data = {
            "kind": "search",
            "target": "RTX 5090",
            "condition": "price_drop",
            "threshold": 15,
            "city": "Астана",
            "item_conditions": ["used", "refurbished"],
            "sellers": ["Technodom", "Sulpak"],
            "channels": ["web", "kaspi"],
        }
        clean = w.normalize(data)
        self.assertEqual(clean["condition"], "price_drop")
        self.assertEqual(clean["threshold"], 15)
        self.assertEqual(clean["item_conditions"], ["used", "refurbished"])
        self.assertEqual(clean["sellers"], ["sulpak", "technodom"])
        self.assertEqual(clean["channels"], ["kaspi", "web"])

        # describe форматирует все параметры
        desc = w.describe(clean)
        self.assertIn("поисковый запрос «RTX 5090»", desc)
        self.assertIn("снижение от 15 %", desc)
        self.assertIn("Астана", desc)
        self.assertIn("продавцы: sulpak, technodom", desc)
        self.assertIn("каналы: kaspi, web", desc)
        self.assertIn("состояние: б/у, refurbished", desc)

    def test_matches_with_item_conditions(self):
        """Фильтрация по состоянию товара (new, used, refurbished)."""
        watch_used = w.normalize({
            "kind": "product",
            "target": "p-100",
            "condition": "new_offer",
            "item_conditions": ["used"],
        })
        offer_new = {"id": "p-100", "current_price": 200000, "condition": "new"}
        offer_used = {"id": "p-100", "current_price": 160000, "condition": "used"}
        offer_refurbished = {"id": "p-100", "current_price": 170000, "condition": "refurbished"}

        self.assertFalse(w.matches(watch_used, offer_new))
        self.assertTrue(w.matches(watch_used, offer_used))
        self.assertFalse(w.matches(watch_used, offer_refurbished))

    def test_matches_with_sellers_and_channels(self):
        """Фильтрация по продавцам и каналам."""
        watch = w.normalize({
            "kind": "search",
            "target": "ноутбук",
            "condition": "new_offer",
            "sellers": ["seller-alpha"],
            "channels": ["telegram"],
        })

        offer_alpha_tg = {
            "id": "p-1",
            "title": "Игровой ноутбук ASUS",
            "seller_slug": "seller-alpha",
            "channel_type": "telegram",
            "current_price": 450000,
        }
        offer_alpha_web = {
            "id": "p-2",
            "title": "Игровой ноутбук ASUS",
            "seller_slug": "seller-alpha",
            "channel_type": "web",
            "current_price": 450000,
        }
        offer_beta_tg = {
            "id": "p-3",
            "title": "Игровой ноутбук ASUS",
            "seller_slug": "seller-beta",
            "channel_type": "telegram",
            "current_price": 450000,
        }

        self.assertTrue(w.matches(watch, offer_alpha_tg))
        self.assertFalse(w.matches(watch, offer_alpha_web))
        self.assertFalse(w.matches(watch, offer_beta_tg))

    def test_seller_kind_matches(self):
        """Проверка сопоставления предложения для наблюдения вида SELLER."""
        watch = w.normalize({
            "kind": "seller",
            "target": "gadget_store",
            "condition": "new_offer",
        })
        offer_matching = {
            "id": "p-1",
            "seller_slug": "gadget_store",
            "current_price": 120000,
        }
        offer_other = {
            "id": "p-2",
            "seller_slug": "mega_shop",
            "current_price": 120000,
        }
        self.assertTrue(w.matches(watch, offer_matching))
        self.assertFalse(w.matches(watch, offer_other))

    def test_condition_met_new_offer(self):
        """Триггер NEW_OFFER: срабатывает на новое предложение, молчит на повтор."""
        watch = w.normalize({
            "kind": "product",
            "target": "p-50",
            "condition": "new_offer",
        })
        offer = {"id": "p-50", "current_price": 150000, "shop": "Technodom"}

        # Новое предложение (state пустой)
        met, reason = w.condition_met(watch, offer, {})
        self.assertTrue(met)
        self.assertIn("новое предложение: 150000 ₸", reason)

        # Повтор предложения с тем же offer_id в known_offers
        state = {"known_offers": ["p-50"], "last_notified_price": 150000}
        met, _ = w.condition_met(watch, offer, state)
        self.assertFalse(met)

    def test_condition_met_new_seller(self):
        """Триггер NEW_SELLER: появление нового продавца для товара."""
        watch = w.normalize({
            "kind": "product",
            "target": "p-50",
            "condition": "new_seller",
        })
        offer_1 = {"id": "p-50", "current_price": 200000, "seller_name": "Kaspi Magazin", "seller_slug": "kaspi-seller"}
        offer_2 = {"id": "p-50", "current_price": 195000, "seller_name": "Alser", "seller_slug": "alser"}

        # Первый продавец
        state: dict = {"known_sellers": ["kaspi-seller"]}
        met, reason = w.condition_met(watch, offer_1, state)
        self.assertFalse(met)  # уже известен

        # Второй продавец
        met, reason = w.condition_met(watch, offer_2, state)
        self.assertTrue(met)
        self.assertIn("новый продавец «Alser»", reason)

    def test_condition_met_new_used_offer(self):
        """Триггер NEW_USED_OFFER: появление б/у предложения."""
        watch = w.normalize({
            "kind": "product",
            "target": "p-77",
            "condition": "new_used_offer",
        })
        offer_new = {"id": "p-77", "current_price": 300000, "condition": "new", "shop": "Sulpak"}
        offer_used = {"id": "p-77", "current_price": 210000, "condition": "used", "shop": "OLX Seller"}

        met, _ = w.condition_met(watch, offer_new, {})
        self.assertFalse(met)  # новый товар не триггерит new_used_offer

        met, reason = w.condition_met(watch, offer_used, {})
        self.assertTrue(met)
        self.assertIn("появилось б/у предложение: 210000 ₸", reason)

    def test_condition_met_better_channel_and_seller(self):
        """Триггеры BETTER_CHANNEL и BETTER_SELLER."""
        watch_ch = w.normalize({
            "kind": "product",
            "target": "p-88",
            "condition": "better_channel",
        })
        watch_seller = w.normalize({
            "kind": "product",
            "target": "p-88",
            "condition": "better_seller",
        })

        state = {
            "min_price": 250000,
            "known_channels": ["web"],
            "known_sellers": ["shop-a"],
        }
        # Дешевле через Telegram
        offer_tg = {
            "id": "p-88",
            "current_price": 230000,
            "channel_type": "telegram",
            "seller_name": "Shop B",
            "seller_slug": "shop-b",
        }

        met_ch, reason_ch = w.condition_met(watch_ch, offer_tg, state)
        self.assertTrue(met_ch)
        self.assertIn("более выгодный канал (telegram): 230000 ₸", reason_ch)

        met_seller, reason_seller = w.condition_met(watch_seller, offer_tg, state)
        self.assertTrue(met_seller)
        self.assertIn("лучшая цена у продавца «Shop B»: 230000 ₸", reason_seller)

    def test_database_crud_with_v2_fields(self):
        """Сохранение и извлечение наблюдения с V2 полями в базе данных."""
        user_id = 99991
        data = {
            "kind": "search",
            "target": "PlayStation 5",
            "condition": "price_drop",
            "threshold": 10,
            "city": "Алматы",
            "item_conditions": ["used"],
            "sellers": ["mechta", "technodom"],
            "channels": ["web", "kaspi"],
            "mode": "instant",
        }
        created = database.create_watch(user_id, data)
        self.assertEqual(created["kind"], "search")
        self.assertEqual(created["target"], "PlayStation 5")
        self.assertEqual(created["item_conditions"], ["used"])
        self.assertEqual(created["sellers"], ["mechta", "technodom"])
        self.assertEqual(created["channels"], ["kaspi", "web"])

        # Обновление
        updated = database.update_watch(user_id, created["id"], {
            "item_conditions": ["new", "used"],
            "sellers": ["mechta"],
        })
        self.assertIsNotNone(updated)
        self.assertEqual(updated["item_conditions"], ["new", "used"])
        self.assertEqual(updated["sellers"], ["mechta"])

        # Проверка через list_watches
        watches = database.list_watches(user_id)
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0]["item_conditions"], ["new", "used"])

    def test_evaluate_watches_with_v2_tracking(self):
        """Интеграционная проверка evaluate_watches с отслеживанием известных продавцов и каналов."""
        user_id = 99992
        watch = database.create_watch(user_id, {
            "kind": "product",
            "target": "item-999",
            "condition": "new_seller",
            "repeat": True,
        })

        offer_1 = {
            "id": "item-999",
            "title": "Наушники Sony",
            "current_price": 100000,
            "seller_slug": "seller-one",
            "seller_name": "Seller One",
            "channel_type": "web",
            "city": "Астана",
        }

        # Первое появление продавца One -> триггер!
        queued = database.evaluate_watches([offer_1])
        self.assertEqual(queued, 1)

        # Повторное появление продавца One -> не триггерит!
        queued_repeat = database.evaluate_watches([offer_1])
        self.assertEqual(queued_repeat, 0)

        # Появление нового продавца Two -> триггер!
        offer_2 = {
            "id": "item-999",
            "title": "Наушники Sony",
            "current_price": 98000,
            "seller_slug": "seller-two",
            "seller_name": "Seller Two",
            "channel_type": "kaspi",
            "city": "Астана",
        }
        queued_two = database.evaluate_watches([offer_2])
        self.assertEqual(queued_two, 1)


if __name__ == "__main__":
    unittest.main()
