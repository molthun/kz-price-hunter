from tests import test_support  # noqa: F401
import json
import pathlib
import unittest
from types import SimpleNamespace

import database
import local_search
from domain.sizes import (
    extract_size_from_query,
    normalize_available_sizes,
    normalize_size_token,
    sort_size_key,
)
from scrapers.intertop import IntertopScraper
from scrapers.kari import KariScraper


class SizesNormalizationTest(unittest.TestCase):
    def test_normalize_tokens_and_sorting(self):
        self.assertEqual(normalize_size_token("EU 42"), "42")
        self.assertEqual(normalize_size_token("37,5"), "37.5")
        self.assertEqual(normalize_size_token("38.0"), "38")
        self.assertEqual(normalize_size_token("2xl"), "XXL")
        self.assertIsNone(normalize_size_token("256GB"))
        self.assertIsNone(normalize_size_token("99"))

        sizes = normalize_available_sizes([{"size": "41"}, {"size": "36"}, {"size": "37,5"}, {"size": "36"}, "XL", "S"])
        self.assertEqual(sizes, ["36", "37.5", "41", "S", "XL"])
        self.assertLess(sort_size_key("36"), sort_size_key("37.5"))
        self.assertLess(sort_size_key("42"), sort_size_key("S"))

    def test_extract_size_from_query_respects_context(self):
        self.assertEqual(extract_size_from_query("кроссовки мужские 42 размер"), ("42", "кроссовки мужские"))
        self.assertEqual(extract_size_from_query("сапоги женские 38.5"), ("38.5", "сапоги женские"))
        self.assertEqual(extract_size_from_query("куртка зимняя 2XL"), ("XXL", "куртка зимняя"))
        self.assertEqual(extract_size_from_query("размер 39 туфли"), ("39", "туфли"))
        # Техника не должна ошибочно считаться размером обуви
        self.assertEqual(extract_size_from_query("телевизор LG 42 дюйма"), (None, "телевизор LG 42 дюйма"))
        self.assertEqual(extract_size_from_query("Apple Watch 44 мм"), (None, "Apple Watch 44 мм"))
        self.assertEqual(extract_size_from_query("SSD 256 ГБ"), (None, "SSD 256 ГБ"))


class ShoeScrapersSizesTest(unittest.TestCase):
    def test_kari_extracts_available_sizes_from_next_data(self):
        scraper = KariScraper()
        ent = {
            "articul": "W123456",
            "title": "Кроссовки женские",
            "brand": {"name": "T.Taccardi"},
            "price": {"current": 19990, "first": 24990},
            "preview": "https://kz.kari.com/img/w123.jpg",
            "sizes": [{"size": "38"}, {"size": "36"}, {"size": "37"}],
        }
        item = scraper._item(ent, "Kari: 👠 Женская обувь")
        self.assertIsNotNone(item)
        self.assertEqual(item["available_sizes"], ["36", "37", "38"])

    def test_intertop_extracts_available_sizes_from_tile_options(self):
        html = """
        <html><body>
          <div class="in-product-tile" data-product-id="8514154" data-product-sku="115-9100">
            <a href="/ru-kz/product/8514154">link</a>
            <div class="in-product-tile__product-brand">Tamaris</div>
            <div class="in-product-tile__product-name">Ботинки зимние</div>
            <div class="in-price__actual">34 990 ₸</div>
            <img src="https://intertop.kz/img/1.jpg" />
            <div class="in-product-tile__options">
              <span>39</span><span>37</span><span>40</span>
            </div>
          </div>
        </body></html>
        """
        scraper = IntertopScraper()
        scraper.session = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(status_code=200, text=html))
        res = scraper._fetch_page("Интертоп: Женская обувь", "https://intertop.kz/ru-kz/catalog/zhenskaya_obuv/", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["available_sizes"], ["37", "39", "40"])


