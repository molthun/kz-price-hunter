"""
telegram_widgets.py — карточка результатов /search в виде HTML-виджета (Bot API 10.4), пилот TGW-01.

Что важно знать о виджетах, чтобы не сломать карточку:
- HTML — до 32768 байт и до 4096 байт после zlib уровня 9; сети внутри нет, внешние картинки не грузятся;
- код карточки после отправки не меняется; бот меняет только chat_data (до 1024 байт);
- запрос виджета боту — до 64 байт, ответ — до 1024 байт, ответить нужно за 15 секунд;
- приложения без поддержки виджетов показывают только подпись, поэтому подпись всегда содержательна.

Модуль не ходит в сеть и не читает базу: только собирает HTML, подпись и chat_data.
"""

import html
import json
import os
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

import config

MAX_OFFERS = 8             # выше карточка упирается в предел высоты 600 px: прокрутки внутри виджета нет
MAX_TITLE = 70
MAX_URL = 256              # предел openLink; длиннее — строка остаётся без ссылки
HTML_LIMIT = 32768
COMPRESSED_LIMIT = 3900    # предел Telegram — 4096; запас на расхождение реализаций zlib
DATA_LIMIT = 1024
CAPTION_LIMIT = 1024
REQUEST_WATCH = "w"
REQUEST_REFRESH = "r"

_MODES = ("off", "admins", "all")


def search_widget_mode() -> str:
    """off (по умолчанию) | admins | all. Читается при каждом вызове: выключатель не требует пересборки."""
    mode = os.getenv("TELEGRAM_SEARCH_WIDGET", "").strip().lower()
    return mode if mode in _MODES else "off"


def search_widget_enabled(user_id: int) -> bool:
    mode = search_widget_mode()
    if mode == "all":
        return True
    return mode == "admins" and user_id in config.ADMIN_TELEGRAM_IDS


def _old_price(item: Dict[str, Any]) -> int:
    """Прежняя цена — только если она выше текущей; иначе скидки нет и показывать нечего."""
    price = int(item.get("current_price") or 0)
    for key in ("old_price_on_site", "first_seen_price"):
        try:
            old = int(item.get(key) or 0)
        except (TypeError, ValueError):
            old = 0
        if old > price:
            return old
    return 0


