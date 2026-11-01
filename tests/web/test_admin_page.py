from tests import test_support  # must be first
import asyncio
import unittest
from unittest import mock

from aiohttp import web

from web.server import admin_page_handler


class _Request:
    def __init__(self, user):
        self.user = user

    def get(self, key, default=None):
        return self.user if key == "user" else default


class AdminPageTest(unittest.TestCase):
    def test_guest_and_regular_user_go_home(self):
        for user in (None, {"id": 1, "is_admin": False, "telegram_id": 1}):
            with self.subTest(user=user):
                with self.assertRaises(web.HTTPFound) as caught:
                    asyncio.run(admin_page_handler(_Request(user)))
                self.assertEqual(caught.exception.location, "/")

    def test_admin_gets_page(self):
        with mock.patch("web.server.is_admin", return_value=True):
            response = asyncio.run(admin_page_handler(_Request({"id": 1})))
        self.assertIsInstance(response, web.FileResponse)
        self.assertTrue(str(response._path).endswith("admin.html"))


if __name__ == "__main__":
    unittest.main()
