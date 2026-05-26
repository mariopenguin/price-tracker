import asyncio
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import User, Product, PriceHistory
from app.dependencies import require_user, templates
from app.scrapers import scraper_for

router = APIRouter()

MAX_PRODUCTS = 100


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product)
        .where(Product.user_id == user.id, Product.is_active == True)  # noqa: E712
        .options(selectinload(Product.price_history))
        .order_by(Product.created_at.desc())
    )
    products = result.scalars().all()

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
        "dashboard.html",
        {"request": request, "user": user, "products_data": products_data},
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
    count_r = await db.execute(
        select(func.count()).where(Product.user_id == user.id, Product.is_active == True)  # noqa: E712
    )
    if (count_r.scalar() or 0) >= MAX_PRODUCTS:
        return templates.TemplateResponse(
            "product_add.html",
            {
                "request": request,
                "user": user,
                "error": f"Has alcanzado el límite de {MAX_PRODUCTS} productos.",
            },
        )

    from app.scrapers import normalize_url
    url = normalize_url(url)
    scrape_fn = scraper_for(url)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, scrape_fn, url)
    name = result.name if result else "Producto"
    price = result.price if result else None
    image = result.image_url if result else None

    product = Product(user_id=user.id, url=url, name=name, current_price=price, image_url=image)
    db.add(product)
    await db.flush()

    if price:
        db.add(PriceHistory(product_id=product.id, price=price))

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
    request: Request,
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
    product.target_percentage = (
        float(target_percentage.replace(",", ".")) if target_percentage.strip() else None
    )
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
