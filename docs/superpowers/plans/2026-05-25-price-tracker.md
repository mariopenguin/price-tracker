# Price Tracker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aplicación web FastAPI con login, tracking de precios via scraping periódico, histórico diario en SQLite, notificaciones y bot de Telegram.

**Architecture:** Monolito FastAPI + APScheduler (in-process scheduler) + SQLite (aiosqlite async). El bot de Telegram corre en un hilo daemon con su propio event loop. Todo en un único contenedor Docker desplegable en Portainer.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, SQLAlchemy (async), aiosqlite, APScheduler, python-telegram-bot, requests, BeautifulSoup4, selenium, passlib, python-jose, uvicorn (sin [standard]).

---

## File Map

```
price-tracker/
├── app/
│   ├── main.py              # FastAPI app factory + lifespan (arranca scheduler y bot)
│   ├── config.py            # Settings via pydantic-settings (lee .env)
│   ├── database.py          # Engine async + SessionLocal + init_db()
│   ├── models.py            # SQLAlchemy ORM: User, InviteCode, Product, PriceHistory
│   ├── auth.py              # hash_password, verify_password, create_token, decode_token
│   ├── dependencies.py      # get_current_user (FastAPI dependency)
│   ├── scheduler.py         # APScheduler setup + job check_all_products()
│   ├── notifier.py          # send_telegram_notification(chat_id, message)
│   ├── bot.py               # Telegram bot handlers + run_bot()
│   ├── scrapers/
│   │   ├── __init__.py      # scraper_for(url) -> scrape_price()
│   │   ├── base.py          # ScrapeResult dataclass
│   │   ├── amazon.py
│   │   ├── ebay.py
│   │   ├── mediamarkt.py
│   │   ├── pccomponentes.py
│   │   ├── elcorteingles.py
│   │   └── generic.py       # BS4 level-1 + Selenium level-2
│   ├── routers/
│   │   ├── auth.py          # /login, /register, /logout
│   │   ├── products.py      # /, /product/add, /product/{id}, /product/{id}/delete, /product/{id}/alerts
│   │   ├── admin.py         # /admin
│   │   └── settings.py      # /settings
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── register.html
│   │   ├── dashboard.html
│   │   ├── product_add.html
│   │   ├── product_detail.html
│   │   ├── settings.html
│   │   └── admin.html
│   └── static/
│       └── style.css
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_scrapers.py
│   ├── test_notifier.py
│   └── test_routes.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Task 1: Project scaffold (requirements, Docker, .env)

**Files:**
- Create: `requirements.txt`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Crear requirements.txt**

```
fastapi==0.111.0
uvicorn==0.29.0
jinja2==3.1.4
python-multipart==0.0.9
sqlalchemy==2.0.30
aiosqlite==0.20.0
passlib[bcrypt]==1.7.4
python-jose[cryptography]==3.3.0
requests==2.32.2
beautifulsoup4==4.12.3
lxml==5.2.1
selenium==4.21.0
APScheduler==3.10.4
python-telegram-bot==21.2
pydantic-settings==2.2.1
```

- [ ] **Step 2: Crear Dockerfile**

```dockerfile
FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get install -y \
    gcc libffi-dev python3-dev \
    chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

VOLUME /data
EXPOSE 8766

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
```

- [ ] **Step 3: Crear docker-compose.yml**

```yaml
version: '3.8'
services:
  price-tracker:
    build: .
    container_name: price-tracker
    restart: unless-stopped
    ports:
      - "8766:8766"
    volumes:
      - price_tracker_data:/data
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - DATABASE_URL=${DATABASE_URL}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - CHECK_INTERVAL_HOURS=${CHECK_INTERVAL_HOURS}
      - FIRST_ADMIN_EMAIL=${FIRST_ADMIN_EMAIL}
      - FIRST_ADMIN_PASSWORD=${FIRST_ADMIN_PASSWORD}
volumes:
  price_tracker_data:
```

- [ ] **Step 4: Crear .env.example**

```env
SECRET_KEY=cambia-esto-por-una-clave-muy-larga-y-aleatoria
DATABASE_URL=sqlite+aiosqlite:////data/db.sqlite3
TELEGRAM_BOT_TOKEN=
CHECK_INTERVAL_HOURS=6
FIRST_ADMIN_EMAIL=tu@email.com
FIRST_ADMIN_PASSWORD=cambia-esto
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt Dockerfile docker-compose.yml .env.example
git commit -m "feat: add project scaffold (Docker, requirements)"
```

---

## Task 2: Config y base de datos

**Files:**
- Create: `app/config.py`
- Create: `app/database.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Crear app/config.py**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    secret_key: str
    database_url: str = "sqlite+aiosqlite:////data/db.sqlite3"
    telegram_bot_token: str = ""
    check_interval_hours: int = 6
    first_admin_email: str = ""
    first_admin_password: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 2: Crear app/database.py**

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with SessionLocal() as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        # WAL mode para lecturas concurrentes
        await conn.execute(__import__("sqlalchemy").text("PRAGMA journal_mode=WAL"))
        from app import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 3: Crear tests/conftest.py**

```python
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.database import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        from app import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

- [ ] **Step 4: Instalar pytest-asyncio y crear pytest.ini**

Añadir a `requirements.txt`:
```
pytest==8.2.0
pytest-asyncio==0.23.6
httpx==0.27.0
```

Crear `pytest.ini` en la raíz del proyecto:
```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/database.py tests/conftest.py requirements.txt
git commit -m "feat: add config, async database setup"
```

---

## Task 3: Modelos ORM

**Files:**
- Create: `app/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Escribir test que verifica la creación de modelos**

`tests/test_models.py`:
```python
import pytest
from datetime import datetime, timezone
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
    history = PriceHistory(product_id=product.id, price=9.99)
    db_session.add(history)
    await db_session.commit()

    await db_session.delete(product)
    await db_session.commit()

    result = await db_session.execute(select(PriceHistory).where(PriceHistory.product_id == product.id))
    assert result.scalar_one_or_none() is None
```

- [ ] **Step 2: Ejecutar test — verificar que falla**

```bash
cd /Users/mariofustesmontero/Documents/GitHub/price-tracker
pytest tests/test_models.py -v
```
Esperado: `ERROR` — `app.models` no existe aún.

- [ ] **Step 3: Crear app/models.py**

```python
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Boolean, ForeignKey, DateTime, Numeric, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    products: Mapped[list["Product"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    invite_codes_created: Mapped[list["InviteCode"]] = relationship(
        foreign_keys="InviteCode.created_by", back_populates="creator"
    )

class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    used_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped["User"] = relationship(foreign_keys=[created_by], back_populates="invite_codes_created")

class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_drop: Mapped[bool] = mapped_column(Boolean, default=False)
    target_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    target_percentage: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)

    user: Mapped["User"] = relationship(back_populates="products")
    price_history: Mapped[list["PriceHistory"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="PriceHistory.recorded_at"
    )

class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped["Product"] = relationship(back_populates="price_history")
```

- [ ] **Step 4: Ejecutar tests**

```bash
pytest tests/test_models.py -v
```
Esperado: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add SQLAlchemy ORM models with cascade delete"
```

---

## Task 4: Auth — hashing y JWT

**Files:**
- Create: `app/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Escribir tests**

