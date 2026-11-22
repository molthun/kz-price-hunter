from tests import test_support  # noqa: F401

import unittest
from unittest.mock import MagicMock

from scrapers.kimex import KimexScraper


SAMPLE_KIMEX_HTML = """
<html><body>
<div class="cataloge__cards">
  <a class="card" data-entity="item" data-id="1038409" href="/catalog/zhenskoe/obuv/krossovki/franco-manatti_1/">
    <span class="card__inner">
      <img src="/upload/img1.webp" />
      <span class="card__title">Кроссовки Franco Manatti</span>
      <span class="card__price" data-entity="price-block">
        <span class="card__price--old" id="bx_price_old" style="display: none;">32 195 ₸</span>
        <span class="card__price--old">45 990 ₸</span>
        <span class="price-current" id="bx_price">32 195 ₸</span>
      </span>
    </span>
  </a>
  <script>
    SDVVJCCatalogItemJSData.push({
      'PRODUCT':{'ID':'1038409','NAME':'Кроссовки Franco Manatti'},
      'OFFERS':[
        {'ID':'2001','NAME':'аяқ киім/кроссовки Franco Manatti (37)','MAX_QUANTITY':'2','CAN_BUY':true,'MORE_PHOTO':[{'ID':'999','SRC':'/a.webp'}]},
        {'ID':'2002','NAME':'аяқ киім/кроссовки Franco Manatti (38)','MAX_QUANTITY':'0','CAN_BUY':true},
        {'ID':'2003','NAME':'аяқ киім/кроссовки Franco Manatti (39)','MAX_QUANTITY':'4','CAN_BUY':false},
        {'ID':'2004','NAME':'аяқ киім/кроссовки Franco Manatti (40)','MAX_QUANTITY':'5','CAN_BUY':true}
      ]
    });
  </script>

  <a class="card" data-entity="item" data-id="1036775" href="/catalog/zhenskoe/odezhda/kurtka_2/">
    <span class="card__inner">
      <img src="/upload/img2.webp" />
      <span class="card__title">Куртка Bugatti</span>
      <span class="card__price">
        <span class="price-current">89 990 ₸</span>
      </span>
    </span>
  </a>
  <script>
    SDVVJCCatalogItemJSData.push({
      'PRODUCT':{'ID':'1036775','NAME':'Куртка Bugatti'},
      'OFFERS':[
        {'ID':'3001','NAME':'Куртка Bugatti (XL)','MAX_QUANTITY':'3','CAN_BUY':true},
        {'ID':'3002','NAME':'Куртка Bugatti (M)','MAX_QUANTITY':'1','CAN_BUY':true},
        {'ID':'3003','NAME':'Куртка Bugatti (S)','MAX_QUANTITY':'2','CAN_BUY':true}
      ]
    });
  </script>
</div>
</body></html>
"""


class KimexScraperTest(unittest.TestCase):
    def test_extract_sizes_map_filters_unavailable_and_sorts(self):
        sizes_map = KimexScraper._extract_sizes_map(SAMPLE_KIMEX_HTML)
        self.assertEqual(sizes_map.get("1038409"), ["37", "40"])
        self.assertEqual(sizes_map.get("1036775"), ["S", "M", "XL"])

    def test_fetch_page_extracts_sizes_and_visible_old_price(self):
        scraper = KimexScraper()
        fake_resp = MagicMock(status_code=200, text=SAMPLE_KIMEX_HTML)
        fake_session = MagicMock()
        fake_session.get.return_value = fake_resp
        scraper.session = fake_session

        res = scraper._fetch_page("KIMEX: Кроссовки", "https://kimex.kz/catalog/zhenskoe/obuv/krossovki/", 1)
        self.assertTrue(res.complete)
        self.assertEqual(len(res), 2)

        shoes = res[0]
        self.assertEqual(shoes["id"], "kimex_1038409")
        self.assertEqual(shoes["price"], 32195)
        self.assertEqual(shoes["old_price_on_site"], 45990)
        self.assertEqual(shoes["available_sizes"], ["37", "40"])

        jacket = res[1]
        self.assertEqual(jacket["id"], "kimex_1036775")
        self.assertEqual(jacket["price"], 89990)
        self.assertEqual(jacket["old_price_on_site"], 0)
        self.assertEqual(jacket["available_sizes"], ["S", "M", "XL"])

    def test_search_live_attaches_sizes(self):
        scraper = KimexScraper()
        fake_resp = MagicMock(status_code=200, text=SAMPLE_KIMEX_HTML)
        fake_session = MagicMock()
        fake_session.get.return_value = fake_resp
        scraper.session = fake_session

        items = scraper.search_live("Franco Manatti", limit=5)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["available_sizes"], ["37", "40"])
        self.assertEqual(items[0]["old_price_on_site"], 45990)


if __name__ == "__main__":
    unittest.main()
