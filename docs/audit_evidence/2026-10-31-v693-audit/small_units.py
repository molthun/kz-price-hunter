"""Explicit /1g and /5g are price bases; standalone 5G remains a connectivity attribute."""
import json
import sqlite3
import unittest

from domain.social_connector import FetchedPost
from domain.social_content import parse_unit_price
from domain.search_core import SearchCore
from repositories import init_schema_v2
from repositories.search_repo import SearchRepository
from repositories.social_source_repo import SocialSourceRepository
from social_ingest import ingest_source


class SmallUnits(unittest.TestCase):
    def test_explicit_small_gram_quantities_do_not_merge(self):
        c=sqlite3.connect(':memory:');init_schema_v2(c)
        for handle,quantity,price in [('audit1g',1,2000),('audit5g',5,6000)]:
            src=SocialSourceRepository(c).add_source('telegram',handle)
            posts=[FetchedPost(post_id='1',text=f'Шафран {price} ₸/{quantity}g',published_at='2030-01-01T00:00:00+00:00')]
            fixture=type('Fixture',(),{'fetch':lambda self,h,posts=posts:posts})()
            result=ingest_source(src,connector=fixture,conn=c)
            self.assertEqual(result.status,'OK')
        offers=[o for i in SearchCore(SearchRepository(c)).search('шафран').items for o in i.offers]
        print('SMALL_UNITS='+json.dumps([{'price':o.price,'unit':o.unit,'quantity':o.quantity,'product_id':o.product_id} for o in offers]))
        self.assertTrue(all(o.unit=='g' for o in offers),'Explicit /1g and /5g must retain their gram basis')
        self.assertEqual(len({o.product_id for o in offers}),2)

    def test_standalone_5g_is_not_a_price_basis(self):
        self.assertIsNone(parse_unit_price('Samsung Galaxy S24 5G 300000 ₸'))


if __name__=='__main__':unittest.main(verbosity=2)
