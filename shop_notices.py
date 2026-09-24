"""Обращения магазинов к тем, кто их парсит: поиск, хранение и оповещение о новых.

Зачем это нужно. shop.kz печатает в консоли браузера: «Если вы парсите сайт на предмет цен и
наличия товаров, воспользуйтесь готовой выгрузкой» и даёт ссылку на YML. Такое сообщение адресовано
именно нам, и заметить его можно было только случайно, открыв консоль руками. Магазин может так же
попросить перестать ходить по каталогу, назвать контакт или сменить ссылку на выгрузку — и об этом
надо узнавать вовремя, а не через полгода.

Что здесь проверяется: главная страница магазина и её собственные скрипты. Сообщение shop.kz живёт
не в HTML, а внутри собранного JS-бандла, поэтому одной страницы мало.

Чего здесь нет: обхода защит и исполнения чужого кода. Модуль только скачивает то, что и так
отдаётся браузеру, и ищет в тексте человеческие фразы. Это наблюдение, а не сбор данных.

Главная сложность — отличить обращение к человеку от совпадения. На страницах магазинов полно слов
«crawler» и «scraper»: это списки user-agent внутри библиотек определения ботов, имена CSS-классов
и атрибуты вида data-is-crawler. Поэтому кандидат обязан выглядеть фразой, обращённой к человеку:
несколько слов подряд, без признаков регулярного выражения и разметки.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urljoin, urlsplit

TIMEOUT_SECONDS = 25.0
MAX_SCRIPTS_PER_SHOP = 30
MAX_SCRIPT_BYTES = 4_000_000
MAX_NOTICES_PER_SHOP = 5

# Слова, ради которых всё затевалось. Общее «crawler» и «scraper» сюда не входят: по ним
# срабатывают списки ботов внутри библиотек, а не обращения к людям.
KEYWORDS = re.compile(
    r"(парс\w*|выгрузк\w*|прайс[- ]?лист\w*|фид\w*\s+товар|"
    r"если\s+вы\s+собира\w+|robots\.txt|"
    r"yml|api\s+для\s+партн|price\s+list|product\s+feed|"
    r"scraping\s+(our|this)|parsing\s+(our|this)|if\s+you\s+(are\s+)?(scrap|pars|crawl))",
    re.I)

# Строка похожа на регулярное выражение со списком user-agent, а не на фразу
REGEX_LIKE = re.compile(r"(\|.*){3,}")
# Строка похожа на разметку, путь или набор классов
MARKUP_LIKE = re.compile(r"(</?[a-z]+[\s>]|\{[^}]*:[^}]*\}|^[\w-]+(\.[\w-]+)+$)", re.I)

QUOTES = "\"'`"
# Окно вокруг найденного слова, в котором ищутся границы литерала и соседняя ссылка
WINDOW = 500
URL_RE = re.compile(r"https?://[^\s\"'`<>\\]{6,200}")

MIN_WORDS = 4


def looks_like_message(text: str) -> bool:
    """Похоже ли на фразу, обращённую к человеку, а не на код и не на список ботов."""
    text = (text or "").strip()
    if not (20 <= len(text) <= 400):
        return False
    if not KEYWORDS.search(text):
        return False
    if REGEX_LIKE.search(text) or MARKUP_LIKE.search(text):
        return False
    words = [w for w in re.split(r"\s+", text) if len(w) > 1]
    if len(words) < MIN_WORDS:
        return False
    # Во фразе должны преобладать буквы, а не служебные символы
    letters = sum(ch.isalpha() or ch.isspace() for ch in text)
    return letters / len(text) >= 0.7


def _enclosing_literal(text: str, position: int) -> Optional[str]:
    """Строковый литерал, внутри которого стоит найденное слово.

    Разбирать весь файл на литераты нельзя: в минифицированном бандле на 400 КБ кавычки сходятся
    не там, где кажется, и нужная строка теряется целиком. Поэтому границы ищутся локально —
    ближайшая кавычка слева и такая же справа.
    """
    left_bound = max(0, position - WINDOW)
    start = max(text.rfind(q, left_bound, position) for q in QUOTES)
    if start < 0:
        return None
    quote = text[start]
    end = text.find(quote, position)
    if end < 0 or end - start > WINDOW:
        return None
    return text[start + 1:end]


def extract(text: str, source: str = "") -> List[Dict[str, str]]:
    """Обращения, найденные в одном файле.

    Поиск идёт от ключевого слова к границам строки, а не наоборот: так фраза находится независимо
    от того, что творится с кавычками в остальном файле.
    """
    text = text or ""
    found: List[Dict[str, str]] = []
    seen = set()
    for match in KEYWORDS.finditer(text):
        literal = _enclosing_literal(text, match.start())
        if literal is None:
            continue
        body = " ".join(literal.replace("\\n", " ").replace('\\"', '"').split())
        if body in seen or not looks_like_message(body):
            continue
        seen.add(body)
        found.append({"text": body, "source": source,
                      "url": _nearby_url(text, match.start())})
        if len(found) >= MAX_NOTICES_PER_SHOP:
            break
    return found


def _nearby_url(text: str, position: int) -> str:
    """Ссылка рядом с обращением: у shop.kz полезен именно адрес выгрузки, а не только фраза."""
    window = text[max(0, position - WINDOW):position + WINDOW]
    match = URL_RE.search(window)
    return match.group(0) if match else ""


def fingerprint(notices: Sequence[Dict[str, str]]) -> str:
    """Отпечаток набора обращений: меняется, когда магазин меняет текст."""
    joined = "\n".join(sorted(n["text"] for n in notices))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16] if joined else ""


def same_site(url: str, host: str) -> bool:
    """Свой ли это скрипт магазина. Чужие CDN не наши и не проверяются."""
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return False
    parts = [p for p in host.lower().split(".") if p not in ("www",)]
    root = ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()
    return hostname == host.lower() or hostname.endswith("." + root) or hostname == root


def script_urls(html: str, page_url: str, host: str) -> List[str]:
    """Свои скрипты страницы, в порядке появления, без повторов."""
    from bs4 import BeautifulSoup
    urls: List[str] = []
    for tag in BeautifulSoup(html or "", "html.parser").select("script[src]"):
        url = urljoin(page_url, tag.get("src") or "")
        if url not in urls and same_site(url, host):
            urls.append(url)
    return urls[:MAX_SCRIPTS_PER_SHOP]


def _fetch(url: str) -> Optional[str]:
    from scrapers import http as requests
    try:
        resp = requests.get(url, impersonate="chrome124", timeout=TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return None
        text = resp.text
        return text if len(text) <= MAX_SCRIPT_BYTES else None
    except Exception:
        return None


def inspect_shop(shop_key: str, host: str) -> Dict[str, Any]:
    """Проверяет один магазин. Не смогли посмотреть — так и говорим, а не «чисто»."""
    page_url = f"https://{host}/"
    html = _fetch(page_url)
    if html is None:
        return {"shop_key": shop_key, "host": host, "checked": False,
                "error": "страница не открылась", "notices": [], "fingerprint": ""}

    notices = extract(html, source="html")
    for url in script_urls(html, page_url, host):
        if len(notices) >= MAX_NOTICES_PER_SHOP:
            break
        body = _fetch(url)
        if body:
            notices.extend(extract(body, source=url.rsplit("/", 1)[-1][:60]))

    unique: List[Dict[str, str]] = []
    seen = set()
    for notice in notices:
        if notice["text"] not in seen:
            seen.add(notice["text"])
            unique.append(notice)
    return {"shop_key": shop_key, "host": host, "checked": True, "error": None,
            "notices": unique[:MAX_NOTICES_PER_SHOP], "fingerprint": fingerprint(unique)}


def changes(results: Iterable[Dict[str, Any]], known: Dict[str, str]) -> List[Dict[str, Any]]:
    """Магазины, у которых обращение появилось или изменилось.

    Непроверенный магазин не считается ни изменившимся, ни прежним: о нём просто ничего не известно,
    и прошлый отпечаток остаётся в силе.
    """
    updates = []
    for result in results:
        if not result.get("checked") or not result.get("notices"):
            continue
        if known.get(result["shop_key"]) != result["fingerprint"]:
            updates.append(result)
    return updates


def message_lines(result: Dict[str, Any], shop_name: str = "") -> List[str]:
    """Текст оповещения владельцу. Цитата магазина приводится как есть."""
    title = shop_name or result.get("shop_key") or "Магазин"
    lines = [f"📣 {title} обращается к тем, кто его парсит:"]
    links = []
    for notice in result.get("notices", []):
        lines.append(f"« {notice['text']} »")
        url = notice.get("url")
        if url and url not in links:
            links.append(url)
    for url in links:
        lines.append(f"Ссылка рядом с сообщением: {url}")
    lines.append(f"Найдено на {result.get('host', '')}. "
                 f"Стоит посмотреть: магазин может предлагать готовую выгрузку "
                 f"или просить не ходить по каталогу.")
    return lines


# --------------------------------------------------------------------------------- проверка и очередь

CHECK_INTERVAL_HOURS = 24 * 7
# Свой диапазон идентификаторов в общей очереди: у суточной сводки 1_000_000_000, здесь другой,
# чтобы задания двух разных вещей не сталкивались
NOTICE_ALERT_BASE = 2_000_000_000


def notice_alert_id(shop_key: str, fingerprint: str) -> int:
    """Идентификатор задания для этого текста у этого магазина.

    В отпечаток входит текст, поэтому изменённое обращение получит новый идентификатор и придёт
    заново, а прежнее не повторится: UNIQUE(alert_id, user_id) очереди сам это обеспечивает.
    """
    digest = hashlib.sha256(f"{shop_key}\n{fingerprint}".encode("utf-8")).digest()
    return -(NOTICE_ALERT_BASE + int.from_bytes(digest[:4], "big"))


def due(known: Dict[str, Any], now_iso: str, shops: int) -> bool:
    """Пора ли проверять. Проверка тяжёлая (страница и её скрипты), поэтому раз в неделю."""
    import datetime as _dt
    if len(known) < shops:
        return True  # кого-то ещё ни разу не смотрели
    checked = [row.get("last_checked_at") for row in known.values() if row.get("last_checked_at")]
    if not checked:
        return True
    try:
        oldest = min(_dt.datetime.fromisoformat(value) for value in checked)
        moment = _dt.datetime.fromisoformat(now_iso)
    except ValueError:
        return True
    return (moment - oldest).total_seconds() >= CHECK_INTERVAL_HOURS * 3600


def check_and_queue(hosts: Dict[str, str], names: Optional[Dict[str, str]] = None,
                    now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Проверяет магазины и ставит владельцу сообщение о новых обращениях.

    Магазин, который не удалось посмотреть, не считается чистым: его прошлое состояние сохраняется,
    а в отчёт попадает отдельным списком.
    """
    import datetime as _dt
    import json as _json
    import database

    moment = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
    names = names or {}
    known = database.get_shop_notices()
    if not due(known, moment, len(hosts)):
        return {"checked": 0, "changed": [], "unreachable": [], "queued": 0, "skipped": True}

    changed, unreachable, checked = [], [], 0
    for shop_key, host in hosts.items():
        result = inspect_shop(shop_key, host)
        if not result["checked"]:
            unreachable.append(shop_key)
            continue
        checked += 1
        if not result["notices"]:
            continue
        is_new = database.save_shop_notice(
            shop_key, host, result["fingerprint"],
            _json.dumps(result["notices"], ensure_ascii=False), moment)
        if is_new:
            changed.append(result)

    queued = 0
    for result in changed:
        queued += _queue_alert(result, names.get(result["shop_key"], ""), moment)
    return {"checked": checked, "changed": [r["shop_key"] for r in changed],
            "unreachable": unreachable, "queued": queued, "skipped": False}


def _queue_alert(result: Dict[str, Any], shop_name: str, moment_iso: str) -> int:
    import datetime as _dt
    import json as _json
    import database
    import daily_digest

    people = daily_digest.recipients()
    if not people:
        return 0
    payload = {"kind": "shop_notice", "shop_key": result["shop_key"], "host": result["host"],
               "lines": message_lines(result, shop_name)}
    created = _dt.datetime.fromisoformat(moment_iso).timestamp()
    alert_id = notice_alert_id(result["shop_key"], result["fingerprint"])
    queued = 0
    with database.get_connection() as conn:
        for user_id in people:
            cur = conn.execute("""INSERT OR IGNORE INTO notification_outbox
                                  (alert_id, user_id, payload, status, created_at, next_attempt_at)
                                  VALUES (?, ?, ?, 'pending', ?, ?)""",
                               (alert_id, user_id, _json.dumps(payload, ensure_ascii=False),
                                created, 0))
            queued += cur.rowcount or 0
        conn.commit()
    return queued
