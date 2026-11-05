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
    'certificate': 'Сертификат 80 PLUS',
    'panel_type': 'Тип матрицы',
    'curved': 'Изогнутый экран',
    'interface': 'Интерфейс',
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
    if val.casefold() in ('да', 'true', 'есть'):
        return 'Да' if key in ('5g', 'nfc', 'studs', 'installment_available', 'credit_available', 'pickup_available', 'is_active', 'has_gift') else None
    if val.casefold() in ('нет', 'false'):
        return 'Нет' if key in ('5g', 'nfc', 'studs', 'installment_available', 'credit_available', 'pickup_available', 'is_active', 'has_gift') else None
    if val in ('1', '0') and key in ('5g', 'nfc', 'studs', 'installment_available', 'credit_available', 'pickup_available', 'is_active', 'has_gift'):
        return 'Да' if val == '1' else 'Нет'

    # Память (ГБ, ТБ): storage, ram, vram
    if key in ('storage', 'ram', 'vram'):
        # Исключаем ложные булевы и текстовые значения
        if val.casefold() in ('да', 'нет', 'true', 'false', 'unknown', 'none', 'null', 'ddr3', 'ddr4', 'ddr5'):
            return None
        # Чистые числа
        if val.isdigit():
            n = int(val)
            if key == 'storage':
                if n in (1, 2, 4, 8):
                    return f"{n} ТБ"
                if n in (1000, 1024):
                    return "1 ТБ"
                if n in (2000, 2048):
                    return "2 ТБ"
                if n in (4000, 4096):
                    return "4 ТБ"
                if n in (120, 128, 240, 250, 256, 480, 500, 512, 960):
                    return f"{n} ГБ"
            if n in (1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96, 128, 256, 512, 1024):
                return f"{n} ГБ" if n < 1024 else "1 ТБ"
            if n in (1000, 1024):
                return "1 ТБ"
            if n in (2000, 2048):
                return "2 ТБ"

        # 1. ТБ
        m_tb = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(?:tb|тб|тбайт)\b', val, re.I)
        if m_tb:
            tb_num = int(float(m_tb.group(1).replace(',', '.')))
            return f"{tb_num} ТБ"

        # 2. 1000/1024/2000/2048 ГБ -> ТБ
        m_big_gb = re.search(r'\b(1000|1024|2000|2048)\s*(?:gb|гб|гбайт|g|gd)\b', val, re.I)
        if m_big_gb:
            n_big = int(m_big_gb.group(1))
            return "1 ТБ" if n_big <= 1024 else "2 ТБ"

        # 3. ГБ (16 Гб, 16 GB, 16G, 16GD, 16Gb, 16GB, 8 ГВ, 1Г6)
        m_gb = re.search(r'\b(\d+)\s*(?:gb|гб|гбайт|гв|g|gd)\b|\b(\d+)(?:gb|гб|g)\b', val, re.I)
        if m_gb:
            gb_num = int(m_gb.group(1) or m_gb.group(2))
            if gb_num in (1, 2) and key == 'storage' and 'гб' not in val.lower() and 'gb' not in val.lower():
                return f"{gb_num} ТБ"
            return f"{gb_num} ГБ"

        # 4. МБ
        m_mb = re.search(r'\b(\d+)\s*(?:mb|мб)\b', val, re.I)
        if m_mb:
            mb_num = int(m_mb.group(1))
            if mb_num >= 1024 and mb_num % 1024 == 0:
                return f"{mb_num // 1024} ГБ"
            return f"{mb_num} МБ"

        return None

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

    # Чипсет: нормализация графических чипов и чипсетов материнских плат
    if key == 'chipset':
        c_low = val.lower().strip()
        # Исключаем голые бренды без модели чипсета
        if c_low in ('nvidia', 'amd', 'intel', 'geforce', 'radeon'):
            return None
        # Исключаем процессоры (CPU), случайно попавшие в поле чипсета
        if re.search(r'\b(?:intel|amd\s+ryzen|core\s+ultra|core\s+i[3579]|ryzen|\d{4,5}[hux]\b|celeron|pentium|xeon)\b', c_low):
            return None
        # NVIDIA GeForce RTX / GTX
        m_rtx = re.search(r'\b(?:geforce\s+)?rtx\s*(\d{3,4}(?:\s*ti|\s*super)?)\b', val, re.I)
        if m_rtx:
            suffix = m_rtx.group(1).upper()
            return f"GeForce RTX {suffix}"
        m_gtx = re.search(r'\b(?:geforce\s+)?gtx\s*(\d{3,4}(?:\s*ti)?)\b', val, re.I)
        if m_gtx:
            suffix = m_gtx.group(1).upper()
            return f"GeForce GTX {suffix}"
        # AMD Radeon RX
        m_rx = re.search(r'\b(?:radeon\s+)?rx\s*(\d{4}(?:\s*xt|\s*xtx|\s*gre)?)\b', val, re.I)
        if m_rx:
            suffix = m_rx.group(1).upper()
            return f"Radeon RX {suffix}"
        # Intel Arc
        m_arc = re.search(r'\b(?:intel\s+)?arc\s*([ab]\d{3})\b', val, re.I)
        if m_arc:
            return f"Intel Arc {m_arc.group(1).upper()}"
        # Материнские платы (чипсеты B650, B760, Z790, X670, H610, H61, B450, B550 и т.д.)
        m_mb = re.search(r'\b(z[678]\d0|b[4567]\d0|x[56]70|a[356]20|h[34567]\d0|h\d{2}|c6\d{2})\b', val, re.I)
        if m_mb:
            return m_mb.group(1).upper()
        return None

    # Частота GPU / процессора
    if key == 'clock_speed':
        m_ghz = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(?:ghz|ггц)\b', val, re.I)
        if m_ghz:
            return f"{m_ghz.group(1).replace(',', '.')} ГГц"
        m_mhz = re.search(r'\b(\d+)\s*(?:mhz|мгц)\b', val, re.I)
        if m_mhz:
            return f"{m_mhz.group(1)} МГц"
        return None

    # Интерфейс
    if key == 'interface':
        i_low = val.lower().strip()
        if 'pci' in i_low or 'pcie' in i_low:
            return 'PCIe'
        if 'hdmi' in i_low and ('dp' in i_low or 'displayport' in i_low):
            return 'HDMI / DisplayPort'
        if 'hdmi' in i_low:
            return 'HDMI'
        if 'displayport' in i_low or 'dp' in i_low:
            return 'DisplayPort'
        if 'usb' in i_low or 'type-c' in i_low:
            return 'USB'
        return None

    # Сокет
    if key == 'socket':
        return ' '.join(val.split()).upper().replace('LGA', 'LGA ')

    # Тип памяти (RAM / VRAM)
    if key == 'memory_type':
        m_mt = re.search(r'\b(gddr[567]x?|ddr[345]|lpddr[45]x?)\b', val, re.I)
        if m_mt:
            return m_mt.group(1).upper()
        return val.strip().upper()

    # Тип матрицы (мониторы)
    if key == 'panel_type':
        p_low = val.lower().strip()
        if 'fast ips' in p_low:
            return 'Fast IPS'
        if 'qd-oled' in p_low:
            return 'QD-OLED'
        if 'mini-led' in p_low or 'miniled' in p_low:
            return 'Mini-LED'
        if 'oled' in p_low:
            return 'OLED'
        if 'ips' in p_low:
            return 'IPS'
        if 'va' in p_low:
            return 'VA'
        if 'tn' in p_low:
            return 'TN'
        return val.strip()

    # Разрешение экрана
    if key == 'resolution':
        r_low = val.lower().strip()
        if '3840' in r_low or '4k' in r_low or 'uhd' in r_low:
            return '3840x2160 (4K UHD)'
        if '2560' in r_low or '2k' in r_low or 'qhd' in r_low or 'wqhd' in r_low:
            return '2560x1440 (2K QHD)'
        if '3440' in r_low or 'uwqhd' in r_low:
            return '3440x1440 (UWQHD)'
        if '1920' in r_low or 'fhd' in r_low or 'full hd' in r_low:
            return '1920x1080 (Full HD)'
        m_res = re.search(r'\b(\d{3,4}\s*[xх]\s*\d{3,4})\b', val, re.I)
        if m_res:
            return m_res.group(1).replace('х', 'x').replace(' ', '')
        return val.strip()

    # Сертификат 80 PLUS (блоки питания)
    if key == 'certificate':
        c_low = val.lower().strip()
        if 'titanium' in c_low or 'титан' in c_low:
            return '80 PLUS Titanium'
        if 'platinum' in c_low or 'платин' in c_low:
            return '80 PLUS Platinum'
        if 'gold' in c_low or 'золот' in c_low:
            return '80 PLUS Gold'
        if 'silver' in c_low or 'серебр' in c_low:
            return '80 PLUS Silver'
        if 'bronze' in c_low or 'бронз' in c_low:
            return '80 PLUS Bronze'
        if 'standard' in c_low or 'white' in c_low or '80 plus' in c_low:
            return '80 PLUS Standard'
        return val.strip()

    # Изогнутый экран
    if key == 'curved':
        c_low = val.lower().strip()
        if any(w in c_low for w in ('да', 'yes', 'true', '1', 'изогнут', 'curved')):
            return 'Да'
        if any(w in c_low for w in ('нет', 'no', 'false', '0', 'плоск')):
            return 'Нет'
        return 'Да'

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

    if key in ('total_volume', 'capacity'):
        m_vol = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:л|l|литр\w*)?', val, re.IGNORECASE)
        if m_vol:
            num = m_vol.group(1).replace(',', '.')
            return f"{num} л"

    if key == 'format':
        if re.search(r'\b2lp\b', val, re.IGNORECASE):
            return '2LP'
        if re.search(r'\blp\b|винил', val, re.IGNORECASE):
            return 'LP'
        if re.search(r'\bcd\b', val, re.IGNORECASE):
            return 'CD'

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

    # 8. Мониторы
    is_monitor = bool(re.search(r'\b(монитор\w*|monitor\w*)\b', title, re.I))
    if is_monitor:
        # Диагональ экрана (23.8", 24", 24.5", 27", 31.5", 32", 34", 49")
        m_msz = re.search(r'\b(1[5-9]\.[0-9]|2[1-9]\.[0-9]|2[1-9]|3[1-4]\.[0-9]|3[1-4]|4[3-9])\s*(?:"|\'\'|дюйм|\binch\b|\b(?=\s*(?:fhd|qhd|uhd|2k|4k|ips|va|oled|gaming|игров|144|165|240|hz|гц)))', title, re.I)
        if m_msz:
            attrs['screen_size'] = f'{m_msz.group(1)}"'
        # Частота обновления (60 Гц, 75 Гц, 100 Гц, 144 Гц, 165 Гц, 180 Гц, 240 Гц, 360 Гц)
        m_mhz = re.search(r'\b(60|75|100|120|144|165|170|180|200|240|280|360|540)\s*(?:hz|гц)\b', title, re.I)
        if m_mhz:
            attrs['refresh_rate'] = f"{m_mhz.group(1)} Гц"
        # Тип матрицы (IPS, Fast IPS, VA, OLED, Mini-LED, TN)
        m_mpt = re.search(r'\b(fast\s+ips|qd-oled|mini-led|oled|ips|va|tn)\b', title, re.I)
        if m_mpt:
            attrs['panel_type'] = normalize_attribute_value('panel_type', m_mpt.group(1))
        # Разрешение экрана
        if re.search(r'\b(3840\s*[xх]\s*2160|4k|uhd)\b', title, re.I):
            attrs['resolution'] = '3840x2160 (4K UHD)'
        elif re.search(r'\b(2560\s*[xх]\s*1440|2k|qhd|wqhd)\b', title, re.I):
            attrs['resolution'] = '2560x1440 (2K QHD)'
        elif re.search(r'\b(3440\s*[xх]\s*1440|uwqhd)\b', title, re.I):
            attrs['resolution'] = '3440x1440 (UWQHD)'
        elif re.search(r'\b(1920\s*[xх]\s*1080|fhd|full\s*hd)\b', title, re.I):
            attrs['resolution'] = '1920x1080 (Full HD)'
        # Изогнутый экран
        if re.search(r'\b(изогнут\w*|curved|1000r|1500r|1800r)\b', title, re.I):
            attrs['curved'] = 'Да'

    # 9. Комплектующие для ПК (Видеокарты, БП, матплаты, ОЗУ, SSD, корпуса, охлаждение)
    is_pc_part = any(k in title_lower for k in [
        'кулер', 'охлаждени', 'cooler', 'вентилятор', 'блок питания', 'бп ', 'корпус',
        'видеокарт', 'geforce', 'rtx', 'radeon', 'материнск', 'процессор', 'socket',
        'lga', 'am4', 'am5', 'ddr3', 'ddr4', 'ddr5', 'b650', 'b760', 'z790', 'ssd',
        'накопитель', 'оперативн', 'dimm'
    ])

    if is_pc_part:
        # Видеокарты (GPU): VRAM и чипсет — только у самой видеокарты (A06, аудит 05.11.2026).
        # У ноутбука, моноблока или системного блока «RTX 4060» — компонент, а первое «16GB» в
        # названии — оперативная память: ни чипсет, ни видеопамять устройству не приписываем.
        is_whole_device = bool(re.search(
            r'(ноутбук\w*|laptop|notebook|macbook|ультрабук\w*|моноблок\w*|системн\w*\s+блок\w*|'
            r'компьютер\w*|игров\w*\s+пк|\bпк\b|неттоп\w*|mini\s*pc|планшет\w*|консол\w*)', title, re.I))
        is_gpu = not is_whole_device and bool(re.search(
            r'\b(видеокарт\w*|geforce|radeon\s+rx|rtx\s*\d{3,4}|gtx\s*\d{3,4}|arc\s*[ab]\d{3})\b', title, re.I))
        if is_gpu:
            # Объём, привязанный к памяти карты (8GB GDDR6, GDDR6X 12GB, 16 ГБ видеопамяти) — надёжнее
            # первого объёма в названии; иначе первый объём, если за ним не идёт DDR/SSD (это не видеопамять).
            m_vram = (re.search(r'\b(\d{1,2})\s*(?:gb|гб|g)\s*(?:gddr\d\w*|hbm\w*|видеопам\w*|vram)', title, re.I)
                      or re.search(r'\b(?:gddr\d\w*|hbm\w*)\s*(\d{1,2})\s*(?:gb|гб|g)\b', title, re.I)
                      or re.search(r'\b(\d{1,2})\s*(?:gb|гб|g)\b(?!\s*(?:ddr|lpddr|ssd|hdd|emmc|ram|озу))', title, re.I))
            m_vram_mb = re.search(r'\b(2048|4096|6144|8192)\s*(?:mb|мб)\b', title, re.I)
            if m_vram:
                v_num = int(m_vram.group(1))
                if v_num in (1, 2, 3, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 48):
                    attrs['vram'] = f"{v_num} ГБ"
            elif m_vram_mb:
                attrs['vram'] = f"{int(m_vram_mb.group(1)) // 1024} ГБ"
            norm_chip = normalize_attribute_value('chipset', title)
            if norm_chip:
                attrs['chipset'] = norm_chip
            if re.search(r'\b(?:lp|low\s+profile)\b', title, re.I):
                attrs['form_factor'] = 'Low Profile'

        # Оперативная память (RAM)
        is_ram = bool(re.search(r'\b(оперативн\w*\s+памят\w*|модул\w*\s+памят\w*|so-dimm|dimm|\bddr[345]\b)\b', title, re.I)) and not is_gpu
        if is_ram:
            m_ram_sz = re.search(r'\b(4|8|16|24|32|48|64|96|128)\s*(?:gb|гб)\b', title, re.I)
            if m_ram_sz:
                attrs['ram'] = f"{m_ram_sz.group(1)} ГБ"
            m_mem_t = re.search(r'\b(ddr[345])\b', title, re.I)
            if m_mem_t:
                attrs['memory_type'] = m_mem_t.group(1).upper()
            m_ram_sp = re.search(r'\b(2133|2400|2666|3000|3200|3600|4800|5200|5600|6000|6400|7200)\s*(?:mhz|мгц)?\b', title, re.I)
            if m_ram_sp:
                attrs['clock_speed'] = f"{m_ram_sp.group(1)} МГц"
            if re.search(r'\bso-dimm\b', title, re.I):
                attrs['form_factor'] = 'SO-DIMM'

        # Материнские платы (Motherboards)
        is_mb = bool(re.search(r'\b(материнск\w*\s+плат\w*|матплат\w*|mainboard|motherboard)\b', title, re.I))
        if is_mb:
            m_mb_ch = re.search(r'\b(z[678]\d0|b[45678]\d0[e]?|x[568]70[e]?|a[356]20|h[34567]\d0|h\d{2})\b', title, re.I)
            if m_mb_ch:
                attrs['chipset'] = m_mb_ch.group(1).upper()
            m_mb_sk = re.search(r'\b(am[45]|lga\s*\d{4}|str5|tr4)\b', title, re.I)
            if m_mb_sk:
                attrs['socket'] = m_mb_sk.group(1).upper().replace('LGA', 'LGA ')
            if re.search(r'\b(?:ddr5|d5)\b', title, re.I):
                attrs['memory_type'] = 'DDR5'
            elif re.search(r'\b(?:ddr4|d4)\b', title, re.I):
                attrs['memory_type'] = 'DDR4'

        # Блоки питания (PSU)
        is_psu = bool(re.search(r'\b(блок\s+питания|бп\s+|power\s+supply)\b', title, re.I))
        if is_psu:
            m_power = re.search(r'\b(\d{3,4})\s*(?:w|вт|watt)\b', title, re.I)
            if m_power:
                w_val = int(m_power.group(1))
                if 150 <= w_val <= 3000:
                    attrs['power'] = f"{w_val} Вт"
            # Сертификат 80 PLUS
            if re.search(r'\b(titanium|титан)\b', title, re.I):
                attrs['certificate'] = '80 PLUS Titanium'
            elif re.search(r'\b(platinum|платин)\b', title, re.I):
                attrs['certificate'] = '80 PLUS Platinum'
            elif re.search(r'\b(gold|золот)\b', title, re.I):
                attrs['certificate'] = '80 PLUS Gold'
            elif re.search(r'\b(silver|серебр)\b', title, re.I):
                attrs['certificate'] = '80 PLUS Silver'
            elif re.search(r'\b(bronze|бронз)\b', title, re.I):
                attrs['certificate'] = '80 PLUS Bronze'
            elif re.search(r'\b(80\s*plus|80\+)\b', title, re.I):
                attrs['certificate'] = '80 PLUS Standard'
            # «Gold/Silver/Titanium» у блока питания — уровень сертификата, а не цвет корпуса.
            # Цвет оставляем, только если металл назван ещё раз вне сертификата.
            if attrs.get('certificate') and attrs.get('color') in ('Золотистый', 'Серебристый', 'Титановый'):
                rest = re.sub(r'(?i)\b(?:80\s*(?:plus|\+)\s*)?(?:titanium|platinum|gold|silver|bronze|'
                              r'титан\w*|платин\w*|золот\w*|серебр\w*|бронз\w*)\b', ' ', title, count=1)
                if not any(re.search(p, rest) for p, c in COLOR_PATTERNS if c == attrs['color']):
                    attrs.pop('color')

        # SSD и накопители
        is_ssd = bool(re.search(r'\b(ssd|накопитель|nvme)\b', title, re.I))
        if is_ssd:
            # Типоразмер M.2 — только указанный в названии; «M.2» без размера не значит 2280 (A06).
            m_m2 = re.search(r'\bm\.?2\b(?:\s*[-/(]?\s*(2230|2242|2260|2280|22110)\b)?', title, re.I)
            m_m2_size = re.search(r'\b(2230|2242|2260|2280|22110)\b', title) if m_m2 else None
            if m_m2:
                size = m_m2.group(1) or (m_m2_size.group(1) if m_m2_size else None)
                attrs['form_factor'] = f'M.2 {size}' if size else 'M.2'
            elif re.search(r'\b2\.5(?:"|\'\'|дюйм)?\b', title, re.I):
                attrs['form_factor'] = '2.5"'
            if re.search(r'\b(pcie\s*5(?:\.0)?|pci-e\s*5|gen5)\b', title, re.I):
                attrs['interface'] = 'PCIe 5.0'
            elif re.search(r'\b(pcie\s*4(?:\.0)?|pci-e\s*4|gen4)\b', title, re.I):
                attrs['interface'] = 'PCIe 4.0'
            elif re.search(r'\b(pcie\s*3(?:\.0)?|pci-e\s*3|gen3)\b', title, re.I):
                attrs['interface'] = 'PCIe 3.0'
            elif re.search(r'\bsata\s*(?:3|iii)?\b', title, re.I):
                attrs['interface'] = 'SATA III'
            m_ssd_sz = re.search(r'\b(120|128|240|250|256|480|500|512|960|1000|1024|2000|2048|4000)\s*(?:gb|гб)\b|\b(1|2|4)\s*(?:tb|тб)\b', title, re.I)
            if m_ssd_sz:
                norm_ssd = normalize_attribute_value('storage', m_ssd_sz.group(0))
                if norm_ssd:
                    attrs['storage'] = norm_ssd

        # Форм-фактор корпуса / матплаты / БП (если еще не определен)
        if 'form_factor' not in attrs:
            m_ff = re.search(r'\b(mid-?tower|mini-?tower|full-?tower|micro-?atx|matx|mini-?itx|mitx|e-?atx|atx|sfx-l|sfx)\b', title, re.I)
            if m_ff:
                ff_raw = m_ff.group(1).lower().replace('-', '')
                ff_map = {
                    'midtower': 'Mid-Tower',
                    'minitower': 'Mini-Tower',
                    'fulltower': 'Full-Tower',
                    'microatx': 'Micro-ATX',
                    'matx': 'Micro-ATX',
                    'miniitx': 'Mini-ITX',
                    'mitx': 'Mini-ITX',
                    'eatx': 'E-ATX',
                    'atx': 'ATX',
                    'sfxl': 'SFX-L',
                    'sfx': 'SFX',
                }
                if ff_raw in ff_map:
                    attrs['form_factor'] = ff_map[ff_raw]

        # Тип охлаждения
        if any(k in title_lower for k in ['сжо', 'сво', 'водяного охлаждения', 'жидкостн']):
            attrs['type'] = 'СЖО (водяное)'
        elif 'кулер' in title_lower:
            attrs['type'] = 'Воздушный кулер'
        elif 'вентилятор для корпуса' in title_lower or 'корпусный вентилятор' in title_lower:
            attrs['type'] = 'Вентилятор для корпуса'

        # Сокет (если еще не определен)
        if 'socket' not in attrs:
            m_sock = re.search(r'\b(lga\s*\d{4}|am[45]|str5|tr4)\b', title, re.I)
            if m_sock:
                sock_val = m_sock.group(1).upper()
                attrs['socket'] = sock_val.replace('LGA', 'LGA ')

    # 9. Винил и музыкальные носители (только если не комплектующие ПК)
    if not is_pc_part and re.search(r'\b(?:винил(?:овая\s+пластинка|\s+lp)?|виниловые\s+пластинки|грампластинк\w*|\b2lp\b|\b\d?lp(?=\s*(?:диск|пластинк|альбом|repress|remaster|edition|soundtrack|vinyl|\(|$)))\b', title, re.I):
        if re.search(r'\b2lp\b', title, re.I):
            attrs['format'] = '2LP'
        elif re.search(r'\blp\b|винил', title, re.I):
            attrs['format'] = 'LP'

    return attrs


