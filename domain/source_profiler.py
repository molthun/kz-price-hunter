"""
Source Profiler for KZ Price Hunter 2.0 (Stage 7 in SEARCH_PLATFORM_PLAN.md).
Determines the primary catalog source and format for a merchant.
Ranks collection tier from structured API/YML (Tier 1) down to AI/Vision (Tier 7).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from domain.models import utc_now_iso
from domain.seller_identity import normalize_domain


class ExtractionTier:
    """
    Preference order of data extraction (from cheapest/fastest to most expensive).
    Tier 1: Documented Feed / YML / API
    Tier 2: Structured Catalog Endpoint (REST/GraphQL/Bitrix JSON)
    Tier 3: Structured Website (Schema.org / Microdata)
    Tier 4: SSR HTML Catalog
    Tier 5: Messenger Text (Telegram)
    Tier 6: Social Text (Instagram post captions)
    Tier 7: Social Images/Stories (AI Vision - last resort)
    """
    FEED_API = 1
    STRUCTURED_ENDPOINT = 2
    STRUCTURED_HTML = 3
    SSR_HTML = 4
    MESSENGER_TEXT = 5
    SOCIAL_TEXT = 6
    SOCIAL_VISION = 7


class CatalogFormat:
    YML = "yml"
    FEED_XML = "feed_xml"
    JSON_API = "json_api"
    GRAPHQL = "graphql"
    SCHEMA_ORG = "schema_org"
    BITRIX_CATALOG = "bitrix_catalog"
    HTML = "html"
    TELEGRAM = "telegram"
    INSTAGRAM_TEXT = "instagram_text"
    INSTAGRAM_VISION = "instagram_vision"
    UNKNOWN = "unknown"


@dataclass
class SourceProfile:
    """
    Profile of a seller's catalog source, capabilities, and extraction tier.
    """
    id: str
    seller_id: Optional[str] = None
    candidate_id: Optional[str] = None
    source_url: str = ""
    domain: Optional[str] = None
    catalog_format: str = CatalogFormat.UNKNOWN
    extraction_tier: int = ExtractionTier.SSR_HTML
    # Наблюдали ли источник на самом деле. Профилирование по одному адресу — это догадка:
    # карта сайта, лента блога и страница входа по форме адреса неотличимы от каталога.
    # Пока observed = False, поля ниже означают «ожидаем», а не «проверено».
    observed: bool = False
    has_prices: bool = True
    has_availability: bool = True
    capabilities: Dict[str, bool] = field(default_factory=lambda: {
        "identity": True,
        "catalog": True,
        "price": True,
        "availability": True,
        "order": False,
    })
    confidence: float = 0.8
    notes: str = ""
    profiled_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> SourceProfile:
        d = dict(row)
        caps = d.get("capabilities_json") or d.get("capabilities") or "{}"
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except Exception:
                caps = {}
        return cls(
            id=d["id"],
            seller_id=d.get("seller_id"),
            candidate_id=d.get("candidate_id"),
            source_url=d.get("source_url") or "",
            domain=d.get("domain"),
            catalog_format=d.get("catalog_format") or CatalogFormat.UNKNOWN,
            extraction_tier=int(d.get("extraction_tier") or ExtractionTier.SSR_HTML),
            observed=bool(d.get("observed", 0)),
            has_prices=bool(d.get("has_prices", 1)),
            has_availability=bool(d.get("has_availability", 1)),
            capabilities=caps,
            confidence=float(d.get("confidence") or 0.8),
            notes=d.get("notes") or "",
            profiled_at=d.get("profiled_at") or utc_now_iso(),
        )


class SourceProfiler:
    """
    Analyzes merchant source URLs and determines the best catalog extraction strategy.
    """

    # Адреса, которые по форме похожи на фид, но каталогом не являются. Без этого списка
    # sitemap.xml и лента блога объявлялись каталогом первого уровня с ценами и наличием.
    NON_CATALOG_HINTS = ("sitemap", "/rss", "rss.xml", "/blog/", "/news/", "/atom",
                         "/auth", "/login", "/logout", "/register", "/oauth", "/token")

    # Признаки настоящей товарной выгрузки. Расширение файла тут не помощник: рабочая выгрузка
    # Белого Ветра оканчивается на .php и без этого списка определялась как обычный HTML.
    CATALOG_HINTS = ("catalog_export", "/export/", "yandex.php", "yandex_run", "/yml",
                     "price_list", "pricelist", "/merchant", "google_merchant")

    @staticmethod
    def _looks_like_catalog(u_lower: str) -> bool:
        if any(h in u_lower for h in SourceProfiler.NON_CATALOG_HINTS):
            return False
        return True

    # Что считается наблюдением источника. Догадка по адресу наблюдением не является:
    # признаком служит только то, что появляется при настоящем обращении — код ответа,
    # тип содержимого или прочитанный образец. Вызывающий может сказать это и прямо.
    OBSERVATION_HINTS = ("observed", "status_code", "content_type", "sample", "body")

    @classmethod
    def _was_observed(cls, hints: Dict[str, Any]) -> bool:
        if hints.get("observed") is True:
            return True
        return any(hints.get(key) for key in cls.OBSERVATION_HINTS if key != "observed")

    @classmethod
    def profile_url(
        cls,
        url: str,
        profile_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        seller_id: Optional[str] = None,
        hints: Optional[Dict[str, Any]] = None,
    ) -> SourceProfile:
        hints = hints or {}
        observed = cls._was_observed(hints)
        raw_url = str(url).strip()
        domain = normalize_domain(raw_url) or ""
        u_lower = raw_url.lower()

        # 1. YML / XML Feeds / API endpoints -> Tier 1
        looks_like_feed = (
            any(u_lower.endswith(ext) for ext in [".yml", ".xml"])
            or "/feed" in u_lower
            or "/yandex/market" in u_lower
            or any(h in u_lower for h in cls.CATALOG_HINTS)
        )
        if looks_like_feed and cls._looks_like_catalog(u_lower):
            fmt = CatalogFormat.YML if ".yml" in u_lower else CatalogFormat.FEED_XML
            return SourceProfile(
                id=profile_id or f"prof_{candidate_id or 'anon'}",
                seller_id=seller_id,
                candidate_id=candidate_id,
                source_url=raw_url,
                domain=domain,
                observed=observed,
                catalog_format=fmt,
                extraction_tier=ExtractionTier.FEED_API,
                has_prices=True,
                has_availability=True,
                capabilities={"identity": True, "catalog": True, "price": True, "availability": True, "order": False},
                confidence=0.7,
                notes="Предположение по адресу: похоже на товарную выгрузку (Tier 1), источник не открывался",
            )

        if ("/api/" in u_lower or u_lower.endswith(".json") or hints.get("has_json_api")) \
                and cls._looks_like_catalog(u_lower):
            return SourceProfile(
                id=profile_id or f"prof_{candidate_id or 'anon'}",
                seller_id=seller_id,
                candidate_id=candidate_id,
                source_url=raw_url,
                domain=domain,
                observed=observed,
                catalog_format=CatalogFormat.JSON_API,
                extraction_tier=ExtractionTier.FEED_API,
                has_prices=True,
                has_availability=True,
                capabilities={"identity": True, "catalog": True, "price": True, "availability": True, "order": False},
                confidence=0.7,
                notes="Предположение по адресу: похоже на JSON API каталога (Tier 1), источник не открывался",
            )

        # 2. Telegram Messenger -> Tier 5
        if "t.me/" in u_lower or "telegram.me/" in u_lower:
            return SourceProfile(
                id=profile_id or f"prof_{candidate_id or 'anon'}",
                seller_id=seller_id,
                candidate_id=candidate_id,
                source_url=raw_url,
                domain=domain,
                observed=observed,
                catalog_format=CatalogFormat.TELEGRAM,
                extraction_tier=ExtractionTier.MESSENGER_TEXT,
                has_prices=True,
                has_availability=False,
                capabilities={"identity": False, "catalog": True, "price": True, "availability": False, "order": True},
                confidence=0.75,
                notes="Primary catalog: Telegram Messenger Channel (Tier 5)",
            )

        # 3. Instagram -> Tier 6 (Text) or Tier 7 (Vision)
        if "instagram.com/" in u_lower:
            has_stories = hints.get("has_stories", False)
            tier = ExtractionTier.SOCIAL_VISION if has_stories else ExtractionTier.SOCIAL_TEXT
            fmt = CatalogFormat.INSTAGRAM_VISION if has_stories else CatalogFormat.INSTAGRAM_TEXT
            return SourceProfile(
                id=profile_id or f"prof_{candidate_id or 'anon'}",
                seller_id=seller_id,
                candidate_id=candidate_id,
                source_url=raw_url,
                domain=domain,
                observed=observed,
                catalog_format=fmt,
                extraction_tier=tier,
                has_prices=True,
                has_availability=False,
                capabilities={"identity": False, "catalog": True, "price": True, "availability": False, "order": True},
                confidence=0.70,
                notes="Primary catalog: Social Media (Tier 6/7)",
            )

        # 4. Structured Website (e-commerce platforms / Schema.org) -> Tier 2 or 3
        if hints.get("has_schema_org"):
            return SourceProfile(
                id=profile_id or f"prof_{candidate_id or 'anon'}",
                seller_id=seller_id,
                candidate_id=candidate_id,
                source_url=raw_url,
                domain=domain,
                catalog_format=CatalogFormat.SCHEMA_ORG,
                extraction_tier=ExtractionTier.STRUCTURED_HTML,
                has_prices=True,
                has_availability=True,
                capabilities={"identity": True, "catalog": True, "price": True, "availability": True, "order": True},
                confidence=0.90,
                notes="Primary catalog: Schema.org Structured Website (Tier 3)",
            )

        # 5. Default: SSR HTML Catalog -> Tier 4
        return SourceProfile(
            id=profile_id or f"prof_{candidate_id or 'anon'}",
            seller_id=seller_id,
            candidate_id=candidate_id,
            source_url=raw_url,
            domain=domain,
            observed=observed,
            catalog_format=CatalogFormat.HTML,
            extraction_tier=ExtractionTier.SSR_HTML,
            has_prices=True,
            has_availability=True,
            capabilities={"identity": True, "catalog": True, "price": True, "availability": True, "order": True},
            # Это запасной вывод: адрес не сказал о источнике ничего определённого. Уверенность
            # здесь не может быть выше, чем у распознанных признаков выгрузки или API.
            confidence=0.4,
            notes="Ничего определённого по адресу: предполагаем обычный сайт (Tier 4), источник не открывался",
        )
