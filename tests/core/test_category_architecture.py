"""
Tests for Category Architecture 2.0 (KZ Price Hunter 2.0, Phase 17).
Verifies:
1. Category domain model and MASTER_TAXONOMY (L1 root and L2 subcategories).
2. TaxonomyMapper (normalization, alias mapping, subtree resolution, breadcrumbs, tree building).
3. CategoryRepository (CRUD, automatic seeding in init_schema_v2, product counts).
4. Category filtering in SearchRepository and SearchCore with multi-merchant aggregation.
5. SearchResponse2 category_facets calculation.
6. HTTP API endpoints: GET /api/v2/categories, GET /api/v2/categories/{id_or_slug}, GET /api/categories.
"""

from tests import test_support  # must be first
import datetime
import json
import sqlite3
import unittest
from aiohttp.test_utils import make_mocked_request

NOW_ISO = datetime.datetime.now(datetime.timezone.utc).isoformat()

from domain.category import Category, TaxonomyMapper, MASTER_TAXONOMY
from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.search_core import SearchCore, SearchOutcome
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)
from repositories.category_repo import CategoryRepository
from web.server import categories_handler, categories_v2_handler, get_category_v2_handler, search_v2_handler


class TestCategoryDomainAndMapper(unittest.TestCase):
    def setUp(self):
        self.mapper = TaxonomyMapper(MASTER_TAXONOMY)

    def test_master_taxonomy_integrity(self):
        roots = [c for c in MASTER_TAXONOMY if c.parent_id is None]
        children = [c for c in MASTER_TAXONOMY if c.parent_id is not None]
        self.assertGreaterEqual(len(roots), 10, "Должно быть не менее 10 L1 корневых категорий")
        self.assertGreaterEqual(len(children), 20, "Должно быть не менее 20 L2 подкатегорий")

        taxonomy_dict = {c.id: c for c in MASTER_TAXONOMY}
        # Проверка валидности parent_id
        for child in children:
            self.assertIn(child.parent_id, taxonomy_dict, f"Родитель {child.parent_id} не найден в таксономии")

    def test_category_serialization(self):
        cat = Category(
            id="phones",
            name="Смартфоны",
            parent_id="electronics",
            icon="📱",
            search_priority=100,
            attributes_schema={"ram_gb": "int", "storage_gb": "int"},
            aliases=["iphone", "телефон"]
        )
        d = cat.to_dict()
        self.assertEqual(d["id"], "phones")
        self.assertEqual(d["name"], "Смартфоны")
        self.assertEqual(d["parent_id"], "electronics")
        self.assertEqual(d["attributes_schema"], {"ram_gb": "int", "storage_gb": "int"})

    def test_taxonomy_mapper_exact_and_slug(self):
        # По ID/slug
        res_slug = self.mapper.map_category("laptops")
        self.assertIsNotNone(res_slug)
        self.assertEqual(res_slug.name, "Ноутбуки")

        # По точному имени
        res_name = self.mapper.map_category("Смартфоны")
        self.assertIsNotNone(res_name)
        self.assertEqual(res_name.id, "phones")

    def test_taxonomy_mapper_aliases(self):
        # По алиасу "айфон" -> Смартфоны
        res_alias = self.mapper.map_category("айфон 15")
        self.assertIsNotNone(res_alias)
        self.assertEqual(res_alias.id, "phones")

        # По алиасу "компьютер" -> Компьютеры и ПК
        res_pc = self.mapper.map_category("персональный компьютер")
        self.assertIsNotNone(res_pc)
        self.assertEqual(res_pc.id, "pc_desktops")

    def test_taxonomy_mapper_matches_whole_words(self):
        # "мясо" не должно совпадать с "мясорубками", "шины" с "машинами", "кофе" с "кофемолками"
        for raw, expected in (
            ("Электромясорубки", "kitchen_appliances"),
            ("Кофемолки", "kitchen_appliances"),
            ("Йогуртницы", "kitchen_appliances"),
            ("Кухонные комбайны и машины", "kitchen_appliances"),
            ("Tgrad: 🧺 Сушильные машины", "washing_machines"),
            ("Шлифовальные машины", "tools_repair"),
            ("Arbuz: 🥩 Мясо и птица", "meat_poultry"),
            ("Каталог / Смартфоны и гаджеты / Apple", "phones"),
            ("Смартфон", "phones"),
            ("SSD диски", "storage"),
            ("ITMag: 🗄 Корпуса", "pc_cases"),
            ("ITMag: 🔋 Блоки питания", "psu"),
            ("Forcecom: 🌐 Роутеры", "network"),
            ("ITMag: 🖨 МФУ", "printers"),
            ("Товары для сада и огорода", "garden"),
            ("Forcecom: 💾 SSD диски", "storage"),
        ):
            with self.subTest(raw=raw):
                res = self.mapper.map_category(raw)
                self.assertIsNotNone(res)
                self.assertEqual(res.id, expected)
        self.assertIsNone(self.mapper.map_category("Интертоп: 👜 Аксессуары"))

    def test_fts_query_finds_word_forms_and_compounds(self):
        from repositories.search_repo import SearchRepository
        query = SearchRepository._build_fts_query("мясорубки")
        self.assertEqual(query, '("мясорубк"* OR "электромясорубк"*)')
        # Множественное число коротких слов от 5 букв находит форму единственного числа
        self.assertIn('"плит"*', SearchRepository._build_fts_query("плиты"))
        self.assertIn('"бритв"*', SearchRepository._build_fts_query("бритвы"))
        # Короткие слова единственного числа окончание не теряют: "машина" не должна находить "машинка"
        self.assertIn('"машина"*', SearchRepository._build_fts_query("машина"))

    def test_get_subtree_ids(self):
        sub_ids = self.mapper.get_subtree_ids("electronics")
        self.assertIn("electronics", sub_ids)
        self.assertIn("phones", sub_ids)
        self.assertIn("laptops", sub_ids)
        self.assertIn("gpus", sub_ids)

        # Для листовой категории поддерево состоит только из неё
        leaf_sub = self.mapper.get_subtree_ids("phones")
        self.assertEqual(leaf_sub, {"phones"})

    def test_get_breadcrumbs(self):
        crumbs = self.mapper.get_breadcrumbs("laptops")
        self.assertEqual(len(crumbs), 2)
        self.assertEqual(crumbs[0]["id"], "electronics")
        self.assertEqual(crumbs[0]["name"], "Электроника")
        self.assertEqual(crumbs[1]["id"], "laptops")
        self.assertEqual(crumbs[1]["name"], "Ноутбуки")

        root_crumbs = self.mapper.get_breadcrumbs("electronics")
        self.assertEqual(len(root_crumbs), 1)
        self.assertEqual(root_crumbs[0]["id"], "electronics")

    def test_build_tree(self):
        tree = self.mapper.build_tree()
        self.assertIsInstance(tree, list)
        self.assertGreaterEqual(len(tree), 10)
        # Находим электронику в дереве
        electronics = next((c for c in tree if c["id"] == "electronics"), None)
        self.assertIsNotNone(electronics)
        children = electronics.get("children", [])
        child_ids = [c["id"] for c in children]
        self.assertIn("phones", child_ids)
        self.assertIn("laptops", child_ids)


