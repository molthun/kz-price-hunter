from tests import test_support  # noqa: F401
"""Браузерная проверка R1-03 (v6.42.0): бейдж «Доставка по РК» и блок «Выгоднее в другом городе».

Проверяет в настоящем браузере все 4 комбинации (390×844 / 1366×768 × светлая / тёмная темы),
экранирование XSS, отклонение javascript: URL, отсутствие горизонтального переполнения и скрытие
блока при пустом other_city_offers.
"""
import json
import pathlib
import unittest

from playwright.sync_api import sync_playwright

from tests.web.test_search_first_specs import launch_browser

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_PATH = ROOT / "web" / "templates" / "search_first.html"
ORIGIN = "http://127.0.0.1:18103"


def _product_payload(with_other: bool):
    return {
        "ok": True,
        "product": {
            "id": "prod_r103",
            "title": "Радиатор алюминиевый UNO Алюрад 500/100",
            "category": "diy",
            "image_url": "",
            "attributes": {},
        },
        "breadcrumbs": [{"id": "diy", "name": "Ремонт", "icon": "🛠"}],
        "min_price": 35000,
        "max_price": 38000,
        "offers_count": 2,
        "sellers_count": 2,
        "has_purchasable_offers": True,
        "best_offer": {
            "id": "off_12m_ast",
            "seller_name": "12 Месяцев",
            "city": "Астана",
            "price": 35000,
            "url": "https://12.kz/catalog/rad/",
            "is_purchasable": True,
        },
        "offers": [
            {
                "id": "off_12m_ast",
                "seller_id": "sel_12m",
                "seller_name": "12 Месяцев",
                "seller_domain": "12.kz",
                "channel_name": "12 Месяцев",
                "channel_type": "website",
                "city": "Астана",
                "price": 35000,
                "condition": "new",
                "url": "https://12.kz/catalog/rad/",
                "is_purchasable": True,
            },
            {
                "id": "off_shopkz",
                "seller_id": "sel_shopkz",
                "seller_name": "Белый Ветер",
                "seller_domain": "shop.kz",
                "channel_name": "Белый Ветер",
                "channel_type": "website",
                "city": "Астана / Казахстан",
                "price": 38000,
                "condition": "new",
                "url": "https://shop.kz/offer/rad/",
                "is_purchasable": True,
            },
        ],
        "other_city_offers": [
            {
                "id": "off_12m_alm",
                "seller_id": "sel_12m",
                "seller_name": "12 Месяцев",
                "seller_domain": "12.kz",
                "channel_name": "12 Месяцев",
                "city": "Алматы",
                "price": 27300,
                "url": "https://12.kz/catalog/rad/",
                "savings_kzt": 7700,
                "delivery_kz": True,
            },
            {
                "id": "off_xss",
                "seller_id": "sel_bad",
                "seller_name": "<img src=x onerror=window.__xss=1>Тест",
                "city": "Шымкент",
                "price": 26000,
                "url": "javascript:alert(1)",
                "savings_kzt": 9000,
                "delivery_kz": True,
            },
        ] if with_other else [],
        "options": [],
        "history": None,
    }


class OtherCityOffersUiTest(unittest.TestCase):
    def test_other_city_box_and_delivery_badge_across_viewports_and_themes(self):
        html = HTML_PATH.read_text(encoding="utf-8")
        with sync_playwright() as p:
            browser = launch_browser(p)
            try:
                for width, theme in ((390, "light"), (390, "dark"), (1366, "light"), (1366, "dark")):
                    with self.subTest(width=width, theme=theme):
                        ctx = browser.new_context(
                            viewport={"width": width, "height": 844},
                            color_scheme=theme,
                            is_mobile=width < 768,
                            has_touch=width < 768,
                        )
                        ctx.add_init_script(
                            f"try {{ localStorage.setItem('kzph_theme', '{theme}'); localStorage.setItem('kzph_city', 'Астана'); }} catch (e) {{}}"
                        )
                        page = ctx.new_page()
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        state_flag = {"other": True}

                        def handle_route(route):
                            u = route.request.url
                            if u.startswith(ORIGIN + "/product/"):
                                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
                            elif "/api/me" in u:
                                route.fulfill(status=200, content_type="application/json",
                                              body=json.dumps({"authenticated": False, "closed_site": False}))
                            elif "/api/v2/products/prod_r103/details" in u:
                                route.fulfill(status=200, content_type="application/json",
                                              body=json.dumps({"ok": True, "specs": [], "evidence": None}))
                            elif "/api/v2/products/prod_r103" in u:
                                route.fulfill(status=200, content_type="application/json",
                                              body=json.dumps(_product_payload(state_flag["other"])))
                            elif "/api/" in u:
                                route.fulfill(status=200, content_type="application/json",
                                              body=json.dumps({"ok": True, "items": [], "categories": [], "shops": []}))
                            else:
                                route.abort()

                        page.route("**/*", handle_route)
                        page.goto(ORIGIN + "/product/prod_r103", wait_until="networkidle")
                        page.wait_for_selector("#otherCityOffersBox .other-city-row", timeout=5000)

                        info = page.evaluate("""() => {
                            const box = document.getElementById('otherCityOffersBox');
                            const badges = [...document.querySelectorAll('.delivery-kz-badge')].map(el => el.textContent.trim());
                            const links = [...box.querySelectorAll('a')].map(a => a.getAttribute('href'));
                            const overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
                            return {
                                visible: getComputedStyle(box).display !== 'none',
                                text: box.textContent,
                                badges,
                                links,
                                xss: Boolean(window.__xss),
                                overflow,
                            };
                        }""")

                        self.assertTrue(info["visible"])
                        self.assertTrue(any("Доставка по РК" in b for b in info["badges"]))
                        self.assertIn("Алматы", info["text"])
                        self.assertIn("27", info["text"])
                        self.assertEqual(info["links"], ["https://12.kz/catalog/rad/"])
                        self.assertFalse(info["xss"])
                        self.assertLessEqual(info["overflow"], 1)
                        self.assertEqual(errors, [])

                        # Пустой other_city_offers скрывает блок
                        state_flag["other"] = False
                        page.goto(ORIGIN + "/product/prod_r103", wait_until="networkidle")
                        page.wait_for_selector("#offersTableBody tr", timeout=5000)
                        hidden = page.evaluate("() => getComputedStyle(document.getElementById('otherCityOffersBox')).display === 'none'")
                        self.assertTrue(hidden)
                        ctx.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
