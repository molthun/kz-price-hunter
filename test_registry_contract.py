"""Контракт каждого магазина из реестра (R-M09): новые адаптеры проверяются автоматически,
без ручного списка классов. Без сети."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import inspect
import os
import tempfile
import unittest

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""

import config  # noqa: E402
from web.server import SHOP_REGISTRY  # noqa: E402


class RegistryContractTest(unittest.TestCase):
    def test_every_registered_shop(self):
        self.assertEqual(set(SHOP_REGISTRY), set(config.SHOP_KEYS), "реестр и SHOP_KEYS расходятся")
        for key, (cls, categories, name) in SHOP_REGISTRY.items():
            with self.subTest(shop=key):
                # Название в товарах совпадает с SHOP_KEYS — иначе личный фильтр магазинов его не узнает
                self.assertEqual(cls.SHOP_NAME, config.SHOP_KEYS[key])
                self.assertEqual(name, config.SHOP_KEYS[key])
                self.assertTrue(inspect.iscoroutinefunction(cls.scrape), "scrape должен быть async")
                params = list(inspect.signature(cls.scrape).parameters)
                self.assertEqual(params[1:3], ["category_name", "category_url"])
                self.assertIn("max_pages", params)
                self.assertTrue(callable(getattr(cls, "close", None)), "нужен close()")
                self.assertTrue(categories, "нет категорий")
                for cat in categories:
                    self.assertTrue(cat.get("name") and cat.get("url") and cat.get("master"), cat)
                    max_pages = cat.get("max_pages")
                    self.assertTrue(max_pages is None or (isinstance(max_pages, int) and max_pages > 0), cat)

    def test_close_is_idempotent(self):
        for key, (cls, _, _) in SHOP_REGISTRY.items():
            with self.subTest(shop=key):
                scraper = cls()
                scraper.close()
                scraper.close()


if __name__ == "__main__":
    unittest.main()
