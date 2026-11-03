"""
Product Search Attributes Indexing.
Maps rich source evidence facts into structured search filter attributes (KZPH-G6-01).
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ATTRIBUTE_LABELS: Dict[str, str] = {
    'storage': 'Встроенная память',
    'ram': 'Оперативная память',
    'cpu': 'Процессор',
    'color': 'Цвет',
    'screen_size': 'Диагональ экрана',
    'resolution': 'Разрешение экрана',
    'refresh_rate': 'Частота обновления',
    'chipset': 'Чипсет',
    'socket': 'Сокет',
    'memory_type': 'Тип памяти',
    'form_factor': 'Форм-фактор',
    'gpu': 'Видеокарта',
    'vram': 'Объем видеопамяти',
    'battery': 'Емкость аккумулятора',
    '5g': 'Поддержка 5G',
    'nfc': 'NFC',
    'sim_count': 'Количество SIM-карт',
    'sim_type': 'Тип SIM-карты',
    'material': 'Материал корпуса',
    'weight': 'Вес',
    'season': 'Сезонность',
    'studs': 'Шипы',
    'connection_type': 'Тип подключения',
    'door_type': 'Тип дверцы',
    'control_panel': 'Панель управления',
    'capacity': 'Объем',
    'diameter': 'Диаметр',
}

FACT_KEY_MAP: Dict[str, Tuple[str, str]] = {
    # Память и накопители
    'размер встроенной памяти': ('storage', 'Встроенная память'),
    'объем встроенной памяти': ('storage', 'Встроенная память'),
    'объём встроенной памяти': ('storage', 'Встроенная память'),
    'встроенная память': ('storage', 'Встроенная память'),
    'объем накопителя': ('storage', 'Объем накопителя'),
    'объём накопителя': ('storage', 'Объем накопителя'),
    'объем ssd': ('storage', 'Объем SSD'),
    'объём ssd': ('storage', 'Объем SSD'),
    'емкость ssd': ('storage', 'Объем SSD'),
    'ёмкость ssd': ('storage', 'Объем SSD'),

    # Оперативная память
    'размер оперативной памяти': ('ram', 'Оперативная память'),
    'объем оперативной памяти': ('ram', 'Оперативная память'),
    'объём оперативной памяти': ('ram', 'Оперативная память'),
    'оперативная память': ('ram', 'Оперативная память'),
    'объем озу': ('ram', 'Оперативная память'),
    'объём озу': ('ram', 'Оперативная память'),

    # Процессор и сокет
    'процессор': ('cpu', 'Процессор'),
    'модель процессора': ('cpu', 'Процессор'),
    'сокет': ('socket', 'Сокет'),
    'разъем процессора': ('socket', 'Сокет'),
    'разъём процессора': ('socket', 'Сокет'),

    # Экран
    'диагональ': ('screen_size', 'Диагональ экрана'),
    'диагональ экрана': ('screen_size', 'Диагональ экрана'),
    'разрешение экрана': ('resolution', 'Разрешение экрана'),
    'разрешение дисплея': ('resolution', 'Разрешение экрана'),
    'частота обновления экрана': ('refresh_rate', 'Частота обновления'),

    # Цвет и корпус
    'цвет': ('color', 'Цвет'),
    'материал': ('material', 'Материал корпуса'),
    'материал корпуса': ('material', 'Материал корпуса'),

    # Связь и интерфейсы
    'поддержка 5g': ('5g', 'Поддержка 5G'),
    '5g': ('5g', 'Поддержка 5G'),
    'технология nfc': ('nfc', 'NFC'),
    'nfc': ('nfc', 'NFC'),
    'количество sim-карт': ('sim_count', 'Количество SIM-карт'),
    'тип sim-карты': ('sim_type', 'Тип SIM-карты'),

    # Комплектующие
    'чипсет': ('chipset', 'Чипсет'),
    'тип памяти': ('memory_type', 'Тип памяти'),
    'тип оперативной памяти': ('memory_type', 'Тип памяти'),
    'форм-фактор': ('form_factor', 'Форм-фактор'),
    'форм-фактор материнской платы': ('form_factor', 'Форм-фактор'),
    'видеокарта': ('gpu', 'Видеокарта'),
    'видеопроцессор': ('gpu', 'Видеокарта'),
    'объем видеопамяти': ('vram', 'Объем видеопамяти'),
    'объём видеопамяти': ('vram', 'Объем видеопамяти'),
}


def normalize_attribute_value(key: str, raw_value: Any) -> Optional[str]:
    """Приводит значение характеристики к аккуратному стандартному виду."""
    if raw_value is None:
        return None
    val = str(raw_value).strip()
    if not val or len(val) > 80:
        return None

    # Булевы значения
    if val.casefold() in ('да', 'true', '1', 'есть'):
        return 'Да'
    if val.casefold() in ('нет', 'false', '0'):
        return 'Нет'

    # Память (ГБ, ТБ)
    if key in ('storage', 'ram', 'vram'):
        m_tb = re.search(r'(?i)\b(\d+)\s*(?:tb|тб)\b', val)
        if m_tb:
            return f"{m_tb.group(1)} ТБ"
        m_gb = re.search(r'(?i)\b(\d+)\s*(?:gb|гб|гбайт)\b', val)
        if m_gb:
            return f"{m_gb.group(1)} ГБ"
        m_mb = re.search(r'(?i)\b(\d+)\s*(?:mb|мб)\b', val)
        if m_mb:
            return f"{m_mb.group(1)} МБ"

    # Цвет: с заглавной буквы
    if key == 'color':
        cleaned = re.sub(r'[^\w\s-]', '', val).strip()
        if cleaned:
            return cleaned.capitalize()

    # Диагональ
    if key == 'screen_size':
        m_inch = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:дюйм|"|\'\'|in)?', val)
        if m_inch:
            num = m_inch.group(1).replace(',', '.')
            return f"{num}\""

    # Частота обновления
    if key == 'refresh_rate':
        m_hz = re.search(r'(\d+)\s*(?:гц|hz)?', val, re.IGNORECASE)
        if m_hz:
            return f"{m_hz.group(1)} Гц"

    # Чипсет / сокет: нормализация пробелов и регистра
    if key in ('chipset', 'socket'):
        return ' '.join(val.split())

    return val[:80]


def index_product_facts(conn, product_id: str, facts: List[Dict[str, Any]], product_title: Optional[str] = None) -> int:
    """Индексирует подтверждённые факты товара в таблицу product_search_attributes."""
    if not product_id or not facts:
        return 0

    if not product_title:
        row = conn.execute('SELECT title FROM canonical_products WHERE id=?', (product_id,)).fetchone()
        if not row or not row[0]:
            return 0
        product_title = row[0]

    now_iso = datetime.now(timezone.utc).isoformat()
    indexed_count = 0

    for f in facts:
        if not isinstance(f, dict):
            continue
        raw_name = str(f.get('name') or '').strip().casefold()
        mapping = FACT_KEY_MAP.get(raw_name)
        if not mapping:
            continue
        key, _ = mapping
        norm_val = normalize_attribute_value(key, f.get('value'))
        if not norm_val:
            continue

        conn.execute('''
            INSERT INTO product_search_attributes (product_id, key, value, evidence, source_title, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, key) DO UPDATE SET
                value=excluded.value,
                evidence=excluded.evidence,
                source_title=excluded.source_title,
                updated_at=excluded.updated_at
        ''', (product_id, key, norm_val, f.get('name', key)[:160], product_title, now_iso))
        indexed_count += 1

    return indexed_count


def sync_all_source_attributes(conn, limit: int = 50000) -> int:
    """Индексирует факты из product_source_evidence для всех имеющихся товаров."""
    cursor = conn.execute('''
        SELECT e.product_id, p.title, e.facts_json
        FROM product_source_evidence e
        JOIN canonical_products p ON p.id = e.product_id
        WHERE e.status = 'ready' AND e.facts_json != '[]'
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()

    total_indexed = 0
    for pid, title, facts_json in rows:
        try:
            facts = json.loads(facts_json)
            if isinstance(facts, list) and facts:
                total_indexed += index_product_facts(conn, pid, facts, title)
        except Exception:
            continue

    if total_indexed > 0 and hasattr(conn, 'commit'):
        conn.commit()

    return total_indexed
