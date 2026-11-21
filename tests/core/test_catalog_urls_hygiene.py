"""Контрактные тесты гигиены URL каталогов DNS, MasterOK, Магнум и Tgrad (v6.42.9)."""
from tests import test_support  # noqa: F401
import unittest

import config


RETIRED_BROKEN_URLS = {
    # DNS (410 Gone / hub redirect)
    "https://www.dns-shop.kz/catalog/17a890dc16404e77/planshety/",
    "https://www.dns-shop.kz/catalog/8a9ddbe317404e77/nakopiteli-ssd/",
    "https://www.dns-shop.kz/catalog/17a8f3cd16404e77/naushniki-i-garnitury/",
    "https://www.dns-shop.kz/catalog/17a9e70116404e77/smart-chasy-i-braslety/",
    "https://www.dns-shop.kz/catalog/17a8a65f16404e77/igrovye-konsoli/",
    # MasterOK (404 Not Found)
    "https://masterok.kz/catalog/minitraktory/",
    "https://masterok.kz/catalog/derevoobrabatyvayushchee-oborudovanie1/",
    # Magnum (removed slug from Strapi catalog API)
    "https://magnum.kz/catalog?category=zamorojennye-produkty",
    # Tgrad (empty/delisted categories)
    "https://tgrad.kz/noutbuki/",
    "https://tgrad.kz/routery/",
    "https://tgrad.kz/elektrobritvy/",
}


class CatalogUrlsHygieneTest(unittest.TestCase):
    TARGET_LISTS = {
        "DNS_CATEGORIES": config.DNS_CATEGORIES,
        "MASTEROK_CATEGORIES": config.MASTEROK_CATEGORIES,
        "MAGNUM_CATEGORIES": config.MAGNUM_CATEGORIES,
        "TGRAD_CATEGORIES": config.TGRAD_CATEGORIES,
    }

    def test_no_retired_broken_urls_present(self):
        for list_name, items in self.TARGET_LISTS.items():
            for entry in items:
                url = entry["url"]
                self.assertNotIn(
                    url,
                    RETIRED_BROKEN_URLS,
                    f"{list_name} still contains retired broken URL: {url}",
                )

    def test_no_duplicate_urls_within_each_shop(self):
        for list_name, items in self.TARGET_LISTS.items():
            urls = [entry["url"] for entry in items]
            self.assertEqual(
                len(urls),
                len(set(urls)),
                f"{list_name} contains duplicate category URLs",
            )

    def test_entries_have_valid_name_url_and_master(self):
        for list_name, items in self.TARGET_LISTS.items():
            self.assertGreater(len(items), 0, f"{list_name} must not be empty")
            for entry in items:
                self.assertTrue(entry.get("name"), f"{list_name}: missing name in {entry}")
                self.assertTrue(
                    str(entry.get("url", "")).startswith("https://"),
                    f"{list_name}: invalid url in {entry}",
                )
                master = entry.get("master")
                self.assertTrue(
                    isinstance(master, str) and bool(master.strip()),
                    f"{list_name}: missing master category in {entry}",
                )

    def test_all_shop_categories_use_known_master_categories(self):
        """Каждый master в *_CATEGORIES всех магазинов должен существовать в MASTER_CATEGORIES (или быть 'all'),
        иначе раздел молча выпадает из волн и добора (v6.42.12)."""
        from web.server import SHOP_REGISTRY

        allowed = set(config.MASTER_CATEGORIES.keys()) | {"all"}
        missing = []

        for shop_key, (_, cats, _shop_name) in SHOP_REGISTRY.items():
            for entry in cats:
                master = entry.get("master")
                if master not in allowed:
                    missing.append((shop_key, entry.get("name"), master))

        for attr_name in dir(config):
            if not attr_name.endswith("_CATEGORIES") or attr_name == "MASTER_CATEGORIES":
                continue
            val = getattr(config, attr_name)
            if isinstance(val, list):
                for entry in val:
                    if isinstance(entry, dict) and "url" in entry:
                        master = entry.get("master")
                        if master not in allowed:
                            missing.append((attr_name, entry.get("name"), master))

        self.assertEqual(missing, [], f"Найдены категории с неизвестным master вне MASTER_CATEGORIES: {missing}")

    def test_dns_ssd_family_covers_sata_and_m2_with_distinct_names(self):
        storage_entries = [
            entry for entry in config.DNS_CATEGORIES if entry.get("master") == "pc_components"
        ]
        sata = [
            e for e in storage_entries
            if "/ssd-nakopiteli/" in e["url"] and ("2.5" in e["name"] or "SATA" in e["name"].upper())
        ]
        m2 = [
            e for e in storage_entries
            if "/ssd-m2-nakopiteli/" in e["url"] and "M.2" in e["name"].upper()
        ]
        self.assertEqual(len(sata), 1, f"DNS pc_components must include 2.5\" SATA SSD: {storage_entries}")
        self.assertEqual(len(m2), 1, f"DNS pc_components must include M.2 SSD: {storage_entries}")
        self.assertNotEqual(sata[0]["name"], m2[0]["name"])

    def test_dns_consoles_family_covers_playstation_xbox_and_portable(self):
        console_entries = [
            entry for entry in config.DNS_CATEGORIES if entry.get("master") == "consoles"
        ]
        ps = [e for e in console_entries if "playstation" in e["url"].lower()]
        xbox = [e for e in console_entries if "xbox" in e["url"].lower()]
        portable = [e for e in console_entries if "portativnye" in e["url"].lower()]

        self.assertEqual(len(ps), 1, f"DNS consoles must include PlayStation: {console_entries}")
        self.assertIn("PlayStation", ps[0]["name"])
        self.assertEqual(len(xbox), 1, f"DNS consoles must include Xbox: {console_entries}")
        self.assertIn("Xbox", xbox[0]["name"])
        self.assertEqual(len(portable), 1, f"DNS consoles must include portable consoles: {console_entries}")

    def test_dns_and_masterok_replacements_registered(self):
        dns_urls = {entry["url"] for entry in config.DNS_CATEGORIES}
        for expected in (
            "https://www.dns-shop.kz/catalog/17a8a05316404e77/plansety/",
            "https://www.dns-shop.kz/catalog/8a9ddfba20724e77/ssd-nakopiteli/",
            "https://www.dns-shop.kz/catalog/dd58148920724e77/ssd-m2-nakopiteli/",
            "https://www.dns-shop.kz/catalog/17a9ef1716404e77/nausniki-i-garnitury/",
            "https://www.dns-shop.kz/catalog/251c82c88ed24e77/smart-casy-i-braslety/",
            "https://www.dns-shop.kz/catalog/17a8978216404e77/konsoli-playstation/",
            "https://www.dns-shop.kz/catalog/17a9f95d16404e77/konsoli-microsoft-xbox/",
            "https://www.dns-shop.kz/catalog/17a8b7da16404e77/portativnye-igrovye-konsoli/",
        ):
            self.assertIn(expected, dns_urls)

        masterok_urls = {entry["url"] for entry in config.MASTEROK_CATEGORIES}
        for expected in (
            "https://masterok.kz/catalog/motobloki-kultivatory-minitraktory/",
            "https://masterok.kz/catalog/derevoobrabatyvayushchee-oborudovanie-1/",
        ):
            self.assertIn(expected, masterok_urls)


if __name__ == "__main__":
    unittest.main()
