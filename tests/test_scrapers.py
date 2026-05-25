from unittest.mock import patch, MagicMock
from app.scrapers.base import ScrapeResult
from app.scrapers import scraper_for


def test_scrape_result_dataclass():
    r = ScrapeResult(price=29.99, currency="EUR", name="Producto", image_url=None)
    assert r.price == 29.99


def test_scraper_for_amazon():
    from app.scrapers.amazon import scrape as amazon_scrape
    assert scraper_for("https://www.amazon.es/dp/B08N5WRWNW") == amazon_scrape


def test_scraper_for_mediamarkt():
    from app.scrapers.mediamarkt import scrape as mm_scrape
    assert scraper_for("https://www.mediamarkt.es/es/product/_auriculares.html") == mm_scrape


def test_scraper_for_pccomponentes():
    from app.scrapers.pccomponentes import scrape as pc_scrape
    assert scraper_for("https://www.pccomponentes.com/producto") == pc_scrape


def test_scraper_for_unknown_uses_generic():
    from app.scrapers.generic import scrape as generic_scrape
    assert scraper_for("https://www.tiendarara.com/producto") == generic_scrape


@patch("app.scrapers.amazon.requests.get")
def test_amazon_scraper_parses_price(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html><body>
    <span id="productTitle">Teclado Mecánico</span>
    <span class="a-offscreen">49,99 €</span>
    <img id="landingImage" src="https://img.example.com/teclado.jpg"/>
    </body></html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.amazon import scrape
    result = scrape("https://www.amazon.es/dp/XXXXX")
    assert result is not None
    assert result.price == 49.99
    assert "Teclado" in result.name


@patch("app.scrapers.mediamarkt.requests.get")
def test_mediamarkt_jsonld(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "TV Samsung", "offers": {"price": "599.99", "priceCurrency": "EUR"}, "image": "https://img.com/tv.jpg"}
    </script>
    </head><body></body></html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.mediamarkt import scrape
    result = scrape("https://www.mediamarkt.es/es/product/tv.html")
    assert result is not None
    assert result.price == 599.99
    assert result.name == "TV Samsung"


@patch("app.scrapers.generic.requests.get")
def test_generic_scraper_jsonld(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Cámara Sony", "offers": {"price": "349.99"}, "image": "https://img.com/cam.jpg"}
    </script>
    </head><body></body></html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.generic import scrape
    result = scrape("https://www.tiendarara.com/producto")
    assert result is not None
    assert result.price == 349.99
    assert result.name == "Cámara Sony"


@patch("app.scrapers.generic.requests.get")
def test_generic_scraper_itemprop(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html><body>
    <h1 itemprop="name">Ratón gaming</h1>
    <span itemprop="price" content="29.99">29,99 €</span>
    </body></html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.generic import scrape
    result = scrape("https://www.tiendarara.com/raton")
    assert result is not None
    assert result.price == 29.99
