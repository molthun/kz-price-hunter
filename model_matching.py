"""Conservative matching: an uncertain variant is not a price comparison."""
import re
import unicodedata

IGNORED = set('''смартфон телефон мобильный ноутбук планшет телевизор монитор процессор
видеокарта наушники часы пылесос игровой игровая игровое smart smartphone laptop
черный чёрный белый серый синий зеленый зелёный красный бежевый серебристый золотой
фиолетовый розовый black white gray grey blue green red silver gold purple pink
beige desert titanium титан титановый natural натуральный корпус цвет'''.split())


def model_tokens(title):
    text = unicodedata.normalize('NFKC', title).lower().replace('ё', 'е')
    # GB and ГБ are equivalent; retain capacity as part of the identity.
    text = re.sub(r'(\d+)\s*(?:tb|тб)\b', lambda m: f' {int(m[1]) * 1024}gb ', text)
    text = re.sub(r'(\d+)\s*(?:gb|гб)\b', r' \1gb ', text)
    text = re.sub(r'(\d+)\s*(?:mb|мб)\b', r' \1mb ', text)
    return {t for t in re.findall(r'[a-zа-я0-9]+', text) if t not in IGNORED}


def same_model(left, right):
    a, b = model_tokens(left), model_tokens(right)
    # Require an actual model identifier, not just a brand or generic device name.
    return bool(a and a == b and any(any(c.isdigit() for c in t) for t in a))


def search_terms(title):
    terms = model_tokens(title)
    # Capacity may be spelled differently in FTS: use brand/model tokens instead.
    terms = [t for t in terms if not re.fullmatch(r'\d+(?:gb|mb)', t)]
    terms.sort(key=lambda t: (not any(c.isdigit() for c in t), -len(t), t))
    return terms[:3]
