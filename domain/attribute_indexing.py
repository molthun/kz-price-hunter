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
    'doors_type': 'Тип конструкции',
    'height_cm': 'Высота',
    'compressor_type': 'Тип компрессора',
    'freezer_location': 'Расположение морозильника',
    'energy_class': 'Класс энергопотребления',
    'noise_level': 'Уровень шума',
    'load_capacity': 'Максимальная загрузка',
    'drying': 'Сушка',
    'spin_speed': 'Скорость отжима',
    'motor_type': 'Тип двигателя',
    'depth_type': 'Глубина корпуса',
    'program_count': 'Количество программ',
    'installation_type': 'Тип установки',
    'cleaning_type': 'Тип уборки',
    'dust_collector_type': 'Тип пылесборника',
    'suction_power': 'Мощность всасывания',
    'power': 'Мощность',
    'tire_size': 'Типоразмер',
    'tire_width': 'Ширина шины',
    'tire_profile': 'Профиль шины',
    'certificate': 'Сертификат 80 PLUS',
    'panel_type': 'Тип матрицы',
    'curved': 'Изогнутый экран',
    'aspect_ratio': 'Соотношение сторон',
    'interface': 'Интерфейс',
    'type': 'Тип устройства',
    'keyboard_type': 'Тип клавиатуры',
    'sensor_dpi': 'Разрешение сенсора',
    'wifi_standard': 'Стандарт Wi-Fi',
    'ports_count': 'Количество портов',
    'lan_speed': 'Скорость портов',
    'poe': 'Поддержка PoE',
    'print_tech': 'Технология печати',
    'print_color': 'Цветность печати',
    'paper_format': 'Формат бумаги',
    'connectivity': 'Интерфейсы',
    'power_btu': 'Мощность (BTU)',
    'area_sqm': 'Площадь помещения',
    'inverter': 'Инвертор',
    'screen_diagonal': 'Диагональ экрана',
    'screen_tech': 'Технология экрана',
    'smart_tv': 'Smart TV',
    'wireless': 'Беспроводные',
    'anc': 'Шумоподавление (ANC)',
    'case_size': 'Размер корпуса',
    'cellular': 'Связь (LTE / eSIM)',
    'width_cm': 'Ширина',
    'capacity_sets': 'Вместимость (комплектов)',
    # Приставки, ПК, кабели, ИБП, одежда, обувь, мебель, инструменты, парфюмерия, игрушки (Волна 3)
    'platform': 'Платформа',
    'disc_drive': 'Дисковод',
    'cpu_family': 'Серия процессора',
    'delivery_type': 'Комплектация',
    'cores': 'Количество ядер',
    'frequency': 'Тактовая частота',
    'side_window': 'Боковое окно',
    'backlight': 'Подсветка',
    'psu_included': 'Блок питания',
    'power_va': 'Полная мощность (ВА)',
    'connector': 'Разъем',
    'gender': 'Пол и возраст',
    'apparel_size': 'Размер',
    'bed_size': 'Размер (спальное место)',
    'power_supply': 'Тип питания',
    'voltage': 'Напряжение АКБ',
    'volume_ml': 'Объем',
    'lego_series': 'Серия конструктора',
    # Волна 4: Наушники, смарт-часы, кулеры, дом, посуда, зоотовары
    'microphone': 'Микрофон',
    'strap_material': 'Материал ремешка',
    'fan_size': 'Размер вентилятора',
    'lamp_base': 'Цоколь лампы',
    'color_temp': 'Цветовая температура',
    'diameter_cm': 'Диаметр',
    'pet_type': 'Вид питомца',
    'pet_age': 'Возраст и назначение',
    'pack_weight': 'Вес упаковки',
    # Волна 5: Продукты, аптека, косметика, детские товары, авто, картриджи, фото/видео
    'fat_content': 'Жирность',
    'dosage': 'Дозировка',
    'age_from': 'Возраст',
    'spf': 'Защита SPF',
    # Волна 6: Программы, телефония, велосипеды, самокаты и спорт
    'license_term': 'Срок лицензии',
    'devices_count': 'Количество устройств',
    'wheel_size': 'Диаметр колес',
    # Музыка, винил и книги
    'artist': 'Исполнитель',
    'format': 'Формат',
    'genre': 'Жанр',
    'release_year': 'Год издания',
    'author': 'Автор',
    'издательство': 'Издательство',
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
    'мощность всасывания': ('suction_power', 'Мощность всасывания'),
    'система разморозки': ('refrigeration_defrost', 'Разморозка'),
    'размораживание': ('refrigeration_defrost', 'Разморозка'),
    'тип размораживания': ('refrigeration_defrost', 'Разморозка'),
    'размораживание морозильной камеры': ('refrigeration_defrost', 'Разморозка'),
    'размораживание холодильной камеры': ('refrigeration_defrost', 'Разморозка'),
    'общий объем': ('capacity', 'Объем'),
    'полезный объем': ('capacity', 'Объем'),
    'объем': ('capacity', 'Объем'),
    'объём': ('capacity', 'Объем'),
    'площадь помещения': ('area_sqm', 'Площадь помещения'),
    'обслуживаемая площадь': ('area_sqm', 'Площадь помещения'),
    'инвертор': ('inverter', 'Инвертор'),
    'инверторный компрессор': ('inverter', 'Инвертор'),
    # Общий тип устройства
    'тип': ('type', 'Тип устройства'),
    'тип устройства': ('type', 'Тип устройства'),
    # Подгузники и гигиена для малышей
    'тип подгузников': ('diaper_type', 'Тип'),
    'вид подгузников': ('diaper_type', 'Тип'),
    'размер подгузников': ('diaper_size', 'Размер'),
    'размер подгузника': ('diaper_size', 'Размер'),
    'вес ребенка': ('weight_range', 'Вес ребенка'),
    'вес малыша': ('weight_range', 'Вес ребенка'),
    'минимальный вес ребенка': ('weight_range', 'Вес ребенка'),
    'максимальный вес ребенка': ('weight_range', 'Вес ребенка'),
    'количество в упаковке': ('pieces_count', 'Количество в упаковке'),
    'количество штук в упаковке': ('pieces_count', 'Количество в упаковке'),
    # Мониторы, периферия, сеть, принтеры
    'тип матрицы': ('panel_type', 'Тип матрицы'),
    'матрица': ('panel_type', 'Тип матрицы'),
    'изогнутый экран': ('curved', 'Изогнутый экран'),
    'соотношение сторон': ('aspect_ratio', 'Соотношение сторон'),
    'тип подключения': ('connection_type', 'Тип подключения'),
    'подключение': ('connection_type', 'Тип подключения'),
    'тип клавиатуры': ('keyboard_type', 'Тип клавиатуры'),
    'конструкция клавиатуры': ('keyboard_type', 'Тип клавиатуры'),
    'разрешение оптического сенсора': ('sensor_dpi', 'Разрешение сенсора'),
    'разрешение сенсора': ('sensor_dpi', 'Разрешение сенсора'),
    'стандарт wi-fi': ('wifi_standard', 'Стандарт Wi-Fi'),
    'стандарт беспроводной связи': ('wifi_standard', 'Стандарт Wi-Fi'),
    'количество портов lan': ('ports_count', 'Количество портов'),
    'количество портов': ('ports_count', 'Количество портов'),
    'скорость портов': ('lan_speed', 'Скорость портов'),
    'базовая скорость передачи данных': ('lan_speed', 'Скорость портов'),
    'поддержка poe': ('poe', 'Поддержка PoE'),
    'технология печати': ('print_tech', 'Технология печати'),
    'тип печати': ('print_tech', 'Технология печати'),
    'цветность печати': ('print_color', 'Цветность печати'),
    'максимальный формат': ('paper_format', 'Формат бумаги'),

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
    _BOOLEAN_KEYS = ('5g', 'nfc', 'studs', 'installment_available', 'credit_available', 'pickup_available', 'is_active', 'has_gift',
                     'smart_tv', 'inverter', 'wireless', 'anc', 'cellular')
    if val.casefold() in ('да', 'true', 'есть'):
        return 'Да' if key in _BOOLEAN_KEYS else None
    if val.casefold() in ('нет', 'false'):
        return 'Нет' if key in _BOOLEAN_KEYS else None
    if val in ('1', '0') and key in _BOOLEAN_KEYS:
        return 'Да' if val == '1' else 'Нет'

    # Диагональ экрана (screen_diagonal, screen_size)
    if key in ('screen_diagonal', 'screen_size'):
        m_diag = re.search(r'\b(\d+(?:\.\d+)?)\s*(?:"|\'\'|дюйм\w*)|\b(\d{2,3})\b', val, re.I)
        if m_diag:
            d_num = m_diag.group(1) or m_diag.group(2)
            return f'{d_num}"'

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
        # Один разбор модели видеокарты с фасетом/фильтром модели: catalog_classifier.canonical_gpu_model
        # (суффиксы Ti/Super/Ti Super/XT/XTX/GRE и регистр совпадают с фильтром). Ниже — запасной разбор.
        try:
            from catalog_classifier import canonical_gpu_model
            canon = canonical_gpu_model(val)
        except Exception:
            canon = None
        if canon:
            return canon
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
        return None

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
        return None

    if key == 'aspect_ratio':
        low = val.casefold()
        if '32:9' in low:
            return '32:9 (Super UltraWide)'
        if '21:9' in low:
            return '21:9 (UltraWide)'
        if '16:10' in low:
            return '16:10'
        if '16:9' in low:
            return '16:9'
        return val[:40]

    if key == 'keyboard_type':
        low = val.casefold()
        if 'механич' in low or 'mechanical' in low:
            return 'Механическая'
        if 'мембран' in low or 'membrane' in low:
            return 'Мембранная'
        if 'оптико' in low or 'оптическ' in low:
            return 'Оптико-механическая'
        if 'ножнич' in low:
            return 'Ножничная'
        return val[:40]

    if key == 'connection_type':
        low = val.casefold()
        if ('беспровод' in low or 'wireless' in low or 'bluetooth' in low or 'радио' in low) and ('провод' in low or 'usb' in low):
            return 'Комбинированное'
        if 'беспровод' in low or 'wireless' in low or 'bluetooth' in low or 'радиоканал' in low:
            return 'Беспроводное'
        if 'провод' in low or 'wired' in low:
            return 'Проводное'
        return val[:40]

    if key == 'sensor_dpi':
        m_dpi = re.search(r'\b(\d{3,5})\s*(?:dpi|cpi)?\b', val, re.I)
        if m_dpi:
            return f"{m_dpi.group(1)} DPI"
        return None

    if key == 'wifi_standard':
        low = val.casefold()
        if 'wi-fi 7' in low or 'wifi 7' in low or '802.11be' in low:
            return 'Wi-Fi 7 (802.11be)'
        if 'wi-fi 6e' in low or 'wifi 6e' in low:
            return 'Wi-Fi 6E'
        if 'wi-fi 6' in low or 'wifi 6' in low or '802.11ax' in low or '\bax' in low:
            return 'Wi-Fi 6 (802.11ax)'
        if 'wi-fi 5' in low or 'wifi 5' in low or '802.11ac' in low or '\bac' in low:
            return 'Wi-Fi 5 (802.11ac)'
        if 'wi-fi 4' in low or 'wifi 4' in low or '802.11n' in low:
            return 'Wi-Fi 4 (802.11n)'
        return val[:40]

    if key == 'ports_count':
        m_p = re.search(r'\b(\d{1,2})\b', val)
        if m_p:
            n_p = int(m_p.group(1))
            if n_p == 1:
                return '1 порт'
            if n_p in (2, 3, 4):
                return f'{n_p} порта'
            if 5 <= n_p <= 52:
                return f'{n_p} портов'
        return None

    if key == 'lan_speed':
        low = val.casefold()
        if '10 гбит' in low or '10g' in low or '10000' in low:
            return '10 Гбит/с'
        if '2.5 гбит' in low or '2.5g' in low or '2500' in low:
            return '2.5 Гбит/с'
        if '1 гбит' in low or '1000' in low or 'гигабит' in low or 'gigabit' in low or 'gbe' in low:
            return '1 Гбит/с'
        if '100 мбит' in low or '10/100' in low or 'fast ethernet' in low:
            return '100 Мбит/с'
        return val[:40]

    if key == 'poe':
        low = val.casefold()
        if any(w in low for w in ('да', 'есть', 'poe+', 'poe', 'true')):
            return 'С поддержкой PoE'
        if any(w in low for w in ('нет', 'false', 'без poe')):
            return 'Без PoE'
        return None

    if key == 'print_tech':
        low = val.casefold()
        if 'снпч' in low:
            return 'Струйная с СНПЧ'
        if 'лазерн' in low or 'светодиодн' in low or 'laser' in low:
            return 'Лазерная'
        if 'струйн' in low or 'inkjet' in low:
            return 'Струйная'
        if 'термо' in low or 'thermal' in low:
            return 'Термопечать'
        if 'матричн' in low:
            return 'Матричная'
        return val[:40]

    if key == 'print_color':
        low = val.casefold()
        if any(k in low for k in ('черный', 'black', 'чёрный', '(bk)')):
            return 'Черный (Black)'
        if any(k in low for k in ('голубой', 'cyan')):
            return 'Голубой (Cyan)'
        if any(k in low for k in ('пурпурный', 'magenta')):
            return 'Пурпурный (Magenta)'
        if any(k in low for k in ('желтый', 'yellow', 'жёлтый')):
            return 'Желтый (Yellow)'
        if 'цветн' in low or 'color' in low or 'комплект' in low:
            return 'Цветная'
        if 'черно-бел' in low or 'ч/б' in low or 'монохром' in low or 'mono' in low:
            return 'Черно-белая'
        return val[:40]

    if key == 'paper_format':
        if re.search(r'\ba3\+?\b|\bа3\+?\b', val, re.I):
            return 'A3'
        if re.search(r'\ba4\b|\bа4\b', val, re.I):
            return 'A4'
        if re.search(r'\ba1\b|\bа1\b|\ba0\b|\bа0\b', val, re.I):
            return 'Широкоформатный (A1/A0)'
        return val[:40]

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

    if key == 'height_cm':
        m_h = re.search(r'(\d{2,3})\s*(?:см|cm)?', val, re.IGNORECASE)
        if m_h:
            h_int = int(m_h.group(1))
            if 80 <= h_int <= 230:
                return f"{h_int} см"

    if key == 'suction_power':
        m_pa = re.search(r'(\d{4,5})\s*(?:па|pa)', val, re.IGNORECASE)
        if m_pa:
            return f"{m_pa.group(1)} Па"
        m_aw = re.search(r'(\d{2,3})\s*(?:авт|aw)', val, re.IGNORECASE)
        if m_aw:
            return f"{m_aw.group(1)} аВт"

    if key == 'diameter':
        m_d = re.search(r'(?i)\b(?:r|р)?(1[2-9]|2[0-4])\b', val)
        if m_d:
            return f"R{m_d.group(1)}"

    if key == 'tire_width':
        m_tw = re.search(r'\b(1[3-9]\d|2\d{2}|3[0-3]\d)\b', val)
        if m_tw:
            return f"{m_tw.group(1)} мм"

    if key == 'tire_profile':
        m_tp = re.search(r'\b(2[5-9]|[3-8]\d|90)\b', val)
        if m_tp:
            return f"{m_tp.group(1)}%"

    if key == 'tire_size':
        m_ts = re.search(r'(\d{3})[/](\d{2})\s*(?:r|р|zr)?\s*(\d{2})', val, re.IGNORECASE)
        if m_ts:
            return f"{m_ts.group(1)}/{m_ts.group(2)} R{m_ts.group(3)}"

    if key == 'season':
        low = val.casefold()
        if 'шип' in low or 'studded' in low:
            return 'Зимние шипованные'
        if 'липучк' in low or 'нешип' in low or 'фрикцион' in low or 'friction' in low:
            return 'Зимние нешипованные (липучка)'
        if 'демисезон' in low or 'осень' in low or 'весна' in low:
            return 'Демисезонные'
        if 'всесезон' in low or 'all season' in low or 'm+s' in low or 'круглогодич' in low:
            return 'Всесезонные'
        if 'летн' in low or 'лето' in low or 'summer' in low:
            return 'Летние'
        if 'зимн' in low or 'зима' in low or 'winter' in low:
            return 'Зимние'

    if key == 'gender':
        low = val.casefold().strip()
        if low in ('девочки', 'для девочек', 'девочка', 'для девочки'):
            return 'Для девочек'
        if low in ('мальчики', 'для мальчиков', 'мальчик', 'для мальчика'):
            return 'Для мальчиков'
        if low in ('женский', 'женские', 'женская', 'для женщин'):
            return 'Женские'
        if low in ('мужской', 'мужские', 'мужская', 'для мужчин'):
            return 'Мужские'
        if low in ('детский', 'детские', 'детская', 'для детей'):
            return 'Детские'
        if low in ('унисекс', 'unisex'):
            return 'Унисекс'
        return val.strip()[:60] if val.strip() else None

    if key == 'form_factor':
        low = val.casefold().strip()
        if low in ('midi-башня', 'midi-tower', 'mid-tower', 'mid tower', 'midi tower'):
            return 'Mid-Tower / ATX'
        if low in ('mini-башня', 'mini-tower', 'mini tower'):
            return 'Mini-Tower / Micro-ATX'
        if low in ('full-башня', 'full-tower', 'full tower'):
            return 'Full-Tower / E-ATX'

    if key == 'type':
        v_clean = val.strip()
        low = v_clean.casefold()
        if low == 'сплит-система':
            return 'Сплит-система'
        if low in ('мульти-сплит-система', 'мульти сплит система', 'мульти-сплит система'):
            return 'Мульти-сплит система'
        if v_clean and v_clean[0].islower():
            return v_clean[0].upper() + v_clean[1:80]

    if key in ('platform', 'disc_drive', 'cpu_family', 'delivery_type', 'side_window', 'backlight',
               'psu_included', 'power_va', 'connector', 'apparel_size', 'bed_size',
               'power_supply', 'voltage', 'volume_ml', 'lego_series', 'microphone', 'strap_material',
               'fan_size', 'lamp_base', 'color_temp', 'diameter_cm', 'pet_type', 'pet_age', 'pack_weight',
               'fat_content', 'dosage', 'age_from', 'spf', 'license_term', 'devices_count', 'wheel_size'):
        return val.strip()[:60] if val.strip() else None

    if key == 'drying':
        low = val.casefold()
        if any(w in low for w in ('да', 'true', 'с сушкой', 'сушильн', 'есть')):
            return 'С сушкой'
        if any(w in low for w in ('нет', 'false', 'без сушки')):
            return 'Без сушки'

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
    (r'(?i)\b(?:черн(?:ый|ая|ое|ые|ую|ом|ого)|black|midnight|темная\s+ночь)\b', 'Черный'),
    (r'(?i)\b(?:бел(?:ый|ая|ое|ые|ую|ом|ого)|white)\b', 'Белый'),
    (r'(?i)\b(?:син(?:ий|яя|ее|ие|юю|ем|его)|blue)\b', 'Синий'),
    (r'(?i)\b(?:сер(?:ый|ая|ое|ые|ую|ом|ого)|gray|grey|space\s+gray)\b', 'Серый'),
    (r'(?i)\b(?:серебр(?:истый|истая|истое|истые|истую|истом)|silver)\b', 'Серебристый'),
    (r'(?i)\b(?:золот(?:ой|ая|ое|ые|ую|ом|ого|истый|истая|истое)|gold)\b', 'Золотистый'),
    (r'(?i)\b(?:зелен(?:ый|ая|ое|ые|ую|ом|ого)|green)\b', 'Зеленый'),
    (r'(?i)\b(?:красн(?:ый|ая|ое|ые|ую|ом|ого)|red)\b', 'Красный'),
    (r'(?i)\b(?:фиолетов(?:ый|ая|ое|ые|ую|ом|ого)|purple)\b', 'Фиолетовый'),
    (r'(?i)\b(?:розов(?:ый|ая|ое|ые|ую|ом|ого)|pink)\b', 'Розовый'),
    (r'(?i)\b(?:желт(?:ый|ая|ое|ые|ую|ом|ого)|yellow)\b', 'Желтый'),
    (r'(?i)\b(?:оранжев(?:ый|ая|ое|ые|ую|ом|ого)|orange)\b', 'Оранжевый'),
    (r'(?i)\b(?:бежев(?:ый|ая|ое|ые|ую|ом|ого)|beige|starlight|сияющая\s+звезда)\b', 'Бежевый'),
    (r'(?i)\b(?:титан(?:овый|овая|овое|овые|овую|овом)|titanium)\b', 'Титановый'),
]


# Целое устройство, в котором видеокарта — лишь компонент (A06/R04, аудиты 05.11.2026)
_WHOLE_DEVICE_WORDS = re.compile(
    r'(ноутбук\w*|laptop|notebook|macbook|ультрабук\w*|моноблок\w*|системн\w*\s+блок\w*|'
    r'компьютер\w*|игров\w*\s+пк|\bпк\b|неттоп\w*|mini\s*pc|планшет\w*|консол\w*)', re.I)
# Признаки целого устройства в названии без слова типа: диагональ экрана, накопитель,
# системная память DDR (не GDDR видеокарты), запись «16/1TB» (ОЗУ/накопитель)
_WHOLE_DEVICE_SIGNS = re.compile(
    r'\b1[0-8](?:[.,]\d)?\s*(?:"|\'\'|”|дюйм\w*|inch)|\b(?:ssd|hdd|nvme|emmc)\b|\b(?:lp)?ddr\d'
    r'|\b\d{1,2}\s*/\s*\d{3,4}\s*(?:gb|гб)?\b|\b\d{1,2}\s*/\s*[12]\s*(?:tb|тб)\b', re.I)
_WHOLE_DEVICE_CATEGORIES = {'laptops', 'pc_desktops', 'tablets', 'phones', 'smartwatches', 'consoles',
                            'game_consoles', 'monitors', 'tvs'}
_classifier = None