# Основание значения в product_search_attributes.evidence и его приоритет (A02, аудит 05.11.2026).
# Факт источника (название характеристики продавца/карточки) главнее эвристики по названию и правил,
# а те — главнее ответа модели. Значение с более низким приоритетом не заменяет уже записанное.
EVIDENCE_TITLE = 'Название товара'
EVIDENCE_RULES = 'Правила каталога (название/описание)'
EVIDENCE_AI = 'Локальный ИИ (значение есть в тексте товара)'
_DERIVED_EVIDENCE_RANK = {
    EVIDENCE_TITLE: 1,
    EVIDENCE_RULES: 1,
    EVIDENCE_AI: 0,
    'Локальный AI (qwen3-vl по описанию товара)': 0,   # записи v6.28.x до исправления
}
SOURCE_EVIDENCE_RANK = 2


def evidence_rank(evidence: Optional[str]) -> int:
    """Приоритет основания: 2 — факт источника, 1 — название/правила, 0 — локальный ИИ."""
    return _DERIVED_EVIDENCE_RANK.get(evidence or '', SOURCE_EVIDENCE_RANK)


def upsert_search_attributes(conn, product_id: str, items: Dict[str, Tuple[str, str]], source_title: str,
                             now_iso: str, prune_evidence: Optional[str] = None) -> Dict[str, str]:
    """Записывает значения с учётом приоритета основания; возвращает действующие значения ключей items.

    Значение не заменяет записанное с более высоким приоритетом (подтверждённый факт источника
    не перезаписывается эвристикой). prune_evidence удаляет строки с этим основанием, которых больше нет
    в items, — так повторная индексация названия убирает значения, которые исправленные правила
    больше не выводят (например, «видеопамять» ноутбука)."""
    existing = {r[0]: (r[1], r[2]) for r in conn.execute(
        'SELECT key, value, evidence FROM product_search_attributes WHERE product_id=?', (product_id,))}
    effective: Dict[str, str] = {}
    for k, (v, evidence) in items.items():
        old = existing.get(k)
        if old and evidence_rank(old[1]) > evidence_rank(evidence):
            effective[k] = old[0]
            continue
        effective[k] = v
        if old == (v, evidence):
            continue
        conn.execute('''
            INSERT INTO product_search_attributes (product_id, key, value, evidence, source_title, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, key) DO UPDATE SET
                value=excluded.value,
                evidence=excluded.evidence,
                source_title=excluded.source_title,
                updated_at=excluded.updated_at
        ''', (product_id, k, v, evidence, source_title, now_iso))
    if prune_evidence:
        stale = [k for k, (_, e) in existing.items() if e == prune_evidence and k not in items]
        for k in stale:
            conn.execute('DELETE FROM product_search_attributes WHERE product_id=? AND key=?', (product_id, k))
        if stale:
            try:
                conn.execute(
                    "UPDATE canonical_products SET attributes_json = json_remove(COALESCE(NULLIF(attributes_json, ''), '{}'), "
                    + ", ".join("?" for _ in stale) + ") WHERE id = ?",
                    [*(f'$."{k}"' for k in stale), product_id])
            except Exception:
                pass
    return effective


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
        merged[k] = (v, EVIDENCE_TITLE)

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
            evidence = str(f.get('name') or key)[:160]
            if evidence_rank(evidence) < SOURCE_EVIDENCE_RANK:
                evidence = f'Источник: {evidence}'[:160]
            merged[key] = (norm_val, evidence)

    effective = upsert_search_attributes(conn, product_id, merged, product_title, now_iso,
                                         prune_evidence=EVIDENCE_TITLE)
    if not merged:
        return 0

    # Также синхронизируем в canonical_products.attributes_json для отображения в карточке товара
    try:
        conn.execute(
            "UPDATE canonical_products SET attributes_json = json_patch(COALESCE(NULLIF(attributes_json, ''), '{}'), ?) WHERE id = ?",
            (json.dumps(effective), product_id)
        )
    except Exception:
        pass

    return len(merged)


