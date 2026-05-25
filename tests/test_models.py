import pytest
from decimal import Decimal
from sqlalchemy import select
from app.models import User, InviteCode, Product, PriceHistory

@pytest.mark.asyncio
async def test_create_user(db_session):
    user = User(email="a@b.com", username="alice", password_hash="hash", is_admin=False)
    db_session.add(user)
    await db_session.commit()
    result = await db_session.execute(select(User).where(User.email == "a@b.com"))
    found = result.scalar_one()
    assert found.username == "alice"

@pytest.mark.asyncio
async def test_product_cascade_delete(db_session):
    user = User(email="b@b.com", username="bob", password_hash="hash")
    db_session.add(user)
    await db_session.flush()
    product = Product(user_id=user.id, url="https://example.com", name="Test")
    db_session.add(product)
    await db_session.flush()
    history = PriceHistory(product_id=product.id, price=Decimal("9.99"))
    db_session.add(history)
    await db_session.commit()

    await db_session.delete(product)
    await db_session.commit()

    result = await db_session.execute(select(PriceHistory).where(PriceHistory.product_id == product.id))
    assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_invite_code_fields(db_session):
    user = User(email="c@b.com", username="carol", password_hash="hash")
    db_session.add(user)
    await db_session.flush()
    import uuid
    code = InviteCode(code=str(uuid.uuid4()), created_by=user.id)
    db_session.add(code)
    await db_session.commit()
    result = await db_session.execute(select(InviteCode).where(InviteCode.created_by == user.id))
    found = result.scalar_one()
    assert found.used_by is None
    assert found.used_at is None
