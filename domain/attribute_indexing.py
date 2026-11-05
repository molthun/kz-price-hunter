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
    # Подгузники и гигиена для малышей
    'diaper_type': 'Тип',
    'diaper_size': 'Размер',
    'weight_range': 'Вес ребенка',
    'pieces_count': 'Количество в упаковке',
    # Бытовая техника и электроника
    'total_volume': 'Общий объем',
    'defrost_system': 'Система разморозки',
    'refrigeration_defrost': 'Разморозка',
    'energy_class': 'Класс энергопотребления',
    'noise_level': 'Уровень шума',
    'load_capacity': 'Максимальная загрузка',
    'spin_speed': 'Скорость отжима',
    'program_count': 'Количество программ',
    'installation_type': 'Тип установки',
    'cleaning_type': 'Тип уборки',
    'dust_collector_type': 'Тип пылесборника',
    'power': 'Мощность',
    'type': 'Тип устройства',
    # Музыка, винил и книги
    'artist': 'Исполнитель',
    'format': 'Формат',
    'genre': 'Жанр',
    'release_year': 'Год издания',
    'author': 'Автор',
    'publisher': 'Издательство',
    'cover_type': 'Переплет',
}

FACT_KEY_MAP: Dict[str, Tuple[str, str]] = {
    # Музыка и винил
    'исполнитель': ('artist', 'Исполнитель'),
    'музыкант': ('artist', 'Исполнитель'),
    'группа': ('artist', 'Исполнитель'),
    'автор/исполнитель': ('artist', 'Исполнитель'),
    'формат': ('format', 'Формат'),
    'носитель': ('format', 'Формат'),
    'жанр': ('genre', 'Жанр'),
    'музыкальный жанр': ('genre', 'Жанр'),
    'год издания': ('release_year', 'Год издания'),
    'год': ('release_year', 'Год издания'),
    'год выпуска': ('release_year', 'Год издания'),
    # Книги
    'автор': ('author', 'Автор'),
    'издательство': ('publisher', 'Издательство'),
    'переплет': ('cover_type', 'Переплет'),
    'тип обложки': ('cover_type', 'Переплет'),
    # Бытовая техника
    'класс энергопотребления': ('energy_class', 'Класс энергопотребления'),
    'энергопотребление': ('energy_class', 'Класс энергопотребления'),
    'уровень шума': ('noise_level', 'Уровень шума'),
    'максимальная загрузка': ('load_capacity', 'Максимальная загрузка'),
    'загрузка': ('load_capacity', 'Максимальная загрузка'),
    'загрузка белья': ('load_capacity', 'Максимальная загрузка'),
    'скорость отжима': ('spin_speed', 'Скорость отжима'),
    'максимальная скорость отжима': ('spin_speed', 'Скорость отжима'),
    'количество программ': ('program_count', 'Количество программ'),
    'число программ': ('program_count', 'Количество программ'),
    'тип установки': ('installation_type', 'Тип установки'),
    'установка': ('installation_type', 'Тип установки'),
    'тип уборки': ('cleaning_type', 'Тип уборки'),
    'тип пылесборника': ('dust_collector_type', 'Тип пылесборника'),
    'пылесборник': ('dust_collector_type', 'Тип пылесборника'),
    'мощность': ('power', 'Мощность'),
    'потребляемая мощность': ('power', 'Мощность'),
    'мощность всасывания': ('power', 'Мощность'),
    'система разморозки': ('refrigeration_defrost', 'Разморозка'),
    'размораживание': ('refrigeration_defrost', 'Разморозка'),
    'тип размораживания': ('refrigeration_defrost', 'Разморозка'),
    'размораживание морозильной камеры': ('refrigeration_defrost', 'Разморозка'),
    'размораживание холодильной камеры': ('refrigeration_defrost', 'Разморозка'),
    'общий объем': ('capacity', 'Объем'),
    'полезный объем': ('capacity', 'Объем'),
    # Подгузники и гигиена для малышей
    'тип': ('diaper_type', 'Тип'),
    'тип подгузников': ('diaper_type', 'Тип'),
    'вид подгузников': ('diaper_type', 'Тип'),
    'размер': ('diaper_size', 'Размер'),
    'размер подгузников': ('diaper_size', 'Размер'),
    'размер подгузника': ('diaper_size', 'Размер'),
    'вес ребенка': ('weight_range', 'Вес ребенка'),
    'вес малыша': ('weight_range', 'Вес ребенка'),
    'минимальный вес ребенка': ('weight_range', 'Вес ребенка'),
    'максимальный вес ребенка': ('weight_range', 'Вес ребенка'),
    'количество в упаковке': ('pieces_count', 'Количество в упаковке'),
    'количество штук в упаковке': ('pieces_count', 'Количество в упаковке'),
    'количество': ('pieces_count', 'Количество в упаковке'),

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
        if val.isdigit():
            n = int(val)
            if n in (1, 2) and key == 'storage':
                return f"{n} ТБ"
            if n in (4, 6, 8, 12, 16, 24, 32, 64, 128, 256, 512, 1024):
                return f"{n} ГБ" if n < 1024 else "1 ТБ"
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

    # Подгузники и гигиена для малышей
    if key == 'diaper_type':
        low = val.casefold()
        if 'ночн' in low or 'magic night' in low:
            return 'Ночные трусики'
        if 'плаван' in low or 'swimming' in low or 'aqua' in low:
            return 'Трусики для плавания'
        if 'трусик' in low:
            return 'Подгузники-трусики'
        if 'пеленк' in low:
            return 'Одноразовые пеленки'
        if 'подгузн' in low or 'памперс' in low:
            return 'Подгузники'

    if key == 'diaper_size':
        low = val.strip()
        if re.search(r'(?i)\b(?:0|nb|newborn|0\+|новорожденн\w*)\b', low):
            return '0 (NB)'
        if re.search(r'(?i)\b(?:1)\b', low):
            return '1'
        if re.search(r'(?i)\b(?:2|s|b2)\b', low):
            return '2 (S)'
        if re.search(r'(?i)\b(?:3|m|b3|d3)\b', low):
            return '3 (M)'
        if re.search(r'(?i)\b(?:4|l|e4)\b', low):
            return '4 (L)'
        if re.search(r'(?i)\b(?:5|xl)\b', low):
            return '5 (XL)'
        if re.search(r'(?i)\b(?:6|xxl|2xl)\b', low):
            return '6 (XXL)'
        if re.search(r'(?i)\b(?:7|xxxl|3xl)\b', low):
            return '7 (XXXL)'

    if key == 'weight_range':
        m_range = re.search(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*кг', val, re.IGNORECASE)
        if m_range:
            return f"{m_range.group(1)}-{m_range.group(2)} кг"
        m_to = re.search(r'до\s*(\d+(?:\.\d+)?)\s*кг', val, re.IGNORECASE)
        if m_to:
            return f"до {m_to.group(1)} кг"
        m_plus = re.search(r'(\d+(?:\.\d+)?)\s*\+\s*кг|(\d+(?:\.\d+)?)\s*кг\+', val, re.IGNORECASE)
        if m_plus:
            num = m_plus.group(1) or m_plus.group(2)
            return f"{num}+ кг"

    if key == 'pieces_count':
        m_pc = re.search(r'(\d+)\s*(?:шт|штук|pcs)?', val, re.IGNORECASE)
        if m_pc:
            return f"{m_pc.group(1)} шт."

    # Бытовая техника
    if key == 'energy_class':
        m_ec = re.search(r'(?i)\b(A\+{1,3}|[A-G])\b', val)
        if m_ec:
            return m_ec.group(1).upper()

    if key == 'spin_speed':
        m_sp = re.search(r'(\d{3,4})\s*(?:об/?мин|rpm)?', val, re.IGNORECASE)
        if m_sp:
            return f"{m_sp.group(1)} об/мин"

    if key == 'load_capacity':
        m_lc = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:кг|kg)?', val, re.IGNORECASE)
        if m_lc:
            num = m_lc.group(1).replace(',', '.')
            return f"{num} кг"

    if key == 'noise_level':
        m_nl = re.search(r'(\d+)\s*(?:д[бб]|db)?', val, re.IGNORECASE)
        if m_nl:
            return f"{m_nl.group(1)} дБ"

    if key == 'refrigeration_defrost':
        low = val.casefold()
        if 'no frost' in low or 'total no frost' in low or 'но фрост' in low:
            return 'No Frost'
        if 'капельн' in low:
            return 'Капельная'
        if 'ручн' in low:
            return 'Ручная'

    if key == 'cleaning_type':
        low = val.casefold()
        if 'сухая' in low and 'влажная' in low:
            return 'Сухая и влажная'
        if 'сухая' in low:
            return 'Сухая'
        if 'влажная' in low or 'моющий' in low:
            return 'Влажная'

    if key == 'dust_collector_type':
        low = val.casefold()
        if 'мешок' in low:
            return 'Мешок'
        if 'контейнер' in low or 'циклон' in low:
            return 'Контейнер'
        if 'аква' in low:
            return 'Аквафильтр'

    if key == 'power':
        m_pw = re.search(r'(\d+)\s*(?:вт|w)?', val, re.IGNORECASE)
        if m_pw:
            return f"{m_pw.group(1)} Вт"

    return val[:80]


COLOR_PATTERNS = [
    (r'(?i)\b(?:черн(?:ый|ая|ое|ые|ую|ом|ого)|black)\b', 'Черный'),
    (r'(?i)\b(?:бел(?:ый|ая|ое|ые|ую|ом|ого)|white)\b', 'Белый'),
    (r'(?i)\b(?:син(?:ий|яя|ее|ие|юю|ем|его)|blue)\b', 'Синий'),
    (r'(?i)\b(?:сер(?:ый|ая|ое|ые|ую|ом|ого)|gray|grey)\b', 'Серый'),
    (r'(?i)\b(?:серебр(?:истый|истая|истое|истые|истую|истом)|silver)\b', 'Серебристый'),
    (r'(?i)\b(?:золот(?:ой|ая|ое|ые|ую|ом|ого|истый|истая|истое)|gold)\b', 'Золотистый'),
    (r'(?i)\b(?:зелен(?:ый|ая|ое|ые|ую|ом|ого)|green)\b', 'Зеленый'),
    (r'(?i)\b(?:красн(?:ый|ая|ое|ые|ую|ом|ого)|red)\b', 'Красный'),
    (r'(?i)\b(?:фиолетов(?:ый|ая|ое|ые|ую|ом|ого)|purple)\b', 'Фиолетовый'),
    (r'(?i)\b(?:розов(?:ый|ая|ое|ые|ую|ом|ого)|pink)\b', 'Розовый'),
    (r'(?i)\b(?:желт(?:ый|ая|ое|ые|ую|ом|ого)|yellow)\b', 'Желтый'),
    (r'(?i)\b(?:оранжев(?:ый|ая|ое|ые|ую|ом|ого)|orange)\b', 'Оранжевый'),
    (r'(?i)\b(?:бежев(?:ый|ая|ое|ые|ую|ом|ого)|beige)\b', 'Бежевый'),
    (r'(?i)\b(?:титан(?:овый|овая|овое|овые|овую|овом)|titanium)\b', 'Титановый'),
]


def extract_title_attributes(title: str) -> Dict[str, str]:
    """Извлекает ключевые характеристики (память, цвет, диагональ, ОЗУ) прямо из названия товара."""
    if not title:
        return {}
    attrs: Dict[str, str] = {}

    # 1. Цвет
    for pattern, color_name in COLOR_PATTERNS:
        if re.search(pattern, title):
            attrs['color'] = color_name
            break

    # 2. RAM + Storage combo: 8GB/256GB, 16/512 ГБ
    m_combo = re.search(r'(?i)\b(\d+)\s*(?:gb|гб)?\s*[/,\+]\s*(\d+)\s*(?:gb|гб|tb|тб)\b', title)
    if m_combo:
        r_val = int(m_combo.group(1))
        if r_val in (4, 6, 8, 12, 16, 24, 32, 64, 128):
            attrs['ram'] = f"{r_val} ГБ"
        norm_s = normalize_attribute_value('storage', m_combo.group(2))
        if norm_s:
            attrs['storage'] = norm_s

    # 3. Storage (standalone: 128GB, 256 ГБ, 512GB, 1TB)
    if 'storage' not in attrs:
        m_s = re.search(r'(?i)\b(64|128|256|512)\s*(?:gb|гб)\b|\b(1|2)\s*(?:tb|тб)\b', title)
        if m_s:
            raw_s = m_s.group(0)
            norm_s = normalize_attribute_value('storage', raw_s)
            if norm_s:
                attrs['storage'] = norm_s

    # 4. RAM (standalone: 8GB RAM, 16 ГБ ОЗУ)
    if 'ram' not in attrs:
        m_r = re.search(r'(?i)\b(4|6|8|12|16|24|32|64)\s*(?:gb|гб)\s*(?:ram|озу|памяти)\b', title)
        if m_r:
            attrs['ram'] = f"{m_r.group(1)} ГБ"

    # 5. Screen size (15.6", 27", 24", 6.1", 6.7")
    m_scr = re.search(r'\b(5\.[4-9]|6\.[1-9]|7\.[0-9]|1[1-7]\.[0-9]|2[4-9]|3[2-4])\s*(?:"|\'\'|дюйм)', title)
    if m_scr:
        attrs['screen_size'] = f'{m_scr.group(1)}"'

    # 6. Подгузники и детская гигиена
    is_diaper = bool(re.search(
        r'\b(подгузник\w*|трусик\w*|памперс\w*|пеленк\w*|joonies|yokosun|huggies|merries|sachiko|emiko|manu|lovular|senso\s+baby|мама\s+знает)\b',
        title,
        re.I
    )) and not bool(re.search(r'\b(менструальн|прокладк|набор\s+(?:лиф|майк)|трусы\s+детск)\b', title, re.I))

    if is_diaper:
        # Тип
        if re.search(r'(?i)\b(ночные\s+трусики|magic\s+night)\b', title):
            attrs['diaper_type'] = 'Ночные трусики'
        elif re.search(r'(?i)\b(для\s+плавания|swimming|aqua)\b', title):
            attrs['diaper_type'] = 'Трусики для плавания'
        elif re.search(r'(?i)\b(подгузники-трусики|трусики-подгузники|трусик\w*)\b', title):
            attrs['diaper_type'] = 'Подгузники-трусики'
        elif re.search(r'(?i)\b(пеленк\w*)\b', title):
            attrs['diaper_type'] = 'Одноразовые пеленки'
        elif re.search(r'(?i)\b(подгузник\w*|памперс\w*)\b', title):
            attrs['diaper_type'] = 'Подгузники'

        # Вес ребенка
        m_w = re.search(r'(\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?\s*кг|до\s*\d+(?:\.\d+)?\s*кг|\d+(?:\.\d+)?\s*кг\+|\d+(?:\.\d+)?\s*\+\s*кг)', title, re.I)
        if m_w:
            norm_w = normalize_attribute_value('weight_range', m_w.group(1))
            if norm_w:
                attrs['weight_range'] = norm_w

        # Размер
        if re.search(r'(?i)\b(?:0\+|nb|newborn|для\s+новорожденных)\b', title):
            attrs['diaper_size'] = '0 (NB)'
        elif re.search(r'(?i)\b(?:xxxl|3xl)\b', title):
            attrs['diaper_size'] = '7 (XXXL)'
        elif re.search(r'(?i)\b(?:xxl|2xl)\b', title):
            attrs['diaper_size'] = '6 (XXL)'
        elif re.search(r'(?i)\b(?:xl)\b', title):
            attrs['diaper_size'] = '5 (XL)'
        elif re.search(r'(?i)\b(?:l)\b', title):
            attrs['diaper_size'] = '4 (L)'
        elif re.search(r'(?i)\b(?:m)\b', title):
            attrs['diaper_size'] = '3 (M)'
        elif re.search(r'(?i)\b(?:s)\b', title):
            attrs['diaper_size'] = '2 (S)'
        else:
            m_num = re.search(r'(?i)(?:размер\s*|трусики\s+|подгузники\s+(?:elite\s+soft\s+|ultra\s+comfort\s+)?|\b)([0-7])\b(?:\s*мал\.|\s*дев\.|\s*\([^\)]*кг|\s*,|\s+\d+[-–]\d+\s*кг|\s*\d+\s*кг\+)', title)
            if not m_num:
                m_num = re.search(r'(?i)\b(?:размер\s*|размера\s*)([0-7])\b', title)
            if not m_num:
                m_num = re.search(r'(?i)\b([bde])([2-4])\b', title)
                if m_num:
                    norm_sz = normalize_attribute_value('diaper_size', m_num.group(2))
                    if norm_sz:
                        attrs['diaper_size'] = norm_sz
            if m_num and 'diaper_size' not in attrs:
                norm_sz = normalize_attribute_value('diaper_size', m_num.group(1))
                if norm_sz:
                    attrs['diaper_size'] = norm_sz

        # Количество штук в упаковке
        m_pcs = re.search(r'(?i)(?:№\s*(\d+)\s*шт|\b(\d+)\s*(?:шт|штук)\b|\b(\d+)\s*шт\*\d+)', title)
        if m_pcs:
            val_pc = m_pcs.group(1) or m_pcs.group(2) or m_pcs.group(3)
            if val_pc:
                attrs['pieces_count'] = f"{val_pc} шт."

    # 7. Бытовая техника: холодильники, стиральные машины, пылесосы
    title_lower = title.casefold()

    # Холодильники
    if 'холодильник' in title_lower or 'морозильник' in title_lower:
        if 'no frost' in title_lower or 'total no frost' in title_lower or 'full no frost' in title_lower:
            attrs['refrigeration_defrost'] = 'No Frost'
        elif 'капельн' in title_lower:
            attrs['refrigeration_defrost'] = 'Капельная'

    # Стиральные машины
    if 'стиральн' in title_lower:
        m_load = re.search(r'\b(4|5|5\.5|6|6\.5|7|7\.5|8|8\.5|9|10|10\.5|12)\s*кг\b', title_lower)
        if m_load:
            attrs['load_capacity'] = f"{m_load.group(1)} кг"
        m_spin = re.search(r'\b(800|1000|1200|1400|1600)\s*(?:об/?мин|об)?\b', title_lower)
        if m_spin:
            attrs['spin_speed'] = f"{m_spin.group(1)} об/мин"
        if 'встраиваемая' in title_lower:
            attrs['installation_type'] = 'Встраиваемая'
        elif 'отдельностоящая' in title_lower:
            attrs['installation_type'] = 'Отдельностоящая'

    # Пылесосы
    if 'пылесос' in title_lower:
        if 'робот' in title_lower:
            if 'влажн' in title_lower or 'моющий' in title_lower:
                attrs['cleaning_type'] = 'Сухая и влажная'
            else:
                attrs['cleaning_type'] = 'Сухая'
            attrs['type'] = 'Робот-пылесос'
        elif 'вертикальн' in title_lower or 'беспроводн' in title_lower:
            attrs['type'] = 'Вертикальный'
        elif 'моющий' in title_lower:
            attrs['cleaning_type'] = 'Влажная'

    # 8. Винил и музыкальные носители
    if re.search(r'\b(винил(?:овая\s+пластинка|\s+lp)?|виниловые\s+пластинки|грампластинк\w*|\b\d?lp\b)\b', title, re.I):
        if re.search(r'\b2lp\b', title, re.I):
            attrs['format'] = '2LP'
        elif re.search(r'\blp\b|винил', title, re.I):
            attrs['format'] = 'LP'

    return attrs


def index_product_facts(conn, product_id: str, facts: List[Dict[str, Any]], product_title: Optional[str] = None) -> int:
    """Индексирует подтверждённые факты товара в таблицу product_search_attributes."""
    if not product_id:
        return 0

    if not product_title:
        row = conn.execute('SELECT title FROM canonical_products WHERE id=?', (product_id,)).fetchone()
        if not row or not row[0]:
            return 0
        product_title = row[0]

    now_iso = datetime.now(timezone.utc).isoformat()

    merged: Dict[str, Tuple[str, str]] = {}

    # 1. Из названия товара
    title_attrs = extract_title_attributes(product_title)
    for k, v in title_attrs.items():
        merged[k] = (v, 'Название товара')

    # 2. Факты из источников имеют высший приоритет
    if facts:
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
            merged[key] = (norm_val, f.get('name', key)[:160])

    if not merged:
        return 0

    indexed_count = 0
    for k, (v, evidence) in merged.items():
        conn.execute('''
            INSERT INTO product_search_attributes (product_id, key, value, evidence, source_title, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, key) DO UPDATE SET
                value=excluded.value,
                evidence=excluded.evidence,
                source_title=excluded.source_title,
                updated_at=excluded.updated_at
        ''', (product_id, k, v, evidence, product_title, now_iso))
        indexed_count += 1

    return indexed_count


def sync_all_source_attributes(conn, limit: int = 50000) -> int:
    """Индексирует характеристики товаров из product_source_evidence и названий canonical_products."""
    total_indexed = 0

    # 1. Из evidence
    try:
        cursor = conn.execute('''
            SELECT e.product_id, p.title, e.facts_json
            FROM product_source_evidence e
            JOIN canonical_products p ON p.id = e.product_id
            WHERE e.status = 'ready' AND e.facts_json != '[]'
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()

        for pid, title, facts_json in rows:
            try:
                facts = json.loads(facts_json)
                if isinstance(facts, list) and facts:
                    total_indexed += index_product_facts(conn, pid, facts, title)
            except Exception:
                continue
    except Exception:
        pass

    # 2. Для товаров, у которых еще нет атрибутов, индексируем из названия
    try:
        cursor = conn.execute('''
            SELECT p.id, p.title
            FROM canonical_products p
            LEFT JOIN product_search_attributes pa ON pa.product_id = p.id
            WHERE pa.product_id IS NULL AND p.title IS NOT NULL AND p.title != ''
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        for pid, title in rows:
            total_indexed += index_product_facts(conn, pid, [], title)
    except Exception:
        pass

    # 3. Для детских товаров и бытовой техники обновляем атрибуты из названия
    try:
        cursor = conn.execute('''
            SELECT p.id, p.title
            FROM canonical_products p
            WHERE p.title IS NOT NULL AND p.title != ''
              AND (
                p.category IN ('Подгузники и гигиена', 'diapers_hygiene', 'Бытовая техника', 'appliances', 'refrigerators', 'washing_machines', 'vacuums')
                OR p.title LIKE '%подгуз%' OR p.title LIKE '%трусик%' OR p.title LIKE '%памперс%'
                OR p.title LIKE '%холодильн%' OR p.title LIKE '%стиральн%' OR p.title LIKE '%пылесос%'
              )
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        for pid, title in rows:
            total_indexed += index_product_facts(conn, pid, [], title)
    except Exception:
        pass

    if total_indexed > 0 and hasattr(conn, 'commit'):
        conn.commit()

    return total_indexed
