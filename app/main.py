import uuid
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, SessionLocal
from app.scheduler import create_scheduler
from app.bot import start_bot_thread

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database and create admin if needed
    await init_db()
    await _create_first_admin()

    # Start background scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info(f"Scheduler started — checking prices every {settings.check_interval_hours}h")

    # Start Telegram bot in daemon thread
    start_bot_thread()

    logger.info("Price Tracker started on port 8766")
    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


async def _create_first_admin() -> None:
    """Creates the initial admin user if no user exists with that email."""
    if not settings.first_admin_email or not settings.first_admin_password:
        return
    from sqlalchemy import select
    from app.models import User, InviteCode
    from app.auth import hash_password
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.email == settings.first_admin_email))
        if result.scalar_one_or_none():
            return  # already exists
        admin = User(
            email=settings.first_admin_email,
            username="admin",
            password_hash=hash_password(settings.first_admin_password),
            is_admin=True,
        )
        db.add(admin)
        await db.flush()
        # Create a used invite code so the admin can link their Telegram account
        code = InviteCode(code=str(uuid.uuid4()), created_by=admin.id, used_by=admin.id)
        db.add(code)
        await db.commit()
        logger.info(f"Admin user created: {settings.first_admin_email}")


app = FastAPI(lifespan=lifespan, title="Price Tracker")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

from app.routers import auth, products, admin, settings as settings_router  # noqa: E402

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(admin.router)
app.include_router(settings_router.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8766, reload=True)
