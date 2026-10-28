import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path.cwd()));os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph6414-cases-')
from tests.scrapers.test_scraper_completeness import evrika_next_page,alser_page
from scrapers.evrika import EvrikaScraper
from scrapers.alser import AlserScraper
class Acceptance(unittest.TestCase):
 def test_evrika_partial_parse_is_not_complete(self):
  html=evrika_next_page(2,'2 товара')
  html=html.replace('currentPrice','brokenPrice',1)
  with patch('scrapers.evrika.requests.get',return_value=Mock(status_code=200,text=html)):
   r=EvrikaScraper()._scrape_sync('Phones','https://evrika.com/catalog/nur-sultan-astana/smartfony')
  print({'case':'evrika_partial','saved':len(r),'complete':r.complete},flush=True)
  self.assertFalse(r.complete,'one of two cards failed parsing but catalog was declared complete')
 def test_alser_partial_parse_is_not_complete(self):
  html=alser_page(2,'2 товара').replace('info-container-product-price','broken-price',1)
  with patch('scrapers.alser.requests.get',return_value=Mock(status_code=200,text=html)):
   r=AlserScraper()._scrape_sync('Laptops','https://alser.kz/astana/c/noutbuki')
  print({'case':'alser_partial','saved':len(r),'complete':r.complete},flush=True)
  self.assertFalse(r.complete)
 def test_evrika_windowed_pager_does_not_override_total(self):
  url='https://evrika.com/catalog/nur-sultan-astana/smartfony'
  pages=[evrika_next_page(24,'120 товаров',last_page=2),evrika_next_page(24,'120 товаров',last_page=3)]
  # independent products on second page
  pages[1]=pages[1].replace('/p482','/p582')
  with patch('scrapers.evrika.requests.get',side_effect=[Mock(status_code=200,text=p) for p in pages]):
   r=EvrikaScraper()._scrape_sync('Phones',url)
  print({'case':'windowed_pager','saved':len(r),'declared':120,'complete':r.complete},flush=True)
  self.assertFalse(r.complete,'visible page links were mistaken for total page count')
if __name__=='__main__':unittest.main(verbosity=2)
