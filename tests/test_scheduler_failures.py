import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import scheduler
from app.models import User, Product, PriceHistory, PriceCheckFailure


@pytest.mark.asyncio
async def test_failed_check_is_recorded_in_history(db_session, monkeypatch):
    user = User(email="f@b.com", username="fail", password_hash="h")
    db_session.add(user)
    await db_session.flush()
    product = Product(user_id=user.id, url="https://example.com/p", name="P")
    db_session.add(product)
    await db_session.commit()

    monkeypatch.setattr(
        "app.database.SessionLocal",
        async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False),
    )

    def boom(url):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr("app.scrapers.scraper_for", lambda url: boom)

    await scheduler._scrape_product(product.id, product.url)

    failures = (await db_session.execute(select(PriceCheckFailure))).scalars().all()
    assert [f.reason for f in failures] == ["chromium crashed"]
    assert (await db_session.execute(select(PriceHistory))).scalars().all() == []
