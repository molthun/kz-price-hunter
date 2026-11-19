from tests import test_support  # noqa: F401
import json
import pathlib
import unittest
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_PATH = ROOT / "web" / "templates" / "search_first.html"
BASE = "http://127.0.0.1:18099"


def launch_browser(p):
    """Встроенный Chromium Playwright (образ стенда) или системный Chrome (задача test в CI,
    где `playwright install` не выполняется): тест браузерный и без браузера не пропускается."""
    try:
        return p.chromium.launch(headless=True)
    except Exception as bundled_error:
        try:
            return p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            raise bundled_error


class SearchFirstSpecTagsTest(unittest.TestCase):
    def test_clickable_spec_tags_toggle_filters_and_handle_apostrophes(self):
        html = HTML_PATH.read_text(encoding="utf-8")
        errors = []
        api_calls = []

        search_payload = {
            "query": "iphone",
            "total_products": 12,
            "page": 1,
            "total_pages": 1,
            "has_prev": False,
            "has_next": False,
            "outcome": "FOUND",
            "facets_available": True,
            "items": [
                {
                    "product": {
                        "id": "p1",
                        "title": "iPhone 15 Pro",
                        "attributes": {
                            "storage": "256 ГБ",
                            "screen_diagonal": '6.1"',
                            "color": "Men's Black",
                        },
                    },
                    "filterable_attributes": {
                        "storage": "256 ГБ",
                        "screen_diagonal": '6.1"',
                        "color": "Men's Black",
                    },
                    "min_price": 450000,
                    "sellers_count": 2,
                    "has_new": True,
                    "best_offer": {},
                }
            ],
            "category_facets": [],
            "seller_facets": [],
            "city_facets": [],
            "brand_facets": [],
        }

        with sync_playwright() as p:
            browser = launch_browser(p)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "console",
                    lambda m: errors.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None,
                )

                def handle_route(route):
                    parsed = urlparse(route.request.url)
                    if not route.request.url.startswith(BASE):
                        route.abort()
                        return
                    if parsed.path == "/api/v2/search":
                        api_calls.append(parse_qs(parsed.query))
                        route.fulfill(
                            content_type="application/json",
                            body=json.dumps(search_payload),
                        )
                        return
                    if parsed.path.startswith("/api/"):
                        route.fulfill(content_type="application/json", body="{}")
                        return
                    route.fulfill(content_type="text/html", body=html)

                page.route("**/*", handle_route)
                page.goto(f"{BASE}/search?q=iphone")
                page.wait_for_selector(".card-spec-tag")

                # 1. Click tag with apostrophe ("Men's Black")
                page.locator(".card-spec-tag", has_text="Men's Black").first.click()
                page.wait_for_function(
                    "() => { const a = new URLSearchParams(location.search).get('attributes');"
                    " return a && JSON.parse(a).color === \"Men's Black\"; }"
                )
                page.locator(".card-spec-tag.active", has_text="Men's Black").first.wait_for(
                    state="visible"
                )
                self.assertTrue(
                    page.locator(".card-spec-tag.active", has_text="Men's Black").first.is_visible()
                )
                self.assertEqual(
                    json.loads(api_calls[-1]["attributes"][0]),
                    {"color": "Men's Black"},
                )

                # 2. Toggle off on second click
                page.locator(".card-spec-tag.active", has_text="Men's Black").first.click()
                page.wait_for_function(
                    "() => !new URLSearchParams(location.search).get('attributes')"
                )

                # 3. Click tag with double quote / diagonal ('6.1"')
                page.locator(".card-spec-tag", has_text='6.1"').first.click()
                page.wait_for_function(
                    "() => { const a = new URLSearchParams(location.search).get('attributes');"
                    " return a && JSON.parse(a).screen_diagonal === '6.1\"'; }"
                )
                self.assertEqual(
                    json.loads(api_calls[-1]["attributes"][0]),
                    {"screen_diagonal": '6.1"'},
                )

                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
