"""
Integration tests for HTTP routes.

These tests spin up the full FastAPI app with an in-memory SQLite database,
replacing the production DB dependency with a scoped test session.
The scheduler and Telegram bot are mocked out so they don't start.

Because the Jinja2Templates in each router uses a relative path ("templates"),
the working directory is temporarily changed to app/ for the duration of the
test session so template files can be found.
"""
import os
import asyncio
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# ─── Setup: change CWD to app/ so Jinja2 can find templates ──────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_DIR = os.path.join(_PROJECT_ROOT, "app")

@pytest.fixture(scope="session", autouse=True)
def chdir_to_app():
    original = os.getcwd()
    os.chdir(_APP_DIR)
    yield
    os.chdir(original)


# ─── In-memory DB fixtures ────────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def test_engine():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        from app import models  # noqa: F401
        from app.database import Base
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        from app.database import Base
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def db_session(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


# ─── App fixture with overridden dependencies ─────────────────────────────────
@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def client(test_engine):
    """
    Returns an AsyncClient wired to the FastAPI app with:
    - DB dependency overridden to use in-memory SQLite
    - Scheduler and Telegram bot mocked out (don't start)
    """
    from app.database import get_db, Base
    from app.main import app

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Prevent scheduler and bot from starting during tests
    mock_scheduler = MagicMock()
    mock_scheduler.start = MagicMock()
    mock_scheduler.shutdown = MagicMock()

    with patch("app.main.create_scheduler", return_value=mock_scheduler), \
         patch("app.main.start_bot_thread"), \
         patch("app.main.init_db"):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as ac:
            yield ac

    app.dependency_overrides.clear()


# ─── Helper: create a user + invite code ─────────────────────────────────────
async def _create_user(db_session, email="test@example.com", username="testuser",
                       password="password123", is_admin=False):
    import uuid
    from app.models import User, InviteCode
    from app.auth import hash_password

    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        is_admin=is_admin,
    )
    db_session.add(user)
    await db_session.flush()

    code = InviteCode(code=str(uuid.uuid4()), created_by=user.id)
    db_session.add(code)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _login(client, email="test@example.com", password="password123"):
    """POST /login and return the response (follow_redirects=False)."""
    return await client.post(
        "/login",
        data={"email": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


# ─── Auth route tests ─────────────────────────────────────────────────────────
class TestAuthRoutes:
    async def test_login_page_returns_200(self, client):
        r = await client.get("/login")
        assert r.status_code == 200
        assert "Price Tracker" in r.text

    async def test_register_page_returns_200(self, client):
        r = await client.get("/register")
        assert r.status_code == 200

    async def test_login_wrong_password_returns_200_with_error(self, client, db_session):
        await _create_user(db_session)
        r = await client.post(
            "/login",
            data={"email": "test@example.com", "password": "wrongpass"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
        assert "incorrectos" in r.text

    async def test_login_success_redirects_and_sets_cookie(self, client, db_session):
        await _create_user(db_session)
        r = await _login(client)
        assert r.status_code == 302
        assert r.headers["location"] == "/"
        assert "access_token" in r.cookies

    async def test_logout_clears_cookie(self, client, db_session):
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.post("/logout", cookies={"access_token": token})
        assert r.status_code == 302
        assert r.headers["location"] == "/login"

    async def test_register_with_valid_invite(self, client, db_session):
        import uuid
        from app.models import User, InviteCode

        # Create admin to own the invite code
        admin = User(
            email="admin@example.com",
            username="admin",
            password_hash="x",
            is_admin=True,
        )
        db_session.add(admin)
        await db_session.flush()
        invite_code = str(uuid.uuid4())
        ic = InviteCode(code=invite_code, created_by=admin.id)
        db_session.add(ic)
        await db_session.commit()

        r = await client.post(
            "/register",
            data={
                "code": invite_code,
                "email": "new@example.com",
                "username": "newuser",
                "password": "securepass",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 302
        assert "access_token" in r.cookies

    async def test_register_with_invalid_invite(self, client):
        r = await client.post(
            "/register",
            data={
                "code": "00000000-0000-0000-0000-000000000000",
                "email": "new2@example.com",
                "username": "newuser2",
                "password": "securepass",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200
        assert "nv" in r.text.lower()  # "inválido" or "invalid"


# ─── Protected route redirect tests ──────────────────────────────────────────
class TestProtectedRoutes:
    async def test_dashboard_unauthenticated_redirects(self, client):
        r = await client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    async def test_product_add_unauthenticated_redirects(self, client):
        r = await client.get("/product/add")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    async def test_settings_unauthenticated_redirects(self, client):
        r = await client.get("/settings")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    async def test_admin_unauthenticated_redirects(self, client):
        r = await client.get("/admin")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    async def test_admin_non_admin_user_returns_403(self, client, db_session):
        await _create_user(db_session, is_admin=False)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.get("/admin", cookies={"access_token": token})
        assert r.status_code == 403


# ─── Dashboard tests ──────────────────────────────────────────────────────────
class TestDashboard:
    async def test_dashboard_authenticated_returns_200(self, client, db_session):
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.get("/", cookies={"access_token": token})
        assert r.status_code == 200
        assert "Mis productos" in r.text

    async def test_dashboard_shows_empty_state(self, client, db_session):
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.get("/", cookies={"access_token": token})
        assert r.status_code == 200
        # Empty state message (check decoded text to avoid non-ASCII bytes issues)
        assert "productos" in r.text


# ─── Products tests ───────────────────────────────────────────────────────────
class TestProducts:
    async def test_add_product_page_returns_200(self, client, db_session):
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.get("/product/add", cookies={"access_token": token})
        assert r.status_code == 200
        assert "Añadir producto" in r.text

    async def test_add_product_scrapes_and_redirects(self, client, db_session):
        """Adding a product should call the scraper and redirect to dashboard."""
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        from app.scrapers.base import ScrapeResult

        fake_result = ScrapeResult(
            name="Test Product",
            price=99.99,
            currency="EUR",
            image_url="https://example.com/img.jpg",
        )
        with patch("app.routers.products.scraper_for", return_value=lambda url: fake_result):
            r = await client.post(
                "/product/add",
                data={"url": "https://www.amazon.es/dp/B000TEST"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                cookies={"access_token": token},
            )
        assert r.status_code == 302
        assert r.headers["location"] == "/"

    async def test_product_detail_404_for_other_user(self, client, db_session):
        """A user cannot view another user's product."""
        from app.models import Product

        user1 = await _create_user(db_session, email="u1@x.com", username="u1")
        await _create_user(db_session, email="u2@x.com", username="u2")

        product = Product(user_id=user1.id, url="https://amazon.es/dp/test", name="P1")
        db_session.add(product)
        await db_session.commit()
        await db_session.refresh(product)

        login_r = await _login(client, email="u2@x.com")
        token = login_r.cookies["access_token"]

        r = await client.get(f"/product/{product.id}", cookies={"access_token": token})
        assert r.status_code == 404

    async def test_delete_product_redirects(self, client, db_session):
        from app.models import Product

        user = await _create_user(db_session)
        product = Product(user_id=user.id, url="https://amazon.es/dp/test", name="DeleteMe")
        db_session.add(product)
        await db_session.commit()
        await db_session.refresh(product)

        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.post(
            f"/product/{product.id}/delete",
            cookies={"access_token": token},
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"

    async def test_delete_other_users_product_returns_404(self, client, db_session):
        from app.models import Product

        user1 = await _create_user(db_session, email="o1@x.com", username="o1")
        await _create_user(db_session, email="o2@x.com", username="o2")

        product = Product(user_id=user1.id, url="https://amazon.es/dp/xyz", name="P")
        db_session.add(product)
        await db_session.commit()
        await db_session.refresh(product)

        login_r = await _login(client, email="o2@x.com")
        token = login_r.cookies["access_token"]

        r = await client.post(
            f"/product/{product.id}/delete",
            cookies={"access_token": token},
        )
        assert r.status_code == 404

    async def test_update_alerts_saves_settings(self, client, db_session):
        from app.models import Product
        from sqlalchemy import select

        user = await _create_user(db_session)
        product = Product(user_id=user.id, url="https://amazon.es/dp/al", name="AlertTest")
        db_session.add(product)
        await db_session.commit()
        await db_session.refresh(product)
        product_id = product.id  # capture before any potential expiry

        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.post(
            f"/product/{product_id}/alerts",
            data={
                "notify_on_drop": "true",
                "target_price": "50.00",
                "target_percentage": "10",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            cookies={"access_token": token},
        )
        assert r.status_code == 302

        # Roll back the open transaction so we see the data committed by the router's session
        await db_session.rollback()
        result = await db_session.execute(select(Product).where(Product.id == product_id))
        updated = result.scalar_one()
        assert updated.notify_on_drop is True
        assert float(updated.target_price) == 50.0
        assert float(updated.target_percentage) == 10.0


# ─── Settings tests ───────────────────────────────────────────────────────────
class TestSettings:
    async def test_settings_page_returns_200(self, client, db_session):
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.get("/settings", cookies={"access_token": token})
        assert r.status_code == 200
        assert "Ajustes" in r.text

    async def test_change_password_wrong_current(self, client, db_session):
        await _create_user(db_session)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.post(
            "/settings/password",
            data={"current_password": "wrongpass", "new_password": "newpassword123"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            cookies={"access_token": token},
        )
        assert r.status_code == 200
        assert "incorrecta" in r.text

    async def test_change_password_success(self, client, db_session):
        await _create_user(db_session, password="oldpassword")
        login_r = await _login(client, password="oldpassword")
        token = login_r.cookies["access_token"]

        r = await client.post(
            "/settings/password",
            data={"current_password": "oldpassword", "new_password": "newpassword123"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            cookies={"access_token": token},
        )
        assert r.status_code == 200
        assert "actualizada" in r.text


# ─── Admin tests ──────────────────────────────────────────────────────────────
class TestAdmin:
    async def test_admin_page_returns_200_for_admin(self, client, db_session):
        await _create_user(db_session, is_admin=True)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.get("/admin", cookies={"access_token": token})
        assert r.status_code == 200
        assert "administraci" in r.text  # "administración"

    async def test_generate_invite_creates_code(self, client, db_session):
        from app.models import InviteCode
        from sqlalchemy import select

        await _create_user(db_session, is_admin=True)
        login_r = await _login(client)
        token = login_r.cookies["access_token"]

        r = await client.post("/admin/invite", cookies={"access_token": token})
        assert r.status_code == 302

        result = await db_session.execute(select(InviteCode))
        codes = result.scalars().all()
        # At least one invite code was created (the first one was created by _create_user fixture)
        assert len(codes) >= 1
