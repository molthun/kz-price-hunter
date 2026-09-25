"""
Domain entities and normalizers for Seller Identity and Seller Graph (KZ Price Hunter 2.0).
Provides deterministic normalization for phones, domains, BIN, and marketplace IDs in Kazakhstan.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit


def utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class IdentityType:
    PHONE = "phone"
    DOMAIN = "domain"
    BIN = "bin"
    MARKETPLACE_SELLER_ID = "marketplace_seller_id"
    SOCIAL = "social"
    LEGAL_NAME = "legal_name"

    ALL = {PHONE, DOMAIN, BIN, MARKETPLACE_SELLER_ID, SOCIAL, LEGAL_NAME}
    STRONG_KEYS = {PHONE, DOMAIN, BIN, MARKETPLACE_SELLER_ID}


class MatchDecision:
    AUTO_MATCH = "AUTO_MATCH"  # Exact match on strong verified keys without conflict
    REVIEW = "REVIEW"          # Ambiguous similarity, requires manual or AI review
    REJECT = "REJECT"          # Conflict of strong keys or completely distinct entities

    ALL = {AUTO_MATCH, REVIEW, REJECT}


@dataclass
class SellerIdentity:
    """A verified digital footprint / identity trait of a seller."""
    id: str
    seller_id: str
    identity_type: str
    identity_value: str
    confidence: float = 1.0
    source: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> SellerIdentity:
        d = dict(row)
        return cls(
            id=d["id"],
            seller_id=d["seller_id"],
            identity_type=d["identity_type"],
            identity_value=d["identity_value"],
            confidence=float(d.get("confidence") or 1.0),
            source=d.get("source"),
            created_at=d.get("created_at") or utc_now_iso(),
        )


@dataclass
class SellerMatchResult:
    """Result of attempting to match an incoming merchant candidate with known sellers."""
    decision: str
    target_seller_id: Optional[str] = None
    confidence: float = 0.0
    matched_identities: List[Tuple[str, str]] = field(default_factory=list)  # (type, value)
    conflicting_identities: List[Tuple[str, str, str]] = field(default_factory=list)  # (type, cand_val, exist_val)
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Deterministic Normalizers for Kazakhstan Business Entities
# ---------------------------------------------------------------------------

def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a Kazakhstan (or international) phone number to E.164 standard.
    Examples:
      "+7 (701) 123-45-67" -> "+77011234567"
      "87011234567"        -> "+77011234567"
      "77011234567"        -> "+77011234567"
      "8 (727) 222-33-44"  -> "+77272223344"
    Returns None if raw is invalid or has wrong length.
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return None

    # Kazakhstan 11-digit numbers starting with 8 or 7
    if len(digits) == 11:
        if digits.startswith("8"):
            return f"+7{digits[1:]}"
        elif digits.startswith("7"):
            return f"+{digits}"
        else:
            return f"+{digits}"

    # 10-digit without country code (e.g. 7011234567)
    if len(digits) == 10 and digits.startswith("7"):
        return f"+7{digits}"

    # Valid international E.164: 10 to 15 digits
    if 10 <= len(digits) <= 15:
        return f"+{digits}"

    return None


def normalize_domain(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a website domain or URL into a canonical domain name.
    Examples:
      "https://www.shop.kz/catalog/phone" -> "shop.kz"
      "http://4mobile.kz:8080"            -> "4mobile.kz"
      "WWW.DNS-SHOP.KZ/"                  -> "dns-shop.kz"
    """
    if not raw:
        return None

    s = str(raw).strip().lower()
    if not s:
        return None

    # If scheme is missing, prepend // for urlsplit to parse netloc properly
    if not s.startswith("http://") and not s.startswith("https://") and not s.startswith("//"):
        s = "//" + s

    try:
        parts = urlsplit(s)
        netloc = parts.netloc or parts.path.split("/")[0]
    except Exception:
        return None

    # Strip port if present
    netloc = netloc.split(":")[0]

    # Strip leading www. or m.
    netloc = re.sub(r"^(?:www|m)\.", "", netloc)

    # Basic validity check: must have a dot and valid host characters
    if "." not in netloc or re.search(r"[^a-z0-9.\-]", netloc):
        return None

    # Strip trailing dots or dashes
    netloc = netloc.strip(".-")
    return netloc or None


def normalize_bin(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a Kazakhstan Business Identification Number (БИН / BIN).
    BIN must be exactly 12 digits.
    Returns None if invalid.
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d]", "", str(raw))
    if len(digits) == 12:
        return digits
    return None


