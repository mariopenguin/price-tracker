import os
import logging
import re
import threading
from typing import Optional

from app.scrapers.base import ScrapeResult

_browser_lock = threading.Lock()
logger = logging.getLogger(__name__)

# Chromedriver paths — ordered by platform
CHROMEDRIVER_PATHS = [
    "/usr/bin/chromedriver",                     # Debian Docker (chromium-driver)
    "/usr/lib/chromium-browser/chromedriver",    # Raspberry Pi OS (chromium-chromedriver)
    "/usr/lib/chromium/chromedriver",            # Debian native
]

# Chromium/Chrome binary paths
CHROMIUM_PATHS = [
    "/usr/bin/chromium-browser",                                      # Raspberry Pi OS
    "/usr/bin/chromium",                                              # Debian / Ubuntu
    "/usr/bin/google-chrome",                                         # Linux Chrome
    "/Applications/Chromium.app/Contents/MacOS/Chromium",            # macOS Chromium
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS Chrome
]

BROWSER_ARGS = [
    "--headless",
    "--no-sandbox",           # requerido en Pi y Docker
    "--disable-dev-shm-usage",  # /dev/shm pequeño en Pi
    "--disable-gpu",
    "--disable-extensions",
    "--mute-audio",
]

# Detectar selenium una sola vez al importar
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False
    logger.warning(
        "Selenium no está instalado — el scraping JS está desactivado. "
        "Instálalo con: pip install selenium"
    )


def _find(paths: list) -> Optional[str]:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _parse_price(text: str) -> float:
    clean = re.sub(r"[^\d,\.]", "", text.strip())
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    return float(clean)


PRICE_SELECTORS = [
    "[itemprop='price']",
    "[data-testid='price']",
    "[data-pl='product-price']",
    ".product-price-value",
    ".price-current",
    ".uniform-banner-box-price",
]

NAME_SELECTORS = [
    "[itemprop='name']",
    "[data-pl='product-title']",
    ".product-title",
    "h1",
]


def scrape(url: str) -> Optional[ScrapeResult]:
    if not _SELENIUM_AVAILABLE:
        return None

    from bs4 import BeautifulSoup
    from app.scrapers.generic import _extract

    options = Options()
    for arg in BROWSER_ARGS:
        options.add_argument(arg)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    chromium_path = _find(CHROMIUM_PATHS)
    if chromium_path:
        options.binary_location = chromium_path

    # Si hay chromedriver en paths del sistema úsalo explícitamente;
    # si no, selenium-manager lo descarga automáticamente (funciona en Mac/amd64)
    chromedriver_path = _find(CHROMEDRIVER_PATHS)
    service = Service(executable_path=chromedriver_path) if chromedriver_path else Service()

    with _browser_lock:
        driver = None
        try:
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(30)

            # Ocultar que es un navegador automatizado antes de cargar la página
            try:
                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                    "source": """
                        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                        window.chrome = { runtime: {} };
                    """
                })
            except Exception:
                pass  # CDP no disponible en versiones antiguas de Chromium

            driver.get(url)

            import time
            time.sleep(6)  # Pi es más lenta; dar tiempo al JS

            # Estrategia 1: extracción genérica sobre el HTML renderizado
            soup = BeautifulSoup(driver.page_source, "html.parser")
            result = _extract(soup)
            if result:
                return result

            # Estrategia 2: selectores CSS en el DOM en vivo
            price: Optional[float] = None
            for selector in PRICE_SELECTORS:
                els = driver.find_elements("css selector", selector)
                if not els:
                    continue
                raw = els[0].get_attribute("content") or els[0].text
                try:
                    price = _parse_price(raw)
                    break
                except Exception:
                    continue

            if price is None:
                logger.warning(f"js_scraper: precio no encontrado en {url}")
                return None

            name = "Producto"
            for selector in NAME_SELECTORS:
                els = driver.find_elements("css selector", selector)
                if els:
                    name = els[0].text.strip()
                    break

            og_image = driver.find_elements("css selector", "meta[property='og:image']")
            image_url = og_image[0].get_attribute("content") if og_image else None

            return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)

        except Exception as e:
            logger.error(f"js_scraper falló para {url}: {e}")
            return None
        finally:
            if driver:
                driver.quit()
