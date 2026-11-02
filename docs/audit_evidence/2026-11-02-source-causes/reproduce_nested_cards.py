"""Synthetic reproducer, no network or working DB. Run from repository root."""
import os
import tempfile
import json
from types import SimpleNamespace
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix='kzph-source-audit-') as directory:
    os.environ['DATA_DIR'] = directory
    import data_quality
    from scrapers.moon import MoonScraper
    from scrapers.vkusmart import VkusmartScraper
    cards = ''.join(f'''<div class="catalog_item_wrapp"><div class="item_block">
      <div class="item-title"><a href="/catalog/fixture-{i}/">Fixture {i}</a></div>
      <meta itemprop="name" content="Fixture {i}">
      <span class="price price_value" data-value="1000">1000</span>
      </div></div>''' for i in range(5))
    response = SimpleNamespace(status_code=200, text=cards)
    results = {}
    with patch('scrapers.moon.requests.get', return_value=response):
        products = MoonScraper()._fetch_page('fixture', 'https://moon.kz/fixture/', 1)
        results['moon'] = data_quality.measure(products)
    with patch.object(VkusmartScraper, '_get_session', return_value=SimpleNamespace(get=lambda *a, **k: response)):
        products = VkusmartScraper()._fetch_page('fixture', 'https://vkusmart.vmv.kz/fixture/', 1)
        results['vkusmart'] = data_quality.measure(products)
    for metrics in results.values():
        assert metrics['received'] == 10 and metrics['valid'] == 5 and metrics['duplicates'] == 5
        assert data_quality.assess(metrics, complete=False, error=None, baseline=None)['quality'] == 'degraded'
    print(json.dumps(results, ensure_ascii=False, indent=2))
