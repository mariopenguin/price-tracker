import os
import logging
import re
import threading
from typing import Optional

from app.scrapers.base import ScrapeResult

_browser_lock = threading.Lock()

logger = logging.getLogger(__name__)

# Detectar disponibilidad de playwright una sola vez al importar
try:
    import playwright  # noqa: F401
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright no está instalado — el scraping JS está desactivado en esta arquitectura.")

# System Chromium paths — Playwright's bundled binary has no ARM32 build
SYSTEM_CHROMIUM_PATHS = [
    "/usr/bin/chromium-browser",  # Raspberry Pi OS
    "/usr/bin/chromium",          # Debian / Ubuntu
    "/usr/bin/google-chrome",
]

BROWSER_ARGS = [
    "--no-sandbox",           # required on Pi (no user namespace support)
    "--disable-dev-shm-usage",  # /dev/shm is tiny on Pi; use /tmp instead
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--mute-audio",
]

# Generic CSS selectors tried in order when HTML extraction fails
PRICE_SELECTORS = [
    "[itemprop='price']",
    "[data-testid='price']",
    "[data-pl='product-price']",
    ".product-price-value",
    ".price-current",
    ".uniform-banner-box-price",
    "[class*='price--'][class*='uniform']",
]

NAME_SELECTORS = [
    "[itemprop='name']",
    "[data-pl='product-title']",
    ".product-title",
    "h1",
]


def _system_chromium() -> Optional[str]:
    for path in SYSTEM_CHROMIUM_PATHS:
        if os.path.exists(path):
            return path
    return None


def _parse_price(text: str) -> float:
    clean = re.sub(r"[^\d,\.]", "", text.strip())
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    return float(clean)


def scrape(url: str) -> Optional[ScrapeResult]:
    chromium_path = _system_chromium()
    if not chromium_path:
        logger.error(
            "No system Chromium found. Install it with: sudo apt install chromium-browser"
        )
        return None

    if not _PLAYWRIGHT_AVAILABLE:
        return None

    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup
        from app.scrapers.generic import _extract
    except ImportError as e:
        logger.warning(f"js_scraper: dependencia no disponible — {e}")
        return None

    with _browser_lock:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=chromium_path,
                headless=True,
                args=BROWSER_ARGS,
            )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                    ),
                    locale="es-ES",
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(3_000)  # let JS render prices

                # Strategy 1: reuse generic extractor on fully rendered HTML
                soup = BeautifulSoup(page.content(), "html.parser")
                result = _extract(soup)
                if result:
                    return result

                # Strategy 2: query live DOM with known CSS selectors
                price: Optional[float] = None
                for selector in PRICE_SELECTORS:
                    el = page.query_selector(selector)
                    if not el:
                        continue
                    raw = el.get_attribute("content") or el.inner_text()
                    try:
                        price = _parse_price(raw)
                        break
                    except Exception:
                        continue

                if price is None:
                    logger.warning(f"js_scraper: no price found for {url}")
                    return None

                name = "Producto"
                for selector in NAME_SELECTORS:
                    el = page.query_selector(selector)
                    if el:
                        name = el.inner_text().strip()
                        break

                og_image = page.query_selector("meta[property='og:image']")
                image_url = og_image.get_attribute("content") if og_image else None

                return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)

            except Exception as e:
                logger.error(f"js_scraper failed for {url}: {e}")
                return None
            finally:
                browser.close()
