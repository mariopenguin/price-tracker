import json
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}


def scrape(url: str) -> Optional[ScrapeResult]:
    """requests + BS4 first; falls back to headless browser if no price found."""
    result = _scrape_with_requests(url)
    if result is None:
        from app.scrapers.js_scraper import scrape as js_scrape
        result = js_scrape(url)
    return result


def _scrape_with_requests(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        return _extract(soup)
    except Exception:
        return None


def _extract_image_url(image) -> Optional[str]:
    """Normaliza el campo 'image' del JSON-LD, que puede ser string, dict o lista."""
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        return image.get("contentUrl") or image.get("url")
    if isinstance(image, list) and image:
        return _extract_image_url(image[0])
    return None


def _extract(soup) -> Optional[ScrapeResult]:
    # Strategy 1: JSON-LD schema.org/Product
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product":
                offers = data.get("offers", {})
                price_raw = offers.get("price") or offers.get("lowPrice")
                if price_raw:
                    return ScrapeResult(
                        price=float(str(price_raw).replace(",", ".")),
                        currency=offers.get("priceCurrency", "EUR"),
                        name=data.get("name", "Producto"),
                        image_url=_extract_image_url(data.get("image")),
                    )
        except Exception:
            continue

    # Strategy 2: itemprop microdata
    price_el = soup.find(attrs={"itemprop": "price"})
    name_el = soup.find(attrs={"itemprop": "name"}) or soup.find("h1")
    if price_el and name_el:
        price_raw = price_el.get("content") or price_el.get_text()
        try:
            img = soup.find(attrs={"itemprop": "image"})
            image_url = (img.get("src") or img.get("content")) if img else None
            return ScrapeResult(
                price=_parse_price(price_raw),
                currency="EUR",
                name=name_el.get_text(strip=True),
                image_url=image_url,
            )
        except Exception:
            pass

    # Strategy 3: Open Graph meta tags
    og_price = soup.find("meta", property="product:price:amount")
    og_title = soup.find("meta", property="og:title")
    og_image = soup.find("meta", property="og:image")
    if og_price and og_title:
        try:
            return ScrapeResult(
                price=float(og_price.get("content", "0").replace(",", ".")),
                currency="EUR",
                name=og_title.get("content", "Producto"),
                image_url=og_image.get("content") if og_image else None,
            )
        except Exception:
            pass

    return None


def _parse_price(text: str) -> float:
    clean = re.sub(r"[^\d,\.]", "", text.strip())
    if "," in clean and "." in clean:
        # European format: 1.234,56 -> remove thousand sep, convert decimal
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    return float(clean)
