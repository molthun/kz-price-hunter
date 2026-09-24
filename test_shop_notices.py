"""Обращения магазинов к тем, кто их парсит: распознавание, хранение и оповещение. Офлайн.

Главное, что здесь проверяется, — отличие обращения к человеку от совпадения. На страницах
магазинов полно слов «crawler» и «scraper»: это списки user-agent внутри библиотек определения
ботов, имена CSS-классов и атрибуты. Если бы они считались обращениями, владелец получал бы
ложные сообщения по половине магазинов, и настоящее утонуло бы среди них.
"""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import unittest
from unittest.mock import patch

import database
import shop_notices as notices

# Настоящее сообщение shop.kz, как оно лежит в их собранном скрипте
SHOPKZ_SCRIPT = (
    'a.push(ob)},onDomReady:function(){var mess3="Скачать YML можно по ссылке: ";'
    'var mess1="Если вы парсите сайт на предмет цен и наличия товаров, воспользуйтесь готовой '
    'выгрузкой.";var mess2="https://shop.kz/bitrix/catalog_export/yandex.php";'
    'this.logToAll(mess1,"h1");App.load();'
)

# Настоящие ложные срабатывания, собранные на живых сайтах магазинов
ISBOT_LIST = ('var re=/bot|Python-urllib|python-requests|aiohttp|httpx|libwww-perl|httpunit|nutch'
              '|Go-http-client|phpcrawl|msnbot|Gigabot|exabot|ia_archiver|GingerCrawler|HTTrack/')
CSS_CLASS = '<style>.product-info-price-subscraption-calculator{display:none !important}</style>'
CRAWLER_ATTR = '<body data-locale="kz" data-is-crawler="False" data-currency-code="KZT">'


class RecognitionTest(unittest.TestCase):
    def test_real_message_is_found_with_its_link(self):
        found = notices.extract(SHOPKZ_SCRIPT, "bundle.js")
        texts = [n["text"] for n in found]
        self.assertIn("Если вы парсите сайт на предмет цен и наличия товаров, "
                      "воспользуйтесь готовой выгрузкой.", texts)
        self.assertIn("Скачать YML можно по ссылке:", texts)
        self.assertTrue(any(n["url"].endswith("yandex.php") for n in found),
                        "адрес выгрузки полезнее самой фразы, он должен попасть в находку")

    def test_message_is_found_in_a_large_minified_file(self):
        """На большом бандле кавычки сходятся не там, где кажется: поиск идёт от слова к границам."""
        noise = 'var x="' + "a" * 5000 + '";var y=\'' + "b" * 5000 + "';"
        found = notices.extract(noise + SHOPKZ_SCRIPT, "bundle.js")
        self.assertTrue(any("воспользуйтесь готовой выгрузкой" in n["text"] for n in found))

    def test_bot_detection_list_is_not_a_message(self):
        self.assertEqual(notices.extract(ISBOT_LIST, "vendor.js"), [])

    def test_css_class_and_html_attribute_are_not_messages(self):
        self.assertEqual(notices.extract(CSS_CLASS, "html"), [])
        self.assertEqual(notices.extract(CRAWLER_ATTR, "html"), [])

    def test_bare_url_is_not_a_message(self):
        self.assertFalse(notices.looks_like_message(
            "https://shop.kz/bitrix/catalog_export/yandex.php"))

    def test_fingerprint_follows_the_text(self):
        one = [{"text": "Если вы парсите сайт, возьмите выгрузку."}]
        two = [{"text": "Если вы парсите сайт, напишите нам на partners@example.kz."}]
        self.assertNotEqual(notices.fingerprint(one), notices.fingerprint(two))
        self.assertEqual(notices.fingerprint(one), notices.fingerprint(list(one)))

    def test_foreign_cdn_scripts_are_not_fetched(self):
        html = ('<script src="https://cdn.jsdelivr.net/x.js"></script>'
                '<script src="/local/app.js"></script>'
                '<script src="https://static.shop.kz/b.js"></script>')
        urls = notices.script_urls(html, "https://shop.kz/", "shop.kz")
        self.assertEqual(urls, ["https://shop.kz/local/app.js", "https://static.shop.kz/b.js"])