def _clean(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").replace("\x00", "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def pick_offers(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Первые MAX_OFFERS предложений в виде, который зашивается в карточку. Порядок = порядок индексов."""
    offers = []
    for item in items:
        price = int(item.get("current_price") or 0)
        if price <= 0:
            continue
        url = str(item.get("url") or "")
        offers.append({
            "id": str(item.get("id") or ""),
            "shop": _clean(item.get("shop") or "Магазин", 30),
            "title": _clean(item.get("title"), MAX_TITLE),
            "price": price,
            "old": _old_price(item),
            "url": url if url.startswith("https://") and len(url) <= MAX_URL else "",
        })
        if len(offers) == MAX_OFFERS:
            break
    return offers


def _script_json(value: Any) -> str:
    """JSON для вставки внутрь <script>: закрывающий тег и разделители строк не должны оборвать скрипт."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_TEMPLATE = """<style>
body{margin:0;padding:10px 12px 8px;font:var(--tg-text-font-size,15px)/1.35 system-ui,sans-serif;color:var(--tg-theme-text-color,#222)}
.h{color:var(--tg-theme-hint-color,#888);font-size:12px}
.c{display:flex;gap:6px;margin:8px 0 4px}
button{font:inherit;border:0;border-radius:8px;padding:6px 10px;background:var(--tg-theme-secondary-bg-color,#eee);color:var(--tg-theme-text-color,#222)}
button:active{opacity:.6}
button.on{background:var(--tg-theme-button-color,#39f);color:var(--tg-theme-button-text-color,#fff)}
.c button{font-size:12px;padding:4px 10px}
.r{display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid var(--tg-theme-section-separator-color,#ddd)}
.n{flex:1;min-width:0}
.n div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.p{font-weight:600;white-space:nowrap}
.b{color:var(--tg-theme-accent-text-color,#2a8)}
.x{opacity:.5}
.a{display:flex;gap:8px;margin-top:8px}
#w{flex:1}
</style>
<b id="q"></b><div class="h" id="m"></div>
<div class="c" id="c"><button data-s="p" class="on">Дешевле</button><button data-s="d">Скидка больше</button><button data-s="o">Только со скидкой</button></div>
<div id="l"></div>
<div class="a"><button id="w">🔔 Следить за ценой</button><button id="u">↻ Обновить</button></div>
<div class="h" id="s">&nbsp;</div>
<script>
var Q=__QUERY__,D=__OFFERS__,T=window.TelegramWidget,S="p",P=null,At=__SENT__;
function $(i){return document.getElementById(i)}
function f(n){return n.toLocaleString("ru-RU")+" ₸"}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function st(){try{var o=JSON.parse(T.chatData);if(o&&o.p&&o.p.length==D.length){P=o.p;At=o.t||At}}catch(e){}}
function draw(){
var a=D.map(function(d,i){var p=P?P[i]:d[1];return{s:d[0],p:p,o:d[2]>p?d[2]:0,u:d[3],t:d[4]}});
if(S=="o")a=a.filter(function(x){return x.o&&x.p});
function k(x){return x.p?(S=="d"?-(x.o?(x.o-x.p)/x.o:0):x.p):1e15}
a.sort(function(x,y){return k(x)-k(y)||x.p-y.p});
var l=$("l");l.textContent="";
a.forEach(function(x,i){
var r=el(x.u&&x.p?"a":"div","r"+(x.p?"":" x"));
if(x.u&&x.p){r.href=x.u;r.style.textDecoration="none";r.style.color="inherit"}
var n=el("div","n");n.appendChild(el("div","",x.s));n.appendChild(el("div","h",x.t));
if(x.o&&x.p)n.appendChild(el("div","h",f(x.o)+" → −"+Math.round((x.o-x.p)/x.o*100)+"%"));
r.appendChild(n);r.appendChild(el("div","p"+(i==0&&x.p?" b":""),x.p?f(x.p):"нет в наличии"));
l.appendChild(r)});
if(!a.length)l.appendChild(el("div","r h","Сейчас предложений со скидкой нет"));
var d=new Date(At*1e3);
$("m").textContent=D.length+" предл. · обновлено "+d.toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"})}
function ask(c,b){
var s=$("s");if(!T){s.textContent="Доступно только в Telegram";return}
s.textContent="Отправляю…";
T.request(c).then(function(r){s.textContent=r.result||"Готово";if(b&&r.result)b.classList.add("on")},
function(e){s.textContent=e.code=="RATE_LIMITED"||e.code=="BUSY"?"Слишком часто, подождите немного":e.code=="TIMEOUT"?"Бот не ответил, попробуйте позже":"Не получилось, попробуйте позже"})}
$("q").textContent=Q;
$("c").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;S=b.dataset.s;
[].forEach.call(this.children,function(x){x.classList.toggle("on",x==b)});draw()});
$("w").addEventListener("click",function(){ask("__WATCH__",this)});
$("u").addEventListener("click",function(){ask("__REFRESH__")});
if(T){st();T.onEvent("chatDataChanged",function(){st();draw()})}
draw();
</script>"""


def _render(query: str, offers: List[Dict[str, Any]], sent_at: int) -> str:
    rows = [[o["shop"], o["price"], o["old"], o["url"], o["title"]] for o in offers]
    return (_TEMPLATE
            .replace("__QUERY__", _script_json(_clean(query, 80)))
            .replace("__OFFERS__", _script_json(rows))
            .replace("__SENT__", str(int(sent_at)))
            .replace("__WATCH__", REQUEST_WATCH)
            .replace("__REFRESH__", REQUEST_REFRESH))


def compressed_size(widget_html: str) -> int:
    return len(zlib.compress(widget_html.encode("utf-8"), 9))


def build_search_widget(query: str, items: List[Dict[str, Any]],
                        now: Optional[float] = None) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """HTML карточки и предложения, которые в неё попали (их порядок — индексы для chat_data).

    Если карточка не помещается в пределы Telegram, предложения убираются с конца. None — показывать
    нечего или не помещается даже одно предложение: вызывающий код отправляет обычный текст.
    """
    offers = pick_offers(items)
    sent_at = int(now if now is not None else time.time())
    while offers:
        widget_html = _render(query, offers, sent_at)
        if len(widget_html.encode("utf-8")) <= HTML_LIMIT and compressed_size(widget_html) <= COMPRESSED_LIMIT:
            return widget_html, offers
        offers = offers[:-1]
    return None


def build_caption(query: str, offers: List[Dict[str, Any]]) -> str:
    """Подпись (HTML Telegram): её видят в уведомлении, в списке чатов и в приложениях без виджетов."""
    lines = [f"🔍 <b>Лучшие цены по запросу «{html.escape(_clean(query, 80))}»</b>"]
    for index, offer in enumerate(offers[:3], 1):
        price = f"{offer['price']:,}".replace(",", " ") + " ₸"
        lines.append(f"{index}. <b>{price}</b> — {html.escape(offer['title'])} (<i>{html.escape(offer['shop'])}</i>)")
    caption = "\n".join(lines)
    while len(caption) > CAPTION_LIMIT and len(lines) > 1:
        lines.pop()
        caption = "\n".join(lines)
    return caption


def build_chat_data(offers: List[Dict[str, Any]], prices_by_id: Dict[str, int],
                    now: Optional[float] = None) -> str:
    """Свежие цены по индексам карточки; 0 — предложение пропало из актуальной выдачи."""
    payload = {"t": int(now if now is not None else time.time()),
               "p": [int(prices_by_id.get(o["id"]) or 0) for o in offers]}
    data = json.dumps(payload, separators=(",", ":"))
    if len(data.encode("utf-8")) > DATA_LIMIT:
        raise ValueError("chat_data карточки не помещается в предел Telegram")
    return data
