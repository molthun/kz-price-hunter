"""Режим «Авто» выбирает живую модель из списка провайдера, а не имя, зашитое в код."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import unittest
from unittest.mock import patch

import ai_service
import model_choice as mc

GEMINI = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro",
          "gemini-3-flash-preview", "gemini-embedding-2", "gemini-3.8-flash-tts",
          "gemini-2.5-flash-image", "gemini-robotics-er-2-preview"]
OPENAI = ["gpt-5.6-sol", "gpt-4o-mini", "gpt-5-mini", "gpt-5.3-codex", "gpt-4o-mini-transcribe",
          "text-embedding-3-large", "gpt-image-2.5-flare", "gpt-realtime-2.1-mini"]


class ChoiceTest(unittest.TestCase):
    def test_newest_cheap_text_model_wins(self):
        self.assertEqual(mc.preferred(GEMINI), "gemini-3.8-flash")
        self.assertEqual(mc.preferred(OPENAI), "gpt-5-mini")

    def test_non_text_models_are_never_chosen(self):
        for name in ("gemini-embedding-2", "gemini-3.8-flash-tts", "gemini-2.5-flash-image",
                     "gpt-realtime-2.1-mini", "gpt-4o-mini-transcribe", "text-embedding-3-large"):
            with self.subTest(name=name):
                self.assertFalse(mc.is_text_model(name))
        self.assertNotIn(mc.preferred(OPENAI), ("gpt-realtime-2.1-mini", "gpt-4o-mini-transcribe"))

    def test_previews_are_skipped(self):
        self.assertFalse(mc.is_stable("gemini-3-flash-preview"))
        self.assertEqual(mc.preferred(["gemini-9-flash-preview", "gemini-2.5-flash"]), "gemini-2.5-flash")

    def test_expensive_model_is_taken_only_when_nothing_cheap_exists(self):
        self.assertEqual(mc.preferred(["gemini-2.5-pro", "gemini-3-pro"]), "gemini-3-pro")

    def test_choice_is_reproducible_regardless_of_order(self):
        self.assertEqual(mc.preferred(GEMINI), mc.preferred(list(reversed(GEMINI))))

    def test_versions_are_compared_as_numbers(self):
        self.assertEqual(mc.version_of("gemini-3.8-flash"), 3.8)
        self.assertEqual(mc.version_of("gpt-4o-mini"), 4.0)
        self.assertEqual(mc.version_of("странная-модель"), 0.0)
        self.assertEqual(mc.preferred(["gemini-10-flash", "gemini-9-flash"]), "gemini-10-flash")

    def test_empty_list_falls_back_to_what_was_configured(self):
        self.assertEqual(mc.preferred([], "запасная"), "запасная")
        self.assertIsNone(mc.preferred([]))


class AutoModelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ai_service._auto_model_cache.clear()
        self.cfg = {"gemini_api_key": "k", "openai_api_key": "k", "openai_api_base": "",
                    "gemini_model": "gemini-2.5-flash", "openai_model": "gpt-4o-mini"}

    async def test_auto_takes_a_live_model_from_the_list(self):
        listing = {"models": [{"name": n, "title": n} for n in GEMINI], "error": None}
        with patch.object(ai_service, "list_gemini_models", return_value=listing):
            self.assertEqual(await ai_service.auto_model("gemini", self.cfg), "gemini-3.8-flash")

    async def test_unavailable_list_keeps_the_configured_name(self):
        with patch.object(ai_service, "list_gemini_models",
                          return_value={"models": [], "error": "HTTP 403"}):
            self.assertEqual(await ai_service.auto_model("gemini", self.cfg), "gemini-2.5-flash")

    async def test_result_is_cached_and_visible_without_network(self):
        listing = {"models": [{"name": "gemini-3.8-flash", "title": "x"}], "error": None}
        with patch.object(ai_service, "list_gemini_models", return_value=listing) as listed:
            await ai_service.auto_model("gemini", self.cfg)
            await ai_service.auto_model("gemini", self.cfg)
            self.assertEqual(listed.await_count, 1, "список не спрашивается на каждый вызов")
        self.assertEqual(ai_service.auto_model_cached("gemini"), "gemini-3.8-flash")

    async def test_nothing_is_cached_before_the_first_call(self):
        self.assertIsNone(ai_service.auto_model_cached("openai"))


class AlternativesTest(unittest.TestCase):
    """Запасная модель берётся из списка провайдера, а не из перечня в коде."""

    def setUp(self):
        ai_service._auto_model_cache.clear()
        self.addCleanup(ai_service._auto_model_cache.clear)

    def cache(self, names):
        ai_service._auto_model_cache["gemini"] = {"model": names[0] if names else None, "at": 9e9,
                                                  "names": names, "from_list": bool(names), "error": None}

    def test_next_candidates_follow_the_chosen_one(self):
        self.cache(["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"])
        self.assertEqual(ai_service.alternatives("gemini", "gemini-3.8-flash"),
                         ["gemini-3.5-flash-lite", "gemini-2.5-flash"])

    def test_chosen_model_is_not_repeated(self):
        self.cache(["gemini-3.8-flash", "gemini-2.5-flash"])
        self.assertNotIn("gemini-2.5-flash", ai_service.alternatives("gemini", "gemini-2.5-flash"))

    def test_without_a_list_there_are_no_alternatives(self):
        self.assertEqual(ai_service.alternatives("gemini", "gemini-2.5-flash"), [])

    def test_unsuitable_models_never_become_alternatives(self):
        self.cache(["gemini-3.8-flash", "gemini-embedding-2", "gemini-3-flash-preview"])
        self.assertEqual(ai_service.alternatives("gemini", "gemini-3.8-flash"), [])