def _is_whole_device(title: str) -> bool:
    if re.search(r'^\s*(?:компьютерн\w*\s+корпус|корпус(?:\s+для\s+пк|\s+компьютерн\w*)?)\b', title, re.I) and not _WHOLE_DEVICE_SIGNS.search(title):
        return False
    if _WHOLE_DEVICE_WORDS.search(title) or _WHOLE_DEVICE_SIGNS.search(title):
        return True
    global _classifier
    try:
        if _classifier is None:
            from catalog_classifier import CatalogClassifier
            _classifier = CatalogClassifier()
        result = _classifier.classify_rules(title)
    except Exception:
        return False
    return bool(result and result.category_id in _WHOLE_DEVICE_CATEGORIES)


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

    # 2. ПК / Ноутбук с связкой GPU VRAM и системной RAM: RTX 4060 8GB / 16GB
    m_gpu_ram = re.search(
        r'(?i)\b(?:rtx|gtx|radeon\s+rx|rx)\s*\d{3,4}(?:\s*(?:ti|super|gre|xtx|xt))?\s*(\d{1,2})\s*(?:gb|гб|g)?\s*[/]\s*(\d{1,2})\s*(?:gb|гб)\b',
        title
    )
    if m_gpu_ram:
        r_val = int(m_gpu_ram.group(2))
        if r_val in (4, 6, 8, 12, 16, 24, 32, 48, 64, 128):
            attrs['ram'] = f"{r_val} ГБ"

    # 3. RAM + Storage combo: 8GB/256GB, 16/512 ГБ, 16/1TB, а также (4/128), (6/128), 8/256, 2GB/32GB
    if 'storage' not in attrs:
        m_combo = re.search(r'(?i)\b(\d+)\s*(?:gb|гб)?\s*[/+]\s*(\d+\s*(?:gb|гб|tb|тб))\b', title)
        if m_combo:
            prefix = title[:m_combo.start()].lower()
            if not re.search(r'\b(?:rtx|gtx|rx|radeon|arc)\s*\d{3,4}\s*$', prefix):
                r_cand = int(m_combo.group(1))
                s_cand = m_combo.group(2)
                norm_s = normalize_attribute_value('storage', s_cand)
                s_num = int(re.match(r'\d+', s_cand).group(0)) if re.match(r'\d+', s_cand) else 0
                is_tb = 'tb' in s_cand.lower() or 'тб' in s_cand.lower()
                # В комбо емкость накопителя >= 32 ГБ и строго больше ОЗУ
                if r_cand in (2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64) and (is_tb or (s_num >= 32 and r_cand < s_num)):
                    if 'ram' not in attrs:
                        attrs['ram'] = f"{r_cand} ГБ"
                    if norm_s:
                        attrs['storage'] = norm_s
        else:
            # Безразмерное комбо в скобках (4/128), (6/128), (8/256) или в смартфоне/планшете/ноутбуке
            m_combo_ul = re.search(r'(?i)(?:\(\s*(\d{1,2})\s*[/+]\s*(32|64|128|256|512|1024)\s*\)|\b(2|3|4|6|8|12|16|24|32)\s*[/]\s*(32|64|128|256|512|1024)\b)', title)
            if m_combo_ul:
                has_parens = m_combo_ul.group(1) is not None
                r_cand = int(m_combo_ul.group(1) or m_combo_ul.group(3))
                s_num = int(m_combo_ul.group(2) or m_combo_ul.group(4))
                if r_cand in (2, 3, 4, 6, 8, 12, 16, 24, 32) and r_cand < s_num and (
                    has_parens or re.search(r'\b(смартфон\w*|телефон\w*|планшет\w*|ноутбук\w*|моноблок\w*|iphone|ipad|galaxy|redmi|poco|xiaomi|honor|huawei|oppo|vivo|realme|tecno|infinix|oneplus|nothing|pixel|motorola|nokia)\b', title, re.I)
                ):
                    if 'ram' not in attrs:
                        attrs['ram'] = f"{r_cand} ГБ"
                    attrs['storage'] = "1 ТБ" if s_num == 1024 else f"{s_num} ГБ"

    # 4. Явный накопитель с маркером (SSD, M.2, HDD, NVMe, накопитель)
    if 'storage' not in attrs:
        m_s_exp = re.search(
            r'(?i)(?:ssd|hdd|m\.2|nvme|накопител\w*)\s*(\d+\s*(?:gb|гб|tb|тб))|\b(\d+\s*(?:gb|гб|tb|тб))\s*(?:ssd|hdd|m\.2|nvme|накопител\w*)',
            title
        )
        if m_s_exp:
            s_val = m_s_exp.group(1) or m_s_exp.group(2)
            norm_s = normalize_attribute_value('storage', s_val)
            if norm_s:
                attrs['storage'] = norm_s

    # 5. Явная оперативная память с маркером (DDR4/5, D4/5, RAM, ОЗУ, памяти)
    if 'ram' not in attrs:
        m_r_exp = re.search(
            r'(?i)\b(4|6|8|12|16|24|32|48|64|96|128)\s*(?:gb|гб)?\s*(?:ddr[345]|d[45]|ram|озу|памяти|so-dimm|dimm)\b|\b(?:ddr[345]|d[45]|so-dimm|dimm)\s*(4|6|8|12|16|24|32|48|64|96|128)\s*(?:gb|гб)\b',
            title
        )
        if m_r_exp:
            r_val = int(m_r_exp.group(1) or m_r_exp.group(2))
            attrs['ram'] = f"{r_val} ГБ"

    # 6. Standalone Storage (128GB, 256 ГБ, 512GB, 1TB, 1024 Гб, а для смартфонов/планшетов также 32GB)
    if 'storage' not in attrs:
        m_s = re.search(r'(?i)\b(64|128|256|512|1024|2048)\s*(?:gb|гб)\b|\b([124])\s*(?:tb|тб)\b', title)
        if m_s:
            raw_s = m_s.group(0)
            norm_s = normalize_attribute_value('storage', raw_s)
            if norm_s:
                attrs['storage'] = norm_s
        elif re.search(r'(?i)\b(смартфон\w*|сотов\w*\s+телефон\w*|мобильн\w*\s+телефон\w*|планшет\w*|iphone|ipad|galaxy\s+[aszm]|redmi|honor|huawei|oppo|vivo|realme|tecno|infinix|nokia)\b', title):
            m_s32 = re.search(r'(?i)\b(16|32)\s*(?:gb|гб)\b', title)
            if m_s32:
                attrs['storage'] = f"{m_s32.group(1)} ГБ"
            else:
                m_paren_st = re.search(r'\(\s*(32|64|128|256|512)\s*\)', title)
                if m_paren_st:
                    attrs['storage'] = f"{m_paren_st.group(1)} ГБ"

    # 7. Для целых устройств: если накопитель уже известен, оставшееся круглое число ГБ — это ОЗУ
    if 'ram' not in attrs and 'storage' in attrs and _is_whole_device(title):
        for m in re.finditer(r'(?i)\b(4|6|8|12|16|24|32|48|64)\s*(?:gb|гб)\b', title):
            val_str = f"{m.group(1)} ГБ"
            if val_str != attrs['storage']:
                prefix = title[:m.start()].lower()
                if not re.search(r'\b(?:rtx|gtx|rx|radeon|geforce|arc)\s*\d{3,4}\s*$', prefix):
                    attrs['ram'] = val_str
                    break

    # 8. Standalone RAM (4GB, 8GB, 16GB, 32GB)
    if 'ram' not in attrs and not re.search(r'\b(видеокарт\w*|geforce|radeon\s+rx|rtx\s*\d{3,4}|gtx\s*\d{3,4})\b', title, re.I):
        m_r = re.search(r'(?i)\b(4|6|8|12|16|24|32|64)\s*(?:gb|гб)\s*(?:ram|озу|памяти)\b', title)
        if m_r:
            attrs['ram'] = f"{m_r.group(1)} ГБ"

    # 5. Screen size (15.6", 14", 27", 24", 6.1", 6.7", включая &#34; и запятую) + характеристики ноутбуков
    m_scr = re.search(r'\b(5[.,][4-9]|6[.,][1-9]|7[.,][0-9]|1[0-7](?:[.,][0-9])?|2[1-9](?:[.,][0-9])?|3[1-4](?:[.,][0-9])?)\s*(?:"|\'\'|”|&#34;|&quot;|дюйм)', title, re.I)
    if m_scr:
        attrs['screen_size'] = f'{m_scr.group(1).replace(",", ".")}"'

    is_laptop_item = bool(re.search(r'\b(ноутбук\w*|ультрабук\w*|macbook|laptop|notebook)\b', title, re.I)) and not bool(
        re.search(r'\b(сумк\w*\s+для|рюкзак\w*\s+для|чехол\w*\s+для|подставк\w*\s+для|столик\s+для|зарядн\w*\s+для|блок\s+питания\s+для|аккумулятор\s+для|батарея\s+для|матрица\s+для|клавиатура\s+для|кулер\s+для|вентилятор\s+для|охлаждающ\w*\s+подставк\w*|пленк\w*\s+для|стекл\w*\s+для)\b', title, re.I)
    )
    if is_laptop_item:
        if 'screen_size' not in attrs:
            if re.search(r'\b(ph317|an517|a317|fx70[67]|fa70[67]|g713|g733|strix\s+g17|raider\s+ge7\d|katana\s+(?:gf76|17)|pulse\s+(?:gl76|17)|vector\s+gp7\d|stealth\s+17|bravo\s+17|crosshair\s+17|creator\s+z17|legion\s+[57]\s*17|ideapad\s+\d+\s*17|v17\b|envy\s+17|pavilion\s+17|omen\s+17|470\s+g[789]|17[.,]3\b)\b', title, re.I):
                attrs['screen_size'] = '17.3"'
            elif re.search(r'\b(ph16|phn16|an16|sfg16|sfx16|a516|fx607|fa617|gu60[345]|g614|g634|v3607|k3605|m1605|x1605|vivobook\s+(?:s\s+|pro\s+)?16|zenbook\s+(?:s\s+|pro\s+)?16|strix\s+(?:scar\s+)?g?16|zephyrus\s+(?:g16|m16)|flow\s+x16|legion\s+(?:pro\s+|slim\s+)?[579]i?\s*16|\d{2}irx[89]|\d{2}iah[78]|\d{2}ach6|thinkbook\s+16|thinkpad\s+[etlp]16|ideapad\s+(?:slim\s+|pro\s+)?[35]i?\s*16|victus\s+16|omen\s+(?:transcend\s+)?16|pavilion\s+plus\s+16|envy\s+16|spectre\s+x360\s+16|elitebook\s+\d{3}\s+16|probook\s+46[05]|vostro\s+(?:56[234]\d|76[23]\d)|inspiron\s+(?:56[234]\d|76[234]\d)|xps\s+16|macbook\s+pro\s+16|a2141|a2485|a2780|a2991|matebook\s+d?\s*16|magicbook\s+(?:x\s*|pro\s*)?16|gram\s+16|galaxy\s+book\d*\s+(?:pro\s+|ultra\s+)?16|vector\s+16|stealth\s+16|pulse\s+16|sword\s+16|crosshair\s+16|summit\s+e16|prestige\s+16|creator\s+m16)\b', title, re.I):
                attrs['screen_size'] = '16"'
            elif re.search(r'\b(sfg14|sfx14|sp314|sf314|sf514|a314|a514|ex214|tmp214|fa401|ga40[123]|ux340\d|ux4\d{2}|um340\d|b140\d|e140\d|x140\d|k340\d|m140\d|vivobook\s+(?:go\s+|s\s+|pro\s+|flip\s+)?14|zenbook\s+(?:s\s+|duo\s+|flip\s+)?14|zephyrus\s+g14|ideapad\s+(?:slim\s+|pro\s+|flex\s+)?[135]i?\s*14|yoga\s+(?:slim\s+|pro\s+)?[679]i?\s*14|thinkbook\s+14|thinkpad\s+(?:[etlp]14|x1\s+carbon|x1\s+yoga)|14(?:itl|alc|aba|iau|iap|iru|amn|abr|ahp|imh|irh|arp)\d*|v14\b|pavilion\s+(?:plus\s+|x360\s+)?14|envy\s+(?:x360\s+)?14|spectre\s+x360\s+14|elitebook\s+(?:84[05]|64[05]|1040)|probook\s+(?:44[05]|640)|14s[- ][a-z]{2}\d+|14[- ](?:dq|dv|dy|ek|em|ep|ew|cf|ck|cm|dk)\d*|240\s+g[789]|245\s+g[789]|vostro\s+(?:34\d{2}|54\d{2})|inspiron\s+(?:34\d{2}|54\d{2}|74\d{2})|latitude\s+(?:34\d{2}|54\d{2}|74\d{2}|94\d{2})|macbook\s+pro\s+14|a2442|a2779|a2918|a2992|matebook\s+(?:d\s*|x\s*)?14|magicbook\s+(?:x\s*)?14|modern\s+14|prestige\s+14|summit\s+e14|stealth\s+14|cyborg\s+14)\b', title, re.I):
                attrs['screen_size'] = '14"'
            elif re.search(r'\b(xps\s+13|latitude\s+(?:33\d{2}|53\d{2}|73\d{2})|inspiron\s+(?:53\d{2}|73\d{2})|ux3\d{2}|um3\d{2}|zenbook\s+(?:s\s+|flip\s+)?13|flow\s+x13|gv30[12]|thinkpad\s+(?:x13|l13|x390)|thinkbook\s+13|yoga\s+\d+\s*13|ideapad\s+\d+\s*13|spectre\s+x360\s+13|envy\s+(?:x360\s+)?13|pavilion\s+aero\s+13|elitebook\s+(?:83[05]|63[05]|x360\s+1030)|probook\s+43[05]|macbook\s+(?:air|pro)\s+13|a1466|a1502|a1706|a1708|a1932|a1989|a2159|a2179|a2251|a2289|a2337|a2338|a2681|a3113|matebook\s+(?:13|x\s+pro)|summit\s+e13|prestige\s+13|13[.,]3\b)\b', title, re.I):
                attrs['screen_size'] = '13.3"'
            elif re.search(r'\b(ph315|an515|anv15|a315|a515|a715|ex215|tmp215|av15|aspire\s+[357]|extensa\s+15|nitro\s+(?:5|v?\s*15)|predator\s+helios\s+300|fx50[567]|fx51[67]|fa50[67]|g51[23]|g53[123]|gu502|ga50[23]|x515|m515|b150\d|e150\d|x150\d|m150\d|k350\d|d515|x509|x543|vivobook\s+(?:go\s+|s\s+|pro\s+|15x\s+)?15|zenbook\s+(?:pro\s+)?15|tuf\s+(?:gaming\s+|dash\s+)?[fa]15|strix\s+(?:scar\s+)?g?15|zephyrus\s+g15|ideapad\s+(?:slim\s+|gaming\s+)?[135]i?\s*15|legion\s+5i?\s*15|loq\s+15|thinkbook\s+15|thinkpad\s+[etlp]15|v15\b|15(?:igl|ada|alc|itl|iau|iap|iru|amn|abr|ach|arh|irh|iax|arp|ahp)\d*|victus\s+15|omen\s+15|pavilion\s+(?:gaming\s+|x360\s+|plus\s+)?15|envy\s+(?:x360\s+)?15|elitebook\s+(?:85[05]|65[05])|probook\s+(?:45[05]|650)|15s[- ][a-z]{2}\d+|15[- ](?:dw|dy|ef|eh|eq|fc|fd|fq|gw|dk|ec|fb|fa|da|db|bs|bw|ra|rb)\d*|250\s+g[789]|255\s+g[789]|450\s+g[789]|vostro\s+(?:35\d{2}|55\d{2}|15)|inspiron\s+(?:35\d{2}|55\d{2}|75\d{2}|15)|latitude\s+(?:35\d{2}|55\d{2}|75\d{2}|95\d{2})|g15\s+55\d{2}|g5\s+(?:se[- ])?55\d{2}|g3\s+35\d{2}|katana\s+(?:gf66|15)|cyborg\s+15|thin\s+(?:gf63|15)|modern\s+15|bravo\s+15|alpha\s+15|pulse\s+(?:gl66|15)|crosshair\s+15|stealth\s+15|delta\s+15|leopard\s+gl65|raider\s+ge66|matebook\s+d\s*15|magicbook\s+(?:x\s*)?15|macbook\s+air\s+15|a2941|a3114|15[.,]6\b)\b', title, re.I):
                attrs['screen_size'] = '15.6"'

        if 'cpu' not in attrs:
            if re.search(r'\bcore\s+ultra\s*[579]\b|\bultra\s+[579]\s+1\d{2}[hu]\b', title, re.I):
                attrs['cpu'] = 'Intel Core Ultra'
            elif re.search(r'\b(?:core\s*)?i9(?:[-\s]\d{4,5}[a-z]{0,2}|\b)', title, re.I):
                attrs['cpu'] = 'Intel Core i9'
            elif re.search(r'\b(?:core\s*)?i7(?:[-\s]\d{4,5}[a-z]{0,2}|\b)', title, re.I):
                attrs['cpu'] = 'Intel Core i7'
            elif re.search(r'\b(?:core\s*)?i5(?:[-\s]\d{4,5}[a-z]{0,2}|\b)', title, re.I):
                attrs['cpu'] = 'Intel Core i5'
            elif re.search(r'\b(?:core\s*)?i3(?:[-\s]\d{4,5}[a-z]{0,2}|\b)', title, re.I):
                attrs['cpu'] = 'Intel Core i3'
            elif re.search(r'\b(?:ryzen\s*9|r9[-\s]\d{4}[a-z]{0,2})\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 9'
            elif re.search(r'\b(?:ryzen\s*7|r7[-\s]\d{4}[a-z]{0,2})\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 7'
            elif re.search(r'\b(?:ryzen\s*5|r5[-\s]\d{4}[a-z]{0,2})\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 5'
            elif re.search(r'\b(?:ryzen\s*3|r3[-\s]\d{4}[a-z]{0,2})\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 3'
            elif re.search(r'\b(?:apple\s+m[1234]|m[1234]\s+(?:pro|max)|chip\s+m[1234]|a2337|a2338|a2442|a2485|a2681|a2779|a2780|a2941|a2918|a2991|a2992|a3113|a3114)\b', title, re.I):
                attrs['cpu'] = 'Apple M-Series'
            elif re.search(r'\b(pentium|celeron|athlon|n100|n200|n4020|n4500|n5100|n305)\b', title, re.I):
                attrs['cpu'] = 'Intel Pentium / Celeron'

        if 'gpu' not in attrs:
            m_lgpu = re.search(r'\b(rtx\s*[2345]0[56789]0(?:\s*ti)?|gtx\s*16[56]0(?:\s*ti)?|rx\s*[567]\d00[ms]?)\b', title, re.I)
            if m_lgpu:
                attrs['gpu'] = re.sub(r'\s+', ' ', m_lgpu.group(1).upper())

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
    is_monitor = bool(re.search(r'\b(монитор\w*|monitor\w*)\b', title, re.I)) and not bool(
        re.search(r'\b(кронштейн\s+для|креплен\w*\s+для|подставк\w*\s+для|салфетк\w*\s+для|чистящ\w*\s+средств\w*\s+для)\b', title, re.I)
    )
    if is_monitor:
        # Диагональ экрана (21.5", 23.8", 24", 24.5", 27", 31.5", 32", 34", 49", 55", 57", включая &#34;, ' и модельные коды)
        m_msz = (
            re.search(r'\b(1[5-9](?:[.,][0-9])?|2[1-9](?:[.,][0-9])?|3[1-9](?:[.,][0-9])?|4[0-9](?:[.,][0-9])?|5[57])\s*(?:"|\'\'|\'|”|&#34;|&quot;|дюйм|\binch\b|\b(?=\s*(?:fhd|qhd|uhd|2k|4k|ips|va|oled|gaming|игров|144|165|180|240|hz|гц)))', title, re.I)
            or re.search(r'(?:"|”|&#34;|&quot;)\s*(2[1-9](?:[.,][0-9])?|3[1-9](?:[.,][0-9])?)\b', title, re.I)
            or re.search(r'\b(?:монитор|monitor)\s+(21[.,]5|23[.,]8|24[.,]5|24|27|31[.,]5|32|34)\b', title, re.I)
        )
        if m_msz:
            attrs['screen_size'] = f'{m_msz.group(1).replace(",", ".")}"'
        elif 'screen_size' not in attrs:
            if re.search(r'\b(?:ls27|lc27|lf27|s27|f27|g27|vg27|vp27|va27|vx27|xg27|pg27|pa27|27g[s56]|27u[pknl]|27b\d|27e[12]|27m[12]|aw27|p272|u272|s272|e272|327pf|x27\b|g27i|a27i|27i\b|m27[fq]|gs27|pd27|gw27|ew27|xl27|ex27|mobiuz\s+ex27|odyssey\s+g\d\s+27)', title, re.I):
                attrs['screen_size'] = '27"'
            elif re.search(r'\b(?:ls24|lc24|lf24|s24|f24|f23hf|g24|vg24|vp24|va24|vx24|xg24|pg24|pa24|24g[s56]|24m[12]|24b\d|24e[12]|24km|kl24|ka24|sa24|sh24|ek24|cb24|b24[78]|v24[78]|nitros?\s+(?:vg24|kg24|qg24|xv24)|p242|u242|s242|e242|324pf|g24i|a24i|24i\b|m24f|gw24|xl24|ex24)', title, re.I):
                attrs['screen_size'] = '23.8"'
            elif re.search(r'\b(?:m245|lf25|25g\d|aw25|vg25|xg25|xl25|24[.,]5)', title, re.I):
                attrs['screen_size'] = '24.5"'
            elif re.search(r'\b(?:ls32|lc32|s32|g32|vg32|vp32|va32|vx32|xg32|pg32|pa32|32g[s56]|32u[pknl]|32b\d|32e[12]|32m[12]|aw32|p322|u322|s322|e322|m32[fqu]|gs32|pd32|ew32|ex32|odyssey\s+(?:neo\s+)?g\d\s+32)', title, re.I):
                attrs['screen_size'] = '31.5"'
            elif re.search(r'\b(?:ls34|lc34|s34|g34|vg34|vp34|va34|vx34|xg34|pg34|34w[pknl]|34g[s56]|34b\d|34e[12]|aw34|u342|s342|p342|m34w|gs34|ex34|odyssey\s+g\d\s+34)', title, re.I):
                attrs['screen_size'] = '34"'
            elif re.search(r'\b(?:322pf|22b\d|22e[12]|v22[78]|k222|ka22|sa22|ek22|gw22|va22|vp22|vx22|p222|e222|s222|ls22|lf22)', title, re.I):
                attrs['screen_size'] = '21.5"'
            elif re.search(r'\b(?:20b\d|e202|v20[67]|k202|19[.,]5)', title, re.I):
                attrs['screen_size'] = '19.5"'
        # Частота обновления (60 Гц, 75 Гц, 100 Гц, 144 Гц, 165 Гц, 180 Гц, 240 Гц, 360 Гц)
        m_mhz = re.search(r'\b(60|75|100|120|144|160|165|170|175|180|200|240|280|360|480|540)\s*(?:hz|гц)\b', title, re.I)
        if m_mhz:
            attrs['refresh_rate'] = f"{m_mhz.group(1)} Гц"
        # Тип матрицы (IPS, Fast IPS, VA, OLED, Mini-LED, TN)
        m_mpt = re.search(r'\b(fast\s+ips|rapid\s+ips|nano\s+ips|qd-oled|mini-led|oled|ips|va|tn)\b', title, re.I)
        if m_mpt:
            attrs['panel_type'] = normalize_attribute_value('panel_type', m_mpt.group(1))
        # Разрешение экрана и соотношение сторон
        if re.search(r'\b(5120\s*[xх]\s*1440|dqhd|32:9)\b', title, re.I):
            attrs['resolution'] = '5120x1440 (DQHD)'
            attrs['aspect_ratio'] = '32:9 (Super UltraWide)'
        elif re.search(r'\b(3840\s*[xх]\s*2160|4k|uhd)\b', title, re.I):
            attrs['resolution'] = '3840x2160 (4K UHD)'
            attrs['aspect_ratio'] = '16:9'
        elif re.search(r'\b(3440\s*[xх]\s*1440|uwqhd)\b', title, re.I):
            attrs['resolution'] = '3440x1440 (UWQHD)'
            attrs['aspect_ratio'] = '21:9 (UltraWide)'
        elif re.search(r'\b(2560\s*[xх]\s*1080|uwfhd|wfhd|ultrawide)\b', title, re.I):
            attrs['resolution'] = '2560x1080 (UWFHD)'
            attrs['aspect_ratio'] = '21:9 (UltraWide)'
        elif re.search(r'\b(2560\s*[xх]\s*1440|2k|qhd|wqhd)\b', title, re.I):
            attrs['resolution'] = '2560x1440 (2K QHD)'
            attrs['aspect_ratio'] = '16:9'
        elif re.search(r'\b(1920\s*[xх]\s*1080|fhd|full\s*hd)\b', title, re.I):
            attrs['resolution'] = '1920x1080 (Full HD)'
            attrs['aspect_ratio'] = '16:9'
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
        # Тип устройства — как у классификатора каталога, плюс признаки целого устройства (R04).
        is_whole_device = _is_whole_device(title)
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
                attrs['frequency'] = f"{m_ram_sp.group(1)} МГц"
            if re.search(r'\bso-dimm\b', title, re.I):
                attrs['form_factor'] = 'SO-DIMM'

        # Процессоры (CPUs)
        is_cpu = not is_whole_device and bool(re.search(
            r'\b(процессор\w*|intel\s+(?:core|xeon|pentium|celeron)|amd\s+(?:ryzen|athlon|threadripper|epyc))\b',
            title, re.I
        )) and not re.search(r'\b(кулер|охлаждени\w*|вентилятор|материнск\w*)\b', title, re.I)
        if is_cpu:
            if re.search(r'\bcore\s+ultra\s*[579]\b', title, re.I):
                attrs['cpu_family'] = 'Intel Core Ultra'
            elif re.search(r'\bcore\s*i9\b', title, re.I):
                attrs['cpu_family'] = 'Intel Core i9'
            elif re.search(r'\bcore\s*i7\b', title, re.I):
                attrs['cpu_family'] = 'Intel Core i7'
            elif re.search(r'\bcore\s*i5\b', title, re.I):
                attrs['cpu_family'] = 'Intel Core i5'
            elif re.search(r'\bcore\s*i3\b', title, re.I):
                attrs['cpu_family'] = 'Intel Core i3'
            elif re.search(r'\bryzen\s*9\b', title, re.I):
                attrs['cpu_family'] = 'AMD Ryzen 9'
            elif re.search(r'\bryzen\s*7\b', title, re.I):
                attrs['cpu_family'] = 'AMD Ryzen 7'
            elif re.search(r'\bryzen\s*5\b', title, re.I):
                attrs['cpu_family'] = 'AMD Ryzen 5'
            elif re.search(r'\bryzen\s*3\b', title, re.I):
                attrs['cpu_family'] = 'AMD Ryzen 3'
            elif re.search(r'\bthreadripper\b', title, re.I):
                attrs['cpu_family'] = 'AMD Threadripper'
            elif re.search(r'\bxeon\b', title, re.I):
                attrs['cpu_family'] = 'Intel Xeon'
            elif re.search(r'\b(pentium|celeron)\b', title, re.I):
                attrs['cpu_family'] = 'Intel Pentium / Celeron'
            elif re.search(r'\bathlon\b', title, re.I):
                attrs['cpu_family'] = 'AMD Athlon'

            if re.search(r'\b(box|бокс)\b', title, re.I):
                attrs['delivery_type'] = 'BOX'
            elif re.search(r'\b(oem|tray)\b', title, re.I):
                attrs['delivery_type'] = 'OEM / Tray'

            m_cores = re.search(r'\b(\d{1,2})\s*(?:ядер|ядра|core|cores|-core)\b', title, re.I)
            if m_cores:
                attrs['cores'] = f"{m_cores.group(1)} ядер"

            m_freq = re.search(r'\b([2-5][.,]\d{1,2})\s*(?:ггц|ghz)\b', title, re.I)
            if m_freq:
                attrs['frequency'] = f"{m_freq.group(1).replace(',', '.')} ГГц"

        # Корпуса для ПК (PC Cases)
        is_case = not is_whole_device and bool(re.search(
            r'^\s*корпус\b|\bкорпус\s+(?:для\s+пк|компьютерн\w*)\b|\bкомпьютерн\w*\s+корпус\b|\bпанель\s+lcd\s+(?:thermaltake|lian\s+li)\b',
            title, re.I
        ))
        if is_case:
            if re.search(r'\b(tg|tempered\s+glass|закаленн\w*\s+стекл\w*)\b', title, re.I):
                attrs['side_window'] = 'Закаленное стекло (TG)'
            elif re.search(r'\b(window|акрил\w*|с\s+окном)\b', title, re.I):
                attrs['side_window'] = 'С окном'

            if re.search(r'\bargb\b', title, re.I):
                attrs['backlight'] = 'ARGB'
            elif re.search(r'\bfrgb\b', title, re.I):
                attrs['backlight'] = 'FRGB'
            elif re.search(r'\brgb\b', title, re.I):
                attrs['backlight'] = 'RGB'

            m_case_psu = re.search(r'\b([3-8]\d0)\s*(?:w|вт)\b', title, re.I)
            if m_case_psu:
                attrs['psu_included'] = f"Со встроенным БП ({m_case_psu.group(1)} Вт)"
            elif re.search(r'\bбез\s+б/?п\b', title, re.I):
                attrs['psu_included'] = 'Без БП'

            if 'form_factor' not in attrs:
                if re.search(r'\b(mini|micro|matx|m-atx)\b', title, re.I):
                    attrs['form_factor'] = 'Mini-Tower / Micro-ATX'
                else:
                    attrs['form_factor'] = 'Mid-Tower / ATX'

        # Материнские платы (Motherboards)
        is_mb = bool(re.search(r'\b(материнск\w*\s+плат\w*|матплат\w*|mainboard|motherboard)\b', title, re.I))
        if is_mb:
            m_mb_ch = re.search(r'\b(z[345678]\d0|b[345678]\d0[e]?|x[4568]70[e]?|a[356]20|h[345678]\d0|h\d{2})\b|\b(z[345678]\d0|b[345678]\d0[e]?|x[4568]70[e]?|a[356]20|h[345678]\d0)(?=[mi-]\b|[mi][-\s])', title, re.I)
            if m_mb_ch:
                ch_val = (m_mb_ch.group(1) or m_mb_ch.group(2)).upper()
                attrs['chipset'] = ch_val
                if 'socket' not in attrs:
                    if re.match(r'^(B650|B850|X670|X870|A620)', ch_val):
                        attrs['socket'] = 'AM5'
                    elif re.match(r'^(B450|B550|X470|X570|A320|A520)', ch_val):
                        attrs['socket'] = 'AM4'
                    elif re.match(r'^(Z890|B860|H810)', ch_val):
                        attrs['socket'] = 'LGA 1851'
                    elif re.match(r'^(Z690|Z790|B660|B760|H610|H670|H770)', ch_val):
                        attrs['socket'] = 'LGA 1700'
                    elif re.match(r'^(Z490|Z590|B460|B560|H410|H510)', ch_val):
                        attrs['socket'] = 'LGA 1200'
            m_mb_sk = re.search(r'\b(am[45]|lga\s*\d{4}|str5|tr4)\b', title, re.I)
            if m_mb_sk:
                attrs['socket'] = re.sub(r'^LGA\s*', 'LGA ', m_mb_sk.group(1).upper())
            if re.search(r'\b(?:ddr5|d5)\b', title, re.I):
                attrs['memory_type'] = 'DDR5'
            elif re.search(r'\b(?:ddr4|d4)\b', title, re.I):
                attrs['memory_type'] = 'DDR4'
            if 'form_factor' not in attrs:
                if re.search(r'\b(?:z|b|x|a|h)\d{2,3}e?i\b|\bmini[- ]?itx\b', title, re.I):
                    attrs['form_factor'] = 'Mini-ITX'
                elif re.search(r'\b(?:z|b|x|a|h)\d{2,3}e?m\b|\bmicro[- ]?atx\b|\bmatx\b', title, re.I):
                    attrs['form_factor'] = 'Micro-ATX'
                else:
                    attrs['form_factor'] = 'ATX'

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
            elif re.search(r'\bnvme\b', title, re.I):
                attrs['interface'] = 'NVMe PCIe'
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

        # Тип охлаждения и кулеров
        is_cooling_item = bool(re.search(r'\b(кулер\w*|сжо|сво|водян\w*\s+охлажден\w*|жидкостн\w*\s+охлажден\w*|вентилятор\w*\s+для\s+корпуса|корпусн\w*\s+вентилятор\w*|термопаст\w*|термопрокладк\w*|охлажден\w*\s+для\s+(?:процессора|пк|ssd))\b', title, re.I))
        if is_cooling_item:
            if any(k in title_lower for k in ['сжо', 'сво', 'водяного охлаждения', 'жидкостн']) or re.search(r'\b(240|280|360|420)\s*(?:mm|мм)?\b.*\b(liquid|water|aio|ls[57]20|le[57]20|lt[57]20|lq[57]20|mystique|arctic\s+liquid)\b', title, re.I):
                attrs['type'] = 'СЖО (водяное)'
            elif re.search(r'\b(термопаст\w*|термопрокладк\w*|жидкий\s+металл)\b', title, re.I):
                attrs['type'] = 'Термопаста / Термоинтерфейс'
            elif 'вентилятор для корпуса' in title_lower or 'корпусный вентилятор' in title_lower or re.search(r'^\s*вентилятор\b', title, re.I):
                attrs['type'] = 'Вентилятор для корпуса'
            else:
                attrs['type'] = 'Воздушный кулер'

            m_fan = re.search(r'\b(80|92|120|140|200|240|280|360|420)\s*(?:mm|мм)\b|\b(?:le|ls|lt|lq|ag|ak)(?:300|400|500|620|520|720)\b', title, re.I)
            if m_fan:
                if m_fan.group(1):
                    attrs['fan_size'] = f"{m_fan.group(1)} мм"
                elif '520' in m_fan.group(0):
                    attrs['fan_size'] = '240 мм'
                elif '720' in m_fan.group(0):
                    attrs['fan_size'] = '360 мм'
                elif '92' in title or '300' in m_fan.group(0):
                    attrs['fan_size'] = '92 мм'
                else:
                    attrs['fan_size'] = '120 мм'

            if 'backlight' not in attrs:
                if re.search(r'\bargb\b', title, re.I):
                    attrs['backlight'] = 'ARGB'
                elif re.search(r'\bfrgb\b', title, re.I):
                    attrs['backlight'] = 'FRGB'
                elif re.search(r'\brgb\b', title, re.I):
                    attrs['backlight'] = 'RGB'
                elif re.search(r'\bled\b', title, re.I):
                    attrs['backlight'] = 'LED'

        # Сокет (если еще не определен)
        if 'socket' not in attrs:
            m_sock = re.search(r'\b(lga\s*\d{4}|am[45]|str5|tr4)\b', title, re.I)
            if m_sock:
                sock_val = m_sock.group(1).upper()
                attrs['socket'] = re.sub(r'^LGA\s*', 'LGA ', sock_val)

    # 9. Винил и музыкальные носители (только если не комплектующие ПК)
    if not is_pc_part and re.search(r'\b(?:винил(?:овая\s+пластинка|\s+lp)?|виниловые\s+пластинки|грампластинк\w*|\b2lp\b|\b\d?lp(?=\s*(?:диск|пластинк|альбом|repress|remaster|edition|soundtrack|vinyl|\(|$)))\b', title, re.I):
        if re.search(r'\b2lp\b', title, re.I):
            attrs['format'] = '2LP'
        elif re.search(r'\blp\b|винил', title, re.I):
            attrs['format'] = 'LP'

    # 10. Посудомоечные машины
    if re.search(r'\b(посудомоечн\w*|посудомойк\w*|dishwasher)\b', title, re.I):
        if re.search(r'\bвстраиваем\w*\b', title, re.I):
            attrs['installation_type'] = 'Встраиваемая'
        elif re.search(r'\b(?:настольн\w*|компактн\w*)\b', title, re.I):
            attrs['installation_type'] = 'Настольная'
        elif re.search(r'\b(?:отдельностоящ\w*|отдельно\s+стоящ\w*)\b', title, re.I):
            attrs['installation_type'] = 'Отдельностоящая'

        m_width = re.search(r'\b(45|60|55)\s*(?:см|cm)\b|\b(450|600|550)\s*(?:мм|mm)\b', title, re.I)
        if m_width:
            w_val = m_width.group(1) or str(int(m_width.group(2)) // 10)
            attrs['width_cm'] = f"{w_val} см"

        m_cap = re.search(r'\b(6|8|9|10|11|12|13|14|15|16)\s*(?:комплект\w*|sets)\b', title, re.I)
        if m_cap:
            attrs['capacity_sets'] = f"{m_cap.group(1)} комплектов"

    # 11. Телевизоры, проекторы, проекционные экраны и ТВ-аксессуары
    is_tv = bool(re.search(
        r'\b(телевизор\w*|\btv\b|oled|qled|led\s+тв|smart\s+tv|проектор\w*|мультимедиа[- ]проектор\w*|'
        r'экран\s+(?:моторизованн\w*|настенн\w*|на\s+треноге|мобильн\w*|для\s+проектора|проекционн\w*|mr\.?\s*pixel|deluxe\s+dlx)|'
        r'креплен\w*\s+(?:потолочн\w*|настенн\w*|наклонно[- ]поворотн\w*|для\s+(?:проектора|телевизора|тв))|'
        r'кронштейн\s+для\s+(?:проектора|телевизора|тв)|tv[- ]?тюнер\w*|тв[- ]?приставк\w*|медиаплеер\w*)\b',
        title, re.I
    )) and not is_monitor and not is_laptop_item
    if is_tv:
        if re.search(r'\b(креплен\w*|кронштейн\w*)\b', title, re.I):
            attrs['type'] = 'Кронштейн / Крепление'
        elif re.search(r'\b(экран\s+(?:моторизованн\w*|настенн\w*|на\s+треноге|мобильн\w*|для\s+проектора|проекционн\w*|mr\.?\s*pixel|deluxe)|проекционн\w*\s+экран)\b', title, re.I):
            attrs['type'] = 'Проекционный экран'
        elif re.search(r'\b(проектор\w*|мультимедиа[- ]проектор\w*)\b', title, re.I) and not re.search(r'\b(игрушк\w*|ночник|детск\w*\s+проектор|для\s+проектора|лампа\s+для)\b', title, re.I):
            attrs['type'] = 'Проектор'
        elif re.search(r'\b(tv[- ]?тюнер\w*|тв[- ]?приставк\w*|медиаплеер\w*|apple\s+tv|mi\s+tv\s+stick|mi\s+box)\b', title, re.I):
            attrs['type'] = 'ТВ-приставка / Тюнер'
        elif re.search(r'\b(телевизор\w*|\btv\b|oled|qled|nanocell|smart\s+tv)\b', title, re.I):
            attrs['type'] = 'Телевизор'

        # Диагональ
        m_diag = (
            re.search(r'\b(24|32|40|42|43|48|50|55|58|65|70|75|77|83|85|86|98|100|106|120)\s*(?:"|\'\'|\'|”|&#34;|&quot;|дюйм\w*)', title, re.I)
            or re.search(r'\b(24|32|40|42|43|48|50|55|58|65|70|75|77|83|85|86|98|100)\b(?=\s*(?:[A-Za-z0-9_-]+\s*)?(?:oled|qled|uhd|4k|smart|led|fhd|hd|nanocell))', title, re.I)
            or re.search(r'\b(?:телевизор|tv)\s+(?:[a-zа-я]+\s+)?(?:ue|qe|kd-|xr-|oled|qled|lt-|le-)?(24|32|40|42|43|48|50|55|58|65|70|75|77|83|85|86|98|100)[a-z0-9]', title, re.I)
        )
        if m_diag:
            d_val = m_diag.group(1) or m_diag.group(2) or m_diag.group(3)
            attrs['screen_diagonal'] = f'{d_val}"'
        # Разрешение
        if re.search(r'\b(4k|uhd|ultra\s*hd|3840\s*[xх]\s*2160)\b', title, re.I):
            attrs['resolution'] = '4K UHD'
        elif re.search(r'\b(8k|7680\s*[xх]\s*4320)\b', title, re.I):
            attrs['resolution'] = '8K'
        elif re.search(r'\b(full\s*hd|fhd|1080p|1920\s*[xх]\s*1080|1920\s*[xх]\s*1200|wuxga)\b', title, re.I):
            attrs['resolution'] = 'Full HD'
        elif re.search(r'\b(hd\s*ready|\bhd\b|wxga|1280\s*[xх]\s*800|1280\s*[xх]\s*720)\b', title, re.I):
            attrs['resolution'] = 'HD'
        # Технология экрана
        if re.search(r'\bmini[- ]?led\b', title, re.I):
            attrs['screen_tech'] = 'Mini LED'
        elif re.search(r'\boled(?:\d|\b)', title, re.I):
            attrs['screen_tech'] = 'OLED'
        elif re.search(r'\b(?:neo\s+)?qled(?:\d|\b)', title, re.I):
            attrs['screen_tech'] = 'QLED'
        elif re.search(r'\bnanocell\b', title, re.I):
            attrs['screen_tech'] = 'NanoCell'
        elif re.search(r'\b(3lcd|dlp)\b', title, re.I):
            attrs['screen_tech'] = m_tech.group(1).upper() if (m_tech := re.search(r'\b(3lcd|dlp)\b', title, re.I)) else 'LED'
        elif re.search(r'\bled\b', title, re.I):
            attrs['screen_tech'] = 'LED'
        # Smart TV
        if re.search(r'\b(smart\s*tv|смарт\s*тв|android\s*tv|google\s*tv|webos|tizen|салют\s*тв|яндекс\s*тв|vidaa)\b', title, re.I):
            attrs['smart_tv'] = 'Да'
        # Частота обновления
        m_hz = re.search(r'\b(60|120|144)\s*(?:гц|hz)\b', title, re.I)
        if m_hz:
            attrs['refresh_rate'] = f"{m_hz.group(1)} Гц"

    # 12. Наушники
    is_hp = bool(re.search(
        r'\b(наушник\w*|гарнитур\w*|airpods|earbuds|galaxy\s+buds|redmi\s+buds|freebuds|enco\s+(?:air|buds|free|x)|'
        r'soundcore\s+(?:life|liberty|space|sport)|marshall\s+(?:major|minor|monitor|motif)|'
        r'wh-1000xm|wf-1000xm|linkbuds|nothing\s+ear|headphones|headset|\btws\b)\b',
        title, re.I
    ))
    if is_hp:
        if re.search(r'\b(беспроводн\w*|bluetooth|\bbt\b|tws|true\s+wireless|airpods|galaxy\s+buds|redmi\s+buds|freebuds|enco\s+|wh-1000|wf-1000|linkbuds|nothing\s+ear|lightspeed|wireless)\b', title, re.I):
            attrs['wireless'] = 'Да'
            attrs['interface'] = 'Bluetooth'
        else:
            attrs['wireless'] = 'Нет'

        if re.search(r'\b(3\.5\s*(?:мм|mm)|mini[- ]?jack|\baux\b)\b', title, re.I):
            attrs['interface'] = '3.5 мм (Mini-Jack)'
        elif re.search(r'\b(type[- ]?c|usb[- ]?c)\b', title, re.I) and attrs.get('wireless') != 'Да':
            attrs['interface'] = 'USB Type-C'
        elif re.search(r'\blightning\b', title, re.I) and attrs.get('wireless') != 'Да':
            attrs['interface'] = 'Lightning'
        elif re.search(r'\busb\b', title, re.I) and attrs.get('wireless') != 'Да':
            attrs['interface'] = 'USB'
        elif re.search(r'\b(2\.4\s*ghz|радиоканал|lightspeed)\b', title, re.I):
            attrs['interface'] = 'Радиоканал 2.4 ГГц'
            attrs['wireless'] = 'Да'

        if re.search(r'\b(гарнитур\w*|headset|с\s+микрофоном|airpods|galaxy\s+buds|redmi\s+buds|freebuds|tws)\b', title, re.I):
            attrs['microphone'] = 'Со встроенным микрофоном'

        if re.search(r'\b(tws|true\s+wireless|airpods(?!\s+max)|galaxy\s+buds|redmi\s+buds|freebuds|enco|wf-1000|nothing\s+ear|earbuds|внутриканальн\w*|вкладыш\w*|затычки)\b', title, re.I):
            attrs['type'] = 'Внутриканальные / TWS'
        elif re.search(r'\b(полноразмерн\w*|охватывающ\w*|over[- ]ear|airpods\s+max|wh-1000|cloud\s+(?:ii|alpha|stinger|iii)|kraken|blackshark|barracuda|arctis|g\s*pro\s*x|g435|g733)\b', title, re.I):
            attrs['type'] = 'Полноразмерные'
        elif re.search(r'\b(накладн\w*|on[- ]ear|major\s+(?:iv|v|iii)|ch520|ch510|t510bt|t520bt|tune\s+[567]\d{2})\b', title, re.I):
            attrs['type'] = 'Накладные'
        elif re.search(r'\bгарнитур\w*\b', title, re.I):
            attrs['type'] = 'Гарнитура (с микрофоном)'
        else:
            attrs['type'] = 'Классические наушники'

        if re.search(r'\b(anc|шумоподавлен\w*|active\s+noise|airpods\s+pro|airpods\s+max|wh-1000|wf-1000|freebuds\s+pro|buds\s*\d*\s*pro)\b', title, re.I):
            attrs['anc'] = 'Да'

    # 13. Кондиционеры
    is_not_ac = bool(re.search(
        r'\b(для\s+(?:волос|белья|ткани|стирки|кожи|тела|авто|шин|аквариум\w*|пруд\w*|воды)|бальзам|шампунь|ополаскивател\w*|conditioner\b|\d+\s*(?:мл|ml|г|гр)\b)\b',
        title, re.I
    ))
    is_ac = not is_not_ac and bool(re.search(
        r'\b(кондиционер\w*|сплит[- ]систем\w*|климатическ\w*|монтажн\w*\s+комплект\b|'
        r'(?:midea|gree|haier|almacom|ava|atlantiq|chigo|hisense|aux|lg|samsung|tcl|ballu|electrolux|zanussi|centek|beko|daikin|mitsubishi|kentatsu|royal\s+clima|mdv|tosot|lessar)\s+.*?(?:inverter|on[- /]?off|\b(?:07|09|12|18|24|30|36)k?\b|(?:mega|actn|ach|afi|msag|ar|as|ac|cs|tac|bspi|bsui)[-\s]?(?:07|09|12|18|24|30|36)))',
        title, re.I
    ))
    if is_ac:
        if re.search(r'\bмобильн\w*\b', title, re.I):
            attrs['type'] = 'Мобильный'
        elif re.search(r'\bколонн\w*\b', title, re.I):
            attrs['type'] = 'Колонный'
        elif re.search(r'\bкассетн\w*\b', title, re.I):
            attrs['type'] = 'Кассетный'
        else:
            attrs['type'] = 'Сплит-система'
        if re.search(r'\b(инвертор\w*|inverter|inv\b|dc[- ]inverter|[a-z]+-\d{2}hrfn|[a-z]+\d{2}hrfn)\b', title, re.I):
            attrs['inverter'] = 'Да'
        elif re.search(r'\b(on[- /]?off|неинвертор\w*|[a-z]+-\d{2}hrn|[a-z]+\d{2}hrn)\b', title, re.I):
            attrs['inverter'] = 'Нет'
        # Мощность BTU и площадь (включая модельные коды AR09, FM-09, MSAG-24, TCL-12, HWH09, ACS-09 и т.д.)
        m_btu = (
            re.search(r'\b(07|09|12|18|24|30|36)\b', title, re.I)
            or re.search(r'\b(7000|9000|12000|18000|24000)\s*(?:btu|бте)?\b', title, re.I)
            or re.search(r'\b(7|9|12|18|24)\s*k\b', title, re.I)
            or re.search(r'(?:[a-z]{2,6}[-/ ]?)(07|09|12|18|24)(?:[a-z0-9]|$)', title, re.I)
        )
        if m_btu:
            raw_btu = m_btu.group(1)
            if len(raw_btu) >= 4:
                raw_btu = str(int(raw_btu) // 1000)
            btu_map = {
                '7': ('07 (до 20 м²)', 'до 20 м²'),
                '07': ('07 (до 20 м²)', 'до 20 м²'),
                '9': ('09 (до 25 м²)', 'до 25 м²'),
                '09': ('09 (до 25 м²)', 'до 25 м²'),
                '12': ('12 (до 35 м²)', 'до 35 м²'),
                '18': ('18 (до 50 м²)', 'до 50 м²'),
                '24': ('24 (до 70 м²)', 'до 70 м²'),
                '30': ('30 (до 85 м²)', 'до 85 м²'),
                '36': ('36 (до 100 м²)', 'до 100 м²'),
            }
            if raw_btu in btu_map:
                attrs['power_btu'] = btu_map[raw_btu][0]
                attrs['area_sqm'] = btu_map[raw_btu][1]

    # 14. Смарт-часы и фитнес-браслеты
    is_sw = bool(re.search(
        r'\b(смарт[- ]час\w*|умные\s+часы|детские\s+часы|часы[- ]телефон|apple\s+watch|galaxy\s+watch|watch\s*(?:\d|se|ultra|fit|gt|pro)|'
        r'фитнес[- ]браслет\w*|smart\s+band|mi\s+band|amazfit|garmin\s+(?:fenix|forerunner|venu|instinct|epix|vivoactive)|'
        r'ремешок\s+для\s+(?:часов|apple\s+watch|galaxy\s+watch|mi\s+band|xiaomi|smart\s+watch))\b',
        title, re.I
    ))
    if is_sw:
        if re.search(r'\b(ремешок|браслет\s+для\s+часов|защитн\w*\s+(?:стекло|пленка|чехол|бампер)\s+для\s+(?:часов|apple\s+watch))\b', title, re.I):
            attrs['type'] = 'Ремешок / Аксессуар для часов'
        elif re.search(r'\b(фитнес[- ]браслет\w*|smart\s+band|mi\s+band|band\s+[789])\b', title, re.I):
            attrs['type'] = 'Фитнес-браслет'
        elif re.search(r'\b(детск\w*|baby\s+watch|kid\w*|aimoto|elari|geozon|jet\s+kid)\b', title, re.I):
            attrs['type'] = 'Детские умные часы'
        elif re.search(r'\b(garmin|suunto|coros|polar|amazfit\s+t-rex)\b', title, re.I):
            attrs['type'] = 'Спортивные / Туристические часы'
        else:
            attrs['type'] = 'Смарт-часы'

        m_cs = re.search(r'\b(38|40|41|42|44|45|46|47|49)\s*(?:mm|мм)\b', title, re.I)
        if m_cs:
            attrs['case_size'] = f"{m_cs.group(1)} мм"
        if re.search(r'\b(cellular|lte|esim|4g|sim)\b', title, re.I):
            attrs['cellular'] = 'Да'
        if re.search(r'\bamoled\b', title, re.I):
            attrs['screen_tech'] = 'AMOLED'
        elif re.search(r'\boled\b', title, re.I):
            attrs['screen_tech'] = 'OLED'
        elif re.search(r'\bips\b', title, re.I):
            attrs['screen_tech'] = 'IPS'

        if re.search(r'\b(силикон\w*|silicone|sport\s+band|fluoroelastomer)\b', title, re.I):
            attrs['strap_material'] = 'Силикон'
        elif re.search(r'\b(кожан\w*|leather)\b', title, re.I):
            attrs['strap_material'] = 'Кожа'
        elif re.search(r'\b(миланск\w*|milanese|металл\w*|стальн\w*|stainless|titanium\s+band)\b', title, re.I):
            attrs['strap_material'] = 'Металл / Миланская петля'
        elif re.search(r'\b(нейлон\w*|тканев\w*|nylon|sport\s+loop|trail\s+loop|alpine\s+loop)\b', title, re.I):
            attrs['strap_material'] = 'Нейлон / Ткань'

    # 15. Холодильники и морозильники
    is_fridge = bool(re.search(r'\b(холодильник\w*|морозильник\w*|морозильн\w*\s+(?:камер\w*|лар\w*)|винн\w*\s+шкаф\w*|refrigerator\w*|freezer\w*)\b', title, re.I))
    if is_fridge:
        if re.search(r'\bдля\s+косметики\b', title, re.I):
            attrs['type'] = 'Холодильник для косметики'
        elif re.search(r'\b(автомобильн\w*|автохолодильник\w*)\b', title, re.I):
            attrs['type'] = 'Автохолодильник'
        elif re.search(r'\bвинн\w*\s+шкаф\w*\b', title, re.I):
            attrs['type'] = 'Винный шкаф'
        elif re.search(r'\b(морозильн\w*\s+лар\w*)\b', title, re.I):
            attrs['type'] = 'Морозильный ларь'
        elif re.search(r'\b(морозильник\w*|морозильн\w*\s+камер\w*)\b', title, re.I):
            attrs['type'] = 'Морозильная камера'
        elif re.search(r'\b(встраиваем\w*|\bnrki\d+|\bbrb\d+|\bkiv\d+)\b', title, re.I):
            attrs['type'] = 'Встраиваемый холодильник'
        elif re.search(r'\b(side[- ]by[- ]side|сайд[- ]бай[- ]сайд|french\s+door|многодверн\w*)\b', title, re.I):
            attrs['type'] = 'Side-by-Side / Многодверный'
        elif re.search(r'\b(однокамерн\w*|мини[- ]холодильник\w*|компактный\s+холодильник\w*)\b', title, re.I):
            attrs['type'] = 'Однокамерный / Мини-холодильник'
        else:
            attrs['type'] = 'Двухкамерный холодильник'

        if re.search(r'\b(?:total\s+no\s+frost|full\s+no\s+frost|no\s*frost|ноу\s*фрост|nf\b|-nd\b|\bnrk\d+|\brb3\d+|\bga-b\d+|\bgn-b\d+|\bcef\d+)\b', title, re.I):
            attrs['refrigeration_defrost'] = 'No Frost'
        elif re.search(r'\b(?:low\s*frost|лоу\s*фрост)\b', title, re.I):
            attrs['refrigeration_defrost'] = 'Low Frost'
        elif re.search(r'\b(?:капельн\w*|статическ\w*|\bхм[- ]\d{4}(?!.*-nd))\b', title, re.I):
            attrs['refrigeration_defrost'] = 'Капельная'

        if re.search(r'\b(?:side[- ]by[- ]side|сайд[- ]бай[- ]сайд)\b', title, re.I):
            attrs['doors_type'] = 'Side-by-Side'
            attrs['freezer_location'] = 'Сбоку (Side-by-Side)'
        elif re.search(r'\b(?:french\s+door|многодверн\w*)\b', title, re.I):
            attrs['doors_type'] = 'Многодверный (French Door)'
        elif re.search(r'\b(?:однокамерн\w*|мини[- ]холодильник\w*|компактный\s+холодильник\w*)\b', title, re.I):
            attrs['doors_type'] = 'Однокамерный'
        elif re.search(r'\b(?:двухкамерн\w*|\bхм[- ]?\d{4}|\bxm[- ]?\d{4}|\bnrki?\d+|\brk\d+|\brb3\d+|\bga-b\d+|\bsj-)\b', title, re.I):
            attrs['doors_type'] = 'Двухкамерный'

        if 'freezer_location' not in attrs:
            if re.search(r'\b(?:морозильн\w*\s+камер\w*\s+снизу|нижн\w*\s+морозильн\w*)\b', title, re.I):
                attrs['freezer_location'] = 'Снизу'
            elif re.search(r'\b(?:морозильн\w*\s+камер\w*\s+сверху|верхн\w*\s+морозильн\w*)\b', title, re.I):
                attrs['freezer_location'] = 'Сверху'
            elif re.search(r'\bбез\s+морозильник\w*\b', title, re.I):
                attrs['freezer_location'] = 'Без морозильника'

        if re.search(r'\b(инвертор\w*|inverter|linear\s+inverter|smart\s+inverter)\b', title, re.I):
            attrs['compressor_type'] = 'Инверторный'

        m_h = re.search(r'\b(1[4-9]\d|20\d|210)\s*(?:см|cm)\b', title, re.I)
        if m_h:
            attrs['height_cm'] = f"{m_h.group(1)} см"

    # 16. Стиральные машины
    is_washer = bool(re.search(r'\b(стиральн\w*\s+машин\w*|стиралк\w*|washer)\b', title, re.I))
    if is_washer:
        m_load = re.search(r'\b(4|5|6|7|8|9|10|11|12)(?:[.,](\d))?\s*(?:кг|kg)\b', title, re.I)
        if m_load:
            dec = f".{m_load.group(2)}" if m_load.group(2) else ""
            attrs['load_capacity'] = f"{m_load.group(1)}{dec} кг"

        if re.search(r'\b(?:с\s+сушкой|сушильн\w*|стирально[- ]сушильн\w*|washer[- ]dryer)\b', title, re.I):
            attrs['drying'] = 'С сушкой'
        else:
            attrs['drying'] = 'Без сушки'

        m_spin = re.search(r'\b(800|1000|1200|1400|1600)\s*(?:об/?мин|rpm)\b', title, re.I)
        if m_spin:
            attrs['spin_speed'] = f"{m_spin.group(1)} об/мин"

        if re.search(r'\b(?:узк(?:ая|ие)|slim|super\s*slim)\b', title, re.I):
            attrs['depth_type'] = 'Узкая (до 45 см)'
        elif re.search(r'\bвстраиваем\w*\b', title, re.I):
            attrs['depth_type'] = 'Встраиваемая'
            attrs['installation_type'] = 'Встраиваемая'
        elif re.search(r'\bполноразмерн\w*\b', title, re.I):
            attrs['depth_type'] = 'Полноразмерная (от 45 см)'

        if re.search(r'\b(инвертор\w*|inverter|direct\s+drive|прямой\s+привод|экоинвертор\w*)\b', title, re.I):
            attrs['motor_type'] = 'Инверторный'

        if 'installation_type' not in attrs:
            if re.search(r'\bотдельностоящ\w*\b', title, re.I):
                attrs['installation_type'] = 'Отдельностоящая'

    # 17. Пылесосы
    is_vacuum = bool(re.search(r'\b(пылесос\w*|vacuum|roborock|dreame\s+bot|робот\s+для\s+мытья\s+(?:пола|окон)|паров\w*\s+швабр\w*|электрошвабр\w*)\b', title, re.I))
    if is_vacuum:
        if re.search(r'\b(для\s+пылесос\w*|сменн\w*\s+(?:фильтр|мешок|мешки|салфетк|щетк|насадк)|мешк\w*[- ]пылесборник\w*)\b', title, re.I):
            attrs['type'] = 'Аксессуары для пылесоса'
        elif re.search(r'\b(робот[- ]пылесос\w*|робот\s+для\s+мытья|робот[- ]мойщик|robot\s+vacuum|roborock|dreame\s+bot|x10\+|x20\+|s10\+|l10s|q\s*revo)\b', title, re.I):
            attrs['type'] = 'Робот-пылесос'
        elif re.search(r'\b(автомобильн\w*\s+пылесос|пылесос\s+автомобильн\w*)\b', title, re.I):
            attrs['type'] = 'Автомобильный пылесос'
        elif re.search(r'\b(паров\w*\s+швабр\w*|электрошвабр\w*)\b', title, re.I):
            attrs['type'] = 'Паровая / Электрошвабра'
        elif re.search(r'\b(строительн\w*|хозяйственн\w*|промышленн\w*|karcher\s+(?:wd|nt|kwd))\b', title, re.I):
            attrs['type'] = 'Строительный / Хозяйственный'
        elif re.search(r'\b(вертикальн\w*|ручн\w*|беспроводн\w*|аккумуляторн\w*|cordless|stick|dyson\s+v\d+|deerma\s+(?:dx|vc)|kitfort\s+кт-5\d{2,3}|dreame\s+(?:v|t|r|h)\d+)\b', title, re.I):
            attrs['type'] = 'Вертикальный беспроводной'
        elif re.search(r'\b(моющий\s+пылесос|с\s+аквафильтром|thomas\s+(?:twin|aqua|drybox))\b', title, re.I):
            attrs['type'] = 'Моющий'
        elif re.search(r'\b(циклон\w*|с\s+контейнером|без\s+мешка)\b', title, re.I):
            attrs['type'] = 'Обычный (циклон)'
        elif re.search(r'\b(с\s+мешком|мешков\w*)\b', title, re.I):
            attrs['type'] = 'Обычный (с мешком)'
        else:
            attrs['type'] = 'Классический напольный'

        if re.search(r'\b(?:сухая\s+и\s+влажная|влажн\w*|vacuum[- ]mop|mop\b|моющий|робот\s+для\s+мытья)\b', title, re.I):
            attrs['cleaning_type'] = 'Сухая и влажная'
        elif re.search(r'\bсухая\b', title, re.I) or attrs.get('type') in ('Обычный (циклон)', 'Обычный (с мешком)', 'Классический напольный'):
            attrs['cleaning_type'] = 'Только сухая'

        if re.search(r'\b(аквафильтр\w*|водный\s+фильтр\w*)\b', title, re.I):
            attrs['dust_collector_type'] = 'Аквафильтр'
        elif re.search(r'\b(контейнер\w*|циклон\w*|tw\d{4}|vc\d{2}k|sc\d{2}|vk\d{2}|vcea)\b', title, re.I):
            attrs['dust_collector_type'] = 'Контейнер (циклон)'
        elif re.search(r'\b(мешок|мешки|мешков\w*)\b', title, re.I):
            attrs['dust_collector_type'] = 'Мешок'

        m_pa = re.search(r'\b(3000|4000|5000|5300|6000|7000|8000|10000)\s*(?:па|pa)\b', title, re.I)
        if m_pa:
            attrs['suction_power'] = f"{m_pa.group(1)} Па"
        else:
            m_aw = re.search(r'\b(120|140|150|180|200|210|240)\s*(?:авт|aw)\b', title, re.I)
            if m_aw:
                attrs['suction_power'] = f"{m_aw.group(1)} аВт"

        m_w = re.search(r'\b(1400|1600|1800|2000|2200|2400)\s*(?:вт|w)\b', title, re.I)
        if m_w:
            attrs['power'] = f"{m_w.group(1)} Вт"

    # 18. Автомобильные шины
    is_not_tire = bool(re.search(r'\b(стиральн\w*|посудомоечн\w*|пильн\w*|для\s+пил\w*|бензопил\w*|электропил\w*)\b', title, re.I))
    is_tire = not is_not_tire and bool(re.search(
        r'\b(автошин\w*|автомобильн\w*\s+шин\w*|зимн\w*\s+шин\w*|летн\w*\s+шин\w*|всесезонн\w*\s+шин\w*|'
        r'шин\w*\s+(?:зимн\w*|летн\w*|всесезон\w*|шип\w*|липучк\w*)|'
        r'\d{3}[/]\d{2}\s*(?:r|zr)\d{2}\b)\b',
        title, re.I
    ))
    if is_tire:
        m_ts = re.search(r'\b(1[5-9]\d|2\d{2}|3[0-2]\d)[/](3[0-5]|4[0-5]|5[05]|6[05]|7[05]|8[05])\s*(?:r|р|zr)?\s*(1[2-9]|2[0-4])\b', title, re.I)
        if m_ts:
            w_val = m_ts.group(1)
            p_val = m_ts.group(2)
            d_val = m_ts.group(3)
            attrs['tire_width'] = f"{w_val} мм"
            attrs['tire_profile'] = f"{p_val}%"
            attrs['diameter'] = f"R{d_val}"
            attrs['tire_size'] = f"{w_val}/{p_val} R{d_val}"
        else:
            m_diam = re.search(r'(?i)\b(?:r|р)(1[2-9]|2[0-4])\b', title)
            if m_diam:
                attrs['diameter'] = f"R{m_diam.group(1)}"

        if re.search(r'\b(шип(?:ованн\w*|ы)?|studded)\b', title, re.I):
            attrs['season'] = 'Зимние шипованные'
        elif re.search(r'\b(липучк\w*|нешип\w*|фрикционн\w*|friction|без\s+шипов)\b', title, re.I):
            attrs['season'] = 'Зимние нешипованные (липучка)'
        elif re.search(r'\b(всесезон\w*|all[- ]season|4[- ]season|m\+s)\b', title, re.I):
            attrs['season'] = 'Всесезонные'
        elif re.search(r'\b(летн\w*|summer)\b', title, re.I):
            attrs['season'] = 'Летние'
        elif re.search(r'\b(зимн\w*|winter)\b', title, re.I):
            attrs['season'] = 'Зимние'

    # 19. Клавиатуры, мыши и периферия
    is_not_periph_device = bool(re.search(r'^\s*(?:(?:\d+(?:\.\d+)?\s*"?\s*)?планшет|ноутбук|моноблок|смартфон)\b', title, re.I))
    is_periph = not is_not_periph_device and bool(re.search(
        r'\b(клавиатур\w*|мышь|мышк\w*|комплект\s+клавиатур\w*|коврик\s+для\s+мыш\w*|игров\w*\s+поверхност\w*|графическ\w*\s+планшет\w*|геймпад\w*|джойстик\w*|руль\s+игров\w*)\b',
        title, re.I
    ))
    if is_periph:
        if re.search(r'\b(комплект|набор)\b.*\b(клавиатур\w*|мышь|мышк\w*)\b|\bклавиатур\w*\s*\+\s*мышь\b', title, re.I):
            attrs['type'] = 'Комплект (клавиатура + мышь)'
        elif re.search(r'\b(коврик\s+для\s+мыш\w*|игров\w*\s+поверхност\w*|mousepad|mouse\s+pad)\b', title, re.I):
            attrs['type'] = 'Коврик для мыши'
        elif re.search(r'\b(графическ\w*\s+планшет\w*)\b', title, re.I):
            attrs['type'] = 'Графический планшет'
        elif re.search(r'\b(геймпад\w*|джойстик\w*|gamepad|dualshock|dualsense)\b', title, re.I):
            attrs['type'] = 'Геймпад / Джойстик'
        elif re.search(r'\b(клавиатур\w*|keyboard)\b', title, re.I):
            attrs['type'] = 'Клавиатура'
        elif re.search(r'\b(мышь|мышк\w*|mouse)\b', title, re.I):
            attrs['type'] = 'Мышь'

        if re.search(r'\b(беспроводн\w*|wireless|bluetooth|bt\b|радиоканал|2\.4\s*ghz|lightspeed)\b', title, re.I):
            attrs['connection_type'] = 'Беспроводное'
        elif re.search(r'\b(проводн\w*|wired|usb)\b', title, re.I):
            attrs['connection_type'] = 'Проводное'

        if re.search(r'\b(механическ\w*|mechanical|cherry\s+mx|gateron|kailh|outemu|red\s+switch|blue\s+switch|brown\s+switch)\b', title, re.I):
            attrs['keyboard_type'] = 'Механическая'
        elif re.search(r'\b(мембранн\w*|membrane)\b', title, re.I):
            attrs['keyboard_type'] = 'Мембранная'
        elif re.search(r'\b(оптико[- ]механическ\w*|optical\s+switch)\b', title, re.I):
            attrs['keyboard_type'] = 'Оптико-механическая'

        m_dpi = re.search(r'\b(800|1000|1200|1600|2400|3200|4000|6400|8000|10000|12000|16000|18000|20000|25600|26000|30000)\s*(?:dpi|cpi)\b', title, re.I)
        if m_dpi:
            attrs['sensor_dpi'] = f"{m_dpi.group(1)} DPI"

    # 20. Техника для кухни
    is_not_kitchen = bool(re.search(
        r'\b(детск\w*\s+игрушк\w*|игрушк\w*|игрушечн\w*|для\s+(?:кофемашин\w*|кофеварк\w*|блендер\w*|мясорубк\w*|мультиварк\w*|чайник\w*|микроволнов\w*|тостер\w*)|'
        r'кофе\s+(?:в\s+зернах|зерновой|молотый|капсульный|растворимый)|капсул\w*\s+для|таблетк\w*\s+для|средств\w*\s+для|фильтр\w*\s+для|чистящ\w*|декальцинац\w*)\b',
        title, re.I
    ))
    is_kitchen = not is_not_kitchen and bool(re.search(
        r'\b(кофемашин\w*|кофеварк\w*|кофемолк\w*|чайник\w*|электрочайник\w*|термопот\w*|'
        r'микроволнов\w*|свч[- ]печ\w*|блендер\w*|миксер\w*|мясорубк\w*|комбайн\w*|'
        r'мультиварк\w*|скороварк\w*|пароварк\w*|аэрогрил\w*|электрогрил\w*|грил\w*\s+электрическ\w*|'
        r'тостер\w*|бутербродниц\w*|вафельниц\w*|соковыжималк\w*|хлебопеч\w*|сушилк\w*\s+для\s+овощ\w*|'
        r'дегидратор\w*|фритюрниц\w*|вакууматор\w*|льдогенератор\w*|электроплит\w*|варочн\w*\s+панел\w*|духов\w*\s+шкаф\w*|вытяжк\w*)\b',
        title, re.I
    ))
    if is_kitchen:
        if re.search(r'\bкофемашин\w*\b', title, re.I):
            attrs['type'] = 'Кофемашина'
        elif re.search(r'\bкофеварк\w*\b', title, re.I):
            attrs['type'] = 'Кофеварка'
        elif re.search(r'\bкофемолк\w*\b', title, re.I):
            attrs['type'] = 'Кофемолка'
        elif re.search(r'\b(термопот\w*)\b', title, re.I):
            attrs['type'] = 'Термопот'
        elif re.search(r'\b(электрочайник\w*|чайник\w*\s+электрическ\w*|чайник\w*)\b', title, re.I):
            attrs['type'] = 'Электрочайник'
        elif re.search(r'\b(микроволнов\w*|свч)\b', title, re.I):
            attrs['type'] = 'Микроволновая печь'
        elif re.search(r'\bблендер\w*\b', title, re.I):
            attrs['type'] = 'Блендер'
        elif re.search(r'\b(планетарн\w*\s+миксер\w*|миксер\w*)\b', title, re.I):
            attrs['type'] = 'Миксер'
        elif re.search(r'\bмясорубк\w*\b', title, re.I):
            attrs['type'] = 'Мясорубка'
        elif re.search(r'\b(кухонн\w*\s+комбайн\w*|кухонн\w*\s+машин\w*)\b', title, re.I):
            attrs['type'] = 'Кухонный комбайн'
        elif re.search(r'\b(мультиварк\w*|скороварк\w*)\b', title, re.I):
            attrs['type'] = 'Мультиварка'
        elif re.search(r'\b(аэрогрил\w*|фритюрниц\w*)\b', title, re.I):
            attrs['type'] = 'Аэрогриль / Фритюрница'
        elif re.search(r'\b(электрогрил\w*|грил\w*)\b', title, re.I):
            attrs['type'] = 'Электрогриль'
        elif re.search(r'\bтостер\w*\b', title, re.I):
            attrs['type'] = 'Тостер'
        elif re.search(r'\bсоковыжималк\w*\b', title, re.I):
            attrs['type'] = 'Соковыжималка'
        elif re.search(r'\bхлебопеч\w*\b', title, re.I):
            attrs['type'] = 'Хлебопечь'
        elif re.search(r'\b(варочн\w*\s+панел\w*|варочн\w*\s+поверхност\w*)\b', title, re.I):
            attrs['type'] = 'Варочная панель'
        elif re.search(r'\bдухов\w*\s+шкаф\w*\b', title, re.I):
            attrs['type'] = 'Духовой шкаф'
        elif re.search(r'\bвытяжк\w*\b', title, re.I):
            attrs['type'] = 'Вытяжка'
        elif re.search(r'\bвакууматор\w*\b', title, re.I):
            attrs['type'] = 'Вакууматор'
        elif re.search(r'\bльдогенератор\w*\b', title, re.I):
            attrs['type'] = 'Льдогенератор'

        m_kw = re.search(r'\b([1-9]\d{2}|[12]\d{3}|3000)\s*(?:вт|w)\b', title, re.I)
        if m_kw:
            attrs['power'] = f"{m_kw.group(1)} Вт"

        m_kl = re.search(r'\b([0-9]+(?:[.,][0-9]+)?)\s*(?:л|l|литр\w*)\b', title, re.I)
        if m_kl:
            v_num = float(m_kl.group(1).replace(',', '.'))
            if 0.3 <= v_num <= 70:
                num_str = str(v_num).rstrip('0').rstrip('.') if '.' in str(v_num) else str(int(v_num))
                attrs['capacity'] = f"{num_str} л"

    # 21. Сетевое оборудование
    is_net = bool(re.search(
        r'\b(маршрутизатор\w*|роутер\w*|router|коммутатор\w*|свитч|switch|точк\w*\s+доступ\w*|access\s+point|'
        r'mesh[- ]систем\w*|репитер\w*|ретранслятор\w*|усилител\w*\s+(?:беспроводн\w*\s+)?сигнал\w*|'
        r'сетев\w*\s+адаптер\w*|wi[- ]?fi\s+адаптер\w*|модем\w*|poe[- ]инжектор\w*|медиаконвертер\w*)\b',
        title, re.I
    )) and not bool(re.search(r'\b(nintendo\s+switch|переключател\w*\s+для\s+клавиатур)\b', title, re.I))
    if is_net:
        if re.search(r'\b(mesh[- ]систем\w*|deco\s+[a-z0-9]+|zenwifi|orbi|halo\s+h)\b', title, re.I):
            attrs['type'] = 'Mesh-система'
        elif re.search(r'\b(маршрутизатор\w*|роутер\w*|router|archer|keenetic|mikrotik\s+hap|mikrotik\s+rb)\b', title, re.I):
            attrs['type'] = 'Wi-Fi роутер'
        elif re.search(r'\b(коммутатор\w*|свитч|switch)\b', title, re.I):
            attrs['type'] = 'Коммутатор (Switch)'
        elif re.search(r'\b(точк\w*\s+доступ\w*|access\s+point|unifi|omada\s+eap|cap\s+ac|wap\s+ac)\b', title, re.I):
            attrs['type'] = 'Точка доступа'
        elif re.search(r'\b(репитер\w*|ретранслятор\w*|усилител\w*\s+(?:беспроводн\w*\s+)?сигнал\w*|extender|repeater)\b', title, re.I):
            attrs['type'] = 'Усилитель сигнала (Репитер)'
        elif re.search(r'\b(wi[- ]?fi\s+адаптер\w*|сетев\w*\s+адаптер\w*|сетев\w*\s+карт\w*)\b', title, re.I):
            attrs['type'] = 'Сетевой / Wi-Fi адаптер'
        elif re.search(r'\b(модем\w*|4g\s+роутер|lte\s+роутер)\b', title, re.I):
            attrs['type'] = '4G/5G модем'

        if re.search(r'\b(wi[- ]?fi\s*7|802\.11be|\bbe\d{3,5}\b)\b', title, re.I):
            attrs['wifi_standard'] = 'Wi-Fi 7 (802.11be)'
        elif re.search(r'\b(wi[- ]?fi\s*6e)\b', title, re.I):
            attrs['wifi_standard'] = 'Wi-Fi 6E'
        elif re.search(r'\b(wi[- ]?fi\s*6|802\.11ax|\bax\d{2,4}\b|ax1500|ax1800|ax3000|ax5400|ax6000)\b', title, re.I):
            attrs['wifi_standard'] = 'Wi-Fi 6 (802.11ax)'
        elif re.search(r'\b(wi[- ]?fi\s*5|802\.11ac|\bac\d{3,4}\b|ac750|ac1200|ac1300|ac1750|ac1900|ac2600)\b', title, re.I):
            attrs['wifi_standard'] = 'Wi-Fi 5 (802.11ac)'
        elif re.search(r'\b(wi[- ]?fi\s*4|802\.11n|\bn300\b|\bn150\b|\bn450\b|300\s*мбит)\b', title, re.I):
            attrs['wifi_standard'] = 'Wi-Fi 4 (802.11n)'

        m_ports = re.search(r'\b(4|5|8|10|16|24|28|48|52)[-\s]*порт\w*\b|\b(4|5|8|16|24|48)\s*x\s*(?:100|1000|ge|fe|rj45|lan|poe)\b|\b(?:tl-sf|tl-sg|ls|des-|dgs-)(?:10)?(05|08|16|24|48)\b', title, re.I)
        if m_ports:
            p_raw = m_ports.group(1) or m_ports.group(2) or m_ports.group(3)
            norm_p = normalize_attribute_value('ports_count', str(int(p_raw)))
            if norm_p:
                attrs['ports_count'] = norm_p

        if re.search(r'\b(10g|10\s*гбит|10\s*gbps|sfp\+)\b', title, re.I):
            attrs['lan_speed'] = '10 Гбит/с'
        elif re.search(r'\b(2\.5g|2\.5\s*гбит|2\.5\s*gbps)\b', title, re.I):
            attrs['lan_speed'] = '2.5 Гбит/с'
        elif re.search(r'\b(гигабит\w*|gigabit|1000\s*мбит|1000\s*mbps|10/100/1000|gbe|tl-sg|dgs-|ls100[58]g)\b', title, re.I):
            attrs['lan_speed'] = '1 Гбит/с'
        elif re.search(r'\b(100\s*мбит|100\s*mbps|10/100\b|fast\s+ethernet|tl-sf|des-|ls100[58](?!g))\b', title, re.I):
            attrs['lan_speed'] = '100 Мбит/с'

        if re.search(r'\b(poe\+?|с\s+poe)\b', title, re.I):
            attrs['poe'] = 'С поддержкой PoE'

    # 22. Принтеры, МФУ и плоттеры
    is_printer = not bool(re.search(r'\b(бумага\s+для|картридж|тонер|чернила|фотобарабан|термопленк)\b', title, re.I)) and bool(re.search(
        r'\b(принтер\w*|мфу|многофункциональн\w*\s+устройств\w*|плоттер\w*|сканер\w*)\b',
        title, re.I
    ))
    if is_printer:
        if re.search(r'\b(мфу|многофункциональн\w*\s+устройств\w*|3\s*в\s*1|3-in-1|laserjet\s+mfp|pixma\s+g[234]\d{2}|ecotank\s+l[3456]\d{3})\b', title, re.I):
            attrs['type'] = 'МФУ (принтер-сканер-копир)'
        elif re.search(r'\b(плоттер\w*|широкоформатн\w*\s+принтер\w*|designjet|imageprograf|surecolor)\b', title, re.I):
            attrs['type'] = 'Плоттер'
        elif re.search(r'\b(принтер\w*\s+(?:чеков|этикеток)|термопринтер\w*|термотрансферн\w*)\b', title, re.I):
            attrs['type'] = 'Принтер этикеток / чеков'
        elif re.search(r'\b(сканер\w*)\b', title, re.I) and not re.search(r'\b(принтер\w*|мфу)\b', title, re.I):
            attrs['type'] = 'Сканер'
        elif re.search(r'\bпринтер\w*\b', title, re.I):
            attrs['type'] = 'Принтер'

        if re.search(r'\b(снпч|ecotank|ink\s+tank|smart\s+tank|pixma\s+g\d{3,4}|l32\d{2}|l12\d{2}|l8050)\b', title, re.I):
            attrs['print_tech'] = 'Струйная с СНПЧ'
        elif re.search(r'\b(лазерн\w*|laser|laserjet|i-sensys|pantum|phaser|ecosys|dcp-l|hl-l|mfc-l)\b', title, re.I):
            attrs['print_tech'] = 'Лазерная'
        elif re.search(r'\b(струйн\w*|inkjet|pixma|deskjet|officejet)\b', title, re.I):
            attrs['print_tech'] = 'Струйная'
        elif re.search(r'\b(термопринтер\w*|термотрансферн\w*|термопечат\w*)\b', title, re.I):
            attrs['print_tech'] = 'Термопечать'

        if re.search(r'\b(цветн\w*|color|colour|pixma|ecotank|smart\s+tank|l32\d{2}|l8050|clx-|clp-|m255|m183|m283|mf65\d)\b', title, re.I):
            attrs['print_color'] = 'Цветная'
        elif re.search(r'\b(черно[- ]бел\w*|ч/б|монохромн\w*|mono|m111|m141|m211|m236|p2500|m6500|mf3010|lbp6030|hl-l23|dcp-l25)\b', title, re.I):
            attrs['print_color'] = 'Черно-белая'

        if re.search(r'\b(a3\+?|а3\+?)\b', title, re.I):
            attrs['paper_format'] = 'A3'
        elif re.search(r'\b(a1|а1|a0|а0|24"|36"|44")\b', title, re.I):
            attrs['paper_format'] = 'Широкоформатный (A1/A0)'
        elif re.search(r'\b(a4|а4)\b', title, re.I):
            attrs['paper_format'] = 'A4'

        if re.search(r'\b(wi[- ]?fi|wireless|w\b)\b', title, re.I):
            attrs['connectivity'] = 'Wi-Fi'
        elif re.search(r'\b(ethernet|lan|сетев\w*)\b', title, re.I):
            attrs['connectivity'] = 'Ethernet (LAN)'

    # 23. Игровые приставки, VR и аксессуары
    is_console_item = not is_hp and bool(re.search(
        r'\b(игров\w*\s+приставк\w*|приставк\w*\s+игров\w*|игров\w*\s+консол\w*|консол\w*\s+игров\w*|'
        r'playstation\s*[45]|ps\s*[45]|xbox\s+series|nintendo\s+switch|steam\s+deck|legion\s+go|rog\s+ally|'
        r'meta\s+quest|очки\s+виртуальной\s+реальности|dualsense|dualshock|'
        r'dendy|денди|\bsega\b|сега|сборник\s+(?:\d+\s*)?игр|игр\w*\s+для\s+(?:ps[45]|playstation|xbox|nintendo|sega|dendy|денди)|'
        r'игров\w*\s+рул\w*|рул\w*\s+игров\w*|педал\w*\s+moza|база\s+рулевая\s+moza|moza\s+racing)\b',
        title, re.I
    ))
    if is_console_item:
        if re.search(r'\b(meta\s+quest|очки\s+виртуальной\s+реальности|ps\s*vr2?|шлем\s+виртуальной)\b', title, re.I):
            attrs['type'] = 'VR-гарнитура'
        elif re.search(r'\b(игров\w*\s+рул\w*|рул\w*\s+игров\w*|педал\w*\s+moza|база\s+рулевая\s+moza|moza\s+racing)\b', title, re.I):
            attrs['type'] = 'Игровой руль / Кокпит'
        elif re.search(r'\b(геймпад\w*|джойстик\w*|контроллер\w*|dualsense|dualshock|joy-con)\b', title, re.I) and not re.search(r'\b(приставк\w*|консол\w*)\b', title, re.I):
            attrs['type'] = 'Геймпад / Контроллер'
        elif re.search(r'\b(игра\s+для|диск\s+с\s+игрой|сборник\s+(?:\d+\s*)?игр|картридж\s+.*(?:sega|dendy|денди)|\bдля\s+ps[45]\b|\bдля\s+nintendo\s+switch\b|\bдля\s+(?:sega|dendy|денди)\b)\b', title, re.I) and not re.search(r'\b(приставк\w*|консол\w*|геймпад\w*|джойстик\w*|зарядн\w*|чехол)\b', title, re.I):
            attrs['type'] = 'Игра / Картридж'
        elif re.search(r'\b(dendy|денди|\bsega\b|сега|ретро[- ]приставк\w*|8[- ]?bit|16[- ]?bit)\b', title, re.I):
            attrs['type'] = 'Ретро-приставка (8/16-bit)'
        elif re.search(r'\b(nintendo\s+switch|steam\s+deck|legion\s+go|rog\s+ally|playstation\s+portal)\b', title, re.I):
            attrs['type'] = 'Портативная консоль'
        elif re.search(r'\b(игров\w*\s+приставк\w*|приставк\w*\s+игров\w*|игров\w*\s+консол\w*|playstation\s*[45]|ps\s*[45]|xbox\s+series)\b', title, re.I):
            attrs['type'] = 'Стационарная консоль'

        if re.search(r'\b(ps\s*5|playstation\s*5|dualsense)\b', title, re.I):
            attrs['platform'] = 'PlayStation 5'
        elif re.search(r'\b(ps\s*4|playstation\s*4|dualshock\s*4)\b', title, re.I):
            attrs['platform'] = 'PlayStation 4'
        elif re.search(r'\b(xbox\s+series|xbox\s+one|xbox)\b', title, re.I):
            attrs['platform'] = 'Xbox Series X/S'
        elif re.search(r'\b(nintendo\s+switch|switch\s+oled|switch\s+lite)\b', title, re.I):
            attrs['platform'] = 'Nintendo Switch'
        elif re.search(r'\b(steam\s+deck|legion\s+go|rog\s+ally)\b', title, re.I):
            attrs['platform'] = 'Steam Deck / Портативные ПК'
        elif re.search(r'\b(meta\s+quest|oculus)\b', title, re.I):
            attrs['platform'] = 'Meta Quest / VR'
        elif re.search(r'\b(dendy|денди|\bsega\b|сега)\b', title, re.I):
            attrs['platform'] = 'Sega / Dendy (Ретро)'

        if re.search(r'\b(digital\s+edition|без\s+дисковода)\b', title, re.I):
            attrs['disc_drive'] = 'Digital Edition (без дисковода)'
        elif re.search(r'\b(с\s+дисководом|blu-?ray)\b', title, re.I):
            attrs['disc_drive'] = 'С дисководом (Blu-ray)'

        m_c_st = re.search(r'\b(64|128|256|512|825)\s*(?:gb|гб)\b|\b(1|2)\s*(?:tb|тб)\b', title, re.I)
        if m_c_st and 'storage' not in attrs:
            norm_cst = normalize_attribute_value('storage', m_c_st.group(0))
            if norm_cst:
                attrs['storage'] = norm_cst

    # 24. Кабели, зарядные устройства и внешние аккумуляторы (Power Bank)
    is_cable_charger = bool(re.search(
        r'\b(мобильн\w*\s+аккумулятор\w*|внешн\w*\s+аккумулятор\w*|аккумулятор\w*\s+внешн\w*|power\s*bank|повербанк\w*|'
        r'портативн\w*\s+электростанц\w*|power\s+station|солнечн\w*\s+панел\w*|'
        r'набор\s+батареек|батарейк\w*\s+(?:литиевая|алкалиновая|щелочная|солевая|aa|aaa|cr\d+|lr\d+|крона)|аккумулятор\w*\s+(?:aa|aaa|18650|крона)|'
        r'сетев\w*\s+зарядн\w*|беспроводн\w*\s+зарядн\w*|автомобильн\w*\s+зарядн\w*|зарядн\w*\s+устройств\w*|'
        r'кабел\w*\s+(?:usb|type-c|lightning|hdmi|displayport|aux|интерфейсн\w*|для\s+|питания|vga|utp|сетев\w*|аудио|видео)|'
        r'(?:kari|ogisana|baseus|ugreen|vention|hoco|borofone|remax|depat|ship|continent)\s+кабел\w*|'
        r'patch\s*cord|патч[- ]?корд\w*|переходник\w*|адаптер\w*\s+type-c|разветвител\w*|usb[- ]хаб|usb\s+hub)\b',
        title, re.I
    )) and not re.search(r'\b(для\s+инструмент\w*|makita|greenworks|bosch|dewalt)\b', title, re.I)
    if is_cable_charger:
        if re.search(r'\b(портативн\w*\s+электростанц\w*|power\s+station|солнечн\w*\s+панел\w*)\b', title, re.I):
            attrs['type'] = 'Портативная электростанция / Панель'
        elif re.search(r'\b(набор\s+батареек|батарейк\w*|аккумулятор\w*\s+(?:aa|aaa|18650|крона))\b', title, re.I):
            attrs['type'] = 'Батарейки и элементы питания'
        elif re.search(r'\b(мобильн\w*\s+аккумулятор\w*|внешн\w*\s+аккумулятор\w*|аккумулятор\w*\s+внешн\w*|power\s*bank|повербанк\w*)\b', title, re.I):
            attrs['type'] = 'Внешний аккумулятор (Power Bank)'
        elif re.search(r'\b(беспроводн\w*\s+зарядн\w*|magsafe\s+charger)\b', title, re.I):
            attrs['type'] = 'Беспроводное зарядное устройство'
        elif re.search(r'\b(автомобильн\w*\s+зарядн\w*|устройств\w*\s+зарядн\w*\s+автомобильн\w*|азу\b)\b', title, re.I):
            attrs['type'] = 'Автомобильное зарядное устройство'
        elif re.search(r'\b(сетев\w*\s+зарядн\w*|зарядн\w*\s+устройств\w*|сзу\b)\b', title, re.I):
            attrs['type'] = 'Сетевое зарядное устройство'
        elif re.search(r'\b(переходник\w*|разветвител\w*|usb[- ]хаб|usb\s+hub|док[- ]станц\w*)\b', title, re.I):
            attrs['type'] = 'Переходник / Концентратор (Hub)'
        elif re.search(r'\b(patch\s*cord|патч[- ]?корд\w*|кабель\s+utp)\b', title, re.I):
            attrs['type'] = 'Патч-корд / Сетевой кабель'
        elif re.search(r'\bкабел\w*\b', title, re.I):
            attrs['type'] = 'Кабель'

        m_pb_mah = re.search(r'\b(5000|10000|15000|20000|25000|30000|40000|50000|60000|80000|100000)\s*(?:mah|мач|мАч)?\b', title, re.I)
        if m_pb_mah:
            attrs['battery'] = f"{m_pb_mah.group(1)} мАч"

        m_chg_w = re.search(r'\b(15|18|20|22\.5|25|27|30|33|35|40|45|60|65|67|87|96|100|120|140|240|300|500|600|1000|1200|2000)\s*(?:w|вт)\b', title, re.I)
        if m_chg_w:
            attrs['power'] = f"{m_chg_w.group(1)} Вт"

        if re.search(r'\b(lightning|8[- ]?pin)\b', title, re.I):
            attrs['connector'] = 'Lightning'
        elif re.search(r'\b(type[- ]?c|usb[- ]?c)\b', title, re.I):
            attrs['connector'] = 'USB Type-C'
        elif re.search(r'\bmicro[- ]?usb\b', title, re.I):
            attrs['connector'] = 'Micro-USB'
        elif re.search(r'\bhdmi\b', title, re.I):
            attrs['connector'] = 'HDMI'
        elif re.search(r'\bdisplayport\b', title, re.I):
            attrs['connector'] = 'DisplayPort'
        elif re.search(r'\b(rj-?45|utp|patch\s*cord|патч[- ]?корд)\b', title, re.I):
            attrs['connector'] = 'RJ-45 (LAN)'
        elif re.search(r'\bvga\b', title, re.I):
            attrs['connector'] = 'VGA'
        elif re.search(r'\b(aux|3\.5\s*мм)\b', title, re.I):
            attrs['connector'] = '3.5 мм (AUX)'

    # 25. ИБП, стабилизаторы и сетевые фильтры
    is_ups_power = bool(re.search(
        r'\b(\bups\b|ибп|источник\w*\s+бесперебойного\s+питания|бесперебойник\w*|стабилизатор\w*\s+напряжения|сетев\w*\s+фильтр\w*)\b',
        title, re.I
    ))
    if is_ups_power:
        if re.search(r'\b(стабилизатор\w*)\b', title, re.I):
            attrs['type'] = 'Стабилизатор напряжения'
        elif re.search(r'\b(сетев\w*\s+фильтр\w*|удлинител\w*)\b', title, re.I):
            attrs['type'] = 'Сетевой фильтр / Удлинитель'
        elif re.search(r'\b(аккумулятор\w*\s+для\s+ибп|батаре\w*\s+для\s+ибп)\b', title, re.I):
            attrs['type'] = 'Аккумулятор для ИБП'
        else:
            attrs['type'] = 'ИБП (UPS)'

        m_va = (
            re.search(r'\b(400|500|600|650|700|750|800|850|900|1000|1100|1200|1500|1600|2000|2200|3000)\s*(?:va|ва)\b', title, re.I)
            or re.search(r'\b(?:v|bx|bv|ut|sp|ert|sl)[- ]?(500|600|650|700|750|800|850|1000|1200|1500|2000|3000)\b', title, re.I)
        )
        if m_va:
            attrs['power_va'] = f"{m_va.group(1)} ВА"

        m_ups_w = re.search(r'\b(240|300|360|390|400|480|500|600|700|720|800|900|1000|1200|1500|1800|2000|2400|2700)\s*(?:w|вт)\b', title, re.I)
        if m_ups_w:
            attrs['power'] = f"{m_ups_w.group(1)} Вт"

    # 26. Обувь и Одежда
    is_footwear = bool(re.search(
        r'\b(кроссовк\w*|кеды|ботинк\w*|полуботинк\w*|сапог\w*|полусапог\w*|ботильон\w*|бoтильоны|челси|туфл\w*|лофер\w*|'
        r'сандали\w*|босоножк\w*|слипон\w*|слингбэк\w*|балетк\w*|мокасин\w*|тапочк\w*|шлепанц\w*|сабо|дутик\w*|угги|бутсы|эспадриль\w*)\b',
        title, re.I
    ))
    is_apparel = bool(re.search(
        r'\b(футболк\w*|фуфайк\w*|поло\b|худи|свитшот\w*|лонгслив\w*|толстовк\w*|олимпийк\w*|пуховик\w*|куртк\w*|ветровк\w*|пальто|полупальто|плащ\w*|тренч\w*|парк[аи]\b|анорак\w*|бомбер\w*|жилет\w*|пухов\w*\s+жилет|'
        r'брюк\w*|джинс\w*|штан\w*|легинс\w*|леггинс\w*|джоггер\w*|тайтс\w*|бридж\w*|капри|комбинезон\w*|полукомбинезон\w*|'
        r'плать\w*|сарафан\w*|юбк\w*|рубашк\w*|сорочк\w*|блузк\w*|блуз[аы]\b|туник\w*|свитер\w*|джемпер\w*|пуловер\w*|кардиган\w*|кофт\w*|водолазк\w*|пиджак\w*|жакет\w*|блейзер\w*|'
        r'шорты|костюм\s+(?:спортивн\w*|флисов\w*|трикотажн\w*|прогулочн\w*|детск\w*|для\s+)|спортивн\w*\s+костюм\w*|комплект\s+одежды|пижам\w*|халат\w*|боди\b|ползунк\w*|распашонк\w*|чепчик\w*|'
        r'плавки|купальник\w*|термобель\w*|кальсон\w*|носки|гольфы|колготк\w*|набор\s+носков|трусы\s+(?:мужск|женск|детск|боксеры|слипы)|бюстгальтер\w*|топ\s+(?:спортивн|женск|для\s+девоч)|майк\w*\s+(?:мужск|женск|детск|спортивн|бельев)|'
        r'шарф\w*|палантин\w*|снуд\w*|шапк\w*|балаклав\w*|бейсболк\w*|кепк\w*|панам\w*|берет\w*|перчатк\w*(?!\s+(?:хозяйственн|нитрилов|латексн|резинов|рабоч|строительн|садов))|варежк\w*|рукавиц\w*|краги|'
        r'ремень|ремни|подтяжк\w*|галстук\w*|бабочка\s+галстук|кошел[её]к\w*|портмоне|визитниц\w*|картхолдер\w*|ключниц\w*|обложк\w*\s+для\s+(?:паспорта|автодокументов)|зонт\w*(?!\s+пляжн|\s+садов)|'
        r'сумк\w*(?!\s+(?:для\s+(?:ноутбук|планшет|камер|фото|инструмент|коляск|велосипед|обуви|сменной|мам)|холодильник|термо))|хобо\b|шоппер\w*|тоут\b|кросс[- ]боди|клатч\w*|багет\b|сэтчел\b|мессенджер\b|барсетк\w*|несессер\w*|поясн\w*\s+сумк\w*|'
        r'рюкзак\w*(?!\s+(?:для\s+ноутбук|туристическ|походн|тактическ|кенгуру|переноск))|ранец\w*|'
        r'очки\s+солнцезащитн\w*|солнцезащитн\w*\s+очки|оправ\w*\s+для\s+очков|футляр\s+для\s+очков|'
        r'ювелирн\w*\s+(?:кольц\w*|цепочк\w*|серьг\w*|подвеск\w*|браслет\w*|колье)|кольцо\s+(?:золот\w*|серебр\w*)|серьги\s+(?:золот\w*|серебр\w*|бижутер)|браслет\s+(?:золот\w*|серебр\w*|кожан\w*|бижутер)|цепочк\w*\s+(?:золот\w*|серебр\w*)|подвеск\w*\s+(?:золот\w*|серебр\w*)|брошь|бижутери\w*|'
        r'резинк\w*\s+для\s+волос|заколк\w*|краб\s+для\s+волос|ободок\s+для\s+волос|повязк\w*\s+на\s+голову|набор\s+для\s+волос|'
        r'губк\w*\s+для\s+обуви|крем\s+для\s+обуви|воск\s+для\s+обуви|краска\s+для\s+обуви|пропитк\w*\s+для\s+обуви|дезодорант\s+для\s+обуви|стельк\w*\s+для\s+обуви|полустельк\w*|шнурк\w*\s+для\s+обуви|ложк\w*\s+для\s+обуви|рожок\s+для\s+обуви)\b',
        title, re.I
    ))
    if is_footwear or is_apparel:
        if is_footwear:
            if re.search(r'\bкроссовк\w*\b', title, re.I):
                attrs['type'] = 'Кроссовки'
            elif re.search(r'\bкеды\b', title, re.I):
                attrs['type'] = 'Кеды'
            elif re.search(r'\bполуботинк\w*\b', title, re.I):
                attrs['type'] = 'Полуботинки'
            elif re.search(r'\b(ботильон\w*|бoтильоны)\b', title, re.I):
                attrs['type'] = 'Ботильоны'
            elif re.search(r'\bчелси\b', title, re.I):
                attrs['type'] = 'Челси'
            elif re.search(r'\bботинк\w*\b', title, re.I):
                attrs['type'] = 'Ботинки'
            elif re.search(r'\b(дутик\w*|угги)\b', title, re.I):
                attrs['type'] = 'Дутики / Угги'
            elif re.search(r'\bполусапог\w*\b', title, re.I):
                attrs['type'] = 'Полусапоги'
            elif re.search(r'\bсапог\w*\b', title, re.I):
                attrs['type'] = 'Сапоги'
            elif re.search(r'\b(туфл\w*|слингбэк\w*)\b', title, re.I):
                attrs['type'] = 'Туфли'
            elif re.search(r'\bлофер\w*\b', title, re.I):
                attrs['type'] = 'Лоферы'
            elif re.search(r'\b(сандали\w*|босоножк\w*)\b', title, re.I):
                attrs['type'] = 'Сандалии и босоножки'
            elif re.search(r'\b(слипон\w*|мокасин\w*|балетк\w*|эспадриль\w*)\b', title, re.I):
                attrs['type'] = 'Слипоны и балетки'
            elif re.search(r'\b(тапочк\w*|шлепанц\w*|сабо|бутсы)\b', title, re.I):
                attrs['type'] = 'Тапочки и сабо'
        elif is_apparel:
            if re.search(r'\b(сумк\w*|хобо\b|шоппер\w*|тоут\b|кросс[- ]боди|клатч\w*|багет\b|сэтчел\b|мессенджер\b|барсетк\w*|несессер\w*|рюкзак\w*|ранец\w*)\b', title, re.I):
                attrs['type'] = 'Сумки и рюкзаки'
            elif re.search(r'\b(футболк\w*|фуфайк\w*|поло\b|майк\w*|топ\b|боди\b)\b', title, re.I):
                attrs['type'] = 'Футболки и поло'
            elif re.search(r'\b(худи|свитшот\w*|лонгслив\w*|толстовк\w*|олимпийк\w*|кофт\w*|спортивн\w*\s+костюм\w*|костюм\s+спортивн\w*)\b', title, re.I):
                attrs['type'] = 'Худи, свитшоты и спорткостюмы'
            elif re.search(r'\b(пуховик\w*|куртк\w*|ветровк\w*|пальто|полупальто|плащ\w*|тренч\w*|парк[аи]\b|анорак\w*|бомбер\w*|жилет\w*|комбинезон\w*|полукомбинезон\w*)\b', title, re.I):
                attrs['type'] = 'Куртки и пуховики'
            elif re.search(r'\b(брюк\w*|джинс\w*|штан\w*|легинс\w*|леггинс\w*|джоггер\w*|тайтс\w*|бридж\w*|капри)\b', title, re.I):
                attrs['type'] = 'Брюки и джинсы'
            elif re.search(r'\b(плать\w*|юбк\w*|сарафан\w*)\b', title, re.I):
                attrs['type'] = 'Платья и юбки'
            elif re.search(r'\b(рубашк\w*|сорочк\w*|блузк\w*|блуз[аы]\b|туник\w*|пиджак\w*|жакет\w*|блейзер\w*)\b', title, re.I):
                attrs['type'] = 'Рубашки, блузки и жакеты'
            elif re.search(r'\b(свитер\w*|джемпер\w*|пуловер\w*|кардиган\w*|водолазк\w*)\b', title, re.I):
                attrs['type'] = 'Свитеры и кардиганы'
            elif re.search(r'\b(шорты|плавки|купальник\w*)\b', title, re.I):
                attrs['type'] = 'Шорты и пляжная одежда'
            elif re.search(r'\b(шарф\w*|палантин\w*|снуд\w*|шапк\w*|балаклав\w*|бейсболк\w*|кепк\w*|панам\w*|берет\w*|перчатк\w*|варежк\w*|рукавиц\w*|краги)\b', title, re.I):
                attrs['type'] = 'Головные уборы, шарфы и перчатки'
            elif re.search(r'\b(ювелирн\w*|кольцо|цепочк\w*|серьг\w*|подвеск\w*|браслет|колье|брошь|бижутери\w*)\b', title, re.I):
                attrs['type'] = 'Украшения и бижутерия'
            elif re.search(r'\b(резинк\w*\s+для\s+волос|заколк\w*|краб\s+для\s+волос|ободок|повязк\w*\s+на\s+голову|набор\s+для\s+волос)\b', title, re.I):
                attrs['type'] = 'Аксессуары для волос'
            elif re.search(r'\b(для\s+обуви|стельк\w*|полустельк\w*|шнурк\w*|рожок\s+для\s+обуви)\b', title, re.I):
                attrs['type'] = 'Уход и аксессуары для обуви'
            else:
                attrs['type'] = 'Аксессуары, портмоне и белье'

        # Пол и возраст
        if re.search(r'\b(для\s+девочек|для\s+девочки)\b', title, re.I):
            attrs['gender'] = 'Для девочек'
        elif re.search(r'\b(для\s+мальчиков|для\s+мальчика)\b', title, re.I):
            attrs['gender'] = 'Для мальчиков'
        elif re.search(r'\b(детск\w*|baby|kids)\b', title, re.I):
            attrs['gender'] = 'Детские'
        elif re.search(r'\b(женск\w*|women|woman|lady)\b', title, re.I):
            attrs['gender'] = 'Женские'
        elif re.search(r'\b(мужск\w*|men|man)\b', title, re.I):
            attrs['gender'] = 'Мужские'
        elif re.search(r'\b(унисекс|uni/|unisex)\b', title, re.I):
            attrs['gender'] = 'Унисекс'

        # Сезон для обуви/одежды
        if re.search(r'\b(зимн\w*|зима)\b', title, re.I):
            attrs['season'] = 'Зимние'
        elif re.search(r'\b(демисезон\w*|осень|весна)\b', title, re.I):
            attrs['season'] = 'Демисезонные'
        elif re.search(r'\b(летн\w*|лето)\b', title, re.I):
            attrs['season'] = 'Летние'
        elif re.search(r'\b(всесезон\w*|круглогодичн\w*)\b', title, re.I):
            attrs['season'] = 'Всесезонные'

        # Буквенный размер одежды в конце или в скобках
        m_asz = re.search(r'(?:размер\s*|\s+)(XXS|XS|S|M|L|XL|XXL|2XL|3XL|XXXL)\s*$', title)
        if m_asz:
            sz_map = {'2XL': 'XXL', 'XXXL': '3XL'}
            attrs['apparel_size'] = sz_map.get(m_asz.group(1), m_asz.group(1))

    # 27. Мебель и матрасы
    is_furniture = bool(re.search(
        r'\b(кресл\w*\s+(?:геймерск\w*|игров\w*|компьютерн\w*|офисн\w*|детск\w*|качалк\w*|мешок|кроват\w*|мягк\w*|садов\w*|ord\b)|'
        r'матрас\w*\s+анатомическ\w*|\bматрас\b|шкаф\w*|кроват\w*|диван\w*|комод\w*|тумб\w*|стеллаж\w*|этажерк\w*|перегородк\w*\s+межкомнатн\w*|'
        r'полк\w*\s+(?:настенн\w*|навесн\w*|углов\w*|обувн\w*|для\s+книг)|[cс]тойк\w*\s+(?:с\s+полками|для\s+|"кул\b)|'
        r'[cс]тол\w*\s+(?:компьютерн\w*|письменн\w*|обеденн\w*|игров\w*|для\s+геймеров|регулируем\w*|журнальн\w*|приставн\w*|кухонн\w*|складн\w*|туалетн\w*|деревянн\w*|квадратн\w*|sc-\d+)|'
        r'компьютерн\w*\s+стол|журнальн\w*\s+столик\w*|приставн\w*\s+столик\w*|столешниц\w*|набор\s+складной\s*:\s*стол|'
        r'подстаканник\s+dxracer|накладка\s+на\s+игровой\s+компьютерный\s+стол|ножки\s+для\s+модульного\s+компьютерного\s+стола|'
        r'стул\w*\s+(?:кухонн\w*|обеденн\w*|барн\w*|складн\w*|офисн\w*|детск\w*|садов\w*)|^\s*стул\b|'
        r'табурет\w*|пуф\w*|банкетк\w*|кухонн\w*\s+гарнитур\w*|обеденн\w*\s+групп\w*|прихож\w*|обувниц\w*|'
        r'монблан\s+\d|торвалль[-\s]\d|эльси\s+\d|теджонс\s+\d|дикинс\s+\d|пронто\s+\d|авильтон\s+\d|льери\s+|дейзи-\d|'
        r'шерона\s+\d|барроу\s+\d|нолан\s+\d|далти\s+\d|овалис\s+\d|финни\s+\d|кейси\s+\d|кирмас-\d|спейс-\d|бронс-\d|'
        r'саннес\s+|милна\s+\d|харинг\s+|ольен\s+|данста\s+|данэм\s+\d|картер\s+\d|енссон\s+\d|дона\s+\d|эймс\s+\d|'
        r'\b(?:велюр|рогожка|шенилл|букле)\s+(?:серый|бежевый|синий|зеленый|зелёный|розовый|оранжевый|желтый|графитовый|молочный|терракотовый|серо-голубой|светло-бежевый))\b',
        title, re.I
    )) or bool(re.search(r'\b(80|90|120|140|160|180|200)\s*[xх]\s*(190|195|200|205)\b', title, re.I) and not is_monitor)
    if is_furniture:
        if re.search(r'\b(кресл\w*\s+(?:геймерск\w*|игров\w*|компьютерн\w*)|gaming\s+chair|подстаканник\s+dxracer)\b', title, re.I):
            attrs['type'] = 'Игровое / Компьютерное кресло'
        elif re.search(r'\b(кресл\w*\s+офисн\w*|стул\w*|табурет\w*|пуф\w*|банкетк\w*)\b', title, re.I):
            attrs['type'] = 'Офисное кресло / Стул'
        elif re.search(r'\bматрас\w*\b', title, re.I):
            attrs['type'] = 'Матрас'
        elif re.search(r'\b(шкаф\w*|комод\w*|тумб\w*|стеллаж\w*|этажерк\w*|[cс]тойк\w*|полк\w*|обувниц\w*|монблан|торвалль|эльси|теджонс|дикинс|пронто|авильтон)\b', title, re.I):
            attrs['type'] = 'Шкаф / Комод / Тумба'
        elif re.search(r'\b(диван\w*|кресл\w*|льери|дейзи|шерона|барроу|нолан|далти|финни|кирмас|спейс|саннес|харинг|ольен|данста)\b', title, re.I):
            attrs['type'] = 'Диван'
        elif re.search(r'\b([cс]тол\w*|столик\w*|столешниц\w*|овалис|данэм|эймс)\b', title, re.I):
            attrs['type'] = 'Стол'
        elif re.search(r'\b(кроват\w*|кейси|бронс|картер|дона|(?:80|90|120|140|160|180|200)\s*[xх]\s*(?:190|195|200|205))\b', title, re.I):
            attrs['type'] = 'Кровать / Матрас'
        else:
            attrs['type'] = 'Мебель и гарнитуры'

        m_bed = re.search(r'\b(80|90|120|140|160|180|200)\s*[xх]\s*(190|195|200|205)\b', title, re.I)
        if m_bed:
            attrs['bed_size'] = f"{m_bed.group(1)}x{m_bed.group(2)} см"
        else:
            m_fdim = re.search(r'\b(\d{2,3})\s*[xх]\s*(\d{2,3})(?:\s*[xх]\s*(\d{2,3}))?\s*(?:см|cm)?\b', title, re.I)
            if m_fdim and not is_monitor:
                attrs['bed_size'] = f"{m_fdim.group(1)}x{m_fdim.group(2)} см"

        if re.search(r'\b(велюр\w*|шенилл\w*|рогожк\w*|букле)\b', title, re.I):
            attrs['material'] = 'Велюр'
        elif re.search(r'\b(экокож\w*|искусственн\w*\s+кож\w*)\b', title, re.I):
            attrs['material'] = 'Экокожа'
        elif re.search(r'\bтрикотаж\w*\b', title, re.I):
            attrs['material'] = 'Трикотаж'
        elif re.search(r'\bротанг\w*\b', title, re.I):
            attrs['material'] = 'Ротанг'
        elif re.search(r'\b(дуб|берез\w*|сосн\w*|ясен\w*|лдсп|мдф|массив|дерев\w*)\b', title, re.I):
            attrs['material'] = 'ЛДСП / Дерево'
        elif re.search(r'\b(металл\w*|сталь\w*)\b', title, re.I):
            attrs['material'] = 'Металл'

    # 28. Инструменты и ремонт
    is_tool = bool(re.search(
        r'\b(шуруповерт\w*|дрель|гайковерт\w*|перфоратор\w*|отбойн\w*\s+молот\w*|ушм|болгарк\w*|углошлифовальн\w*|угловая\s+шлифовальн\w*|плоскошлифовальн\w*|шлифовальн\w*\s+машин\w*|шлифмашин\w*|полировальн\w*\s+машин\w*|фрезер\w*|рубанок|электрорубанок|'
        r'электролобзик\w*|лобзик\w*|циркулярн\w*\s+пил\w*|дисков\w*\s+пил\w*|торцовочн\w*\s+пил\w*|монтажн\w*\s+отрезн\w*\s+пил\w*|цепн\w*\s+(?:мини\s+)?пил\w*|бензопил\w*|сабельн\w*\s+пил\w*|ножовк\w*|'
        r'сварочн\w*\s+(?:аппарат\w*|полуавтомат\w*|инвертор\w*|электрод\w*)|электрод\w*\s+сварочн\w*|паяльник\w*|паяльн\w*\s+станц\w*|строительн\w*\s+фен|термовоздуходувк\w*|клеев\w*\s+пистолет\w*|краскопульт\w*|'
        r'тестер\s+сети|мультиметр\w*|токов\w*\s+клещ\w*|измерительн\w*\s+провод\w*|лазерн\w*\s+уровен\w*|нивелир\w*|дальномер\w*|рулетк\w*|мерн\w*\s+лент\w*|геодезическ\w*|штангенциркул\w*|уровень\s+строительн\w*|'
        r'стремянк\w*|жима\s+саты|лестниц\w*|тележк\w*\s+гидравлическ\w*|рохл\w*|косилк\w*|триммер\s+(?:бензинов\w*|электрическ\w*|аккумуляторн\w*|садов\w*|травокосилк\w*)|мотокос\w*|бензокос\w*|газонокосилк\w*|мойк\w*\s+высокого\s+давления|минимойк\w*|воздуходувк\w*|кусторез\w*|мотоблок\w*|культиватор\w*|минитрактор\w*|снегоубор\w*|снегочистител\w*|компрессор\w*|генератор\s+(?:бензинов\w*|дизельн\w*|инверторн\w*)|дизельн\w*\s+генератор|бензинов\w*\s+генератор|мотопомп\w*|насос\s+(?:дренажн\w*|погружн\w*|скважинн\w*|циркуляционн\w*|поверхностн\w*|вибрационн\w*)|бетономешалк\w*|каминокомплект\w*|электрокамин\w*|отопительн\w*\s+котел|газов\w*\s+котел|'
        r'батарея\s+аккумуляторная|зарядн\w*\s+устройств\w*\s+(?:makita|greenworks|bosch|dewalt|metabo|интерскол|вихрь|зубр|alteco|ставр|ferm)|'
        r'набор\s+инструментов|отвертк\w*|ключ\s+(?:гаечн\w*|разводн\w*|комбинированн\w*|динамометрическ\w*|трубн\w*|трещоточн\w*|имбусов\w*|шестигранн\w*)|торцев\w*\s+головк\w*|набор\s+ключей|плоскогубц\w*|пассатиж\w*|бокорез\w*|кусачк\w*|клещи\s+|молоток\s+(?:слесарн\w*|плотницк\w*|резинов\w*|киянка)|топор\b|стамеск\w*|нож\s+строительн\w*|ножниц\w*\s+по\s+металлу|тиски\b|струбцин\w*|степлер\s+строительн\w*|заклепочник\w*|'
        r'пинцет\w*\s+(?:pro\'?s\s*kit|антистатическ\w*|монтажн\w*|техническ\w*|керамическ\w*|прецизионн\w*)|инструмент\s+для\s+(?:расшивки|зачистки|снятия\s+изоляции|обжима)|стриппер\w*|кримпер\w*|обжимн\w*\s+инструмент|экстрактор\w*|набор\s+шестигранник\w*|шестигранник\w*|монтировк\w*|гвоздодер\w*|лом\s+строительн\w*|зубил\w*|кернер\w*|стеклорез\w*|труборез\w*|пiстолет\s+для\s+(?:пены|герметика)|пистолет\s+для\s+(?:пены|герметика|силикона)|маск\w*\s+сварочн\w*|сварочн\w*\s+маск\w*|стекл\w*\s+для\s+маск\w*|'
        r'сверл\w*\s+(?:по\s+металлу|по\s+бетону|по\s+дереву|ступенчат\w*|корончат\w*)|набор\s+сверл|бур\s+по\s+бетону|диск\s+(?:отрезн\w*|пильн\w*|алмазн\w*|шлифовальн\w*|лепестков\w*|обдирочн\w*|зачистн\w*)|(?:отрезн\w*|обдирочн\w*|шлифовальн\w*|лепестков\w*|алмазн\w*|зачистн\w*|пильн\w*)\s+(?:круг\w*|диск\w*|чашк\w*)|коронк\w*\s+(?:биметаллическ\w*|алмазн\w*|по\s+(?:бетону|дереву|металлу|керамике))|щетк\w*\s+(?:корщетк\w*|чашечн\w*|дисков\w*|проволочн\w*)|корщетк\w*|набор\s+бит|биты\s+для|пилки\s+для\s+лобзика|полотно\s+(?:пильное|для\s+сабельной|для\s+ножовки)|шина\s+для\s+пил\w*|цепь\s+пильная|'
        r'клей\s+(?:для\s+керамогранита|плиточн\w*|монтажн\w*|обойн\w*|пва)|краска\s+(?:моющаяся|водоэмульсионн\w*|акрилов\w*\s+для\s+стен|фасадн\w*|интерьерн\w*|эмаль)|грунтовк\w*|шпатлевк\w*|шпаклевк\w*|шпатлевочн\w*|штукатурк\w*|knauf\s+rotband|alinex\s+|samaragips|затирк\w*|герметик\w*|пена\s+монтажная|звукоизоляция|панел\w*\s+(?:самоклеющ\w*|пвх|мдф|стенов\w*|настенн\w*)|камень\s+искусственн\w*|плитк\w*\s+(?:керамическ\w*|кафельн\w*|напольн\w*|настенн\w*)|керамогранит\w*|ламинат\w*|линолеум\w*|плинтус\w*|порог\s+\d+\s*мм|угол\s+(?:наружн\w*|отделочн\w*|внутренн\w*)|обои\s+(?:винилов\w*|флизелинов\w*|бумажн\w*|под\s+покраску)|фотообои\b|'
        r'душев\w*\s+(?:кабин\w*|огражден\w*|систем\w*|угол\w*|стойк\w*|гарнитур\w*|лейк\w*|поддон\w*)|гигиеническ\w*\s+душ|лейк\w*\s+для\s+душа|зеркал\w*\s+для\s+ванн\w*|экран\s+(?:под\s+ванн\w*|для\s+ванн\w*|для\s+радиатор\w*)|смесител\w*\s+для|раковин\w*|умывальник\w*|мойк\w*\s+для\s+кухни|унитаз\w*|инсталляц\w*\s+для|сидень\w*\s+для\s+унитаза|ванн\w*\s+(?:акрилов\w*|стальн\w*|чугунн\w*)|полотенцесушител\w*)\b',
        title, re.I
    ))
    if is_tool:
        if re.search(r'\b(шуруповерт\w*|дрель|гайковерт\w*)\b', title, re.I):
            attrs['type'] = 'Дрель / Шуруповерт'
        elif re.search(r'\b(перфоратор\w*|отбойн\w*\s+молот\w*)\b', title, re.I):
            attrs['type'] = 'Перфоратор / Отбойный молоток'
        elif re.search(r'\b(ушм|болгарк\w*|углошлифовальн\w*|угловая\s+шлифовальн\w*|плоскошлифовальн\w*|шлифовальн\w*\s+машин\w*|шлифмашин\w*|полировальн\w*|фрезер\w*|рубанок|электрорубанок)\b', title, re.I):
            attrs['type'] = 'УШМ и шлифмашины'
        elif re.search(r'\b(электролобзик\w*|лобзик\w*|циркулярн\w*\s+пил\w*|дисков\w*\s+пил\w*|монтажн\w*\s+отрезн\w*\s+пил\w*|цепн\w*\s+(?:мини\s+)?пил\w*|бензопил\w*|сабельн\w*\s+пил\w*|торцовочн\w*|ножовк\w*)\b', title, re.I):
            attrs['type'] = 'Пилы и электролобзики'
        elif re.search(r'\b(сварочн\w*|электрод\w*\s+сварочн\w*|маск\w*\s+сварочн\w*|стекл\w*\s+для\s+маск\w*)\b', title, re.I):
            attrs['type'] = 'Сварочное оборудование'
        elif re.search(r'\b(тестер\s+сети|мультиметр\w*|токов\w*\s+клещ\w*|измерительн\w*|лазерн\w*\s+уровен\w*|нивелир\w*|дальномер\w*|рулетк\w*|мерн\w*\s+лент\w*|геодезическ\w*|штангенциркул\w*|уровень\s+строительн\w*)\b', title, re.I):
            attrs['type'] = 'Измерительный инструмент и тестеры'
        elif re.search(r'\b(паяльник\w*|паяльн\w*|строительн\w*\s+фен|термовоздуходувк\w*|клеев\w*\s+пистолет\w*|краскопульт\w*)\b', title, re.I):
            attrs['type'] = 'Паяльное и тепловое оборудование'
        elif re.search(r'\b(косилк\w*|газонокосилк\w*|триммер|мотокос\w*|бензокос\w*|мойк\w*\s+высокого\s+давления|минимойк\w*|воздуходувк\w*|кусторез\w*|мотоблок\w*|культиватор\w*|минитрактор\w*|снегоубор\w*|снегочистител\w*|компрессор\w*|генератор|мотопомп\w*|насос|бетономешалк\w*|тележк\w*\s+гидравлическ\w*|рохл\w*|каминокомплект\w*|электрокамин\w*|котел)\b', title, re.I):
            attrs['type'] = 'Садовая и силовая техника'
        elif re.search(r'\b(батарея\s+аккумуляторная|аккумулятор\s+для\s+инструмента|зарядн\w*\s+устройств\w*)\b', title, re.I):
            attrs['type'] = 'Аккумуляторы для инструмента'
        elif re.search(r'\b(стремянк\w*|жима\s+саты|лестниц\w*)\b', title, re.I):
            attrs['type'] = 'Стремянки и лестницы'
        elif re.search(r'\b(набор\s+инструментов|отвертк\w*|ключ\s+|торцев\w*\s+головк\w*|набор\s+ключей|плоскогубц\w*|пассатиж\w*|бокорез\w*|кусачк\w*|клещи|молоток|топор|стамеск\w*|нож\s+строительн\w*|ножниц\w*\s+по\s+металлу|тиски|струбцин\w*|степлер\s+строительн\w*|заклепочник\w*|пинцет\w*|инструмент\s+для\s+(?:расшивки|зачистки|снятия|обжима)|стриппер\w*|кримпер\w*|обжимн\w*|экстрактор\w*|шестигранник\w*|монтировк\w*|гвоздодер\w*|лом\s+|зубил\w*|кернер\w*|стеклорез\w*|труборез\w*|пистолет\s+для)\b', title, re.I):
            attrs['type'] = 'Ручной инструмент и наборы'
        elif re.search(r'\b(сверл\w*|набор\s+сверл|бур\s+по|диск\s+|(?:отрезн\w*|обдирочн\w*|шлифовальн\w*|лепестков\w*|алмазн\w*|зачистн\w*|пильн\w*)\s+(?:круг\w*|диск\w*|чашк\w*)|коронк\w*|щетк\w*|корщетк\w*|набор\s+бит|биты\s+для|пилки\s+для|полотно|шина\s+для\s+пил|цепь\s+пильная)\b', title, re.I):
            attrs['type'] = 'Оснастка и расходники'
        elif re.search(r'\b(клей\s+|краска\s+|грунтовк\w*|шпатлевк\w*|шпаклевк\w*|шпатлевочн\w*|штукатурк\w*|knauf\s+rotband|alinex|samaragips|затирк\w*|пропитка\s+|звукоизоляция|герметик\w*|пена\s+монтажная|панел\w*|камень\s+искусственн\w*|плитк\w*|керамогранит\w*|ламинат\w*|линолеум\w*|плинтус\w*|порог\s+|угол\s+|обои|фотообои)\b', title, re.I):
            attrs['type'] = 'Строительные смеси, ЛКМ и отделка'
        elif re.search(r'\b(душев\w*|гигиеническ\w*\s+душ|лейк\w*\s+для\s+душа|зеркал\w*\s+для\s+ванн|экран\s+|смесител\w*|раковин\w*|умывальник\w*|мойк\w*\s+для\s+кухни|унитаз\w*|инсталляц\w*|сидень\w*\s+для\s+унитаза|ванн\w*|полотенцесушител\w*)\b', title, re.I):
            attrs['type'] = 'Сантехника'

        if re.search(r'\b(аккумуляторн\w*|батарея\s+аккумуляторная|li-ion)\b', title, re.I):
            attrs['power_supply'] = 'Аккумуляторный'
        elif re.search(r'\b(бензинов\w*|бензопил\w*|бензокос\w*|мотокос\w*|мотоблок\w*|мотопомп\w*)\b', title, re.I):
            attrs['power_supply'] = 'Бензиновый'
        elif re.search(r'\b(сетев\w*|220\s*в|\d{3,4}\s*(?:w|вт))\b', title, re.I):
            attrs['power_supply'] = 'Сетевой (220 В)'
        elif attrs.get('type') == 'Ручной инструмент и наборы':
            attrs['power_supply'] = 'Ручной'

        m_volt = (
            re.search(r'\b(10\.8|12|14\.4|18|20|24|36|40|80)\s*(?:v|в)\b', title, re.I)
            or re.search(r'\b(?:bl18|g18)\w*\b', title, re.I)
            or re.search(r'\b(?:g24|bl24)\w*\b', title, re.I)
            or re.search(r'\b(?:g40|bl40)\w*\b', title, re.I)
        )
        if m_volt:
            v_str = m_volt.group(1) if m_volt.lastindex else ('18' if '18' in m_volt.group(0) else ('24' if '24' in m_volt.group(0) else '40'))
            attrs['voltage'] = f"{v_str} В"
            if 'power_supply' not in attrs:
                attrs['power_supply'] = 'Аккумуляторный'

        m_tool_w = re.search(r'\b([1-9]\d{1,3})\s*(?:w|вт)\b', title, re.I)
        if m_tool_w and 'power' not in attrs:
            attrs['power'] = f"{m_tool_w.group(1)} Вт"

    # 29. Парфюмерия, Косметика, Уход за волосами и кожей, Гигиена
    is_perfume_beauty = bool(re.search(
        r'\b(парфюмерн\w*\s+вод\w*|туалетн\w*\s+вод\w*|одеколон\w*|парфюмированн\w*\s+(?:мист|спрей|лосьон|крем|гель)|eau\s+de\s+(?:parfum|toilette)|'
        r'дезодорант\w*|антиперспирант\w*|крем[- ]краск\w*|краск\w*\s+для\s+(?:волос|бровей)|шампунь\w*|гель\s+для\s+(?:душа|купания|умывания|бритья|интимной|бровей|лица|тела)|'
        r'крем\s+для\s+(?:рук|лица|тела|ног|век|кожи\s+вокруг\s+глаз|бритья|депиляции|укладки|волос)|крем[- ]гель|крем[- ]флюид|крем[- ]баттер|флюид\s+для\s+лица|эмульси\w*\s+для\s+лица|эссенци\w*\s+для\s+лица|'
        r'сыворотк\w*\s+(?:для|с\s+)|маск\w*\s+для\s+(?:лица|волос|губ|рук|ног|кожи)|патчи\s+(?:для|гидрогелев\w*|тканев\w*)|гидрофильн\w*\s+(?:масло|бальзам)|мицеллярн\w*\s+(?:вода|гель|пенка)|тоник\s+для\s+лица|тонер\s+для\s+лица|мист\s+для\s+(?:лица|тела|волос)|пенк\w*\s+для\s+(?:умывания|лица|интимной|волос)|мусс\s+для\s+(?:умывания|волос|тела|губ)|скраб\s+для\s+(?:лица|тела|губ|кожи\s+головы)|пилинг\s+(?:для|скатка)|лосьон\s+для\s+(?:тела|лица|волос|после\s+бритья)|молочко\s+для\s+(?:тела|лица|снятия\s+макияжа)|бальзам\s+для\s+(?:губ|волос|тела|после\s+бритья)|масло\s+для\s+(?:губ|волос|тела|лица|бороды|кутикулы|душа|загара)|кондиционер\s+для\s+волос|несмываем\w*\s+(?:уход|спрей|крем)|термозащит\w*\s+для\s+волос|лак\s+для\s+(?:волос|ногтей)|паста\s+для\s+(?:укладки|шугаринга)|воск\s+для\s+(?:укладки|волос|бороды|депиляции)|сухой\s+шампунь|'
        r'палетк\w*\s+(?:теней|для\s+макияжа|для\s+скульптурирования|для\s+контуринга|консилеров|хайлайтеров)|тени\s+для\s+(?:век|бровей)|тушь\s+для\s+(?:ресниц|бровей)|помада\s+для\s+(?:губ|бровей)|губная\s+помада|блеск\s+для\s+губ|тинт\s+для\s+губ|карандаш\s+для\s+(?:губ|глаз|бровей|каял)|подводк\w*\s+для\s+глаз|лайнер\s+для\s+(?:глаз|бровей)|тональн\w*\s+(?:крем|основа|флюид|сыворотка|мусс|кушон)|кушон\s+для\s+лица|bb[- ]крем|cc[- ]крем|консилер\w*|корректор\s+для\s+лица|пудра\s+(?:для\s+лица|компактная|рассыпчатая|матирующая|минеральная)|румяна\s+(?:для\s+лица|компактные|жидкие|кремовые|в\s+стике)|хайлайтер\w*|бронзер\w*|скульптор\s+для\s+лица|контуринг\s+(?:для\s+)?лица|стик\s+для\s+(?:лица|контуринга|макияжа)|праймер\s+для\s+(?:лица|макияжа|ресниц|век)|база\s+под\s+(?:макияж|тени)|фиксатор\s+(?:для\s+)?макияжа|фиксирующ\w*\s+(?:спрей|гель|пудра)|спонж\s+для\s+макияжа|кисть\s+для\s+(?:макияжа|пудры|румян|теней|бровей|тона)|набор\s+кистей\s+для\s+макияжа|накладн\w*\s+(?:ресниц\w*|ногт\w*)|средств\w*\s+для\s+снятия\s+(?:макияжа|лака)|'
        r'электробритв\w*|бритв\w*\s+(?:мужск\w*|женск\w*|одноразов\w*|безопасн\w*|gillette|venus|bic)|сменн\w*\s+кассет\w*\s+для\s+бритв\w*|эпилятор\w*|фотоэпилятор\w*|триммер\w*\s+(?:для\s+(?:бороды|усов|носа|ушей|тела|бикини|волос|бровей)|мужск\w*|универсальн\w*|philips|braun|panasonic|rowenta|remington|wahl|moser|babyliss|xiaomi|scarlett|vitek|polaris)|машинк\w*\s+для\s+стрижки\s+(?:волос|катышков)?|фен\s+(?:для\s+волос|щетка|стайлер|профессиональн\w*|дорожн\w*|dyson|philips|rowenta|babyliss|remington|braun|valera|scarlett|vitek|polaris|xiaomi|dreame)|электрощипц\w*|щипц\w*\s+(?:для\s+завивки|гофре|выпрямитель)|плойк\w*\s+для\s+волос|выпрямител\w*\s+для\s+волос|утюжок\s+для\s+волос|мультистайлер\w*|стайлер\w*\s+для\s+волос|термобигуди|расческ\w*\s+(?:для\s+волос|массажн\w*|выпрямитель|tangle\s+teezer|janeke)|щетк\w*\s+для\s+(?:волос|укладки|лица|тела|зубная|электрическая\s+зубная)|ирригатор\w*|зубн\w*\s+(?:паст\w*|щетк\w*|нить|порошок)|ополаскивател\w*\s+для\s+полости\s+рта|'
        r'косметичк\w*|пилочк\w*\s+для\s+ногтей|маникюрн\w*\s+набор|кусачки\s+маникюрн\w*|тонометр\w*|ингалятор\w*|небулайзер\w*|термометр\s+(?:медицинск\w*|бесконтактн\w*|инфракрасн\w*|электронн\w*)|массажер\w*\s+для\s+(?:тела|шеи|спины|ног|лица|головы|глаз)|дарсонвал\w*|аппарат\s+для\s+(?:маникюра|педикюра|чистки\s+лица)|'
        r'весы\s+(?!кухонн\w*|торгов\w*|кранов\w*|товарн\w*|платформенн\w*|фасовочн\w*|ювелирн\w*|багажн\w*)[a-zа-я0-9]+)\b',
        title, re.I
    )) and not is_tool
    if is_perfume_beauty:
        if re.search(r'\b(парфюмерн\w*\s+вод\w*|eau\s+de\s+parfum|\bedp\b)\b', title, re.I):
            attrs['type'] = 'Парфюмерная вода (EDP)'
        elif re.search(r'\b(туалетн\w*\s+вод\w*|eau\s+de\s+toilette|\bedt\b|одеколон\w*)\b', title, re.I):
            attrs['type'] = 'Туалетная вода (EDT)'
        elif re.search(r'\b(парфюмированн\w*\s+(?:мист|спрей)|мист\s+для\s+тела|спрей\s+для\s+тела)\b', title, re.I):
            attrs['type'] = 'Парфюмированный мист / Спрей'
        elif re.search(r'\b(дезодорант\w*|антиперспирант\w*)\b', title, re.I):
            attrs['type'] = 'Дезодорант / Антиперспирант'
        elif re.search(r'\bвесы\b', title, re.I):
            attrs['type'] = 'Напольные весы'
        elif re.search(r'\b(электробритв\w*|эпилятор\w*|фотоэпилятор\w*|триммер\w*|машинк\w*\s+для\s+стрижки|бритв\w*|сменн\w*\s+кассет\w*\s+для\s+бритв)\b', title, re.I):
            attrs['type'] = 'Бритвы, триммеры и эпиляторы'
        elif re.search(r'\b(фен\s+|фен[- ]щетк\w*|электрощипц\w*|щипц\w*\s+для|плойк\w*|выпрямител\w*\s+для\s+волос|утюжок\s+для\s+волос|мультистайлер\w*|стайлер\w*|термобигуди)\b', title, re.I):
            attrs['type'] = 'Фены, стайлеры и выпрямители'
        elif re.search(r'\b(зубн\w*\s+(?:паст\w*|щетк\w*|нить|порошок)|электрическая\s+зубная|ирригатор\w*|ополаскивател\w*\s+для\s+полости\s+рта)\b', title, re.I):
            attrs['type'] = 'Уход за полостью рта'
        elif re.search(r'\b(тонометр\w*|ингалятор\w*|небулайзер\w*|термометр\s+(?:медицинск|бесконтактн|инфракрасн|электронн)|массажер\w*|дарсонвал\w*|аппарат\s+для\s+(?:маникюра|педикюра|чистки\s+лица))\b', title, re.I):
            attrs['type'] = 'Медтехника и массажеры'
        elif re.search(r'\b(палетк\w*\s+(?:теней|для)|тени\s+для|тушь\s+для|помада|блеск\s+для\s+губ|тинт\s+для\s+губ|мусс\s+для\s+губ|карандаш\s+для\s+(?:глаз|бровей|губ|каял)|подводк\w*\s+для\s+глаз|лайнер\s+для|тональн\w*\s+(?:крем|основа|флюид|сыворотка|мусс|кушон)|кушон|bb[- ]крем|cc[- ]крем|консилер\w*|корректор\s+для\s+лица|пудра|румяна|хайлайтер\w*|бронзер\w*|скульптор\s+для\s+лица|контуринг|стик\s+для\s+лица|праймер|база\s+под|фиксатор\s+макияжа|фиксирующ\w*\s+(?:спрей|гель|пудра)|спонж\s+для\s+макияжа|кисть\s+для|набор\s+кистей|накладн\w*\s+(?:ресниц|ногт)|лак\s+для\s+ногтей)\b', title, re.I):
            if re.search(r'\b(помада\s+для\s+губ|губная\s+помада|блеск\s+для\s+губ|тинт\s+для\s+губ|мусс\s+для\s+губ|карандаш\s+для\s+губ)\b', title, re.I):
                attrs['type'] = 'Макияж для губ (помады, блески, карандаши)'
            elif re.search(r'\b(тушь\s+для|тени\s+для|палетк\w*\s+теней|подводк\w*\s+для\s+глаз|лайнер\s+для|карандаш\s+для\s+(?:глаз|бровей|каял)|гель\s+для\s+бровей|помада\s+для\s+бровей|накладн\w*\s+ресниц)\b', title, re.I):
                attrs['type'] = 'Макияж для глаз и бровей'
            elif re.search(r'\b(тональн\w*|кушон|bb[- ]крем|cc[- ]крем|консилер\w*|корректор|пудра|румяна|хайлайтер\w*|бронзер\w*|скульптор|контуринг|стик\s+для\s+лица|праймер|база\s+под|фиксатор|фиксирующ\w*)\b', title, re.I):
                attrs['type'] = 'Макияж для лица (тон, пудра, контуринг)'
            else:
                attrs['type'] = 'Кисти, спонжи и маникюр'
        elif re.search(r'\b(крем[- ]краск\w*|краск\w*\s+для\s+волос|шампунь\w*|сухой\s+шампунь|бальзам\s+для\s+волос|кондиционер\s+для\s+волос|маск\w*\s+для\s+волос|масло\s+для\s+волос|спрей\s+для\s+волос|несмываем\w*|термозащит\w*|лак\s+для\s+волос|мусс\s+для\s+волос|пенк\w*\s+для\s+волос|паста\s+для\s+укладки|воск\s+для\s+волос|расческ\w*|щетк\w*\s+для\s+(?:волос|укладки))\b', title, re.I):
            attrs['type'] = 'Уход, укладка и окрашивание волос'
        elif re.search(r'\b(крем\s+для\s+(?:лица|век|кожи\s+вокруг\s+глаз)|крем[- ]гель|крем[- ]флюид|флюид\s+для\s+лица|эмульси\w*\s+для\s+лица|эссенци\w*\s+для\s+лица|сыворотк\w*|маск\w*\s+для\s+(?:лица|губ)|патчи|гидрофильн\w*\s+(?:масло|бальзам)|мицеллярн\w*|тоник\s+для\s+лица|тонер\s+для\s+лица|мист\s+для\s+лица|пенк\w*\s+для\s+(?:умывания|лица)|гель\s+для\s+умывания|мусс\s+для\s+умывания|скраб\s+для\s+(?:лица|губ)|пилинг|лосьон\s+для\s+лица|молочко\s+для\s+(?:лица|снятия\s+макияжа)|бальзам\s+для\s+губ|масло\s+для\s+(?:губ|лица)|средств\w*\s+для\s+снятия\s+макияжа)\b', title, re.I):
            attrs['type'] = 'Уход за лицом и губами'
        elif re.search(r'\b(крем\s+для\s+(?:рук|тела|ног|бритья|депиляции)|крем[- ]баттер|молочко\s+для\s+тела|лосьон\s+для\s+(?:тела|после\s+бритья)|бальзам\s+после\s+бритья|масло\s+для\s+(?:тела|душа|загара|бороды|кутикулы)|скраб\s+для\s+тела)\b', title, re.I):
            attrs['type'] = 'Уход за телом, руками и бритье'
        elif re.search(r'\b(гель\s+для\s+(?:душа|купания|интимной|бритья)|пенк\w*\s+для\s+интимной|жидкое\s+мыло|мыло\s+туалетное|пена\s+для\s+ванн)\b', title, re.I):
            attrs['type'] = 'Гели для душа, мыло и гигиена'
        elif re.search(r'\b(косметичк\w*|пилочк\w*\s+для\s+ногтей|маникюрн\w*|кусачки\s+маникюрн\w*)\b', title, re.I):
            attrs['type'] = 'Косметички и маникюр'

        if re.search(r'\b(pour\s+femme|donna|for\s+her|for\s+women|женск\w*|lady|miss\s+dior|la\s+vie\s+est\s+belle)\b', title, re.I):
            attrs['gender'] = 'Женский'
        elif re.search(r'\b(pour\s+homme|for\s+him|for\s+men|мужск\w*|uomo)\b', title, re.I):
            attrs['gender'] = 'Мужской'

        m_spf = re.search(r'\bspf\s*(15|20|25|30|35|40|45|50)\+?\b', title, re.I)
        if m_spf:
            attrs['spf'] = f"SPF {m_spf.group(1)}+" if '+' in m_spf.group(0) or m_spf.group(1) == '50' else f"SPF {m_spf.group(1)}"

        m_ml = re.search(r'\b(\d{1,4}(?:[.,]\d)?)\s*(?:мл|ml)\b', title, re.I)
        if m_ml:
            attrs['volume_ml'] = f"{m_ml.group(1).replace(',', '.')} мл"
        else:
            m_bg = re.search(r'\b(\d{1,3}(?:[.,]\d)?)\s*(?:г|гр|g)\b', title, re.I)
            if m_bg:
                attrs['pack_weight'] = f"{m_bg.group(1).replace(',', '.')} г"

        m_bw = re.search(r'\b([1-3]\d{2,3})\s*(?:вт|w)\b', title, re.I)
        if m_bw and 'power' not in attrs:
            attrs['power'] = f"{m_bw.group(1)} Вт"

    # 30. Игрушки и конструкторы (включая LEGO)
    is_toy_item = bool(re.search(
        r'\b(lego\b|лего\b|конструктор\w*|игрушк\w*\s+мягконабивн\w*|мягк\w*\s+игрушк\w*|игрушк\w*(?!\s+для\s+(?:кошек|собак|животных))|игрушечн\w*|'
        r'надувн\w*\s+(?:игрушк\w*|плот|матрас[- ]плот|круг\s+intex|круг\s+bestway|бассейн\s+детск\w*|жилет\s+для\s+плавания|нарукавник\w*|мяч)|игрушк\w*[- ]плот|'
        r'квадрокоптер\w*\s+.*на\s+радиоуправлении|на\s+радиоуправлении|радиоуправляем\w*|трансформер\w*\s+класса|'
        r'подвижная\s+фигурка|фигурка\s+персонажа|игров\w*\s+фигурк\w*|коллекционн\w*\s+фигурк\w*|фигурка\s+funko|'
        r'кукла\b|куклы\b|пупс\b|коляск\w*\s+.*для\s+кук\w*|кроватк\w*\s+для\s+кук\w*|одежда\s+для\s+кук\w*|мебел\w*\s+для\s+кук\w*|дом\w*\s+для\s+кук\w*|'
        r'бластер\w*|водн\w*\s+пистолет\w*|мыльн\w*\s+пузыр\w*|слайм\w*|лизун\w*|антистресс\w*|сквиш\w*|спиннер\w*|'
        r'машинк\w*\s+(?:металлическ|инерционн|детск|каталка|технопарк|hot\s+wheels|спецтехник)|автотрек\w*|парковк\w*\s+(?:детск|игров|уровнев|maxrace)|гараж\s+(?:детск|игров|maxrace)|железн\w*\s+дорог\w*|паровозик\w*|поезд\s+игрушечн|трактор\s+(?:полесье|детск|инерционн)|спецтехник\w*\s+полесье|'
        r'игров\w*\s+(?:набор\w*|комплект|центр|коврик|палатк\w*|домик|кухн\w*|магазин|касса|мастерская)|набор\s+(?:доктора|врача|парикмахера|стилиста|инструментов\s+детск|солдатиков|динозавров|животных|посуды\s+детск|для\s+песочницы|для\s+игры)|тележк\w*\s+(?:с\s+набором|для\s+супермаркета)|'
        r'юла\b|юла[- ]карусель|неваляшк\w*|каталк\w*(?!\s*-толокар)|заводн\w*\s+игрушк\w*|музыкальн\w*\s+(?:игрушк\w*|коврик|руль|телефончик)|книжк\w*\s+для\s+ванной|игрушк\w*\s+для\s+ванной|магнитн\w*\s+мозаик\w*|деревянн\w*\s+(?:игрушк\w*|пазл|вкладыш|шнуровк|стучалк|лабиринт)|елочн\w*\s+(?:игрушк\w*|украшен\w*|шар\w*))\b',
        title, re.I
    )) and not re.search(r'\bдля\s+(?:кошек|собак|животных|грызунов|птиц)\b', title, re.I)
    if is_toy_item:
        if re.search(r'\b(lego\b|лего\b|конструктор\w*|магнитн\w*\s+мозаик\w*)\b', title, re.I):
            attrs['type'] = 'Конструктор'
            if re.search(r'\btechnic\b', title, re.I):
                attrs['lego_series'] = 'LEGO Technic'
            elif re.search(r'\bcity\b', title, re.I):
                attrs['lego_series'] = 'LEGO City'
            elif re.search(r'\bspeed\s+champions\b', title, re.I):
                attrs['lego_series'] = 'LEGO Speed Champions'
            elif re.search(r'\bstar\s+wars\b', title, re.I):
                attrs['lego_series'] = 'LEGO Star Wars'
            elif re.search(r'\bninjago\b', title, re.I):
                attrs['lego_series'] = 'LEGO Ninjago'
            elif re.search(r'\bfriends\b', title, re.I):
                attrs['lego_series'] = 'LEGO Friends'
            elif re.search(r'\bminecraft\b', title, re.I):
                attrs['lego_series'] = 'LEGO Minecraft'
            elif re.search(r'\b(marvel|super\s+heroes|avengers|spider-man)\b', title, re.I):
                attrs['lego_series'] = 'LEGO Marvel / Super Heroes'
            elif re.search(r'\b(botanicals|icons)\b', title, re.I):
                attrs['lego_series'] = 'LEGO Icons / Botanicals'
            elif re.search(r'\bcreator\b', title, re.I):
                attrs['lego_series'] = 'LEGO Creator'
            elif re.search(r'\bharry\s+potter\b', title, re.I):
                attrs['lego_series'] = 'LEGO Harry Potter'
            elif re.search(r'\bduplo\b', title, re.I):
                attrs['lego_series'] = 'LEGO Duplo'
            elif re.search(r'\bclassic\b', title, re.I):
                attrs['lego_series'] = 'LEGO Classic'
            elif re.search(r'\bмагнитн\w*\b', title, re.I):
                attrs['lego_series'] = 'Магнитный конструктор'
        elif re.search(r'\b(мягконабивн\w*|мягк\w*\s+игрушк\w*|плюшев\w*)\b', title, re.I):
            attrs['type'] = 'Мягкая игрушка'
        elif re.search(r'\b(на\s+радиоуправлении|радиоуправляем\w*)\b', title, re.I):
            attrs['type'] = 'Радиоуправляемая игрушка'
        elif re.search(r'\b(машинк\w*|автотрек\w*|парковк\w*|гараж|железн\w*\s+дорог\w*|паровозик\w*|поезд|трактор|спецтехник\w*)\b', title, re.I):
            attrs['type'] = 'Машинки, треки и техника'
        elif re.search(r'\b(трансформер\w*|подвижная\s+фигурка|фигурка\s+персонажа|игров\w*\s+фигурк\w*|коллекционн\w*\s+фигурк\w*|фигурка\s+funko|солдатик\w*|динозавр\w*)\b', title, re.I):
            attrs['type'] = 'Фигурка / Робот'
        elif re.search(r'\b(кукла|куклы|пупс|для\s+кук\w*)\b', title, re.I):
            attrs['type'] = 'Кукла / Пупс и аксессуары'
        elif re.search(r'\b(надувн\w*|плот|для\s+плавания|нарукавник\w*|водн\w*\s+пистолет|мыльн\w*\s+пузыр|для\s+песочницы)\b', title, re.I):
            attrs['type'] = 'Надувные и летние игрушки'
        else:
            attrs['type'] = 'Игрушка / Игровой набор'

        m_det = re.search(r'\b(\d{1,4})\s*(?:дет\.?|предм\w*|эл\.?|шт\.?)\b', title, re.I)
        if m_det:
            attrs['pieces_count'] = f"{m_det.group(1)} дет."

    # 31. Компьютеры, системные блоки, моноблоки и неттопы
    is_desktop_pc = bool(re.search(
        r'\b(моноблок\w*|all[- ]in[- ]one|\baio\b|\bimac\b|системн\w*\s+блок\w*|игров\w*\s+пк|\bпк\s+[a-zа-я0-9]|'
        r'компьютер\s+(?:игров\w*|офисн\w*|настольн\w*|персональн\w*|x-game|iru|hyperpc|ardor|delux)|'
        r'неттоп\w*|мини[- ]пк|mini\s*pc|mac\s+mini|mac\s+studio|mac\s+pro|тонкий\s+клиент)\b',
        title, re.I
    )) and not bool(re.search(r'\b(корпус\s+для|блок\s+питания|стол\s+для|кресло|монитор|держатель|кронштейн|чехол|сумка)\b', title, re.I))
    if is_desktop_pc:
        if re.search(r'\b(моноблок\w*|all[- ]in[- ]one|\baio\b|\bimac\b)\b', title, re.I):
            attrs['type'] = 'Моноблок (All-in-One)'
            if 'screen_size' not in attrs:
                m_aio_sz = (
                    re.search(r'\b(21\.5|23\.8|24|27|31\.5|32|34)\s*(?:"|”|\'\'|дюйм\w*)', title, re.I)
                    or re.search(r'\b(?:aio\s*\d*\s*|all-in-one\s+|c|v|a3|a5|m|cr|cb|df|dd|dp|imac\s+)(21\.5|23\.8|24|27)(?=[-a-z0-9]|\b)', title, re.I)
                )
                if m_aio_sz:
                    attrs['screen_size'] = f'{m_aio_sz.group(1)}"'
                elif re.search(r'\b(?:optiplex\s+[57]4\d{2}|a3402|24itl|24iap|24irh|24arr)', title, re.I):
                    attrs['screen_size'] = '23.8"'
                elif re.search(r'\b(?:v30a)', title, re.I):
                    attrs['screen_size'] = '21.5"'
        elif re.search(r'\b(неттоп\w*|мини[- ]пк|mini\s*pc|mac\s+mini|mac\s+studio|тонкий\s+клиент|nuc\b)\b', title, re.I):
            attrs['type'] = 'Мини-ПК / Неттоп'
        else:
            attrs['type'] = 'Системный блок (ПК)'

        if 'cpu' not in attrs:
            if re.search(r'\bcore\s+ultra\s*[579]\b', title, re.I):
                attrs['cpu'] = 'Intel Core Ultra'
            elif re.search(r'\bcore\s*i9\b', title, re.I):
                attrs['cpu'] = 'Intel Core i9'
            elif re.search(r'\bcore\s*i7\b', title, re.I):
                attrs['cpu'] = 'Intel Core i7'
            elif re.search(r'\bcore\s*i5\b', title, re.I):
                attrs['cpu'] = 'Intel Core i5'
            elif re.search(r'\bcore\s*i3\b', title, re.I):
                attrs['cpu'] = 'Intel Core i3'
            elif re.search(r'\bryzen\s*9\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 9'
            elif re.search(r'\bryzen\s*7\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 7'
            elif re.search(r'\bryzen\s*5\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 5'
            elif re.search(r'\bryzen\s*3\b', title, re.I):
                attrs['cpu'] = 'AMD Ryzen 3'
            elif re.search(r'\bapple\s+m[1234]\b|\bm[1234]\s+(?:pro|max|ultra)\b', title, re.I):
                attrs['cpu'] = 'Apple M-Series'
            elif re.search(r'\b(pentium|celeron|intel\s+n\d{3})\b', title, re.I):
                attrs['cpu'] = 'Intel Pentium / Celeron'

        if 'os' not in attrs:
            if re.search(r'\b(win(?:dows)?\s*11\s*pro|w11p)\b', title, re.I):
                attrs['os'] = 'Windows 11 Pro'
            elif re.search(r'\b(win(?:dows)?\s*11|w11)\b', title, re.I):
                attrs['os'] = 'Windows 11'
            elif re.search(r'\b(win(?:dows)?\s*10|w10)\b', title, re.I):
                attrs['os'] = 'Windows 10'
            elif re.search(r'\b(macos|imac|mac\s+mini|mac\s+studio)\b', title, re.I):
                attrs['os'] = 'macOS'
            elif re.search(r'\b(freedos|dos\b|без\s+ос|no\s*os)\b', title, re.I):
                attrs['os'] = 'Без ОС'
            elif re.search(r'\b(ubuntu|linux)\b', title, re.I):
                attrs['os'] = 'Linux'

    # 32. Аудиотехника (микрофоны, колонки, саундбары, акустика)
    is_audio_equip = bool(re.search(
        r'\b(микрофон\w*|радиосистем\w*|петличн\w*\s+микрофон|колонка\s+|портативн\w*\s+(?:акустик\w*|колонк\w*)|'
        r'умн\w*\s+колонк\w*|яндекс\s+станц\w*|саундбар\w*|soundbar|акустическ\w*\s+систем\w*|'
        r'компьютерн\w*\s+колонк\w*|колонк\w*\s+для\s+пк|музыкальн\w*\s+центр\w*|магнитол\w*|радиоприемник\w*|'
        r'проигрывател\w*\s+винил\w*|винилов\w*\s+проигрывател\w*|диктофон\w*|ресивер\w*\s+av|усилител\w*\s+для\s+наушников|микшерн\w*\s+пульт)\b',
        title, re.I
    )) and not is_hp
    if is_audio_equip:
        if re.search(r'\b(микрофон\w*|радиосистем\w*)\b', title, re.I):
            attrs['type'] = 'Микрофон / Радиосистема'
        elif re.search(r'\b(умн\w*\s+колонк\w*|яндекс\s+станц\w*|vk\s+капсул\w*|homepod|echo\s+dot)\b', title, re.I):
            attrs['type'] = 'Умная колонка'
        elif re.search(r'\b(саундбар\w*|soundbar|домашн\w*\s+кинотеатр)\b', title, re.I):
            attrs['type'] = 'Саундбар / Домашний кинотеатр'
        elif re.search(r'\b(портативн\w*\s+(?:акустик\w*|колонк\w*)|bluetooth[- ]колонк\w*|jbl\s+(?:flip|charge|pulse|xtreme|boombox|go|clip|partybox))\b', title, re.I):
            attrs['type'] = 'Портативная / Вечериночная акустика'
        elif re.search(r'\b(проигрывател\w*\s+винил\w*|винилов\w*\s+проигрывател\w*)\b', title, re.I):
            attrs['type'] = 'Виниловый проигрыватель'
        elif re.search(r'\b(магнитол\w*|радиоприемник\w*|музыкальн\w*\s+центр\w*)\b', title, re.I):
            attrs['type'] = 'Магнитола / Музыкальный центр'
        elif re.search(r'\b(диктофон\w*)\b', title, re.I):
            attrs['type'] = 'Диктофон'
        else:
            attrs['type'] = 'Стационарная / Компьютерная акустика'

        if re.search(r'\b(беспроводн\w*|bluetooth|\bbt\b|wi-?fi|яндекс\s+станц\w*)\b', title, re.I):
            attrs['wireless'] = 'Да'
        elif re.search(r'\b(проводн\w*|\busb\b|\bxlr\b|3\.5\s*(?:мм|mm))\b', title, re.I):
            attrs['wireless'] = 'Нет'

    # 33. Книги и канцелярия
    m_author_book = re.match(r'^\s*([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.(?:\s*[А-ЯЁ]\.)?(?:,\s*[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.(?:\s*[А-ЯЁ]\.)?)?)\s*:\s*\S+', title)
    is_books_stat = bool(m_author_book or re.search(
        r'\b(книга\b|роман\b|энциклопеди\w*|учебник\w*|комикс\w*|манга\b|раскраск\w*|пропис\w*|'
        r'бумага\s+(?!туалетн\w*|для\s+выпечки)|фотобумаг\w*|набор\s+для\s+фотопринтера|'
        r'тетрад\w*|блокнот\w*|скетчбук\w*|ежедневник\w*|альбом\s+для\s+рисования|дневник\s+школьн\w*|'
        r'текстовыделител\w*|набор\s+текстовыделител\w*|маркер\w*\s+(?:перманентн|для\s+доски|для\s+скетчинга|набор)|фломастер\w*|карандаш\w*|ручк\w*\s+(?:шариков|гелев|роллер|перьевая|набор)|шариков\w*\s+ручк\w*|'
        r'краск\w*\s+(?:акрилов\w*|акварельн\w*|гуашь|маслян\w*|пальчиков\w*)|гуашь\b|акварель\b|пластилин\w*|тесто\s+для\s+лепки|глина\s+полимерная|картин\w*\s+по\s+номерам|алмазн\w*\s+мозаик\w*|холст\s+на|фигурк\w*\s+гипсов\w*|гипсов\w*\s+фигурк\w*|'
        r'набор\s+(?:акриловых|цветных|гелевых|кистей|для\s+творчества|для\s+лепки|для\s+рисования|для\s+вышивания|для\s+опытов|для\s+скрапбукинга|для\s+создания|канцелярск\w*)|'
        r'папк\w*\s+(?:для\s+бумаг|с\s+файлами|регистратор|на\s+молнии|скоросшиватель)|папк\w*[- ](?:картотек\w*|регистратор\w*|скоросшивател\w*|конверт\w*|уголок)|'
        r'стикер\w*\s+бумажн\w*|блок\s+для\s+заметок|наклейк\w*|ластик\w*|точилк\w*|степлер\w*(?!\s+строительн)|скобы\s+для\s+степлера|дырокол\w*|клей[- ]карандаш|корректор\w*\s+канцелярск\w*|'
        r'пенал\w*|глобус\w*|калькулятор\w*|ламинатор\w*|уничтожител\w*\s+бумаг|шредер\w*|переплетчик\w*|artbox\s+)\b',
        title, re.I
    )) and not is_printer and not is_tool
    if is_books_stat:
        if m_author_book:
            attrs['type'] = 'Книги и учебная литература'
            attrs['author'] = m_author_book.group(1).strip()
        elif re.search(r'\b(бумага\s+|фотобумаг\w*|набор\s+для\s+фотопринтера)\b', title, re.I):
            attrs['type'] = 'Бумага и фотобумага'
        elif re.search(r'\b(тетрад\w*|блокнот\w*|скетчбук\w*|ежедневник\w*|альбом\s+для|дневник)\b', title, re.I):
            attrs['type'] = 'Тетради, блокноты и альбомы'
        elif re.search(r'\b(текстовыделител\w*|маркер\w*|фломастер\w*|карандаш\w*|ручк\w*|шариков\w*\s+ручк\w*)\b', title, re.I):
            attrs['type'] = 'Письменные принадлежности'
        elif re.search(r'\b(краск\w*|гуашь|акварель|пластилин\w*|тесто\s+для\s+лепки|глина|картин\w*\s+по\s+номерам|алмазн\w*\s+мозаик\w*|холст|раскраск\w*|фигурк\w*\s+гипсов\w*|гипсов\w*\s+фигурк\w*|набор\s+(?:акриловых|для\s+творчества|для\s+лепки|для\s+рисования|для\s+вышивания|для\s+опытов|для\s+скрапбукинга|для\s+создания))\b', title, re.I):
            attrs['type'] = 'Товары для творчества и рисования'
        elif re.search(r'\b(книга|роман|энциклопеди\w*|учебник\w*|комикс\w*|манга|пропис\w*)\b', title, re.I):
            attrs['type'] = 'Книги и учебная литература'
        else:
            attrs['type'] = 'Офисная и школьная канцелярия'

        m_pfmt = re.search(r'\b(a3|а3|a4|а4|a5|а5|a6|а6|10\s*[xх]\s*15)\b', title, re.I)
        if m_pfmt:
            f_raw = m_pfmt.group(1).upper().replace('А', 'A')
            attrs['paper_format'] = 'A6 (10x15)' if ('A6' in f_raw or '10' in f_raw) else f_raw

        m_sheets = re.search(r'\b(\d{1,3})\s*(?:лист\w*\b|л\.|\bл\b|шт\.?)', title, re.I)
        if m_sheets and 'pieces_count' not in attrs:
            attrs['pieces_count'] = f"{m_sheets.group(1)} шт."

    # 34. Зоотовары (кошки, собаки и другие питомцы)
    is_pet_item = bool(re.search(
        r'\b(корм\s+(?:для\s+)?(?:кошек|котят|собак|щенков|грызунов|птиц|рыб|kitten|adult|junior|sterilised)|'
        r'консервы\b.*\bдля\s+(?:взрослых\s+|стерилизованных\s+)?(?:кошек|котят|собак|щенков)|'
        r'сухой\s+корм|влажный\s+корм|корм\s+«?(?:felix|kitekat|whiskas|sheba|gourmet|purina|pro\s+plan|royal\s+canin|happy\s+cat|happy\s+dog|carny|animonda|monge|acana|orijen|brit|grandorf|pedigree|chappi|perfect\s+fit)»?|'
        r'наполнитель\s+для\s+кошачьего|когтеточк\w*|лежанк\w*\s+для|переноск\w*\s+для\s+(?:кошек|собак|животных)|ошейник\w*\s+для|поводок\s+для|шлейк\w*\s+для|лакомств\w*\s+для\s+(?:кошек|собак))\b',
        title, re.I
    ))
    if is_pet_item:
        if re.search(r'\b(наполнитель\w*|лоток\s+для|пеленк\w*\s+для\s+животных)\b', title, re.I):
            attrs['type'] = 'Наполнитель и гигиена'
        elif re.search(r'\b(лакомств\w*|витамин\w*|кости\s+жевательн\w*)\b', title, re.I):
            attrs['type'] = 'Лакомства и витамины'
        elif re.search(r'\b(когтеточк\w*|лежанк\w*|переноск\w*|ошейник\w*|поводок|шлейк\w*|миск\w*)\b', title, re.I):
            attrs['type'] = 'Аксессуары и уход'
        elif re.search(r'\b(влажн\w*\s+корм|пауч\w*|консерв\w*|желе|соус\w*|\b(?:75|80|85|100|400)\s*(?:г|гр)\b)\b', title, re.I) and not re.search(r'\bсухой\b', title, re.I):
            attrs['type'] = 'Влажный корм / Паучи'
        else:
            attrs['type'] = 'Сухой корм'

        if re.search(r'\b(кошек|котят|кошк\w*|котов|kitten|happy\s+cat|felix|kitekat|whiskas|sheba|gourmet|carny)\b', title, re.I):
            attrs['pet_type'] = 'Для кошек'
        elif re.search(r'\b(собак|щенк\w*|puppy|happy\s+dog|pedigree|chappi)\b', title, re.I):
            attrs['pet_type'] = 'Для собак'

        if re.search(r'\b(котят|щенк\w*|kitten|junior|puppy|до\s+12\s+мес|4-12\s+мес)\b', title, re.I):
            attrs['pet_age'] = 'Для котят / щенков (Junior)'
        elif re.search(r'\b(стерилизованн\w*|кастрированн\w*|sterilised|neutered)\b', title, re.I):
            attrs['pet_age'] = 'Для стерилизованных (Sterilised)'
        elif re.search(r'\b(\bvet\b|ветеринарн\w*|hepatic|renal|gastrointestinal|urinary|hypersensitivity|sensible)\b', title, re.I):
            attrs['pet_age'] = 'Чувствительное пищеварение / Диета'
        elif re.search(r'\b(взросл\w*|adult)\b', title, re.I):
            attrs['pet_age'] = 'Для взрослых (Adult)'

        m_pw_kg = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(?:кг|kg)\b', title, re.I)
        m_pw_g = re.search(r'\b(\d{2,4})\s*(?:г|гр|g)\b', title, re.I)
        if m_pw_kg:
            attrs['pack_weight'] = f"{m_pw_kg.group(1).replace(',', '.')} кг"
        elif m_pw_g:
            g_val = int(m_pw_g.group(1))
            if g_val >= 1000 and g_val % 100 == 0:
                attrs['pack_weight'] = f"{g_val / 1000:g} кг"
            else:
                attrs['pack_weight'] = f"{g_val} г"

    # 35. Дом и сад, освещение, текстиль и посуда
    is_home_kitchen = bool(re.search(
        r'\b(led\s+ламп\w*|светодиодн\w*\s+(?:ламп\w*|лент\w*|панел\w*|прожектор\w*)|ламп\w*\s+(?:светодиодн\w*|филаментн\w*|накаливания|энергосберегающ\w*|умн\w*)|'
        r'настольн\w*\s+(?:led\s+)?ламп\w*|[cс]ветильник\w*|люстр\w*|бра\b|торшер\w*|прожектор\w*|ночник\w*|ночной\s+[cс]ветильник|умн\w*\s+(?:светодиодн\w*\s+лент\w*|ламп\w*|розетк\w*|выключател\w*)|led[- ]панел\w*|осветител\w*\s+для|фонарик\w*|'
        r'сковород\w*|кастрюл\w*|ковш\s+|казан\w*|сотейник\w*|вок\b|wok\b|набор\s+посуды|термос\w*|термокружк\w*|бутылк\w*\s+для\s+воды|френч[- ]пресс|чайник\s+заварочн\w*|нож\s+кухонн\w*|набор\s+ножей|разделочн\w*\s+доск\w*|тарелк\w*|кружк\w*|бокал\w*|стакан\w*|форма\s+для\s+(?:выпечки|запекания)|контейнер\s+для\s+(?:пищевых|еды|хранения)|'
        r'постельн\w*\s+бель\w*|комплект\s+постельного|одеял\w*|подушк\w*(?!\s+безопасности)|плед\w*|покрывал\w*|простын\w*|наволочк\w*|полотенц\w*|штор\w*|занавеск\w*|ковер\b|коврик\s+(?:придверн|для\s+ванной|влаговпитывающ)|'
        r'вешалк\w*\s+(?:гардеробн\w*|для\s+одежды|напольн\w*|плечики)|корзин\w*\s+для\s+(?:белья|хранения)|коробк\w*\s+для\s+хранения|органайзер\w*|ролик\s+для\s+чистки\s+одежды|автоматик\w*\s+для\s+ворот|привод\s+для\s+ворот|'
        r'ароматическ\w*\s+(?:диффузор|свеч\w*|саше)|подарочн\w*\s+(?:пакет|коробк\w*|набор)|'
        r'шланг\s+(?:поливочн\w*|садов\w*|растягивающ\w*|сочащ\w*|капельн\w*)|садов\w*\s+(?:шланг\w*|тележк\w*|тачк\w*|душ|секатор|ножниц\w*|опрыскивател\w*|инвентар\w*|качел\w*|павильон|шатер|зонт)|'
        r'пистолет\w*[- ]распылител\w*|распылител\w*\s+для\s+(?:полива|воды|шланга)|дождевател\w*|оросител\w*|набор\s+для\s+(?:полива|капельного\s+полива)|катушк\w*\s+для\s+шланга|тележк\w*\s+для\s+шланга|коннектор\w*\s+для\s+шланга|адаптер\s+для\s+(?:крана|шланга|полива)|таймер\s+полива|'
        r'электрическ\w*\s+триммер\w*|аккумуляторн\w*\s+(?:триммер\w*|секатор\w*|опрыскивател\w*|цепн\w*\s+пил\w*|газонокосилк\w*|кусторез\w*)|бензинов\w*\s+(?:триммер\w*|газонокосилк\w*|мотокос\w*|бензопил\w*)|'
        r'секатор\w*|сучкорез\w*|высоторез\w*|лопат\w*\s+(?:совков|штыков|снегоуборочн|садов|универсальн)|грабли\b|веерн\w*\s+грабли|тяпк\w*|мотыг\w*|рыхлител\w*|корнеудалител\w*|плоскорез\w*|лейк\w*\s+садов|опрыскивател\w*|семена\s+|удобрен\w*|грунт\s+(?:для|универсальн|цветочн|торфян)|горшок\s+(?:для\s+цветов|цветочн)|кашпо\b|парник\w*|теплиц\w*|агроволокн\w*|спанбонд|укрывн\w*\s+материал|бордюрн\w*\s+лент\w*|садов\w*\s+сетк\w*|средств\w*\s+от\s+(?:насекомых|комаров|сорняков|вредителей|грызунов))\b',
        title, re.I
    )) and not is_kitchen and not is_furniture
    if is_home_kitchen:
        if re.search(r'\b(led\s+ламп\w*|светодиодн\w*\s+(?:ламп\w*|лент\w*|панел\w*|прожектор\w*)|ламп\w*\s+(?:светодиодн\w*|филаментн\w*|накаливания|умн\w*)|[cс]ветильник\w*|люстр\w*|бра\b|торшер\w*|прожектор\w*|ночник\w*|настольн\w*\s+(?:led\s+)?ламп\w*|умн\w*\s+(?:светодиодн\w*\s+лент\w*|ламп\w*)|led[- ]панел\w*|осветител\w*|фонарик\w*)\b', title, re.I):
            if re.search(r'\b([cс]ветильник\w*|люстр\w*|бра\b|торшер\w*|прожектор\w*|ночник\w*|настольн\w*\s+(?:led\s+)?ламп\w*|светодиодн\w*\s+(?:лент\w*|панел\w*)|led[- ]панел\w*|осветител\w*|фонарик\w*)\b', title, re.I):
                attrs['type'] = 'Светильники, люстры и LED-ленты'
            else:
                attrs['type'] = 'Светодиодные лампочки (LED)'
            m_base = re.search(r'\b(e27|e14|gu10|gu5\.3|g4|g9|g13|gx53)\b', title, re.I)
            if m_base:
                attrs['lamp_base'] = m_base.group(1).upper()
            m_kelv = re.search(r'\b([2-6]\d00)\s*[kк]\b', title, re.I)
            if m_kelv:
                k_int = int(m_kelv.group(1))
                if k_int <= 3300:
                    attrs['color_temp'] = f"Теплый свет ({k_int}K)"
                elif k_int <= 4700:
                    attrs['color_temp'] = f"Нейтральный свет ({k_int}K)"
                else:
                    attrs['color_temp'] = f"Холодный свет ({k_int}K)"
            m_lw = re.search(r'\b(\d{1,3}(?:[.,]\d)?)\s*(?:вт|w)\b', title, re.I)
            if m_lw and 'power' not in attrs:
                attrs['power'] = f"{m_lw.group(1).replace(',', '.')} Вт"
        elif re.search(r'\b(сковород\w*|вок\b|wok\b|сотейник\w*)\b', title, re.I):
            attrs['type'] = 'Сковороды, воки и сотейники'
        elif re.search(r'\b(кастрюл\w*|ковш|казан\w*|набор\s+посуды)\b', title, re.I):
            attrs['type'] = 'Кастрюли, ковши и наборы посуды'
        elif re.search(r'\b(термос\w*|термокружк\w*|бутылк\w*\s+для\s+воды|френч[- ]пресс|чайник\s+заварочн\w*)\b', title, re.I):
            attrs['type'] = 'Термосы, бутылки и заварники'
        elif re.search(r'\b(нож\s+кухонн\w*|набор\s+ножей|разделочн\w*\s+доск\w*|тарелк\w*|кружк\w*|бокал\w*|стакан\w*|форма\s+для|контейнер\s+для)\b', title, re.I):
            attrs['type'] = 'Кухонная утварь и сервировка'
        elif re.search(r'\b(постельн\w*\s+бель\w*|комплект\s+постельного|одеял\w*|подушк\w*|плед\w*|покрывал\w*|простын\w*|наволочк\w*|полотенц\w*|штор\w*|занавеск\w*|ковер|коврик)\b', title, re.I):
            attrs['type'] = 'Постельное белье, текстиль и ковры'
        elif re.search(r'\b(шланг|садов\w*|распылител\w*|дождевател\w*|оросител\w*|полив\w*|триммер\w*|газонокосилк\w*|кусторез\w*|секатор\w*|сучкорез\w*|высоторез\w*|лопат\w*|грабли|тяпк\w*|мотыг\w*|рыхлител\w*|корнеудалител\w*|плоскорез\w*|лейк\w*|опрыскивател\w*|семена|удобрен\w*|грунт|горшок|кашпо|парник\w*|теплиц\w*|агроволокн\w*|спанбонд|бордюрн\w*|от\s+(?:насекомых|комаров|сорняков|вредителей|грызунов))\b', title, re.I):
            if 'type' not in attrs:
                attrs['type'] = 'Садовый инвентарь, полив и растения'
            if 'power_supply' not in attrs:
                if re.search(r'\bаккумуляторн\w*\b', title, re.I):
                    attrs['power_supply'] = 'Аккумуляторный'
                elif re.search(r'\b(электрическ\w*|сетев\w*|\d{3,4}\s*(?:вт|w))\b', title, re.I):
                    attrs['power_supply'] = 'Сетевой (220 В)'
                elif re.search(r'\bбензинов\w*\b', title, re.I):
                    attrs['power_supply'] = 'Бензиновый'
        else:
            attrs['type'] = 'Хранение, декор и хозтовары'

        m_dcm = re.search(r'\b(14|16|18|20|22|24|26|28|30|32)\s*(?:см|cm)\b', title, re.I)
        if m_dcm:
            attrs['diameter_cm'] = f"{m_dcm.group(1)} см"

        if 'material' not in attrs:
            if re.search(r'\bчугун\w*\b', title, re.I):
                attrs['material'] = 'Чугун'
            elif re.search(r'\b(нержавеющ\w*|сталь\w*)\b', title, re.I):
                attrs['material'] = 'Нержавеющая сталь'
            elif re.search(r'\bалюмин\w*\b', title, re.I):
                attrs['material'] = 'Алюминий'
            elif re.search(r'\b(стекл\w*|хрустал\w*)\b', title, re.I):
                attrs['material'] = 'Стекло'
            elif re.search(r'\b(керамик\w*|фарфор\w*)\b', title, re.I):
                attrs['material'] = 'Керамика / Фарфор'
            elif re.search(r'\b(хлопок|сатин|бязь|поплин|перкаль)\b', title, re.I):
                attrs['material'] = 'Хлопок / Сатин'

    # 36. Бытовая техника для дома, климата и ухода за одеждой
    is_home_appliance = bool(re.search(
        r'\b(утюг\w*|отпаривател\w*|парогенератор\w*|гладильн\w*\s+(?:доск\w*|систем\w*)|машинк\w*\s+для\s+удаления\s+катышков|'
        r'увлажнител\w*\s+воздуха|очистител\w*\s+воздуха|мойк\w*\s+воздуха|осушител\w*\s+воздуха|'
        r'водонагревател\w*|бойлер\w*|обогревател\w*|конвектор\w*|тепловентилятор\w*|маслян\w*\s+радиатор\w*|теплов\w*\s+пушк\w*|'
        r'вентилятор\s+(?:напольн\w*|настольн\w*|колонн\w*)|швейн\w*\s+машин\w*|оверлок\w*)\b',
        title, re.I
    ))
    if is_home_appliance:
        if re.search(r'\bутюг\w*\b', title, re.I):
            attrs['type'] = 'Утюг'
        elif re.search(r'\b(отпаривател\w*|парогенератор\w*)\b', title, re.I):
            attrs['type'] = 'Отпариватель / Парогенератор'
        elif re.search(r'\b(гладильн\w*\s+доск\w*|машинк\w*\s+для\s+удаления\s+катышков)\b', title, re.I):
            attrs['type'] = 'Уход за одеждой и глажка'
        elif re.search(r'\b(увлажнител\w*|очистител\w*\s+воздуха|мойк\w*\s+воздуха|осушител\w*)\b', title, re.I):
            attrs['type'] = 'Увлажнитель / Очиститель воздуха'
        elif re.search(r'\b(водонагревател\w*|бойлер\w*)\b', title, re.I):
            attrs['type'] = 'Водонагреватель (Бойлер)'
        elif re.search(r'\b(обогревател\w*|конвектор\w*|тепловентилятор\w*|маслян\w*\s+радиатор\w*|теплов\w*\s+пушк\w*|вентилятор)\b', title, re.I):
            attrs['type'] = 'Обогреватель / Вентилятор'
        elif re.search(r'\b(швейн\w*\s+машин\w*|оверлок\w*)\b', title, re.I):
            attrs['type'] = 'Швейная машина / Оверлок'

        m_hap_w = re.search(r'\b([1-3]\d{2,3})\s*(?:вт|w)\b', title, re.I)
        if m_hap_w and 'power' not in attrs:
            attrs['power'] = f"{m_hap_w.group(1)} Вт"

    # 37. Продукты питания, Напитки, Молочные продукты и Мясо (FMCG / Stage P09)
    is_fmcg = bool(re.search(
        r'\b(шоколад\w*|конфет\w*|батончик\w*|мармелад\w*|зефир\w*|пастил\w*|халв\w*|печень\w*|вафл\w*|пряник\w*|круассан\w*|кекс\w*|рулет\w*\s+бисквитн\w*|бисквит\w*|торт\w*|пирожн\w*|'
        r'чипс\w*|сухарик\w*|попкорн\w*|орех\w*|фисташк\w*|миндал\w*|кешью|фундук|арахис\w*|семечк\w*|сухофрукт\w*|чернослив|курага|изюм|'
        r'макарон\w*|паста\s+(?:barilla|макфа|султан|макаронн\w*|томатн\w*|арахисов\w*|шоколадн\w*|nutella)|спагетти|лапш\w*|вермишел\w*|круп\w*|гречк\w*|рис\s+|овсян\w*|хлопья|мюсли|завтрак\s+сухой|мука\s+|сахар\s+|соль\s+(?:пищевая|морская|йодированная)|'
        r'масло\s+(?:подсолнечн\w*|оливков\w*|растительн\w*|кукурузн\w*|кунжутн\w*|льнян\w*|сливочн\w*|топленое)|майонез\w*|кетчуп\w*|соус\w*|горчиц\w*|уксус\w*|приправ\w*|специ\w*|перец\s+(?:черный|красный|молотый|горошком)|бульон\w*|'
        r'консерв\w*|тушенк\w*|говядин\w*\s+тушен\w*|кукуруз\w*\s+консерв\w*|горошек\s+зелен\w*|фасоль\s+консерв\w*|оливки\b|маслин\w*|шпрот\w*|сардин\w*|тунец|сайра|килька|икра\s+|мед\s+натуральн\w*|джем\w*|варень\w*|сгущенк\w*|молоко\s+сгущенн\w*|'
        r'молоко\s+|кефир\w*|ряженк\w*|айран\w*|тан\b|кумыс\w*|шубат\w*|сливки\s+|сметан\w*|творог\w*|творожн\w*|сырок\s+|йогурт\w*|биойогурт\w*|сыр\s+|камамбер|моцарелла|пармезан|гауда|чеддер|тильзитер|ламбер|сулугуни|брынза|фета|маскарпоне|рикотта|маргарин\w*|спред\w*|'
        r'колбас\w*|сосиск\w*|сардельк\w*|шпикачк\w*|ветчин\w*|бекон\w*|карбонад\w*|грудинк\w*|корейк\w*|сервелат\w*|салями|деликатес\w*|паштет\w*|пельмен\w*|манты|вареник\w*|котлет\w*|наггетс\w*|фарш\s+|стейк\w*|филе\s+(?:курин\w*|цыпленк\w*|индейк\w*|рыб\w*|семги|лосося|минтая)|тушк\w*\s+цыпленк\w*|бедро\s+цыпленк\w*|голень\s+цыпленк\w*|крылышк\w*|говядин\w*|конин\w*|баранин\w*|свинин\w*|куриц\w*|индейк\w*|утк\w*\s+(?:охлажден|заморожен|тушка|филе)|казы|шужук|жая|'
        r'вода\s+|напиток\w*|сок\s+|нектар\w*|морс\w*|компот\w*|лимонад\w*|кола\b|coca[- ]cola|pepsi|fanta|sprite|mirinda|7up|энергетическ\w*\s+напиток|энергетик\w*|квас\w*|холодный\s+чай|чай\s+|кофе\s+|какао\w*|цикори\w*)',
        title, re.I
    )) and not is_pet_item and not is_perfume_beauty and not is_kitchen and not is_home_kitchen and not is_furniture and not is_apparel and not is_footwear and not is_tool
    if is_fmcg:
        if re.search(r'\b(чай\s+|холодный\s+чай)', title, re.I) and not re.search(r'\bчайник\b', title, re.I):
            attrs['type'] = 'Чай'
        elif re.search(r'\b(кофе\s+|какао\w*|цикори\w*)', title, re.I) and not re.search(r'\b(кофеварк|кофемашин|кофемолк)\b', title, re.I):
            attrs['type'] = 'Кофе и какао'
        elif re.search(r'\b(вода\s+|минеральн\w*\s+вод\w*|боржоми|borjomi|samal|tassay|тассай|асем[- ]ай|сарыагаш|ессентуки|нарзан|evian|bonaqua|aqua\s+minerale)', title, re.I):
            attrs['type'] = 'Вода питьевая и минеральная'
        elif re.search(r'\b(сок\s+|нектар\w*|морс\w*|компот\w*|сокосодержащ\w*)', title, re.I):
            attrs['type'] = 'Соки, нектары и морсы'
        elif re.search(r'\b(напиток\w*|лимонад\w*|кола\b|coca[- ]cola|pepsi|fanta|sprite|mirinda|7up|энергетик\w*|энергетическ\w*|квас\w*)', title, re.I):
            attrs['type'] = 'Газированные и энергетические напитки'
        elif re.search(r'\b(сыр\s+|камамбер|моцарелла|пармезан|гауда|чеддер|тильзитер|ламбер|сулугуни|брынза|фета|маскарпоне|рикотта|hochland)', title, re.I):
            attrs['type'] = 'Сыр'
        elif re.search(r'\b(молоко\s+|сливки\s+|сгущенк\w*)', title, re.I):
            attrs['type'] = 'Молоко и сливки'
        elif re.search(r'\b(йогурт\w*|биойогурт\w*|творог\w*|творожн\w*|сырок\s+|сметан\w*|кефир\w*|ряженк\w*|айран\w*|тан\b|кумыс\w*|шубат\w*)', title, re.I):
            attrs['type'] = 'Кисломолочные продукты, творог и йогурты'
        elif re.search(r'\b(масло\s+сливочн\w*|масло\s+топленое|маргарин\w*|спред\w*)', title, re.I):
            attrs['type'] = 'Масло сливочное и маргарин'
        elif re.search(r'\b(колбас\w*|сосиск\w*|сардельк\w*|шпикачк\w*|ветчин\w*|бекон\w*|карбонад\w*|грудинк\w*|корейк\w*|сервелат\w*|салями|деликатес\w*|паштет\w*|казы|шужук|жая)', title, re.I):
            attrs['type'] = 'Колбасы, сосиски и деликатесы'
        elif re.search(r'\b(пельмен\w*|манты|вареник\w*|котлет\w*|наггетс\w*|фарш\s+|стейк\w*|филе\s+|тушк\w*|бедро\s+|голень\s+|крылышк\w*|говядин\w*|конин\w*|баранин\w*|свинин\w*|куриц\w*|индейк\w*)', title, re.I):
            attrs['type'] = 'Мясо, птица и полуфабрикаты'
        elif re.search(r'\b(шоколад\w*|конфет\w*|батончик\w*|мармелад\w*|зефир\w*|пастил\w*|халв\w*|печень\w*|вафл\w*|пряник\w*|круассан\w*|кекс\w*|рулет\w*|бисквит\w*|торт\w*|пирожн\w*|паста\s+шоколадн\w*|nutella|мед\s+|джем\w*|варень\w*)', title, re.I):
            attrs['type'] = 'Сладости, шоколад и выпечка'
        elif re.search(r'\b(макарон\w*|паста\s+|спагетти|лапш\w*|вермишел\w*|круп\w*|гречк\w*|рис\s+|овсян\w*|хлопья|мюсли|завтрак\s+сухой|мука\s+|сахар\s+|соль\s+)', title, re.I):
            attrs['type'] = 'Бакалея, макароны и крупы'
        elif re.search(r'\b(масло\s+(?:подсолнечн|оливков|растительн|кукурузн)|майонез\w*|кетчуп\w*|соус\w*|горчиц\w*|уксус\w*|приправ\w*|специ\w*|перец\s+|бульон\w*)', title, re.I):
            attrs['type'] = 'Масла, соусы и специи'
        elif re.search(r'\b(чипс\w*|сухарик\w*|попкорн\w*|орех\w*|фисташк\w*|миндал\w*|кешью|фундук|арахис\w*|семечк\w*|сухофрукт\w*|чернослив|курага|изюм)', title, re.I):
            attrs['type'] = 'Снеки, чипсы и орехи'
        elif re.search(r'\b(консерв\w*|тушенк\w*|кукуруз\w*|горошек\s+|фасоль\s+|оливки\b|маслин\w*|шпрот\w*|сардин\w*|тунец|сайра|килька|икра\s+)', title, re.I):
            attrs['type'] = 'Консервы и соленья'

        m_fat = re.search(r'\b(\d{1,2}(?:[.,]\d)?)\s*%', title)
        if m_fat:
            f_val = float(m_fat.group(1).replace(',', '.'))
            if 0.1 <= f_val <= 85.0:
                attrs['fat_content'] = f"{m_fat.group(1).replace(',', '.') }%"

        m_f_l = re.search(r'\b([0-9]+(?:[.,][0-9]+)?)\s*(?:л|l|литр\w*)\b', title, re.I)
        m_f_ml = re.search(r'\b(\d{2,4})\s*(?:мл|ml)\b', title, re.I)
        if m_f_l:
            l_val = float(m_f_l.group(1).replace(',', '.'))
            if 0.1 <= l_val <= 25.0:
                attrs['volume_ml'] = f"{m_f_l.group(1).replace(',', '.')} л"
        elif m_f_ml:
            ml_int = int(m_f_ml.group(1))
            if ml_int >= 1000 and ml_int % 100 == 0:
                attrs['volume_ml'] = f"{ml_int / 1000:g} л"
            else:
                attrs['volume_ml'] = f"{ml_int} мл"

        m_f_kg = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(?:кг|kg)\b', title, re.I)
        m_f_g = re.search(r'\b(\d{1,4}(?:[.,]\d)?)\s*(?:г|гр|g)\b', title, re.I)
        if m_f_kg:
            attrs['pack_weight'] = f"{m_f_kg.group(1).replace(',', '.')} кг"
        elif m_f_g:
            g_num = float(m_f_g.group(1).replace(',', '.'))
            if g_num >= 1000 and g_num % 100 == 0:
                attrs['pack_weight'] = f"{g_num / 1000:g} кг"
            elif g_num >= 5:
                attrs['pack_weight'] = f"{int(g_num) if g_num.is_integer() else g_num} г"

        m_f_pcs = re.search(r'\b(\d{1,3})\s*(?:пакет\w*|пирамид\w*|капсул\w*|шт\.?)\b', title, re.I)
        if m_f_pcs and 'pieces_count' not in attrs:
            attrs['pieces_count'] = f"{m_f_pcs.group(1)} шт."

    # 38. Лекарства, витамины и аптечные товары
    is_pharmacy = bool(re.search(
        r'\b(таблетк\w*|табл\.?\b|капсул\w*|капс\.?\b|суппозитори\w*|свечи\s+ректальн\w*|ампул\w*|амп\.?\b|'
        r'раствор\s+(?:для|инъекц|инфуз|внутр|наружн|местн)|сироп\s+(?:от|для|\d)|суспензи\w*|капли\s+(?:глазн|назальн|ушн|для\s+приема|в\s+нос)|спрей\s+(?:назальн|для\s+горла|для\s+полости\s+рта)|'
        r'мазь\s+(?:для|от|\d|%)|гель\s+(?:для\s+наружного|венотонизирующ|обезболивающ|противовоспалительн)|пастилк\w*\s+для\s+рассасывания|леденц\w*\s+от\s+кашля|порошок\s+для\s+приготовления|'
        r'витамин\w*|мультивитамин\w*|омега[- ]?3|\bбад\b|биологически\s+активн\w*|пластыр\w*\s+(?:бактерицидн|перцов|медицинск)|бинт\s+(?:марлев|эластичн|стерильн)|шприц\w*|жгут\s+кровоостанавливающ|\b\d+\s*(?:мг|мкг|ме)\b)\b',
        title, re.I
    )) and not is_fmcg and not is_perfume_beauty and not re.search(r'\b(для\s+посудомоечн|для\s+стирк|для\s+чистки|кофе\s+в\s+капсулах)\b', title, re.I)
    if is_pharmacy:
        if re.search(r'\b(таблетк\w*|табл\.?\b|пастилк\w*|леденц\w*|драже)\b', title, re.I):
            attrs['type'] = 'Таблетки и пастилки'
        elif re.search(r'\b(капсул\w*|капс\.?\b)\b', title, re.I):
            attrs['type'] = 'Капсулы'
        elif re.search(r'\b(раствор|ампул\w*|амп\.?\b|капли|спрей|сироп|суспензи\w*|настойк\w*|экстракт)\b', title, re.I):
            attrs['type'] = 'Растворы, капли, спреи и сиропы'
        elif re.search(r'\b(мазь|гель|крем|линимент|суппозитори\w*|свечи)\b', title, re.I):
            attrs['type'] = 'Мази, гели и суппозитории'
        elif re.search(r'\b(порошок|гранул\w*|пакетик\w*|саше)\b', title, re.I):
            attrs['type'] = 'Порошки и гранулы (саше)'
        elif re.search(r'\b(пластыр\w*|бинт|марл\w*|ват\w*|шприц\w*|салфетк\w*\s+спиртов)\b', title, re.I):
            attrs['type'] = 'Перевязочные материалы и изделия'
        else:
            attrs['type'] = 'Витамины и аптечные средства'

        m_dos = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(мг/мл|мг|мкг|ме|mg|mcg|iu)\b', title, re.I)
        if m_dos:
            unit_map = {'mg': 'мг', 'mcg': 'мкг', 'iu': 'МЕ', 'ме': 'МЕ', 'мг': 'мг', 'мкг': 'мкг', 'мг/мл': 'мг/мл'}
            u_str = unit_map.get(m_dos.group(2).lower(), m_dos.group(2))
            attrs['dosage'] = f"{m_dos.group(1).replace(',', '.')} {u_str}"

        m_ph_pcs = (
            re.search(r'(?:№|n|no)\s*(\d{1,3})\b', title, re.I)
            or re.search(r'\b(\d{1,3})\s*(?:табл\w*|капс\w*|пакет\w*|саше|амп\w*|супп\w*|шт\.?)\b', title, re.I)
        )
        if m_ph_pcs:
            attrs['pieces_count'] = f"{m_ph_pcs.group(1)} шт."

        m_ph_ml = re.search(r'\b(\d{1,4}(?:[.,]\d)?)\s*(?:мл|ml)\b', title, re.I)
        if m_ph_ml and 'volume_ml' not in attrs:
            attrs['volume_ml'] = f"{m_ph_ml.group(1).replace(',', '.')} мл"

    # 39. Бытовая химия и хозтовары
    is_hchem = bool(re.search(
        r'\b(стиральн\w*\s+порошок|порошок\s+(?:для\s+стирки|tide|ariel|persil|миф|ушастый\s+нянь|бимакс|bimax|лоск|losk|сарма)|гель\s+для\s+стирки|капсул\w*\s+для\s+стирки|кондиционер\s+для\s+белья|концентрированн\w*\s+кондиционер|ополаскивател\w*\s+(?:для\s+(?:белья|посудомоечн)|flo)|пятновыводител\w*|отбеливател\w*|'
        r'средств\w*\s+(?:для\s+(?:мытья|посудомоечн\w*|чистки|сантехники|унитаза|труб|ковров|духовок|плит|стирки|бесконтактной|санитарной|напольного|очистки|удаления|ухода)|чистящ\w*|моющее|дезинфицирующ\w*|grass\b|karcher\b)|таблетк\w*\s+для\s+посудомоечн\w*|соль\s+для\s+посудомоечн\w*|'
        r'чистящ\w*\s+(?:средств\w*|порошок|паста|крем|гель|спрей)|дезинфицирующ\w*\s+средств\w*|антижир|устранител\w*\s+запахов|освежител\w*\s+воздуха|сменн\w*\s+баллон|блок\s+bref|биоактиватор\w*|'
        r'туалетн\w*\s+бумаг\w*|бумаг\w*\s+туалетн\w*|бумажн\w*\s+(?:полотенц\w*|салфетк\w*|платочк\w*)|полотенц\w*\s+бумажн\w*|влажн\w*\s+салфетк\w*(?!\s+детск)|салфетк\w*\s+(?:selpak|карина|бумажн\w*|влажн\w*|рута|zewa|плюш)|мешк\w*\s+для\s+мусора|пакет\w*\s+для\s+мусора|мусорн\w*\s+(?:бак|ведро|контейнер)|'
        r'губк\w*\s+для\s+(?:посуды|обуви|тела)|салфетк\w*\s+(?:из\s+микрофибры|вискозн\w*|для\s+уборки|целлюлозн\w*)|тряпк\w*\s+для\s+пола|швабр\w*(?!\s+паров)|ведро\s+(?:для\s+уборки|с\s+отжимом|пластиковое|оцинкованное)|таз\s+(?:пластиков\w*|квадрат)|щетк\w*\s+для\s+(?:уборки|подметания|одежды|обуви|мытья|унитаза)|ершик\s+для\s+унитаза|совок\s+для\s+мусора|'
        r'фольг\w*\s+алюминиев\w*|пергамент\s+для\s+выпечки|бумага\s+для\s+выпечки|рукав\s+для\s+запекания|пищев\w*\s+пленк\w*|пакет\w*\s+(?:фасовочн\w*|для\s+заморозки|для\s+льда|с\s+замком)|перчатк\w*\s+(?:хозяйственн\w*|нитрилов\w*|латексн\w*|резинов\w*)|прищепк\w*|сушилк\w*\s+для\s+(?:белья|посуды))\b',
        title, re.I
    )) and not is_vacuum and not is_kitchen
    if is_hchem:
        if re.search(r'\b(стиральн\w*\s+порошок|порошок\s+(?:для\s+стирки|tide|ariel|persil|миф|ушастый|бимакс|bimax|лоск|losk|сарма)|гель\s+для\s+стирки|капсул\w*\s+для\s+стирки|кондиционер\s+для\s+белья|концентрированн\w*\s+кондиционер|ополаскивател\w*\s+(?:для\s+белья|flo)|пятновыводител\w*|отбеливател\w*|средств\w*\s+для\s+стирки)\b', title, re.I):
            attrs['type'] = 'Средства для стирки и ухода за бельем'
        elif re.search(r'\b(для\s+мытья\s+посуды|для\s+посудомоечн\w*)\b', title, re.I):
            attrs['type'] = 'Средства для мытья посуды и ПММ'
        elif re.search(r'\b(чистящ\w*|дезинфицирующ\w*|антижир|средств\w*\s+(?:для|grass|karcher)|освежител\w*\s+воздуха|сменн\w*\s+баллон|блок\s+bref|биоактиватор\w*)\b', title, re.I):
            attrs['type'] = 'Чистящие средства и освежители'
        elif re.search(r'\b(туалетн\w*\s+бумаг\w*|бумаг\w*\s+туалетн\w*|бумажн\w*\s+(?:полотенц|салфетк|платочк)|полотенц\w*\s+бумажн\w*|влажн\w*\s+салфетк\w*|салфетк\w*\s+(?:selpak|карина|рута|zewa|плюш))\b', title, re.I):
            attrs['type'] = 'Туалетная бумага и салфетки'
        elif re.search(r'\b(мешк\w*\s+для\s+мусора|пакет\w*\s+для\s+мусора|фольг\w*|пергамент|бумага\s+для\s+выпечки|рукав\s+для\s+запекания|пищев\w*\s+пленк\w*|пакет\w*\s+(?:фасовочн|для\s+заморозки|для\s+льда|с\s+замком))\b', title, re.I):
            attrs['type'] = 'Пакеты, пленка и фольга'
        else:
            attrs['type'] = 'Инвентарь для уборки и хозтовары'

        m_hc_l = re.search(r'\b([0-9]+(?:[.,][0-9]+)?)\s*(?:л|l|литр\w*)\b', title, re.I)
        m_hc_ml = re.search(r'\b(\d{2,4})\s*(?:мл|ml)\b', title, re.I)
        if m_hc_l:
            attrs['volume_ml'] = f"{m_hc_l.group(1).replace(',', '.')} л"
        elif m_hc_ml:
            ml_v = int(m_hc_ml.group(1))
            attrs['volume_ml'] = f"{ml_v / 1000:g} л" if (ml_v >= 1000 and ml_v % 100 == 0) else f"{ml_v} мл"

        m_hc_kg = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(?:кг|kg)\b', title, re.I)
        m_hc_g = re.search(r'\b(\d{2,4})\s*(?:г|гр|g)\b', title, re.I)
        if m_hc_kg:
            attrs['pack_weight'] = f"{m_hc_kg.group(1).replace(',', '.')} кг"
        elif m_hc_g:
            g_v = int(m_hc_g.group(1))
            attrs['pack_weight'] = f"{g_v / 1000:g} кг" if (g_v >= 1000 and g_v % 100 == 0) else f"{g_v} г"

        m_hc_pcs = re.search(r'\b(\d{1,3})\s*(?:шт\.?|рулон\w*|сло[яй]|табл\w*|капсул\w*)\b', title, re.I)
        if m_hc_pcs and 'pieces_count' not in attrs:
            attrs['pieces_count'] = f"{m_hc_pcs.group(1)} шт."

    # 40. Товары для малышей, Детское питание, Коляски и автокресла, Детская комната
    is_baby_stroller_kids = bool(re.search(
        r'\b(молочн\w*\s+смес\w*|смесь\s+(?:nan|nestogen|nutrilon|similac|kabrita|малютка|friso|bellakt|pediasure|малоежка)|молочко\s+(?:nan|nestogen|nutrilon|малютка)|'
        r'пюре\s+(?:фрутоняня|агуша|гербер|gerber|бабушкино\s+лукошко|сады\s+придонья|спеленок|тема|hipp|хейнц|heinz|детск\w*|фруктов\w*|овощн\w*|мясн\w*)|каша\s+(?:молочная|безмолочная|детская|фрутоняня|агуша|нестле|nestle|хейнц|heinz|малютка|kabrita|флер\s+альпин)|'
        r'бутылочк\w*\s+для\s+кормления|пустышк\w*|соск\w*\s+(?:для\s+бутылочки|силиконов\w*|латексн\w*)|поильник\w*|непроливайк\w*|ниблер\w*|нагрудник\w*|слюнявчик\w*|молокоотсос\w*|стерилизатор\s+для|подогревател\w*\s+для\s+бутылочек|пакет\w*\s+для\s+грудного\s+молока|'
        r'коляск\w*(?!\s+.*для\s+кук)|автокресл\w*|автолюльк\w*|бустер\s+автомобильн\w*|эргорюкзак\w*|слинг\s+для|ходунк\w*\s+детск\w*|манеж\s+детск\w*|шезлонг\s+детск\w*|качел\w*\s+(?:детск\w*|электронн\w*)|стульчик\s+для\s+кормления|'
        r'погремушк\w*|прорезывател\w*|грызунок|развивающ\w*\s+коврик|мобиль\s+на\s+кроватку|сортер\w*|пирамидк\w*|бизиборд\w*|каталка[- ]толокар|толокар\w*|беговел\w*|самокат\s+(?:детск\w*|трехколесн\w*)|велосипед\s+(?:детск\w*|трехколесн\w*)|санки|снегокат\w*|тюбинг\w*|ватрушк\w*|'
        r'детск\w*\s+(?:кроват\w*|тахт\w*|диван\w*|кресл\w*|стульчик|столик|матрас|подушк\w*|одеял\w*|покрывал\w*|комплект\s+постельного|горшок|ванночк\w*)|'
        r'кровать\s+двухъярусн\w*|askona\s+kids|набор\s+детской\s+посуды)\b',
        title, re.I
    ))
    if is_baby_stroller_kids:
        if re.search(r'\b(молочн\w*\s+смес\w*|смесь\s+|молочко\s+(?:nan|nestogen|nutrilon|малютка)|pediasure|малоежка)\b', title, re.I):
            attrs['type'] = 'Молочные смеси'
        elif re.search(r'\b(пюре\s+|каша\s+(?:молочная|безмолочная|детская|фрутоняня|агуша|нестле|nestle|хейнц|heinz|малютка))\b', title, re.I):
            attrs['type'] = 'Детские пюре и каши'
        elif re.search(r'\b(бутылочк\w*|пустышк\w*|соск\w*|поильник\w*|непроливайк\w*|ниблер\w*|нагрудник\w*|слюнявчик\w*|молокоотсос\w*|стерилизатор|подогревател\w*|пакет\w*\s+для\s+грудного|набор\s+детской\s+посуды)\b', title, re.I):
            attrs['type'] = 'Бутылочки, пустышки и кормление'
        elif re.search(r'\b(автокресл\w*|автолюльк\w*|бустер)\b', title, re.I):
            attrs['type'] = 'Автокресло / Автолюлька'
        elif re.search(r'\b(коляск\w*(?!\s+.*для\s+кук)|эргорюкзак\w*|слинг)\b', title, re.I):
            if re.search(r'\b(3\s*в\s*1|2\s*в\s*1|трансформер)\b', title, re.I):
                attrs['type'] = 'Коляска 2 в 1 / 3 в 1'
            elif re.search(r'\b(прогулочн\w*|трость)\b', title, re.I):
                attrs['type'] = 'Прогулочная коляска'
            else:
                attrs['type'] = 'Детская коляска / Аксессуары'
        elif re.search(r'\b(каталка[- ]толокар|толокар\w*|беговел\w*|самокат|велосипед|санки|снегокат\w*|тюбинг\w*|ватрушк\w*|ходунк\w*)\b', title, re.I):
            attrs['type'] = 'Детский транспорт и ходунки'
        elif re.search(r'\b(детск\w*\s+(?:кроват\w*|тахт\w*|диван\w*|кресл\w*|стульчик|столик|матрас|подушк\w*|одеял\w*|покрывал\w*|комплект\s+постельного)|кровать\s+двухъярусн\w*|askona\s+kids|манеж|шезлонг|качел\w*|стульчик\s+для\s+кормления)\b', title, re.I):
            attrs['type'] = 'Детская мебель, кроватки и текстиль'
        elif re.search(r'\b(погремушк\w*|прорезывател\w*|грызунок|развивающ\w*\s+коврик|мобиль|сортер\w*|пирамидк\w*|бизиборд\w*)\b', title, re.I):
            attrs['type'] = 'Игрушки для малышей и развитие'
        else:
            attrs['type'] = 'Уход за малышом и купание'

        m_age = re.search(r'\b(?:с\s*|от\s*)(\d{1,2})\s*(?:мес\w*|\+\s*мес)\b|\b(0|3|6|12|18)\+\s*(?:мес)?\b|\b(\d{1,2})\s*\+\s*(?:лет|года|г\.)?', title, re.I)
        if m_age:
            if m_age.group(1):
                attrs['age_from'] = f"с {m_age.group(1)} мес."
            elif m_age.group(2):
                attrs['age_from'] = f"с {m_age.group(2)} мес."
            elif m_age.group(3):
                attrs['age_from'] = f"{m_age.group(3)}+ лет"
        elif re.search(r'\bс\s+рождения\b', title, re.I):
            attrs['age_from'] = 'с рождения (0+)'

        m_bk_g = re.search(r'\b(\d{2,4})\s*(?:г|гр|g)\b', title, re.I)
        m_bk_ml = re.search(r'\b(\d{2,4})\s*(?:мл|ml)\b', title, re.I)
        if m_bk_g and 'pack_weight' not in attrs:
            gv = int(m_bk_g.group(1))
            attrs['pack_weight'] = f"{gv / 1000:g} кг" if (gv >= 1000 and gv % 100 == 0) else f"{gv} г"
        if m_bk_ml and 'volume_ml' not in attrs:
            attrs['volume_ml'] = f"{m_bk_ml.group(1)} мл"

    # 41. Автотовары и автоэлектроника
    is_auto_item = bool(re.search(
        r'\b(видеорегистратор\w*|радар[- ]детектор\w*|комбо[- ]устройств\w*|автомагнитол\w*|магнитол\w*\s+автомобильн\w*|автоакустик\w*|колонк\w*\s+автомобильн\w*|сабвуфер\s+|усилител\w*\s+автомобильн\w*|carplay\b|автосканер\w*|'
        r'автомобильн\w*\s+(?:компрессор\w*|насос|пылесос|держател\w*|инвертор\w*|преобразовател\w*|зарядн\w*|холодильник|видеорегистратор|домкрат)|компрессор\s+автомобильн\w*|держател\w*\s+(?:автомобильн\w*|в\s+авто|на\s+торпедо|в\s+воздуховод)|car\s+mount\b|устройств\w*\s+зарядн\w*\s+автомобильн\w*|'
        r'пуско[- ]зарядн\w*\s+устройств\w*|пусков\w*\s+устройств\w*|бустер\s+пусков\w*|провода\s+прикуривания|домкрат\w*\s+(?:гидравлическ\w*|подкатн\w*|ромбическ\w*|бутылочн\w*)|'
        r'моторн\w*\s+масл\w*|масло\s+(?:моторн\w*|трансмиссионн\w*)|антифриз\w*|тосол\w*|стеклоомывател\w*|жидкость\s+.*стеклоомывающ\w*|незамерзайк\w*|автошампунь|полироль\s+(?:для\s+(?:кузова|пластика|панели|шин)|восковая)|чернител\w*\s+шин|чернение\s+(?:шин|резины)|очистител\w*\s+(?:карбюратора|тормозов|салона|кузова|битумных|стекол|двигателя|кондиционера)|размораживател\w*\s+замков|вд[- ]?40|wd[- ]?40|'
        r'амортизатор\w*|стойк\w*\s+стабилизатора|втулк\w*\s+стабилизатора|тяг\w*\s+стабилизатора|сайлентблок\w*|рычаг\w*\s+подвески|пружин\w*\s+подвески|опор\w*\s+(?:амортизатора|двигателя|шаровая|стойки|stellox|patron|febest)|шаров\w*\s+опор\w*|рулев\w*\s+(?:наконечник|тяга|рейка)|'
        r'тормозн\w*\s+(?:колодк\w*|диск\w*|барабан\w*|шланг\w*|цилиндр)|ремкомплект\s+(?:суппорта|тормозн|рулев|frenkit)|frenkit\s+ремкомплект|трос\s+(?:стояночного|ручника|сцепления|газа)|patron\s+(?:трос|опора|тяга|рычаг|стойка|шаровая|шрус)|stellox\s+(?:опора|стойка|сайлентблок|рычаг|тяга)|febest\s+|formpart\s+рычаг|'
        r'фильтр\s+(?:маслян\w*|воздушн\w*|салон\w*|топливн\w*|трансмиссионн\w*|акпп)|(?:трансмиссионн\w*|воздушн\w*|маслян\w*|топливн\w*|салонн\w*)\s+фильтр|свеч\w*\s+зажигания|катушк\w*\s+зажигания|датчик\s+(?:включения|положения|кислорода|абс|abs|температуры|давления|детонации|расхода)|насос\s+(?:электрический\s+топливной|топливный|водяной|гура)|патрубок\s+|прокладк\w*\s+(?:гбц|клапанной|коллектора|поддона)|комплект\s+(?:прокладок|грм|сцепления)|ступиц\w*|подшипник\s+ступиц\w*|привод\s+в\s+сборе|шрус\b|'
        r'щетк\w*\s+(?:стеклоочистител\w*|бескаркасн\w*|каркасн\w*|гибридн\w*|для\s+снега|с\s+водосгоном)|скребок\s+для\s+льда|ароматизатор\s+(?:автомобильн\w*|в\s+машину|подвесн\w*|на\s+дефлектор|гелев\w*|пластиков\w*|картонн\w*|жидк\w*|мелов\w*)|'
        r'коврик\w*\s+в\s+(?:салон|багажник)|seintex\s+\d+|чехл\w*\s+на\s+сидень\w*|накидк\w*\s+на\s+сидень\w*|трос\s+буксировочн\w*|знак\s+аварийной\s+остановки|аптечк\w*\s+автомобильн\w*|огнетушител\w*|канистр\w*\s+для\s+топлива|алкотестер\w*|парктроник\w*|камер\w*\s+заднего\s+вида|сигнализац\w*\s+автомобильн\w*|автосигнализац\w*|starline\s+брелок|брелок\s+starline|временн\w*\s+парковочн\w*\s+карт\w*)\b',
        title, re.I
    )) and not is_tire and not is_tool
    if is_auto_item:
        if re.search(r'\b(видеорегистратор\w*|радар[- ]детектор\w*|комбо[- ]устройств\w*|парктроник\w*|камер\w*\s+заднего\s+вида|автосигнализац\w*|сигнализац\w*\s+автомобильн\w*|starline\s+брелок|брелок\s+starline|алкотестер\w*|автосканер\w*|carplay)\b', title, re.I):
            attrs['type'] = 'Видеорегистраторы и автоэлектроника'
        elif re.search(r'\b(автомагнитол\w*|магнитол\w*\s+автомобильн\w*|автоакустик\w*|колонк\w*\s+автомобильн\w*|сабвуфер\s+|усилител\w*\s+автомобильн\w*)\b', title, re.I):
            attrs['type'] = 'Автозвук и автомагнитолы'
        elif re.search(r'\b(амортизатор\w*|стойк\w*\s+стабилизатора|втулк\w*\s+стабилизатора|тяг\w*\s+стабилизатора|сайлентблок\w*|рычаг\w*|пружин\w*\s+подвески|опор\w*|шаров\w*|рулев\w*|тормозн\w*|ремкомплект|frenkit|трос\s+(?!буксировочн)|patron\s+|stellox\s+|febest\s+|formpart\s+|фильтр|свеч\w*\s+зажигания|катушк\w*\s+зажигания|датчик|насос\s+(?:электрический\s+топливной|топливный|водяной|гура)|патрубок|прокладк\w*|комплект\s+(?:прокладок|грм|сцепления)|ступиц\w*|подшипник\s+ступиц|привод\s+в\s+сборе|шрус)\b', title, re.I):
            attrs['type'] = 'Автозапчасти, подвеска и фильтры'
        elif re.search(r'\b(компрессор\w*|насос|пуско[- ]зарядн\w*|пусков\w*\s+устройств\w*|бустер\s+пусков\w*|провода\s+прикуривания|домкрат\w*|инвертор\w*|преобразовател\w*)\b', title, re.I):
            attrs['type'] = 'Компрессоры, ПЗУ и техпомощь'
        elif re.search(r'\b(держател\w*|car\s+mount|автомобильн\w*\s+зарядн\w*|устройств\w*\s+зарядн\w*\s+автомобильн\w*|трансмиттер\w*|fm[- ]модулятор)\b', title, re.I):
            attrs['type'] = 'Автодержатели и зарядные устройства'
        elif re.search(r'\b(моторн\w*\s+масл\w*|масло\s+(?:моторн|трансмиссионн)|антифриз\w*|тосол\w*|стеклоомывател\w*|стеклоомывающ\w*|незамерзайк\w*|автошампунь|полироль|чернител\w*|чернение\s+|очистител\w*|размораживател\w*|wd[- ]?40|вд[- ]?40)\b', title, re.I):
            attrs['type'] = 'Масла, автохимия и автокосметика'
        else:
            attrs['type'] = 'Аксессуары в салон, щетки и уход'

        m_au_l = re.search(r'\b(0\.5|1|4|5|10|20)\s*(?:л|l|литр\w*)\b', title, re.I)
        m_au_ml = re.search(r'\b(\d{1,4})\s*(?:мл|ml)\b', title, re.I)
        if m_au_l and 'volume_ml' not in attrs:
            attrs['volume_ml'] = f"{m_au_l.group(1)} л"
        elif m_au_ml and 'volume_ml' not in attrs:
            attrs['volume_ml'] = f"{m_au_ml.group(1)} мл"

        m_au_v = re.search(r'\b(12|24)\s*(?:v|в)\b', title, re.I)
        if m_au_v and 'voltage' not in attrs:
            attrs['voltage'] = f"{m_au_v.group(1)} В"

    # 42. Чехлы, сумки для ноутбуков, защитные стекла и картриджи для принтеров
    is_case_bag_ink = bool(re.search(
        r'\b(чехол\w*\s+(?:для|книжка|накладка|бампер|папка|конверт|противоударный|силиконовый|прозрачный|magsafe|smart\s+folio|smart\s+cover)|^\s*чехол\b|бампер\s+для|'
        r'сумк\w*\s+для\s+(?:ноутбук\w*|планшет\w*|электронных\s+устройств)|рюкзак\s+для\s+ноутбук\w*|папк\w*-чехол\s+для\s+ноутбука|'
        r'защитн\w*\s+(?:стекл\w*|пленк\w*|гидрогелев\w*)|стекло\s+защитное|бронепленк\w*|protective\s+(?:front\s+)?film|back\s+film|privacy\s+protector|'
        r'картридж\w*|принт[- ]картридж\w*|тонер[- ]картридж\w*|драм[- ]картридж\w*|фотобарабан\w*|тонер\s+для|чернил\w*\s+для\s+принтера|краска\s+для\s+принтера|контейнер\s+с\s+чернилами|бутылочк\w*\s+с\s+чернилами)\b',
        title, re.I
    )) and not is_printer and not is_sw
    if is_case_bag_ink:
        if re.search(r'\b(картридж\w*|принт[- ]картридж\w*|тонер[- ]картридж\w*|драм[- ]картридж\w*|фотобарабан\w*|тонер\s+для|чернил\w*|краска\s+для\s+принтера|контейнер\s+с\s+чернилами|бутылочк\w*\s+с\s+чернилами)\b', title, re.I):
            if re.search(r'\b(чернил\w*|краска\s+для\s+принтера|контейнер\s+с\s+чернилами|бутылочк\w*\s+с\s+чернилами)\b', title, re.I):
                attrs['type'] = 'Чернила (контейнер для СНПЧ)'
            elif re.search(r'\b(фотобарабан\w*|драм[- ]картридж\w*|drum)\b', title, re.I):
                attrs['type'] = 'Фотобарабан (Drum-картридж)'
            elif re.search(r'\b(тонер[- ]картридж\w*|тонер\s+для|лазерн\w*)\b', title, re.I):
                attrs['type'] = 'Тонер-картридж (лазерный)'
            else:
                attrs['type'] = 'Картридж для принтера'

            if re.search(r'\b(black|черн\w*|чёрн\w*|\bbk\b)\b', title, re.I):
                attrs['print_color'] = 'Черный (Black)'
            elif re.search(r'\b(cyan|голуб\w*|син\w*)\b', title, re.I):
                attrs['print_color'] = 'Голубой (Cyan)'
            elif re.search(r'\b(magenta|пурпурн\w*|розов\w*|красн\w*)\b', title, re.I):
                attrs['print_color'] = 'Пурпурный (Magenta)'
            elif re.search(r'\b(yellow|желт\w*|жёлт\w*)\b', title, re.I):
                attrs['print_color'] = 'Желтый (Yellow)'
            elif re.search(r'\b(color|цветн\w*|трехцветн\w*|комплект)\b', title, re.I):
                attrs['print_color'] = 'Цветной / Комплект'
        elif re.search(r'\b(защитн\w*\s+(?:стекл\w*|пленк\w*|гидрогелев\w*)|стекло\s+защитное|бронепленк\w*|protective\s+(?:front\s+)?film|back\s+film|privacy\s+protector)\b', title, re.I):
            if re.search(r'\b(пленк\w*|гидрогелев\w*|бронепленк\w*|film|protector)\b', title, re.I) and not re.search(r'\bстекл\w*\b', title, re.I):
                attrs['type'] = 'Защитная пленка'
                if 'material' not in attrs:
                    attrs['material'] = 'Гидрогель / ТПУ'
            else:
                attrs['type'] = 'Защитное стекло'
        elif re.search(r'\b(сумк\w*\s+для\s+(?:ноутбук\w*|планшет\w*|электронных\s+устройств)|рюкзак\s+для\s+ноутбук\w*|папк\w*-чехол\s+для\s+ноутбука|чехол\s+для\s+(?:ноутбук\w*|macbook))\b', title, re.I):
            attrs['type'] = 'Сумка / Рюкзак / Чехол для ноутбука'
        elif re.search(r'\b(для\s+(?:планшет\w*|ipad|galaxy\s+tab|matepad|redmi\s+pad|xiaomi\s+pad)|smart\s+folio|smart\s+cover)\b', title, re.I):
            attrs['type'] = 'Чехол для планшета'
        elif re.search(r'\b(для\s+(?:электронн\w*\s+книг\w*|ридер\w*|pocketbook|onyx\s+boox|kindle))\b', title, re.I):
            attrs['type'] = 'Чехол для электронной книги'
        elif re.search(r'\b(для\s+(?:наушников|airpods|galaxy\s+buds|freebuds))\b', title, re.I):
            attrs['type'] = 'Чехол для наушников'
        else:
            attrs['type'] = 'Чехол для смартфона'

        m_cb_sz = re.search(r'\b(6|7|8|9\.7|10\.1|10\.2|10\.4|10\.5|10\.9|11|12\.4|12\.9|13|13\.3|14|15|15\.6|16|17|17\.3)\s*(?:"|”|\'\'|дюйм\w*)', title, re.I)
        if m_cb_sz and 'screen_size' not in attrs:
            attrs['screen_size'] = f'{m_cb_sz.group(1)}"'

        if 'material' not in attrs:
            if re.search(r'\b(силикон\w*|silicone|tpu|тпу)\b', title, re.I):
                attrs['material'] = 'Силикон / TPU'
            elif re.search(r'\b(кожан\w*|экокож\w*|leather)\b', title, re.I):
                attrs['material'] = 'Кожа / Экокожа'
            elif re.search(r'\b(полиэстер|нейлон|ткань)\b', title, re.I):
                attrs['material'] = 'Полиэстер / Нейлон'
            elif re.search(r'\b(пластик\w*|поликарбонат)\b', title, re.I):
                attrs['material'] = 'Пластик / Поликарбонат'

    # 43. Фото и видео, Туризм и кемпинг, Настольные игры и пазлы
    is_photo_camp_board = bool(re.search(
        r'\b(веб[- ]камер\w*|web[- ]камер\w*|webcam|экшн[- ]камер\w*|action\s+camera|gopro|insta360|dji\s+(?:osmo|pocket|action|mini|air|mavic)|фотоаппарат\w*|видеокамер\w*|камер\w*\s+видеонаблюдения|ip[- ]камер\w*|smart\s+camera|\bimou\b|\btapo\s+c\d+|\bhikvision\b|\bdahua\b|\bezviz\b|объектив\w*|штатив\w*|монопод\w*|селфи[- ]палк\w*|трипод\w*|стабилизатор\s+(?:для\s+смартфона|для\s+камеры|трехосевой|стедикам)|кольцев\w*\s+ламп\w*|карта\s+видеозахвата|бинокл\w*|телескоп\w*|микроскоп\w*|фоторамк\w*|фотоальбом\w*|пленк\w*|картридж\s+(?:для\s+фото|fujifilm\s+instax)|'
        r'палатк\w*\s+(?:туристическ\w*|\d[- ]местн\w*|кемпингов\w*|автоматическ\w*|зимн\w*|шатер)|спальн\w*\s+мешок|спальник\w*|коврик\s+туристическ\w*|каремат\w*|пенк\w*\s+туристическ\w*|матрас\s+надувн\w*|надувн\w*\s+(?:матрас|кровать|подушка|круг|бассейн|лодка)|фонар\w*\s+(?:налобн\w*|кемпингов\w*|ручн\w*|аккумуляторн\w*|светодиодн\w*|туристическ\w*)|мультитул\w*|нож\s+(?:складн\w*|туристическ\w*|перочинн\w*|охотнич\w*|многофункциональн\w*)|горелк\w*\s+газов\w*|плитк\w*\s+(?:газов\w*|туристическ\w*|портативн\w*)|ветрозащитн\w*\s+экран|баллон\s+газов\w*|мангал\w*|шампур\w*|решетк\w*\s+для\s+гриля|уголь\s+древесн\w*|розжиг|сумк\w*[- ]холодильник|термосумк\w*|автохолодильник\w*|аккумулятор\s+холода|чемодан\w*|дорожн\w*\s+сумк\w*|гамак\w*|стул\s+(?:складн\w*|туристическ\w*|кемпингов\w*)|кресло\s+(?:складн\w*|туристическ\w*|кемпингов\w*)|стол\s+(?:складн\w*|туристическ\w*|кемпингов\w*)|удочк\w*|спиннинг\w*|катушк\w*\s+рыболовн\w*|экран\s+.*\s+для\s+ловли|'
        r'настольн\w*\s+игр\w*|игра\s+настольная|пазл\w*|3d[- ]пазл\w*|puzzle|головоломк\w*|кубик\s+рубика|шахмат\w*|шашки|нарды|домино|лото|монополи\w*|карты\s+игральн\w*|карточн\w*\s+игр\w*|uno\b|уно\b|мафия|имаджинариум|манчкин|свинтус|дженга|башня\s+падающая|руммикуб|эрудит|скрэббл|катан|колонизаторы|каркассон|билет\s+на\s+поезд)\b',
        title, re.I
    )) and not is_printer and not is_case_bag_ink
    if is_photo_camp_board:
        if re.search(r'\b(веб[- ]камер\w*|web[- ]камер\w*|webcam|карта\s+видеозахвата)\b', title, re.I):
            attrs['type'] = 'Веб-камера / Стриминг'
            if re.search(r'\b(4k|uhd|2160p)\b', title, re.I):
                attrs['resolution'] = '4K UHD'
            elif re.search(r'\b(2k|qhd|1440p)\b', title, re.I):
                attrs['resolution'] = '2K QHD'
            elif re.search(r'\b(full\s*hd|fhd|1080p|1920\s*[xх]\s*1080)\b', title, re.I):
                attrs['resolution'] = 'Full HD 1080p'
            elif re.search(r'\b(hd|720p|1280\s*[xх]\s*720)\b', title, re.I):
                attrs['resolution'] = 'HD 720p'
        elif re.search(r'\b(камер\w*\s+видеонаблюдения|ip[- ]камер\w*|smart\s+camera|\bimou\b|\btapo\s+c\d+|\bhikvision\b|\bdahua\b|\bezviz\b)\b', title, re.I):
            attrs['type'] = 'Камера видеонаблюдения / IP-камера'
            if re.search(r'\b(4k|8\s*mp|8\s*мп)\b', title, re.I):
                attrs['resolution'] = '4K UHD (8 МП)'
            elif re.search(r'\b(3k|5\s*mp|5\s*мп)\b', title, re.I):
                attrs['resolution'] = '3K (5 МП)'
            elif re.search(r'\b(2k|qhd|4\s*mp|4\s*мп|3\s*mp|3\s*мп)\b', title, re.I):
                attrs['resolution'] = '2K QHD (3–4 МП)'
            elif re.search(r'\b(full\s*hd|fhd|1080p|2\s*mp|2\s*мп)\b', title, re.I):
                attrs['resolution'] = 'Full HD 1080p (2 МП)'
        elif re.search(r'\b(экшн[- ]камер\w*|action\s+camera|gopro|insta360|dji\s+(?:osmo|pocket|action|mini|air|mavic)|фотоаппарат\w*|видеокамер\w*|объектив\w*)\b', title, re.I):
            attrs['type'] = 'Фотоаппараты, экшн-камеры и оптика'
        elif re.search(r'\b(штатив\w*|монопод\w*|селфи[- ]палк\w*|трипод\w*|стабилизатор|кольцев\w*\s+ламп\w*|бинокл\w*|телескоп\w*|микроскоп\w*|фоторамк\w*|фотоальбом\w*|instax)\b', title, re.I):
            attrs['type'] = 'Штативы, моноподы, оптика и аксессуары'
        elif re.search(r'\b(палатк\w*|спальн\w*\s+мешок|спальник\w*|коврик\s+туристическ\w*|каремат\w*|матрас\s+надувн\w*|надувн\w*|гамак\w*|стул\s+(?:складн|туристическ|кемпингов)|кресло\s+(?:складн|туристическ|кемпингов)|стол\s+(?:складн|туристическ|кемпингов))\b', title, re.I):
            attrs['type'] = 'Палатки, спальники и кемпинговая мебель'
        elif re.search(r'\b(фонар\w*|мультитул\w*|нож\s+(?:складн|туристическ|перочинн|охотнич|многофункциональн)|горелк\w*|плитк\w*\s+(?:газов|туристическ|портативн)|ветрозащитн\w*\s+экран|баллон\s+газов\w*|мангал\w*|шампур\w*|решетк\w*\s+для\s+гриля|уголь|розжиг|сумк\w*[- ]холодильник|термосумк\w*|аккумулятор\s+холода|чемодан\w*|удочк\w*|спиннинг\w*|для\s+ловли)\b', title, re.I):
            attrs['type'] = 'Фонари, мультитулы, гриль и снаряжение'
        elif re.search(r'\b(пазл\w*|3d[- ]пазл\w*|puzzle|головоломк\w*|кубик\s+рубика)\b', title, re.I):
            attrs['type'] = 'Пазлы и головоломки'
        elif re.search(r'\b(шахмат\w*|шашки|нарды|домино|лото)\b', title, re.I):
            attrs['type'] = 'Шахматы, нарды, шашки и лото'
        else:
            attrs['type'] = 'Настольная / Карточная игра'

        m_puz = re.search(r'\b(\d{2,4})\s*(?:эл\.?|элемент\w*|дет\.?|детал\w*)\b', title, re.I)
        if m_puz and 'pieces_count' not in attrs:
            attrs['pieces_count'] = f"{m_puz.group(1)} эл."

    # 44. Программы (ПО), Стационарные и IP-телефоны, Велосипеды, самокаты, спорт и аксессуары электроники (Волна 6)
    is_software_item = bool(re.search(
        r'\b(антивирус\w*|dr\.?\s*web|kaspersky|eset\s+nod32|microsoft\s+(?:office|windows|365)|'
        r'windows\s+1[01]\s+(?:home|pro|professional)|office\s+(?:home|pro|professional|202\d|365)|1с:\s*предприятие)\b',
        title, re.I
    )) and not bool(re.search(r'\b(ноутбук\w*|laptop|notebook|macbook|ультрабук\w*|моноблок\w*|системн\w*\s+блок\w*|компьютер\w*|неттоп\w*|mini\s*pc|планшет\w*)\b', title, re.I))
    if is_software_item:
        if re.search(r'\b(антивирус\w*|dr\.?\s*web|kaspersky|eset|security\s+space|internet\s+security|total\s+security)\b', title, re.I):
            attrs['type'] = 'Антивирус и безопасность'
        elif re.search(r'\b(windows\s+1[01]|операционн\w*\s+систем\w*)\b', title, re.I):
            attrs['type'] = 'Операционная система'
        else:
            attrs['type'] = 'Офисное и прикладное ПО'

        if re.search(r'\b(12\s*мес\w*|1\s*год)\b', title, re.I):
            attrs['license_term'] = '1 год (12 мес.)'
        elif re.search(r'\b(24\s*мес\w*|2\s*года)\b', title, re.I):
            attrs['license_term'] = '2 года (24 мес.)'
        elif re.search(r'\b(36\s*мес\w*|3\s*года)\b', title, re.I):
            attrs['license_term'] = '3 года (36 мес.)'
        elif re.search(r'\b(бессрочн\w*|пожизненн\w*|box|oem)\b', title, re.I):
            attrs['license_term'] = 'Бессрочная'

        m_sw_dev = re.search(r'\b([1-9]|10)\s*(?:пк|устр\w*|pc|devices)\b', title, re.I)
        if m_sw_dev:
            attrs['devices_count'] = f"{m_sw_dev.group(1)} ПК"

        if re.search(r'\b(электронн\w*\s+ключ|esd|цифров\w*\s+код)\b', title, re.I):
            attrs['delivery_type'] = 'Электронный ключ (ESD)'
        elif re.search(r'\b(box|коробочн\w*)\b', title, re.I):
            attrs['delivery_type'] = 'Коробочная версия (BOX)'
        elif re.search(r'\boem\b', title, re.I):
            attrs['delivery_type'] = 'OEM-лицензия'

    is_telephony_item = bool(re.search(
        r'\b(радио[- ]?телефон\w*|ip[- ]?телефон\w*|sip[- ]?телефон\w*|voip[- ]?телефон\w*|dect[- ]?трубк\w*|'
        r'телефон\s+(?:panasonic\s+kx-|texet\s+tx-|gigaset|yealink|grandstream|cisco|fanvil|avaya|alcatel|проводной|стационарный)|'
        r'системн\w*\s+телефон\w*\s+panasonic|консоль\s+panasonic\s+kx-)\b',
        title, re.I
    ))
    if is_telephony_item:
        if re.search(r'\b(ip[- ]?телефон\w*|sip[- ]?телефон\w*|voip|yealink|grandstream|cisco|fanvil)\b', title, re.I):
            attrs['type'] = 'IP / SIP-телефон'
            attrs['connection_type'] = 'VoIP / SIP (LAN)'
        elif re.search(r'\b(радио[- ]?телефон\w*|dect|kx-tg\d+|tx-d\d+|gigaset)\b', title, re.I):
            attrs['type'] = 'Радиотелефон (DECT)'
            attrs['connection_type'] = 'Беспроводной DECT'
        else:
            attrs['type'] = 'Проводной стационарный телефон'
            attrs['connection_type'] = 'Аналоговый проводной'

        if re.search(r'\bpoe\b', title, re.I):
            attrs['poe'] = 'С поддержкой PoE'

    is_sport_bike_item = bool(re.search(
        r'\b(электросамокат\w*|самокат\w*|kickscooter|гироскутер\w*|моноколес\w*|marsrover|сигвей|велосипед\w*|kids\s+bike|беговел\w*|'
        r'скейтборд\w*|ролики\b|роликов\w*\s+коньк\w*|коньки\s+(?:ледов|фигурн|хоккейн|раздвижн)|защитн\w*\s+шлем|commuter\s+helmet|шлем\s+для\s+(?:девочки|мальчика|велосипеда|самоката|роликов)|'
        r'электромобил\w*|квадроцикл\w*\s+kari|электроскутер\w*|'
        r'бегов\w*\s+дорожк\w*|дорожк\w*\s+для\s+ходьбы|велотренажер\w*|эллиптическ\w*\s+тренажер\w*|гребн\w*\s+тренажер|'
        r'гантел\w*|гиря\b|штанга\b|спортивн\w*\s+валик|коврик\s+для\s+(?:йоги|фитнеса)|мяч\s+(?:футбольн|баскетбольн|волейбольн|гимнастическ|фитбол)|массажн\w*\s+кресл\w*)\b',
        title, re.I
    ))
    if is_sport_bike_item:
        if re.search(r'\b(электросамокат\w*|kickscooter|mi\s+electric\s+scooter|электроскутер\w*)\b', title, re.I):
            attrs['type'] = 'Электросамокат'
            attrs['power_supply'] = 'Электрический (АКБ)'
        elif re.search(r'\b(гироскутер\w*|моноколес\w*|marsrover|сигвей)\b', title, re.I):
            attrs['type'] = 'Гироскутер / Моноколесо'
            attrs['power_supply'] = 'Электрический (АКБ)'
        elif re.search(r'\b(электромобил\w*|квадроцикл\w*\s+kari)\b', title, re.I):
            attrs['type'] = 'Детский электромобиль / Квадроцикл'
            attrs['power_supply'] = 'Электрический (АКБ)'
        elif re.search(r'\b(велосипед\w*|kids\s+bike)\b', title, re.I):
            attrs['type'] = 'Велосипед'
            attrs['power_supply'] = 'Механический'
        elif re.search(r'\b(самокат\w*|беговел\w*)\b', title, re.I):
            attrs['type'] = 'Самокат / Беговел'
            attrs['power_supply'] = 'Механический'
        elif re.search(r'\b(скейтборд\w*|ролики\b|роликов\w*\s+коньк\w*|коньки)\b', title, re.I):
            attrs['type'] = 'Ролики, скейтборды и коньки'
        elif re.search(r'\b(защитн\w*\s+шлем|commuter\s+helmet|шлем\s+для)\b', title, re.I):
            attrs['type'] = 'Защитный шлем и экипировка'
        elif re.search(r'\b(бегов\w*\s+дорожк\w*|дорожк\w*\s+для\s+ходьбы|велотренажер\w*|эллиптическ\w*\s+тренажер\w*|гребн\w*\s+тренажер|массажн\w*\s+кресл\w*)\b', title, re.I):
            attrs['type'] = 'Кардиотренажеры и массажные кресла'
        else:
            attrs['type'] = 'Инвентарь для фитнеса и спорта'

        m_whl = re.search(r'\b(8\.5|10|12|14|16|18|20|24|26|27\.5|28|29)\s*(?:"|”|\'\'|дюйм\w*)', title, re.I)
        if m_whl:
            attrs['wheel_size'] = f'{m_whl.group(1)}"'

    is_elec_accessory = bool(re.search(
        r'\b(фигурка[- ]подставка\s+для|подставка\s+(?:для\s+(?:смартфон|телефон|планшет|ноутбук|наушник)|ugreen|orico|eco\s+estelar|baseus|wiwu)|'
        r'кронштейн\s+для\s+(?:смартфонов|планшетов|монитора|телевизора)|настольн\w*\s+держател\w*|попсокет\w*|'
        r'картридер\w*|card\s*reader|умн\w*\s+гирлянд\w*|twinkly\b|пульт\s+(?:для|ду)|magic\s+(?:lg|mr20ga)|'
        r'интерактивн\w*\s+панел\w*|проекционн\w*\s+экран|датчик\s+(?:движения|открытия|протечки|дыма|температуры\s+и\s+влажности))\b',
        title, re.I
    )) and not is_auto_item and not is_furniture
    if is_elec_accessory and 'type' not in attrs:
        if re.search(r'\b(фигурка[- ]подставка|подставка|кронштейн|настольн\w*\s+держател\w*|попсокет)\b', title, re.I):
            attrs['type'] = 'Подставка / Держатель / Кронштейн'
        elif re.search(r'\b(картридер\w*|card\s*reader)\b', title, re.I):
            attrs['type'] = 'Картридер'
        elif re.search(r'\b(умн\w*\s+гирлянд\w*|twinkly|датчик\s+(?:движения|открытия|протечки|дыма|температуры))\b', title, re.I):
            attrs['type'] = 'Умный дом и смарт-освещение'
        elif re.search(r'\b(пульт|magic\s+(?:lg|mr20ga)|интерактивн\w*\s+панел\w*|проекционн\w*\s+экран)\b', title, re.I):
            attrs['type'] = 'ТВ-аксессуары, экраны и пульты ДУ'

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
    rows = conn.execute(
        'SELECT key, value, evidence, source_title FROM product_search_attributes WHERE product_id=?',
        (product_id,)).fetchall()
    existing = {r[0]: (r[1], r[2]) for r in rows}
    bound_title = {r[0]: r[3] for r in rows}
    effective: Dict[str, str] = {}
    for k, (v, evidence) in items.items():
        old = existing.get(k)
        if old and evidence_rank(old[1]) > evidence_rank(evidence):
            effective[k] = old[0]
            continue
        effective[k] = v
        # Значение получено заново по текущему названию: привязку к названию обновляем даже при
        # том же значении — поиск учитывает только source_title = title (R02)
        if old == (v, evidence) and bound_title.get(k) == source_title:
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
    p.category IN ('Подгузники и гигиена', 'diapers_hygiene', 'Бытовая техника', 'appliances', 'refrigerators', 'Холодильники',
                   'washing_machines', 'Стиральные машины', 'dishwashers', 'Посудомоечные машины', 'vacuums', 'Пылесосы',
                   'tires', 'Шины и диски', 'Мониторы', 'Комплектующие для ПК', 'Видеокарты', 'monitors',
                   'pc_parts', 'gpus', 'tv', 'Телевизоры', 'headphones', 'Наушники', 'air_conditioners', 'Кондиционеры', 'smartwatches', 'Смарт-часы',
                   'peripherals', 'Клавиатуры и мыши', 'kitchen_appliances', 'Техника для кухни', 'network', 'Сетевое оборудование',
                   'printers', 'Принтеры и МФУ', 'tablets', 'Планшеты', 'removable_storage', 'Флешки и карты памяти', 'audio', 'Аудиотехника',
                   'consoles', 'Игровые приставки', 'cpus', 'Процессоры', 'pc_cases', 'Корпуса', 'storage', 'SSD накопители',
                   'power', 'ИБП и питание', 'cables_chargers', 'Кабели и зарядные устройства',
                   'shoes', 'Обувь', 'clothing', 'Одежда и обувь', 'women_clothing', 'Женская одежда', 'men_clothing', 'Мужская одежда',
                   'furniture', 'Мебель', 'tools_repair', 'Инструменты и ремонт', 'perfume', 'Парфюмерия', 'beauty', 'Красота и здоровье',
                   'toys', 'Игрушки и конструкторы', 'pc_desktops', 'Компьютеры и ПК', 'motherboards', 'Материнские платы',
                   'cooling', 'Охлаждение и кулеры', 'hdd', 'Жесткие диски HDD', 'ram', 'Оперативная память',
                   'books_stationery', 'Книги и канцелярия', 'books', 'Книги', 'pets', 'Зоотовары',
                   'cat_food', 'Товары для кошек', 'dog_food', 'Товары для собак',
                   'home', 'Дом и сад', 'home_textile', 'Текстиль и декор', 'garden', 'Сад и огород',
                   'kitchenware', 'Посуда и кухня', 'household_chem', 'Бытовая химия и хозтовары',
                   'food', 'Продукты питания', 'drinks', 'Напитки и соки', 'dairy', 'Молочные продукты',
                   'meat_poultry', 'Мясо и птица', 'medicine', 'Лекарства и витамины',
                   'kids', 'Детские товары', 'baby_care', 'Товары для малышей', 'baby_food', 'Детское питание',
                   'baby_feeding', 'Кормление и посуда', 'baby_bath_care', 'Купание и уход',
                   'nursery_sleep', 'Детская комната и сон', 'strollers', 'Коляски и автокресла',
                   'baby_toys', 'Игрушки для малышей', 'skincare', 'Уход за кожей',
                   'haircare', 'Уход за волосами', 'makeup', 'Декоративная косметика',
                   'auto', 'Автотовары', 'car_electronics', 'Автоэлектроника', 'car_oils', 'Масла и автохимия',
                   'phone_cases', 'Чехлы и бамперы', 'phone_glass', 'Защитные стекла и пленки',
                   'printer_ink', 'Картриджи и чернила', 'tourism', 'Туризм и кемпинг',
                   'board_games', 'Настольные игры', 'photo_video', 'Фото и видео',
                   'electronics', 'Электроника', 'software', 'Программы',
                   'telephony', 'Стационарные и IP-телефоны', 'bicycles', 'Велосипеды и самокаты',
                   'sport', 'Спорт и отдых', 'fitness', 'Фитнес и тренажеры',
                   'laptops', 'Ноутбуки', 'phones', 'Смартфоны', 'psu', 'Блоки питания')
    OR p.title LIKE '%подгуз%' OR p.title LIKE '%трусик%' OR p.title LIKE '%памперс%'
    OR p.title LIKE '%холодильн%' OR p.title LIKE '%стиральн%' OR p.title LIKE '%посудомо%' OR p.title LIKE '%пылесос%'
    OR p.title LIKE '%автошин%' OR p.title LIKE '%зимн%шин%' OR p.title LIKE '%летн%шин%' OR p.title LIKE '% R1%' OR p.title LIKE '% R2%'
    OR p.title LIKE '%монитор%' OR p.title LIKE '%видеокарт%' OR p.title LIKE '%geforce%'
    OR p.title LIKE '%radeon%' OR p.title LIKE '%материнск%' OR p.title LIKE '%блок питания%'
    OR p.title LIKE '%ssd%' OR p.title LIKE '%оперативн%памят%' OR p.title LIKE '%ddr5%'
    OR p.title LIKE '%ddr4%' OR p.title LIKE '%телевизор%' OR p.title LIKE '%наушник%'
    OR p.title LIKE '%кондиционер%' OR p.title LIKE '%сплит-систем%' OR p.title LIKE '%смарт-час%'
    OR p.title LIKE '%клавиатур%' OR p.title LIKE '%мышь%' OR p.title LIKE '%мышк%'
    OR p.title LIKE '%кофемашин%' OR p.title LIKE '%чайник%' OR p.title LIKE '%микроволнов%' OR p.title LIKE '%блендер%'
    OR p.title LIKE '%роутер%' OR p.title LIKE '%маршрутизатор%' OR p.title LIKE '%коммутатор%'
    OR p.title LIKE '%принтер%' OR p.title LIKE '%мфу%' OR p.title LIKE '%плоттер%' OR p.title LIKE '%планшет%'
    OR p.title LIKE '%процессор%' OR p.title LIKE '%приставк%' OR p.title LIKE '%playstation%' OR p.title LIKE '%power bank%'
    OR p.title LIKE '%парфюмерн%' OR p.title LIKE '%туалетная вода%' OR p.title LIKE '%lego%' OR p.title LIKE '%конструктор%'
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
    batch_size = 200
    for idx, (pid, title) in enumerate(titles.items(), start=1):
        try:
            total_indexed += index_product_facts(conn, pid, facts_by_product.get(pid, []), title)
            if idx % batch_size == 0 and hasattr(conn, 'commit'):
                conn.commit()
        except Exception:
            continue

    if hasattr(conn, 'commit'):
        conn.commit()

    return total_indexed
