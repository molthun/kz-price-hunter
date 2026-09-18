"""ITMag (itmag.kz): компьютерная техника и IT-оборудование, Алматы."""
from scrapers.schema_listing import SchemaListingScraper

class ItmagScraper(SchemaListingScraper):
    SHOP_NAME = "ITMag"
    SHOP_EMOJI = "💾"
    BASE_URL = "https://itmag.kz"
    ID_PREFIX = "itmag"
    ID_PATTERN = r"/p/(\d+)"
