"""Догрузка описания товара со страницы магазина.

URL товара приходит из данных магазина, то есть недоверенный. Запрос выполняется только
к доменам магазинов из белого списка, каждый редирект проверяется заново, адреса хоста
должны быть публичными, размер ответа ограничен. Неудачи кэшируются, одинаковые
одновременные запросы объединяются.
"""
import asyncio
import ipaddress
import re
import socket
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from curl_cffi import CurlOpt

from auth import RateLimiter
from scrapers import http


# Домены витрин, с которых разрешено читать описание (включая поддомены)
STORE_DOMAINS = (
    "shop.kz", "kaspi.kz", "dns-shop.kz", "technodom.kz", "sulpak.kz", "mechta.kz",
    "alser.kz", "evrika.com", "moon.kz", "forcecom.kz", "flip.kz", "halykmarket.kz",
    "tgrad.kz", "ants.kz", "itmag.kz", "ispace.kz", "market.forte.kz",
    # Магазины №19–35: описание — общими селекторами/мета-тегом, с теми же ограничениями
    "vkusmart.vmv.kz", "12.kz", "zeta.kz", "komfort.kz", "lemanapro.kz", "arbuz.kz",
    "masterok.kz", "magnum.kz", "intertop.kz", "marwin.kz", "meloman.kz", "i-teka.kz", "mebel.kz",
    "detmir.kz", "askona.kz", "zoomarket.kz", "planeta.kz", "kimex.kz", "europharma.kz",
)
# Общий лимит внешней догрузки описаний (R-M05): перебор разных карточек не создаёт
# неограниченную очередь запросов к магазинам
MAX_CONCURRENT_FETCHES = 4
MAX_FETCHES_PER_MINUTE = 30
FETCH_WAIT_SECONDS = 5
_fetch_rate = RateLimiter(max_calls=MAX_FETCHES_PER_MINUTE, period=60)
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 20_000
REQUEST_TIMEOUT_SECONDS = 10
NEGATIVE_TTL_SECONDS = 6 * 3600
NEGATIVE_CACHE_MAX = 5000

_negative: "OrderedDict[str, float]" = OrderedDict()
_inflight: Dict[str, asyncio.Future] = {}


class UnsafeUrl(ValueError):
    pass


def store_host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in STORE_DOMAINS)


