import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}


def _parse_price(text: str) -> float:
    clean = re.sub(r"[^\d,\.]", "", text.strip())
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    return float(clean)


def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        title_el = soup.find(id="productTitle")
        name = title_el.get_text(strip=True) if title_el else "Producto Amazon"

        offscreen = soup.select_one(".a-offscreen")
        if offscreen:
            price = _parse_price(offscreen.get_text(strip=True))
        else:
            whole = soup.select_one(".a-price-whole")
            frac = soup.select_one(".a-price-fraction")
            if whole and frac:
                price = _parse_price(
                    f"{whole.get_text(strip=True).replace('.', '')},{frac.get_text(strip=True)}"
                )
            else:
                return None

        img = soup.find(id="landingImage")
        image_url = img.get("src") if img else None
        return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
    except Exception:
        return None
