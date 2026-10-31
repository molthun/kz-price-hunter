from tests import test_support  # must be first import

import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from aiohttp.test_utils import make_mocked_request

from database import get_connection, init_db
from domain.discovery import CandidateStatus, SellerCandidate
from domain.models import ChannelType
from domain.seller_onboarding import (
    SellerOnboardingSubmission,
    prepare_onboarding_candidate,
    validate_onboarding_submission,
)
from repositories.candidate_repo import CandidateRepository
from repositories.channel_repo import ChannelRepository
from repositories.schema_v2 import init_schema_v2
from repositories.seller_repo import SellerRepository
import web.server as server


class TestSellerOnboardingFullFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_onboarding.db")
        self.patcher = patch("database.DB_PATH", Path(self.db_path))
        self.patcher.start()

        init_db()
        conn = get_connection()
        try:
            init_schema_v2(conn)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_validate_onboarding_submission(self):
        # 1. Пустое имя -> ошибка
        sub_bad_name = SellerOnboardingSubmission(name="", website_url="https://shop.kz")
        ok, errs = validate_onboarding_submission(sub_bad_name)
        self.assertFalse(ok)
        self.assertTrue(any("название" in e.lower() for e in errs))

        # 2. Имя есть, но нет ни одного контакта/канала -> ошибка
        sub_no_channels = SellerOnboardingSubmission(name="Shop KZ")
        ok, errs = validate_onboarding_submission(sub_no_channels)
        self.assertFalse(ok)
        self.assertTrue(any("хотя бы один канал" in e.lower() for e in errs))

        # 3. Неверный БИН (не 12 цифр) -> ошибка
        sub_bad_bin = SellerOnboardingSubmission(
            name="Shop KZ",
            website_url="https://shop.kz",
            bin="12345",
        )
        ok, errs = validate_onboarding_submission(sub_bad_bin)
        self.assertFalse(ok)
        self.assertTrue(any("12 цифр" in e.lower() for e in errs))

        # 4. Корректная заявка с сайтом, телефоном и БИН 12 цифр
        sub_good = SellerOnboardingSubmission(
            name="Mega Tech KZ",
            city="Алматы",
            website_url="https://megatech.kz",
            phone="+7 777 123 45 67",
            instagram="@megatech_kz",
            bin="123456789012",
        )
        ok, errs = validate_onboarding_submission(sub_good)
        self.assertTrue(ok)
        self.assertEqual(len(errs), 0)

    async def test_public_register_seller_endpoint(self):
        # Успешная отправка формы через API
        payload = {
            "name": "Nova Store Almaty",
            "city": "Алматы",
            "website_url": "https://novastore.kz",
            "phone": "+7 701 555 44 33",
            "instagram": "novastore_almaty",
            "telegram": "novastore_kz",
            "marketplace_links": ["https://kaspi.kz/shop/info/merchant/novastore/address-and-delivery"],
            "bin": "987654321098",
        }
        req = make_mocked_request("POST", "/api/v2/sellers/register")
        req.read = unittest.mock.AsyncMock(return_value=json.dumps(payload).encode("utf-8"))

        resp = await server.register_seller_v2_handler(req)
        self.assertEqual(resp.status, 201)
        data = json.loads(resp.text)
        self.assertTrue(data.get("ok"))
        cand = data.get("candidate") or {}
        self.assertEqual(cand.get("name"), "Nova Store Almaty")
        self.assertEqual(cand.get("status"), CandidateStatus.REVIEW_REQUIRED)

        # Проверяем, что обнаружены каналы продаж
        detected = data.get("channels_detected") or []
        chan_types = {d["channel_type"] for d in detected}
        self.assertIn("website", chan_types)
        self.assertIn("instagram", chan_types)
        self.assertIn("telegram", chan_types)
        self.assertIn("marketplace", chan_types)

        # Проверяем, что кандидат сохранен в БД
        conn = get_connection()
        try:
            repo = CandidateRepository(conn)
            saved = repo.get_candidate_by_id(cand["id"])
            self.assertIsNotNone(saved)
            self.assertEqual(saved.status, CandidateStatus.REVIEW_REQUIRED)
        finally:
            conn.close()

    async def test_admin_onboarding_list_and_approval_flow(self):
        # 1. Создаем кандидата через репозиторий
        sub = SellerOnboardingSubmission(
            name="Apple City Shop",
            city="Астана",
            website_url="https://applecity.kz",
            phone="+7 705 111 22 33",
            marketplace_links=["https://kaspi.kz/shop/c/applecity/"],
        )
        cand, profiles, detected = prepare_onboarding_candidate(sub, candidate_id="cand_app_city")
        conn = get_connection()
        try:
            repo = CandidateRepository(conn)
            repo.save_candidate(cand)
            conn.commit()
        finally:
            conn.close()

        # 2. Получение списка заявок админом
        req_list = make_mocked_request("GET", "/api/v2/admin/onboarding?status=REVIEW_REQUIRED")
        req_list["user"] = {"id": 1, "username": "admin"}
        with patch("auth.is_admin", return_value=True):
            resp_list = await server.admin_list_onboarding_handler(req_list)
            self.assertEqual(resp_list.status, 200)
            data_list = json.loads(resp_list.text)
            self.assertTrue(data_list.get("ok"))
            candidates = data_list.get("candidates") or []
            self.assertTrue(any(c["id"] == "cand_app_city" for c in candidates))

        # 3. Одобрение заявки админом (approve)
        req_approve = make_mocked_request(
            "POST",
            "/api/v2/admin/onboarding/cand_app_city/decision",
            match_info={"candidate_id": "cand_app_city"},
        )
        req_approve.read = unittest.mock.AsyncMock(
            return_value=json.dumps({"decision": "approve"}).encode("utf-8")
        )
        req_approve["user"] = {"id": 1, "username": "admin"}
        with patch("auth.is_admin", return_value=True):
            resp_dec = await server.admin_onboarding_decision_handler(req_approve)
            self.assertEqual(resp_dec.status, 200)
            data_dec = json.loads(resp_dec.text)
            self.assertTrue(data_dec.get("ok"))
            self.assertEqual(data_dec.get("decision"), "APPROVED")
            seller_created = data_dec.get("seller")
            self.assertIsNotNone(seller_created)
            self.assertEqual(seller_created.get("name"), "Apple City Shop")

        # 4. Проверяем, что в базе появился продавец и каналы
        conn = get_connection()
        try:
            seller_repo = SellerRepository(conn)
            seller_db = seller_repo.get_by_id(seller_created["id"])
            self.assertIsNotNone(seller_db)
            self.assertEqual(seller_db.name, "Apple City Shop")

            ch_repo = ChannelRepository(conn)
            channels = ch_repo.list_by_seller(seller_created["id"])
            self.assertGreaterEqual(len(channels), 1)
        finally:
            conn.close()
