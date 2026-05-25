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
            "login.html",
            {"request": request, "error": "Email o contraseña incorrectos"},
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
    # Validate invite code (must exist and not yet used)
    ic_result = await db.execute(
        select(InviteCode).where(InviteCode.code == code, InviteCode.used_by == None)  # noqa: E711
    )
    invite = ic_result.scalar_one_or_none()
    if not invite:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "code": code, "error": "Código inválido o ya usado"},
        )

    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "code": code, "error": "Email ya registrado"},
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
