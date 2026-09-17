import re
import sqlite3
import datetime
from typing import Optional, Dict, Any, List
from config import DB_PATH, get_scan_interval_seconds

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_db():
    """Создает таблицы, FTS5 индекс и выполняет миграции для поддержки поля shop и city."""
    # Если запущен в Docker с примонтированным томом, а том содержит старую/пустую базу
    try:
        from config import BASE_DIR, DATA_DIR, DB_PATH
        seed_db = BASE_DIR / "prices.db"
        if DATA_DIR != BASE_DIR and seed_db.exists() and seed_db.resolve() != DB_PATH.resolve():
            cur_count = 0
            if DB_PATH.exists():
                try:
                    c = sqlite3.connect(DB_PATH)
                    cur_count = c.cursor().execute("SELECT COUNT(*) FROM products").fetchone()[0]
                    c.close()
                except Exception:
                    cur_count = 0

            # Если на проде старая база (например, 2 000 товаров), а в образе полная (20k+)
            if cur_count < 5000:
                sc = sqlite3.connect(seed_db)
                seed_count = sc.cursor().execute("SELECT COUNT(*) FROM products").fetchone()[0]
                sc.close()
                if seed_count > cur_count:
                    print(f"[Database] 📦 Авто-синхронизация: копирование полной базы ({seed_count} товаров) в постоянный том {DATA_DIR}...")
                    import shutil
                    shutil.copy2(seed_db, DB_PATH)
                    print(f"[Database] ✅ База данных тома успешно обновлена до {seed_count} товаров!")
    except Exception as e:
        print(f"[Database] Ошибка проверки seed-базы: {e}")

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

def get_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products")
        total_products = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alerts")
        total_alerts = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alerts WHERE alert_type = 'ZERO_GLITCH'")
        total_anomalies = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alerts WHERE alert_type IN ('SUPER_DISCOUNT', 'MARKET_ARBITRAGE')")
        total_discounts = cursor.fetchone()[0]

        cursor.execute("SELECT shop, COUNT(*) as count FROM products GROUP BY shop")
        shops_stats = {row["shop"]: row["count"] for row in cursor.fetchall()}

        return {
            "total_products": total_products,
            "total_alerts": total_alerts,
            "total_anomalies": total_anomalies,
            "total_discounts": total_discounts,
            "shops": shops_stats,
            "db_freshness": get_db_freshness(threshold_seconds=get_scan_interval_seconds())
        }

def get_alerts(limit: int = 150, city: Optional[str] = None, alert_type: Optional[str] = None) -> list:
    query = """
        SELECT a.*, p.title, p.url, p.image_url, p.category, a.shop
        FROM alerts a
        LEFT JOIN products p ON a.product_id = p.id
        WHERE 1=1
    """
    params = []
    if city and city != "Все":
        query += " AND (a.city = ? OR a.city IS NULL)"
        params.append(city)

    if alert_type:
        at = alert_type.lower()
        if at in ("anomaly", "anomalies", "glitch", "zero_glitch"):
            query += " AND a.alert_type = 'ZERO_GLITCH'"
        elif at in ("discount", "discounts"):
            query += " AND a.alert_type IN ('SUPER_DISCOUNT', 'MARKET_ARBITRAGE')"
        elif at in ("super", "super_discount"):
            query += " AND a.alert_type = 'SUPER_DISCOUNT'"
        elif at in ("arbitrage", "market_arbitrage"):
            query += " AND a.alert_type = 'MARKET_ARBITRAGE'"
        else:
            query += " AND a.alert_type = ?"
            params.append(alert_type)

    query += " ORDER BY a.id DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

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

