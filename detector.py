from typing import Optional, Dict, Any, List
from config import load_settings, ZERO_DROP_RATIO_MIN, ZERO_DROP_RATIO_MAX

PREMIUM_KEYWORDS = [
    "rtx 50", "rtx 40", "rtx 30", "rx 7900", "rx 7800",
    "macbook", "iphone", "galaxy s2", "galaxy z", "ipad pro",
    "core i7", "core i9", "ryzen 7", "ryzen 9", "playstation 5", "xbox series x",
    "oled", "qled"
]

USED_GOODS_KEYWORDS = [
    "уценен", "уценка", "витрин", "б/у", "восстановлен", "refurbished", "б.у"
]

def is_junk_accessory(title: str, custom_keywords: Optional[List[str]] = None) -> bool:
    t = title.lower()
    keywords = custom_keywords if custom_keywords is not None else [
        "чехол", "пленка", "плёнка", "стекло", "кабель", "переходник",
        "ремешок", "держатель", "подставка", "амбушюры", "накладка", "салфетки",
        "зарядное", "зарядка", "блок питания", "адаптер"
    ]
    return any(k.lower() in t for k in keywords if k.strip())

def is_used_goods(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in USED_GOODS_KEYWORDS)

def check_anomaly(product: Dict[str, Any], history_info: Dict[str, Any], custom_settings: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    s = custom_settings if custom_settings is not None else load_settings()
    
    title = product["title"]
    curr_price = int(product["price"])
    old_price_history = int(history_info.get("old_price", curr_price))
    first_price = int(history_info.get("first_seen_price", curr_price))
    old_price_on_site = int(product.get("old_price_on_site", 0))

    # 1. Проверка стоп-слов хлама / аксессуаров
    junk_list = s.get("junk_keywords", [])
    if is_junk_accessory(title, junk_list):
        return None

    # 2. Проверка уценки / б/у
    if s.get("exclude_used_goods", True) and is_used_goods(title):
        return None

    # 3. Проверка ценового диапазона
    min_price_cap = s.get("min_item_price_kzt", 30000)
    max_price_cap = s.get("max_item_price_kzt", 3000000)
    
    # Для детекции пропущенного нуля цена может быть ниже min_price_cap (например, 18 990 ₸ вместо 189 990 ₸)
    # Но если товар дороже max_price_cap, отсекаем
    if curr_price > max_price_cap:
        return None

    detect_zero = s.get("detect_zero_glitch", True)
    detect_discount = s.get("detect_super_discount", True)
    min_drop_pct = s.get("price_glitch_drop_pct", 65)
    min_savings = s.get("min_savings_kzt", 40000)

    # Опорная цена — максимум из истории и зачеркнутой цены на сайте
    reference_price = max(old_price_history, first_price, old_price_on_site)

    # 4. Анализ падения относительно опорной цены
    if reference_price > 0 and curr_price < reference_price:
        savings = reference_price - curr_price
        drop_pct = round((savings / reference_price) * 100, 1)
        ratio = reference_price / curr_price if curr_price > 0 else 0

        # А) Пропущенный ноль (Drop в ~10 раз): 189 990 -> 18 990
        if detect_zero and ZERO_DROP_RATIO_MIN <= ratio <= ZERO_DROP_RATIO_MAX and reference_price >= 80_000:
            return {
                "type": "ZERO_GLITCH",
                "emoji": "🚨 ОШИБКА ЦЕНЫ (ПРОПУЩЕН НОЛЬ!)",
                "old_price": reference_price,
                "new_price": curr_price,
                "drop_pct": drop_pct,
                "savings": savings,
                "reason": f"Цена упала в {round(ratio, 1)} раз (вероятно, пропущен ноль при переоценке!)"
            }

        # Б) Глубокий обвал цены (Супер-скидка)
        if detect_discount and curr_price >= min_price_cap and drop_pct >= min_drop_pct and savings >= min_savings:
            return {
                "type": "SUPER_DISCOUNT",
                "emoji": "💥 СУПЕР-СКИДКА / ЦЕНОВОЙ СБОЙ",
                "old_price": reference_price,
                "new_price": curr_price,
                "drop_pct": drop_pct,
                "savings": savings,
                "reason": f"Обвал цены на {drop_pct}% с экономией {savings:,} ₸".replace(",", " ")
            }

    # 5. Эвристическая проверка для новых товаров без истории
    if detect_zero:
        title_lower = title.lower()
        for kw in PREMIUM_KEYWORDS:
            if kw in title_lower and curr_price < 80_000 and curr_price > 5000:
                estimated_market_price = curr_price * 10
                return {
                    "type": "ZERO_GLITCH",
                    "emoji": "🚨 ПОДОЗРЕНИЕ НА ОШИБОЧНУЮ ЦЕНУ",
                    "old_price": estimated_market_price,
                    "new_price": curr_price,
                    "drop_pct": 90.0,
                    "savings": estimated_market_price - curr_price,
                    "reason": f"Флагманский товар ({kw.upper()}) выставлен всего за {curr_price:,} ₸!".replace(",", " ")
                }

    return None

def check_market_arbitrage(
    product: Dict[str, Any],
    other_stores_avg: Optional[int] = None,
    custom_settings: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Детекция аномалии межмагазинного арбитража / глубокой скидки по сравнению с другими магазинами."""
    s = custom_settings if custom_settings is not None else load_settings()
    if not s.get("detect_market_arbitrage", True):
        return None

    curr_price = int(product.get("price", 0) or product.get("current_price", 0))
    if curr_price <= 0:
        return None

    min_pct = float(s.get("arbitrage_min_drop_pct", 25.0))
    min_diff = int(s.get("arbitrage_min_diff_kzt", 25000))

    if other_stores_avg is not None and other_stores_avg > 0:
        benchmark_price = other_stores_avg
        other_shop_name = "других магазинов"
    else:
        # Автоматический поиск аналогов в других сетях через FTS5
        from database import find_market_comparisons
        market = find_market_comparisons(
            title=product.get("title", ""),
            current_shop=product.get("shop", ""),
            current_price=curr_price,
            city=product.get("city")
        )
        if not market or market.get("competitor_count", 0) == 0:
            return None
        benchmark_price = market["min_price"]
        other_shop_name = market.get("cheapest_shop", "других сетей")

    if benchmark_price <= curr_price:
        return None

    diff = benchmark_price - curr_price
    diff_pct = round((diff / benchmark_price) * 100, 1)

    if diff_pct >= min_pct and diff >= min_diff:
        shop = product.get("shop", "магазине")
        return {
            "type": "MARKET_ARBITRAGE",
            "emoji": "🎯 МЕЖМАГАЗИННЫЙ АРБИТРАЖ (СУПЕР-ЦЕНА)",
            "old_price": benchmark_price,
            "new_price": curr_price,
            "drop_pct": diff_pct,
            "savings": diff,
            "reason": f"В {shop} на {diff_pct}% дешевле, чем в {other_shop_name} ({benchmark_price:,} ₸)!".replace(",", " ")
        }

    return None

