import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.config import settings

logger = logging.getLogger(__name__)

KNOWN_DOMAINS = {
    "amazon.es", "amazon.com", "mediamarkt.es",
    "pccomponentes.com", "elcorteingles.es", "ebay.es", "ebay.com",
}


def check_all_products() -> None:
    """Synchronous job entry point — runs async logic in a new event loop."""
    asyncio.run(_check_all_products_async())


async def _check_all_products_async() -> None:
    from app.database import SessionLocal
    from app.models import Product
    from sqlalchemy import select
    from urllib.parse import urlparse

    async with SessionLocal() as db:
        result = await db.execute(select(Product).where(Product.is_active == True))
        products = result.scalars().all()

    # Process known-domain products first, then generic/browser-based ones
    standard = [p for p in products if any(d in urlparse(p.url).netloc for d in KNOWN_DOMAINS)]
    generic = [p for p in products if p not in standard]

    for product in standard + generic:
        try:
            await _scrape_product(product.id, product.url)
        except Exception as e:
            logger.error(f"Unexpected error processing product {product.id}: {e}")


async def _scrape_product(product_id: int, url: str) -> None:
    from app.database import SessionLocal
    from app.models import Product, PriceHistory
    from app.scrapers import scraper_for
    from app.notifier import should_notify, format_notification, send_telegram_message
    from app.models import User
    from sqlalchemy import select, func

    scrape_fn = scraper_for(url)
    scrape_result = None
    loop = asyncio.get_event_loop()
    for attempt in range(2):
        try:
            scrape_result = await loop.run_in_executor(None, scrape_fn, url)
            if scrape_result:
                break
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")

    if not scrape_result:
        logger.error(f"Could not get price for {url}")
        return

    async with SessionLocal() as db:
        product = await db.get(Product, product_id)
        if not product:
            return

        old_price = float(product.current_price) if product.current_price else None
        new_price = scrape_result.price

        today = datetime.now(timezone.utc).date().isoformat()
        existing = await db.execute(
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .where(func.date(PriceHistory.recorded_at) == today)
        )
        today_record = existing.scalar_one_or_none()
        if today_record:
            today_record.price = new_price
        else:
            db.add(PriceHistory(product_id=product.id, price=new_price))

        product.current_price = new_price
        product.last_checked_at = datetime.now(timezone.utc)
        if scrape_result.name and product.name in ("Producto", "Producto Amazon"):
            product.name = scrape_result.name
        if scrape_result.image_url and not product.image_url:
            product.image_url = scrape_result.image_url

        await db.commit()

        # Send notification if applicable
        if old_price and should_notify(
            old=old_price,
            new=new_price,
            notify_on_drop=product.notify_on_drop,
            target_price=float(product.target_price) if product.target_price else None,
            target_pct=float(product.target_percentage) if product.target_percentage else None,
        ):
            user = await db.get(User, product.user_id)
            if user and user.telegram_chat_id:
                msg = format_notification(product.name, old_price, new_price, product.url)
                send_telegram_message(user.telegram_chat_id, msg)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        check_all_products,
        "interval",
        hours=settings.check_interval_hours,
        id="check_prices",
        replace_existing=True,
    )
    return scheduler
