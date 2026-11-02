"""Allowlisted scraper failure metadata. Never serialize exception messages or URLs."""
import contextlib
import contextvars
import re

LABELS = {
    'dns': 'Не удалось разрешить адрес магазина',
    'timeout': 'Магазин не ответил вовремя',
    'tls': 'Не удалось проверить защищённое соединение',
    'connection': 'Не удалось соединиться с магазином',
    'access_challenge': 'Магазин закрыл доступ проверкой Cloudflare',
    'http_403': 'Магазин запретил доступ',
    'http_404': 'Страница магазина не найдена',
    'http_429': 'Магазин ограничил частоту запросов',
    'http_5xx': 'Ошибка сервера магазина',
    'http_other': 'Магазин отклонил запрос',
    'cooldown': 'Действует пауза перед запросами к магазину',
    'parse': 'Не удалось разобрать ответ магазина',
    'empty': 'Карточки не найдены, полнота не подтверждена',
    'end_unconfirmed': 'Конец каталога не подтверждён',
    'unknown': 'Причина сбоя не определена',
}
PHASES = {'fetch', 'parse', 'category'}
_capture = contextvars.ContextVar('source_failure_capture', default=None)
_PATTERN = re.compile(r'SC02\[code=([a-z0-9_]+);phase=([a-z]+);page=(\d+|-);http=(\d+|-)\]')


def make(code='unknown', *, phase='category', page=None, http_status=None):
    return {'error_code': code if isinstance(code,str) and code in LABELS else 'unknown',
            'phase': phase if isinstance(phase,str) and phase in PHASES else 'category',
            'page': page if type(page) is int and 1 <= page <= 100000 else None,
            'http_status': http_status if type(http_status) is int and 100 <= http_status <= 599 else None}


def clean(value):
    if not isinstance(value, dict): return None
    return make(value.get('error_code'), phase=value.get('phase'), page=value.get('page'), http_status=value.get('http_status'))


@contextlib.contextmanager
def capture():
    state = {'http_status': None, 'failure': None}
    token = _capture.set(state)
    try: yield state
    finally: _capture.reset(token)


def observe_http(status):
    state = _capture.get()
    if state is not None and type(status) is int and 100 <= status <= 599:
        state['http_status'] = status
        state['failure'] = None


def observe_exception(error):
    state = _capture.get()
    if state is not None: state['failure'] = classify(error, phase='fetch')


def _http_code(status):
    return 'http_403' if status == 403 else 'http_404' if status == 404 else 'http_429' if status == 429 else 'http_5xx' if status >= 500 else 'http_other'


def classify(error, *, page=None, phase='category', observed=None):
    chain=[];seen=set();current=error
    while isinstance(current, BaseException) and id(current) not in seen and len(chain)<6:
        chain.append(current);seen.add(id(current))
        current=current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
    for exc in reversed(chain):
        name=type(exc).__name__.lower()
        # Only recognized constant signals are extracted; no text is returned or logged.
        text=str(exc)[:4000]
        if name=='hostcooldown': return make('cooldown',phase='fetch',page=page)
        if 'dns' in name or re.search(r'net::ERR_NAME_NOT_RESOLVED|Could not resolve host',text,re.I): code='dns'
        elif isinstance(exc,TimeoutError) or 'timeout' in name or re.search(r'net::ERR_TIMED_OUT',text): code='timeout'
        elif 'ssl' in name or 'certificate' in name or re.search(r'net::ERR_CERT_[A-Z_]+|net::ERR_SSL_[A-Z_]+',text): code='tls'
        elif isinstance(exc,ConnectionError) or 'connection' in name or re.search(r'net::ERR_CONNECTION_[A-Z_]+',text): code='connection'
        else: code=None
        if code: return make(code,phase='fetch',page=page)
        status=getattr(getattr(exc,'response',None),'status_code',None)
        if type(status) is not int or not 400 <= status <= 599:
            match=re.search(r'(?:^|:\s)HTTP(?:\s+Error)?\s+([45]\d\d)\b',text)
            status=int(match[1]) if match else None
        if status: return make(_http_code(status),phase='fetch',page=page,http_status=status)
        if isinstance(exc,(ValueError,KeyError,TypeError,AttributeError)):
            return make('parse',phase='parse',page=page,http_status=(observed or {}).get('http_status'))
    if observed and observed.get('failure'):
        result=clean(observed['failure']);result['page']=make(page=page)['page'];return result
    return make(phase=phase,page=page,http_status=(observed or {}).get('http_status'))


def from_legacy(value):
    """A legacy string is not new evidence; retain only embedded validated SC02 data."""
    if not isinstance(value,str): return None
    match=_PATTERN.search(value)
    if not match or match[1] not in LABELS or match[2] not in PHASES: return None
    page=int(match[3]) if match[3]!='-' else None
    status=int(match[4]) if match[4]!='-' else None
    result=make(match[1],phase=match[2],page=page,http_status=status)
    if result['page']!=page or result['http_status']!=status: return None
    return result


def describe(value):
    info=clean(value) or make()
    return ('SC02[code='+info['error_code']+';phase='+info['phase']+';page='+str(info['page'] or '-')+
            ';http='+str(info['http_status'] or '-')+'] '+LABELS[info['error_code']])


def public(value):
    info=clean(value)
    return {**info,'message':LABELS[info['error_code']]} if info else None


def result_failure(result, observed=None):
    info=clean(getattr(result,'error_info',None))
    if info: return info
    failed = bool(getattr(result,'error',None)) or (not result and not getattr(result,'complete',False))
    if failed:
        embedded = from_legacy(getattr(result,'error',None))
        if embedded: return embedded
        status = (observed or {}).get('http_status')
        if type(status) is int and 400 <= status <= 599:
            return make(_http_code(status),phase='fetch',http_status=status)
        if (observed or {}).get('failure'): return clean(observed['failure'])
        if not result: return make('empty',phase='parse',http_status=status)
        return classify(RuntimeError(),observed=observed)
    return None
