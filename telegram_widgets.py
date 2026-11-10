"""
telegram_widgets.py — карточка результатов /search в виде HTML-виджета (Bot API 10.4), пилот TGW-01.

Что важно знать о виджетах, чтобы не сломать карточку:
- HTML — до 32768 байт и до 4096 байт после zlib уровня 9; сети внутри нет, внешние картинки не грузятся;
- код карточки после отправки не меняется; бот меняет только chat_data (до 1024 байт);
- запрос виджета боту — до 64 байт, ответ — до 1024 байт, ответить нужно за 15 секунд;
- приложения без поддержки виджетов показывают только подпись: в ней запрос и лучшая цена, а сама подпись
  стоит над карточкой и служит её заголовком.

Модуль не ходит в сеть и не читает базу: только собирает HTML, подпись и chat_data.
"""

import html
import json
import os
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

import config

MAX_OFFERS = 8
MAX_WATCHES = 8
# Telegram обрезает карточку выше 600 px, прокрутки внутри нет. Строки предложений убираются с конца,
# пока карточка не уложится: кнопки «Следить» и «Обновить» внизу должны оставаться видны при любом шрифте.
FIT_HEIGHT = 560
MAX_TITLE = 70
MAX_URL = 256              # предел openLink; длиннее — строка остаётся без ссылки
HTML_LIMIT = 32768
COMPRESSED_LIMIT = 3900    # предел Telegram — 4096; запас на расхождение реализаций zlib
DATA_LIMIT = 1024
CAPTION_LIMIT = 1024
REQUEST_WATCH = "w"
REQUEST_REFRESH = "r"
REQUEST_DELETE_PREFIX = "d"

_MODES = ("off", "admins", "all")


def feature_mode(env_name: str) -> str:
    """off (по умолчанию) | admins | all. Читается при каждом вызове: выключатель не требует пересборки."""
    mode = os.getenv(env_name, "").strip().lower()
    return mode if mode in _MODES else "off"


def feature_enabled(env_name: str, user_id: int) -> bool:
    mode = feature_mode(env_name)
    if mode == "all":
        return True
    return mode == "admins" and user_id in config.ADMIN_TELEGRAM_IDS


def search_widget_mode() -> str:
    return feature_mode("TELEGRAM_SEARCH_WIDGET")


def search_widget_enabled(user_id: int) -> bool:
    return feature_enabled("TELEGRAM_SEARCH_WIDGET", user_id)


def watches_widget_mode() -> str:
    return feature_mode("TELEGRAM_WATCHES_WIDGET")