_SYNC_TITLE_SCOPE = '''
    p.category IN ('Подгузники и гигиена', 'diapers_hygiene', 'Бытовая техника', 'appliances', 'refrigerators',
                   'washing_machines', 'vacuums', 'Мониторы', 'Комплектующие для ПК', 'Видеокарты', 'monitors',
                   'pc_parts', 'gpus')
    OR p.title LIKE '%подгуз%' OR p.title LIKE '%трусик%' OR p.title LIKE '%памперс%'
    OR p.title LIKE '%холодильн%' OR p.title LIKE '%стиральн%' OR p.title LIKE '%пылесос%'
    OR p.title LIKE '%монитор%' OR p.title LIKE '%видеокарт%' OR p.title LIKE '%geforce%'
    OR p.title LIKE '%radeon%' OR p.title LIKE '%материнск%' OR p.title LIKE '%блок питания%'
    OR p.title LIKE '%ssd%' OR p.title LIKE '%оперативн%памят%' OR p.title LIKE '%ddr5%'
    OR p.title LIKE '%ddr4%'
'''


def sync_all_source_attributes(conn, limit: int = 50000) -> int:
    """Индексирует характеристики товаров из product_source_evidence и названий canonical_products.

    Каждый товар индексируется один раз — сразу с фактами источника и названием, поэтому повторный
    проход «по названию» больше не затирает подтверждённое значение (A02). Повторный запуск
    даёт тот же результат."""
    facts_by_product: Dict[str, List[Dict[str, Any]]] = {}
    titles: Dict[str, str] = {}

    # 1. Товары с фактами источника
    try:
        rows = conn.execute('''
            SELECT e.product_id, p.title, e.facts_json
            FROM product_source_evidence e
            JOIN canonical_products p ON p.id = e.product_id
            WHERE e.status = 'ready' AND e.facts_json != '[]'
            LIMIT ?
        ''', (limit,)).fetchall()
    except Exception:
        rows = []
    for pid, title, facts_json in rows:
        try:
            facts = json.loads(facts_json)
        except Exception:
            continue
        if isinstance(facts, list) and facts:
            facts_by_product[pid] = facts
            titles[pid] = title

    # 2. Товары без атрибутов и 3. разделы, где атрибуты берутся из названия
    for sql in (
        '''SELECT p.id, p.title FROM canonical_products p
             LEFT JOIN product_search_attributes pa ON pa.product_id = p.id
             WHERE pa.product_id IS NULL AND p.title IS NOT NULL AND p.title != '' LIMIT ?''',
        f'''SELECT p.id, p.title FROM canonical_products p
             WHERE p.title IS NOT NULL AND p.title != '' AND ({_SYNC_TITLE_SCOPE}) LIMIT ?''',
    ):
        try:
            for pid, title in conn.execute(sql, (limit,)).fetchall():
                titles.setdefault(pid, title)
        except Exception:
            continue

    total_indexed = 0
    for pid, title in titles.items():
        try:
            total_indexed += index_product_facts(conn, pid, facts_by_product.get(pid, []), title)
        except Exception:
            continue

    if hasattr(conn, 'commit'):
        conn.commit()

    return total_indexed
