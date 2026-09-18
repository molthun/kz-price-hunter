"""Model matching and canonical identity extraction for KZ Price Hunter."""
import re
import unicodedata
from typing import Optional, Set, Dict

IGNORED = set('''смартфон телефон мобильный ноутбук планшет телевизор монитор процессор
видеокарта наушники часы пылесос игровой игровая игровое smart smartphone laptop
черный чёрный белый серый синий зеленый зелёный красный бежевый серебристый золотой
фиолетовый розовый black white gray grey blue green red silver gold purple pink
beige desert titanium титан титановый natural натуральный корпус цвет'''.split())

BRAND_MAP = {
    'айфон': 'apple', 'эппл': 'apple', 'эпл': 'apple', 'apple': 'apple',
    'самсунг': 'samsung', 'samsung': 'samsung',
    'сяоми': 'xiaomi', 'ксиоми': 'xiaomi', 'xiaomi': 'xiaomi', 'redmi': 'redmi', 'редми': 'redmi', 'poco': 'poco', 'поко': 'poco',
    'сони': 'sony', 'sony': 'sony',
    'асус': 'asus', 'asus': 'asus',
    'леново': 'lenovo', 'lenovo': 'lenovo',
    'эйсер': 'acer', 'асер': 'acer', 'acer': 'acer',
    'хп': 'hp', 'hp': 'hp',
    'делл': 'dell', 'dell': 'dell',
    'палит': 'palit', 'palit': 'palit',
    'гигабайт': 'gigabyte', 'gigabyte': 'gigabyte',
    'мсй': 'msi', 'msi': 'msi',
    'хуавей': 'huawei', 'huawei': 'huawei',
    'хонор': 'honor', 'honor': 'honor',
    'lg': 'lg', 'плейстейшн': 'sony', 'playstation': 'sony', 'ps': 'sony',
    'нвидиа': 'nvidia', 'nvidia': 'nvidia',
}

RE_BRACKET_TAGS = re.compile(r'\[[a-zA-Z0-9_\-\.\s/]+\]|\((?![0-9]+\s*(?:gb|гб|tb|тб)\b)[a-zA-Z0-9_\-\.\s/]+\)', re.IGNORECASE)
RE_DIAGONAL = re.compile(r'\b\d+(?:\.\d+)?\s*(?:"|\'\'|дюйм(?:а|ов)?)\b', re.IGNORECASE)
RE_SIM_FLUFF = re.compile(r'\b(?:nano[\s-]*sim|dual[\s-]*sim|e[\s-]*sim|sim|wi-fi|wifi|4g|5g|lte)\b', re.IGNORECASE)
RE_MARKETING_FLUFF = re.compile(r'\b(?:global[\s-]*version|глобальная\s*версия|роснефть|акция|скидка|новинка|оригинал|гарантия|ростест|евротест)\b', re.IGNORECASE)