class SizesSearchIntegrationTest(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_sync_index_facet_search_and_sold_out_update(self):
        pair_small = {
            "id": "kari_size_test_small",
            "sku": "W100",
            "shop": "Kari",
            "title": "Кроссовки T.Taccardi Run Alpha Белые",
            "category": "Kari: 👠 Женская обувь",
            "url": "https://kz.kari.com/product/W100/",
            "image_url": "https://kz.kari.com/img/w100.jpg",
            "price": 18990,
            "old_price_on_site": 22990,
            "city": "Алматы / Казахстан",
            "available_sizes": ["36", "37", "38"],
        }
        pair_large = {
            "id": "kari_size_test_large",
            "sku": "M200",
            "shop": "Kari",
            "title": "Кроссовки Pierre Cardin Run Beta Черные",
            "category": "Kari: 👞 Мужская обувь",
            "url": "https://kz.kari.com/product/M200/",
            "image_url": "https://kz.kari.com/img/m200.jpg",
            "price": 25990,
            "old_price_on_site": 0,
            "city": "Алматы / Казахстан",
            "available_sizes": ["41", "42", "43"],
        }
        database.save_or_update_products_batch([pair_small, pair_large])

        # 1. Поиск «кроссовки 42 размер» находит только вторую пару и отдаёт фасет «Размер»
        res42 = local_search.search_sync(
            {"query": "кроссовки 42 размер", "limit": 20, "offset": 0},
            {"plan": None},
            {},
        )
        titles42 = [it["product"]["title"] for it in res42["items"]]
        self.assertTrue(any("Run Beta" in t for t in titles42), titles42)
        self.assertFalse(any("Run Alpha" in t for t in titles42), titles42)

        # Доступные размеры пробрасываются в AggregatedOffer
        beta_item = next(it for it in res42["items"] if "Run Beta" in it["product"]["title"])
        self.assertEqual(beta_item["offers"][0]["available_sizes"], ["41", "42", "43"])

        # Фасет «Размер» присутствует и отсортирован по возрастанию
        size_facet = next((f for f in res42["attribute_facets"] if f["key"] == "size"), None)
        self.assertIsNotNone(size_facet)
        self.assertEqual(size_facet["label"], "Размер")
        facet_vals = [v["value"] for v in size_facet["values"]]
        self.assertIn("42", facet_vals)
        self.assertIn("36", facet_vals)
        self.assertEqual(facet_vals, sorted(facet_vals, key=sort_size_key))

        # 2. Обновление: 42-й размер раскупили -> поиск по 42 больше не находит эту пару
        database.save_or_update_products_batch([{**pair_large, "available_sizes": ["41", "43"]}])
        res42_after = local_search.search_sync(
            {"query": "кроссовки 42 размер", "limit": 20, "offset": 0},
            {"plan": None},
            {},
        )
        titles_after = [it["product"]["title"] for it in res42_after["items"]]
        self.assertFalse(any("Run Beta" in t for t in titles_after), titles_after)

    def test_search_first_template_renders_available_sizes(self):
        html = pathlib.Path("web/templates/search_first.html").read_text(encoding="utf-8")
        self.assertIn("off.available_sizes", html)
        self.assertIn("offer-sizes-row", html)
        self.assertIn("offer-size-chip", html)


class UserSizesSettingsSyncTest(unittest.TestCase):
    def test_validate_user_settings_sizes(self):
        from config import validate_user_settings
        clean = validate_user_settings({"preferred_shoe_size": "37,5", "preferred_clothing_size": "2xl"})
        self.assertEqual(clean["preferred_shoe_size"], "37.5")
        self.assertEqual(clean["preferred_clothing_size"], "XXL")

        cleared = validate_user_settings({"preferred_shoe_size": "", "preferred_clothing_size": ""})
        self.assertEqual(cleared["preferred_shoe_size"], "")
        self.assertEqual(cleared["preferred_clothing_size"], "")

        with self.assertRaises(ValueError):
            validate_user_settings({"preferred_shoe_size": "XL"})
        with self.assertRaises(ValueError):
            validate_user_settings({"preferred_clothing_size": "42"})
        with self.assertRaises(ValueError):
            validate_user_settings({"preferred_shoe_size": "999"})

    def test_http_me_settings_saves_and_isolates_user_sizes(self):
        import asyncio
        from unittest.mock import patch
        from aiohttp.test_utils import TestServer
        import auth
        import web.server as server
        from tests.test_support import BrowserTestClient

        database.init_db()
        uid1, uid2 = 8601, 8602
        database.upsert_telegram_user({"id": uid1, "first_name": "UserOne"})
        database.upsert_telegram_user({"id": uid2, "first_name": "UserTwo"})
        tok1 = database.create_session(uid1)
        tok2 = database.create_session(uid2)

        async def _scenario():
            app = server.create_app()
            app.cleanup_ctx.clear()
            with patch.object(auth, "PUBLIC_ORIGIN", ""):
                async with BrowserTestClient(TestServer(app)) as client:
                    # 1. Гость получает 401
                    r_guest = await client.post("/api/me/settings", json={"preferred_shoe_size": "42"})
                    self.assertEqual(r_guest.status, 401)

                    # 2. UserOne сохраняет размеры
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tok1})
                    r_bad = await client.post("/api/me/settings", json={"preferred_shoe_size": "INVALID"})
                    self.assertEqual(r_bad.status, 400)

                    r_save = await client.post(
                        "/api/me/settings",
                        json={"preferred_shoe_size": "42,5", "preferred_clothing_size": "2xl"},
                    )
                    self.assertEqual(r_save.status, 200)
                    saved = (await r_save.json())["settings"]
                    self.assertEqual(saved["preferred_shoe_size"], "42.5")
                    self.assertEqual(saved["preferred_clothing_size"], "XXL")

                    r_me1 = await client.get("/api/me")
                    me1 = await r_me1.json()
                    self.assertEqual(me1["settings"]["preferred_shoe_size"], "42.5")
                    self.assertEqual(me1["settings"]["preferred_clothing_size"], "XXL")

                    # 3. UserTwo не видит размеры UserOne
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tok2})
                    r_me2 = await client.get("/api/me")
                    me2 = await r_me2.json()
                    self.assertEqual(me2["settings"]["preferred_shoe_size"], "")
                    self.assertEqual(me2["settings"]["preferred_clothing_size"], "")

        asyncio.run(_scenario())


