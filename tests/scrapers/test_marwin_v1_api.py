"""Тесты MarwinScraper на публичном JSON REST API `/v1/catalog/*` (v6.42.10, MW-01..03)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import Mock

import config
from scrapers.marwin import MarwinScraper, extract_category_slug, parse_marwin_pagination
from tests.scrapers.test_city_confirmation_r1a import _retired_after


def _raw_item(pid, name="Товар", price=5000, old=None, in_stock=True, slug=None):
    return {
        "id": pid,
        "sku": str(pid),
        "name": name,
        "slug": slug if slug is not None else f"item-{pid}",
        "image": f"https://media.marwin.kz/{pid}.jpg",
        "price": {"current": price, "old": old},
        "in_stock": in_stock,
    }


class MarwinV1ApiTest(unittest.TestCase):
    def test_extract_category_slug_handles_locales_and_trailing_slash(self):
        self.assertEqual(extract_category_slug("https://www.marwin.kz/knigi-32627"), "knigi-32627")
        self.assertEqual(extract_category_slug("https://www.marwin.kz/en/knigi-32627/"), "knigi-32627")
        self.assertEqual(extract_category_slug("https://www.marwin.kz/ru/videoigry-i-konsoli-32532"), "videoigry-i-konsoli-32532")

    def test_v2_id_namespace_prevents_collision_with_legacy_magento_ids(self):
        """MW-01: одинаковое число 24909 в новом API даёт marwin_v2_24909 и не пересекается с legacy marwin_24909."""
        s = MarwinScraper()
        s._slug_to_id["vinil-i-cd-32606"] = 58
        s.session = Mock(get=Mock(return_value=Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [_raw_item(24909, name="Новый товар v2", price=9000)],
                "pagination": {"total": 1, "page": 1, "pages": 1, "limit": 20},
            }),
        )))
        res = s._scrape_sync("Музыка", "https://www.marwin.kz/vinil-i-cd-32606", max_pages=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "marwin_v2_24909")
        self.assertNotEqual(res[0]["id"], "marwin_24909")

    def test_out_of_stock_skipped_without_unparsed_and_allows_complete(self):
        s = MarwinScraper()
        s._slug_to_id["knigi-32627"] = 33
        resp = Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [
                    _raw_item(101, name="Книга в наличии", price=4990, old=5990, in_stock=True),
                    _raw_item(102, name="Нет в наличии", price=3990, in_stock=False),
                ],
                "pagination": {"total": 2, "page": 1, "pages": 1, "limit": 20},
            }),
        )
        s.session = Mock(get=Mock(return_value=resp))

        res = s._fetch_page("Книги", "https://www.marwin.kz/knigi-32627", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "marwin_v2_101")
        self.assertEqual(res[0]["old_price_on_site"], 5990)
        self.assertEqual(res.unparsed, 0)
        self.assertTrue(res.complete)
        called_params = s.session.get.call_args.kwargs.get("params") or {}
        self.assertEqual(called_params.get("availability"), "in_stock")
        self.assertNotIn("in_stock", called_params)

    def test_broken_item_increments_unparsed_and_blocks_retirement(self):
        src_url = "https://www.marwin.kz/videoigry-i-konsoli-32532"
        existing = ("marwin_v2_9001@unknown", "marwin_v2_9002@unknown")

        s = MarwinScraper()
        s._slug_to_id["videoigry-i-konsoli-32532"] = 28
        resp = Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [
                    _raw_item(9001, name="PlayStation 5 Slim", price=399990),
                    _raw_item(9999, name="Сломанная карточка без цены", price=0),
                ],
                "pagination": {"total": 2, "page": 1, "pages": 1, "limit": 20},
            }),
        )
        s.session = Mock(get=Mock(return_value=resp))

        res = s._scrape_sync("Меломан: 🎮 Видеоигры и консоли", src_url, max_pages=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res.unparsed, 1)
        self.assertFalse(res.complete)
        retired, active, assessment = _retired_after(res, existing, "marwin", src_url)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])
        self.assertEqual(active, set(existing))

        # Положительный контроль: чистый полный обход (unparsed=0) снимает отсутствующий marwin_v2_9002@unknown
        resp_clean = Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [_raw_item(9001, name="PlayStation 5 Slim", price=399990)],
                "pagination": {"total": 1, "page": 1, "pages": 1, "limit": 20},
            }),
        )
        s.session = Mock(get=Mock(return_value=resp_clean))
        res_clean = s._scrape_sync("Меломан: 🎮 Видеоигры и консоли", src_url, max_pages=5)
        self.assertTrue(res_clean.complete)
        self.assertEqual(res_clean.unparsed, 0)
        retired_clean, active_clean, assessment_clean = _retired_after(res_clean, existing, "marwin", src_url)
        self.assertTrue(assessment_clean["may_retire"])
        self.assertEqual(retired_clean, ["marwin_v2_9002@unknown"])
        self.assertEqual(active_clean, {"marwin_v2_9001@unknown"})

    def test_invalid_or_contradictory_pagination_blocks_complete_and_retirement(self):
        """MW-03: bool, float, <=0, строка и противоречивые page/total/limit не разрешают снятие."""
        src_url = "https://www.marwin.kz/knigi-32627"
        existing = ("marwin_v2_101@unknown", "marwin_v2_102@unknown")
        bad_paginations = [
            ("bool_true", {"total": 1, "page": 1, "pages": True, "limit": 20}),
            ("bool_false", {"total": 1, "page": 1, "pages": False, "limit": 20}),
            ("float_1_9", {"total": 1, "page": 1, "pages": 1.9, "limit": 20}),
            ("zero_pages", {"total": 1, "page": 1, "pages": 0, "limit": 20}),
            ("neg_pages", {"total": 1, "page": 1, "pages": -1, "limit": 20}),
            ("str_pages", {"total": 1, "page": 1, "pages": "1", "limit": 20}),
            ("contradictory_total_vs_pages", {"total": 1000, "page": 1, "pages": 1, "limit": 20}),
            ("contradictory_total_vs_raw", {"total": 5, "page": 1, "pages": 1, "limit": 20}),
            ("mismatched_page", {"total": 1, "page": 2, "pages": 1, "limit": 20}),
            ("bool_total", {"total": True, "page": 1, "pages": 1, "limit": 20}),
        ]
        for label, pag in bad_paginations:
            with self.subTest(case=label):
                pages, valid = parse_marwin_pagination(pag, page_num=1, raw_count=1)
                self.assertFalse(valid, f"{label} must be rejected")
                s = MarwinScraper()
                s._slug_to_id["knigi-32627"] = 33
                s.session = Mock(get=Mock(return_value=Mock(
                    status_code=200,
                    json=Mock(return_value={"items": [_raw_item(101)], "pagination": pag}),
                )))
                res = s._scrape_sync("Книги", src_url, max_pages=3)
                self.assertFalse(res.complete, f"{label} must not mark scan complete")
                retired, active, assessment = _retired_after(res, existing, "marwin", src_url)
                self.assertFalse(assessment["may_retire"])
                self.assertEqual(retired, [])
                self.assertEqual(active, set(existing))

    def test_mw03_six_scenario_pagination_matrix(self):
        """MW-03 матрица из 6 сценариев: 2 положительных (20+20/40, 20+1/21) и 4 отрицательных
        (хвост 20+1/40, середина 15+20/40, смена total 40->21, перекрытие 1..20 + 20..39 при total=40)."""
        src_url = "https://www.marwin.kz/knigi-32627"

        def _run(pages_payloads, existing_ids):
            s = MarwinScraper()
            s._slug_to_id["knigi-32627"] = 33
            responses = [
                Mock(status_code=200, json=Mock(return_value=p))
                for p in pages_payloads
            ]
            # Запасной пустой ответ на случай запроса стр.3 при complete=False
            responses.append(Mock(status_code=200, json=Mock(return_value={"items": [], "pagination": {}})))
            s.session = Mock(get=Mock(side_effect=responses))
            res = s._scrape_sync("Книги", src_url, max_pages=5)
            retired, active, assessment = _retired_after(res, existing_ids, "marwin", src_url)
            return res, retired, active, assessment

        existing_40_plus_missing = tuple(f"marwin_v2_{i}@unknown" for i in range(1, 42))
        existing_21_plus_missing = tuple(f"marwin_v2_{i}@unknown" for i in range(1, 23))

        # 1. Положительный контроль: 20 + 20 при total=40, pages=2, limit=20 -> снимается только 41
        res, retired, active, assessment = _run(
            [
                {"items": [_raw_item(i) for i in range(1, 21)], "pagination": {"total": 40, "page": 1, "pages": 2, "limit": 20}},
                {"items": [_raw_item(i) for i in range(21, 41)], "pagination": {"total": 40, "page": 2, "pages": 2, "limit": 20}},
            ],
            existing_40_plus_missing,
        )
        self.assertTrue(res.complete)
        self.assertTrue(assessment["may_retire"])
        self.assertEqual(retired, ["marwin_v2_41@unknown"])

        # 2. Положительный контроль: 20 + 1 при total=21, pages=2, limit=20 -> снимается только 22
        res, retired, active, assessment = _run(
            [
                {"items": [_raw_item(i) for i in range(1, 21)], "pagination": {"total": 21, "page": 1, "pages": 2, "limit": 20}},
                {"items": [_raw_item(21)], "pagination": {"total": 21, "page": 2, "pages": 2, "limit": 20}},
            ],
            existing_21_plus_missing,
        )
        self.assertTrue(res.complete)
        self.assertTrue(assessment["may_retire"])
        self.assertEqual(retired, ["marwin_v2_22@unknown"])

        # 3. Отрицательный: усечение хвоста 20 + 1 при total=40, pages=2, limit=20
        res, retired, active, assessment = _run(
            [
                {"items": [_raw_item(i) for i in range(1, 21)], "pagination": {"total": 40, "page": 1, "pages": 2, "limit": 20}},
                {"items": [_raw_item(21)], "pagination": {"total": 40, "page": 2, "pages": 2, "limit": 20}},
            ],
            existing_40_plus_missing,
        )
        self.assertFalse(res.complete)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])

        # 4. Отрицательный: усечение середины 15 + 20 при total=40, pages=2, limit=20
        res, retired, active, assessment = _run(
            [
                {"items": [_raw_item(i) for i in range(1, 16)], "pagination": {"total": 40, "page": 1, "pages": 2, "limit": 20}},
                {"items": [_raw_item(i) for i in range(21, 41)], "pagination": {"total": 40, "page": 2, "pages": 2, "limit": 20}},
            ],
            existing_40_plus_missing,
        )
        self.assertFalse(res.complete)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])

        # 5. Отрицательный: меняющийся total 40 -> 21 между страницами
        res, retired, active, assessment = _run(
            [
                {"items": [_raw_item(i) for i in range(1, 21)], "pagination": {"total": 40, "page": 1, "pages": 2, "limit": 20}},
                {"items": [_raw_item(21)], "pagination": {"total": 21, "page": 2, "pages": 2, "limit": 20}},
            ],
            existing_40_plus_missing,
        )
        self.assertFalse(res.complete)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])

        # 6. Отрицательный: межстраничное перекрытие 1..20 и 20..39 при total=40 (ID 40 вытеснен, нельзя снимать)
        res, retired, active, assessment = _run(
            [
                {"items": [_raw_item(i) for i in range(1, 21)], "pagination": {"total": 40, "page": 1, "pages": 2, "limit": 20}},
                {"items": [_raw_item(i) for i in range(20, 40)], "pagination": {"total": 40, "page": 2, "pages": 2, "limit": 20}},
            ],
            existing_40_plus_missing,
        )
        self.assertFalse(res.complete)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])
        self.assertIn("marwin_v2_40@unknown", active)

    def test_scan_state_resets_between_different_sources(self):
        """Сломанная пагинация на одном источнике не должна отравлять следующий источник того же экземпляра скрапера."""
        s = MarwinScraper()
        s._slug_to_id["knigi-32627"] = 33
        s._slug_to_id["videoigry-i-konsoli-32532"] = 28

        r_bad = Mock(status_code=200, json=Mock(return_value={
            "items": [_raw_item(1)],
            "pagination": {"total": 10, "page": 1, "pages": 1, "limit": 20},
        }))
        r_good = Mock(status_code=200, json=Mock(return_value={
            "items": [_raw_item(1)],
            "pagination": {"total": 1, "page": 1, "pages": 1, "limit": 20},
        }))
        s.session = Mock(get=Mock(side_effect=[r_bad, r_good]))

        res_bad = s._fetch_page("Книги", "https://www.marwin.kz/knigi-32627", 1)
        self.assertFalse(res_bad.complete)
        res_good = s._fetch_page("Игры", "https://www.marwin.kz/videoigry-i-konsoli-32532", 1)
        self.assertTrue(res_good.complete)

    def test_valid_multipage_complete_scan_retires_only_missing_v2_item(self):
        """Положительный контроль MW-03: валидная двухстраничная выдача завершается и снимает отсутствующий товар."""
        src_url = "https://www.marwin.kz/knigi-32627"
        existing = ("marwin_v2_201@unknown", "marwin_v2_202@unknown", "marwin_v2_203@unknown")

        s = MarwinScraper()
        s._slug_to_id["knigi-32627"] = 33
        r_p1 = Mock(status_code=200, json=Mock(return_value={
            "items": [_raw_item(201, name="Книга 1")],
            "pagination": {"total": 2, "page": 1, "pages": 2, "limit": 1},
        }))
        r_p2 = Mock(status_code=200, json=Mock(return_value={
            "items": [_raw_item(202, name="Книга 2")],
            "pagination": {"total": 2, "page": 2, "pages": 2, "limit": 1},
        }))
        s.session = Mock(get=Mock(side_effect=[r_p1, r_p2]))

        res = s._scrape_sync("Книги", src_url, max_pages=5)
        self.assertTrue(res.complete)
        self.assertEqual(res.unparsed, 0)
        self.assertEqual(len(res), 2)
        retired, active, assessment = _retired_after(res, existing, "marwin", src_url)
        self.assertTrue(assessment["may_retire"])
        self.assertEqual(retired, ["marwin_v2_203@unknown"])
        self.assertEqual(active, {"marwin_v2_201@unknown", "marwin_v2_202@unknown"})

    def test_legacy_url_resolves_via_redirect_fallback(self):
        s = MarwinScraper()
        r_404 = Mock(status_code=404)
        r_redir = Mock(status_code=200, url="https://www.marwin.kz/en/knigi-32627")
        r_cat = Mock(status_code=200, json=Mock(return_value={"id": 33, "slug": "knigi-32627"}))
        s.session = Mock(get=Mock(side_effect=[r_404, r_redir, r_cat]))

        cat_id = s._resolve_category_id("https://www.marwin.kz/books/")
        self.assertEqual(cat_id, 33)
        self.assertEqual(s._resolve_category_id("https://www.marwin.kz/books/"), 33)
        self.assertEqual(s.session.get.call_count, 3)

    def test_marwin_categories_use_v1_slugs(self):
        self.assertEqual(len(config.MARWIN_CATEGORIES), 9)
        urls = [c["url"] for c in config.MARWIN_CATEGORIES]
        self.assertEqual(len(urls), len(set(urls)))
        for entry in config.MARWIN_CATEGORIES:
            slug = extract_category_slug(entry["url"])
            self.assertRegex(slug, r".+-\d+$", f"Expected numeric-suffix v1 slug in {entry}")


if __name__ == "__main__":
    unittest.main()
