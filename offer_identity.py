"""Идентичность предложения: магазин + товар магазина + подтверждённое место продажи.

Адаптеры отдают `id` товара в магазине (например `kaspi_123`). В базе предложение
хранится как `kaspi_123@almaty`: одна и та же позиция в разных городах — разные записи,
live-цена одного города не перезаписывает цену другого.
"""
from typing import Any, Dict, Iterable, Optional

from config import CITIES_KZ, SHOP_KEYS

# Цена действует по всей стране (например, общая цена Forte Market)
NATIONWIDE = "Казахстан"
UNKNOWN_LOCATION = "Регион не подтверждён"

_NATIONWIDE_LABELS = {NATIONWIDE, "Астана / Казахстан", "Алматы / Казахстан", "Все регионы"}
_SLUG_BY_NAME = {c["name"]: c["id"] for c in CITIES_KZ.values()}


def location_slug(city: Optional[str]) -> str:
    """Короткий код места продажи для id: astana, almaty, …, kz или unknown."""
    name = (city or "").strip()
    if name in _SLUG_BY_NAME:
        return _SLUG_BY_NAME[name]
    if name in _NATIONWIDE_LABELS:
        return "kz"
    return "unknown"


# Суффикс id — ключ хранимой записи, а не смысл города. 6.41.0 сделал эти метки общенациональными
# для поиска (location_slug → kz), но записи прода хранятся под прежними суффиксами; смена суффикса
# без переноса данных создавала новые id и снимала старые, разрывая историю цен и наблюдения
# (docs/STAND_WAVE_CYCLE_2026-11-20.md §7). Поэтому id строится с прежним суффиксом.
_LEGACY_ID_SLUG = {"Алматы / Казахстан": "unknown"}
# Фид Белого Ветра до 6.41.0 подписывался «Астана» — его записи хранятся как …@astana
_LEGACY_SHOP_ID_SLUG = {("Белый Ветер", "Астана / Казахстан"): "astana"}


def id_location_slug(city: Optional[str], shop: Optional[str] = None) -> str:
    """Суффикс места продажи в id: как location_slug, но с прежними значениями для меток,
    смысл которых менялся после появления записей (совместимость идентичности)."""
    name = (city or "").strip()
    legacy = _LEGACY_SHOP_ID_SLUG.get(((shop or "").strip(), name)) or _LEGACY_ID_SLUG.get(name)
    return legacy or location_slug(name)


# Префикс магазина в id (R-H01): у старых адаптеров исторический префикс — их id не меняются;
# остальные магазины получают ключ из SHOP_KEYS. Без префикса одинаковый id товара в разных
# магазинах давал одну запись в БД.
_LEGACY_ID_PREFIX = {"technodom": "td", "forcecom": "fc", "fourmobile": "4mobile", "fortemarket": "forte"}
_KEY_BY_SHOP_NAME = {name: key for key, name in SHOP_KEYS.items()}


def id_prefix(shop_key: str) -> str:
    return _LEGACY_ID_PREFIX.get(shop_key, shop_key)


def namespaced_id(raw_id: str, shop: Optional[str]) -> str:
    """id с префиксом магазина; уже префиксованный не меняется. Неизвестный магазин — без изменений."""
    key = _KEY_BY_SHOP_NAME.get((shop or "").strip())
    if not key:
        return raw_id
    prefix = id_prefix(key) + "_"
    return raw_id if raw_id.startswith(prefix) else prefix + raw_id


def offer_id(product: Dict[str, Any]) -> str:
    """Идемпотентно: уже составной id не меняется."""
    base = str(product["id"])
    if "@" in base:
        return base
    shop = product.get("shop")
    return f"{namespaced_id(base, shop)}@{id_location_slug(product.get('city'), shop)}"


def assign_offer_ids(products: Iterable[Dict[str, Any]]):
    """Проставляет составные id на месте (список/ScanResult сохраняется)."""
    for product in products:
        product["id"] = offer_id(product)
    return products


def city_config(city: Optional[str]) -> Dict[str, Any]:
    """Конфигурация города по названию или коду; неизвестный/«Все» — Астана (фактически опрашиваемый город)."""
    name = (city or "").strip()
    return next((c for c in CITIES_KZ.values() if name in (c["name"], c["id"])), CITIES_KZ["astana"])
