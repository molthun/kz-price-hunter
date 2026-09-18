"""ANTS (ants.kz): компьютерная техника и электроника, Алматы."""
from scrapers.schema_listing import SchemaListingScraper

class AntsScraper(SchemaListingScraper):
    SHOP_NAME = "ANTS"
    SHOP_EMOJI = "🐜"
    BASE_URL = "https://ants.kz"
    ID_PREFIX = "ants"
    ID_PATTERN = r"/tovar-(\d+)"
    # Проверено: выдача ANTS идет «сначала в наличии», затем только отсутствующие товары
    IN_STOCK_FIRST = True