class TestCategoryRepositoryAndSeeding(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.repo = CategoryRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_auto_seeding_on_init(self):
        all_cats = self.repo.list_all()
        self.assertGreaterEqual(len(all_cats), 30, "Таксономия должна быть автоматически засеяна в БД")

        roots = self.repo.list_roots()
        self.assertGreaterEqual(len(roots), 10)

        electronics_children = self.repo.list_children("electronics")
        self.assertGreater(len(electronics_children), 0)

    def test_crud_category(self):
        custom = Category(
            id="custom_smart_home",
            name="Умный дом",
            parent_id="electronics",
            icon="🏠",
            search_priority=80,
            attributes_schema={"protocol": "string", "ecosystem": "string"},
            aliases=["smart home", "zigbee"]
        )
        self.repo.save(custom)

        fetched = self.repo.get_by_id("custom_smart_home")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Умный дом")
        self.assertEqual(fetched.attributes_schema, {"protocol": "string", "ecosystem": "string"})
        self.assertEqual(fetched.aliases, ["smart home", "zigbee"])

    def test_count_products_by_category(self):
        prod_repo = ProductRepository(self.conn)
        prod_repo.save_or_update(CanonicalProduct(
            id="p1",
            canonical_key="key_p1",
            title="Apple iPhone 15",
            brand="Apple",
            model="iPhone 15",
            category="phones"
        ))
        prod_repo.save_or_update(CanonicalProduct(
            id="p2",
            canonical_key="key_p2",
            title="ASUS ROG Strix Laptop",
            brand="ASUS",
            model="ROG",
            category="laptops"
        ))

        counts = self.repo.count_products_by_category()
        self.assertEqual(counts.get("phones"), 1)
        self.assertEqual(counts.get("laptops"), 1)


class TestSearchWithCategoryArchitecture(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.core = SearchCore(self.search_repo)

        # Создаем продавца и канал
        self.seller = Seller(id="s1", slug="kaspi-magazin", name="Kaspi Magazin", rating=4.9)
        self.seller_repo.save_or_update(self.seller)
        self.channel = Channel(id="c1", seller_id="s1", channel_type=ChannelType.WEBSITE, name="Kaspi Store")
        self.channel_repo.save_or_update(self.channel)

        # Заполняем товары разных категорий
        p_phone = CanonicalProduct(id="prod_phone", canonical_key="key_phone", title="Смартфон Apple iPhone 15 128GB", category="phones")
        p_laptop = CanonicalProduct(id="prod_laptop", canonical_key="key_laptop", title="Ноутбук Apple MacBook Air 13 M2", category="laptops")
        p_coffee = CanonicalProduct(id="prod_coffee", canonical_key="key_coffee", title="Кофемашина DeLonghi Magnifica", category="coffee_machines")

        self.product_repo.save_or_update(p_phone)
        self.product_repo.save_or_update(p_laptop)
        self.product_repo.save_or_update(p_coffee)

        self.offer_repo.save_or_update(Offer(
            id="off1", product_id="prod_phone", channel_id="c1", seller_id="s1",
            external_sku="sku1", url="https://example.com/1",
            price=380000, condition=Condition.NEW, availability=Availability.IN_STOCK,
            city="Алматы"
        ))
        self.offer_repo.save_or_update(Offer(
            id="off2", product_id="prod_laptop", channel_id="c1", seller_id="s1",
            external_sku="sku2", url="https://example.com/2",
            price=540000, condition=Condition.NEW, availability=Availability.IN_STOCK,
            city="Алматы"
        ))
        self.offer_repo.save_or_update(Offer(
            id="off3", product_id="prod_coffee", channel_id="c1", seller_id="s1",
            external_sku="sku3", url="https://example.com/3",
            price=220000, condition=Condition.NEW, availability=Availability.IN_STOCK,
            city="Алматы"
        ))

    def tearDown(self):
        self.conn.close()

    def test_search_subtree_category_filtering(self):
        # Поиск "Apple" без фильтра находит и iPhone, и MacBook
        res_all = self.core.search("Apple")
        self.assertEqual(res_all.total_products, 2)

        # Фильтр по подкатегории "phones" находит только iPhone
        res_phones = self.core.search("Apple", category="phones")
        self.assertEqual(res_phones.total_products, 1)
        self.assertEqual(res_phones.items[0].product.id, "prod_phone")

        # Фильтр по родительской категории "electronics" находит и iPhone, и MacBook
        res_electronics = self.core.search("Apple", category="electronics")
        self.assertEqual(res_electronics.total_products, 2)

        # Фильтр по категории бытовой техники "home_appliances" не находит Apple
        res_home = self.core.search("Apple", category="home_appliances")
        self.assertEqual(res_home.total_products, 0)

    def test_category_facets_in_search_response(self):
        # Ищем без строгой категории - получаем товары разных категорий и проверяем фасеты
        res = self.core.search("")
        self.assertGreaterEqual(res.total_products, 3)
        self.assertTrue(len(res.category_facets) > 0)

        # Проверяем структуру фасетов
        facet_map = {f["id"]: f for f in res.category_facets}
        self.assertIn("phones", facet_map)
        self.assertEqual(facet_map["phones"]["count"], 1)
        self.assertIn("icon", facet_map["phones"])


class TestCategoryHttpEndpoints(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os
        import tempfile
        self.tmp_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)

        conn = sqlite3.connect(self.db_path)
        init_schema_v2(conn)

        # Добавим продавца, канал, товар и оффер для проверки
        s_repo = SellerRepository(conn)
        s_repo.save_or_update(Seller(id="s_test", slug="test-seller", name="Test Seller"))
        ch_repo = ChannelRepository(conn)
        ch_repo.save_or_update(Channel(id="ch_test", seller_id="s_test", channel_type=ChannelType.WEBSITE, name="Test Web"))

        prod_repo = ProductRepository(conn)
        prod_repo.save_or_update(CanonicalProduct(
            id="p_test",
            canonical_key="key_test",
            title="Тестовый Смартфон",
            category="phones"
        ))

        off_repo = OfferRepository(conn)
        off_repo.save_or_update(Offer(
            id="off_test",
            product_id="p_test",
            channel_id="ch_test",
            seller_id="s_test",
            external_sku="sku_test",
            url="https://example.com/phone",
            price=150000,
            condition=Condition.NEW,
            availability=Availability.IN_STOCK,
            city="Алматы"
        ))
        conn.commit()
        conn.close()

        import database
        self._orig_get_connection = database.get_connection
        database.get_connection = lambda: sqlite3.connect(self.db_path)

    def tearDown(self):
        import os
        import database
        database.get_connection = self._orig_get_connection
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_get_categories_tree_v2(self):
        req = make_mocked_request("GET", "/api/v2/categories?tree=true")
        resp = await categories_v2_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["ok"])
        cats = body["categories"]
        self.assertIsInstance(cats, list)
        self.assertGreaterEqual(len(cats), 10)

        # Проверяем счетчик у электроники (должен включить 1 телефон)
        electronics = next((c for c in cats if c["id"] == "electronics"), None)
        self.assertIsNotNone(electronics)
        self.assertGreaterEqual(electronics["product_count"], 1)

    async def test_get_categories_flat_v2(self):
        req = make_mocked_request("GET", "/api/v2/categories?tree=false")
        resp = await categories_v2_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["ok"])
        cats = body["categories"]
        self.assertGreaterEqual(len(cats), 30)

    async def test_get_category_detail_v2(self):
        req = make_mocked_request("GET", "/api/v2/categories/phones")
        req.match_info["id_or_slug"] = "phones"
        resp = await get_category_v2_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["ok"])
        self.assertEqual(body["category"]["id"], "phones")
        self.assertEqual(len(body["breadcrumbs"]), 2)
        self.assertEqual(body["breadcrumbs"][0]["id"], "electronics")

    async def test_get_category_not_found(self):
        req = make_mocked_request("GET", "/api/v2/categories/nonexistent_12345")
        req.match_info["id_or_slug"] = "nonexistent_12345"
        resp = await get_category_v2_handler(req)
        self.assertEqual(resp.status, 404)

    async def test_legacy_categories_handler(self):
        req = make_mocked_request("GET", "/api/categories")
        resp = await categories_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertIsInstance(body, list)
        self.assertGreater(len(body), 0)
        self.assertIn("name", body[0])
        self.assertIn("icon", body[0])

    async def test_search_v2_with_category_param(self):
        req = make_mocked_request("GET", "/api/v2/search?q=Смартфон&category=phones")
        resp = await search_v2_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertEqual(body["total_products"], 1)
        self.assertIn("category_facets", body)


if __name__ == "__main__":
    unittest.main()
