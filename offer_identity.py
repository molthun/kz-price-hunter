"""Идентичность предложения: магазин + товар магазина + подтверждённое место продажи.

Адаптеры отдают `id` товара в магазине (например `kaspi_123`). В базе предложение
хранится как `kaspi_123@almaty`: одна и та же позиция в разных городах — разные записи,
live-цена одного города не перезаписывает цену другого.
"""
from typing import Any, Dict, Iterable, Optional

from config import CITIES_KZ

# Цена действует по всей стране (например, общая цена Forte Market)
NATIONWIDE = "Казахстан"
UNKNOWN_LOCATION = "Регион не подтверждён"

_NATIONWIDE_LABELS = {NATIONWIDE, "Астана / Казахстан", "Все регионы"}
_SLUG_BY_NAME = {c["name"]: c["id"] for c in CITIES_KZ.values()}


def location_slug(city: Optional[str]) -> str:
    """Короткий код места продажи для id: astana, almaty, …, kz или unknown."""
    name = (city or "").strip()
    if name in _SLUG_BY_NAME:
        return _SLUG_BY_NAME[name]
    if name in _NATIONWIDE_LABELS:
        return "kz"
    return "unknown"


def offer_id(product: Dict[str, Any]) -> str:
    """Идемпотентно: уже составной id не меняется."""
    base = str(product["id"])
    if "@" in base:
        return base
    return f"{base}@{location_slug(product.get('city'))}"


def assign_offer_ids(products: Iterable[Dict[str, Any]]):
    """Проставляет составные id на месте (список/ScanResult сохраняется)."""
    for product in products:
        product["id"] = offer_id(product)
    return products


def city_config(city: Optional[str]) -> Dict[str, Any]:
    """Конфигурация города по названию или коду; неизвестный/«Все» — Астана (фактически опрашиваемый город)."""
    name = (city or "").strip()
    return next((c for c in CITIES_KZ.values() if name in (c["name"], c["id"])), CITIES_KZ["astana"])
