"""Часовые пояса, которые не роняют сервис, если базы часовых поясов нет.

В контейнере может не оказаться системной базы IANA (tzdata): `ZoneInfo("Asia/Almaty")` тогда бросает
`ZoneInfoNotFoundError`, причём даже для «UTC». Это ломало не только то, что связано со временем:
проверка настроек отвергала собственное значение по умолчанию, и на проде переставали сохраняться
вообще любые системные настройки.

Правило здесь простое: сервис работает всегда, а отсутствие пояса — это причина считать время по UTC
и сказать об этом, а не повод упасть. Сама база ставится пакетом `tzdata` (см. requirements.txt),
и этот модуль — страховка на случай, когда её всё равно нет.
"""
from __future__ import annotations

import datetime
from typing import Optional

UTC = datetime.timezone.utc


def available() -> bool:
    """Есть ли в системе база часовых поясов вообще."""
    try:
        import zoneinfo
        zoneinfo.ZoneInfo("UTC")
        return True
    except Exception:
        return False


def zone(name: Optional[str]) -> datetime.tzinfo:
    """Часовой пояс по имени. Неизвестное имя и отсутствие базы дают UTC, а не исключение."""
    if not name:
        return UTC
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(str(name))
    except Exception:
        return UTC


def known(name: Optional[str]) -> Optional[bool]:
    """True — пояс есть, False — имени нет в базе, None — базы нет и проверить нечем.

    Третий ответ важен: «не могу проверить» не должно читаться как «имя неверное», иначе отсутствие
    tzdata запрещает сохранить настройку с совершенно правильным именем.
    """
    if not available():
        return None
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(str(name))
        return True
    except Exception:
        return False