`tests/test_auth.py`:
```python
import pytest
from app.auth import hash_password, verify_password, create_access_token, decode_access_token

def test_password_hash_and_verify():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed)
    assert not verify_password("wrong", hashed)

def test_token_roundtrip():
    token = create_access_token({"sub": "42"})
    payload = decode_access_token(token)
    assert payload["sub"] == "42"

def test_invalid_token_returns_none():
    result = decode_access_token("not.a.token")
    assert result is None
```

- [ ] **Step 2: Ejecutar — verificar que falla**

```bash
pytest tests/test_auth.py -v
```
Esperado: `ERROR` — módulo no existe.

- [ ] **Step 3: Crear app/auth.py**

```python
from datetime import datetime, timedelta, timezone
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
```

- [ ] **Step 4: Ejecutar tests**

```bash
pytest tests/test_auth.py -v
```
Esperado: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat: add password hashing and JWT auth"
```

---

## Task 5: Dependency get_current_user

**Files:**
- Create: `app/dependencies.py`

- [ ] **Step 1: Crear app/dependencies.py**

```python
from typing import Optional
from fastapi import Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.auth import decode_access_token
from app.database import get_db
from app.models import User

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == int(user_id)))
    return result.scalar_one_or_none()

async def require_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await get_current_user(request, db)
    if not user:
        # 302 con Location hace que el navegador redirija correctamente
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user

async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user
```

- [ ] **Step 2: Commit**

```bash
git add app/dependencies.py
git commit -m "feat: add auth dependencies (require_user, require_admin)"
```

---

## Task 6: Scrapers — base + scrapers dedicados

**Files:**
- Create: `app/scrapers/base.py`
- Create: `app/scrapers/__init__.py`
- Create: `app/scrapers/amazon.py`
- Create: `app/scrapers/ebay.py`
- Create: `app/scrapers/mediamarkt.py`
- Create: `app/scrapers/pccomponentes.py`
- Create: `app/scrapers/elcorteingles.py`
- Create: `tests/test_scrapers.py`

- [ ] **Step 1: Escribir tests de scrapers con mocks**

`tests/test_scrapers.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from app.scrapers.base import ScrapeResult
from app.scrapers import scraper_for

def test_scrape_result_dataclass():
    r = ScrapeResult(price=29.99, currency="EUR", name="Producto", image_url=None)
    assert r.price == 29.99

def test_scraper_for_amazon():
    from app.scrapers.amazon import scrape as amazon_scrape
    dispatcher = scraper_for("https://www.amazon.es/dp/B08N5WRWNW")
    assert dispatcher == amazon_scrape

def test_scraper_for_mediamarkt():
    from app.scrapers.mediamarkt import scrape as mm_scrape
    dispatcher = scraper_for("https://www.mediamarkt.es/es/product/_auriculares.html")
    assert dispatcher == mm_scrape

def test_scraper_for_pccomponentes():
    from app.scrapers.pccomponentes import scrape as pc_scrape
    dispatcher = scraper_for("https://www.pccomponentes.com/producto")
    assert dispatcher == pc_scrape

def test_scraper_for_unknown_uses_generic():
    from app.scrapers.generic import scrape as generic_scrape
    dispatcher = scraper_for("https://www.tiendarara.com/producto")
    assert dispatcher == generic_scrape

