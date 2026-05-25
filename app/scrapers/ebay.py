import requests
from typing import Optional
from app.scrapers.base import ScrapeResult
from app.scrapers.mediamarkt import _try_jsonld, _parse_price

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}


def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        result = _try_jsonld(soup)
        if result:
            return result
        price_el = soup.select_one("#prcIsum, .x-price-primary [itemprop='price']")
        name_el = soup.select_one("#itemTitle, h1.x-item-title__mainTitle")
        if not price_el or not name_el:
            return None
        img = soup.select_one("#icImg, .ux-image-carousel-item img")
        return ScrapeResult(
            price=_parse_price(price_el.get("content") or price_el.get_text()),
            currency="EUR",
            name=name_el.get_text(strip=True).replace("Detalles sobre  ", ""),
            image_url=img.get("src") if img else None,
        )
    except Exception:
        return None
