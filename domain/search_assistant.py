"""
AI Search Assistant over Search Core 2.0 (S2-E22, KZ Price Hunter 2.0).

Превращает естественный запрос пользователя:
- «ноутбук для бухгалтера до 400 тысяч»
- «где дешевле купить iPhone 16 Pro, если мне нужна рассрочка?»
- «какая пицца 40 см сейчас со скидкой в Алматы?»

в структурированный план поиска (`SearchAssistantPlan`), выполняет его по реальной
базе `SearchCore` и строит проверяемый ответ (`SearchAssistantResponse`) только по
реально найденным предложениям в БД — без выдумывания товаров, цен или условий.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from domain.query_parser import ParsedQuery, QueryParser
from domain.search_core import AggregatedOffer, ProductSearchResult, SearchCore, SearchOutcome

# ---------------------------------------------------------------------------
# Use-case and conversational filler stripping
# ---------------------------------------------------------------------------
USE_CASE_PATTERNS = [
    (re.compile(r"\bдля\s+бухгалтер(?:а|ии|ов|ской\s+работы)\b", re.IGNORECASE), "office_accounting"),
    (re.compile(r"\bдля\s+(?:офиса|работы|учебы|учёбы|студента|школьника)\b", re.IGNORECASE), "office_study"),
    (re.compile(r"\bдля\s+(?:игр|гейминга|киберспорта)\b", re.IGNORECASE), "gaming"),
    (re.compile(r"\bдля\s+(?:дизайна|монтажа|видеомонтажа|графики|фотошопа)\b", re.IGNORECASE), "creator"),
    (re.compile(r"\bдля\s+(?:дома|семьи|подарка|ребенка|ребёнка|родителей)\b", re.IGNORECASE), "home_family"),
]

INSTALLMENT_RE = re.compile(
    r"\b(?:в\s+рассрочк[уеи]|рассрочк[ауеи]|кредит|kaspi\s*red|каспи\s*ред|0\s*[-–]\s*0\s*[-–]\s*(?:12|24))\b",
    re.IGNORECASE,
)

DISCOUNT_RE = re.compile(
    r"\b(?:со\s+скидк(?:ой|ами)|скидк[аиу]|по\s+акции|акци[яию]|распродаж[аеу]|выгодно)\b",
    re.IGNORECASE,
)

CHEAPEST_RE = re.compile(
    r"\b(?:где\s+дешевле|самый\s+деш[её]вый|дешевле\s+всего|минимальная\s+цена|подешевле)\b",
    re.IGNORECASE,
)

CONVERSATIONAL_NOISE_RE = re.compile(
    r"\b(?:где|какой|какая|какое|какие|купить|взять|найти|подскажи(?:те)?|посоветуй(?:те)?|"
    r"мне|нужен|нужна|нужно|нужны|если|сейчас|выгоднее|лучше|можно|хочу|ищу|пожалуйста)\b",
    re.IGNORECASE,
)

CONDITION_LABELS = {
    "NEW": "новый",
    "USED": "б/у",
    "REFURBISHED": "восстановленный",
    "OPEN_BOX": "уценка",
    "UNKNOWN": "состояние не указано",
}


def _has_confirmed_installment(off: AggregatedOffer) -> bool:
    """Рассрочка — только если она записана в самом предложении (флаг, срок, способ оплаты).
    Тип площадки (Kaspi, маркетплейс) или слово kaspi в адресе условий не доказывают (V05)."""
    return bool(off.installment_available or off.credit_available)


def _condition_label(condition: Optional[str]) -> str:
    return CONDITION_LABELS.get(str(condition or "").upper(), str(condition or "").lower())


@dataclass
class SearchAssistantPlan:
    """Структурированный план выполнения пользовательского запроса."""
    raw_query: str
    search_query: str
    use_case: Optional[str] = None
    city: Optional[str] = None
    condition: Optional[str] = None
    channel_type: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    needs_installment: bool = False
    needs_discount: bool = False
    prefer_cheapest: bool = False
    sort_by: str = "relevance"
    extracted_constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchAssistantResponse:
    """Ответ AI-помощника поверх Search Core 2.0 (строго по данным БД)."""
    query: str
    plan: SearchAssistantPlan
    outcome: str
    explanation: str
    total_products: int = 0
    total_offers: int = 0
    best_overall_offer: Optional[Dict[str, Any]] = None
    best_installment_offer: Optional[Dict[str, Any]] = None
    cross_channel_comparison: Optional[Dict[str, Any]] = None
    items: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["plan"] = self.plan.to_dict()
        return res


class SearchAssistant:
    """
    Помощник выбора поверх Search Core 2.0.
    Сначала разбирает естественный запрос детерминированно, затем выполняет поиск
    в SearchCore и формирует объяснение только по реально найденным предложениям.
    """

    def __init__(self, search_core: SearchCore):
        self.search_core = search_core

    @classmethod
    def build_plan(
        cls,
        query: str,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        channel_type: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> SearchAssistantPlan:
        """Разбирает разговорный запрос в структурированный план поиска."""
        raw = (query or "").strip()
        working = raw
        constraints: List[str] = []

        use_case: Optional[str] = None
        for pattern, uc_code in USE_CASE_PATTERNS:
            m = pattern.search(working)
            if m:
                use_case = uc_code
                constraints.append(f"назначение: {m.group(0).strip()}")
                working = pattern.sub(" ", working)
                break

        needs_installment = bool(INSTALLMENT_RE.search(working))
        if needs_installment:
            constraints.append("рассрочка/кредит")
            working = INSTALLMENT_RE.sub(" ", working)

        needs_discount = bool(DISCOUNT_RE.search(working))
        if needs_discount:
            constraints.append("со скидкой")
            working = DISCOUNT_RE.sub(" ", working)

        prefer_cheapest = bool(CHEAPEST_RE.search(working))
        if prefer_cheapest:
            constraints.append("минимальная цена")
            working = CHEAPEST_RE.sub(" ", working)

        # Strip conversational filler words before passing to QueryParser
        working = CONVERSATIONAL_NOISE_RE.sub(" ", working)
        working = re.sub(r"[?,!;:]+", " ", working)
        working = re.sub(r"\s+", " ", working).strip()

        parsed: ParsedQuery = QueryParser.parse(working or raw)

        eff_city = city or parsed.detected_city
        eff_cond = condition or parsed.condition
        eff_channel = channel_type or parsed.channel_type
        eff_min = min_price if min_price is not None else parsed.min_price
        eff_max = max_price if max_price is not None else parsed.max_price

        if eff_city:
            constraints.append(f"город: {eff_city}")
        if eff_cond:
            constraints.append(f"состояние: {eff_cond}")
        if eff_min is not None:
            constraints.append(f"цена от {int(eff_min):,} ₸".replace(",", " "))
        if eff_max is not None:
            constraints.append(f"бюджет до {int(eff_max):,} ₸".replace(",", " "))

        clean_search = (parsed.clean_query or working or raw).strip()
        # Clean stray prepositions left after stripping
        clean_search = re.sub(r"^(?:в|на|по|за|для|с|со|из|у|от|до)\s+", "", clean_search, flags=re.IGNORECASE)
        clean_search = re.sub(r"\s+(?:в|на|по|за|для|с|со|из|у|от|до)$", "", clean_search, flags=re.IGNORECASE)
        clean_search = re.sub(r"\s+", " ", clean_search).strip()

        sort_by = "price_asc" if (prefer_cheapest or eff_max is not None) else "relevance"

        return SearchAssistantPlan(
            raw_query=raw,
            search_query=clean_search,
            use_case=use_case,
            city=eff_city,
            condition=eff_cond,
            channel_type=eff_channel,
            min_price=eff_min,
            max_price=eff_max,
            needs_installment=needs_installment,
            needs_discount=needs_discount,
            prefer_cheapest=prefer_cheapest,
            sort_by=sort_by,
            extracted_constraints=constraints,
        )

    def ask(
        self,
        query: str,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        channel_type: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        limit: int = 20,
        now: Optional[datetime.datetime] = None,
    ) -> SearchAssistantResponse:
        """
        Выполняет запрос через SearchCore и формирует ответ только по реальным предложениям.
        """
        plan = self.build_plan(
            query=query,
            city=city,
            condition=condition,
            channel_type=channel_type,
            min_price=min_price,
            max_price=max_price,
        )

        # Do not force channel_type=MARKETPLACE during DB lookup when needs_installment=True
        # so we can show BOTH the best installment offer AND any cheaper direct/social alternative!
        search_res = self.search_core.search(
            query=plan.search_query,
            city=plan.city,
            condition=plan.condition,
            channel_type=plan.channel_type,
            min_price=plan.min_price,
            max_price=plan.max_price,
            sort_by=plan.sort_by,
            limit=limit,
            now=now,
        )

        filtered_items: List[ProductSearchResult] = []
        for item in search_res.items:
            if plan.needs_discount:
                discounted_offers = [
                    o for o in item.offers
                    if o.old_price is not None and o.old_price > o.price and o.is_purchasable
                ]
                if not discounted_offers:
                    continue
            filtered_items.append(item)

        # Collect all purchasable offers across matched products
        all_purchasable: List[tuple[Any, AggregatedOffer]] = []
        for item in filtered_items:
            for off in item.offers:
                if off.is_purchasable and off.price > 0:
                    all_purchasable.append((item.product, off))

        best_overall: Optional[Dict[str, Any]] = None
        best_installment: Optional[Dict[str, Any]] = None
        cross_channel: Optional[Dict[str, Any]] = None

        if all_purchasable:
            all_purchasable.sort(key=lambda pair: pair[1].price)
            top_prod, top_off = all_purchasable[0]
            best_overall = {
                "product_id": top_prod.id,
                "product_title": getattr(top_prod, "title", None) or getattr(top_prod, "canonical_title", ""),
                **top_off.to_dict(),
            }

            installment_candidates = [
                (prod, off) for prod, off in all_purchasable if _has_confirmed_installment(off)
            ]
            if installment_candidates:
                installment_candidates.sort(key=lambda pair: pair[1].price)
                inst_prod, inst_off = installment_candidates[0]
                best_installment = {
                    "product_id": inst_prod.id,
                    "product_title": getattr(inst_prod, "title", None) or getattr(inst_prod, "canonical_title", ""),
                    **inst_off.to_dict(),
                }

                # Разница «без рассрочки против рассрочки» — только для того же товара в том же
                # состоянии: б/у против нового — разница состояний, а не способа покупки (V05)
                comparable = [
                    off for prod, off in all_purchasable
                    if prod.id == inst_prod.id
                    and str(off.condition).upper() == str(inst_off.condition).upper()
                    and off.id != inst_off.id
                ]
                if comparable:
                    direct_off = min(comparable, key=lambda o: o.price)
                    if direct_off.price < inst_off.price:
                        diff = round(inst_off.price - direct_off.price, 2)
                        cond_label = _condition_label(inst_off.condition)
                        cross_channel = {
                            "direct_offer": {
                                "product_id": inst_prod.id,
                                "product_title": best_installment["product_title"],
                                **direct_off.to_dict(),
                            },
                            "installment_offer": best_installment,
                            "condition": inst_off.condition,
                            "price_difference": diff,
                            "summary": (
                                f"Тот же товар ({cond_label}): у «{direct_off.seller_name}» "
                                f"({direct_off.channel_name or direct_off.channel_type}) — "
                                f"{int(direct_off.price):,} ₸, с рассрочкой у «{inst_off.seller_name}» "
                                f"({inst_off.channel_name or inst_off.channel_type}) — "
                                f"{int(inst_off.price):,} ₸ (разница {int(diff):,} ₸)"
                            ).replace(",", " "),
                        }

        explanation = self._build_grounded_explanation(
            plan=plan,
            items=filtered_items,
            best_overall=best_overall,
            best_installment=best_installment,
            cross_channel=cross_channel,
        )

        outcome = SearchOutcome.FOUND if filtered_items else SearchOutcome.NOT_FOUND
        total_offers = sum(len(i.offers) for i in filtered_items)

        return SearchAssistantResponse(
            query=query,
            plan=plan,
            outcome=outcome,
            explanation=explanation,
            total_products=len(filtered_items),
            total_offers=total_offers,
            best_overall_offer=best_overall,
            best_installment_offer=best_installment,
            cross_channel_comparison=cross_channel,
            items=[i.to_dict() for i in filtered_items],
            latency_ms=search_res.latency_ms,
        )

    @staticmethod
    def _build_grounded_explanation(
        plan: SearchAssistantPlan,
        items: List[ProductSearchResult],
        best_overall: Optional[Dict[str, Any]],
        best_installment: Optional[Dict[str, Any]],
        cross_channel: Optional[Dict[str, Any]],
    ) -> str:
        """Строит русскоязычное резюме строго по найденным записям БД (без выдумок)."""
        if not items or not best_overall:
            constraints_str = (
                f" (фильтры: {', '.join(plan.extracted_constraints)})"
                if plan.extracted_constraints
                else ""
            )
            return (
                f"По запросу «{plan.search_query}»{constraints_str} "
                "в базе актуальных предложений не найдено."
            )

        parts: List[str] = []
        price_fmt = f"{int(best_overall['price']):,} ₸".replace(",", " ")
        parts.append(
            f"Найдено товаров: {len(items)}. "
            f"Самое выгодное предложение: {best_overall['product_title']} "
            f"({_condition_label(best_overall.get('condition'))}) — "
            f"{price_fmt} у продавца «{best_overall['seller_name']}» ({best_overall['channel_type']})."
        )

        if plan.needs_installment:
            if best_installment:
                inst_fmt = f"{int(best_installment['price']):,} ₸".replace(",", " ")
                months = best_installment.get("installment_months")
                terms = f", рассрочка {months} мес." if months else ""
                parts.append(
                    f"Лучший вариант с рассрочкой или кредитом по данным продавца: "
                    f"{best_installment['product_title']} ({_condition_label(best_installment.get('condition'))}) "
                    f"за {inst_fmt} "
                    f"(«{best_installment['seller_name']}», {best_installment.get('channel_name') or best_installment['channel_type']}{terms})."
                )
            else:
                parts.append(
                    "Условия рассрочки по найденным предложениям неизвестны: продавцы их не указали. "
                    "Уточняйте у продавца."
                )

        if cross_channel and cross_channel.get("summary"):
            parts.append(cross_channel["summary"] + ".")

        return " ".join(parts)
