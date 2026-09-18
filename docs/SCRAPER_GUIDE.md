# 🛠 Руководство по разработке скраперов (Scraper Developer Guide)

Данное руководство предназначено для разработчиков и AI-агентов, подключающих новые магазины к системе **KZ Price Hunter**. Следуйте этим стандартам для безопасного масштабирования каталога источников до 100+ сетей.

---

## 1. Единый контракт скрапера (`Scraper Protocol`)

Каждый скрапер в директории `scrapers/` обязан реализовывать протокол `Scraper` из `scrapers/base.py`:

```python
from typing import Protocol, List, Dict, Any, Optional
from scrapers.base import ScanResult

class Scraper(Protocol):
    SHOP_NAME: str

    def scrape(
        self,
        categories: Optional[List[Dict[str, Any]]] = None,
        max_pages: Optional[int] = None
    ) -> ScanResult:
        """Сбор данных по переданным категориям (синхронный или асинхронный)."""
        ...

    def close(self) -> None:
        """Освобождение сетевых сессий, пулов соединений или процессов браузера."""
        ...
```

### Требования к контракту:
1. **`SHOP_NAME`**: Уникальное строковое имя магазина, совпадающее с ключом в `SHOP_REGISTRY`.
2. **`scrape(...)`**: Возвращает объект `ScanResult(items, pages_scanned, errors, status, duration_sec)`.
3. **`close()`**: Метод гарантированного закрытия ресурсов (`client.close()`, `session.close()`, `browser.close()`). Метод обязан быть безопасным при повторном вызове (`idempotent`).

---

## 2. Структура возвращаемых данных (`Offer Snapshot`)

Каждый товар в `items` должен быть словарем и **обязательно** проходить валидацию через вспомогательную функцию `validate_product_item(item)`:

```python
from scrapers.base import validate_product_item

raw_item = {
    "id": str(item_id),              # Уникальный строковый ID товара внутри магазина
    "shop": self.SHOP_NAME,          # Имя магазина
    "title": title.strip(),          # Полное наименование товара
    "price": int(current_price),     # Текущая цена продажи (целое положительное число > 0)
    "old_price_on_site": old_price,  # Старая/зачеркнутая цена сайта (int или None)
    "url": product_url,              # Прямая ссылка на страницу товара (начинается с http/https)
    "image_url": image_url or "",    # Ссылка на изображение товара
    "category": category_name,       # Категория товара
    "city": city_name or "Астана",   # Город (по умолчанию "Астана")
    "description": description or "" # Описание характеристик (опционально)
}

# Проверка корректности:
if validate_product_item(raw_item):
    items.append(raw_item)
else:
    # Запись предупреждения о невалидной структуре
    logger.warning(f"[{self.SHOP_NAME}] Отклонен невалидный товар: {raw_item}")
```

### Правила валидации:
- `price` обязан быть строго больше `0`. Товар с нулевой или отрицательной ценой **не сохраняется** в базу.
- `title` не должен быть пустым.
- `url` обязан быть валидным веб-адресом (`http://` или `https://`).
- Рассрочка (installment, Kaspi Red/Рассрочка) не должна подменяться вместо полной цены товара.

---

## 3. Выбор типа транспорта и источника

Классифицируйте источник по шкале надежности и нагрузки:

| Тип | Механизм | Примеры в проекте | Рекомендации |
|---|---|---|---|
| **A (Feed)** | Официальный YML/XML/CSV экспорт магазина | `ShopKzScraper` (shop.kz YML) | Наивысший приоритет. Минимальная нагрузка на сервер магазина, максимальная стабильность. |
| **B (Schema)** | Микроразметка Schema.org / JSON-LD | `AntsScraper`, `ItmagScraper` | Наследуйтесь от `SchemaListingScraper`. Чтение структурированных `itemprop`. |
| **C (JSON API)**| Внутренний JSON API сайта или мобильного приложения | `KaspiScraper`, `MechtaScraper`, `ForteMarketScraper` | Высокая скорость. Эмулируйте стандартные заголовки браузера (`User-Agent`, `Referer`). |
| **D (HTML)** | Парсинг страниц каталога через BeautifulSoup / lxml | `SulpakScraper`, `AlserScraper`, `FlipScraper` | Наследуйтесь от `PagedScraper`. Изучайте стабильные селекторы или `data-*` атрибуты. |
| **E (Browser)** | Headless-браузер (Playwright Chromium) | `DNSScraper` | Крайняя мера (только для сайтов с тяжелым JS-рендерингом и динамической защитой). Обязательно используйте флаги безопасности контейнера (`--no-sandbox`, `--disable-dev-shm-usage`, `--disable-gpu`) и гарантированное закрытие в блоке `finally: browser.close()`. |

---

## 4. Правила вежливого сбора данных (Polite Crawling)

1. **Таймауты**:
   - Максимальный таймаут на один HTTP-запрос — не более 15–30 секунд (до 90 секунд для больших YML-фидов).
2. **Паузы (Rate Limiting & Jitter)**:
   - Пауза между страницами: от 0.3 до 1.0 секунды с добавлением случайного джиттера (`random.uniform(0.1, 0.4)`).
3. **Обработка ошибок**:
   - При получении HTTP 429 (Too Many Requests) учитывайте заголовок `Retry-After`.
   - При фатальных ошибках (403 Forbidden, блокировка) завершайте парсинг со статусом `ScanResult.status = "blocked"` или `"failed"`, не входя в бесконечный цикл повторов.
4. **Ограничение глубины**:
   - Всегда соблюдайте параметр `max_pages` (по умолчанию не более 30–50 страниц на категорию за волну).

---

## 5. Пошаговый чек-лист добавления нового магазина

1. **Создайте модуль скрапера**: `scrapers/<shop_code>.py`.
2. **Реализуйте класс**: унаследуйте от `PagedScraper`, `SchemaListingScraper` или напишите класс, реализующий `Scraper Protocol`.
3. **Зарегистрируйте в `scrapers/__init__.py`**:
   - Импортируйте класс скрапера.
   - Добавьте в словарь `SHOP_REGISTRY`: `"<shop_code>": MyNewScraper`.
4. **Настройте категории в `config.py`**:
   - Добавьте список категорий магазина в `CATEGORIES` или специальный маппинг мастер-категорий.
5. **Добавьте документацию в `SOURCES.md`**:
   - Опишите тип транспорта, эндпоинты, таймауты и формат отдачи данных.
6. **Напишите модульный тест**:
   - Создайте тест с мок-ответами в `tests/` или добавьте в `test_scraper_reliability.py`.
   - Запустите проверку контракта: `python3 -m unittest test_scraper_contract.py`.
