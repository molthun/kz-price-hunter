"""Scoped scan settings editor; no scheduler transitions or scan starts."""
import re
import math
import config

FIELDS={'scan_interval_minutes','enabled_shops'}


def view(settings=None):
    settings=config.load_settings() if settings is None else settings
    shops=[dict(id=key,name=name,enabled=bool(settings['enabled_shops'].get(key,True))) for key,name in config.SHOP_KEYS.items()]
    plan=config.get_wave_plan(enabled_categories=settings.get('enabled_categories'),hot_categories=settings.get('hot_categories',config.DEFAULT_HOT_CATEGORIES),wave_size=settings.get('wave_size',2),wave_mode=settings.get('wave_mode','rolling'))
    return dict(settings=dict(scan_interval_minutes=settings['scan_interval_minutes'],shops=shops),revision=config.scan_settings_revision(settings),
                limits=dict(min_minutes=config.SCAN_INTERVAL_MIN_MINUTES,max_minutes=config.SCAN_INTERVAL_MAX_MINUTES),
                schedule=dict(wave_mode=settings.get('wave_mode','rolling'),total_waves=plan['total_waves'],base_wave_interval_minutes=config.get_wave_interval_seconds(settings,plan['total_waves'])/60,
                              adaptive_stage=settings.get('adaptive_scheduler_stage','off')))


def parse(body):
    if not isinstance(body,dict) or set(body)!={'revision','scan_interval_minutes','enabled_shops'}:raise ValueError('Эта форма меняет только периодичность и подключённые магазины')
    if not isinstance(body['revision'],str) or not re.fullmatch('[a-f0-9]{64}',body['revision']):raise ValueError('Обновите форму настроек')
    minutes=body['scan_interval_minutes']
    if type(minutes) is not int or not config.SCAN_INTERVAL_MIN_MINUTES<=minutes<=config.SCAN_INTERVAL_MAX_MINUTES:raise ValueError('Интервал должен быть целым числом от 5 минут до 30 дней')
    shops=body['enabled_shops']
    if not isinstance(shops,dict) or set(shops)!=set(config.SHOP_KEYS) or any(type(v) is not bool for v in shops.values()):raise ValueError('Обновите полный список подключённых магазинов')
    return dict(scan_interval_minutes=minutes,enabled_shops=shops)


def save(body):
    return view(config.save_settings(parse(body),expected_scan_revision=body['revision']))


def runtime(state):
    try:progress=max(0,min(100,float(state.get('progress_pct') or 0)))
    except (TypeError,ValueError):progress=0
    if not math.isfinite(progress):progress=0
    return dict(is_running=bool(state.get('is_running')),current_shop=str(state.get('current_shop') or '')[:120],
                current_category=str(state.get('current_category') or '')[:160],progress_pct=progress,
                last_completed=state.get('last_completed'),has_error=bool(state.get('error')))