def normalize_business_name(raw: Optional[str]) -> str:
    """
    Normalizes a company or merchant name by stripping corporate suffixes
    (ТОО, ИП, АО, LLP, etc.), punctuation, and extra whitespace.
    Examples:
      'ТОО "Белый Ветер KZ"' -> "белый ветер kz"
      'ИП 4Mobile'           -> "4mobile"
      'АО "Technodom Оперейтор"' -> "technodom оперейтор"
    """
    if not raw:
        return ""

    s = str(raw).strip().lower()

    # Remove quotes
    s = re.sub(r'["\'«»“”„`]', "", s)

    # Remove legal forms in Russian, Kazakh and English
    legal_forms = [
        r"\bтоо\b", r"\bип\b", r"\bао\b", r"\bооо\b", r"\bзао\b", r"\bжао\b", r"\bжақ\b", r"\bжшс\b",
        r"\bllp\b", r"\bjsc\b", r"\bcorp\b", r"\bltd\b", r"\binc\b", r"\bчп\b",
    ]
    for form in legal_forms:
        s = re.sub(form, "", s)

    # Clean punctuation and redundant whitespace
    s = re.sub(r"[^\w\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def validate_kz_bin_checksum(raw: Optional[str]) -> bool:
    """
    Validates Kazakhstan 12-digit BIN/IIN checksum according to national standard.
    Algorithm uses modulo 11 with two weight vectors.
    """
    if not raw:
        return False
    digits = re.sub(r"[^\d]", "", str(raw))
    if len(digits) != 12:
        return False

    d = [int(x) for x in digits]
    # Check 1: weights 1..11
    w1 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1]
    s1 = sum(d[i] * w1[i] for i in range(11))
    r1 = s1 % 11
    if r1 < 10:
        return r1 == d[11]

    # Check 2: secondary weights if remainder was 10
    w2 = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]
    s2 = sum(d[i] * w2[i] for i in range(11))
    r2 = s2 % 11
    if r2 < 10:
        return r2 == d[11]

    return False


def get_registrable_domain(domain: Optional[str]) -> Optional[str]:
    """
    Extracts the registrable domain (e.g., 'catalog.shop.kz' -> 'shop.kz', 'astana.mechta.kz' -> 'mechta.kz').
    Preserves second-level ccTLDs like .com.kz, .org.kz.
    """
    if not domain:
        return None
    d = normalize_domain(domain)
    if not d:
        return None

    parts = d.split(".")
    if len(parts) <= 2:
        return d

    kz_second_level = {"com.kz", "org.kz", "net.kz", "edu.kz", "gov.kz", "mil.kz", "co.uk"}
    last_two = f"{parts[-2]}.{parts[-1]}"
    if last_two in kz_second_level:
        if len(parts) >= 3:
            return f"{parts[-3]}.{last_two}"
        return d

    return f"{parts[-2]}.{parts[-1]}"


# ---------------------------------------------------------------------------
# Родовые слова (Generic names) для розничного бизнеса Казахстана (B2)
# ---------------------------------------------------------------------------

GENERIC_BUSINESS_TERMS = {
    # русские термины
    "магазин", "маркет", "супермаркет", "гипермаркет", "дискаунтер", "дисконт", "шоп", "shop",
    "аптека", "аптеки", "фарм", "оптика", "зоомаркет", "зоотовары", "ветклиника", "товары", "товаров",
    "мебель", "мебели", "дом", "мир", "планета", "центр", "комфорт", "уют", "галерея", "салон",
    "стройматериалы", "строймаркет", "стройка", "ремонт", "инструменты", "крепеж", "материалы",
    "продукты", "продуктовый", "продуктов", "гастроном", "минимаркет", "мясо", "овощи", "фрукты",
    "электроника", "электроники", "техника", "техники", "техномаркет", "смартфоны", "телефоны", "компьютеры",
    "одежда", "одежды", "обувь", "обуви", "бутик", "текстиль", "мода", "трикотаж",
    "цветы", "цветов", "флористика", "подарки", "подарков", "сувениры", "книги", "книг", "канцтовары",
    "авто", "автозапчасти", "автомаркет", "шины", "колеса", "моторс",
    "плюс", "люкс", "элит", "онлайн", "online", "экспресс", "express", "сити", "city",
    "групп", "group", "сервис", "service", "company", "компани", "компания",
    # казахские термины
    "дүкен", "дүкені", "маркеті", "дәріхана", "дәріханасы", "нарық", "базар", "орталық", "орталығы",
    "сауда", "құрылыс", "жиһаз", "жиһазы", "өнімдер", "өнімдері", "тағам", "киім",
    "аяқкиім", "гүлдер", "кітаптар", "көлік", "автокөлік", "бөлшектер", "қызмет",
}



def is_generic_business_name(name: Optional[str]) -> bool:
    """
    Determines if a business name consists entirely of common/generic retail terms.
    If true, the name cannot be used for AUTO_MATCH or distinctive REVIEW without strong keys.
    """
    if not name:
        return True
    norm = normalize_business_name(name)
    if not norm:
        return True
    words = [w for w in re.split(r"[\s\-]+", norm) if w]
    if not words:
        return True
    return all(w in GENERIC_BUSINESS_TERMS for w in words)


# ---------------------------------------------------------------------------
# Слабые идентификаторы: значения, которые делят многие продавцы
# ---------------------------------------------------------------------------

