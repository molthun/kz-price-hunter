"""История версий для посетителей сайта: раздел «Для пользователей» из CHANGELOG.md.

CHANGELOG пишется для разработчиков и аудита. Посетителю нужна короткая выжимка, поэтому у
версии, заметной снаружи, есть подраздел:

    ## [6.4.4] — 2026-10-28
    ### Для пользователей
    - Окно «История версий» показывает настоящие выпуски.

Окно на сайте строится только из этих подразделов. Версия без него в окно не попадает,
но текущая версия обязана его иметь (tests/core/test_release_notes.py) — иначе окно отстанет снова.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

CHANGELOG_PATH = Path(__file__).resolve().parent / "CHANGELOG.md"
USER_SECTION = "### Для пользователей"
VERSION_HEADER = re.compile(r"^## \[(\d+\.\d+\.\d+)\]\s*[—–-]\s*(\d{4}-\d{2}-\d{2})")

_cache: Dict[str, Any] = {"mtime": None, "releases": []}


def parse(text: str) -> List[Dict[str, Any]]:
    """Версии с разделом для пользователей, от новых к старым: version, date, items."""
    releases: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    in_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        header = VERSION_HEADER.match(line)
        if header:
            current = {"version": header.group(1), "date": header.group(2), "items": []}
            releases.append(current)
            in_section = False
            continue
        if line.startswith("## "):
            current, in_section = None, False
            continue
        if line.startswith("### "):
            in_section = current is not None and line.strip() == USER_SECTION
            continue
        if not in_section or not line.strip():
            continue
        if line.startswith("- "):
            current["items"].append(line[2:].strip())
        elif raw.startswith("  ") and current["items"]:
            current["items"][-1] += " " + line.strip()  # перенос строки пункта
    return [r for r in releases if r["items"]]


def releases(limit: int = 30, path: Path = CHANGELOG_PATH) -> List[Dict[str, Any]]:
    """Последние версии для окна сайта; файл перечитывается, только если изменился."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _cache["mtime"] != (str(path), mtime):
        _cache["releases"] = parse(path.read_text(encoding="utf-8"))
        _cache["mtime"] = (str(path), mtime)
    return _cache["releases"][:max(1, limit)]