class StorageTest(unittest.TestCase):
    def setUp(self):
        database.init_db()
        with database.get_connection() as conn:
            conn.execute("DELETE FROM shop_notices")
            conn.commit()

    def test_new_text_is_reported_once_and_first_seen_is_kept(self):
        body = json.dumps([{"text": "Если вы парсите сайт, возьмите выгрузку."}])
        self.assertTrue(database.save_shop_notice("shopkz", "shop.kz", "aaa", body, "2026-09-25T10:00:00+00:00"))
        self.assertFalse(database.save_shop_notice("shopkz", "shop.kz", "aaa", body, "2026-09-26T10:00:00+00:00"),
                         "тот же текст второй раз владельца не беспокоит")
        self.assertTrue(database.save_shop_notice("shopkz", "shop.kz", "bbb", body, "2026-09-27T10:00:00+00:00"),
                        "изменённый текст сообщается заново")
        row = database.get_shop_notices()["shopkz"]
        self.assertEqual(row["first_seen_at"], "2026-09-25T10:00:00+00:00",
                         "дата первого появления не переписывается")
        self.assertEqual(row["last_changed_at"], "2026-09-27T10:00:00+00:00")
        self.assertEqual(row["last_checked_at"], "2026-09-27T10:00:00+00:00")

    def test_alert_id_is_stable_for_text_and_differs_between_shops(self):
        self.assertEqual(notices.notice_alert_id("shopkz", "aaa"), notices.notice_alert_id("shopkz", "aaa"))
        self.assertNotEqual(notices.notice_alert_id("shopkz", "aaa"), notices.notice_alert_id("shopkz", "bbb"))
        self.assertNotEqual(notices.notice_alert_id("shopkz", "aaa"), notices.notice_alert_id("sulpak", "aaa"))
        self.assertLess(notices.notice_alert_id("shopkz", "aaa"), -notices.NOTICE_ALERT_BASE + 1,
                        "свой диапазон идентификаторов, чтобы не столкнуться со сводкой")


class ScheduleTest(unittest.TestCase):
    def test_unseen_shop_forces_a_check(self):
        self.assertTrue(notices.due({"shopkz": {"last_checked_at": "2026-09-25T10:00:00+00:00"}},
                                    "2026-09-25T11:00:00+00:00", shops=3))

    def test_recent_check_is_not_repeated(self):
        known = {"a": {"last_checked_at": "2026-09-25T10:00:00+00:00"}}
        self.assertFalse(notices.due(known, "2026-09-26T10:00:00+00:00", shops=1))

    def test_week_old_check_is_due_again(self):
        known = {"a": {"last_checked_at": "2026-09-01T10:00:00+00:00"}}
        self.assertTrue(notices.due(known, "2026-09-25T10:00:00+00:00", shops=1))


class CheckAndQueueTest(unittest.TestCase):
    def setUp(self):
        database.init_db()
        with database.get_connection() as conn:
            conn.execute("DELETE FROM shop_notices")
            conn.execute("DELETE FROM notification_outbox")
            conn.commit()

    def _run(self, inspect_results, now="2026-09-25T10:00:00+00:00"):
        with patch.object(notices, "inspect_shop", side_effect=lambda k, h: inspect_results[k]), \
             patch("daily_digest.recipients", return_value=[777]):
            return notices.check_and_queue({k: v["host"] for k, v in inspect_results.items()},
                                           names={"shopkz": "Белый Ветер"}, now_iso=now)

    def test_new_notice_reaches_the_owner_once(self):
        result = {"shop_key": "shopkz", "host": "shop.kz", "checked": True, "error": None,
                  "notices": [{"text": "Если вы парсите сайт, возьмите выгрузку.",
                               "source": "b.js", "url": "https://shop.kz/y.php"}],
                  "fingerprint": "aaa"}
        first = self._run({"shopkz": result})
        self.assertEqual(first["changed"], ["shopkz"])
        self.assertEqual(first["queued"], 1)

        second = self._run({"shopkz": result}, now="2026-10-05T10:00:00+00:00")
        self.assertEqual(second["changed"], [], "тот же текст второй раз не рассылается")
        with database.get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM notification_outbox").fetchone()["c"]
        self.assertEqual(total, 1, "одно сообщение на одно обращение")

    def test_unreachable_shop_is_not_counted_as_clean(self):
        result = {"shop_key": "moon", "host": "moon.kz", "checked": False,
                  "error": "страница не открылась", "notices": [], "fingerprint": ""}
        outcome = self._run({"moon": result})
        self.assertEqual(outcome["unreachable"], ["moon"])
        self.assertEqual(outcome["checked"], 0, "непроверенный магазин не попадает в проверенные")

    def test_message_quotes_the_shop_and_shows_the_link(self):
        lines = notices.message_lines(
            {"shop_key": "shopkz", "host": "shop.kz",
             "notices": [{"text": "Возьмите готовую выгрузку.", "source": "b.js",
                          "url": "https://shop.kz/y.php"}]}, "Белый Ветер")
        text = "\n".join(lines)
        self.assertIn("Белый Ветер", text)
        self.assertIn("Возьмите готовую выгрузку.", text)
        self.assertIn("https://shop.kz/y.php", text)


if __name__ == "__main__":
    unittest.main()
