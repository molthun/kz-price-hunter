"""Test isolation hook and browser-like test client.

Every test module imports this module first. config computes DB_PATH once, from
DATA_DIR, on first import; whichever test module imports config first fixes the
DB for the whole run. So DATA_DIR must point to a temporary directory before any
project module is imported, whatever the discovery order.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


def _is_repo_dir(value):
    return not value or Path(value).resolve() == REPO_DIR


if _is_repo_dir(os.environ.get("DATA_DIR")):
    TEST_DATA_DIR = tempfile.mkdtemp(prefix="kzph-test-data-")
    atexit.register(shutil.rmtree, TEST_DATA_DIR, ignore_errors=True)
    os.environ["DATA_DIR"] = TEST_DATA_DIR
else:
    TEST_DATA_DIR = os.environ["DATA_DIR"]

_config = sys.modules.get("config")
if _config is not None and _is_repo_dir(str(_config.DATA_DIR)):
    raise RuntimeError(
        "config was imported before test_support with DATA_DIR = repo root; "
        "tests would write to the working prices.db. Import test_support first.")

from aiohttp.test_utils import TestClient  # noqa: E402


class BrowserTestClient(TestClient):
    async def _request(self, method, path, **kwargs):
        headers = dict(kwargs.pop('headers', {}) or {})
        if str(path).startswith('/api/best-price'):
            headers.setdefault('X-KZPH-Request', '1')
        if method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
            headers.setdefault('Origin', str(self.make_url('/')).rstrip('/'))
        return await super()._request(method, path, headers=headers, **kwargs)
