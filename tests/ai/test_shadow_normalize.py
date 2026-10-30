from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_service
import database
import monitoring
import product_key
import product_verify
from scripts import c2_quality_report as c2
from scripts import migrate_to_k2 as mig
from tests.ai.test_local_ai import _DbCase

CREDS = {"local_api_base": "http://unused/v1", "local_model": "fake", "has_local": True}
ROOT = Path(__file__).resolve().parents[2]
PAIRS = json.loads((ROOT / "scripts" / "k2_verified_pairs.json").read_text(encoding="utf-8"))["pairs"]


def fields(model="Galaxy S24", **kw):
    return {"brand": "Samsung", "model": model, "sku": None, "edition": None,
            "variants": [{"value": 256, "unit": "gb"}], "unbranded": False, **kw}


def shadow(titles, primary, items, standard="k2"):
    async def reply(*args, **kwargs):
        return items if items is None or not isinstance(items, list) else {"items": items}
    with patch.object(ai_service, "call_local_api", reply):
        asyncio.run(ai_service._run_shadow_normalize(titles, CREDS, primary, standard))


def working_tables():
    with database.get_connection() as conn:
        out = {}
        for name in ("title_fields", "title_articles", "title_canonical_cache", "identity_aliases", "products"):
            out[name] = [tuple(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2")]
    return out


class KeyRulesTest(unittest.TestCase):
    def test_verified_pairs(self):
        """Регрессии известных случаев, а не оценка качества: она считается по случайной выборке (C2A-01).
        Положительные пары вне regression здесь не требуются: их потери — предмет отчёта C2, а не зелёного теста."""
        for pair in PAIRS:
            if pair["same"] is True and not pair.get("regression"):
                continue
            if pair["same"] is None or pair["label"] == "packaging":
                continue                                # OEM и BOX — разные, но правила ключа этого ещё не умеют (задача C2)
            with self.subTest(a=pair["a"], b=pair["b"]):
                keys = product_key.resolve_keys_with_articles({pair["a"]: pair["fields_a"], pair["b"]: pair["fields_b"]})
                self.assertEqual(keys[pair["a"]] == keys[pair["b"]], pair["same"], keys)

    def test_verified_pairs_across_batches_keep_apart(self):
        """Порядок и сохранённый ключ не склеивают: вторая сторона приходит, когда первая уже в базе."""
        for pair in (p for p in PAIRS if p["same"] is False and p["label"] != "packaging"):
            for first, second in ((pair["a"], pair["b"]), (pair["b"], pair["a"])):
                fa, fb = pair["fields_a"] if first == pair["a"] else pair["fields_b"], \
                    pair["fields_b"] if first == pair["a"] else pair["fields_a"]
                stored = {"title": first, "fields": fa, "canonical_key": product_key.build_key(fa, first)}
                with self.subTest(first=first):
                    keys = product_key.resolve_keys_with_articles({second: fb}, [stored])
                    self.assertNotEqual(keys[second], stored["canonical_key"])

    def test_revision_not_bridged_by_title_without_revision(self):
        """Название без ревизии с общим артикулом — спорное, а не мост между V2 и V3."""
        base = {"brand": "Sanc", "model": "M2442PH", "sku": "M2442PH", "edition": None, "variants": [], "unbranded": False}
        entries = {"a V2": {**base, "edition": "V2"}, "b V3": {**base, "edition": "V3"}, "c": base}
        disputed = []
        keys = product_key.resolve_keys_with_articles(entries, on_disputed=lambda a, b, r: disputed.append(r))
        self.assertEqual(len(set(keys.values())), 3, keys)
        self.assertTrue(any(r.startswith("revision_unknown") for r in disputed), disputed)

    def test_same_verdict_cannot_merge_component_and_system(self):
        pair = PAIRS[0]
        verdicts = {product_key.pair_hash(pair["a"], pair["b"]): "same"}
        keys = product_key.resolve_keys_with_articles({pair["a"]: pair["fields_a"], pair["b"]: pair["fields_b"]},
                                                      pair_verdicts=verdicts)
        self.assertNotEqual(keys[pair["a"]], keys[pair["b"]])

    def test_ordinary_laptop_word_does_not_split(self):
        base = {"brand": "Asus", "model": "X515", "sku": None, "edition": None, "variants": [], "unbranded": False}
        self.assertEqual(product_key.build_key(base, "Ноутбук Asus X515"), product_key.build_key(base, "Asus X515"))

    def test_bluetooth_version_is_not_revision(self):
        base = {"brand": "JBL", "model": "Tune 520", "sku": None, "edition": None, "variants": [], "unbranded": False}
        self.assertEqual(product_key.build_key(base, "JBL Tune 520 Bluetooth V5.3"), "k2:jbl:tune520")

    def test_component_word_is_not_system(self):
        base = {"brand": "Intel", "model": "Core i3-12100", "sku": None, "edition": None, "variants": [], "unbranded": False}
        self.assertEqual(product_key.build_key(base, "Процессор Intel CPU Desktop Core i3-12100, LGA1700, Tray"),
                         "k2:intel:corei312100")
        self.assertTrue(product_key.is_system_key(product_key.build_key(base, "Компьютер Core i3-12100 16GB")))

    def test_memory_card_speed_class_is_not_revision(self):
        base = {"brand": "Kingston", "model": "Canvas Go Plus", "sku": None, "edition": None, "variants": [], "unbranded": False}
        self.assertEqual(product_key.build_key(base, "Карта памяти Canvas Go Plus V30 64 ГБ Kingston"),
                         "k2:kingston:canvasgoplus:64gb")

    def test_title_revision_beats_model_answer(self):
        base = {"brand": "Afox", "model": "GTX 1050 Ti", "sku": None, "edition": "V2", "variants": [], "unbranded": False}
        self.assertEqual(product_key.revision_of(product_key.build_key(base, "Видеокарта Afox GTX 1050 Ti V9, 4 GB")), "v9")

    def test_compare_keys(self):
        cases = [("samsung:galaxy s24:256gb", "k2:samsung:galaxys24:256gb", "matched"),
                 ("aoc:27b31h:27", "k2:aoc:27b31h", "matched"),
                 ("aoc:24b3:23.8", "k2:aoc:24b3:23.8in", "matched"),
                 ("kingston:memory:1gb", "k2:kingston:memory:1tb", "mismatch"),
                 ("brand:food:85g", "k2:brand:food:85ml", "mismatch"),
                 ("brand:food:1kg", "k2:brand:food:1000g", "matched"),
                 ("brand:drink:0.5l", "k2:brand:drink:500ml", "matched"),
                 ("kingston:nv3:1000gb", "k2:kingston:nv3:1tb", "matched"),
                 ("brand:food:5", "k2:brand:food:5g", "incomparable"),
                 ("brand:phone:8/256gb", "k2:brand:phone:256gb", "incomparable"),
                 ("samsung:galaxy s24", "k2:samsung:galaxys24:256gb", "incomparable"),
                 ("samsung:galaxy s24:box", "k2:samsung:galaxys24", "matched"),
                 ("samsung:galaxy s24:256gb", "k2:samsung:galaxys24ultra:256gb", "mismatch"),
                 ("samsung:galaxy s24:128gb", "k2:samsung:galaxys24:256gb", "mismatch"),
                 ("unknown:chechevitsa:1kg", "k2:x:y", "incomparable"),
                 ("k2:a:b", "k2:a:c", "mismatch")]
        for primary, sk, expected in cases:
            with self.subTest(primary=primary):
                self.assertEqual(product_key.compare_keys(primary, sk), expected)


class ClassRulesTest(unittest.TestCase):
    """Правила задачи качества ключей: что разделяется и что при этом не должно разойтись."""
    def key(self, title, brand, model, sku=None, **kw):
        return product_key.build_key({"brand": brand, "model": model, "sku": sku, "edition": None,
                                      "variants": [], "unbranded": False, **kw}, title)

    def test_packaging(self):
        oem = self.key("AMD Ryzen 5 7600X OEM", "AMD", "Ryzen 5 7600X")
        tray = self.key("Процессор AMD Ryzen 5 7600X Tray", "AMD", "Ryzen 5 7600X")
        box = self.key("AMD Ryzen 5 7600X BOX", "AMD", "Ryzen 5 7600X")
        wof = self.key("AMD Ryzen 5 7600X WOF", "AMD", "Ryzen 5 7600X")
        cooler = self.key("Процессор AMD Ryzen 5 7600X, OEM - с кулером", "AMD", "Ryzen 5 7600X")
        self.assertEqual(oem, tray)
        self.assertEqual(box, wof)
        self.assertEqual(box, cooler)
        self.assertNotEqual(oem, box)
        self.assertEqual(self.key("DDR-5 Kingston Fury, BOX (KF560C36BBEAK2-64)", "Kingston", "Fury", "KF560C36BBEAK2-64"),
                         self.key("Память DIMM DDR5 Kingston KF560C36BBEAK2-64", "Kingston", "Fury", "KF560C36BBEAK2-64"),
                         "у памяти box — не поставка процессора")

    def test_cable_length(self):
        one = self.key("Patch cord RJ-45 Cablexpert PP12-1M/G, UTP, 1m, Green", "Cablexpert", "PP12-1M/G")
        same = self.key("Патч-корд Cablexpert PP12 1 м Cablexpert", "Cablexpert", "PP12")
        half = self.key("Patch cord RJ-45 Cablexpert PP12-0.5M/O, UTP, 0.5m", "Cablexpert", "PP12-0.5M/O")
        self.assertEqual(one, same)
        self.assertEqual(one, "k2:cablexpert:pp12:1m")
        self.assertNotEqual(one, half)
        self.assertNotEqual(self.key("Сетевой фильтр Defender ES Largo, 5м", "Defender", "ES Largo"),
                            self.key("Фильтр сетевой Defender ES Largo 1.8 м", "Defender", "ES Largo"))

    def test_watch_size(self):
        self.assertNotEqual(self.key("Смарт-часы GARMIN Fenix 8 47mm", "Garmin", "Fenix 8"),
                            self.key("Смарт-часы Garmin Fenix 8 43mm", "Garmin", "Fenix 8"))
        self.assertEqual(self.key("Смарт-часы Garmin Fenix 8 51 мм", "Garmin", "Fenix 8"),
                         self.key("Смарт-часы GARMIN Fenix 8 51mm", "Garmin", "Fenix 8"))

    def test_memory_identified_by_code(self):
        self.assertNotEqual(self.key("Оперативная память 32 GB kit Kingston Fury Beast KF560C30BBEK2-32", "Kingston", "Fury Beast"),
                            self.key("Оперативная память 32 GB Kingston Fury Beast KF432C16BB1K2/32", "Kingston", "Fury Beast"))
        self.assertEqual(self.key("Оперативная память 8Gb Transcend TS3200HLB-8G", "Transcend", "TS3200HLB-8G"),
                         self.key("Память оперативная DDR4 Desktop Transcend 8Гб (TS3200HLB-8G)", "Transcend", "TS3200HLB"))
        self.assertNotEqual(self.key("DDR-4 DIMM 16 GB Netac Basic, BOX (NTBSD4P32SP-16)", "Netac", "Basic"),
                            self.key("Оперативная память 16Gb Netac Basic NTBSD5P56SP-16", "Netac", "Basic"))

    def test_board_wifi_and_headset_modification(self):
        self.assertNotEqual(self.key("ASRock B850M-X WIFI R2.0", "ASRock", "B850M-X"),
                            self.key("Материнская плата ASRock B850M-X R2.0, AM5", "ASRock", "B850M-X"))
        self.assertNotEqual(self.key("Гарнитура Jabra Evolve2 85 Link380a MS Stereo", "Jabra", "Evolve2 85"),
                            self.key("Гарнитура Jabra Evolve2 85 Link380c MS Stereo", "Jabra", "Evolve2 85"))
        self.assertNotEqual(self.key("Гарнитура Jabra Evolve2 85 Link380a MS Stereo", "Jabra", "Evolve2 85"),
                            self.key("Гарнитура Jabra Evolve2 85 Link380a UC Stereo", "Jabra", "Evolve2 85"))
        self.assertNotEqual(self.key("Гарнитура Jabra Evolve2 65 Flex Link380a", "Jabra", "Evolve2 65"),
                            self.key("Гарнитура Jabra Evolve2 65 Link380a", "Jabra", "Evolve2 65"))

    def test_accessory_is_not_the_device(self):
        self.assertNotEqual(self.key("Игровая приставка Sony PlayStation 5 Белый +Геймпад", "Sony", "PlayStation 5"),
                            self.key("Пульт для Sony PlayStation 5, White", "Sony", "PlayStation 5"))

    def test_marketing_and_stray_variants_do_not_split(self):
        self.assertEqual(self.key("Телевизор HISENSE QLED 50E7S UHD SMART", "Hisense", "QLED 50E7S"),
                         self.key("Телевизор Hisense QLED 50E7S 50\" (50E7S)", "Hisense", "50E7S"))
        phone = [{"value": 256, "unit": "гб"}, {"value": 8, "unit": "гб"}]
        self.assertEqual(self.key("Смартфон TECNO SPARK 50 Pro 8 ГБ/256 ГБ, 6.78’’", "Tecno", "Spark 50 Pro",
                                  variants=phone + [{"value": 6.78, "unit": "дюйм"}]),
                         self.key("Смартфон Tecno Spark 50 Pro, 8 GB, 256 GB", "Tecno", "Spark 50 Pro", variants=phone))
        self.assertEqual(self.key("SSD LEXAR NS100 512GB, 2.5”, SATA (6Gb/s)", "Lexar", "NS100"),
                         self.key("SSD накопитель 512GB LEXAR NS100", "Lexar", "NS100"))

    def test_marks_are_not_undone_by_article_link(self):
        """Метка ставится в build_key, но модель-артикул связывает названия: связь должна учитывать метки."""
        for a, b, fields_ in (("AMD Ryzen 7 7700X BOX", "Процессор AMD Ryzen 7 7700X OEM",
                               {"brand": "AMD", "model": "Ryzen 7 7700X", "sku": None}),
                              ("ASRock B850M-X WIFI R2.0", "Материнская плата ASRock B850M-X R2.0",
                               {"brand": "ASRock", "model": "B850M-X", "sku": None}),
                              ("Игровая приставка Sony PlayStation 5", "Пульт для Sony PlayStation 5",
                               {"brand": "Sony", "model": "PlayStation 5", "sku": None})):
            with self.subTest(a=a):
                f = {**fields_, "variants": [], "unbranded": False}
                keys = product_key.resolve_keys_with_articles({a: f, b: f})
                self.assertNotEqual(keys[a], keys[b])

    def test_same_verdict_cannot_merge_marks(self):
        self.assertTrue(product_key.keys_conflict("k2:amd:ryzen77700", "k2:amd:ryzen77700.box"))
        self.assertTrue(product_key.keys_conflict("k2:asrock:b850mx", "k2:asrock:b850mx.wifi"))
        self.assertFalse(product_key.keys_conflict("k2:amd:ryzen77700.box", "k2:amd:ryzen77700.box"))

    def test_board_with_ddr_is_not_memory(self):
        self.assertEqual(self.key("ASRock B850M PRO RS WIFI DDR5", "ASRock", "B850M PRO RS"), "k2:asrock:b850mprors.wifi")

    def test_memory_speed_standard_is_not_a_code(self):
        fury = self.key("Оперативная память Kingston DDR4-3200 Fury Beast 16GB", "Kingston", "Fury Beast")
        value = self.key("Оперативная память Kingston DDR4-3200 ValueRAM 16GB", "Kingston", "ValueRAM")
        self.assertNotEqual(fury, value)
        self.assertEqual(fury, "k2:kingston:furybeast:16gb+ddr4")
        self.assertEqual(self.key("Оперативная память PC4-25600 DDR4-3200 Kingston KF432C16BB1/16", "Kingston", "Fury"),
                         "k2:kingston:kf432c16bb116", "настоящий код после стандарта находится")
        self.assertEqual(self.key("Оперативная память DDR5 Kingston", "Kingston", "Fury", "DDR5-5600"),
                         "k2:kingston:fury:ddr5", "стандарт в поле артикула — тоже не код")

    def test_modifier_placement_gives_one_key(self):
        self.assertEqual(self.key("ASRock B850M-X WIFI", "ASRock", "B850M-X WIFI"),
                         self.key("Материнская плата ASRock B850M-X WIFI", "ASRock", "B850M-X"))
        self.assertEqual(self.key("Jabra Evolve2 65 Flex Link380a MS Stereo", "Jabra", "Evolve2 65 Flex"),
                         self.key("Гарнитура Jabra Evolve2 65 Flex Link380a MS Stereo", "Jabra", "Evolve2 65"))
        self.assertNotEqual(self.key("ASRock B850M-X WIFI", "ASRock", "B850M-X WIFI"),
                            self.key("ASRock B850M-X", "ASRock", "B850M-X"))

    def test_cpu_families_agree_between_fields_and_title(self):
        """Явный BOX виден одинаково с полями и без них для всех семейств (C2R-03)."""
        cases = [("AMD", "Ryzen 7 7700X"), ("AMD", "Athlon 200GE"), ("AMD", "Threadripper 7960X"), ("AMD", "EPYC 7302"),
                 ("Intel", "Core i7-14700K"), ("Intel", "Core Ultra 7 265K"), ("Intel", "Core 5 120"), ("Intel", "Xeon E5-2680"),
                 ("Intel", "Pentium G7400"), ("Intel", "Celeron G5905")]
        for brand, model in cases:
            title = f"{brand} {model} BOX"
            with self.subTest(title=title):
                self.assertIn("box", product_key.marks_of(self.key(title, brand, model)))
                self.assertIn("box", product_key._title_marks(title))
                self.assertNotIn("box", product_key._title_marks(f"{brand} {model} OEM"))
        self.assertEqual(product_key._title_marks("Корпус Thermaltake Core V21 BOX"), frozenset())
        self.assertEqual(product_key._title_marks("Видеокарта Afox RTX 4060 BOX"), frozenset())

    def test_core_ultra_system_is_not_the_chip(self):
        self.assertTrue(product_key.is_system_key(self.key("Компьютер Core Ultra 7 265K/RAM 16GB/SSD 512GB", "Intel", "Core Ultra 7 265K")))

    def test_adapter_in_sku_does_not_link_models(self):
        f = {"brand": "Jabra", "sku": "Link380a MS Stereo", "variants": [], "unbranded": False}
        keys = product_key.resolve_keys_with_articles({"Jabra Evolve2 65": {**f, "model": "Evolve2 65"},
                                                       "Jabra Evolve2 85": {**f, "model": "Evolve2 85"}})
        self.assertNotEqual(keys["Jabra Evolve2 65"], keys["Jabra Evolve2 85"])


class CompletenessTest(unittest.TestCase):
    def test_only_confirmed_absence_is_no_identity(self):
        cases = [({}, "incomplete"), ({"title": "x"}, "incomplete"),
                 ({"brand": None, "model": None}, "incomplete"),
                 ({"brand": None, "model": "x", "unbranded": None}, "incomplete"),
                 ({"brand": None, "model": "x", "unbranded": False}, "incomplete"),
                 ({"brand": "Bosch", "model": None, "unbranded": False}, "incomplete"),
                 ({"brand": "Bosch", "model": None, "unbranded": True}, "incomplete"),
                 ({"brand": "Bosch", "model": ["x"], "unbranded": False}, "incomplete"),
                 ({"brand": "Bosch", "model": "x", "variants": "256", "unbranded": False}, "incomplete"),
                 ({"brand": "Bosch", "model": "x", "unbranded": "нет"}, "incomplete"),
                 ({"brand": None, "model": None, "unbranded": True}, "no_identity"),
                 ({"brand": "noname", "model": "x", "unbranded": False}, "no_identity"),
                 ({"brand": "Bosch", "model": "x"}, None)]
        for fields_, expected in cases:
            with self.subTest(fields=fields_):
                self.assertEqual(product_key.no_key_reason(fields_), expected)


class ShadowIsolationTest(_DbCase):
    def test_shadow_never_touches_working_tables(self):
        title, other = "Samsung Galaxy S24 256GB", "Samsung Galaxy S24 256 ГБ"
        primary = product_key.build_key(fields(), title)
        database.save_cached_canonical_keys_batch({title: primary, other: primary})
        database.save_title_fields_batch({title: fields(), other: fields()}, "k2", "gemini", "primary")
        before = working_tables()
        shadow([title], {title: primary}, [{"title": title, **fields("Galaxy S24 Ultra")}])    # расхождение
        self.assertEqual(working_tables(), before)
        shadow([title], {title: primary}, None)                                                # сбой
        shadow([title], {title: primary}, "мусор")                                             # не тот вид
        self.assertEqual(working_tables(), before)
        self.assertEqual(database.get_title_fields_for_titles([title])[title], fields())
        self.assertEqual(database.shadow_keys_for_titles([title])[title], "k2:samsung:galaxys24ultra:256gb")

    def test_reverse_completion_order_keeps_primary_fields(self):
        """Тень успела раньше основной и наоборот — рабочими остаются поля основной."""
        title = "Samsung Galaxy S24 256GB"
        shadow([title], {}, [{"title": title, **fields("Galaxy S24 Ultra")}])
        database.save_title_fields_batch({title: fields()}, "k2", "gemini", "primary")
        shadow([title], {title: "k2:samsung:galaxys24:256gb"}, [{"title": title, **fields("Galaxy S24 Ultra")}])
        saved = database.get_title_fields_with_source([title])[title]
        self.assertEqual((saved["source"], saved["fields"]), ("primary", fields()))


class ShadowComparisonTest(_DbCase):
    def test_v1_format_is_not_a_mismatch(self):
        title = "Samsung Galaxy S24 256GB"
        shadow([title], {title: "samsung:galaxy s24:256gb"}, [{"title": title, **fields()}], "v1")
        summary = database.normalize_shadow_summary()
        self.assertEqual((summary["matched"], summary["mismatch"]), (1, 0))

    def test_unparsable_legacy_key_is_separate(self):
        title = "Samsung Galaxy S24 256GB"
        shadow([title], {title: "unknown:galaxy:256gb"}, [{"title": title, **fields()}], "v1")
        summary = database.normalize_shadow_summary()
        self.assertEqual(summary["by_kind"].get("incomparable"), 1)
        self.assertIsNone(summary["match_rate"])

    def test_shadow_key_uses_article_link_across_chunks(self):
        """Как у основной: второе название связывается с первым по артикулу и в другой пачке."""
        a, b = "Transcend MTE110S 128GB", "Transcend TS128GMTE110S 128GB"
        fa = {"brand": "Transcend", "model": "TS128GMTE110S", "sku": None, "variants": [{"value": 128, "unit": "ГБ"}], "unbranded": False}
        fb = {**fa, "model": "MTE110S", "sku": "TS128GMTE110S"}
        key = product_key.build_key(fa, a)
        shadow([a], {a: key}, [{"title": a, **fa}])
        shadow([b], {b: key}, [{"title": b, **fb}])
        summary = database.normalize_shadow_summary()
        self.assertEqual((summary["matched"], summary["mismatch"]), (2, 0))


class ShadowQueueTest(_DbCase):
    A, B = "Samsung Galaxy S24 256GB black", "Samsung Galaxy S24 256GB white"

    def tick(self, ask=None):
        async def no_ask(prompt):
            raise AssertionError("модель проверки не нужна")
        return asyncio.run(product_verify.process_disputed_queue_tick("http://unused", ask or no_ask, daily_limit=10))

    def test_missed_match_between_groups_is_enqueued(self):
        primary = {self.A: "samsung:sm-s921b:256gb", self.B: "samsung:galaxy s24:256gb"}
        database.save_cached_canonical_keys_batch(primary)
        shadow([self.A], {self.A: primary[self.A]}, [{"title": self.A, **fields()}], "v1")
        shadow([self.B], {self.B: primary[self.B]}, [{"title": self.B, **fields()}], "v1")      # другая пачка
        pending = database.get_pending_disputed_pairs()
        self.assertEqual(len(pending), 1)
        self.assertIn("missed", pending[0]["reason"])

    def test_suspect_merge_is_enqueued_only_when_shadow_differs(self):
        primary = "samsung:galaxy s24:256gb"
        database.save_cached_canonical_keys_batch({self.A: primary, self.B: primary})
        shadow([self.A, self.B], {self.A: primary, self.B: primary},
               [{"title": self.A, **fields("Galaxy S24 Ultra")}, {"title": self.B, **fields("Galaxy S24 Ultra")}], "v1")
        self.assertEqual(database.get_pending_disputed_pairs(), [], "теневые ключи одинаковы: разделять нечего")
        shadow([self.A], {self.A: primary}, [{"title": self.A, **fields("Galaxy S24 FE")}], "v1")
        self.assertEqual(len(database.get_pending_disputed_pairs()), 1)

    def test_neighbour_without_fields_gets_shadow_fields_and_reaches_verdict(self):
        """Сквозной путь: тень → очередь → воркер → вердикт; сеть подменена, очередь и статусы настоящие."""
        primary = "samsung:galaxy s24:256gb"
        database.save_cached_canonical_keys_batch({self.A: primary, self.B: primary})

        async def reply(prompt, *args, **kwargs):
            if self.B in prompt and self.A not in prompt:
                return {"items": [{"title": self.B, **fields()}]}
            return {"items": [{"title": self.A, **fields("Galaxy S24 Ultra")}]}
        with patch.object(ai_service, "call_local_api", reply):
            asyncio.run(ai_service._run_shadow_normalize([self.A], CREDS, {self.A: primary}, "v1"))
        self.assertEqual(len(database.get_pending_disputed_pairs()), 1)
        self.assertEqual(database.get_title_fields_for_titles([self.A, self.B]), {}, "рабочие поля пусты")

        async def verify(*args, **kwargs):
            return {"decision": product_verify.DIFFERENT}
        with patch.object(product_verify, "verify_pair_cached", verify):
            result = self.tick()
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["fields_source"], {"a": "shadow", "b": "shadow"})

    def test_pair_without_fields_waits_and_is_not_lost(self):
        database.enqueue_disputed_pair(self.A, self.B, "shadow_mismatch:split")
        result = self.tick()
        self.assertEqual((result["status"], result["reason"]), ("pending", "no_fields"))
        with database.get_connection() as conn:
            row = conn.execute("SELECT status, next_attempt_at FROM disputed_product_pairs").fetchone()
        self.assertEqual(row[0], "pending")
        self.assertIsNotNone(row[1])

    def test_old_skipped_pair_reopens(self):
        database.enqueue_disputed_pair(self.A, self.B, "x")
        ph = database.get_pending_disputed_pairs()[0]["pair_hash"]
        database.update_disputed_pair_status(ph, "skipped", "no_fields")
        self.assertTrue(database.enqueue_disputed_pair(self.A, self.B, "x"))
        self.assertEqual(len(database.get_pending_disputed_pairs()), 1)

    def test_same_verdict_does_not_merge_revisions(self):
        a, b = "Sanc M2442PH V2", "Sanc M2442PH V3"
        base = {"brand": "Sanc", "model": "M2442PH", "sku": None, "variants": [], "unbranded": False}
        fa, fb = {**base, "edition": "V2"}, {**base, "edition": "V3"}
        database.save_title_fields_batch({a: fa, b: fb}, "k2", "gemini", "m")
        database.save_cached_canonical_keys_batch({a: product_key.build_key(fa, a), b: product_key.build_key(fb, b)})
        database.enqueue_disputed_pair(a, b, "x")

        async def verify(*args, **kwargs):
            return {"decision": product_verify.SAME}
        with patch.object(product_verify, "verify_pair_cached", verify):
            result = self.tick()
        self.assertEqual(result["status"], "unresolved")
        keys = database.get_cached_canonical_keys_batch([a, b])
        self.assertNotEqual(keys[a], keys[b])


class SameVerdictOnLegacyKeysTest(_DbCase):
    """Вердикт «один товар» при старых ключах: защита идёт по полям и названиям, отказ ничего не меняет (C45R-01)."""
    def product(self, pid, title, key, shop):
        database.save_or_update_product({"id": pid, "title": title, "canonical_key": key, "shop": shop,
                                         "city": "Алматы", "price": 1000, "url": f"https://example.kz/{pid}"})

    def tick(self, decision=None):
        async def verify(*args, **kwargs):
            return {"decision": decision or product_verify.SAME}

        async def ask(prompt):
            raise AssertionError("не нужна")
        with patch.object(product_verify, "verify_pair_cached", verify):
            return asyncio.run(product_verify.process_disputed_queue_tick("http://unused", ask, daily_limit=10))

    def state(self):
        with database.get_connection() as conn:
            return {name: [tuple(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2")]
                    for name in ("title_canonical_cache", "products", "offers", "offer_price_history", "identity_aliases")}

    def case(self, keys, fields_by_title, shadow_fields=True):
        database.save_cached_canonical_keys_batch(keys)
        for i, (t, k) in enumerate(keys.items()):
            self.product(f"p{i}", t, k, f"shop{i}")
        if fields_by_title and shadow_fields:
            database.save_shadow_fields_batch(fields_by_title, {t: product_key.build_key(f, t) for t, f in fields_by_title.items()},
                                              "k2", "local", "m")
        elif fields_by_title:
            database.save_title_fields_batch(fields_by_title, "k2", "gemini", "m")

    def sanc(self, edition):
        return {"brand": "Sanc", "model": "M2442PH", "sku": None, "edition": edition, "variants": [], "unbranded": False}

    def assert_refused(self, a, b):
        database.enqueue_disputed_pair(a, b, "x")
        before = self.state()
        result = self.tick()
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(self.state(), before)

    def test_v1_v1_revisions_shadow_and_working_fields(self):
        a, b = 'Sanc M2442PH V2 23.8"', 'Sanc M2442PH V3 23.8"'
        keys = {a: "sanc:m2442ph v2:23.8", b: "sanc:m2442ph v3:23.8"}
        for use_shadow in (True, False):
            with self.subTest(shadow=use_shadow):
                self.case(keys, {a: self.sanc("V2"), b: self.sanc("V3")}, use_shadow)
                self.assert_refused(a, b)
                with database.get_connection() as conn:
                    conn.execute("DELETE FROM disputed_product_pairs")
                    conn.execute("DELETE FROM title_fields")
                    conn.execute("DELETE FROM title_fields_shadow")
                    conn.commit()

    def test_v1_and_old_k2_without_marks(self):
        a, b = "Sanc M2442PH 23.8 черный", "Sanc M2442PH 23.8 белый"
        self.case({a: "sanc:m2442ph:23.8", b: "k2:sanc:m2442ph:23.8in"}, {a: self.sanc("V2"), b: self.sanc("V3")})
        self.assert_refused(a, b)

    def test_old_k2_component_and_system(self):
        a, b = "AMD Ryzen 5 5600GT BOX", "Ryzen 5 5600GT-3.6GHz/A520/RAM 8GB/SSD 512GB/no DVD/400W"
        fa = {"brand": "AMD", "model": "Ryzen 5 5600GT", "sku": None, "variants": [], "unbranded": False}
        self.case({a: "k2:amd:ryzen55600gt", b: "k2:amd:ryzen55600gt:512gb"}, {a: fa, b: fa})
        self.assert_refused(a, b)

    def test_legacy_keys_with_different_capacity_without_fields(self):
        a, b = "Телефон X 128", "Телефон X 256"
        self.case({a: "brand:x:128gb", b: "brand:x:256gb"}, {a: {"brand": "Brand", "model": "X", "variants": []},
                                                             b: {"brand": "Brand", "model": "X", "variants": []}})
        self.assert_refused(a, b)

    def test_group_member_conflict_blocks_whole_merge(self):
        """Сама пара не спорит, но второй ключ переехал бы вместе с участником другой ревизии."""
        a, b, member = 'Sanc M2442PH V2', 'Sanc M2442PH черный', 'Sanc M2442PH V3 белый'
        self.case({a: "sanc:m2442ph v2:23.8", b: "sanc:m2442ph:23.8", member: "sanc:m2442ph:23.8"},
                  {a: self.sanc("V2"), b: self.sanc(None), member: self.sanc("V3")})
        self.assert_refused(a, b)

    def test_member_without_fields_checked_by_title(self):
        a, b, member = 'Sanc M2442PH V2', 'Sanc M2442PH черный', 'Монитор Sanc M2442PH V3'
        self.case({a: "sanc:m2442ph v2:23.8", b: "sanc:m2442ph:23.8", member: "sanc:m2442ph:23.8"},
                  {a: self.sanc("V2"), b: self.sanc(None)})
        self.assert_refused(a, b)

    def memory(self, gb):
        return {"brand": "Samsung", "model": "Galaxy S24", "sku": None, "edition": None,
                "variants": [{"value": gb, "unit": "gb"}], "unbranded": False}

    def test_member_without_fields_capacity_on_either_side(self):
        """Вердикт по паре верен, но группа тянет за собой товар с другим объёмом в названии (C45RR-01)."""
        a, b, hidden = "Samsung Galaxy S24 128GB black", "Samsung Galaxy S24 128GB white", "Samsung Galaxy S24 256GB blue"
        sides = {"во второй группе": {a: "samsung:s24-a", b: "samsung:s24-b", hidden: "samsung:s24-b"},
                 "в первой группе": {a: "samsung:s24-a", b: "samsung:s24-b", hidden: "samsung:s24-a"}}
        for name, keys in sides.items():
            with self.subTest(name):
                self.case(keys, {a: self.memory(128), b: self.memory(128)}, False)
                self.assert_refused(a, b)
                with database.get_connection() as conn:
                    conn.execute("DELETE FROM disputed_product_pairs")
                    conn.commit()

    def test_member_without_fields_diagonal_and_system(self):
        mon = {"brand": "AOC", "model": "24B1", "sku": None, "edition": None, "variants": [], "unbranded": False}
        a, b, hidden = 'Монитор AOC 24B1 24"', 'AOC 24B1 24" черный', 'AOC 24B1 27"'
        self.case({a: "aoc:a", b: "aoc:b", hidden: "aoc:b"}, {a: mon, b: mon}, False)
        self.assert_refused(a, b)
        chip = {"brand": "AMD", "model": "Ryzen 5 5600GT", "sku": None, "edition": None, "variants": [], "unbranded": False}
        c, d, pc = "AMD Ryzen 5 5600GT BOX", "Процессор AMD Ryzen 5 5600GT", "Ryzen 5 5600GT/A520/RAM 8GB/SSD 512GB/400W"
        self.case({c: "amd:a", d: "amd:b", pc: "amd:b"}, {c: chip, d: chip}, False)
        self.assert_refused(c, d)

    def test_member_without_fields_capacity_against_legacy_key(self):
        a, b, hidden = "Телефон X белый", "Телефон X черный", "Телефон X 256GB"
        f = {"brand": "Brand", "model": "X", "variants": []}
        self.case({a: "brand:a:128gb", b: "brand:b", hidden: "brand:b"}, {a: f, b: f}, False)
        self.assert_refused(a, b)

    def test_member_without_fields_and_same_capacity_merges(self):
        a, b, other = "Samsung Galaxy S24 128GB black", "Samsung Galaxy S24 128GB white", "Смартфон Samsung Galaxy S24 128 ГБ"
        self.case({a: "samsung:s24-a", b: "samsung:s24-b", other: "samsung:s24-b"},
                  {a: self.memory(128), b: self.memory(128)}, False)
        database.enqueue_disputed_pair(a, b, "x")
        self.assertEqual(self.tick()["status"], "resolved")
        self.assertEqual(len(set(database.get_cached_canonical_keys_batch([a, b, other]).values())), 1)

    def test_model_extra_variant_is_not_conflict_with_title(self):
        """Название знает только объём: мощность от модели противоречием не считается."""
        a, b, other = "Пылесос X 2 л", "Пылесос X белый", "Пылесос X"
        f = {"brand": "Brand", "model": "X", "variants": [{"value": 2000, "unit": "вт"}]}
        self.assertFalse(product_key.identities_conflict([(a, f, "brand:a")], [(b, f, "brand:b"), (other, None, "brand:b")]))

    def cpu(self):
        return {"brand": "AMD", "model": "Ryzen 7 7700X", "sku": None, "edition": None, "variants": [], "unbranded": False}

    def test_member_without_fields_explicit_differences_on_either_side(self):
        """Третий участник без полей: BOX, длина, размер, память, Wi-Fi — и в первой, и во второй группе (C2R-03)."""
        cable = {"brand": "Cablexpert", "model": "PP12", "sku": None, "variants": [], "unbranded": False}
        watch = {"brand": "Garmin", "model": "Fenix 8", "sku": None, "variants": [], "unbranded": False}
        ram = {"brand": "Kingston", "model": "Fury", "sku": None, "variants": [], "unbranded": False}
        board = {"brand": "ASRock", "model": "B850M-X", "sku": None, "variants": [], "unbranded": False}
        ultra = {"brand": "Intel", "model": "Core Ultra 7 265K", "sku": None, "edition": None, "variants": [], "unbranded": False}
        cases = [("AMD Ryzen 7 7700X OEM", "Процессор AMD Ryzen 7 7700X OEM", "AMD Ryzen 7 7700X BOX", self.cpu()),
                 ("Intel Core Ultra 7 265K OEM", "Процессор Intel Core Ultra 7 265K OEM", "Intel Core Ultra 7 265K BOX", ultra),
                 ("Патч-корд Cablexpert PP12 1 м", "Patch cord Cablexpert PP12 1m", "Патч-корд Cablexpert PP12 3 м", cable),
                 ("Смарт-часы Garmin Fenix 8 47mm", "Часы Garmin Fenix 8 47 мм", "Смарт-часы Garmin Fenix 8 43mm", watch),
                 ("Оперативная память Kingston KF560C30BBE-16", "Память DDR5 DIMM Kingston (KF560C30BBE-16)",
                  "Оперативная память Kingston KF432C16BB1/16", ram),
                 ("Материнская плата ASRock B850M-X", "ASRock B850M-X материнская плата", "Материнская плата ASRock B850M-X WIFI", board)]
        for a, b, hidden, f in cases:
            for side in ("old-b", "old-a"):
                with self.subTest(hidden=hidden, side=side):
                    keys = {a: "x:a", b: "x:b", hidden: "x:b" if side == "old-b" else "x:a"}
                    self.case(keys, {a: f, b: f}, False)
                    self.assert_refused(a, b)
                    with database.get_connection() as conn:
                        conn.execute("DELETE FROM disputed_product_pairs")
                        conn.commit()

    def test_core_ultra_third_oem_member_still_merges(self):
        a, b, other = "Intel Core Ultra 7 265K OEM", "Процессор Intel Core Ultra 7 265K OEM", "Intel Core Ultra 7 265K Tray"
        f = {"brand": "Intel", "model": "Core Ultra 7 265K", "sku": None, "edition": None, "variants": [], "unbranded": False}
        self.case({a: "intel:a", b: "intel:b", other: "intel:b"}, {a: f, b: f}, False)
        database.enqueue_disputed_pair(a, b, "x")
        self.assertEqual(self.tick()["status"], "resolved")
        self.assertEqual(len(set(database.get_cached_canonical_keys_batch([a, b, other]).values())), 1)

    def test_compatible_legacy_pair_still_merges(self):
        a, b = "Samsung Galaxy S24 256GB black", "Samsung S24 256 ГБ"
        self.case({a: "samsung:galaxy s24:256gb", b: "samsung:sm-s921b:256gb"}, {a: fields(), b: fields()})
        database.enqueue_disputed_pair(a, b, "x")
        self.assertEqual(self.tick()["status"], "resolved")
        keys = database.get_cached_canonical_keys_batch([a, b])
        self.assertEqual(keys[a], keys[b])


class ShadowMetricsTest(_DbCase):
    def test_aggregates_cover_all_rows(self):
        for i in range(200):
            database.record_normalize_shadow("matched", f"p{i}", f"s{i}", f"t{i}")
            database.record_normalize_shadow("matched", f"p{i}", f"s{i}", f"t{i}")
        for i in range(201):
            database.record_normalize_shadow("mismatch", f"x{i}", f"y{i}", f"u{i}")
        summary = database.normalize_shadow_summary()
        self.assertEqual((summary["total"], summary["matched"], summary["mismatch"]), (601, 400, 201))
        self.assertAlmostEqual(summary["match_rate"], 400 / 601, places=3)
        self.assertEqual(len(summary["breakdown"]), 200)
        self.assertEqual(monitoring.normalize_shadow_section()["status"], monitoring.DEGRADED)

    def test_failures_are_visible_and_limit_health(self):
        a, b = "Samsung Galaxy S24 256GB", "Samsung Galaxy S25 256GB"
        shadow([a], {a: product_key.build_key(fields(), a)}, [{"title": a, **fields()}])
        shadow([b], {b: product_key.build_key(fields("Galaxy S25"), b)}, None)
        data = monitoring.normalize_shadow_section()
        self.assertEqual((data["total"], data["match_rate"], data["coverage"]), (2, 1.0, 0.5))
        self.assertEqual(data["by_kind"]["failed"], 1)
        self.assertEqual(data["status"], monitoring.DEGRADED)

    def test_each_outcome_has_its_kind(self):
        t1, t2, t3 = "Samsung Galaxy S24 256GB", "Без бренда 1", "Без ответа"
        shadow([t1, t2, t3], {t1: "k2:samsung:galaxys24:256gb"},
               [{"title": t1, **fields()}, {"title": t2, "brand": None, "model": None, "unbranded": True}])
        shadow([t1], {t1: "k2:x:y"}, "мусор")
        with patch("local_ai.pause_reason", return_value="выкл"):
            shadow([t1], {t1: "k2:x:y"}, None)
        kinds = database.normalize_shadow_summary()["by_kind"]
        self.assertEqual({k: kinds.get(k) for k in ("matched", "both_empty", "missing", "invalid", "paused", "scheduled")},
                         {"matched": 1, "both_empty": 1, "missing": 1, "invalid": 1, "paused": 1, "scheduled": 5})

    def test_exception_marks_rest_as_failed(self):
        async def boom(*args, **kwargs):
            raise RuntimeError("x")
        with patch.object(ai_service, "call_local_api", boom):
            asyncio.run(ai_service._run_shadow_normalize(["a", "b"], CREDS, {}, "k2"))
        self.assertEqual(database.normalize_shadow_summary()["by_kind"]["failed"], 2)


class MigrationIdentityAndBaselineTest(_DbCase):
    OLD = "unknown:chechevitsa krasnaya:1kg"
    LENTIL, BEAN = "Чечевица красная кг", "Фасоль красная кг"
    UNBRANDED = {"brand": None, "model": "красная", "sku": None, "unbranded": True, "variants": []}

    def add_product(self, pid, title, shop, key):
        database.save_or_update_product({"id": pid, "shop": shop, "city": "Алматы", "title": title, "price": 1000,
                                         "canonical_key": key, "url": f"https://example.kz/{pid}"})

    def migrate(self, **kw):
        return asyncio.run(mig.run_migration(str(database.DB_PATH), "http://fake", "m", **kw))

    def seed(self):
        database.save_cached_canonical_keys_batch({self.LENTIL: self.OLD, self.BEAN: self.OLD})
        self.add_product("p1", self.LENTIL, "shop1", self.OLD)
        self.add_product("p2", self.BEAN, "shop2", self.OLD)

    def test_confirmed_no_identity_drops_old_comparison_and_repeat_is_stable(self):
        self.seed()
        database.save_title_fields_batch({self.LENTIL: self.UNBRANDED, self.BEAN: self.UNBRANDED}, "k2", "local", "m")
        first = self.migrate(rescore_only=True, check_pairs=True)
        self.assertEqual(database.get_cached_canonical_keys_batch([self.LENTIL, self.BEAN]), {})
        with database.get_connection() as conn:
            keys = [r[0] for r in conn.execute("SELECT canonical_key FROM products ORDER BY id")]
        self.assertEqual(len(set(keys)), 2, "две позиции больше не в одной группе")
        self.assertTrue(all(k.startswith("nokey:") for k in keys))
        self.assertEqual(first["no_identity_titles"], 2)
        self.assertEqual(first["pairs_comparison"]["new_pairs_count"], 0)
        second = self.migrate(rescore_only=True)
        self.assertEqual(second["no_identity_titles"], 2)
        with database.get_connection() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT canonical_key FROM products ORDER BY id")], keys)

    def test_model_failure_and_skip_keep_old_key_and_retry(self):
        self.seed()

        async def fail(prompt, *a, **k):
            return None
        with patch.object(ai_service, "call_local_api", fail):
            report = self.migrate()
        self.assertEqual((report["failed_titles"], report["no_identity_titles"]), (2, 0))

        async def partial(prompt, *a, **k):
            return {"items": [{"title": self.LENTIL, **self.UNBRANDED}]}
        with patch.object(ai_service, "call_local_api", partial):
            report = self.migrate()
        self.assertEqual((report["missing_titles"], report["no_identity_titles"]), (1, 1))
        self.assertEqual(database.get_cached_canonical_keys_batch([self.BEAN]), {self.BEAN: self.OLD})
        self.assertEqual(report["run"]["cumulative"]["failed_titles"], 2)
        self.assertEqual(report["run"]["cumulative"]["missing_titles"], 1)

    def test_bad_item_keeps_identity_and_is_retried(self):
        """Обрезанный элемент, неверные типы, неопределённый unbranded: кэш и V2 прежние, название на повторе."""
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.add_product("phone", title, "shop1", old)
        database.save_cached_canonical_keys_batch({title: old})
        with database.get_connection() as conn:
            offer = tuple(conn.execute("SELECT id, product_id FROM offers").fetchone())
        bad = [{"title": title}, {"title": title, "brand": ["x"], "model": "m"},
               {"title": title, "brand": None, "model": None}, {"title": title, "brand": None, "model": "m", "unbranded": "?"}]
        for item in bad:
            with self.subTest(item=item):
                async def reply(*a, **k):
                    return {"items": [item]}
                with patch.object(ai_service, "call_local_api", reply):
                    report = self.migrate()
                self.assertEqual(database.get_cached_canonical_keys_batch([title]), {title: old})
                self.assertEqual((report["no_identity_titles"], report["incomplete_titles"], report["missing_titles"]), (0, 1, 1))
                self.assertEqual(database.get_title_fields_for_titles([title]), {})
                with database.get_connection() as conn:
                    self.assertEqual(tuple(conn.execute("SELECT id, product_id FROM offers").fetchone()), offer)

        async def good(*a, **k):
            return {"items": [{"title": title, **fields()}]}
        with patch.object(ai_service, "call_local_api", good):
            self.assertEqual(self.migrate()["with_k2_key"], 1)
        self.assertTrue(database.get_cached_canonical_keys_batch([title])[title].startswith("k2:"))

    def test_stored_incomplete_fields_are_refetched_not_cleared(self):
        """Записи прежних прогонов: все поля пусты. Пересборка ключ не снимает, обычный проход спросит заново."""
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.add_product("phone", title, "shop1", old)
        database.save_cached_canonical_keys_batch({title: old})
        database.save_title_fields_batch({title: {k: None for k in product_key.FIELDS_SCHEMA}}, "k2", "local", "m")
        report = self.migrate(rescore_only=True)
        self.assertEqual((report["no_identity_titles"], report["incomplete_titles"]), (0, 1))
        self.assertEqual(database.get_cached_canonical_keys_batch([title]), {title: old})

        async def good(*a, **k):
            return {"items": [{"title": title, **fields()}]}
        with patch.object(ai_service, "call_local_api", good):
            self.assertEqual(self.migrate()["with_k2_key"], 1)

    def two_titles(self):
        a, b = "Samsung Galaxy S24 256GB A", "Samsung Galaxy S24 256GB B"
        database.save_cached_canonical_keys_batch({a: "samsung:a", b: "samsung:b"})
        return a, b

    def test_interrupt_after_commit_keeps_counters_once(self):
        self.two_titles()

        async def reply(titles, *a, **k):
            return {t: fields() for t in titles}, 0
        original = mig.save_migrated_batch

        def stop(*a, **k):
            original(*a, **k)
            raise KeyboardInterrupt()
        with patch.object(mig, "fetch_fields_batch", reply), patch.object(mig, "save_migrated_batch", stop):
            with self.assertRaises(KeyboardInterrupt):
                self.migrate(batch_size=1, run_id="r")
        with patch.object(mig, "fetch_fields_batch", reply):
            report = self.migrate(batch_size=1, run_id="r")
        cumulative = report["run"]["cumulative"]
        self.assertEqual((cumulative["api_calls"], cumulative["api_started"], cumulative["api_unfinished"]), (2, 2, 0))
        self.assertEqual(report["api_calls"], 1, "этот запуск")

    def test_interrupt_during_model_call_is_unfinished_attempt(self):
        self.two_titles()

        async def boom(titles, *a, **k):
            raise KeyboardInterrupt()

        async def reply(titles, *a, **k):
            return {t: fields() for t in titles}, 0
        with patch.object(mig, "fetch_fields_batch", boom), self.assertRaises(KeyboardInterrupt):
            self.migrate(batch_size=1, run_id="r")
        with patch.object(mig, "fetch_fields_batch", reply):
            cumulative = self.migrate(batch_size=1, run_id="r")["run"]["cumulative"]
        self.assertEqual((cumulative["api_calls"], cumulative["api_started"], cumulative["api_unfinished"]), (2, 3, 1))

    def test_failed_save_rolls_back_counters_with_data(self):
        a, _ = self.two_titles()

        async def reply(titles, *x, **k):
            return {t: fields() for t in titles}, 0
        with patch.object(mig, "fetch_fields_batch", reply), \
                patch.object(database, "rekey_titles_conn", side_effect=RuntimeError("сбой")), self.assertRaises(RuntimeError):
            self.migrate(batch_size=1, run_id="r")
        with database.get_connection() as conn:
            row = conn.execute("SELECT api_calls, api_started FROM k2_migration_runs").fetchone()
        self.assertEqual(tuple(row), (0, 1), "данных нет — вызов завершён неизвестно чем")
        self.assertEqual(database.get_title_fields_for_titles([a]), {})

    def test_interrupt_in_batch_without_fields_keeps_failure(self):
        self.two_titles()

        async def fail(titles, *a, **k):
            return {}, 1
        with patch.object(mig, "fetch_fields_batch", fail):
            cumulative = self.migrate(batch_size=1, run_id="r")["run"]["cumulative"]
        self.assertEqual((cumulative["api_calls"], cumulative["api_failures"], cumulative["failed_titles"]), (2, 2, 2))

    OLD_LEDGER = """CREATE TABLE k2_migration_runs (
        run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, api_calls INTEGER NOT NULL DEFAULT 0,
        api_failures INTEGER NOT NULL DEFAULT 0, failed_titles INTEGER NOT NULL DEFAULT 0,
        missing_titles INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0)"""

    def old_ledger(self, title, old):
        """Проход прежней версии скрипта: точная схема учёта, снимок и счётчики."""
        database.save_cached_canonical_keys_batch({title: old})
        with database.get_connection() as conn:
            conn.execute(self.OLD_LEDGER)
            conn.execute("""CREATE TABLE k2_migration_baseline (
                run_id TEXT NOT NULL, title TEXT NOT NULL, old_key TEXT NOT NULL, PRIMARY KEY (run_id, title))""")
            conn.execute("INSERT INTO k2_migration_runs VALUES ('old', '2026-10-30T15:00:00Z', 3, 1, 2, 1, 7.0)")
            conn.execute("INSERT INTO k2_migration_baseline VALUES ('old', ?, ?)", (title, old))
            conn.commit()

    def ledger_columns(self):
        with database.get_connection() as conn:
            return [r[1] for r in conn.execute("PRAGMA table_info(k2_migration_runs)")]

    def test_old_ledger_resumes_with_counters_and_baseline(self):
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.old_ledger(title, old)

        async def good(*a, **k):
            return {"items": [{"title": title, **fields()}]}
        with patch.object(ai_service, "call_local_api", good):
            report = self.migrate(run_id="old", check_pairs=True)
        cumulative = report["run"]["cumulative"]
        self.assertEqual((cumulative["api_calls"], cumulative["api_started"], cumulative["api_unfinished"]), (4, 4, 0))
        self.assertEqual((cumulative["api_failures"], cumulative["failed_titles"], cumulative["missing_titles"]), (1, 2, 1))
        self.assertEqual((report["run"]["baseline_created_at"], report["run"]["baseline_titles"]), ("2026-10-30T15:00:00Z", 1))
        again = self.migrate(run_id="old", check_pairs=True)                  # повторное открытие обновлённой схемы
        self.assertEqual(again["run"]["cumulative"]["api_started"], 4)
        self.assertEqual(self.ledger_columns().count("api_started"), 1)

    def test_old_ledger_dry_run_changes_nothing(self):
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.old_ledger(title, old)
        before = self.ledger_columns()

        async def good(*a, **k):
            return {"items": [{"title": title, **fields()}]}
        with patch.object(ai_service, "call_local_api", good):
            report = self.migrate(run_id="old", dry_run=True)
        self.assertEqual(self.ledger_columns(), before)
        cumulative = report["run"]["cumulative"]
        self.assertEqual((cumulative["api_calls"], cumulative["api_started"], cumulative["api_unfinished"]), (4, 4, 0))
        with database.get_connection() as conn:
            self.assertEqual(tuple(conn.execute("SELECT api_calls FROM k2_migration_runs").fetchone()), (3,))

    def test_interrupted_ledger_upgrade_is_atomic(self):
        """Настоящее подключение: обрыв между добавлением колонки и заполнением не оставляет полуобновлённой схемы."""
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.old_ledger(title, old)
        real = mig.get_db_connection(str(database.DB_PATH))

        class Interrupted:
            def execute(self, sql, *args):
                if sql.startswith("UPDATE k2_migration_runs SET api_started"):
                    raise KeyboardInterrupt()
                return real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(real, name)
        try:
            with self.assertRaises(KeyboardInterrupt):
                mig.load_baseline(Interrupted(), "old", read_only=False)
        finally:
            real.close()
        self.assertNotIn("api_started", self.ledger_columns(), "откат убрал и колонку")
        report = self.migrate(run_id="old", rescore_only=True, check_pairs=True)
        cumulative = report["run"]["cumulative"]
        self.assertEqual((cumulative["api_calls"], cumulative["api_started"], cumulative["api_unfinished"]), (3, 3, 0))
        self.assertEqual((cumulative["api_failures"], cumulative["failed_titles"], cumulative["missing_titles"]), (1, 2, 1))
        self.assertEqual(report["run"]["baseline_titles"], 1)

    def partial_upgrade(self, title, old, started=0):
        """Состояние после обрыва в прежней версии: колонка уже есть, значения не заполнены."""
        self.old_ledger(title, old)
        with database.get_connection() as conn:
            conn.execute("ALTER TABLE k2_migration_runs ADD COLUMN api_started INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE k2_migration_runs SET api_started = ?", (started,))
            conn.commit()

    def test_partial_upgrade_from_previous_version_is_repaired(self):
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.partial_upgrade(title, old)
        dry = self.migrate(run_id="old", rescore_only=True, dry_run=True, check_pairs=True)["run"]["cumulative"]
        self.assertEqual(dry["api_unfinished"], 0)
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT api_started FROM k2_migration_runs").fetchone()[0], 0, "пробный прогон не пишет")
        report = self.migrate(run_id="old", rescore_only=True, check_pairs=True)["run"]["cumulative"]
        self.assertEqual((report["api_started"], report["api_unfinished"]), (3, 0))

    def test_real_unfinished_attempts_are_not_erased(self):
        title, old = "Samsung Galaxy S24 256GB", "samsung:galaxy s24:256gb"
        self.partial_upgrade(title, old, started=5)
        report = self.migrate(run_id="old", rescore_only=True, check_pairs=True)["run"]["cumulative"]
        self.assertEqual((report["api_started"], report["api_unfinished"]), (5, 2))

    def test_rescore_of_old_keys_keeps_marks(self):
        """Настоящая пересборка: в кэше и артикулах прежний ключ без метки, все порядки и размеры пачек (C2R-02)."""
        import hashlib
        import itertools
        cpu = {"brand": "AMD", "model": "Ryzen 7 7700X", "sku": None, "edition": None, "variants": [], "unbranded": False}
        titles = ["AMD Ryzen 7 7700X BOX black", "AMD Ryzen 7 7700X BOX white", "Процессор AMD Ryzen 7 7700X OEM"]
        old = "k2:amd:ryzen77700x"
        for order, size, dry in itertools.product(itertools.permutations(titles), (1, 2, 3), (False, True)):
            with self.subTest(order=order, size=size, dry=dry):
                with database.get_connection() as conn:
                    for table in ("title_fields", "title_articles", "title_canonical_cache", "products", "offers",
                                  "k2_migration_runs", "k2_migration_baseline"):
                        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
                            conn.execute(f"DELETE FROM {table}")
                    conn.commit()
                for i, t in enumerate(order):
                    self.add_product(f"p{i}", t, f"shop{i}", old)
                    database.save_title_fields_batch({t: cpu}, "k2", "local", "m")
                    database.save_cached_canonical_keys_batch({t: old})
                    database.save_title_articles_batch([(a, hashlib.sha256(t.encode()).hexdigest(), old) for a in product_key.article_tokens(cpu)])
                self.migrate(rescore_only=True, batch_size=size, dry_run=dry)
                if dry:
                    self.assertEqual(set(database.get_cached_canonical_keys_batch(titles).values()), {old}, "пробный прогон не пишет")
                    continue
                self.migrate(rescore_only=True, batch_size=size)                 # повтор ничего не меняет
                keys = database.get_cached_canonical_keys_batch(titles)
                self.assertEqual(keys[titles[0]], "k2:amd:ryzen77700x.box")
                self.assertEqual(keys[titles[1]], keys[titles[0]])
                self.assertEqual(keys[titles[2]], old)
                with database.get_connection() as conn:
                    pids = {r[0]: r[1] for r in conn.execute("SELECT p.title, o.product_id FROM products p JOIN offers o ON o.id = 'off_' || p.id")}
                self.assertEqual(pids[titles[0]], pids[titles[1]])
                self.assertNotEqual(pids[titles[0]], pids[titles[2]])

    def test_resume_compares_with_full_baseline(self):
        """Как на стенде: часть уже переведена в прошлом запуске, отчёт всё равно по всему снимку."""
        a, b, c = "Samsung Galaxy S24 256GB A", "Samsung Galaxy S24 256GB B", "Samsung Galaxy S25 256GB"
        old = {a: "samsung:s24", b: "samsung:s24", c: "samsung:s25"}
        database.save_cached_canonical_keys_batch(old)
        for pid, (t, shop) in enumerate(((a, "s1"), (b, "s2"), (c, "s3"))):
            self.add_product(f"p{pid}", t, shop, old[t])

        async def only_a(prompt, *x, **k):
            return {"items": [{"title": a, **fields()}]}

        async def rest(prompt, *x, **k):
            return {"items": [{"title": b, **fields()}, {"title": c, **fields("Galaxy S25")}]}
        with patch.object(ai_service, "call_local_api", only_a):
            self.migrate(limit=1, batch_size=1)
        with patch.object(ai_service, "call_local_api", rest):
            report = self.migrate(check_pairs=True)
        self.assertEqual(report["total_titles"], 2, "этот запуск")
        self.assertEqual(report["run"]["baseline_titles"], 3)
        self.assertEqual(report["run"]["actual_k2_keys"], 3)
        self.assertEqual(report["pairs_comparison"]["old_pairs_count"], 1)
        self.assertEqual(report["pairs_comparison"]["retained_pairs_count"], 1)

    def test_remaining_legacy_keys_count_as_actual(self):
        self.seed()

        async def fail(prompt, *a, **k):
            return None
        with patch.object(ai_service, "call_local_api", fail):
            report = self.migrate(check_pairs=True)
        self.assertEqual(report["run"]["actual_legacy_keys"], 2)
        self.assertEqual(report["pairs_comparison"]["retained_pairs_count"], 1, "сравнение ещё работает по старому ключу")

    def test_baseline_is_unchanged_and_dry_run_writes_nothing(self):
        self.seed()
        dry = self.migrate(dry_run=True)
        self.assertEqual(dry["run"]["baseline_titles"], 2)
        with database.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'k2_migration_runs'").fetchone())

        async def fail(prompt, *a, **k):
            return None
        with patch.object(ai_service, "call_local_api", fail):
            self.migrate()
        database.save_cached_canonical_keys_batch({"Новое название": "x:y"})
        with patch.object(ai_service, "call_local_api", fail):
            self.assertEqual(self.migrate()["run"]["baseline_titles"], 2)
            self.assertEqual(self.migrate(run_id="new")["run"]["baseline_titles"], 3)

    def test_verified_set_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "set.json"
            path.write_text(json.dumps({"version": 7, "pairs": [
                {"a": "A", "b": "B", "same": True}, {"a": "A", "b": "C", "same": True},
                {"a": "A", "b": "D", "same": False}, {"a": "E", "b": "F", "same": False},
                {"a": "A", "b": "нет в базе", "same": True}, {"a": "G", "b": "H", "same": True},
                {"a": "A", "b": "B", "same": None}]}))
            out = mig.evaluate_verified_pairs(str(path), {"A": "k", "B": "k", "C": "x", "D": "k", "E": "e", "F": "f", "G": "", "H": ""})
        self.assertEqual((out["same_found"], out["same_lost"], out["different_kept_apart"], out["absent_in_db"]), (1, 2, 1, 1))
        self.assertEqual(out["false_merges"], [{"a": "A", "b": "D", "key": "k"}])
        self.assertEqual(out["recall"], 0.3333)
        self.assertEqual(out["undecided"], 1)


class C2QualityReportTest(unittest.TestCase):
    """Оценка C2: одна формула для обеих сторон и реестр, где каждая пара имеет ровно одну причину (C2A-01/02)."""
    FIELDS = {"brand": "B", "model": "M", "sku": None, "variants": [], "unbranded": False}

    def data(self):
        fields = self.FIELDS
        dropped = [
            {"a": "a1", "b": "b1", "old": "b:m", "new_a": "k2:b:m", "new_b": "b:m", "fields_a": fields, "fields_b": None},
            {"a": "a2", "b": "b2", "old": "b:m", "new_a": "k2:b:m:128gb", "new_b": "k2:b:m:256gb", "fields_a": fields, "fields_b": fields},
            {"a": "a3", "b": "b3", "old": "b:m", "new_a": "k2:b:m", "new_b": "k2:b:m:256gb", "fields_a": fields, "fields_b": fields},
            {"a": "a4", "b": "b4", "old": "b:m", "new_a": "k2:b:m.v2", "new_b": "k2:b:m.v3", "fields_a": fields, "fields_b": fields},
            {"a": "x CODE12345", "b": "y CODE12345", "old": "b:m", "new_a": "k2:b:m", "new_b": "k2:b:n", "fields_a": fields, "fields_b": fields},
            {"a": "a6", "b": "b6", "old": "b:m", "new_a": "k2:b:m", "new_b": "k2:b:n", "fields_a": fields, "fields_b": fields},
            {"a": "a7", "b": "b7", "old": "b:m", "new_a": "k2:b:m", "new_b": "k2:b:m.system", "fields_a": fields, "fields_b": fields},
        ]
        added = [{"a": "c1", "b": "d1", "new": "k2:b:m", "old_a": "b:m x", "old_b": "b:mx", "fields_a": fields, "fields_b": fields},
                 {"a": "c2", "b": "d2", "new": "k2:b:m", "old_a": "b:m:1", "old_b": "b:m:2", "fields_a": fields, "fields_b": fields},
                 {"a": "c3", "b": "d3", "new": "k2:b:m", "old_a": "b:a", "old_b": "b:c", "fields_a": fields, "fields_b": fields},
                 {"a": "c4", "b": "d4", "new": "k2:b:m", "old_a": "b:a", "old_b": "b:c", "fields_a": fields,
                  "fields_b": {**fields, "model": "Z"}}]
        inputs = {"r1": {"old": "b:r", "new": "k2:b:r"}, "r2": {"old": "b:r", "new": "k2:b:r"}}
        return {"dropped": dropped, "added": added, "eval_inputs": inputs, "key_counts": {"k2": 1, "legacy": 1, "empty": 0},
                "pairs": {"before": 8, "after": 5, "retained": 1, "dropped": len(dropped), "added": len(added)}}

    def sample(self):
        rows = [("a1", "b1", "same", "dropped_mixed"), ("a2", "b2", "different", "dropped_both_k2"),
                ("c1", "d1", "same", "added"), ("c3", "d3", "different", "added"),
                ("c4", "d4", "packaging", "added"), ("c2", "d2", "uncertain", "added"),
                ("r1", "r2", "same", "retained_pool")]
        return {"pairs": [{"a": a, "b": b, "label": label, "strata": [stratum]} for a, b, label, stratum in rows]}

    def test_registry_has_one_cause_per_pair_and_sums_match(self):
        report, registry = c2.build(self.data(), self.sample())
        self.assertEqual(report["registry_counts"]["dropped"],
                         {"missing_fields_one_side": 1, "missing_fields_both": 0, "legacy_with_fields": 0, "no_identity": 0, "variant_one_side": 1, "variant_conflict": 1,
                          "revision_differs": 1, "system_vs_component": 1, "identity_differs_shared_code": 1,
                          "identity_differs_other": 1})
        self.assertEqual(report["registry_counts"]["added"],
                         {"old_format_only": 1, "old_variant_differs": 1, "same_identity_new_old_empty": 0,
                          "own_keys_equal_old_identity_differs": 1, "linked_by_article": 1})
        self.assertEqual(len(registry["dropped"]), 7)

    def test_registry_rejects_wrong_total(self):
        data = self.data()
        data["pairs"]["dropped"] = 99
        with self.assertRaises(AssertionError):
            c2.registry(data)

    def test_same_formula_for_baseline_and_candidate(self):
        report, _ = c2.build(self.data(), self.sample())
        sample = report["random_sample"]
        # Прежний кэш: a1 вместе, a2 вместе (ложно), c1 и c3 врозь, r вместе. k2: a1 и a2 врозь, c1 и c3 вместе (c3 ложно), r вместе
        # Решение владельца: упаковка — разные товары, поэтому c4 у k2 — ложная склейка
        self.assertEqual(sample["baseline"], {"tp": 2, "fn": 1, "fp": 1, "tn": 2, "uncertain": 1, "packaging": 0})
        self.assertEqual(sample["candidate"], {"tp": 2, "fn": 1, "fp": 2, "tn": 1, "uncertain": 1, "packaging": 0})
        self.assertEqual(sorted(m["a"] for m in report["candidate_false_merges"]), ["c3", "c4"])
        self.assertEqual([m["a"] for m in report["candidate_lost_true_pairs"]], ["a1"])

    def test_undecided_labels_never_count_and_packaging_is_a_scenario(self):
        report, _ = c2.build(self.data(), self.sample())
        added = {name: report["weighted"][name]["share_true_by_stratum"]["added"]
                 for name in ("packaging_excluded", "packaging_same", "packaging_different")}
        self.assertEqual((added["packaging_excluded"]["n"], added["packaging_excluded"]["of"]), (1, 2))
        self.assertEqual((added["packaging_same"]["n"], added["packaging_same"]["of"]), (2, 3))
        self.assertEqual((added["packaging_different"]["n"], added["packaging_different"]["of"]), (1, 3))

    def test_title_absent_from_snapshot_is_not_a_known_pair(self):
        """Нет в снимке и пустой ключ после no_identity — разные случаи (P3 проверки Codex)."""
        data, sample = self.data(), self.sample()
        data["eval_inputs"].update({"n1": {"old": None, "new": None}, "n2": {"old": None, "new": None},
                                    "e1": {"old": "b:e", "new": ""}, "e2": {"old": "b:e", "new": ""}})
        sample["pairs"] += [{"a": "n1", "b": "n2", "label": "same", "strata": ["old_set"]},
                            {"a": "e1", "b": "e2", "label": "same", "strata": ["old_set"]}]
        report, _ = c2.build(data, sample)
        self.assertEqual(report["labels"]["without_keys"], 1)

    def test_rules_now_uses_current_rules_with_same_labels(self):
        """Ключи пересчитываются из полей, метки прежние; без полей остаётся прежний ключ."""
        cpu = {"brand": "AMD", "model": "Ryzen 7 7700", "sku": None, "variants": [], "unbranded": False}
        data = {"dropped": [{"a": "t1", "b": "t2", "old": "x:y", "new_a": "k2:x:y", "new_b": "x:y", "fields_a": self.FIELDS, "fields_b": None}],
                "added": [{"a": "AMD Ryzen 7 7700 BOX", "b": "AMD Ryzen 7 7700 OEM", "new": "k2:amd:ryzen77700", "old_a": "a:b", "old_b": "a:c",
                           "fields_a": cpu, "fields_b": cpu}],
                "eval_inputs": {}, "pairs": {"retained": 0, "added": 1, "dropped": 1}, "key_counts": {}}
        sample = {"pairs": [{"a": "AMD Ryzen 7 7700 BOX", "b": "AMD Ryzen 7 7700 OEM", "label": "packaging", "strata": ["holdout_added"]},
                            {"a": "t1", "b": "t2", "label": "same", "strata": ["dropped_mixed"]}]}
        out = c2.rules_now(data, sample)
        self.assertEqual((out["strata"]["holdout_added"]["before_fp"], out["strata"]["holdout_added"]["now_fp"]), (1, 0))
        self.assertEqual(out["strata"]["dropped_mixed"]["now_tp"], 0, "без полей пара не восстанавливается")
        self.assertEqual(out["added_still_together"], 0)

    def test_known_pairs_are_counted_as_census_not_as_fresh_share(self):
        """C3A-02: свежая доля применяется только к остатку; известная часть слоя считается как есть."""
        rows = [{"label": "same" if i < 1 else "different", "strata": ["dropped_both_k2"]} for i in range(10)]
        rows += [{"label": "same", "strata": [name]} for name in ("retained", "dropped_mixed", "added")]
        sizes = {"retained": 10, "dropped_mixed": 10, "dropped_both_k2": 110, "added": 10}
        known = {"dropped_both_k2": ["same"] * 8 + ["different"] + ["uncertain"]}
        out = c2.weighted_estimate(rows, sizes, False, known)
        layer = out["layers"]["dropped_both_k2"]
        self.assertEqual((layer["pool"], layer["true"], layer["false"], layer["open"]), (100, 18.0, 91.0, 1.0))
        self.assertEqual(out["baseline"]["true"], 38, "10 + 10 + 18, а не 110 × 10 %")
        self.assertGreaterEqual(layer["true_high"] - layer["true"], 1, "uncertain переписи входит в верхнюю границу")

    def test_excluded_packaging_is_open_in_sample_and_in_census(self):
        """Аудит C3A P3: упаковка, исключённая из сравнения, одинаково открыта в свежей и известной части."""
        rows = [{"label": "same", "strata": [n]} for n in ("retained", "dropped_mixed", "dropped_both_k2", "added")]
        rows.append({"label": "packaging", "strata": ["added"]})
        sizes = {"retained": 10, "dropped_mixed": 10, "dropped_both_k2": 10, "added": 101}
        layer = c2.weighted_estimate(rows, sizes, None, {"added": ["packaging"]})["layers"]["added"]
        self.assertEqual((layer["known_open"], layer["pool"], layer["drawn"], layer["drawn_open"]), (1, 100, 2, 1))
        self.assertEqual((layer["true"], layer["false"], layer["open"]), (50.0, 0.0, 51.0))

    def test_uncertain_counts_against_candidate_in_bounds(self):
        rows = [{"label": "same", "strata": [n]} for n in ("retained", "dropped_mixed", "dropped_both_k2", "added")]
        rows.append({"label": "uncertain", "strata": ["added"]})
        sizes = {"retained": 10, "dropped_mixed": 10, "dropped_both_k2": 10, "added": 10}
        out = c2.weighted_estimate(rows, sizes, False)
        self.assertEqual(out["layers"]["added"]["open"], 5.0)
        self.assertGreater(out["layers"]["added"]["false_high"], 5)
        self.assertLess(out["layers"]["added"]["true_low"], 5)

    def test_mixed_loss_without_fields_is_not_no_identity(self):
        """C3A-03: нет полей, есть поля без k2 и подтверждённое отсутствие идентичности — разные причины."""
        base = {"a": "a", "b": "b", "old": "x", "new_a": "k2:x", "fields_a": self.FIELDS}
        self.assertEqual(c2.dropped_cause({**base, "new_b": "x", "fields_b": None}), "missing_fields_one_side")
        self.assertEqual(c2.dropped_cause({**base, "new_b": "x", "fields_b": self.FIELDS}), "legacy_with_fields")
        self.assertEqual(c2.dropped_cause({**base, "new_b": "", "fields_b": self.FIELDS}), "no_identity")

    def test_labelled_sample_file_is_consistent(self):
        sample = json.loads((ROOT / "scripts" / "k2_labeled_sample.json").read_text(encoding="utf-8"))
        self.assertIn("OEM", sample["convention"]["packaging"])
        self.assertEqual({row["label"] for row in sample["pairs"]}, {"same", "different", "uncertain", "packaging"})
        seen = {frozenset((row["a"], row["b"])) for row in sample["pairs"]}
        self.assertEqual(len(seen), len(sample["pairs"]), "пара размечена один раз")
        undecided = {frozenset((row["a"], row["b"])) for row in PAIRS if row.get("same") is None}
        labels = {frozenset((row["a"], row["b"])): row["label"] for row in sample["pairs"]}
        self.assertEqual({labels[pair] for pair in undecided}, {"uncertain"}, "перенос не решает неопределённое за разметчика")


if __name__ == "__main__":
    unittest.main()
