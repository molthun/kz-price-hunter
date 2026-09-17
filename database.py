import re
import json
import sqlite3
import hashlib
import secrets
import datetime
from typing import Optional, Dict, Any, List
from config import DB_PATH, get_scan_interval_seconds, merge_user_settings

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_db():
    """Создает таблицы, FTS5 индекс и выполняет миграции для поддержки поля shop и city."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                shop TEXT DEFAULT 'DNS Казахстан',
                title TEXT NOT NULL,
                category TEXT,
                url TEXT NOT NULL,
                image_url TEXT,
                current_price INTEGER NOT NULL,
                first_seen_price INTEGER NOT NULL,
                min_price INTEGER NOT NULL,
                max_price INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Миграция: если поле shop отсутствует в старой БД, добавляем его
        try:
            cursor.execute("ALTER TABLE products ADD COLUMN shop TEXT DEFAULT 'DNS Казахстан'")
        except sqlite3.OperationalError:
            pass  # Колонка уже существует

        try:
            cursor.execute("ALTER TABLE products ADD COLUMN city TEXT DEFAULT 'Астана'")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop TEXT DEFAULT 'DNS Казахстан',
                city TEXT DEFAULT 'Астана',
                product_id TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                old_price INTEGER,
                new_price INTEGER NOT NULL,
                discount_pct REAL,
                savings_kzt INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        """)

        try:
            cursor.execute("ALTER TABLE alerts ADD COLUMN shop TEXT DEFAULT 'DNS Казахстан'")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE alerts ADD COLUMN city TEXT DEFAULT 'Астана'")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE alerts ADD COLUMN competitor_shop TEXT")
        except sqlite3.OperationalError:
            pass

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_id ON products(id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_city ON products(city)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_shop_city_price ON products(shop, city, current_price)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_product ON alerts(product_id)")

        # Пользователи (вход через Telegram) и их личные настройки
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                photo_url TEXT,
                settings TEXT NOT NULL DEFAULT '{}',
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Сессии: в базе хранится только SHA-256 токена из cookie
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")

        # Состояние сканирования по каждому магазину: свежесть считается отдельно,
        # поэтому прерванный цикл (например, из-за перезапуска контейнера) догоняется по отставшим магазинам
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shop_scans (
                shop_key TEXT PRIMARY KEY,
                last_attempt_at TIMESTAMP,
                last_success_at TIMESTAMP,
                last_items INTEGER DEFAULT 0,
                last_duration_sec REAL DEFAULT 0,
                last_error TEXT
            )
        """)

        # Автоматическая миграция: исправление ссылок на картинки Белого Ветра
        try:
            cursor.execute("UPDATE products SET image_url = REPLACE(image_url, 'https://shop.kz//static.shop.kz', 'https://static.shop.kz') WHERE image_url LIKE 'https://shop.kz//static.shop.kz%'")
        except Exception:
            pass

        # Полнотекстовый индекс FTS5 для мгновенного поиска по миллионам товаров
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
                    id UNINDEXED,
                    title,
                    shop,
                    city,
                    category,
                    content='products',
                    content_rowid='rowid'
                )
            """)

            # Триггеры авто-синхронизации products -> products_fts
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
                    INSERT INTO products_fts(rowid, id, title, shop, city, category)
                    VALUES (new.rowid, new.id, new.title, new.shop, new.city, new.category);
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
                    INSERT INTO products_fts(products_fts, rowid, id, title, shop, city, category)
                    VALUES('delete', old.rowid, old.id, old.title, old.shop, old.city, old.category);
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
                    INSERT INTO products_fts(products_fts, rowid, id, title, shop, city, category)
                    VALUES('delete', old.rowid, old.id, old.title, old.shop, old.city, old.category);
                    INSERT INTO products_fts(rowid, id, title, shop, city, category)
                    VALUES (new.rowid, new.id, new.title, new.shop, new.city, new.category);
                END;
            """)

            # Построение FTS5 индекса из таблицы products
            cursor.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")
        except sqlite3.OperationalError as e:
            print(f"[DB] Предупреждение инициализации FTS5: {e}")

        conn.commit()

def save_or_update_product(p: Dict[str, Any]) -> Dict[str, Any]:
    pid = str(p["id"])
    shop = p.get("shop", "DNS Казахстан")
    city = p.get("city", "Астана")
    title = p["title"]
    category = p.get("category", "")
    url = p["url"]
    image_url = p.get("image_url", "")
    if image_url and "shop.kz//static.shop.kz" in image_url:
        image_url = image_url.replace("https://shop.kz//static.shop.kz", "https://static.shop.kz").replace("shop.kz//static.shop.kz", "static.shop.kz")
    current_price = int(p["price"])

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_price, first_seen_price, min_price, max_price FROM products WHERE id = ?", (pid,))
        existing = cursor.fetchone()

        if existing is None:
            cursor.execute("""
                INSERT INTO products (id, shop, city, title, category, url, image_url, current_price, first_seen_price, min_price, max_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pid, shop, city, title, category, url, image_url, current_price, current_price, current_price, current_price, now, now))
            conn.commit()
            return {
                "is_new": True,
                "old_price": current_price,
                "first_seen_price": current_price,
                "current_price": current_price,
                "price_changed": False
            }
        else:
            old_price = existing["current_price"]
            first_seen_price = existing["first_seen_price"]
            min_price = min(existing["min_price"], current_price)
            max_price = max(existing["max_price"], current_price)
            price_changed = (old_price != current_price)

            cursor.execute("""
                UPDATE products
                SET shop = ?, city = ?, title = ?, category = ?, url = ?, image_url = ?,
                    current_price = ?, min_price = ?, max_price = ?, updated_at = ?
                WHERE id = ?
            """, (shop, city, title, category, url, image_url, current_price, min_price, max_price, now, pid))
            conn.commit()

            return {
                "is_new": False,
                "old_price": old_price,
                "first_seen_price": first_seen_price,
                "current_price": current_price,
                "price_changed": price_changed
            }

