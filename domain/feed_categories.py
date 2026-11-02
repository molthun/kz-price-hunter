"""Разделы магазинов важнее локальной модели.

Прямая выгрузка (SHOP_FEEDS) — первоисточник; явный тип RAM/SSD/HDD/карты памяти
в названии исправляет противоречащий ему раздел. У остальных магазинов
раздел — наш запрос к их каталогу, и в «Смартфоны» приходят защитные стёкла, поэтому сначала правило по
названию, затем раздел (apply_section_categories). Локальная модель решает только оставшееся.
"""
from __future__ import annotations

import datetime
import re
from typing import Dict, Optional

from domain.category import MASTER_TAXONOMY, TaxonomyMapper
from domain.storage_types import storage_type

FEED_MODEL = "feed"
_GROUPS: Dict[str, str] = {
    "garden": "Товары для сада и огорода|Мойки высокого давления|Снегоуборочная техника|Бассейны|Аксессуары для бассейнов|Надувные матрасы|Качели, горки",
    "pc_components": "Корпуса|Корпуса (уцененный товар)|Корпуса для дисков|Блоки питания|Кулеры для процессора|Прочее охлаждение|Системы водяного охлаждения|Термопаста|Подставки для охлаждения|Звуковые карты и усилители|Комплектующие для серверов|Карты видеозахвата|Оптические дисководы (приводы)",
    "ram": "Оперативная память|Оперативная память для ноутбуков",
    "network": "MESH-системы|Powerline-адаптеры|Адаптеры, контроллеры|Антенны, усилители сигнала|Коммутаторы (switch)|Медиаконвертеры|Роутеры, модемы|Сетевые карты, адаптеры|Точки доступа|Кабель-каналы, фурнитура|Серверные шкафы|Сетевые хранилища|Прочее оборудование",
    "printers": "3D принтеры|МФУ лазерные|МФУ струйные|Принтеры лазерные|Принтеры струйные|Плоттеры|Сканеры|Пластик для 3D печати",
    "power": "ИБП (UPS)|Аккумуляторы и аксессуары для ИБП|Инверторы|Стабилизаторы|Стабилизаторы (уцененный товар)|Сетевые фильтры, адаптеры",
    "photo_video": "Аксессуары для фотоаппаратов|Аксессуары для экшн-камер|Экшн-камеры|Фотоаппараты|Сумки для фото и видео|Моноподы|Системы видеонаблюдения|Веб-камеры",
    "software": "Антивирусы|Операционные системы|Офисные программы|Цифровые подписки",
    "storage": "SSD диски",
    "hdd": "Жесткие диски",
    "removable_storage": "Карты памяти|Флешки",
    "peripherals": "Джойстики, рули, геймпады|Игровые клавиатуры|Игровые мыши|Клавиатуры|Коврики для мыши|Контроллеры для стриминга|Мыши|Наклейки для клавиатур|Графические планшеты",
    "cables_chargers": "Power bank (мобильные аккумуляторы)|Зарядные устройства|Зарядные устройства для ноутбуков|Кабели и переходники|Кабели, коннекторы|Кабели, переходники",
    "audio": "Аксессуары для микрофонов|Акустические системы и колонки|Аудиотехника, Hi-Fi оборудование|Микрофоны|Музыкальные центры|Плееры|Портативные колонки|Саундбары|Спикерфоны",
    "headphones": "Наушники и гарнитуры",
    "phone_cases": "Чехлы для наушников|Чехлы для смартфонов|Чехлы для электронных книг",
    "phone_glass": "Бронепленки|Защитные стекла и пленки",
    "phones": "Смартфоны|Телефоны",
    "tablets": "Планшеты",
    "laptops": "Ноутбуки|Ультрабуки",
    "pc_desktops": "Моноблоки|Настольные компьютеры|Неттопы",
    "monitors": "Мониторы|Крепления для мониторов",
    "tv": "Телевизоры|Антенны для телевизора|Кронштейны для телевизоров|Крепления для проекторов|Медиаплееры|Проекторы|Пульты|ТВ-тюнеры|Экраны проекционные",
    "gpus": "Видеокарты",
    "cpus": "Процессоры",
    "motherboards": "Материнские платы",
    "consoles": "Игровые приставки",
    "smartwatches": "Смарт-часы|Фитнес-браслеты|Ремешки для смарт-часов и фитнес-браслетов",
    "printer_ink": "Лазерные картриджи|Матричные картриджи|Струйные картриджи и чернила",
    "electronics": "Датчики, устройства и управление для умного дома|Прочие аксессуары|Крепления, подставки для смартфонов|USB-hub|Картридеры|Док-станции, концентраторы|Сумки для ноутбуков",
    "kitchen_appliances": "Аэрогрили|Блендеры|Вакууматоры и аксессуары|Вафельницы, блинницы|Гаджеты для кухни|Йогуртницы|Кофеварки, кофемашины|Кофемолки|Кухонные комбайны и машины|Льдогенераторы|Миксеры|Мороженицы|Мультиварки, пароварки|Соковыжималки|Сушилки для продуктов|Тостеры, гриль|Фритюрницы|Хлебопечи|Чайники, термопоты|Электромясорубки|Электрошашлычницы|Весы кухонные",
    "refrigerators": "Морозильники|Холодильники|Винные шкафы",
    "vacuums": "Пылесосы|Пароочистители|Стеклоочистители",
    "vacuum_parts": "Аксессуары для пылесосов",
    "appliances": "Утюги, отпариватели|Увлажнители и очистители воздуха|Швейные машинки, оверлоки|Уход за одеждой",
    "kitchenware": " Посуда для сервировки|Посуда для сервировки|Кастрюли|Кухонные принадлежности|Сковороды, сотейники|Турки",
    "home": "Автоматика для ворот Doorhan|Лампы, светильники|Фонари|Часы настенные",
    "furniture": "Компьютерные кресла|Компьютерные столы|Комплектующие для шкафов|Парты-трансформеры",
    "household_chem": "Бытовая химия|Уборка дома",
    "tools_repair": "Аккумуляторы для электроинструментов|Дрели, шуруповерты|Инструменты|Инструменты и оборудование|Лазерные дальномеры|Наборы инструментов|Обжимной инструмент|Отвертки|Паяльное оборудование|Перфораторы|Прочие инструменты, оборудование|Шлифовальные машины|Электролобзики, пилы|Домкраты",
    "auto": "Автокомпрессоры|Автомобильные аксессуары",
    "car_electronics": "Видеорегистраторы|Радар-детекторы",
    "beauty": "Здоровье и гигиена|Массажеры|Уход за полостью рта|Весы",
    "haircare": "Машинки для стрижки, триммеры|Фены, электрощипцы|Электробритвы, эпиляторы",
    "toys": "Игрушечные машинки|Конструкторы|Радиоуправляемые игрушки|Развивающие игрушки и робототехника",
    "books_stationery": "Блокноты, бумажная продукция|Бумага, фотобумага|Для рисования|Карандаши|Клей, клейкая лента|Корректоры, ластики|Папки, файлы, регистраторы|Скрепки, кнопки, зажимы|Степлеры, дыроколы",
    "bicycles": "Гироскутеры|Сигвеи|Электросамокаты и велосипеды",
    "fitness": "Беговые дорожки",
    "tourism": "Надувные лодки|Пляжный отдых|Сумки и рюкзаки|Туризм и отдых на природе|Чемоданы|Эхолоты",
    "pets": "Товары для животных",
}
SHOP_FEEDS: Dict[str, Dict[str, str]] = {
    "Белый Ветер": {raw.strip(): cat_id for cat_id, raws in _GROUPS.items() for raw in raws.split("|")},
}


