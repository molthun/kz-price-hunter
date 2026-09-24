"""Подсказка цен моделей с публичного прайса провайдера — с обязательным подтверждением человеком.

Почему так, а не «тянуть автоматически, как список моделей»: список моделей отдаёт API провайдера,
это официальный ответ по ключу. Цен в API нет ни у Google, ни у OpenAI — они живут только на страницах
для людей. Разбор такой страницы всегда может ошибиться (и ошибался при первой же попытке: строка
кэширования легко принимается за цену ответа), а ошибка в цене — это неверные деньги в отчётах,
показанные с видом точной цифры.

Поэтому здесь ровно подсказка: модуль возвращает найденные числа вместе с источником и временем
чтения, ничего не сохраняет, и при малейшей неоднозначности отвечает «не нашёл», а не догадкой.
Сохранить цену может только владелец, подтвердив её в настройках.
"""
from __future__ import annotations

import datetime
import html
import re
from typing import Any, Dict, List, Optional

GEMINI_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"
OPENAI_PRICING_URL = "https://platform.openai.com/docs/pricing"

TIMEOUT_SECONDS = 20.0
MAX_PAGE_BYTES = 4_000_000

# Первая цена в строке «Paid Tier»: берём только доллары за миллион токенов, без «за час» и «за 1000»
_PRICE_RE = re.compile(r"\$([0-9]+(?:\.[0-9]+)?)")
_PER_OTHER_RE = re.compile(r"per\s+hour|/\s*1,000|per\s+1,000|/\s*minute", re.I)

DISCLAIMER = ("Цены прочитаны с публичной страницы провайдера, а не из вашего аккаунта. "
              "Тариф, пакетный и flex-режимы могут отличаться — проверьте и подтвердите сохранением.")


def _text_of(section: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", section)
    return html.unescape(re.sub(r"\s+", " ", plain))


def _shorten(text: str, limit: int = 160) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _first_price(cell: str) -> Optional[float]:
    """Первое долларовое число строки, если оно относится к миллиону токенов."""
    if _PER_OTHER_RE.search(cell):
        return None
    match = _PRICE_RE.search(cell)
    return float(match.group(1)) if match else None


def parse_gemini(page: str, model: str) -> Optional[Dict[str, Any]]:
    """Цены одной модели со страницы Gemini. Неоднозначность — это «не нашёл»."""
    anchor = f'id="{model}"'
    start = page.find(anchor)
    if start < 0:
        return None
    text = _text_of(page[start:start + 8000])
    # Раздел «Standard»: дальше идут Batch и прочие режимы, их цены другие
    standard = text.split("Batch")[0]
    input_match = re.search(r"Input price(.{0,200}?)Output price", standard, re.S)
    output_match = re.search(r"Output price[^$]{0,120}?(\$[0-9.,]+)", standard, re.S)
    if not input_match or not output_match:
        return None
    raw_in, raw_out = input_match.group(1).strip(), output_match.group(0).strip()
    price_in = _first_price(raw_in)
    price_out = _first_price(raw_out)
    if price_in is None or price_out is None:
        return None
    # У части моделей цена зависит от длины запроса или вида данных (аудио дороже текста): тогда в
    # строке несколько чисел, и подставленное — только первое из них. Об этом обязательно сказать,
    # иначе подсказка выглядит точнее, чем есть.
    variants = len(_PRICE_RE.findall(raw_in)) + len(_PRICE_RE.findall(raw_out))
    ambiguous = variants > 2
    return {"model": model, "input": price_in, "output": price_out,
            "source": GEMINI_PRICING_URL, "basis": "Standard, за 1 млн токенов, текст",
            "ambiguous": ambiguous,
            "raw": _shorten(raw_in) + " / " + _shorten(raw_out)}


async def fetch_page(url: str) -> Optional[str]:
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as session:
            async with session.get(url, headers={"Accept-Language": "en",
                                                 "User-Agent": "kz-price-hunter/pricing-check"}) as resp:
                if resp.status != 200:
                    return None
                return (await resp.text())[:MAX_PAGE_BYTES]
    except Exception:
        return None


async def suggest(models: List[str], now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Подсказка цен для перечисленных моделей. Ничего не сохраняет и ни о чём не догадывается."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    found: Dict[str, Any] = {}
    missing: List[Dict[str, str]] = []

    gemini_models = [m for m in models if str(m).startswith("gemini")]
    other_models = [m for m in models if not str(m).startswith("gemini")]

    if gemini_models:
        page = await fetch_page(GEMINI_PRICING_URL)
        if page is None:
            for model in gemini_models:
                missing.append({"model": model, "why": "страница прайса Gemini не открылась",
                                "source": GEMINI_PRICING_URL})
        else:
            for model in gemini_models:
                price = parse_gemini(page, model)
                if price:
                    found[model] = price
                else:
                    missing.append({"model": model,
                                    "why": "на странице прайса такой модели нет или цена не разобралась",
                                    "source": GEMINI_PRICING_URL})

    for model in other_models:
        # Прайс OpenAI отдаётся пустой страницей и рисуется в браузере: читать его сервером нечем,
        # и выдумывать цифры вместо этого нельзя
        missing.append({"model": model,
                        "why": "прайс этого провайдера собирается в браузере и не читается сервером — "
                               "откройте страницу и впишите цену вручную",
                        "source": OPENAI_PRICING_URL})

    return {"found": found, "missing": missing, "fetched_at": moment.isoformat(),
            "disclaimer": DISCLAIMER}