def get_price_history_batch(product_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Возвращает сохраненную историю цен для набора товаров (до пакетной перезаписи)."""
    ids = [str(pid) for pid in product_ids]
    history: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return history

    with get_connection() as conn:
        cursor = conn.cursor()
        # Порциями, чтобы не упереться в лимит параметров SQLite
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"SELECT id, current_price, first_seen_price FROM products WHERE id IN ({placeholders})",
                chunk
            )
            for row in cursor.fetchall():
                history[row["id"]] = {
                    "old_price": row["current_price"],
                    "first_seen_price": row["first_seen_price"]
                }
    return history

def save_or_update_products_batch(products: List[Dict[str, Any]]) -> int:
    """Массовая вставка/обновление товаров в единой транзакции (для YML выгрузок и больших каталогов)."""
    if not products:
        return 0

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    updated_count = 0

    with get_connection() as conn:
        cursor = conn.cursor()
        for p in products:
            pid = str(p["id"])
            shop = p.get("shop", "Белый Ветер")
            city = p.get("city", "Астана")
            title = p["title"]
            category = p.get("category", "")
            url = p["url"]
            image_url = p.get("image_url", "")
            if image_url and "shop.kz//static.shop.kz" in image_url:
                image_url = image_url.replace("https://shop.kz//static.shop.kz", "https://static.shop.kz").replace("shop.kz//static.shop.kz", "static.shop.kz")
            current_price = int(p["price"])

            cursor.execute("SELECT current_price, min_price, max_price FROM products WHERE id = ?", (pid,))
            existing = cursor.fetchone()

            if existing is None:
                cursor.execute("""
                    INSERT INTO products (id, shop, city, title, category, url, image_url, current_price, first_seen_price, min_price, max_price, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (pid, shop, city, title, category, url, image_url, current_price, current_price, current_price, current_price, now, now))
            else:
                min_price = min(existing["min_price"], current_price)
                max_price = max(existing["max_price"], current_price)
                cursor.execute("""
                    UPDATE products
                    SET shop = ?, city = ?, title = ?, category = ?, url = ?, image_url = ?,
                        current_price = ?, min_price = ?, max_price = ?, updated_at = ?
                    WHERE id = ?
                """, (shop, city, title, category, url, image_url, current_price, min_price, max_price, now, pid))
            updated_count += 1
        conn.commit()

    return updated_count

def was_alert_sent_recently(product_id: str, new_price: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM alerts
            WHERE product_id = ? AND new_price = ?
            LIMIT 1
        """, (str(product_id), new_price))
        return cursor.fetchone() is not None

def record_alert(product_id: str, alert_type: str, old_price: int, new_price: int, discount_pct: float, savings_kzt: int, shop: str = "DNS Казахстан", city: str = "Астана", competitor_shop: Optional[str] = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO alerts (shop, city, product_id, alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (shop, city, str(product_id), alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop))
        conn.commit()

def get_db_freshness(threshold_seconds: int = 10800) -> Dict[str, Any]:
    """Определяет свежесть базы данных на основе времени последнего обновления товаров.
    По умолчанию порог устаревания: 3 часа (10800 секунд).
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(updated_at) FROM products")
        row = cursor.fetchone()
        latest_str = row[0] if row else None

        cursor.execute("SELECT COUNT(*) FROM products")
        total_count = cursor.fetchone()[0]

    if not latest_str or total_count == 0:
        return {
            "latest_update": None,
            "age_seconds": None,
            "is_stale": True,
            "threshold_seconds": threshold_seconds,
            "total_products": total_count
        }

    try:
        if "T" in latest_str:
            latest_dt = datetime.datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
        else:
            latest_dt = datetime.datetime.strptime(latest_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)

        now = datetime.datetime.now(datetime.timezone.utc)
        age_seconds = max(0, int((now - latest_dt).total_seconds()))
    except Exception:
        age_seconds = 999999

    return {
        "latest_update": latest_str,
        "age_seconds": age_seconds,
        "is_stale": age_seconds >= threshold_seconds,
        "threshold_seconds": threshold_seconds,
        "total_products": total_count
    }

# Сколько последних кандидатов просматривается при фильтрации ленты по личным порогам
ALERTS_SCAN_WINDOW = 3000

def _alert_type_clause(alert_type: Optional[str]):
    if not alert_type:
        return "", []
    at = alert_type.lower()
    if at in ("anomaly", "anomalies", "glitch", "zero_glitch"):
        return " AND a.alert_type = 'ZERO_GLITCH'", []
    if at in ("discount", "discounts"):
        return " AND a.alert_type IN ('SUPER_DISCOUNT', 'MARKET_ARBITRAGE')", []
    if at in ("super", "super_discount"):
        return " AND a.alert_type = 'SUPER_DISCOUNT'", []
    if at in ("arbitrage", "market_arbitrage"):
        return " AND a.alert_type = 'MARKET_ARBITRAGE'", []
    return " AND a.alert_type = ?", [alert_type]

def _fetch_filtered_alerts(user_settings: Dict[str, Any], city: Optional[str] = None, alert_type: Optional[str] = None, limit: Optional[int] = None) -> list:
    from detector import alert_matches_user

    query = """
        SELECT a.*, p.title, p.url, p.image_url, p.category
        FROM alerts a
        LEFT JOIN products p ON a.product_id = p.id
        WHERE 1=1
    """
    params: List[Any] = []
    if city and city != "Все":
        query += " AND (a.city = ? OR a.city IS NULL)"
        params.append(city)
    clause, clause_params = _alert_type_clause(alert_type)
    query += clause
    params.extend(clause_params)
    query += " ORDER BY a.id DESC LIMIT ?"
    params.append(ALERTS_SCAN_WINDOW)

    result = []
    with get_connection() as conn:
        for row in conn.execute(query, params):
            item = dict(row)
            if alert_matches_user(item, user_settings):
                result.append(item)
                if limit is not None and len(result) >= limit:
                    break
    return result

def get_stats(user_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    settings = user_settings if user_settings is not None else merge_user_settings({})
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products")
        total_products = cursor.fetchone()[0]

        cursor.execute("SELECT shop, COUNT(*) as count FROM products GROUP BY shop")
        shops_stats = {row["shop"]: row["count"] for row in cursor.fetchall()}

    # Счетчики аномалий и скидок — по личным порогам пользователя (у гостей — по умолчанию)
    visible = _fetch_filtered_alerts(settings)
    total_anomalies = sum(1 for a in visible if a["alert_type"] == "ZERO_GLITCH")
    total_discounts = len(visible) - total_anomalies

    return {
        "total_products": total_products,
        "total_alerts": len(visible),
        "total_anomalies": total_anomalies,
        "total_discounts": total_discounts,
        "shops": shops_stats,
        "db_freshness": get_db_freshness(threshold_seconds=get_scan_interval_seconds())
    }

def get_alerts(limit: int = 150, city: Optional[str] = None, alert_type: Optional[str] = None, user_settings: Optional[Dict[str, Any]] = None) -> list:
    settings = user_settings if user_settings is not None else merge_user_settings({})
    return _fetch_filtered_alerts(settings, city=city, alert_type=alert_type, limit=limit)

def get_products_list(shop: Optional[str] = None, city: Optional[str] = None, search: Optional[str] = None, limit: int = 50, offset: int = 0) -> list:
    query = "SELECT * FROM products WHERE 1=1"
    params = []
    if shop and shop != "Все":
        query += " AND shop = ?"
        params.append(shop)
    if city and city != "Все":
        query += " AND (city = ? OR city IS NULL)"
        params.append(city)
    if search:
        query += " AND title LIKE ?"
        params.append(f"%{search}%")
    query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def get_products_count(shop: Optional[str] = None, city: Optional[str] = None, search: Optional[str] = None) -> int:
    query = "SELECT COUNT(*) FROM products WHERE 1=1"
    params = []
    if shop and shop != "Все":
        query += " AND shop = ?"
        params.append(shop)
    if city and city != "Все":
        query += " AND (city = ? OR city IS NULL)"
        params.append(city)
    if search:
        query += " AND title LIKE ?"
        params.append(f"%{search}%")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()[0]

def find_market_comparisons(
    title: str,
    current_shop: str,
    current_price: int,
    city: Optional[str] = None,
    limit: int = 15
) -> Optional[Dict[str, Any]]:
    """Ищет аналогичные товары в других магазинах через FTS5.
    Рассчитывает статистику цен конкурентов: минимальная цена, средняя цена,
    разница и процент экономии относительно рынка.
    """
    if current_price <= 0 or not title:
        return None

    # Извлекаем очищенные поисковые токены модели
    cleaned = re.sub(r"[^\w\s]", " ", title)
    cleaned = re.sub(r"(\d+)([a-zA-Zа-яА-Я]+)", r"\1 \2", cleaned)
    cleaned = re.sub(r"([a-zA-Zа-яА-Я]+)(\d+)", r"\1 \2", cleaned)
    stop_words = {
        "смартфон", "ноутбук", "процессор", "видеокарта", "телевизор", "монитор",
        "пылесос", "планшет", "наушники", "часы", "стайлер", "выпрямитель",
        "приставка", "консоль", "купить", "для", "чехол", "стекло", "пленка", "блок", "питания",
        "черный", "белый", "серый", "black", "white", "silver", "gold", "blue", "green", "red",
        "oem", "box", "nano", "esim", "sim", "игровая", "смарт", "cpu", "am4", "am5", "lga1700",
        "led", "oled", "uhd", "smart", "wifi", "lte", "gadzhety", "offers", "offer",
        "gb", "гб", "tb", "тб", "mb", "мб", "hz", "гц"
    }
    words = cleaned.split()
    tokens = []
    for w in words:
        wl = w.lower()
        if wl in stop_words:
            continue
        if len(wl) >= 2 and not (wl.isdigit() and len(wl) == 1):
            tokens.append(wl)

    if not tokens:
        return None

    # Приоритет токенам с цифрами/моделями
    tokens.sort(key=lambda x: (not (any(c.isdigit() for c in x) and any(c.isalpha() for c in x)), not any(c.isdigit() for c in x)))
    selected_tokens = tokens[:3]
    fts_query = " AND ".join(selected_tokens)

    competitors = []
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            sql = """
                SELECT shop, title, current_price, url, city
                FROM products
                WHERE shop != ? AND current_price > 0 AND rowid IN (
                    SELECT rowid FROM products_fts WHERE products_fts MATCH ?
                )
                ORDER BY current_price ASC
                LIMIT ?
            """
            cursor.execute(sql, (current_shop, fts_query, limit))
            rows = cursor.fetchall()
            for r in rows:
                competitors.append(dict(r))
        except Exception:
            return None

    if not competitors:
        return None

    # Фильтрация нерелевантного хлама, несопоставимых по цене товаров и проверка схожести моделей
    import difflib
    from detector import is_junk_accessory

    valid_competitors = []
    for c in competitors:
        c_price = c.get("current_price", 0)
        # Отсекаем нереалистичные скачки цен: конкурент не может стоить в 2.5 раза дороже (другой класс устройства)
        if c_price > current_price * 2.5 or c_price < current_price * 0.35:
            continue
        if is_junk_accessory(c["title"]):
            continue
        # Проверяем схожесть названий моделей
        sim = difflib.SequenceMatcher(None, title.lower(), c["title"].lower()).ratio()
        if sim >= 0.38:
            valid_competitors.append(c)

    if not valid_competitors:
        return None

    prices = [c["current_price"] for c in valid_competitors]
    min_comp_price = min(prices)
    avg_comp_price = int(sum(prices) / len(prices))
    cheapest_comp = min(valid_competitors, key=lambda x: x["current_price"])

    return {
        "competitor_count": len(valid_competitors),
        "min_price": min_comp_price,
        "avg_price": avg_comp_price,
        "cheapest_shop": cheapest_comp["shop"],
        "cheapest_title": cheapest_comp["title"],
        "competitors": valid_competitors
    }



# ===== Пользователи и сессии =====

SESSION_TTL_DAYS = 30

def _user_row_to_dict(row) -> Dict[str, Any]:
    user = dict(row)
    try:
        raw = json.loads(user.get("settings") or "{}")
    except (TypeError, ValueError):
        raw = {}
    user["settings"] = merge_user_settings(raw)
    user["is_blocked"] = bool(user["is_blocked"])
    return user

def upsert_telegram_user(tg: Dict[str, Any], initial_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Создает пользователя при первом входе или обновляет его профиль Telegram."""
    uid = int(tg["id"])
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone()
        if exists:
            conn.execute("""
                UPDATE users SET username = ?, first_name = ?, last_name = ?, photo_url = ?, last_login_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (tg.get("username"), tg.get("first_name"), tg.get("last_name"), tg.get("photo_url"), uid))
        else:
            conn.execute("""
                INSERT INTO users (id, username, first_name, last_name, photo_url, settings)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (uid, tg.get("username"), tg.get("first_name"), tg.get("last_name"), tg.get("photo_url"),
                  json.dumps(initial_settings or {}, ensure_ascii=False)))
        conn.commit()
    return get_user(uid)

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    return _user_row_to_dict(row) if row else None

def save_user_settings(user_id: int, clean_settings: Dict[str, Any]) -> Dict[str, Any]:
    user = get_user(user_id)
    merged = merge_user_settings({**user["settings"], **clean_settings})
    with get_connection() as conn:
        conn.execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(merged, ensure_ascii=False), int(user_id)))
        conn.commit()
    return merged

def list_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY last_login_at DESC").fetchall()
    return [_user_row_to_dict(r) for r in rows]

def set_user_blocked(user_id: int, blocked: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_blocked = ? WHERE id = ?", (1 if blocked else 0, int(user_id)))
        if blocked:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))
        conn.commit()

def get_notification_recipients() -> List[Dict[str, Any]]:
    """Незаблокированные пользователи с включенными Telegram-уведомлениями."""
    return [u for u in list_users() if not u["is_blocked"] and u["settings"].get("telegram_notify_enabled")]

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=SESSION_TTL_DAYS)
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.datetime.now(datetime.timezone.utc).isoformat(),))
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (_hash_token(token), int(user_id), expires.isoformat()))
        conn.commit()
    return token

def get_session_user(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        row = conn.execute("""
            SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_blocked = 0
        """, (_hash_token(token), now)).fetchone()
    return _user_row_to_dict(row) if row else None

def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()


# ===== Состояние сканирования по магазинам =====

def record_shop_scan_start(shop_key: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO shop_scans (shop_key, last_attempt_at) VALUES (?, ?)
            ON CONFLICT(shop_key) DO UPDATE SET last_attempt_at = excluded.last_attempt_at
        """, (shop_key, now))
        conn.commit()

def record_shop_scan_result(shop_key: str, items: int, duration_sec: float, error: Optional[str] = None) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection() as conn:
        if error:
            conn.execute("""
                INSERT INTO shop_scans (shop_key, last_attempt_at, last_items, last_duration_sec, last_error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(shop_key) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at, last_items = excluded.last_items,
                    last_duration_sec = excluded.last_duration_sec, last_error = excluded.last_error
            """, (shop_key, now, items, duration_sec, error[:500]))
        else:
            conn.execute("""
                INSERT INTO shop_scans (shop_key, last_attempt_at, last_success_at, last_items, last_duration_sec, last_error)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(shop_key) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at, last_success_at = excluded.last_success_at,
                    last_items = excluded.last_items, last_duration_sec = excluded.last_duration_sec, last_error = NULL
            """, (shop_key, now, now, items, duration_sec))
        conn.commit()

def get_shop_scans() -> Dict[str, Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM shop_scans").fetchall()
    return {row["shop_key"]: dict(row) for row in rows}

def _age_seconds(timestamp: Optional[str]) -> Optional[int]:
    if not timestamp:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return max(0, int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()))
    except Exception:
        return None

def get_stale_shops(shop_keys: List[str], max_age_seconds: int) -> List[str]:
    """Магазины, которые пора обойти: сначала те, что дольше всех не обновлялись."""
    scans = get_shop_scans()
    stale = []
    for key in shop_keys:
        age = _age_seconds((scans.get(key) or {}).get("last_success_at"))
        if age is None or age >= max_age_seconds:
            stale.append((key, age if age is not None else 10**9))
    return [key for key, _ in sorted(stale, key=lambda x: -x[1])]

def get_shops_scan_report(shop_keys: List[str]) -> List[Dict[str, Any]]:
    """Сводка по магазинам для админ-панели."""
    scans = get_shop_scans()
    report = []
    for key in shop_keys:
        row = scans.get(key) or {}
        report.append({
            "shop_key": key,
            "last_success_at": row.get("last_success_at"),
            "age_seconds": _age_seconds(row.get("last_success_at")),
            "last_items": row.get("last_items") or 0,
            "last_duration_sec": round(row.get("last_duration_sec") or 0, 1),
            "last_error": row.get("last_error"),
        })
    return report
