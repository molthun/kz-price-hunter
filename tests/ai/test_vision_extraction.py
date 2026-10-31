"""
Тесты мультимодального конвейера Vision Extraction (P17-D / S2-E10).

Проверяет 8 обязательных сценариев:
1. Постер (один товар с крупной ценой);
2. Прайс-лист из 5+ товаров;
3. Зачёркнутая старая цена (старая > новой);
4. Отсечение чужой валюты (RUB/USD не публикуются как тенге);
5. Цена за единицу («за кг», «/кг»);
6. Размеры и объем памяти рядом с ценой (не путать 256GB с ценой);
7. Закрытая/размытая цена (палец/стикер/звёздочки -> «Цена не определена», publishable=False);
8. Защита от prompt injection внутри картинки (игнорирование команд).

А также:
- Fallback с локальной модели на Gemini при сбое локальной;
- Поддержка форматов передачи в extract_from_observation_async: байты, base64, локальный путь;
- Детерминированный fallback на OCR при отсутствии картинки.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports

import asyncio
import base64
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import ai_router
from domain.models import Availability, Condition
from domain.social_content import ContentObservation
from domain.vision_extraction import (
    MIN_VISION_PUBLISH_CONFIDENCE,
    STATUS_PRICE_DETERMINED,
    STATUS_PRICE_UNDETERMINED,
    VisionCandidate,
    VisionExtractionEngine,
)


class TestVisionExtractionScenarios(unittest.TestCase):
    """8 обязательных сценариев P17-D + валидация candidates."""

    def test_01_poster_single_item_large_price(self):
        """Сценарий 1: Рекламный постер с одним товаром и крупной ценой."""
        raw_items = [
            {
                "title": "Sony PlayStation 5 Slim 1TB",
                "price": 269990,
                "currency": "KZT",
                "confidence": 0.96,
                "is_price_occluded": False,
                "size_or_volume": "1TB",
                "raw_snippet": "PS5 Slim 1TB 269 990 ₸",
            }
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.raw_title, "Sony PlayStation 5 Slim 1TB")
        self.assertEqual(c.price, 269990.0)
        self.assertIsNone(c.old_price)
        self.assertEqual(c.currency, "KZT")
        self.assertTrue(c.price_determined)
        self.assertTrue(c.publishable)
        self.assertEqual(c.status_label, STATUS_PRICE_DETERMINED)

        offer = c.to_offer_candidate()
        self.assertIsNotNone(offer)
        self.assertEqual(offer.price, 269990.0)
        self.assertEqual(offer.raw_title, "Sony PlayStation 5 Slim 1TB")

    def test_02_price_list_multiple_items(self):
        """Сценарий 2: Прайс-лист из 5+ товаров."""
        raw_items = [
            {"title": "Apple iPhone 15 128GB Black", "price": 389990, "confidence": 0.95},
            {"title": "Samsung Galaxy S24 256GB Gray", "price": 429990, "confidence": 0.92},
            {"title": "Xiaomi 14 512GB White", "price": 399990, "confidence": 0.90},
            {"title": "Apple MacBook Air 13 M2 256GB", "price": 549990, "confidence": 0.94},
            {"title": "Apple iPad 10th Gen 64GB Blue", "price": 219990, "confidence": 0.88},
            {"title": "AirPods Pro 2 USB-C", "price": 119990, "confidence": 0.96},
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 6)
        for cand in candidates:
            self.assertTrue(cand.publishable)
            self.assertTrue(cand.price_determined)
            self.assertGreaterEqual(cand.confidence, MIN_VISION_PUBLISH_CONFIDENCE)
            self.assertIsNotNone(cand.to_offer_candidate())

    def test_03_strikethrough_old_price(self):
        """Сценарий 3: Зачёркнутая старая цена и проверка (старая > новой)."""
        raw_items = [
            {
                "title": "Dyson V15 Detect Extra",
                "price": 389990,
                "old_price": 469990,
                "is_discount": True,
                "currency": "KZT",
                "confidence": 0.91,
                "raw_snippet": "Dyson V15 ~~469 990~~ 389 990 ₸",
            },
            {
                # Некорректная старая цена: меньше текущей -> должна быть сброшена
                "title": "Roborock S8 Pro Ultra",
                "price": 600000,
                "old_price": 500000,
                "currency": "KZT",
                "confidence": 0.90,
            },
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 2)

        # 1. Корректная скидка
        c1 = candidates[0]
        self.assertEqual(c1.price, 389990.0)
        self.assertEqual(c1.old_price, 469990.0)
        self.assertTrue(c1.is_discount)
        offer1 = c1.to_offer_candidate()
        self.assertEqual(offer1.old_price, 469990.0)

        # 2. Невалидная скидка (old_price <= price) -> сброс old_price
        c2 = candidates[1]
        self.assertEqual(c2.price, 600000.0)
        self.assertIsNone(c2.old_price)
        self.assertFalse(c2.is_discount)
        offer2 = c2.to_offer_candidate()
        self.assertIsNone(offer2.old_price)

    def test_04_reject_foreign_currency(self):
        """Сценарий 4: Отсечение чужой валюты (RUB/USD не выдаются за тенге)."""
        raw_items = [
            {"title": "Яндекс Станция Миди", "price": 14990, "currency": "RUB", "confidence": 0.95},
            {"title": "Nike Air Force 1 07", "price": 115, "currency": "USD", "confidence": 0.90},
            {"title": "Samsung Galaxy A55", "price": 189990, "currency": "KZT", "confidence": 0.92},
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 3)

        # RUB
        rub_cand = candidates[0]
        self.assertFalse(rub_cand.publishable)
        self.assertEqual(rub_cand.rejection_reason, "foreign_currency:RUB")
        self.assertIsNone(rub_cand.to_offer_candidate())

        # USD
        usd_cand = candidates[1]
        self.assertFalse(usd_cand.publishable)
        self.assertEqual(usd_cand.rejection_reason, "foreign_currency:USD")
        self.assertIsNone(usd_cand.to_offer_candidate())

        # KZT - валиден
        kzt_cand = candidates[2]
        self.assertTrue(kzt_cand.publishable)
        self.assertIsNotNone(kzt_cand.to_offer_candidate())

    def test_05_unit_price_per_kg(self):
        """Сценарий 5: Цена за единицу веса/объёма («за кг»)."""
        raw_items = [
            {
                "title": "Яблоки Gala Казахстан",
                "price": 650,
                "currency": "KZT",
                "is_unit_price": True,
                "unit": "кг",
                "quantity": 1.0,
                "confidence": 0.92,
            }
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertTrue(c.is_unit_price)
        self.assertEqual(c.unit, "kg")
        self.assertEqual(c.price, 650.0)
        self.assertTrue(c.publishable)

    def test_06_size_and_volume_near_price(self):
        """Сценарий 6: Размеры и объем памяти рядом с ценой не путаются с ценой."""
        raw_items = [
            {
                "title": "Apple iPhone 15 Pro Max 512GB Natural",
                "size_or_volume": "512GB",
                "price": 689990,
                "currency": "KZT",
                "confidence": 0.95,
                "raw_snippet": "iPhone 15 Pro Max 512GB - 689 990 ₸",
            }
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.price, 689990.0)
        self.assertNotEqual(c.price, 512.0)
        self.assertEqual(c.size_or_volume, "512GB")
        self.assertTrue(c.publishable)

    def test_07_occluded_or_blurred_price(self):
        """Сценарий 7: Закрытая/размытая цена (палец, стикер, звездочки) -> Цена не определена."""
        raw_items = [
            {
                "title": "LG OLED 55 C3",
                "price": None,
                "is_price_occluded": True,
                "currency": "KZT",
                "confidence": 0.85,
                "raw_snippet": "LG OLED 55 C3 4** *** ₸ (цена закрыта стикером)",
            },
            {
                "title": "Xiaomi Robot Vacuum S10",
                "price": "9? ???",
                "currency": "KZT",
                "confidence": 0.80,
                "raw_snippet": "Xiaomi Robot 9? ??? ₸",
            },
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 2)
        for c in candidates:
            self.assertFalse(c.publishable)
            self.assertFalse(c.price_determined)
            self.assertIsNone(c.price)
            self.assertEqual(c.status_label, STATUS_PRICE_UNDETERMINED)
            self.assertIsNone(c.to_offer_candidate())

    def test_08_prompt_injection_safety_and_low_confidence(self):
        """Сценарий 8: Защита от prompt injection в картинке и отсечение низкой уверенности."""
        # 1. Попытка инъекции с ценой 0 или мусором
        raw_items = [
            {
                "title": "SYSTEM OVERRIDE HACKED PRODUCT",
                "price": 0,
                "currency": "KZT",
                "confidence": 0.99,
            },
            {
                "title": "Suspicious cheap TV",
                "price": 50000,
                "currency": "KZT",
                "confidence": 0.50,  # ниже MIN_VISION_PUBLISH_CONFIDENCE (0.75)
            },
        ]
        candidates = VisionExtractionEngine.extract_from_structured(raw_items)
        self.assertEqual(len(candidates), 2)

        # 0 тг отсекается движком валидации
        inj = candidates[0]
        self.assertFalse(inj.publishable)
        self.assertFalse(inj.price_determined)
        self.assertIsNone(inj.price)

        # Низкая уверенность (0.50 < 0.75) -> Цена не определена
        low_conf = candidates[1]
        self.assertFalse(low_conf.publishable)
        self.assertFalse(low_conf.price_determined)
        self.assertEqual(low_conf.rejection_reason, "low_confidence")
        self.assertEqual(low_conf.status_label, STATUS_PRICE_UNDETERMINED)


class TestVisionAsyncPipeline(unittest.IsolatedAsyncioTestCase):
    """Асинхронные тесты: extract_from_image, extract_from_observation_async, router & fallback."""

    async def test_extract_from_image_via_router(self):
        """extract_from_image вызывает ai_router.run('vision_extraction') и валидирует результат."""
        dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        mock_result = {
            "result": {
                "items": [
                    {
                        "title": "Apple iPhone 15 128GB Black",
                        "price": 389990,
                        "currency": "KZT",
                        "confidence": 0.95,
                    }
                ]
            },
            "source": "model",
            "provider": "local",
            "model": "qwen3-vl:8b-instruct",
            "fallback_used": False,
        }

        with patch("ai_router.run", new_callable=AsyncMock) as mock_router:
            mock_router.return_value = mock_result
            candidates = await VisionExtractionEngine.extract_from_image(
                dummy_png, mime_type="image/png"
            )
            mock_router.assert_called_once()
            call_args = mock_router.call_args
            self.assertEqual(call_args[0][0], "vision_extraction")
            self.assertEqual(call_args[1]["image_bytes"], dummy_png)
            self.assertEqual(call_args[1]["mime_type"], "image/png")

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].raw_title, "Apple iPhone 15 128GB Black")
            self.assertEqual(candidates[0].price, 389990.0)
            self.assertTrue(candidates[0].publishable)

    async def test_fallback_from_local_to_gemini(self):
        """Проверка fallback с локальной модели на Gemini в ai_router при сбое локальной."""
        import ai_service

        dummy_bytes = b"fake-jpeg-data-for-testing"
        fake_config = {
            "enabled": True,
            "has_local": True,
            "local_api_base": "http://192.168.28.25:11434/v1",
            "local_model": "qwen3-vl:8b-instruct",
            "gemini_api_key": "dummy-gemini-key",
            "ai_provider": "auto",
        }

        # Эмулируем ошибку локального вызова и успех Gemini
        with patch("ai_service.call_local_api", new_callable=AsyncMock) as mock_local, \
             patch("ai_service.call_gemini_api", new_callable=AsyncMock) as mock_gemini, \
             patch("ai_router.budget_allows", return_value=True):

            mock_local.side_effect = ConnectionError("Ollama timeout or GPU busy")
            mock_gemini.return_value = {
                "items": [
                    {
                        "title": "Xiaomi Redmi Note 13 8/256GB",
                        "price": 99990,
                        "currency": "KZT",
                        "confidence": 0.93,
                    }
                ]
            }

            result = await ai_router.run(
                "vision_extraction",
                "test prompt",
                config=fake_config,
                image_bytes=dummy_bytes,
                mime_type="image/jpeg",
            )

            mock_local.assert_called_once()
            mock_gemini.assert_called_once()
            # Проверяем, что fallback сработал: ответ получен от gemini с флагом fallback_used=True
            self.assertEqual(result["provider"], "gemini")
            self.assertTrue(result["fallback_used"])
            self.assertIn("items", result["result"])

    async def test_extract_from_observation_async_formats(self):
        """extract_from_observation_async поддерживает image_bytes, base64 и путь к файлу."""
        raw_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy"
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        # 1. Base64 в raw_metadata
        obs_b64 = ContentObservation(
            id="cobs_b64",
            seller_id="seller_1",
            post_id="post_b64",
            observed_at="2026-10-31T12:00:00Z",
            text="Скидки дня!",
            raw_metadata={"image_bytes": b64_str, "mime_type": "image/png"},
        )

        with patch.object(VisionExtractionEngine, "extract_from_image", new_callable=AsyncMock) as mock_efi:
            mock_efi.return_value = [
                VisionCandidate(
                    raw_title="Samsung Galaxy Tab S9",
                    price=349990.0,
                    currency="KZT",
                    confidence=0.92,
                    publishable=True,
                    price_determined=True,
                )
            ]
            cands = await VisionExtractionEngine.extract_from_observation_async(obs_b64)
            mock_efi.assert_called_once()
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0].raw_title, "Samsung Galaxy Tab S9")

        # 2. Локальный путь к файлу (image_path)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name

        try:
            obs_file = ContentObservation(
                id="cobs_file",
                seller_id="seller_1",
                post_id="post_file",
                observed_at="2026-10-31T12:00:00Z",
                text="Постер акции",
                raw_metadata={"image_path": tmp_path},
            )
            with patch.object(VisionExtractionEngine, "extract_from_image", new_callable=AsyncMock) as mock_efi:
                mock_efi.return_value = [
                    VisionCandidate(
                        raw_title="LG TV 65",
                        price=450000.0,
                        currency="KZT",
                        confidence=0.91,
                        publishable=True,
                        price_determined=True,
                    )
                ]
                cands = await VisionExtractionEngine.extract_from_observation_async(obs_file)
                mock_efi.assert_called_once()
                self.assertEqual(len(cands), 1)
                self.assertEqual(cands[0].raw_title, "LG TV 65")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # 3. Детерминированный fallback на OCR при отсутствии картинки
        obs_text = ContentObservation(
            id="cobs_text",
            seller_id="seller_1",
            post_id="post_text",
            observed_at="2026-10-31T12:00:00Z",
            text="Apple iPad Air M2 329 990 ₸ в наличии",
            raw_metadata={},
        )
        cands_ocr = await VisionExtractionEngine.extract_from_observation_async(obs_text)
        self.assertEqual(len(cands_ocr), 1)
        self.assertEqual(cands_ocr[0].price, 329990.0)
        self.assertEqual(cands_ocr[0].currency, "KZT")
        self.assertTrue(cands_ocr[0].publishable)


if __name__ == "__main__":
    unittest.main()
