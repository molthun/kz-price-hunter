"""Narrow local connection editor. No plaintext secret is returned to the browser."""
import re
from urllib.parse import urlsplit
import config

FIELDS={'local_api_base','local_model','local_api_key'}


def view(settings=None):
    settings=config.load_settings() if settings is None else settings
    return dict(settings=dict(local_api_base=settings.get('local_api_base') or '',local_model=settings.get('local_model') or '',
                              key_configured=bool(settings.get('local_api_key'))),
                revision=config.local_connection_revision(settings),ai_enabled=settings.get('ai_search_enabled') is not False)


def parse(body):
    allowed={'revision','local_api_base','local_model','key_action','local_api_key'}
    if not isinstance(body,dict) or set(body)-allowed:raise ValueError('Эта форма меняет только локальное подключение')
    if not isinstance(body.get('revision'),str) or not re.fullmatch('[a-f0-9]{64}',body['revision']):raise ValueError('Обновите форму настроек')
    result={}
    for key,limit in (('local_api_base',200),('local_model',150)):
        value=body.get(key)
        if not isinstance(value,str) or len(value)>limit:raise ValueError('Проверьте адрес сервера и имя модели')
        result[key]=value.strip()
    base=result['local_api_base']
    if base:
        try:
            parsed=urlsplit(base)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError()
            if parsed.port is not None and not 1<=parsed.port<=65535:raise ValueError()
        except ValueError:raise ValueError('Адрес: http(s)://хост:порт/v1, ключ укажите отдельным полем') from None
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}',result['local_model']):raise ValueError('Укажите корректное имя модели')
    action=body.get('key_action','preserve')
    if action not in ('preserve','replace','remove'):raise ValueError('Выберите действие с ключом')
    if action=='replace':
        key=body.get('local_api_key')
        if not isinstance(key,str) or not key.strip() or len(key)>4096 or key in ('***','...') or any(ord(c)<32 for c in key):raise ValueError('Укажите новый ключ без переноса строк')
        result['local_api_key']=key.strip()
    elif 'local_api_key' in body:raise ValueError('Ключ передаётся только при явной замене')
    elif action=='remove':result['local_api_key']=''
    return result


def save(body):
    patch=parse(body)
    return view(config.save_settings(patch,expected_local_revision=body['revision']))


def for_check(body):
    patch=parse(body);current=config.load_settings()
    if body['revision']!=config.local_connection_revision(current):raise config.LocalSettingsConflict('Подключение изменилось. Обновите форму перед проверкой.')
    if not patch['local_api_base'] or not patch['local_model']:raise ValueError('Для проверки укажите адрес и модель')
    return patch['local_api_base'],patch['local_model'],patch.get('local_api_key',current.get('local_api_key') or '')


def public_check(value,key):
    if isinstance(value,str):return value.replace(key,'***') if key else value
    if isinstance(value,list):return [public_check(v,key) for v in value]
    if isinstance(value,dict):return {k:public_check(v,key) for k,v in value.items()}
    return value