def _resolve_public(host: str, port: int) -> List[str]:
    """Все адреса хоста должны быть публичными: не loopback/private/link-local/reserved.
    Возвращает проверенные адреса — соединение закрепляется за первым из них."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsafeUrl(f"DNS: {type(e).__name__}") from None
    if not infos:
        raise UnsafeUrl("DNS: нет адресов")
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise UnsafeUrl("адрес магазина не публичный")
        addresses.append(str(address))
    return addresses


def check_url(url: str, resolve=None) -> str:
    """Возвращает URL, если его разрешено запрашивать; иначе UnsafeUrl."""
    return check_and_pin(url, resolve)[0]


def check_and_pin(url: str, resolve=None):
    """Проверка URL и запись для curl RESOLVE: соединение идёт на тот адрес, который прошёл
    проверку, — повторное разрешение имени (DNS rebinding) не подменит его (R-M06).
    Host и SNI при этом остаются исходными."""
    parts = urlsplit(str(url or ""))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrl("только http/https")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrl("учётные данные в URL")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UnsafeUrl("некорректный порт") from None
    if port not in (80, 443):
        raise UnsafeUrl("нестандартный порт")
    if not store_host_allowed(parts.hostname):
        raise UnsafeUrl("домен не из списка магазинов")
    addresses = (resolve or _resolve_public)(parts.hostname, port)
    if not addresses:
        return url, None
    ip = addresses[0]
    return url, f"{parts.hostname}:{port}:{'[' + ip + ']' if ':' in ip else ip}"


def fetch_html(url: str, resolve=None) -> Optional[str]:
    """GET с ручными редиректами (каждый проверяется) и пределом размера тела.

    Своя сессия живёт, пока тело читается потоком: модульный `get` закрывает её сразу.
    """
    current, pin = check_and_pin(url, resolve)
    for _ in range(MAX_REDIRECTS + 1):
        options = {CurlOpt.RESOLVE: [pin]} if pin else None
        with http.Session(curl_options=options) as session:
            response = session.get(current, impersonate="chrome124", timeout=REQUEST_TIMEOUT_SECONDS,
                                   allow_redirects=False, stream=True)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location") or ""
                    current, pin = check_and_pin(urljoin(current, location), resolve)
                    continue
                if response.status_code != 200:
                    return None
                if "html" not in (response.headers.get("Content-Type") or "text/html").lower():
                    return None
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                    return None
                body = bytearray()
                for chunk in response.iter_content():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return None
                return bytes(body).decode(response.encoding or "utf-8", errors="replace")
            finally:
                response.close()
    return None


def _clean(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))[:MAX_DESCRIPTION_CHARS]


def extract_description(html: str, url: str, shop: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    host = (urlsplit(url).hostname or "").lower()

    # 1. Белый Ветер (shop.kz)
    if (host.endswith("shop.kz") and "dns-shop" not in host) or "Белый Ветер" in shop:
        desc_el = soup.select_one(".bx_item_description")
        if desc_el:
            lines = [l for l in desc_el.get_text(separator="\n").splitlines() if l.strip() and l.strip().lower() != "описание"]
            text = _clean("\n".join(lines))
            if text:
                return text
        spec_items = []
        for item in soup.select(".dotted-item"):
            n_el = item.select_one(".dotted-item__name")
            v_el = item.select_one(".dotted-item__value")
            if n_el and v_el:
                n, v = n_el.get_text().strip(), v_el.get_text().strip()
                if n and v:
                    spec_items.append(f"• {n}: {v}")
        if spec_items:
            return _clean("\n".join(spec_items))

    # 2. Kaspi Магазин: характеристики
    if host.endswith("kaspi.kz") or "Kaspi" in shop:
        specs = []
        for li in soup.select(".specifications-list .specifications-list__spec"):
            term = li.select_one(".specifications-list__spec-term-translation, .specifications-list__spec-term")
            val = li.select_one(".specifications-list__spec-definition-translation, .specifications-list__spec-definition")
            if term and val:
                specs.append(f"• {term.get_text().strip()}: {val.get_text().strip()}")
        if specs:
            return _clean("\n".join(specs))

    # 3. Общие популярные селекторы описания
    for sel in [
        ".product-card-description", ".product-about__text", ".product-detail__description",
        ".item-description", "#description", ".description-text", ".product-features",
        "[itemprop='description']", ".product-view__description"
    ]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(separator="\n").strip()
            if len(t) > 30:
                return _clean(t)

    # 4. Fallback к мета-тегу description
    meta = soup.select_one("meta[name='description'], meta[property='og:description']")
    if meta and meta.get("content"):
        c = meta["content"].strip()
        if len(c) > 40 and not c.startswith("Купить "):
            return c[:MAX_DESCRIPTION_CHARS]
    return ""


def forte_description(prod: Dict[str, Any]) -> str:
    """Forte Market: характеристики из API только у совпавшего objectID, не у первого результата."""
    from scrapers.fortemarket import API_SEARCH_URL, build_description_from_params
    # id предложения составной: forte_<objectID>@<город> — город в objectID не входит (R-M07)
    pid = str(prod.get("id") or "").split("@", 1)[0]
    object_id = pid[len("forte_"):] if pid.startswith("forte_") else ""
    title = prod.get("title") or ""
    if not object_id or not title:
        return ""
    r = http.post(API_SEARCH_URL, json={"query": title, "hitsPerPage": 10}, timeout=REQUEST_TIMEOUT_SECONDS)
    if r.status_code not in (200, 201):
        return ""
    hits = (r.json() or {}).get("hits") or []
    for hit in hits:
        if str(hit.get("objectID") or hit.get("ID") or "") == object_id:
            return _clean(build_description_from_params(hit) or "")
    return ""


def fetch_description_sync(prod: Dict[str, Any]) -> str:
    url = prod.get("url") or ""
    shop = prod.get("shop") or ""
    try:
        if str(prod.get("id") or "").startswith("forte_") or "Forte" in shop:
            return forte_description(prod)
        html = fetch_html(url)
        return extract_description(html, url, shop) if html else ""
    except Exception as e:
        # URL и тело не логируем: только тип ошибки
        print(f"[Details] Описание не загружено: {type(e).__name__}")
        return ""


def _negative_hit(pid: str) -> bool:
    expires = _negative.get(pid)
    if expires is None:
        return False
    if expires < time.monotonic():
        _negative.pop(pid, None)
        return False
    return True


def _remember_failure(pid: str) -> None:
    _negative[pid] = time.monotonic() + NEGATIVE_TTL_SECONDS
    _negative.move_to_end(pid)
    while len(_negative) > NEGATIVE_CACHE_MAX:
        _negative.popitem(last=False)


async def get_description(prod: Dict[str, Any]) -> str:
    """Single-flight + negative cache вокруг блокирующей загрузки."""
    pid = str(prod.get("id") or "")
    if not pid or _negative_hit(pid):
        return ""
    pending = _inflight.get(pid)
    if pending is not None:
        return await asyncio.shield(pending)
    # Общий лимит: новые внешние загрузки не чаще MAX_FETCHES_PER_MINUTE и не больше
    # MAX_CONCURRENT_FETCHES одновременно; не дождались места — карточка отдаётся без описания
    if _fetch_rate.retry_after("details"):
        return ""
    semaphore = _fetch_semaphore()
    try:
        await asyncio.wait_for(semaphore.acquire(), FETCH_WAIT_SECONDS)
    except asyncio.TimeoutError:
        return ""
    future = asyncio.get_running_loop().create_future()
    _inflight[pid] = future
    try:
        text = await asyncio.to_thread(fetch_description_sync, prod)
        if not text:
            _remember_failure(pid)
        future.set_result(text)
        return text
    except BaseException as e:
        future.set_result("")
        if isinstance(e, Exception):
            return ""
        raise
    finally:
        semaphore.release()
        _inflight.pop(pid, None)


_semaphores: Dict[int, asyncio.Semaphore] = {}


def _fetch_semaphore() -> asyncio.Semaphore:
    """Семафор привязан к текущему event loop (в тестах их несколько)."""
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _semaphores:
        _semaphores.clear()
        _semaphores[loop_id] = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    return _semaphores[loop_id]
