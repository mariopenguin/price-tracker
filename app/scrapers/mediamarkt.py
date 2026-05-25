import json
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}


def _parse_price(text: str) -> float:
    clean = re.sub(r"[^\d,\.]", "", text.strip())
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    return float(clean)


def _try_jsonld(soup) -> Optional[ScrapeResult]:
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
                        image_url=data.get("image"),
                    )
        except Exception:
            continue
    return None


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
        price_el = soup.select_one('[data-test="product-price"]') or soup.select_one(".price")
        name_el = soup.select_one("h1")
        if not price_el or not name_el:
            return None
        img = soup.select_one("img.product-image, img[data-test='product-image']")
        return ScrapeResult(
            price=_parse_price(price_el.get_text()),
            currency="EUR",
            name=name_el.get_text(strip=True),
            image_url=img.get("src") if img else None,
        )
    except Exception:
        return None
