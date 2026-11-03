"""
Unit tests for domain/image_quality.py.
"""
from domain.image_quality import upgrade_image_url, upgrade_image_urls


def test_upgrade_kaspi_image_url():
    # With existing preview format
    u1 = "https://resources.cdn-kaspi.kz/img/m/p/h48/hd3/63904460603422.jpg?format=preview-medium"
    assert upgrade_image_url(u1) == "https://resources.cdn-kaspi.kz/img/m/p/h48/hd3/63904460603422.jpg?format=gallery-large"

    u2 = "https://resources.cdn-kaspi.kz/img/m/p/h48/hd3/63904460603422.jpg?format=preview-small"
    assert upgrade_image_url(u2) == "https://resources.cdn-kaspi.kz/img/m/p/h48/hd3/63904460603422.jpg?format=gallery-large"

    # Without format param
    u3 = "https://resources.cdn-kaspi.kz/img/m/p/h48/hd3/63904460603422.jpg"
    assert upgrade_image_url(u3) == "https://resources.cdn-kaspi.kz/img/m/p/h48/hd3/63904460603422.jpg?format=gallery-large"


def test_upgrade_kari_image_url():
    u = "https://cdn.api.kari.com/f/75504050_001-preview.jpg"
    assert upgrade_image_url(u) == "https://cdn.api.kari.com/f/75504050_001.jpg"

    u_webp = "https://cdn.api.kari.com/f/75504050_001-preview.webp"
    assert upgrade_image_url(u_webp) == "https://cdn.api.kari.com/f/75504050_001.webp"


def test_upgrade_marwin_image_url():
    u = "https://simg.marwin.kz/media/catalog/product/cache/caed27b441cb6475334305a4c65b6330/w/5/deftones.jpg"
    assert upgrade_image_url(u) == "https://simg.marwin.kz/media/catalog/product/w/5/deftones.jpg"


def test_upgrade_technodom_image_url():
    u = "https://api.technodom.kz/f3/api/v1/images/272/272/298760_1.jpg"
    assert upgrade_image_url(u) == "https://api.technodom.kz/f3/api/v1/images/800/800/298760_1.jpg"


def test_upgrade_alser_image_url():
    u = "https://s3s.alser.kz/uploads/products/noutbuki/18a/noutbuk-cehol-w160.webp"
    assert upgrade_image_url(u) == "https://s3s.alser.kz/uploads/products/noutbuki/18a/noutbuk-cehol.webp"


def test_upgrade_fortemarket_image_url():
    u1 = "https://market.forte.kz/darbiz/catalogs/506/s/bcc19e8a-5069-11f0-b9f6-d616217d20cd.webp"
    assert upgrade_image_url(u1) == "https://object.pscloud.io/darbiz/catalogs/506/bcc19e8a-5069-11f0-b9f6-d616217d20cd.webp"

    u2 = "https://object.pscloud.io/darbiz/catalogs/506/m/bcc19e8a.jpg"
    assert upgrade_image_url(u2) == "https://object.pscloud.io/darbiz/catalogs/506/bcc19e8a.jpg"


def test_upgrade_image_urls_list():
    urls = [
        "https://cdn.api.kari.com/f/1-preview.jpg",
        "https://cdn.api.kari.com/f/1.jpg",  # duplicate after upgrade
        "https://api.technodom.kz/f3/api/v1/images/272/272/2.jpg",
        "",
        None,
    ]
    res = upgrade_image_urls(urls)
    assert res == [
        "https://cdn.api.kari.com/f/1.jpg",
        "https://api.technodom.kz/f3/api/v1/images/800/800/2.jpg",
    ]
