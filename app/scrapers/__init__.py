from typing import Callable
from urllib.parse import urlparse


def scraper_for(url: str) -> Callable:
    """Returns the appropriate scrape function for the given URL."""
    domain = urlparse(url).netloc.lower()
    if "amazon.es" in domain or "amazon.com" in domain:
        from app.scrapers.amazon import scrape
    elif "mediamarkt.es" in domain:
        from app.scrapers.mediamarkt import scrape
    elif "pccomponentes.com" in domain:
        from app.scrapers.pccomponentes import scrape
    elif "elcorteingles.es" in domain:
        from app.scrapers.elcorteingles import scrape
    elif "ebay.es" in domain or "ebay.com" in domain:
        from app.scrapers.ebay import scrape
    else:
        from app.scrapers.generic import scrape
    return scrape
