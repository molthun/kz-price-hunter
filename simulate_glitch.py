"""
Скрипт для демонстрации: симулирует ситуацию пользователя,
когда дорогой товар внезапно стал стоить в 10 раз дешевле из-за пропущенного нуля!
"""
import sys
from database import init_db, save_or_update_product, record_alert
from detector import check_anomaly
from notifier import send_alert

def main():
    init_db()

    # Шаг 1: Товар существовал по обычной цене 189 990 ₸
    normal_product = {
        "id": "demo-tv-189990",
        "title": '55" (139 см) Телевизор LED Samsung Crystal 4K UHD Smart TV черный',
        "category": "📺 Телевизоры",
        "url": "https://www.dns-shop.kz/product/demo-tv-189990/",
        "image_url": "https://c.dns-shop.kz/thumb/st1/fit/500/500/sample.jpg",
        "price": 189990,
        "city": "Астана"
    }

    print("1. Сохраняем товар в базу по нормальной цене: 189 990 ₸...")
    history1 = save_or_update_product(normal_product)
    print("   Результат:", history1)

    # Шаг 2: При следующем проходе контент-менеджер DNS допустил ошибку: 18 990 ₸
    glitched_product = dict(normal_product)
    glitched_product["price"] = 18990

    print("\n2. Монитор обнаружил изменение цены на 18 990 ₸...")
    history2 = save_or_update_product(glitched_product)
    print("   История обновлена:", history2)

    # Шаг 3: Проверка детектором
    print("\n3. Анализ детектора ценовых аномалий...")
    anomaly = check_anomaly(glitched_product, history2)

    if anomaly:
        print("   🔥 ДЕТЕКТОР СРАБОТАЛ!")
        send_alert(glitched_product, anomaly)
    else:
        print("   Детектор не обнаружил сбоя.")

if __name__ == "__main__":
    main()
