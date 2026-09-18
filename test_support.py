"""Browser-like test client; security tests use raw TestClient for missing Origin."""
from aiohttp.test_utils import TestClient


class BrowserTestClient(TestClient):
    async def _request(self, method, path, **kwargs):
        headers = dict(kwargs.pop('headers', {}) or {})
        if str(path).startswith('/api/best-price'):
            headers.setdefault('X-KZPH-Request', '1')
        if method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
            headers.setdefault('Origin', str(self.make_url('/')).rstrip('/'))
        return await super()._request(method, path, headers=headers, **kwargs)
