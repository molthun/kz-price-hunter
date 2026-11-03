from tests import test_support  # noqa: F401
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tests.ai.test_product_classification_tags import seed,body
from repositories.classification_repo import ClassificationRepository
from repositories.product_repo import ProductRepository
from domain.models import CanonicalProduct
from catalog_classifier import CatalogClassifier
import product_classification as pc
import product_evidence as pe
import classification_facts as cf

RAM_FACTS=[dict(name='Тип памяти',value='DDR5',source_url='https://example.com/p'),dict(name='Форм-фактор памяти',value='DIMM',source_url='https://example.com/p')]

class CharacteristicInputTests(unittest.TestCase):
    def test_bounded_attributes_exclude_prose_credentials_urls_and_hash_is_order_stable(self):
        attrs={'Тип памяти':'DDR5','Форм-фактор':'DIMM','description':'fixture-private-prose','api_key':'fixture-private-key','image_url':'https://example.com/photo','metadata':{'token':'fixture-private-token'}}
        facts=cf.catalog_facts(attrs)
        self.assertEqual(len(facts),2);self.assertNotIn('fixture-private',json.dumps(facts))
        self.assertEqual(cf.attributes_hash(attrs),cf.attributes_hash(dict(reversed(list(attrs.items())))))
        self.assertEqual(cf.attributes_hash({'description':'only prose'}),'')
        self.assertNotEqual(cf.attributes_hash({'Тип памяти':'DDR4'}),cf.attributes_hash({'Тип памяти':'DDR5'}))
    def test_late_critical_and_whole_device_facts_reach_prompt(self):
        facts=[dict(name='Feature '+str(i),value='x') for i in range(40)]+RAM_FACTS+[dict(name='Processor',value='Intel Core i5')]
        product={'title':'Fixture','source_facts':facts,'catalog_facts':[dict(name='Интерфейс',value='SATA')]}
        payload=json.loads(pc.prompt(product,[],[]).split('\nТовар: ',1)[1])
        self.assertTrue(any(f['name']=='Тип памяти' for f in payload['verified_source_facts']))
        self.assertTrue(any(f['name']=='Processor' for f in payload['verified_source_facts']))
        self.assertEqual(payload['catalog_characteristics'][0]['value'],'SATA')
        self.assertFalse(any('source_url' in f for f in payload['verified_source_facts']))
        self.assertIsNone(pc.source_constraint(facts))
    def test_english_memory_attributes_and_type_conflicts(self):
        self.assertEqual(pc.source_constraint([dict(name='Memory type',value='DDR5'),dict(name='Form factor',value='DIMM')]),'ram')
        self.assertEqual(cf.conflicts([dict(name='Тип товара',value='RAM'),dict(name='Тип товара',value='SSD')]),['тип товара'])

class CharacteristicsWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db'
        c=self.conn();seed(c);c.close()
    async def asyncTearDown(self):self.tmp.cleanup()
    def conn(self):return sqlite3.connect(self.path,timeout=10)
    def setup_product(self,title,category,facts=None,attrs=None):
        c=self.conn();c.execute('UPDATE canonical_products SET title=?,category=?,attributes_json=? WHERE id=\'p\'',(title,category,json.dumps(attrs or {})))
        if facts is not None:
            signature=c.execute(f'SELECT {pe.SOURCE_SIGNATURE} FROM canonical_products p WHERE id=\'p\'').fetchone()[0]
            pe.save(c,'p',signature,dict(status='ready',facts=facts,images=[],sources=[]))
        c.commit();c.close()
    async def test_characteristics_bypass_title_only_shortcut_and_call_local_model(self):
        title='Оперативная память Kingston DDR5 32GB';rule=CatalogClassifier().classify_rules(title)
        self.setup_product(title,rule.category_name,RAM_FACTS)
        ask=AsyncMock(return_value=dict(category_id='ram',confidence=.99,reason='DDR5 и DIMM — модуль ОЗУ'))
        stats=await pc.tick(self.conn,ask,'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['asked'],1);self.assertEqual(stats['rule'],0);ask.assert_awaited_once()
        self.assertIn('DDR5',ask.call_args.args[0])
    async def test_conflicting_title_and_verified_type_are_reviewed_not_auto_applied(self):
        self.setup_product('SSD Samsung 980 1TB','SSD накопители',RAM_FACTS)
        ask=AsyncMock(return_value=dict(category_id='ram',confidence=.99,reason='Характеристики указывают на RAM'))
        stats=await pc.tick(self.conn,ask,'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['asked'],1);self.assertEqual(stats['applied'],0)
        with self.conn() as c:
            d=ClassificationRepository(c).detail('p');self.assertEqual(d['title'],'SSD Samsung 980 1TB');self.assertEqual(d['job']['status'],'review')
    async def test_article_with_verified_characteristics_can_be_classified_without_renaming(self):
        self.setup_product('KF560C30BBEAK2-64','SSD накопители',RAM_FACTS)
        stats=await pc.tick(self.conn,AsyncMock(return_value=dict(category_id='ram',confidence=.99,reason='DDR5 DIMM')), 'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['applied'],1)
        with self.conn() as c:
            d=ClassificationRepository(c).detail('p');self.assertEqual(d['tag']['category_id'],'ram');self.assertEqual(d['title'],'KF560C30BBEAK2-64')
    async def test_catalog_attributes_reach_model_but_do_not_claim_verified_source(self):
        attrs={'Тип памяти':'DDR5','Форм-фактор':'DIMM','description':'fixture-private-prose'}
        self.setup_product('KF560C30BBEAK2-64','SSD накопители',attrs=attrs)
        ask=AsyncMock(return_value=dict(category_id='ram',confidence=.99,reason='DIMM'))
        stats=await pc.tick(self.conn,ask,'fixture-local',CatalogClassifier(),limit=1,pause=0)
        payload=json.loads(ask.call_args.args[0].split('\nТовар: ',1)[1])
        self.assertEqual(len(payload['catalog_characteristics']),2);self.assertEqual(payload['verified_source_facts'],[])
        self.assertNotIn('fixture-private-prose',ask.call_args.args[0]);self.assertEqual(stats['applied'],0)
        with self.conn() as c:self.assertNotIn('fixture-private-prose',ClassificationRepository(c).source('p')['signature'])
    async def test_conflicting_sources_cannot_auto_apply(self):
        facts=RAM_FACTS+[dict(name='Тип товара',value='Оперативная память',source_url='https://example.com/a'),dict(name='Тип товара',value='SSD',source_url='https://example.com/b')]
        self.setup_product('Kingston Fury Beast','SSD накопители',facts)
        stats=await pc.tick(self.conn,AsyncMock(return_value=dict(category_id='ram',confidence=1,reason='RAM')), 'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['applied'],0)
    async def test_attribute_change_during_model_call_drops_stale_result(self):
        self.setup_product('Kingston Fury Beast','SSD накопители',attrs={'Тип памяти':'DDR4'})
        async def answer(prompt):
            with self.conn() as c:c.execute('UPDATE canonical_products SET attributes_json=? WHERE id=\'p\'',(json.dumps({'Тип памяти':'DDR5'}),))
            return dict(category_id='ram',confidence=.99,reason='old DDR4')
        stats=await pc.tick(self.conn,answer,'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['applied'],0)
        with self.conn() as c:self.assertEqual(ClassificationRepository(c).job('p')['status'],'queued')
    async def test_ingested_attribute_change_requeues_but_manual_lock_survives(self):
        with self.conn() as c:
            repo=ClassificationRepository(c);item=repo.detail('p');repo.act('p',body(item),'policy')
            before=repo.job('p')['generation']
        with patch.dict(os.environ,{'CATALOG_CLASSIFICATION_ENABLED':'1'}):
            with self.conn() as c:
                ProductRepository(c).save_or_update(CanonicalProduct(id='p',canonical_key='key:p',title='Оперативная память Kingston DDR5 32GB',category='SSD накопители',attributes={'Тип памяти':'DDR5'}))
        with self.conn() as c:
            repo=ClassificationRepository(c);d=repo.detail('p')
            self.assertTrue(d['tag']['locked']);self.assertEqual(d['tag']['category_id'],'ram');self.assertEqual(d['job']['status'],'review');self.assertGreater(d['job']['generation'],before)
    async def test_whole_device_specs_do_not_turn_bare_article_into_ram(self):
        facts=RAM_FACTS+[dict(name='Processor',value='Intel Core i5')]
        self.setup_product('OPAQUE-123','Ноутбуки',facts)
        stats=await pc.tick(self.conn,AsyncMock(return_value=dict(category_id='ram',confidence=1,reason='DDR5')), 'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['applied'],0)
        with self.conn() as c:self.assertEqual(ClassificationRepository(c).job('p')['status'],'review')

    async def test_no_characteristics_keeps_existing_title_fast_path(self):
        title='Оперативная память Kingston DDR5 32GB';rule=CatalogClassifier().classify_rules(title);self.setup_product(title,rule.category_name)
        ask=AsyncMock();stats=await pc.tick(self.conn,ask,'fixture-local',CatalogClassifier(),limit=1,pause=0)
        self.assertEqual(stats['rule'],1);ask.assert_not_called()