def apply_feed_categories(conn) -> int:
    """Ставит товарам раздел из выгрузки и закрепляет его. Возвращает число изменённых товаров."""
    names = {c.id: c.name for c in MASTER_TAXONOMY}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    changed = 0
    from catalog_classifier import CatalogClassifier
    classifier = CatalogClassifier()
    for shop, table in SHOP_FEEDS.items():
        rows = conn.execute(
            """SELECT o.product_id, p.category, c.category, v.model, c.title
               FROM products p
               JOIN offers o ON o.id = 'off_' || p.id AND o.is_active = 1
               JOIN canonical_products c ON c.id = o.product_id
               LEFT JOIN category_ai_verdicts v ON v.product_id = o.product_id
               WHERE p.shop = ? AND p.is_active = 1 AND NOT EXISTS(SELECT 1 FROM product_category_tags t WHERE t.product_id=c.id)""",
            (shop,),
        ).fetchall()
        for product_id, raw, current, model, title in rows:
            cat_id = table.get((raw or "").strip())
            if not cat_id:
                continue
            explicit = storage_type(title)
            if not explicit and cat_id in {'storage','hdd','ram','removable_storage'}:
                rule = classifier.classify_rules(title)
                explicit = rule.category_id if rule and rule.source in TITLE_RULE_SOURCES else None
            cat_id = explicit or cat_id
            target = names[cat_id]
            if current == target and model == FEED_MODEL:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO category_ai_verdicts
                   (product_id, old_category, new_category, confidence, model, applied, decided_at)
                   VALUES (?, ?, ?, NULL, ?, 1, ?)""",
                (product_id, current, target, FEED_MODEL, now),
            )
            if current != target:
                conn.execute("UPDATE canonical_products SET category = ? WHERE id = ?", (target, product_id))
                changed += 1
    conn.commit()
    return changed


# Подборки, а не разделы: в них лежит что угодно
_NOT_A_SECTION_RE = re.compile(r"распродаж|акци|скидк|уценк|новинк|витрин|контент|запчаст|^игры$|^электроника$|^samsung$|^dyson$|^mac$|музыка", re.I)
# Разделы наших запросов, которых не знают синонимы каталога или понимают неверно
_SECTIONS: Dict[str, str] = {
    "tools_repair": "перфораторы|сварочные аппараты|сварочное оборудование|деревообрабатывающие станки|складское оборудование|стремянки|генераторы и электростанции|стройматериалы|отделочные материалы|строительство и ремонт",
    "cables_chargers": "wiwu power bank|зарядки wiwu|wiwu зарядные станции|wiwu переходники",
    "pc_components": "жесткие диски и ssd|накопители ssd и hdd",
    "electronics": "аксессуары apple|wiwu автодержатели|wiwu ремень для телефона|wiwu петлички|dji дроны|компьютерные аксессуары",
    "audio": "яндекс станции|акустические системы",
    "smartwatches": "часы garmin",
    "printer_ink": "расходные материалы",
    "beauty": "уход за полостью рта|санитария и гигиена|личная гигиена и косметика|подарочные наборы",
    "skincare": "косметика и дерматологический уход",
    "household_chem": "средства для мытья посуды",
    "furniture": "анатомические матрасы|матрасы|кресла и пуфы|компьютерные кресла|стеллажи и этажерки",
    "home_textile": "одеяла",
    "baby_care": "мать и дитя|товары для матери и ребенка|уход и гигиена",
    "medicine": "антибиотики|антибиотики и противомикробные|противовирусные препараты|жаропонижающие|спазмолитики|медицинские приборы|тонометры и диагностика|медтехника и изделия мед. назначения",
    "shoes": "женские лоферы|женские босоножки|мужские мокасины",
    "sport": "санки и снегокаты",
    "vacuums": "техника для уборки",
    "garden": "мотоблоки и культиваторы|минитракторы|садовый инвентарь|гриль и мангалы",
    "home": "товары для кухни|товары для праздника|светильники и свет|емкости, баки и ведра|вешалки|обувницы и подставки|электротовары, свет и климат|хранение и порядок|аксессуары для хранения",
    "kitchen_appliances": "вытяжки",
    "appliances": "водонагреватели|утюги|отпариватели|утюги и отпариватели|воздухоочистители",
    "haircare": "электробритвы",
    "books_stationery": "хобби и творчество|творчество и хобби",
    "kids": "детская комната",
    "cat_food": "консервы для кошек|ветаптека для кошек",
    "dog_food": "консервы для собак|ветаптека для собак",
    "pets": "товары для грызунов|аквариумистика и рыбы|наполнители и туалеты",
    "clothing": "аксессуары|женские аксессуары|мужские аксессуары|детские аксессуары|сумки и рюкзаки|ювелирные украшения",
}
_SECTION_TABLE = {name: cat_id for cat_id, names in _SECTIONS.items() for name in names.split("|")}
_mapper = TaxonomyMapper()
TITLE_RULE_SOURCES = ("rule_title", "rule_accessory")


def section_category(raw: Optional[str]) -> Optional[str]:
    """Раздел каталога по разделу магазина: id или None, если раздел ни о чём не говорит."""
    # Срезается и префикс магазина, и эмодзи: «Мечта: 💨 Утюги» → «утюги»
    name = re.sub(r"[^\w\s,.-]", " ", TaxonomyMapper._normalize_token(raw or ""))
    name = re.sub(r"\s+", " ", name).strip()
    if not name or _NOT_A_SECTION_RE.search(name):
        return None
    if name in _SECTION_TABLE:
        return _SECTION_TABLE[name]
    mapped = _mapper.map_category(name)
    return mapped.id if mapped else None


def repair_storage_categories(conn, apply=True):
    names = {c.id:c.name for c in MASTER_TAXONOMY}
    family = [names[k] for k in ('storage','hdd','ram','removable_storage')]
    rows = conn.execute('SELECT id,title,category FROM canonical_products c WHERE category IN (?,?,?,?) AND NOT EXISTS(SELECT 1 FROM product_category_tags t WHERE t.product_id=c.id)',family).fetchall()
    from catalog_classifier import CatalogClassifier
    classifier=CatalogClassifier()
    moves=[]
    for pid,title,current in rows:
        target=storage_type(title)
        if not target:
            rule=classifier.classify_rules(title)
            target=rule.category_id if rule and rule.source in TITLE_RULE_SOURCES else None
        if not target and re.fullmatch(r'[\W_]*[A-Za-z0-9][A-Za-z0-9._()/+-]*[\W_]*', title or ''):
            # A bare article has no title type: require unanimous explicit source sections.
            sources = conn.execute("SELECT DISTINCT p.category FROM offers o JOIN products p ON o.id='off_'||p.id WHERE o.product_id=? AND o.is_active=1 AND p.is_active=1",(pid,)).fetchall()
            mapped = {section_category(row[0]) for row in sources}
            if len(mapped)==1 and mapped.issubset({'ram','hdd','removable_storage'}):
                target=next(iter(mapped))
        if target and names[target]!=current:
            moves.append((pid,current,names[target]))
    if apply:
        stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
        for pid,old,target in moves:
            conn.execute('UPDATE canonical_products SET category=? WHERE id=?',(target,pid))
            conn.execute("""INSERT OR REPLACE INTO category_ai_verdicts
                (product_id,old_category,new_category,confidence,model,applied,decided_at)
                VALUES(?,?,?,0.97,'rule_storage_type',1,?)""",(pid,old,target,stamp))
        conn.commit()
        if moves:
            from repositories.search_repo import SearchRepository
            SearchRepository._cat_cache={}
    return moves


def apply_section_categories(conn, classifier, apply: bool = True) -> Dict:
    """Правило по названию, затем раздел магазина. Решение закрепляется, и модель такие товары не трогает."""
    names = {c.id: c.name for c in MASTER_TAXONOMY}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    storage_repairs = repair_storage_categories(conn, apply=apply)
    rows = conn.execute(
        """SELECT o.product_id, MIN(p.category), c.category, c.title
           FROM products p
           JOIN offers o ON o.id = 'off_' || p.id AND o.is_active = 1
           JOIN canonical_products c ON c.id = o.product_id
           LEFT JOIN category_ai_verdicts v ON v.product_id = o.product_id
           WHERE p.is_active = 1 AND NOT EXISTS(SELECT 1 FROM product_category_tags t WHERE t.product_id=c.id) AND COALESCE(v.model, '') NOT IN (?, 'ai_check')
           GROUP BY o.product_id""",
        (FEED_MODEL,),
    ).fetchall()
    root_names = {c.name for c in MASTER_TAXONOMY if not c.parent_id}
    leaf_names = {c.name for c in MASTER_TAXONOMY if c.parent_id}
    moves = []
    pins = []
    for product_id, raw, current, title in rows:
        rule = classifier.classify_rules(title)
        # Только бренд ничего не говорит о товаре: правило в этом случае отдаёт общую «Электронику»
        target = rule.category_name if rule and rule.category_id and rule.source in TITLE_RULE_SOURCES else None
        if not target:
            cat_id = section_category(raw)
            target = names.get(cat_id) if cat_id else None
        if not target:
            continue
        # Общий раздел не отменяет точный: «Красота и здоровье» не заменит «Уход за волосами»
        if target in root_names and current in leaf_names:
            target = current
        pins.append((product_id, current, target, FEED_MODEL, now))
        if target != current:
            moves.append((target, product_id, raw, current, title))
    if apply:
        conn.executemany(
            """INSERT OR REPLACE INTO category_ai_verdicts
               (product_id, old_category, new_category, confidence, model, applied, decided_at)
               VALUES (?, ?, ?, NULL, ?, 1, ?)""",
            pins,
        )
        conn.executemany("UPDATE canonical_products SET category = ? WHERE id = ?", [m[:2] for m in moves])
        conn.commit()
    return {"seen": len(rows), "pinned": len(pins), "moved": len(moves), "moves": moves, "storage_repairs": storage_repairs}
