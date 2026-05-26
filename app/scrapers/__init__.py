import re
from typing import Callable
from urllib.parse import urlparse, urlunparse


# Parámetros de tracking que no aportan nada al scraping
_TRACKING_PARAMS = {
    "spm", "algo_pvid", "algo_exp_id", "pdp_ext_f", "pdp_npi",
    "curPageLogUid", "utparam-url", "pvid", "scm", "gatewayAdapt",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "tag", "aff_platform", "aff_trace_key",
}


def normalize_url(url: str) -> str:
    """Limpia parámetros de tracking y devuelve una URL canónica."""
    from urllib.parse import parse_qs, urlencode
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # AliExpress: extraer solo el product ID y construir URL limpia
    if "aliexpress.com" in domain:
        m = re.search(r"/item/(\d+)", parsed.path)
        if m:
            product_id = m.group(1)
            return f"https://es.aliexpress.com/item/{product_id}.html"

    # Resto: eliminar parámetros de tracking conocidos
    params = parse_qs(parsed.query, keep_blank_values=True)
    clean = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
    clean_query = urlencode(clean, doseq=True)
    return urlunparse(parsed._replace(query=clean_query))


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
    elif "aliexpress.com" in domain:
        from app.scrapers.js_scraper import scrape
    else:
        from app.scrapers.generic import scrape
    return scrape
