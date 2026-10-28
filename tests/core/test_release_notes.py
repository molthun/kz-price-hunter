"""Окно «История версий» на сайте не должно снова отстать от выпусков (6.4.4)."""

from tests import test_support  # noqa: F401
import tempfile
import unittest
from pathlib import Path

import release_notes
from version import __version__
from web import server
from aiohttp.test_utils import AioHTTPTestCase

SAMPLE = """# Changelog

## [2.0.0] — 2026-10-02
### Для пользователей
- **Новое:** первая строка
  с переносом.
- Вторая.

### Технические детали
- не для посетителей

## [1.1.0] - 2026-10-01
### Исправления
- только внутреннее

## [1.0.0] - 2026-09-30
### Для пользователей
- Первая версия.
"""


class ParseTest(unittest.TestCase):
    def test_only_user_sections_newest_first(self):
        self.assertEqual(release_notes.parse(SAMPLE), [
            {"version": "2.0.0", "date": "2026-10-02", "items": ["**Новое:** первая строка с переносом.", "Вторая."]},
            {"version": "1.0.0", "date": "2026-09-30", "items": ["Первая версия."]},
        ])

    def test_file_is_reread_after_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text(SAMPLE, encoding="utf-8")
            self.assertEqual(release_notes.releases(10, path)[0]["version"], "2.0.0")
            path.write_text(SAMPLE.replace("2.0.0", "2.0.1"), encoding="utf-8")
            import os
            os.utime(path, (1, 1))
            self.assertEqual(release_notes.releases(10, path)[0]["version"], "2.0.1")


class CurrentVersionContractTest(unittest.TestCase):
    def test_current_version_leads_changelog_and_has_user_section(self):
        text = release_notes.CHANGELOG_PATH.read_text(encoding="utf-8")
        first = next(release_notes.VERSION_HEADER.match(l) for l in text.splitlines()
                     if release_notes.VERSION_HEADER.match(l))
        self.assertEqual(first.group(1), __version__, "первая версия CHANGELOG должна совпадать с version.py")
        top = release_notes.parse(text)[0]
        self.assertEqual(top["version"], __version__,
                         "у текущей версии нет раздела «### Для пользователей» — окно на сайте отстанет")

    def test_template_has_no_hardcoded_history(self):
        html = (Path(server.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="changelogList"', html)
        self.assertNotIn("v2.1.0 (Текущая версия)", html)


class ChangelogEndpointTest(AioHTTPTestCase):
    async def get_application(self):
        app = server.create_app()
        app.cleanup_ctx.clear()
        return app

    async def test_endpoint_returns_current_version_first(self):
        resp = await self.client.get("/api/changelog?limit=3")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["current"], __version__)
        self.assertEqual(data["releases"][0]["version"], __version__)
        self.assertLessEqual(len(data["releases"]), 3)
        bad = await self.client.get("/api/changelog?limit=abc")
        self.assertEqual(bad.status, 200)


if __name__ == "__main__":
    unittest.main()
