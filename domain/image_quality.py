"""
Utility for upgrading product image URLs to maximum available resolution.
Supports Kaspi, Kari, Marwin/Meloman, Technodom, Alser, ForteMarket, Wildberries, Ozon, etc.
"""
from __future__ import annotations
import re
from typing import List, Optional


def upgrade_image_url(url: Optional[str]) -> str:
    """Улучшает ссылку на изображение до максимального доступного разрешения."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if not u or not u.startswith(("http://", "https://")):
        return u

    # 1. Kaspi CDN: заменяем превью (preview-medium, preview-small, preview-large, etc.) на gallery-large
    if "cdn-kaspi.kz" in u:
        if "format=" in u:
            u = re.sub(r'([?&]format=)(?:preview-[a-z0-9_-]+|preview|thumbnail|small|medium|gallery-medium)', r'\g<1>gallery-large', u)
        else:
            u += ("&" if "?" in u else "?") + "format=gallery-large"

    # 2. Kari CDN: убираем суффикс -preview
    elif "kari.com" in u:
        u = re.sub(r'-preview\.(jpg|jpeg|png|webp)', r'.\1', u, flags=re.IGNORECASE)

    # 3. Marwin / Meloman: убираем кеш миниатюр Magento (/cache/[32hex]/) для получения исходника 4K
    elif "marwin.kz" in u or "meloman.kz" in u:
        u = re.sub(r'/cache/[a-f0-9]{32}/', '/', u, flags=re.IGNORECASE)

    # 4. Technodom: переключаем с низкого разрешения (272/272) на высокое (800/800)
    elif "technodom.kz" in u:
        u = re.sub(r'/images/\d+/\d+/', '/images/800/800/', u)

    # 5. Alser: убираем суффикс ограничения ширины (-w160, -w300) для получения полного webp
    elif "alser.kz" in u:
        u = re.sub(r'-w\d+(\.webp|\.jpg|\.png)', r'\1', u, flags=re.IGNORECASE)

    # 6. Forte Market / Darbiz: перенаправляем на прямой CDN object.pscloud.io и убираем миниатюры /s/ и /m/
    elif "darbiz" in u:
        u = re.sub(r'https?://(?:market\.forte\.kz|forte\.kz)/darbiz/', 'https://object.pscloud.io/darbiz/', u)
        u = re.sub(r'(/darbiz/catalogs/[^/]+/)(?:s|m)/', r'\1', u)

    # 7. Wildberries CDN: переключаем миниатюры c246x328 / tm / c516x688 на big / c900x1200
    elif "wbcontent.net" in u or "wildberries.ru" in u or "wildberries.kz" in u or "wbbasket.ru" in u:
        u = re.sub(r'/images/(?:c246x328|c516x688|tm)/', '/images/big/', u)

    # 8. Ozon CDN: увеличиваем размер миниатюр wc50 / wc200 / c200 -> c1000
    elif "ozonimages.ru" in u or "ozon.ru" in u or "ozon.kz" in u:
        u = re.sub(r'/(?:wc\d+|c\d+)/', '/c1000/', u)

    return u


def upgrade_image_urls(urls: Optional[List[str]]) -> List[str]:
    """Улучшает список ссылок на изображения с сохранением порядка и удалением дубликатов."""
    if not urls:
        return []
    seen = set()
    res = []
    for u in urls:
        up = upgrade_image_url(u)
        if up and up not in seen:
            seen.add(up)
            res.append(up)
    return res