if __name__ == "__main__":
    unittest.main()


class SizeIsOfferConditionTest(unittest.TestCase):
    """SZ-01 (Codex 22.11): размер и город/продавец/цена — условия одного и того же предложения."""

    BASE = {"sku": "RUNSIZE901", "title": "Кроссовки T.Taccardi Run Offer Белые", "category": "Обувь",
            "image_url": "https://example.invalid/image.jpg", "old_price_on_site": 0}

    @classmethod
    def setUpClass(cls):
        database.init_db()
        a = dict(cls.BASE, id="kari_sz01_a", shop="Kari", price=10000, city="Астана",
                 url="https://example.invalid/sz01/a", available_sizes=["36"])
        b = dict(cls.BASE, id="intertop_sz01_b", shop="Интертоп", price=20000, city="Алматы",
                 url="https://example.invalid/sz01/b", available_sizes=["42"])
        c = dict(cls.BASE, id="kari_sz01_c", shop="Kari", price=15000, city="Алматы",
                 url="https://example.invalid/sz01/c", available_sizes=["42"])
        database.save_or_update_products_batch([a, b, c])
        with database.get_connection() as conn:
            rows = {r["url"]: dict(r) for r in conn.execute(
                "SELECT id, product_id, seller_id, url FROM offers WHERE url LIKE 'https://example.invalid/sz01/%'")}
            # Третье предложение (42, Kari, 15 000) снято с продажи: его размер не должен засчитываться
            conn.execute("UPDATE offers SET is_active = 0 WHERE id = ?", (rows["https://example.invalid/sz01/c"]["id"],))
            conn.commit()
        cls.rows = rows

    def _search(self, size="42", **kw):
        from repositories.search_repo import SearchRepository
        with database.get_connection() as conn:
            repo = SearchRepository(conn)
            repo.attribute_filters = {"size": size} if size else {}
            items = repo.search_products("кроссовки", **kw)
            total = repo.count_search_products("кроссовки", **kw)["total_products"]
        return ([(o["city"], o["price"]) for it in items for o in it["offers"]], total)

    def test_one_canonical_product_for_both_sellers(self):
        pids = {r["product_id"] for r in self.rows.values()}
        self.assertEqual(len(pids), 1, "сценарий Codex: одна модель — один товар у разных продавцов")

    def test_negative_scenarios_give_nothing(self):
        kari = self.rows["https://example.invalid/sz01/a"]["seller_id"]
        for name, kw in (("город Астана", {"city": "Астана"}),
                         ("продавец Kari", {"seller_id": kari}),
                         ("цена до 15 000", {"max_price": 15000})):
            with self.subTest(name):
                self.assertEqual(self._search(**kw), ([], 0))

    def test_unscoped_size_shows_only_matching_offer_and_price(self):
        self.assertEqual(self._search(), ([("Алматы", 20000.0)], 1))
        offers, _ = self._search(size=None)
        self.assertIn(("Астана", 10000.0), offers, "без размера дешёвое предложение остаётся в выдаче")

    def test_local_search_astana_size42_is_empty(self):
        r = local_search.search_sync({"query": "кроссовки Taccardi 42 размер", "city": "Астана", "limit": 20, "offset": 0},
                                     {"plan": None}, {})
        self.assertEqual(r.get("selected_attributes", {}).get("size"), "42")
        self.assertEqual([it for it in r["items"] if "Run Offer" in it["product"]["title"]], [])

    def test_size_facet_counts_only_offers_passing_city(self):
        from repositories.search_repo import SearchRepository
        with database.get_connection() as conn:
            repo = SearchRepository(conn)
            repo.attribute_filters = {}
            facets = repo.attribute_facets({"query": "кроссовки", "city": "Астана"}, {})
        sizes = {v["value"] for f in facets if f["key"] == "size" for v in f["values"]}
        self.assertIn("36", sizes)
        self.assertNotIn("42", sizes, "42 есть только в Алматы и у снятого предложения")

    def test_watch_matches_size_on_the_same_offer(self):
        pid = self.rows["https://example.invalid/sz01/a"]["product_id"]
        target = local_search.WATCH_PREFIX + json.dumps({"query": "кроссовки", "attributes": {"size": "42"}},
                                                        ensure_ascii=False)
        self.assertFalse(local_search.matches_watch(target, {"canonical_product_id": pid, "price": 10000,
                                                             "available_sizes": ["36"]}))
        self.assertFalse(local_search.matches_watch(target, {"canonical_product_id": pid, "price": 10000}))
        self.assertTrue(local_search.matches_watch(target, {"canonical_product_id": pid, "price": 20000,
                                                            "available_sizes": ["41", "42"]}))


