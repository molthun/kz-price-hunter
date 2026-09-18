"""Tests for HTTP security headers and notifier URL sanity checks."""
import unittest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import auth
import notifier


class SecurityHeadersTest(unittest.IsolatedAsyncioTestCase):
    async def test_security_headers_in_auth_middleware(self):
        async def mock_handler(request):
            return web.Response(text="ok")

        req = make_mocked_request("GET", "/api/test", headers={"Host": "localhost:8080"})
        res = await auth.auth_middleware(req, mock_handler)

        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertEqual(res.headers.get("Cache-Control"), "no-store")

    def test_is_safe_image_url(self):
        # Valid URLs
        self.assertTrue(notifier.is_safe_image_url("https://cdn.shop.kz/images/item123.jpg"))
        self.assertTrue(notifier.is_safe_image_url("http://images.technodom.kz/goods/photo.png?w=500"))

        # Invalid schemes
        self.assertFalse(notifier.is_safe_image_url("ftp://example.com/image.jpg"))
        self.assertFalse(notifier.is_safe_image_url("file:///etc/passwd"))
        self.assertFalse(notifier.is_safe_image_url("javascript:alert(1)"))
        self.assertFalse(notifier.is_safe_image_url("data:image/png;base64,iVBORw0KGgoAAAANS..."))

        # Credentials / Malformed
        self.assertFalse(notifier.is_safe_image_url("https://admin:secret@malicious.site/img.jpg"))
        self.assertFalse(notifier.is_safe_image_url(""))
        self.assertFalse(notifier.is_safe_image_url(None))
        self.assertFalse(notifier.is_safe_image_url("just-a-string"))


if __name__ == "__main__":
    unittest.main()
