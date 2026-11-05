from tests import test_support  # noqa: F401
import sqlite3
import unittest
from catalog_classifier import CatalogClassifier
from domain.category import TaxonomyMapper
from domain.storage_types import storage_type
from domain.feed_categories import apply_feed_categories, apply_section_categories, repair_storage_categories, SHOP_FEEDS
from repositories import init_schema_v2, ProductRepository, OfferRepository, SellerRepository, ChannelRepository
from repositories.category_repo import CategoryRepository
from repositories.search_repo import SearchRepository
from domain.models import CanonicalProduct, Offer, Seller, Channel
from domain.search_core import SearchCore


class StorageTypesTests(unittest.TestCase):
    def test_types_despite_bad_section_and_primary_device_specs(self):
        c=CatalogClassifier()
        cases=[
            ('Комплект оперативной памяти AMD Radeon UDIMM (R5S532G5600U1K) 32 ГБ, DDR 5, 5600 МГц','ram'),
            ('Оперативная память для ноутбука Kingston DDR4 SO-DIMM 8GB','ram'),
            ('Kingston Fury Beast DDR5 16GB DIMM','ram'),
            ('Модуль памяти DDR4 Kingston для AMD Ryzen','ram'),
            ('SSD Samsung 990 PRO 1TB','storage'),
            ('Samsung SSD 870 EVO 1TB','storage'),
            ('SSD накопитель Kingston для ноутбука 512GB','storage'),
            ('Жесткий диск SSD Kingston 512GB','storage'),
            ('Внешний жесткий диск 1000GB Transcend StoreJet','hdd'),
            ('Внешний жёсткий диск Seagate 2TB','hdd'),
            ('HDD Western Digital 4TB','hdd'),
            ('Seagate BarraCuda HDD 2TB','hdd'),
            ('Карта памяти Samsung microSD 128GB','removable_storage'),
            ('USB Flash Drive Kingston 64GB','removable_storage'),
            ('USB Флешка Transcend 32GB','removable_storage'),
            ('Флеш-диск Cromex 16ГБ','removable_storage'),
            ('USB Flash-карта Smartbuy 64GB','removable_storage'),
            ('Карта памяти microSD Transcend 64GB, адаптер','removable_storage'),
            ('Внешний HDD Transcend 2TB, металлический корпус','hdd'),
            ('Корпоративный жесткий диск Seagate EXOS 10TB','hdd'),
            ('Ноутбук Lenovo 16GB DDR5 SO-DIMM SSD 512GB','laptops'),
            ('Компьютер HP 32GB DDR5 SSD 1TB HDD 2TB','pc_desktops'),
            ('Материнская плата ASUS DDR5 DIMM','motherboards'),
            ('Видеокарта Radeon DDR5 8GB','gpus'),
        ]
        for title,cat in cases:
            with self.subTest(title=title):
                self.assertEqual(c.classify_rules(title,raw_category='SSD диски').category_id,cat)
        for title in ('Корпус для HDD 2.5','Радиатор для оперативной памяти DDR5 DIMM','Кабель SATA для SSD','Док-станция для HDD и SSD'):
            self.assertIsNone(storage_type(title),title)

    def test_mapping_and_feed_sections_are_disjoint(self):
        self.assertNotIn('USB внешние накопители', SHOP_FEEDS['Белый Ветер'])
        mapper=TaxonomyMapper()
        for raw,cat in [('SSD','storage'),('Жесткие диски','hdd'),('HDD','hdd'),('RAM','ram'),('Оперативная память','ram'),('Карты памяти','removable_storage'),('removable_storage','removable_storage')]:
            self.assertEqual(mapper.map_category(raw).id,cat)


class StorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        init_schema_v2(self.conn)
        self.conn.execute('CREATE TABLE products(id TEXT,shop TEXT,category TEXT,is_active INTEGER)')
        SellerRepository(self.conn).save_or_update(Seller(id='s',slug='s',name='Shop'))
        ChannelRepository(self.conn).save_or_update(Channel(id='c',seller_id='s',channel_type='direct',name='Shop'))
        self.conn.commit()
        SearchRepository._cat_cache={}

    def add(self,pid,title,raw='SSD диски',shop='X'):
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id=pid,canonical_key=pid,title=title,category='SSD накопители'))
        OfferRepository(self.conn).save_or_update(Offer(id='off_'+pid,product_id=pid,seller_id='s',channel_id='c',external_sku=pid,url='https://example.com/'+pid,price=10000,city='Астана'))
        self.conn.execute('INSERT INTO products VALUES(?,?,?,1)',(pid,shop,raw))
        self.conn.execute("INSERT INTO category_ai_verdicts VALUES(?, 'SSD накопители','SSD накопители',NULL,'feed',1,'x')",(pid,))
        self.conn.commit()

    def test_repair_old_pins_search_facets_repeat_and_ingestion(self):
        self.add('ram','Комплект оперативной памяти AMD Radeon UDIMM DDR5 32GB')
        self.add('hdd','Внешний жесткий диск Transcend 1TB')
        self.add('ssd','SSD Samsung 1TB')
        self.add('flash','Карта памяти microSD 128GB')
        self.add('unknown','Неизвестная модель')
        self.add('article','(KF560C30BBEAK2-64)',raw='ITMag: Оперативная память')
        self.add('case','Корпус Wintek K1008 ATX HDD SSD',shop='Белый Ветер')
        before=[tuple(r) for r in self.conn.execute('SELECT id,product_id,price FROM offers ORDER BY id')]
        self.assertEqual(len(repair_storage_categories(self.conn,apply=False)),5)
        self.assertEqual(self.conn.execute("SELECT category FROM canonical_products WHERE id='ram'").fetchone()[0],'SSD накопители')
        repair_storage_categories(self.conn)
        self.assertEqual(repair_storage_categories(self.conn),[])
        apply_section_categories(self.conn,CatalogClassifier())
        apply_feed_categories(self.conn)
        self.assertEqual(ProductRepository(self.conn).get_by_id('case').category,'Корпуса')
        repo=SearchRepository(self.conn)
        for category,pid in [('ram','ram'),('hdd','hdd'),('removable_storage','flash')]:
            data=SearchCore(repo).search('',category=category,city='Астана')
            self.assertEqual({i.product.id for i in data.items}, {'ram','article'} if category=='ram' else {pid})
        self.assertEqual({i.product.id for i in SearchCore(repo).search('',category='storage').items},{'ssd','unknown'})
        self.assertEqual(before,[tuple(r) for r in self.conn.execute('SELECT id,product_id,price FROM offers ORDER BY id')])
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='ram',canonical_key='ram',title='Комплект оперативной памяти AMD Radeon UDIMM DDR5 32GB',category='SSD накопители'))
        self.assertEqual(ProductRepository(self.conn).get_by_id('ram').category,'Оперативная память')

    def test_feed_does_not_reverse_explicit_type_and_changed_title_is_rechecked(self):
        self.add('ram','Оперативная память Kingston DDR4 16GB',shop='Белый Ветер')
        apply_feed_categories(self.conn)
        self.assertEqual(ProductRepository(self.conn).get_by_id('ram').category,'Оперативная память')
        self.conn.execute("UPDATE canonical_products SET title='HDD Seagate 2TB' WHERE id='ram'");self.conn.commit()
        apply_section_categories(self.conn,CatalogClassifier())
        self.assertEqual(ProductRepository(self.conn).get_by_id('ram').category,'Жесткие диски HDD')
        apply_feed_categories(self.conn)
        self.assertEqual(ProductRepository(self.conn).get_by_id('ram').category,'Жесткие диски HDD')
