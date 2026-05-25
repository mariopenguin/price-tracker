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
    """Level 1: requests + BS4. Level 2: Selenium if level 1 fails."""
    result = _scrape_with_requests(url)
    if result:
        return result
    return _scrape_with_selenium(url)


def _scrape_with_requests(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        return _extract(soup)
    except Exception:
        return None


def _scrape_with_selenium(url: str) -> Optional[ScrapeResult]:
    """Starts Chromium headless, extracts price, closes immediately to free RAM."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from bs4 import BeautifulSoup
        import time

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.binary_location = "/usr/bin/chromium"

        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.get(url)
            time.sleep(3)  # wait for JS render
            soup = BeautifulSoup(driver.page_source, "lxml")
            return _extract(soup)
        finally:
            driver.quit()  # always close to free memory
    except Exception:
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
                        image_url=data.get("image"),
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
