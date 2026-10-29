"""Independent A01 edge: prior broken card, final page entirely intentional skips."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path.cwd()));os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph6414-empty-')
from tests.scrapers.test_scraper_partial_parse import evrika_page
from tests.scrapers.test_scraper_completeness import alser_page
from scrapers.evrika import EvrikaScraper
from scrapers.alser import AlserScraper
class EmptyLastPage(unittest.TestCase):
 def check(self,cls,module,url,pages):
  s=cls();s.PAGE_DELAY_SECONDS=0
  with patch('scrapers.'+module+'.requests.get',side_effect=[Mock(status_code=200,text=p) for p in pages]):
   r=s._scrape_sync('Category',url)
  print({'shop':module,'saved':len(r),'complete':r.complete,'unparsed':r.unparsed},flush=True)
  self.assertFalse(r.complete,'unparsed earlier card was forgotten on an empty final page')
 def test_evrika_prior_partial_parse_then_empty_final_page(self):
  self.check(EvrikaScraper,'evrika','https://evrika.com/catalog/nur-sultan-astana/smartfony',[
   evrika_page(2,'3 товара',broken_price=1),evrika_page(1,'3 товара',start=2,out_of_stock=1)])
 def test_alser_prior_partial_parse_then_empty_final_page(self):
  first=alser_page(2,'3 товара').replace('info-container-product-price','renamed-price',1)
  last=alser_page(1,'3 товара',start=2).replace('499 990 ₸','Нет в наличии')
  self.check(AlserScraper,'alser','https://alser.kz/astana/c/noutbuki',[first,last])
if __name__=='__main__':unittest.main(verbosity=2)
