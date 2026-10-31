"""
Social Content Intelligence & Stories TTL (S2-E08, KZ Price Hunter 2.0).
Parses unstructured product offers, prices, and conditions from Telegram channels,
Instagram posts, stories, and direct messages across Kazakhstan.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from domain.models import Availability, Condition, Offer, utc_now_iso
from domain.query_parser import KZ_CITIES_MAP


class ContentType:
    """Types of social & messenger content."""
    POST = "POST"              # Permanent feed post (Instagram / Telegram)
    STORY = "STORY"            # Ephemeral 24h story (Instagram / Telegram)
    MESSAGE = "MESSAGE"        # Telegram channel/group message
    HIGHLIGHT = "HIGHLIGHT"    # Saved Instagram story highlight
    REEL = "REEL"              # Video reel description

    ALL = {POST, STORY, MESSAGE, HIGHLIGHT, REEL}


@dataclass
class ContentObservation:
    """
    Raw observation of unstructured commercial content from social platforms.
    """
    id: str = field(default_factory=lambda: f"cobs_{uuid.uuid4().hex[:12]}")
    seller_id: str = ""
    channel_id: str = ""
    content_type: str = ContentType.POST
    post_id: Optional[str] = None
    text: str = ""
    media_urls: List[str] = field(default_factory=list)
    published_at: Optional[str] = None
    observed_at: str = field(default_factory=utc_now_iso)
    expires_at: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Auto-calculate default TTL for ephemeral content (Stories = 24h)
        if not self.expires_at and self.content_type == ContentType.STORY:
            try:
                obs_dt = datetime.fromisoformat(self.observed_at)
            except Exception:
                obs_dt = datetime.now(timezone.utc)
            self.expires_at = (obs_dt + timedelta(hours=24)).isoformat()

    def is_expired(self, now_iso: Optional[str] = None) -> bool:
        if not self.expires_at:
            return False
        try:
            now_dt = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
            exp_dt = datetime.fromisoformat(self.expires_at)
            # ISO timestamps without offsets are interpreted as UTC throughout the
            # application. Normalize both sides before comparing to avoid treating
            # an expired Story as active when its source omitted a timezone suffix.
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            else:
                now_dt = now_dt.astimezone(timezone.utc)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            else:
                exp_dt = exp_dt.astimezone(timezone.utc)
            return now_dt >= exp_dt
        except Exception:
            return False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> ContentObservation:
        m_urls = row.get("media_urls")
        if isinstance(m_urls, str):
            try:
                m_list = json.loads(m_urls)
            except Exception:
                m_list = []
        elif isinstance(m_urls, list):
            m_list = m_urls
        else:
            m_list = []

        meta = row.get("raw_metadata")
        if isinstance(meta, str):
            try:
                meta_dict = json.loads(meta)
            except Exception:
                meta_dict = {}
        elif isinstance(meta, dict):
            meta_dict = meta
        else:
            meta_dict = {}

        return cls(
            id=row["id"],
            seller_id=row["seller_id"],
            channel_id=row["channel_id"],
            content_type=row["content_type"],
            post_id=row.get("post_id"),
            text=row.get("text") or "",
            media_urls=m_list,
            published_at=row.get("published_at"),
            observed_at=row.get("observed_at") or utc_now_iso(),
            expires_at=row.get("expires_at"),
            raw_metadata=meta_dict,
        )


@dataclass
class ExtractedOfferCandidate:
    """
    Extracted commercial candidate parsed from raw social content.
    """
    raw_title: str
    price: float
    old_price: Optional[float] = None
    condition: str = Condition.NEW
    city: str = "Все"
    availability: str = Availability.IN_STOCK
    confidence: float = 0.8
    expires_at: Optional[str] = None
    raw_snippet: str = ""
    is_unit_price: bool = False
    quantity: Optional[float] = None
    unit: Optional[str] = None
    size_or_volume: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def price_basis(self) -> Optional[Dict[str, Any]]:
        """За что указана цена, если это не одна штука: основа идентичности товара (FA69-07).

        100 г и 1 кг, 1 л и 1,5 л, упаковка и штука — разные товары: сравнивать их цены напрямую нельзя.
        Вес и объём приводятся к граммам и миллилитрам, поэтому «1000 г» и «1 кг» — одно и то же.
        """
        unit = (self.unit or "").strip().lower() or None
        quantity = self.quantity if self.quantity and self.quantity > 0 else None
        match = _SIZE_VOLUME_RE.search(self.size_or_volume or "")
        if match and (unit is None or quantity in (None, 1.0)):
            size_unit = _UNIT_ALIASES[match.group(2).lower()]
            if unit in (None, "pcs") or _UNIT_BASE[size_unit][0] == _UNIT_BASE.get(unit, ("",))[0]:
                unit = size_unit
                quantity = float(match.group(1).replace(",", "."))
        unit = _UNIT_ALIASES.get(unit, unit) if unit else None
        if unit not in _UNIT_BASE:
            return None
        quantity = quantity or 1.0
        if unit == "pcs" and quantity == 1.0 and not self.is_unit_price:
            return None
        base_unit, factor = _UNIT_BASE[unit]
        amount = f"{quantity * factor:.6g}{base_unit}"
        return {
            "is_unit_price": bool(self.is_unit_price),
            "quantity": quantity,
            "unit": unit,
            "pack": f"{'per' if self.is_unit_price else 'pack'}:{amount}",
            "label": f"{'за ' if self.is_unit_price else ''}{quantity:g} {_UNIT_LABELS[unit]}",
        }


_UNIT_BASE = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "pcs": ("pcs", 1)}
_UNIT_LABELS = {"g": "г", "kg": "кг", "ml": "мл", "l": "л", "pcs": "шт"}
_UNIT_ALIASES = {"г": "g", "гр": "g", "g": "g", "кг": "kg", "kg": "kg", "мл": "ml", "ml": "ml",
                 "л": "l", "l": "l", "шт": "pcs", "pcs": "pcs"}
_SIZE_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|kg|гр|г|g|мл|ml|л|l)(?![a-zа-я])", re.IGNORECASE)


class SocialContentExtractor:
    """
    Deterministic rule-based extractor for unstructured Telegram & Instagram commercial content.
    Extracts item titles, prices in KZT, conditions, cities, and validity periods.
    """

    # False-positive price rejection patterns (Kazakhstan phone numbers)
    PHONE_PATTERN = re.compile(r"(?:\+?7|8)\s*\(?[7]\d{2}\)?\s*\d{3}[\s-]?\d{2}[\s-]?\d{2}")

    # Memory / Storage tokens that look like numbers (e.g. 256гб, 512 gb, 1024 mb)
    STORAGE_PREFIX_SUFFIX = re.compile(r"\b(\d+)\s*(?:gb|гб|mb|мб|tb|тб|hz|гц)\b", re.IGNORECASE)

    # Condition regexes
    RE_USED = re.compile(
        r"\b(?:б/у|бу|бывший в употреблении|в идеале|идеал|состояние \d/\d|микроцарапины|с пробегом|second hand)\b",
        re.IGNORECASE
    )
    RE_REFURBISHED = re.compile(
        r"\b(?:восстановленный|refurbished|после ремонта|после сц|заменен экран|заменен аккум)\b",
        re.IGNORECASE
    )
    RE_OPEN_BOX = re.compile(
        r"\b(?:витрина|витринный образец|вскрыта коробка|распечатан|уценка|уцененный|повреждена упаковка)\b",
        re.IGNORECASE
    )
    RE_NEW = re.compile(
        r"\b(?:новый|новая|новое|в упаковке|запечатан|запечатанный|в пленке|не активирован)\b",
        re.IGNORECASE
    )

    @classmethod
    def _clean_phones_and_storage(cls, text: str) -> str:
        """Masks phone numbers and storage capacities so they are not read as prices.

        Маска сохраняет длину фрагмента: индексы в очищенной строке совпадают с индексами
        в исходной, поэтому из исходной же строки можно вырезать название без цены.
        """
        masked = cls.PHONE_PATTERN.sub(lambda match: "X" * len(match.group(0)), text)
        return cls.STORAGE_PREFIX_SUFFIX.sub(lambda match: "X" * len(match.group(0)), masked)

    NUMBER_RUN = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+|\d{2,9})(?!\d)")
    CURRENCY_AFTER = re.compile(r"[ \u00a0]*(?:тенге|тг|₸|kzt|т\b)", re.IGNORECASE)
    PRICE_KEYWORD_BEFORE = re.compile(r"(?:цена|стоимость|бағасы)\s*[:\-]?\s*$", re.IGNORECASE)
    DISCOUNT_MARKERS = re.compile(
        r"(?:→|->|—>|вместо|было|стало|скидка|старая цена|новая цена)", re.IGNORECASE)

    @classmethod
    def _normalize_run(cls, raw: str) -> Tuple[Optional[float], str]:
        """Отделяет номер модели от цены в числовом прогоне.

        «iPhone 15 399 000» — модель 15 и цена 399 000: цену собирают группы по три цифры,
        а ведущая группа из одной-двух цифр — номер модели. Ведущая группа из трёх цифр
        и одиночная цифра перед тысячами («1 299 000») остаются частью цены.
        """
        groups = [g for g in re.split(r"[ \u00a0]+", raw.strip()) if g]
        if not groups:
            return None, raw
        if len(groups) == 1:
            try:
                return float(groups[0]), groups[0]
            except ValueError:
                return None, raw
        if len(groups) >= 3 and 1 <= len(groups[0]) <= 2:
            kept = groups[1:]
            try:
                rest = float("".join(kept))
            except ValueError:
                rest = 0.0
            if rest >= 500:
                return rest, " ".join(kept)
        try:
            return float("".join(groups)), " ".join(groups)
        except ValueError:
            return None, raw

    @classmethod
    def _price_entries(cls, line: str) -> List[Tuple[float, int, int]]:
        """Находит цены в строке как (значение, начало, конец) в координатах строки.

        Учитываются только числа, привязанные к валюте, слову «цена» или суффиксу «к»:
        иначе номер модели («iPhone 15») или объём памяти превращались бы в цену.
        Пары «было/стало» и «X → Y» распознаются отдельно по маркеру скидки.
        """
        entries: List[Tuple[float, int, int]] = []
        for match in cls.NUMBER_RUN.finditer(line):
            raw = match.group(1)
            after = line[match.end():]
            before = line[:match.start()]
            anchored = bool(cls.CURRENCY_AFTER.match(after))
            keyword = bool(cls.PRICE_KEYWORD_BEFORE.search(before))
            if not (anchored or keyword):
                continue
            value, kept = cls._normalize_run(raw)
            if value is None or not (500 <= value <= 100_000_000):
                continue
            start = match.start() + (len(raw) - len(kept)) if kept != raw else match.start()
            entries.append((value, start, match.end()))

        # «450к»: суффикс «к» относится только к числу непосредственно перед ним,
        # иначе «V15 250к» склеивалось бы в 15 250.
        for match in re.finditer(r"(?<!\d)(\d{1,4})[ \u00a0]*[кk](?![а-яa-z0-9])", line, re.IGNORECASE):
            value = float(match.group(1)) * 1000.0
            if 500 <= value <= 100_000_000:
                entries.append((value, match.start(1), match.end()))

        if len(entries) < 2 and cls.DISCOUNT_MARKERS.search(line):
            pairs: List[Tuple[float, int, int]] = []
            for match in cls.NUMBER_RUN.finditer(line):
                raw = match.group(1)
                value, kept = cls._normalize_run(raw)
                if value is None or not (500 <= value <= 100_000_000):
                    continue
                start = match.start() + (len(raw) - len(kept)) if kept != raw else match.start()
                pairs.append((value, start, match.end()))
            entries = pairs[-2:]

        entries.sort(key=lambda entry: entry[1])
        deduped: List[Tuple[float, int, int]] = []
        for entry in entries:
            if deduped and entry[1] < deduped[-1][2]:
                continue
            deduped.append(entry)
        return deduped

    @classmethod
    def _extract_price_and_old_price(cls, line: str) -> Tuple[Optional[float], Optional[float]]:
        entries = cls._price_entries(cls._clean_phones_and_storage(line))
        if not entries:
            return None, None
        if len(entries) >= 2:
            first, last = entries[0][0], entries[-1][0]
            if first > last:
                return last, first
            return first, last
        return entries[0][0], None

    @classmethod
    def _parse_num_val(cls, raw: str) -> Optional[float]:
        """Converts raw string with spaces or 'к' suffix to float."""
        clean = raw.strip().lower()
        if clean.endswith("к") or clean.endswith("k"):
            num_part = clean[:-1].strip()
            try:
                return float(num_part) * 1000.0
            except ValueError:
                return None
        # Remove spaces
        no_spaces = re.sub(r"\s+", "", clean)
        try:
            val = float(no_spaces)
            return val
        except ValueError:
            return None

    @classmethod
    def _detect_condition(cls, text: str) -> str:
        if cls.RE_REFURBISHED.search(text):
            return Condition.REFURBISHED
        if cls.RE_OPEN_BOX.search(text):
            return Condition.OPEN_BOX
        if cls.RE_USED.search(text):
            return Condition.USED
        if cls.RE_NEW.search(text):
            return Condition.NEW
        return Condition.NEW

    @classmethod
    def _detect_city(cls, text: str) -> str:
        text_lower = text.lower()
        for variant, canonical in KZ_CITIES_MAP.items():
            pattern = r"\b" + re.escape(variant) + r"\b"
            if re.search(pattern, text_lower):
                return canonical
        return "Все"

    @classmethod
    def extract_candidates(cls, observation: ContentObservation) -> List[ExtractedOfferCandidate]:
        """
        Parses observation text line by line or block by block to find product offers.
        """
        candidates: List[ExtractedOfferCandidate] = []
        if not observation.text or not observation.text.strip():
            return candidates

        raw_text = observation.text
        # Inherit expiration from observation
        default_expires = observation.expires_at

        # Process lines
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

        # Global city detection if mentioned in post header/footer
        global_city = cls._detect_city(raw_text)
        global_condition = cls._detect_condition(raw_text)

        for line in lines:
            # Skip pure contact or hashtag lines
            if line.startswith("#") or "whatsapp" in line.lower() or "direct" in line.lower() and len(line) < 30:
                continue

            price, old_price = cls._extract_price_and_old_price(line)
            if price is None:
                continue

            # Название — всё, что стоит до первой цены. Индексы совпадают, потому что
            # маска телефона/памяти не меняет длину строки.
            entries = cls._price_entries(cls._clean_phones_and_storage(line))
            title_part = line[:entries[0][1]].strip() if entries else ""
            title_part = re.sub(
                r"(?:цена|стоимость|бағасы|было|стало|старая цена|новая цена)\s*[:\-–—]*\s*$",
                "", title_part, flags=re.IGNORECASE).strip()
            title_part = title_part.strip(" :-–—•*·")

            # Clean bullet markers (-, •, 1., 2.)
            title_part = re.sub(r"^[\d\.\-\•\*\—\–\s]+", "", title_part).strip()

            if len(title_part) < 3:
                # Title too short on this line, try combining with line before it
                continue

            line_condition = cls._detect_condition(line)
            effective_condition = line_condition if line_condition != Condition.NEW else global_condition

            line_city = cls._detect_city(line)
            effective_city = line_city if line_city != "Все" else global_city

            cand = ExtractedOfferCandidate(
                raw_title=title_part,
                price=price,
                old_price=old_price,
                condition=effective_condition,
                city=effective_city,
                availability=Availability.IN_STOCK,
                confidence=0.85 if old_price else 0.8,
                expires_at=default_expires,
                raw_snippet=line,
            )
            candidates.append(cand)

        return candidates

    @classmethod
    def candidate_to_offer(
        cls,
        candidate: ExtractedOfferCandidate,
        observation: ContentObservation,
        product_id: str,
        external_sku: Optional[str] = None,
    ) -> Offer:
        """
        Converts an extracted candidate into a fully formed domain Offer.
        """
        offer_id = f"off_soc_{uuid.uuid4().hex[:12]}"
        sku = external_sku or f"SKU-SOC-{candidate.raw_title[:16].strip().replace(' ', '-').upper()}"
        # Keep only a source-provided permalink. Do not invent a Telegram URL:
        # Instagram has no t.me path, and a missing Graph permalink is not a Telegram post.
        url = str(observation.raw_metadata.get("post_url") or "").strip()

        # TTL: просроченное наблюдение (сторис/временная акция) не может стать доступным
        # к покупке предложением. Иначе SearchCore посчитает его purchasable и предложит
        # цену, которой уже нет. Правило «отсутствие данных не ответ» здесь не работает:
        # срок истечения известен, значит состояние — out_of_stock, а не unknown.
        availability = (Availability.OUT_OF_STOCK if observation.is_expired()
                        else candidate.availability)

        return Offer(
            id=offer_id,
            product_id=product_id,
            seller_id=observation.seller_id,
            channel_id=observation.channel_id,
            external_sku=sku,
            url=url,
            price=candidate.price,
            old_price=candidate.old_price,
            condition=candidate.condition,
            availability=availability,
            city=candidate.city,
            observed_at=observation.observed_at,
            expires_at=observation.expires_at,
        )