# Домены площадок: на них торгуют тысячи разных продавцов
PLATFORM_DOMAINS = {
    # соцсети и мессенджеры
    "instagram.com", "facebook.com", "t.me", "telegram.me", "vk.com", "wa.me", "whatsapp.com",
    "tiktok.com", "youtube.com", "pinterest.com",
    # маркетплейсы и агрегаторы Казахстана и СНГ
    "kaspi.kz", "halykmarket.kz", "market.forte.kz", "forte.kz", "wildberries.kz", "ozon.kz",
    "satu.kz", "olx.kz", "krisha.kz", "market.yandex.kz", "aliexpress.com",
    # карты и справочники
    "2gis.kz", "2gis.com", "google.com", "maps.google.com",
    # конструкторы сайтов и бесплатные хостинги
    "wixsite.com", "wix.com", "tilda.ws", "tilda.cc", "shopify.com", "blogspot.com",
    "wordpress.com", "ucoz.ru", "webnode.com", "site123.me",
}

# Общие горячие линии, колл-центры маркетплейсов и банков (D1)
SHARED_HOTLINES = {
    "+77272585989", "+77272585965", "+77272585955", "+77272587575",
    "+77272590777", "+77172611699",
}

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def is_platform_domain(domain: Optional[str]) -> bool:
    """Домен площадки, а не продавца. Поддомены площадки — тоже площадка."""
    if not domain:
        return False
    return any(domain == p or domain.endswith("." + p) for p in PLATFORM_DOMAINS)


def is_trivial_value(value: Optional[str]) -> bool:
    """Значение-заглушка: из одного повторяющегося символа или из одних нулей.

    Такие значения приходят из разметки магазинов сплошь и рядом («+7 700 000 00 00»,
    БИН «000000000000») и не принадлежат никакому конкретному продавцу.
    """
    if not value:
        return True
    digits = re.sub(r"[^\d]", "", str(value))
    if not digits:
        return True
    if len(set(digits)) == 1:
        return True
    # У телефона показательна абонентская часть: «+7 700 000 00 00» целиком не однородна
    # (есть семёрки кода), но последние семь цифр — одни нули, и это заглушка.
    if len(digits) >= 7 and len(set(digits[-7:])) == 1:
        return True
    return False


def is_weak_identity(identity_type: str, value: Optional[str]) -> bool:
    """Нельзя ли считать это значение сильным ключом.

    Ключ слабый, если его делят многие продавцы: домен площадки, IP общего хостинга,
    телефон-заглушка или колл-центра, БИН из одних нулей. Частотную часть правила
    («значение уже встречается у N продавцов») проверяет репозиторий — она требует базы.
    """
    if not value:
        return True
    if identity_type == IdentityType.DOMAIN:
        if is_platform_domain(value) or _IPV4_RE.match(value):
            return True
        return False
    if identity_type == IdentityType.PHONE:
        if is_trivial_value(value):
            return True
        digits = re.sub(r"[^\d]", "", str(value))
        # Short service numbers (e.g., 9999, 7575, 7111)
        if len(digits) <= 5:
            return True
        # Freephone 8-800
        if str(value).startswith("+7800") or digits.startswith("8800") or digits.startswith("7800"):
            return True
        # Known marketplace/bank shared hotlines
        if value in SHARED_HOTLINES:
            return True
        return False
    if identity_type == IdentityType.MARKETPLACE_SELLER_ID:
        # Без площадки номер ничего не значит: 42 на Kaspi и 42 на Forte — разные продавцы
        return not marketplace_id_has_namespace(value)
    if identity_type == IdentityType.BIN:
        # БИН — самый сильный ключ (уверенность 1.0 и немедленное совпадение), поэтому номер
        # с неверной контрольной цифрой не должен им становиться: это опечатка или выдумка,
        # а не идентификатор юридического лица. Проверка была написана и покрыта тестом, но
        # ниоткуда не вызывалась, и два разных продавца склеивались по битому номеру.
        # normalize_bin остаётся нормализатором формы: решение о пригодности ключа принимается здесь.
        return is_trivial_value(value) or not validate_kz_bin_checksum(value)
    return False

def normalize_marketplace_id(marketplace: Optional[str], external_id: Optional[str]) -> Optional[str]:
    """Идентификатор продавца на площадке в виде «площадка:id».

    Локальные номера площадок независимы: продавец 42 на Kaspi и продавец 42 на Forte —
    разные люди. Без пространства имён такой номер становился сильным ключом и склеивал их
    с уверенностью 0.95. Поэтому ключом считается только пара, и только она.
    """
    market = re.sub(r"[^a-z0-9_.-]+", "", str(marketplace or "").strip().lower())
    ident = str(external_id or "").strip()
    if not market or not ident:
        return None
    if ":" in ident:
        # Уже в каноническом виде — не заворачиваем второй раз
        return ident.lower() if ident.lower().startswith(f"{market}:") else f"{market}:{ident}"
    return f"{market}:{ident}"


def marketplace_id_has_namespace(value: Optional[str]) -> bool:
    """Есть ли у идентификатора площадки пространство имён. Голый номер ключом быть не может."""
    if not value:
        return False
    market, _, ident = str(value).partition(":")
    return bool(market.strip()) and bool(ident.strip())