@patch("app.scrapers.amazon.requests.get")
def test_amazon_scraper_parses_price(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html>
    <span id="productTitle">Teclado Mecánico</span>
    <span class="a-price-whole">49</span>
    <span class="a-price-fraction">99</span>
    <img id="landingImage" src="https://img.example.com/teclado.jpg"/>
    </html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.amazon import scrape
    result = scrape("https://www.amazon.es/dp/XXXXX")
    assert result is not None
    assert result.price == 49.99
    assert "Teclado" in result.name
```

- [ ] **Step 2: Ejecutar — verificar que falla**

```bash
pytest tests/test_scrapers.py -v
```
Esperado: errores de importación.

- [ ] **Step 3: Crear app/scrapers/base.py**

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ScrapeResult:
    price: float
    currency: str
    name: str
    image_url: Optional[str]
```

- [ ] **Step 4: Crear app/scrapers/__init__.py**

```python
from urllib.parse import urlparse
from app.scrapers.base import ScrapeResult
from typing import Callable, Optional

def scraper_for(url: str) -> Callable:
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
    else:
        from app.scrapers.generic import scrape
    return scrape
```

- [ ] **Step 5: Crear app/scrapers/amazon.py**

```python
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}

def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        # Nombre
        title_el = soup.find(id="productTitle")
        name = title_el.get_text(strip=True) if title_el else "Producto Amazon"

        # Precio — formato "49" + "99" separados o ".a-offscreen"
        offscreen = soup.select_one(".a-offscreen")
        if offscreen:
            price_text = offscreen.get_text(strip=True)
            price = _parse_price(price_text)
        else:
            whole = soup.select_one(".a-price-whole")
            frac = soup.select_one(".a-price-fraction")
            if whole and frac:
                price_text = f"{whole.get_text(strip=True).replace('.','')},{frac.get_text(strip=True)}"
                price = _parse_price(price_text)
            else:
                return None

        # Imagen
        img = soup.find(id="landingImage")
        image_url = img.get("src") if img else None

        return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
    except Exception:
        return None

def _parse_price(text: str) -> float:
    # Elimina símbolo de moneda, convierte coma decimal europea
    clean = re.sub(r"[^\d,\.]", "", text)
    clean = clean.replace(".", "").replace(",", ".")
    return float(clean)
```

- [ ] **Step 6: Crear app/scrapers/mediamarkt.py**

```python
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}

def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        # JSON-LD primero
        result = _try_jsonld(soup, url)
        if result:
            return result

        # Selectores directos
        price_el = soup.select_one('[data-test="product-price"]') or soup.select_one(".price")
        name_el = soup.select_one("h1")
        if not price_el or not name_el:
            return None

        price = _parse_price(price_el.get_text())
        name = name_el.get_text(strip=True)
        img = soup.select_one("img.product-image, img[data-test='product-image']")
        image_url = img.get("src") if img else None
        return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
    except Exception:
        return None

def _try_jsonld(soup, url) -> Optional[ScrapeResult]:
    import json
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product":
                offers = data.get("offers", {})
                price = float(offers.get("price", 0))
                name = data.get("name", "Producto")
                image_url = data.get("image")
                if price:
                    return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
        except Exception:
            continue
    return None

def _parse_price(text: str) -> float:
    clean = re.sub(r"[^\d,\.]", "", text.strip())
    clean = clean.replace(".", "").replace(",", ".")
    return float(clean)
```

- [ ] **Step 7: Crear app/scrapers/pccomponentes.py**

```python
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult
from app.scrapers.mediamarkt import _try_jsonld, _parse_price

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}

def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        result = _try_jsonld(soup, url)
        if result:
            return result

        price_el = soup.select_one('[data-price], .price-container .price')
        name_el = soup.select_one("h1")
        if not price_el or not name_el:
            return None

        price = _parse_price(price_el.get("data-price") or price_el.get_text())
        name = name_el.get_text(strip=True)
        img = soup.select_one(".product-image img")
        image_url = img.get("src") if img else None
        return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
    except Exception:
        return None
```

- [ ] **Step 8: Crear app/scrapers/elcorteingles.py**

```python
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult
from app.scrapers.mediamarkt import _try_jsonld, _parse_price

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}

def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        result = _try_jsonld(soup, url)
        if result:
            return result

        price_el = soup.select_one(".price-sale, .sale-price, [itemprop='price']")
        name_el = soup.select_one("h1, [itemprop='name']")
        if not price_el or not name_el:
            return None

        price_str = price_el.get("content") or price_el.get_text()
        price = _parse_price(price_str)
        name = name_el.get_text(strip=True)
        img = soup.select_one("[itemprop='image'], .product-image img")
        image_url = img.get("src") or img.get("content") if img else None
        return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
    except Exception:
        return None
```

- [ ] **Step 9: Crear app/scrapers/ebay.py**

```python
import re
import requests
from typing import Optional
from app.scrapers.base import ScrapeResult
from app.scrapers.mediamarkt import _try_jsonld, _parse_price

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}

def scrape(url: str) -> Optional[ScrapeResult]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        result = _try_jsonld(soup, url)
        if result:
            return result

        price_el = soup.select_one("#prcIsum, .x-price-primary [itemprop='price']")
        name_el = soup.select_one("#itemTitle, h1.x-item-title__mainTitle")
        if not price_el or not name_el:
            return None

        price_str = price_el.get("content") or price_el.get_text()
        price = _parse_price(price_str)
        name = name_el.get_text(strip=True).replace("Detalles sobre  ", "")
        img = soup.select_one("#icImg, .ux-image-carousel-item img")
        image_url = img.get("src") if img else None
        return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
    except Exception:
        return None
```

- [ ] **Step 10: Ejecutar tests**

```bash
pytest tests/test_scrapers.py -v
```
Esperado: `5 passed` (los tests unitarios con mocks).

- [ ] **Step 11: Commit**

```bash
git add app/scrapers/ tests/test_scrapers.py
git commit -m "feat: add dedicated scrapers (Amazon, MediaMarkt, PCComponentes, ECI, eBay)"
```

---

## Task 7: Scraper genérico (BS4 + Selenium fallback)

**Files:**
- Create: `app/scrapers/generic.py`

- [ ] **Step 1: Añadir test para el scraper genérico con JSON-LD**

En `tests/test_scrapers.py`, añadir:
```python
@patch("app.scrapers.generic.requests.get")
def test_generic_scraper_jsonld(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Cámara Sony", "offers": {"price": "349.99"}, "image": "https://img.com/cam.jpg"}
    </script>
    </head><body></body></html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.generic import scrape
    result = scrape("https://www.tiendarara.com/producto")
    assert result is not None
    assert result.price == 349.99
    assert result.name == "Cámara Sony"

@patch("app.scrapers.generic.requests.get")
def test_generic_scraper_itemprop(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html><body>
    <h1 itemprop="name">Ratón gaming</h1>
    <span itemprop="price" content="29.99">29,99 €</span>
    </body></html>
    """
    mock_get.return_value = mock_response
    from app.scrapers.generic import scrape
    result = scrape("https://www.tiendarara.com/raton")
    assert result is not None
    assert result.price == 29.99
```

- [ ] **Step 2: Crear app/scrapers/generic.py**

```python
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
    """Nivel 1: requests + BS4. Nivel 2: Selenium si falla."""
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
    """Instancia Chromium headless, extrae precio y cierra inmediatamente."""
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
            time.sleep(3)  # Esperar JS
            soup = BeautifulSoup(driver.page_source, "lxml")
            return _extract(soup)
        finally:
            driver.quit()  # Siempre cerrar para liberar memoria
    except Exception:
        return None

def _extract(soup) -> Optional[ScrapeResult]:
    """Intenta extraer precio con varias estrategias."""
    # Estrategia 1: JSON-LD schema.org/Product
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

    # Estrategia 2: itemprop
    price_el = soup.find(attrs={"itemprop": "price"})
    name_el = soup.find(attrs={"itemprop": "name"}) or soup.find("h1")
    if price_el and name_el:
        price_raw = price_el.get("content") or price_el.get_text()
        try:
            price = _parse_price(price_raw)
            name = name_el.get_text(strip=True)
            img = soup.find(attrs={"itemprop": "image"})
            image_url = img.get("src") or img.get("content") if img else None
            return ScrapeResult(price=price, currency="EUR", name=name, image_url=image_url)
        except Exception:
            pass

    # Estrategia 3: Open Graph
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
    # Formato europeo: 1.234,56 → eliminar puntos como miles, coma como decimal
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    return float(clean)
```

- [ ] **Step 3: Ejecutar todos los tests de scrapers**

```bash
pytest tests/test_scrapers.py -v
```
Esperado: `7 passed`

- [ ] **Step 4: Commit**

```bash
git add app/scrapers/generic.py
git commit -m "feat: add generic scraper with BS4 + Selenium fallback"
```

---

## Task 8: Notifier y scheduler

**Files:**
- Create: `app/notifier.py`
- Create: `app/scheduler.py`
- Create: `tests/test_notifier.py`

- [ ] **Step 1: Escribir tests del notifier**

`tests/test_notifier.py`:
```python
import pytest
from unittest.mock import patch, AsyncMock
from app.notifier import format_notification, should_notify

def test_format_notification_drop():
    msg = format_notification(
        name="Teclado",
        old_price=89.99,
        new_price=74.99,
        url="https://amazon.es/dp/X"
    )
    assert "74,99" in msg
    assert "89,99" in msg
    assert "-16,7%" in msg or "16,7" in msg

def test_should_notify_on_drop():
    assert should_notify(old=100.0, new=90.0, notify_on_drop=True, target_price=None, target_pct=None)

def test_should_not_notify_when_disabled():
    assert not should_notify(old=100.0, new=90.0, notify_on_drop=False, target_price=None, target_pct=None)

def test_should_notify_target_price():
    assert should_notify(old=60.0, new=49.0, notify_on_drop=False, target_price=50.0, target_pct=None)

def test_should_notify_target_percentage():
    # Baja 15%, umbral 10%
    assert should_notify(old=100.0, new=85.0, notify_on_drop=False, target_price=None, target_pct=10.0)

def test_should_not_notify_when_price_rises():
    assert not should_notify(old=80.0, new=90.0, notify_on_drop=True, target_price=None, target_pct=None)
```

- [ ] **Step 2: Ejecutar — verificar que falla**

```bash
pytest tests/test_notifier.py -v
```

- [ ] **Step 3: Crear app/notifier.py**

```python
from typing import Optional
import requests as req
from app.config import settings

def should_notify(
    old: float,
    new: float,
    notify_on_drop: bool,
    target_price: Optional[float],
    target_pct: Optional[float],
) -> bool:
    if new >= old:
        return False
    if notify_on_drop:
        return True
    if target_price is not None and new <= target_price:
        return True
    if target_pct is not None:
        drop_pct = ((old - new) / old) * 100
        if drop_pct >= target_pct:
            return True
    return False

def format_notification(name: str, old_price: float, new_price: float, url: str) -> str:
    drop_pct = ((old_price - new_price) / old_price) * 100
    return (
        f"📉 {name}\n"
        f"Precio anterior: {old_price:,.2f} €\n"
        f"Precio actual:   {new_price:,.2f} € (-{drop_pct:.1f}%)\n"
        f"🔗 {url}"
    ).replace(",", "X").replace(".", ",").replace("X", ".")

def send_telegram_message(chat_id: str, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    try:
        req.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass
```

- [ ] **Step 4: Ejecutar tests**

```bash
pytest tests/test_notifier.py -v
```
Esperado: `6 passed`

- [ ] **Step 5: Crear app/scheduler.py**

```python
import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.config import settings

logger = logging.getLogger(__name__)

def check_all_products():
    """Job síncrono que corre en el hilo del scheduler."""
    asyncio.run(_check_all_products_async())

async def _check_all_products_async():
    from app.database import SessionLocal
    from app.models import Product, PriceHistory
    from app.scrapers import scraper_for
    from app.notifier import should_notify, format_notification, send_telegram_message
    from sqlalchemy import select

    async with SessionLocal() as db:
        result = await db.execute(select(Product).where(Product.is_active == True))
        products = result.scalars().all()

    # Separar productos que necesitan Selenium (genérico) de los que no
    from urllib.parse import urlparse
    known_domains = {"amazon.es", "amazon.com", "mediamarkt.es", "pccomponentes.com",
                     "elcorteingles.es", "ebay.es", "ebay.com"}

    standard = [p for p in products if any(d in urlparse(p.url).netloc for d in known_domains)]
    generic = [p for p in products if p not in standard]

    for product in standard + generic:
        await _scrape_product(product)

async def _scrape_product(product):
    from app.database import SessionLocal
    from app.models import Product, PriceHistory
    from app.scrapers import scraper_for
    from app.notifier import should_notify, format_notification, send_telegram_message
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    scrape_fn = scraper_for(product.url)

    for attempt in range(2):
        try:
            result = scrape_fn(product.url)
            break
        except Exception as e:
            logger.warning(f"Intento {attempt+1} fallido para {product.url}: {e}")
            result = None

    if not result:
        logger.error(f"No se pudo obtener precio de {product.url}")
        return

    async with SessionLocal() as db:
        prod = await db.get(Product, product.id)
        if not prod:
            return

        old_price = float(prod.current_price) if prod.current_price else None
        new_price = result.price

        # Guardar en histórico
        history = PriceHistory(product_id=prod.id, price=new_price)
        db.add(history)

        # Actualizar precio actual
        prod.current_price = new_price
        prod.last_checked_at = datetime.now(timezone.utc)
        if result.name and prod.name == "Producto":
            prod.name = result.name
        if result.image_url and not prod.image_url:
            prod.image_url = result.image_url

        await db.commit()

        # Notificar si aplica
        if old_price and should_notify(
            old=old_price, new=new_price,
            notify_on_drop=prod.notify_on_drop,
            target_price=float(prod.target_price) if prod.target_price else None,
            target_pct=float(prod.target_percentage) if prod.target_percentage else None,
        ):
            # Obtener chat_id del usuario
            from app.models import User
            user = await db.get(User, prod.user_id)
            if user and user.telegram_chat_id:
                msg = format_notification(prod.name, old_price, new_price, prod.url)
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
```

- [ ] **Step 6: Commit**

```bash
git add app/notifier.py app/scheduler.py tests/test_notifier.py
git commit -m "feat: add price notifier logic and APScheduler job"
```

---

## Task 9: Bot de Telegram

**Files:**
- Create: `app/bot.py`

- [ ] **Step 1: Crear app/bot.py**

```python
import asyncio
import logging
import threading
from typing import Optional
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from app.config import settings

logger = logging.getLogger(__name__)

# ── helpers de BD (síncronos via asyncio.run en el hilo del bot) ──────────────

async def _get_user_by_chat_id(chat_id: str):
    from app.database import SessionLocal
    from app.models import User
    from sqlalchemy import select
    async with SessionLocal() as db:
        r = await db.execute(select(User).where(User.telegram_chat_id == str(chat_id)))
        return r.scalar_one_or_none()

async def _link_user(code: str, chat_id: str) -> Optional[str]:
    """Vincula chat_id a la cuenta con ese invite_code ya usado. Devuelve username o None."""
    from app.database import SessionLocal
    from app.models import InviteCode, User
    from sqlalchemy import select
    async with SessionLocal() as db:
        # Buscar usuario que usó este código (invite_code.used_by)
        # Alternativa: el usuario se registra en web y luego vincula bot con /start <code>
        # Usamos un token de vinculación: el usuario genera un token en /settings
        # Por simplicidad, el código de invitación también sirve para vinculación inicial
        ic = await db.execute(select(InviteCode).where(InviteCode.code == code))
        invite = ic.scalar_one_or_none()
        if not invite or not invite.used_by:
            return None
        user = await db.get(User, invite.used_by)
        if not user:
            return None
        if user.telegram_chat_id:
            return None  # Ya vinculado
        user.telegram_chat_id = str(chat_id)
        await db.commit()
        return user.username

async def _get_products(chat_id: str) -> list:
    from app.database import SessionLocal
    from app.models import Product
    from sqlalchemy import select
    user = await _get_user_by_chat_id(chat_id)
    if not user:
        return []
    async with SessionLocal() as db:
        r = await db.execute(
            select(Product).where(Product.user_id == user.id, Product.is_active == True)
        )
        return r.scalars().all()

async def _add_product(chat_id: str, url: str) -> Optional[str]:
    """Añade producto y devuelve nombre o None si falla."""
    user = await _get_user_by_chat_id(chat_id)
    if not user:
        return None
    from app.database import SessionLocal
    from app.models import Product
    from app.scrapers import scraper_for
    from sqlalchemy import select, func
    async with SessionLocal() as db:
        count_r = await db.execute(
            select(func.count()).where(Product.user_id == user.id, Product.is_active == True)
        )
        if count_r.scalar() >= 100:
            return "LIMIT"
        scrape_fn = scraper_for(url)
        result = scrape_fn(url)
        name = result.name if result else "Producto"
        price = result.price if result else None
        image = result.image_url if result else None
        product = Product(user_id=user.id, url=url, name=name, current_price=price, image_url=image)
        db.add(product)
        await db.commit()
        return name

async def _delete_product(chat_id: str, product_id: int) -> bool:
    user = await _get_user_by_chat_id(chat_id)
    if not user:
        return False
    from app.database import SessionLocal
    from app.models import Product
    async with SessionLocal() as db:
        product = await db.get(Product, product_id)
        if not product or product.user_id != user.id:
            return False
        await db.delete(product)
        await db.commit()
        return True

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = await _get_user_by_chat_id(chat_id)
    if user:
        await update.message.reply_text(f"Ya estás registrado como {user.username}. Usa /ayuda.")
        return
    await update.message.reply_text(
        "Bienvenido al bot de Price Tracker.\n\n"
        "Para vincular tu cuenta, ve a la web → Ajustes → Copiar código de vinculación "
        "y envíamelo aquí."
    )

async def handle_link_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cualquier mensaje de texto que no sea comando → intenta vincular como código."""
    chat_id = str(update.effective_chat.id)
    user = await _get_user_by_chat_id(chat_id)
    if user:
        return  # Ya vinculado, ignorar
    code = update.message.text.strip()
    username = await _link_user(code, chat_id)
    if username:
        await update.message.reply_text(f"Cuenta vinculada como {username}. Usa /ayuda para ver los comandos.")
    else:
        await update.message.reply_text("Código no válido. Genera uno nuevo en la web → Ajustes.")

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    products = await _get_products(chat_id)
    if not products:
        await update.message.reply_text("No tienes productos trackeados.")
        return
    lines = []
    for p in products:
        price_str = f"{float(p.current_price):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".") if p.current_price else "Sin datos"
        lines.append(f"[{p.id}] {p.name} — {price_str}")
    await update.message.reply_text("\n".join(lines))

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /add <url>")
        return
    url = context.args[0]
    await update.message.reply_text("Obteniendo precio, espera...")
    result = await _add_product(chat_id, url)
    if result == "LIMIT":
        await update.message.reply_text("Has alcanzado el límite de 100 productos.")
    elif result:
        await update.message.reply_text(f"Producto añadido: {result}")
    else:
        await update.message.reply_text("No se pudo añadir el producto. ¿Estás registrado?")

async def precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /precio <id>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID debe ser un número.")
        return
    products = await _get_products(chat_id)
    product = next((p for p in products if p.id == pid), None)
    if not product:
        await update.message.reply_text("Producto no encontrado.")
        return
    price_str = f"{float(product.current_price):,.2f} €" if product.current_price else "Sin datos"
    await update.message.reply_text(f"{product.name}\nPrecio actual: {price_str}\n{product.url}")

async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /borrar <id>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID debe ser un número.")
        return
    ok = await _delete_product(chat_id, pid)
    await update.message.reply_text("Producto eliminado." if ok else "No se pudo eliminar.")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/lista — Ver todos tus productos\n"
        "/add <url> — Añadir producto\n"
        "/precio <id> — Ver precio actual\n"
        "/borrar <id> — Eliminar producto\n"
        "/ayuda — Esta ayuda"
    )

# ── Runner ────────────────────────────────────────────────────────────────────

def run_bot():
    """Arranca el bot en su propio event loop (llamar desde un hilo)."""
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN no configurado, bot desactivado.")
        return

    async def _run():
        app = Application.builder().token(settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("lista", lista))
        app.add_handler(CommandHandler("add", add))
        app.add_handler(CommandHandler("precio", precio))
        app.add_handler(CommandHandler("borrar", borrar))
        app.add_handler(CommandHandler("ayuda", ayuda))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link_code))
        await app.run_polling(close_loop=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())

def start_bot_thread():
    thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    thread.start()
```

- [ ] **Step 2: Commit**

```bash
git add app/bot.py
git commit -m "feat: add Telegram bot with polling and all commands"
```

---

## Task 10: Routers — Auth (login, register, logout)

**Files:**
- Create: `app/routers/auth.py`

- [ ] **Step 1: Crear app/routers/auth.py**

```python
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, InviteCode
from app.auth import hash_password, verify_password, create_access_token
from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Email o contraseña incorrectos"}
        )
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=30 * 86400)
    return response

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, code: str = ""):
    return templates.TemplateResponse("register.html", {"request": request, "code": code})

@router.post("/register")
async def register(
    request: Request,
    code: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Validar código de invitación
    ic_result = await db.execute(
        select(InviteCode).where(InviteCode.code == code, InviteCode.used_by == None)
    )
    invite = ic_result.scalar_one_or_none()
    if not invite:
        return templates.TemplateResponse(
            "register.html", {"request": request, "code": code, "error": "Código inválido o ya usado"}
        )

    # Verificar unicidad
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            "register.html", {"request": request, "code": code, "error": "Email ya registrado"}
        )

    user = User(email=email, username=username, password_hash=hash_password(password))
    db.add(user)
    await db.flush()

    invite.used_by = user.id
    invite.used_at = datetime.now(timezone.utc)
    await db.commit()

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=30 * 86400)
    return response

@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response
```

- [ ] **Step 2: Commit**

```bash
git add app/routers/auth.py
git commit -m "feat: add auth routes (login, register with invite, logout)"
```

---

## Task 11: Routers — Products

**Files:**
- Create: `app/routers/products.py`

- [ ] **Step 1: Crear app/routers/products.py**

```python
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import User, Product, PriceHistory
from app.dependencies import require_user
from app.scrapers import scraper_for

router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_PRODUCTS = 100

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product)
        .where(Product.user_id == user.id, Product.is_active == True)
        .options(selectinload(Product.price_history))
        .order_by(Product.created_at.desc())
    )
    products = result.scalars().all()

    # Calcular variación respecto al precio anterior (penúltimo registro)
    products_data = []
    for p in products:
        history = sorted(p.price_history, key=lambda h: h.recorded_at)
        prev_price = float(history[-2].price) if len(history) >= 2 else None
        curr_price = float(p.current_price) if p.current_price else None
        change = None
        if prev_price and curr_price:
            change = ((curr_price - prev_price) / prev_price) * 100
        products_data.append({"product": p, "prev_price": prev_price, "change": change})

    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "products_data": products_data}
    )

@router.get("/product/add", response_class=HTMLResponse)
async def add_product_page(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse("product_add.html", {"request": request, "user": user})

@router.post("/product/add")
async def add_product(
    request: Request,
    url: str = Form(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Verificar límite
    count_r = await db.execute(
        select(func.count()).where(Product.user_id == user.id, Product.is_active == True)
    )
    if count_r.scalar() >= MAX_PRODUCTS:
        return templates.TemplateResponse(
            "product_add.html",
            {"request": request, "user": user, "error": f"Límite de {MAX_PRODUCTS} productos alcanzado"},
        )

    # Scrape inicial para obtener metadatos
    scrape_fn = scraper_for(url)
    result = scrape_fn(url)
    name = result.name if result else "Producto"
    price = result.price if result else None
    image = result.image_url if result else None

    product = Product(user_id=user.id, url=url, name=name, current_price=price, image_url=image)
    db.add(product)
    await db.flush()

    if price:
        history = PriceHistory(product_id=product.id, price=price)
        db.add(history)

    await db.commit()
    return RedirectResponse(url="/", status_code=302)

@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(
    product_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id, Product.user_id == user.id)
        .options(selectinload(Product.price_history))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404)

    history = sorted(product.price_history, key=lambda h: h.recorded_at)
    chart_labels = [h.recorded_at.strftime("%d/%m %H:%M") for h in history]
    chart_prices = [float(h.price) for h in history]

    return templates.TemplateResponse(
        "product_detail.html",
        {
            "request": request,
            "user": user,
            "product": product,
            "chart_labels": chart_labels,
            "chart_prices": chart_prices,
        },
    )

@router.post("/product/{product_id}/alerts")
async def update_alerts(
    product_id: int,
    notify_on_drop: bool = Form(False),
    target_price: str = Form(""),
    target_percentage: str = Form(""),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user.id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404)

    product.notify_on_drop = notify_on_drop
    product.target_price = float(target_price.replace(",", ".")) if target_price.strip() else None
    product.target_percentage = float(target_percentage.replace(",", ".")) if target_percentage.strip() else None
    await db.commit()
    return RedirectResponse(url=f"/product/{product_id}", status_code=302)

@router.post("/product/{product_id}/delete")
async def delete_product(
    product_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user.id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404)
    await db.delete(product)
    await db.commit()
    return RedirectResponse(url="/", status_code=302)
```

- [ ] **Step 2: Commit**

```bash
git add app/routers/products.py
git commit -m "feat: add product routes (dashboard, add, detail, alerts, delete)"
```

---

## Task 12: Routers — Admin y Settings

**Files:**
- Create: `app/routers/admin.py`
- Create: `app/routers/settings.py`

- [ ] **Step 1: Crear app/routers/admin.py**

```python
import uuid
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, InviteCode
from app.dependencies import require_admin

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    users_r = await db.execute(select(User).order_by(User.created_at.desc()))
    users = users_r.scalars().all()

    codes_r = await db.execute(
        select(InviteCode).order_by(InviteCode.created_at.desc()).limit(20)
    )
    codes = codes_r.scalars().all()

    return templates.TemplateResponse(
        "admin.html", {"request": request, "user": admin, "users": users, "codes": codes}
    )

@router.post("/admin/invite")
async def create_invite(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import RedirectResponse
    code = InviteCode(code=str(uuid.uuid4()), created_by=admin.id)
    db.add(code)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)
```

- [ ] **Step 2: Crear app/routers/settings.py**

```python
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import User
from app.dependencies import require_user
from app.auth import hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})

@router.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        return templates.TemplateResponse(
            "settings.html",
            {"request": request, "user": user, "error": "Contraseña actual incorrecta"},
        )
    user_obj = await db.get(User, user.id)
    user_obj.password_hash = hash_password(new_password)
    await db.commit()
    return templates.TemplateResponse(
        "settings.html", {"request": request, "user": user, "success": "Contraseña actualizada"}
    )
```

- [ ] **Step 3: Commit**

```bash
git add app/routers/admin.py app/routers/settings.py
git commit -m "feat: add admin (invite codes) and settings routes"
```

---

## Task 13: main.py — app factory y lifespan

**Files:**
- Create: `app/main.py`

- [ ] **Step 1: Crear app/main.py**

```python
import uuid
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database import init_db, SessionLocal
from app.scheduler import create_scheduler
from app.bot import start_bot_thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializar BD
    await init_db()
    await _create_first_admin()

    # Arrancar scheduler
    scheduler = create_scheduler()
    scheduler.start()

    # Arrancar bot de Telegram
    start_bot_thread()

    logger.info("Price Tracker arrancado. Puerto 8766.")
    yield

    scheduler.shutdown(wait=False)

async def _create_first_admin():
    """Crea el usuario admin inicial si no existe ningún usuario."""
    if not settings.first_admin_email or not settings.first_admin_password:
        return
    from sqlalchemy import select
    from app.models import User, InviteCode
    from app.auth import hash_password
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.email == settings.first_admin_email))
        if result.scalar_one_or_none():
            return
        admin = User(
            email=settings.first_admin_email,
            username="admin",
            password_hash=hash_password(settings.first_admin_password),
            is_admin=True,
        )
        db.add(admin)
        await db.flush()
        # Crear un código de invitación inicial para el admin
        code = InviteCode(code=str(uuid.uuid4()), created_by=admin.id, used_by=admin.id)
        db.add(code)
        await db.commit()
        logger.info(f"Admin creado: {settings.first_admin_email}")

app = FastAPI(lifespan=lifespan, title="Price Tracker")

app.mount("/static", StaticFiles(directory="static"), name="static")

from app.routers import auth, products, admin, settings as settings_router
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(admin.router)
app.include_router(settings_router.router)
```

- [ ] **Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat: add FastAPI app factory with lifespan (scheduler + bot + DB init)"
```

---

## Task 14: Templates HTML

**Files:**
- Create: `app/templates/base.html`
- Create: `app/templates/login.html`
- Create: `app/templates/register.html`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/product_add.html`
- Create: `app/templates/product_detail.html`
- Create: `app/templates/settings.html`
- Create: `app/templates/admin.html`
- Create: `app/static/style.css`

- [ ] **Step 1: Crear app/templates/base.html**

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Price Tracker{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
  {% block extra_head %}{% endblock %}
</head>
<body>
  {% if user %}
  <nav>
    <a href="/" class="nav-brand">Price Tracker</a>
    <div class="nav-links">
      <a href="/">Dashboard</a>
      <a href="/product/add">+ Añadir</a>
      <a href="/settings">Ajustes</a>
      {% if user.is_admin %}<a href="/admin">Admin</a>{% endif %}
      <form method="post" action="/logout" style="display:inline">
        <button type="submit" class="btn-link">Salir</button>
      </form>
    </div>
  </nav>
  {% endif %}
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 2: Crear app/templates/login.html**

```html
{% extends "base.html" %}
{% block title %}Login — Price Tracker{% endblock %}
{% block content %}
<div class="form-card">
  <h1>Iniciar sesión</h1>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post" action="/login">
    <label>Email<input type="email" name="email" required autofocus></label>
    <label>Contraseña<input type="password" name="password" required></label>
    <button type="submit">Entrar</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 3: Crear app/templates/register.html**

```html
{% extends "base.html" %}
{% block title %}Registro — Price Tracker{% endblock %}
{% block content %}
<div class="form-card">
  <h1>Crear cuenta</h1>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post" action="/register">
    <input type="hidden" name="code" value="{{ code }}">
    <label>Código de invitación<input type="text" name="code" value="{{ code }}" required></label>
    <label>Email<input type="email" name="email" required></label>
    <label>Nombre de usuario<input type="text" name="username" required></label>
    <label>Contraseña<input type="password" name="password" required minlength="8"></label>
    <button type="submit">Registrarse</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4: Crear app/templates/dashboard.html**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Price Tracker{% endblock %}
{% block content %}
<div class="page-header">
  <h1>Mis productos</h1>
  <a href="/product/add" class="btn">+ Añadir producto</a>
</div>

{% if not products_data %}
  <p class="empty">No tienes productos. <a href="/product/add">Añade uno</a>.</p>
{% else %}
<div class="product-grid">
  {% for item in products_data %}
  {% set p = item.product %}
  <div class="product-card">
    {% if p.image_url %}
    <img src="{{ p.image_url }}" alt="{{ p.name }}" class="product-thumb">
    {% endif %}
    <div class="product-info">
      <a href="/product/{{ p.id }}"><h2>{{ p.name }}</h2></a>
      <p class="price">
        {% if p.current_price %}
          {{ "%.2f"|format(p.current_price|float) }} €
          {% if item.change is not none %}
            {% if item.change < 0 %}
              <span class="badge down">▼ {{ "%.1f"|format(item.change|abs) }}%</span>
            {% elif item.change > 0 %}
              <span class="badge up">▲ {{ "%.1f"|format(item.change) }}%</span>
            {% endif %}
          {% endif %}
        {% else %}
          Sin datos aún
        {% endif %}
      </p>
      <p class="url"><a href="{{ p.url }}" target="_blank">Ver en tienda</a></p>
    </div>
    <form method="post" action="/product/{{ p.id }}/delete" onsubmit="return confirm('¿Eliminar producto?')">
      <button type="submit" class="btn-danger-sm">✕</button>
    </form>
  </div>
  {% endfor %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Crear app/templates/product_add.html**

```html
{% extends "base.html" %}
{% block title %}Añadir producto — Price Tracker{% endblock %}
{% block content %}
<div class="form-card">
  <h1>Añadir producto</h1>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <p class="hint">Pega la URL del producto. La app extraerá el nombre e imagen automáticamente.</p>
  <form method="post" action="/product/add">
    <label>URL del producto
      <input type="url" name="url" required autofocus placeholder="https://www.amazon.es/dp/...">
    </label>
    <button type="submit">Añadir y obtener precio</button>
  </form>
  <a href="/">← Volver al dashboard</a>
</div>
{% endblock %}
```

- [ ] **Step 6: Crear app/templates/product_detail.html**

```html
{% extends "base.html" %}
{% block title %}{{ product.name }} — Price Tracker{% endblock %}
{% block extra_head %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
{% endblock %}
{% block content %}
<div class="detail-header">
  {% if product.image_url %}
  <img src="{{ product.image_url }}" alt="{{ product.name }}" class="detail-thumb">
  {% endif %}
  <div>
    <h1>{{ product.name }}</h1>
    <p class="price-big">
      {% if product.current_price %}{{ "%.2f"|format(product.current_price|float) }} €{% else %}Sin datos{% endif %}
    </p>
    <a href="{{ product.url }}" target="_blank" class="btn">Ver en tienda</a>
    <form method="post" action="/product/{{ product.id }}/delete"
          onsubmit="return confirm('¿Eliminar producto y todo su histórico?')" style="display:inline">
      <button type="submit" class="btn-danger">Eliminar</button>
    </form>
  </div>
</div>

<section class="chart-section">
  <h2>Histórico de precios</h2>
  {% if chart_prices %}
  <canvas id="priceChart" height="100"></canvas>
  <script>
    new Chart(document.getElementById('priceChart'), {
      type: 'line',
      data: {
        labels: {{ chart_labels | tojson }},
        datasets: [{
          label: 'Precio (€)',
          data: {{ chart_prices | tojson }},
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          tension: 0.3,
          fill: true,
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } } }
    });
  </script>
  {% else %}
  <p>Sin histórico aún.</p>
  {% endif %}
</section>

<section class="alerts-section">
  <h2>Alertas</h2>
  <form method="post" action="/product/{{ product.id }}/alerts">
    <label class="checkbox-label">
      <input type="checkbox" name="notify_on_drop" value="true"
             {% if product.notify_on_drop %}checked{% endif %}>
      Notificar en cualquier bajada de precio
    </label>
    <label>Precio objetivo (€) — avisa si baja de este precio
      <input type="number" name="target_price" step="0.01" min="0"
             value="{{ product.target_price if product.target_price else '' }}"
             placeholder="ej: 49.99">
    </label>
    <label>Bajada mínima (%) — avisa si baja este porcentaje
      <input type="number" name="target_percentage" step="0.1" min="0" max="100"
             value="{{ product.target_percentage if product.target_percentage else '' }}"
             placeholder="ej: 10">
    </label>
    <button type="submit">Guardar alertas</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 7: Crear app/templates/settings.html**

```html
{% extends "base.html" %}
{% block title %}Ajustes — Price Tracker{% endblock %}
{% block content %}
<h1>Ajustes</h1>

{% if error %}<p class="error">{{ error }}</p>{% endif %}
{% if success %}<p class="success">{{ success }}</p>{% endif %}

<section class="settings-section">
  <h2>Telegram</h2>
  {% if user.telegram_chat_id %}
    <p class="success">Cuenta de Telegram vinculada.</p>
  {% else %}
    <p>Para vincular tu cuenta de Telegram:</p>
    <ol>
      <li>Abre el bot en Telegram</li>
      <li>Envía <code>/start</code></li>
      <li>Cuando te pida el código, envía el código de invitación con el que te registraste</li>
    </ol>
  {% endif %}
</section>

<section class="settings-section">
  <h2>Cambiar contraseña</h2>
  <form method="post" action="/settings/password">
    <label>Contraseña actual<input type="password" name="current_password" required></label>
    <label>Nueva contraseña<input type="password" name="new_password" required minlength="8"></label>
    <button type="submit">Cambiar contraseña</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 8: Crear app/templates/admin.html**

```html
{% extends "base.html" %}
{% block title %}Admin — Price Tracker{% endblock %}
{% block content %}
<h1>Panel de administración</h1>

<section class="admin-section">
  <h2>Generar código de invitación</h2>
  <form method="post" action="/admin/invite">
    <button type="submit" class="btn">Generar código</button>
  </form>

  <h3>Códigos recientes</h3>
  <table>
    <thead><tr><th>Código</th><th>Creado</th><th>Usado por</th></tr></thead>
    <tbody>
    {% for code in codes %}
    <tr>
      <td><code>{{ code.code }}</code></td>
      <td>{{ code.created_at.strftime('%d/%m/%Y %H:%M') }}</td>
      <td>{% if code.used_by %}Usuario #{{ code.used_by }}{% else %}—{% endif %}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</section>

<section class="admin-section">
  <h2>Usuarios ({{ users|length }})</h2>
  <table>
    <thead><tr><th>ID</th><th>Email</th><th>Usuario</th><th>Admin</th><th>Telegram</th><th>Registro</th></tr></thead>
    <tbody>
    {% for u in users %}
    <tr>
      <td>{{ u.id }}</td>
      <td>{{ u.email }}</td>
      <td>{{ u.username }}</td>
      <td>{% if u.is_admin %}✓{% endif %}</td>
      <td>{% if u.telegram_chat_id %}✓{% endif %}</td>
      <td>{{ u.created_at.strftime('%d/%m/%Y') }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

- [ ] **Step 9: Crear app/static/style.css**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body { font-family: system-ui, sans-serif; background: #f8fafc; color: #1e293b; line-height: 1.5; }

nav {
  display: flex; justify-content: space-between; align-items: center;
  padding: .75rem 1.5rem; background: #1e293b; color: white;
}
nav a, .nav-brand { color: white; text-decoration: none; margin-left: 1rem; }
.nav-brand { font-weight: bold; font-size: 1.1rem; margin-left: 0; }
.btn-link { background: none; border: none; color: white; cursor: pointer; font-size: 1rem; }

main { max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }

.form-card {
  max-width: 420px; margin: 3rem auto; background: white;
  padding: 2rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
}
.form-card h1 { margin-bottom: 1.5rem; font-size: 1.4rem; }
label { display: block; margin-bottom: 1rem; font-size: .9rem; font-weight: 500; }
label input { display: block; width: 100%; margin-top: .25rem; padding: .5rem .75rem;
  border: 1px solid #cbd5e1; border-radius: 6px; font-size: 1rem; }
.checkbox-label { display: flex; align-items: center; gap: .5rem; font-weight: normal; }
.checkbox-label input { width: auto; margin: 0; }

button[type="submit"], .btn {
  display: inline-block; padding: .55rem 1.2rem; background: #3b82f6; color: white;
  border: none; border-radius: 6px; font-size: .95rem; cursor: pointer; text-decoration: none;
  margin-top: .5rem;
}
button[type="submit"]:hover, .btn:hover { background: #2563eb; }
.btn-danger { background: #ef4444; }
.btn-danger:hover { background: #dc2626; }
.btn-danger-sm { background: #ef4444; color: white; border: none; border-radius: 4px;
  padding: .2rem .5rem; cursor: pointer; font-size: .8rem; }

.error { color: #dc2626; background: #fee2e2; padding: .5rem .75rem; border-radius: 6px; margin-bottom: 1rem; }
.success { color: #16a34a; background: #dcfce7; padding: .5rem .75rem; border-radius: 6px; margin-bottom: 1rem; }
.hint { color: #64748b; margin-bottom: 1rem; font-size: .9rem; }

.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; }
.page-header h1 { font-size: 1.6rem; }
.empty { color: #64748b; }

.product-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }
.product-card {
  background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
  padding: 1rem; display: flex; gap: .75rem; position: relative;
}
.product-thumb { width: 70px; height: 70px; object-fit: contain; border-radius: 4px; }
.product-info { flex: 1; }
.product-info h2 { font-size: .95rem; margin-bottom: .25rem; }
.product-info h2 a { text-decoration: none; color: #1e293b; }
.price { font-size: 1.1rem; font-weight: bold; color: #1e293b; }
.url a { font-size: .8rem; color: #3b82f6; }
.badge { font-size: .75rem; font-weight: 600; padding: .1rem .35rem; border-radius: 4px; margin-left: .25rem; }
.badge.down { background: #dcfce7; color: #16a34a; }
.badge.up { background: #fee2e2; color: #dc2626; }

.detail-header { display: flex; gap: 1.5rem; align-items: flex-start; margin-bottom: 2rem;
  background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.detail-thumb { width: 120px; height: 120px; object-fit: contain; }
.price-big { font-size: 2rem; font-weight: bold; color: #1e293b; margin: .5rem 0; }

.chart-section, .alerts-section, .settings-section, .admin-section {
  background: white; padding: 1.5rem; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 1.5rem;
}
.chart-section h2, .alerts-section h2 { margin-bottom: 1rem; }
.alerts-section label { margin-bottom: .75rem; }

table { width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: .9rem; }
th, td { text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #e2e8f0; }
th { background: #f1f5f9; font-weight: 600; }
code { background: #f1f5f9; padding: .1rem .35rem; border-radius: 4px; font-size: .85rem; }
```

- [ ] **Step 10: Commit**

```bash
git add app/templates/ app/static/
git commit -m "feat: add Jinja2 templates and CSS styles"
```

---

## Task 15: Tests de integración de rutas

**Files:**
- Create: `tests/test_routes.py`

- [ ] **Step 1: Crear tests/test_routes.py**

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import Base, get_db
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture(scope="module")
async def test_app():
    engine = create_async_engine(TEST_DB_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        from app import models  # noqa
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, session_factory

    app.dependency_overrides.clear()
    await engine.dispose()

@pytest.mark.asyncio
async def test_login_page_returns_200(test_app):
    client, _ = test_app
    resp = await client.get("/login")
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_login_invalid_credentials(test_app):
    client, _ = test_app
    resp = await client.post("/login", data={"email": "nope@a.com", "password": "bad"})
    assert resp.status_code == 200
    assert "incorrectos" in resp.text

@pytest.mark.asyncio
async def test_dashboard_redirects_unauthenticated(test_app):
    client, _ = test_app
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)

@pytest.mark.asyncio
async def test_full_register_and_login_flow(test_app):
    client, session_factory = test_app
    import uuid
    from app.models import User, InviteCode
    from app.auth import hash_password

    # Crear admin y código de invitación directamente en BD
    async with session_factory() as db:
        admin = User(email="admin@test.com", username="admin", password_hash=hash_password("adminpass"), is_admin=True)
        db.add(admin)
        await db.flush()
        code = InviteCode(code=str(uuid.uuid4()), created_by=admin.id)
        db.add(code)
        await db.commit()
        invite_code = code.code

    # Registrar nuevo usuario con el código
    resp = await client.post("/register", data={
        "code": invite_code, "email": "user@test.com",
        "username": "testuser", "password": "testpass123"
    }, follow_redirects=False)
    assert resp.status_code == 302

    # Login
    resp = await client.post("/login", data={"email": "user@test.com", "password": "testpass123"},
                             follow_redirects=False)
    assert resp.status_code == 302
    assert "access_token" in resp.cookies
```

- [ ] **Step 2: Ejecutar todos los tests**

```bash
pytest tests/ -v
```
Esperado: todos los tests pasan.

- [ ] **Step 3: Commit**

```bash
git add tests/test_routes.py
git commit -m "test: add route integration tests"
```

---

## Task 16: Crear repositorio en GitHub y push

- [ ] **Step 1: Crear repo en GitHub**

```bash
cd /Users/mariofustesmontero/Documents/GitHub/price-tracker
gh repo create price-tracker --public --description "Price tracker web app con Telegram bot — desplegado en Raspberry Pi" --source=. --push
```

- [ ] **Step 2: Verificar que el repo existe y tiene todos los commits**

```bash
git log --oneline
gh repo view
```

- [ ] **Step 3: Crear documentación en Obsidian**

Crear el fichero `/Users/mariofustesmontero/Documents/Obsidian Vault/Raspberry - Price Tracker.md` con:
- URL del repo GitHub
- Puerto: 8766
- URL local: `http://192.168.1.50:8766`
- Variables de entorno necesarias
- Pasos para desplegar en Portainer
- Comandos de mantenimiento

---

## Task 17: Despliegue en Portainer (verificación final)

- [ ] **Step 1: Verificar que el Dockerfile construye en local (si tienes Docker)**

```bash
cd /Users/mariofustesmontero/Documents/GitHub/price-tracker
docker build -t price-tracker-test . 2>&1 | tail -5
```

- [ ] **Step 2: Desplegar desde Portainer en la Raspberry Pi**

```
1. Abrir http://192.168.1.50:9000
2. Stacks → Add stack → Repository
3. Repository URL: https://github.com/<tu-usuario>/price-tracker
4. Reference: refs/heads/main
5. Compose path: docker-compose.yml
6. Environment variables:
   SECRET_KEY=<genera con: python3 -c "import secrets; print(secrets.token_hex(32))">
   DATABASE_URL=sqlite+aiosqlite:////data/db.sqlite3
   TELEGRAM_BOT_TOKEN=<token de BotFather>
   CHECK_INTERVAL_HOURS=6
   FIRST_ADMIN_EMAIL=mariodam123@gmail.com
   FIRST_ADMIN_PASSWORD=<contraseña segura>
7. Deploy the stack
```

- [ ] **Step 3: Verificar que la app responde**

```bash
ssh rasp "curl -s -o /dev/null -w '%{http_code}' http://localhost:8766/login"
```
Esperado: `200`

- [ ] **Step 4: Añadir entrada DNS en Pi-hole**

```bash
ssh rasp "curl -s -X POST http://localhost/api/config/dns/hosts \
  -H 'Content-Type: application/json' \
  -d '{\"ip\":\"192.168.1.50\",\"name\":\"prices.lan\"}'"
```

- [ ] **Step 5: Commit final**

```bash
git add .
git commit -m "docs: finalize deployment docs and spec" --allow-empty
```