def _family_key(title: str) -> Optional[str]:
    """Извлекает канонический ключ товара вида <brand>:<model_or_family>:<capacity>."""
    if not title:
        return None
    text = unicodedata.normalize('NFKC', title).lower().replace('ё', 'е')
    text = re.sub(r'(\d+)\s*(?:tb|тб)\b', lambda m: f' {int(m[1]) * 1024}gb ', text)
    text = re.sub(r'(\d+)\s*(?:gb|гб)\b', r' \1gb ', text)
    text = re.sub(r'(\d+)\s*(?:mb|мб)\b', r' \1mb ', text)

    # 1. Извлечение памяти / объема
    capacities = re.findall(r'\b(\d+gb)\b', text)
    cap_val = capacities[-1] if capacities else ''

    # 2. Очистка шума (артикулы магазинов в скобках, диагонали, nano-sim)
    cleaned = RE_BRACKET_TAGS.sub(' ', text)
    cleaned = RE_DIAGONAL.sub(' ', cleaned)
    cleaned = RE_SIM_FLUFF.sub(' ', cleaned)
    cleaned = RE_MARKETING_FLUFF.sub(' ', cleaned)

    tokens = [t for t in re.findall(r'[a-zа-я0-9]+', cleaned) if t not in IGNORED and t != cap_val]

    # Определение бренда
    brand = None
    for t in tokens:
        if t in BRAND_MAP:
            brand = BRAND_MAP[t]
            break

    # 3.1. Apple iPhone
    if 'iphone' in tokens or 'айфон' in tokens:
        brand = 'apple'
        m = re.search(r'(?:iphone|айфон)\s*(?:se|\d{1,2})(?:\s*(?:pro\s*max|plus|pro|mini))?', cleaned)
        if m:
            model = m.group(0).replace('айфон', 'iphone').strip()
            # убираем двойные пробелы
            model = re.sub(r'\s+', ' ', model)
            return f'apple:{model}:{cap_val}' if cap_val else f'apple:{model}'

    # 3.2. Samsung Galaxy (только для смартфонов/планшетов, исключая мониторы и бытовую технику)
    if ('galaxy' in tokens or ('samsung' in tokens and any(t.startswith('s') and len(t) > 1 and t[1:].isdigit() for t in tokens))) and not any(kw in title.lower() for kw in ('монитор', 'телевизор', 'пылесос', 'холодильник', 'стиральн', 'essential')):
        brand = 'samsung'
        m = re.search(r'(?:galaxy\s*)?([sazm]\d{1,2})(?:\s*(?:ultra|plus|fe|\+))?', cleaned)
        if m:
            base = m.group(0).replace('galaxy', '').strip()
            model = f'galaxy {base}'.strip()
            model = re.sub(r'\s+', ' ', model)
            return f'samsung:{model}:{cap_val}' if cap_val else f'samsung:{model}'

    # 3.3. Sony PlayStation
    if any(t in ('playstation', 'ps5', 'ps4', 'плейстейшн') for t in tokens):
        m = re.search(r'(?:playstation|ps|плейстейшн)\s*([45])(?:\s*(?:slim|pro|fat))?', cleaned)
        if m:
            ver = m.group(1)
            sub = 'slim' if 'slim' in cleaned else ('pro' if 'pro' in cleaned else '')
            model = f'playstation {ver}' + (f' {sub}' if sub else '')
            return f'sony:{model.strip()}:{cap_val}' if cap_val else f'sony:{model.strip()}'

    # 3.4. Видеокарты GeForce RTX / Radeon RX
    m_gpu = re.search(r'(?:geforce\s*|radeon\s*)?(rtx|rx)\s*(\d{4})(?:\s*(?:ti|super|xt|xtx))?', cleaned)
    if m_gpu:
        chip = m_gpu.group(0)
        chip = re.sub(r'^(?:geforce|radeon)\s*', '', chip).strip()
        gpu_brand = brand or ('nvidia' if 'rtx' in chip else 'amd')
        return f'{gpu_brand}:{chip}:{cap_val}' if cap_val else f'{gpu_brand}:{chip}'

    # 3.5. iPad / MacBook
    if 'ipad' in tokens or 'айпад' in tokens:
        brand = 'apple'
        m = re.search(r'(?:ipad|айпад)(?:\s*(?:pro|air|mini))?(?:\s*\d{1,2})?', cleaned)
        if m:
            model = m.group(0).replace('айпад', 'ipad').strip()
            model = re.sub(r'\s+', ' ', model)
            return f'apple:{model}:{cap_val}' if cap_val else f'apple:{model}'

    if 'macbook' in tokens or 'макбук' in tokens:
        brand = 'apple'
        m = re.search(r'(?:macbook|макбук)(?:\s*(?:air|pro))?(?:\s*m\d)?', cleaned)
        if m:
            model = m.group(0).replace('макбук', 'macbook').strip()
            model = re.sub(r'\s+', ' ', model)
            return f'apple:{model}:{cap_val}' if cap_val else f'apple:{model}'

    # 3.6. Базовый канонический ключ: brand + модельный токен с цифрой + память
    if brand:
        digit_tokens = [t for t in tokens if any(c.isdigit() for c in t) and t != cap_val]
        if digit_tokens:
            return f'{brand}:{digit_tokens[0]}:{cap_val}' if cap_val else f'{brand}:{digit_tokens[0]}'

    return None


def identity_tokens(title):
    """Keep specifications; discard only cosmetic words and recognizable SKU tags."""
    text = unicodedata.normalize('NFKC', title).lower().replace('ё', 'е')
    text = re.sub(r'\[\d+\]|[\[(][a-z0-9]*[a-z][a-z0-9]*[-/][a-z0-9/.-]+[\])]', ' ', text)
    text = re.sub(r'(\d+)\s*/\s*(\d+)\s*(?:gb|гб)\b', r'\1gb \2gb', text)
    text = re.sub(r'(\d+)\s*(?:tb|тб)\b', lambda m: f' {int(m[1]) * 1024}gb ', text)
    text = re.sub(r'(\d+)\s*(?:gb|гб)\b', r' \1gb ', text)
    text = re.sub(r'\b(?:geforce|radeon)\b', ' ', text)
    text = re.sub(r'\bps([45])\b', r'playstation \1', text)
    text = text.replace('wi-fi', 'wifi').replace('+', ' plus ')
    ignored = IGNORED | {'приставка', 'консоль'}
    return {BRAND_MAP.get(t, t) if t not in {'playstation'} else t
            for t in re.findall(r'[a-zа-я0-9]+(?:\.[0-9]+)?', text) if t not in ignored}