def watches_widget_enabled(user_id: int) -> bool:
    return feature_enabled("TELEGRAM_WATCHES_WIDGET", user_id)


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
    """До MAX_OFFERS предложений в виде, который зашивается в карточку. Порядок = порядок индексов.

    От каждого магазина берётся одно предложение — самое дешёвое: иначе три расцветки одного телефона
    в одном магазине вытесняют из карточки остальные магазины (решение владельца, 10.11.2026).
    """
    offers, seen_shops = [], set()
    for item in sorted(items, key=lambda it: int(it.get("current_price") or 0)):
        price = int(item.get("current_price") or 0)
        if price <= 0:
            continue
        shop_key = " ".join(str(item.get("shop") or "").lower().split())
        if shop_key in seen_shops:
            continue
        seen_shops.add(shop_key)
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
.c{display:flex;gap:6px;margin:6px 0 4px}
button{font:inherit;border:0;border-radius:8px;padding:6px 10px;background:var(--tg-theme-secondary-bg-color,#eee);color:var(--tg-theme-text-color,#222)}
button:active{opacity:.6}
button.on{background:var(--tg-theme-button-color,#39f);color:var(--tg-theme-button-text-color,#fff)}
.c button{font-size:12px;padding:4px 10px}
.r{display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid var(--tg-theme-section-separator-color,#ddd)}
.n{flex:1;min-width:0}
.n div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.p{font-weight:600;white-space:nowrap;text-align:right}
.p .h{font-weight:400}
.b{color:var(--tg-theme-accent-text-color,#2a8)}
.x{opacity:.5}
.a{display:flex;gap:8px;margin-top:8px}
#w{flex:1}
</style>
<div class="h" id="m"></div>
<div class="c" id="c"><button data-s="p" class="on">Дешевле</button><button data-s="d">Скидка больше</button><button data-s="o">Только со скидкой</button></div>
<div id="l"></div>
<div class="a"><button id="w">🔔 Следить за ценой</button><button id="u">↻ Обновить</button></div>
<div class="h" id="s">&nbsp;</div>
<script>
var D=__OFFERS__,T=window.TelegramWidget,S="p",P=null,At=__SENT__,H=__HEIGHT__;
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
var p=el("div","p"),g=x.o&&x.p?Math.round((x.o-x.p)/x.o*100):0;
p.appendChild(el("div",i==0&&x.p?"b":"",x.p?f(x.p):"нет в наличии"));
if(g>0)p.appendChild(el("div","h","−"+g+"% от "+f(x.o)));
r.appendChild(n);r.appendChild(p);l.appendChild(r)});
if(!a.length)l.appendChild(el("div","r h","Сейчас предложений со скидкой нет"));
var d=new Date(At*1e3);
$("m").textContent="Магазинов: "+D.length+" · обновлено "+d.toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
fit()}
function fit(){
var l=$("l"),n=0;
while(l.children.length>1&&document.body.offsetHeight>H&&n++<D.length)l.removeChild(l.lastChild)}
function ask(c,b){
var s=$("s");if(!T){s.textContent="Доступно только в Telegram";return}
s.textContent="Отправляю…";
T.request(c).then(function(r){s.textContent=r.result||"Готово";if(b&&r.result)b.classList.add("on")},
function(e){s.textContent=e.code=="RATE_LIMITED"||e.code=="BUSY"?"Слишком часто, подождите немного":e.code=="TIMEOUT"?"Бот не ответил, попробуйте позже":"Не получилось, попробуйте позже"})}
$("c").addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;S=b.dataset.s;
[].forEach.call(this.children,function(x){x.classList.toggle("on",x==b)});draw()});
$("w").addEventListener("click",function(){ask("__WATCH__",this)});
$("u").addEventListener("click",function(){ask("__REFRESH__")});
if(T){st();T.onEvent("chatDataChanged",function(){st();draw()})}
draw();
</script>"""


def _render(offers: List[Dict[str, Any]], sent_at: int) -> str:
    rows = [[o["shop"], o["price"], o["old"], o["url"], o["title"]] for o in offers]
    return (_TEMPLATE
            .replace("__OFFERS__", _script_json(rows))
            .replace("__SENT__", str(int(sent_at)))
            .replace("__HEIGHT__", str(FIT_HEIGHT))
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
        widget_html = _render(offers, sent_at)
        if len(widget_html.encode("utf-8")) <= HTML_LIMIT and compressed_size(widget_html) <= COMPRESSED_LIMIT:
            return widget_html, offers
        offers = offers[:-1]
    return None


def build_caption(query: str, offers: List[Dict[str, Any]]) -> str:
    """Одна строка над карточкой (HTML Telegram): заголовок, текст уведомления и замена карточки там,
    где виджеты не поддерживаются. Списка цен здесь нет — он дублировал бы карточку."""
    best = min(o["price"] for o in offers)
    price = f"{best:,}".replace(",", " ") + " ₸"
    return f"🔍 <b>{html.escape(_clean(query, 80))}</b> — от {price}"


def build_chat_data(offers: List[Dict[str, Any]], prices_by_id: Dict[str, int],
                    now: Optional[float] = None) -> str:
    """Свежие цены по индексам карточки; 0 — предложение пропало из актуальной выдачи."""
    payload = {"t": int(now if now is not None else time.time()),
               "p": [int(prices_by_id.get(o["id"]) or 0) for o in offers]}
    data = json.dumps(payload, separators=(",", ":"))
    if len(data.encode("utf-8")) > DATA_LIMIT:
        raise ValueError("chat_data карточки не помещается в предел Telegram")
    return data


# --- TG-07: Виджет /watches ---

_WATCHES_TEMPLATE = """<style>
body{margin:0;padding:10px 12px 8px;font:var(--tg-text-font-size,15px)/1.35 system-ui,sans-serif;color:var(--tg-theme-text-color,#222)}
.h{color:var(--tg-theme-hint-color,#888);font-size:12px}
button{font:inherit;border:0;border-radius:8px;padding:6px 10px;background:var(--tg-theme-secondary-bg-color,#eee);color:var(--tg-theme-text-color,#222);cursor:pointer}
button:active{opacity:.6}
.r{display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid var(--tg-theme-section-separator-color,#ddd)}
.r.off{opacity:.45}
.n{flex:1;min-width:0}
.n div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t{font-weight:600}
button.del{font-size:13px;padding:4px 8px;background:transparent;border:1px solid var(--tg-theme-section-separator-color,#ddd);border-radius:6px;color:var(--tg-theme-hint-color,#888)}
button.del:active{background:var(--tg-theme-secondary-bg-color,#eee)}
.off-badge{font-size:11px;color:var(--tg-theme-hint-color,#888);white-space:nowrap}
.a{display:flex;gap:8px;margin-top:8px}
#u{flex:1}
</style>
<div class="h" id="m"></div>
<div id="l"></div>
<div class="a"><button id="u">↻ Обновить</button></div>
<div class="h" id="s">&nbsp;</div>
<script>
var W=__WATCHES__,T=window.TelegramWidget,A=null,At=__SENT__,H=__HEIGHT__;
function $(i){return document.getElementById(i)}
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function st(){try{var o=JSON.parse(T.chatData);if(o&&o.a){A=o.a;At=o.t||At}}catch(e){}}
function draw(){
var act=0,l=$("l");l.textContent="";
W.forEach(function(w){
var id=w[0],title=w[1],desc=w[2],icon=w[3];
var on=A?(A.indexOf(id)!==-1):(w[4]===1);
if(on)act++;
var r=el("div","r"+(on?"":" off")),n=el("div","n");
n.appendChild(el("div","t",icon+" "+title));
n.appendChild(el("div","h",desc));
r.appendChild(n);
if(on){
var b=el("button","del","🔕");b.title="Отключить";
b.addEventListener("click",function(){del(id,b)});
r.appendChild(b);
}else{
r.appendChild(el("div","off-badge","отключено"));
}
l.appendChild(r);
});
if(!W.length)l.appendChild(el("div","r h","Активных наблюдений нет"));
var d=new Date(At*1e3);
$("m").textContent="Активных: "+act+" из "+W.length+" · "+d.toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
fit();
}
function fit(){
var l=$("l"),n=0;
while(l.children.length>1&&document.body.offsetHeight>H&&n++<W.length)l.removeChild(l.lastChild);
}
function del(id,b){
var s=$("s");if(!T){s.textContent="Доступно только в Telegram";return}
s.textContent="Отключаю…";b.disabled=true;
T.request("__DEL_PREFIX__"+id).then(function(r){
s.textContent=r.result||"Готово";
if(A){var i=A.indexOf(id);if(i!==-1)A.splice(i,1);}
else{A=W.filter(function(x){return x[0]!==id&&x[4]===1}).map(function(x){return x[0]});}
draw();
},function(e){
b.disabled=false;
s.textContent=e.code=="RATE_LIMITED"||e.code=="BUSY"?"Слишком часто, подождите немного":e.code=="TIMEOUT"?"Бот не ответил, попробуйте позже":"Не получилось, попробуйте позже";
});
}
function refresh(){
var s=$("s");if(!T){s.textContent="Доступно только в Telegram";return}
s.textContent="Обновляю…";
T.request("__REFRESH__").then(function(r){
s.textContent=r.result||"Готово";
},function(e){s.textContent="Не получилось обновить";});
}
$("u").addEventListener("click",refresh);
if(T){st();T.onEvent("chatDataChanged",function(){st();draw()})}
draw();
</script>"""


def _kind_icon(kind: str) -> str:
    icons = {
        "search": "🔍",
        "product": "📦",
        "model": "📱",
        "category": "🏷",
        "shop": "🏪",
        "deal": "⚡",
        "arbitrage": "⚖️",
    }
    return icons.get(kind, "🔔")


def _format_watch_condition(w: Dict[str, Any]) -> str:
    cond = str(w.get("condition") or "")
    thresh = w.get("threshold")
    if cond == "any_drop":
        return "любое снижение цены"
    if cond == "target_price" and thresh is not None:
        try:
            return f"цена ниже {int(thresh):,} ₸".replace(",", " ")
        except (ValueError, TypeError):
            pass
    if cond == "drop_pct" and thresh is not None:
        return f"скидка от {thresh}%"
    if cond == "drop_amount" and thresh is not None:
        try:
            return f"снижение от {int(thresh):,} ₸".replace(",", " ")
        except (ValueError, TypeError):
            pass
    if cond == "in_stock":
        return "появление в наличии"
    if cond == "best_price":
        return "лучшая цена"
    desc = str(w.get("description") or "")
    if desc:
        return _clean(desc, 50)
    return "снижение цены"


def pick_watches(watches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """До MAX_WATCHES наблюдений для карточки."""
    items = []
    for w in watches:
        wid = w.get("id")
        if not wid:
            continue
        title = w.get("title") or w.get("target") or f"Наблюдение #{wid}"
        kind = str(w.get("kind") or "search")
        items.append({
            "id": int(wid),
            "title": _clean(title, 45),
            "desc": _format_watch_condition(w),
            "icon": _kind_icon(kind),
            "active": 1 if w.get("is_active", 1) else 0,
        })
        if len(items) == MAX_WATCHES:
            break
    return items


def _render_watches(watches: List[Dict[str, Any]], sent_at: int) -> str:
    rows = [[w["id"], w["title"], w["desc"], w["icon"], w["active"]] for w in watches]
    return (_WATCHES_TEMPLATE
            .replace("__WATCHES__", _script_json(rows))
            .replace("__SENT__", str(int(sent_at)))
            .replace("__HEIGHT__", str(FIT_HEIGHT))
            .replace("__DEL_PREFIX__", REQUEST_DELETE_PREFIX)
            .replace("__REFRESH__", REQUEST_REFRESH))


def build_watches_widget(watches: List[Dict[str, Any]],
                         now: Optional[float] = None) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """HTML карточки наблюдений и наблюдения, которые в неё попали."""
    picked = pick_watches(watches)
    if not picked:
        return None
    sent_at = int(now if now is not None else time.time())
    while picked:
        widget_html = _render_watches(picked, sent_at)
        if len(widget_html.encode("utf-8")) <= HTML_LIMIT and compressed_size(widget_html) <= COMPRESSED_LIMIT:
            return widget_html, picked
        picked = picked[:-1]
    return None


def build_watches_caption(watches: List[Dict[str, Any]], total_count: int) -> str:
    """Подпись над карточкой наблюдений."""
    count = len(watches)
    if total_count > count:
        return f"🔔 <b>Ваши наблюдения за ценами</b> ({total_count} активных, показано {count})"
    return f"🔔 <b>Ваши наблюдения за ценами</b> ({count} активных)"


def build_watches_chat_data(watches: List[Dict[str, Any]], active_ids: Any,
                            now: Optional[float] = None) -> str:
    """Обновлённое состояние активности наблюдений."""
    active_set = set(active_ids)
    payload = {
        "t": int(now if now is not None else time.time()),
        "a": [w["id"] for w in watches if w["id"] in active_set]
    }
    data = json.dumps(payload, separators=(",", ":"))
    if len(data.encode("utf-8")) > DATA_LIMIT:
        raise ValueError("chat_data карточки наблюдений не помещается в предел Telegram")
    return data