class SizeStringParsingTest(unittest.TestCase):
    """SZ-02 и уточнения парсера запроса."""

    def test_decimal_comma_inside_list(self):
        for raw in ("37,5;38", "37.5;38", ["37,5", "38"], [{"size": "37,5"}, {"size": "38"}], "38/37,5"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_available_sizes(raw), ["37.5", "38"])
        self.assertEqual(normalize_available_sizes("37,5"), ["37.5"])
        self.assertEqual(normalize_available_sizes("36,38,40"), ["36", "38", "40"])
        self.assertEqual(normalize_available_sizes("36, 37,5, 38"), ["36", "37.5", "38"])

    def test_letters_in_clothing_context_and_tv_guard(self):
        self.assertEqual(extract_size_from_query("футболка M"), ("M", "футболка"))
        self.assertEqual(extract_size_from_query("платье S"), ("S", "платье"))
        self.assertEqual(extract_size_from_query("телевизор размер 42 дюйма"), (None, "телевизор размер 42 дюйма"))
        self.assertEqual(extract_size_from_query("телевизор размер42 дюйма"), (None, "телевизор размер42 дюйма"))
        self.assertEqual(extract_size_from_query("macbook m3"), (None, "macbook m3"))


class SizeWatchRealPathTest(unittest.TestCase):
    """SZ-03 (Codex 22.11): размер доходит до наблюдений через штатный watched_offers → evaluate_watches."""

    USER = 864201
    BASE = {"sku": "RUNSIZE903", "title": "Кроссовки T.Taccardi Run Watch Белые", "category": "Обувь",
            "image_url": "https://example.invalid/image.jpg", "old_price_on_site": 0}

    def setUp(self):
        database.init_db()
        database.upsert_telegram_user({"id": self.USER, "first_name": "SizeWatch"})
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watches WHERE user_id = ?", (self.USER,))
            conn.commit()
        self.kari = dict(self.BASE, id="kari_sz03_a", shop="Kari", price=10000, city="Алматы",
                         url="https://example.invalid/sz03/a", available_sizes=["36"])
        self.inter = dict(self.BASE, id="intertop_sz03_b", shop="Интертоп", price=20000, city="Алматы",
                          url="https://example.invalid/sz03/b", available_sizes=["42"])
        database.save_or_update_products_batch([self.kari, self.inter])
        target = local_search.WATCH_PREFIX + json.dumps(
            {"query": "кроссовки", "attributes": {"size": "42"}, "original_query": "кроссовки 42 размер"},
            ensure_ascii=False)
        database.create_watch(self.USER, {"kind": "search", "target": target, "condition": "target_price",
                                          "threshold": 25000, "quiet_from": "", "quiet_to": "",
                                          "cooldown_hours": 0})

    def events(self):
        return [e for e in database.watch_events(self.USER) if "Run Watch" in (e.get("title") or e.get("target") or "")
                or e["product_id"] in (self.kari["id"], self.inter["id"])]

    def test_watched_offers_carries_sizes_of_the_same_offer(self):
        rows = {r["id"]: r for r in database.watched_offers([self.kari["id"], self.inter["id"]])}
        self.assertEqual(rows[self.inter["id"]]["available_sizes"], ["42"])
        self.assertEqual(rows[self.kari["id"]]["available_sizes"], ["36"])

    def test_size_watch_fires_once_on_matching_offer_only(self):
        ids = [self.kari["id"], self.inter["id"]]
        database.evaluate_watches(database.watched_offers(ids))
        fired = self.events()
        self.assertEqual([e["product_id"] for e in fired], [self.inter["id"]],
                         "42 есть только у Интертопа; дешёвое предложение Kari с 36 не срабатывает")
        database.evaluate_watches(database.watched_offers(ids))
        self.assertEqual(len(self.events()), 1, "повтор не дублирует событие")

    def test_sold_out_size_stops_matching(self):
        database.save_or_update_products_batch([dict(self.inter, price=19000, available_sizes=["43"])])
        rows = {r["id"]: r for r in database.watched_offers([self.inter["id"]])}
        self.assertEqual(rows[self.inter["id"]]["available_sizes"], ["43"])
        database.evaluate_watches(database.watched_offers([self.kari["id"], self.inter["id"]]))
        self.assertEqual(self.events(), [], "42 раскуплен — ложного события нет")