def extract_canonical_key(title: str) -> Optional[str]:
    if not title:
        return None
    from detector import is_junk_accessory
    if is_junk_accessory(title):
        return None
    family = _family_key(title)
    if not family:
        return None
    # Regular iPhones have fixed RAM per generation/storage; retain existing SKU aliases.
    if family.startswith('apple:iphone ') and not family.startswith('apple:iphone se'):
        return family
    return family + '|spec:' + ' '.join(sorted(identity_tokens(title)))


def model_tokens(title):
    text = unicodedata.normalize('NFKC', title).lower().replace('ё', 'е')
    # GB and ГБ are equivalent; retain capacity as part of the identity.
    text = re.sub(r'(\d+)\s*(?:tb|тб)\b', lambda m: f' {int(m[1]) * 1024}gb ', text)
    text = re.sub(r'(\d+)\s*(?:gb|гб)\b', r' \1gb ', text)
    text = re.sub(r'(\d+)\s*(?:mb|мб)\b', r' \1mb ', text)
    return {t for t in re.findall(r'[a-zа-я0-9]+', text) if t not in IGNORED}


_RE_DUAL_SIM = re.compile(r'dual[\s-]*sim|\b2\s*[-x]?\s*sim\b|две\s*sim', re.IGNORECASE)
_RE_ESIM = re.compile(r'\be[\s-]*sim\b', re.IGNORECASE)
_RE_NANO_SIM = re.compile(r'nano[\s-]*sim|\bsim\s*\+\s*e[\s-]*sim|physical\s*sim|физическ\w*\s*sim', re.IGNORECASE)


def sim_variant(title: str) -> str:
    """Вариант SIM, если он явно указан: 'dual', 'nano+esim', 'esim'; иначе ''.

    Для iPhone это разные артикулы с разной ценой (Dual SIM, nano-SIM + eSIM, только eSIM).
    """
    text = unicodedata.normalize('NFKC', title or '')
    if _RE_DUAL_SIM.search(text):
        return 'dual'
    has_esim = bool(_RE_ESIM.search(text))
    if has_esim and _RE_NANO_SIM.search(text):
        return 'nano+esim'
    return 'esim' if has_esim else ''


def variants_compatible(left: str, right: str) -> bool:
    """Явно указанные разные варианты SIM — разные товары; неуказанный вариант не противоречит."""
    a, b = sim_variant(left), sim_variant(right)
    return not (a and b and a != b)


# Формат ключа от AI: <brand>:<model>[:<spec>] — латиница, цифры, пробелы и .-+
_RE_AI_KEY = re.compile(r'^[a-z0-9][a-z0-9-]{0,30}:[a-z0-9][a-z0-9 .+-]{0,60}(?::[a-z0-9][a-z0-9 .+-]{0,30})?$')


def valid_ai_canonical_key(key: str) -> bool:
    return bool(isinstance(key, str) and _RE_AI_KEY.match(key))


def same_model(left, right):
    """Сравнивает две модели: сначала по каноническому ключу, затем консервативно по токенам.

    Разные явно указанные варианты SIM (eSIM / Dual SIM / nano-SIM + eSIM) — не одна модель.
    """
    if not variants_compatible(left, right):
        return False
    k_left = extract_canonical_key(left)
    k_right = extract_canonical_key(right)
    if k_left and k_right:
        return k_left == k_right

    a, b = model_tokens(left), model_tokens(right)
    # Require an actual model identifier, not just a brand or generic device name.
    return bool(a and a == b and any(any(c.isdigit() for c in t) for t in a))


def search_terms(title):
    terms = model_tokens(title)
    # Capacity may be spelled differently in FTS: use brand/model tokens instead.
    terms = [t for t in terms if not re.fullmatch(r'\d+(?:gb|mb)', t)]
    terms.sort(key=lambda t: (not any(c.isdigit() for c in t), -len(t), t))
    return terms[:3]

