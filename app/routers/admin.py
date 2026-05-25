import uuid

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
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
        "admin.html",
        {"request": request, "user": admin, "users": users, "codes": codes},
    )


@router.post("/admin/invite")
async def create_invite(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    code = InviteCode(code=str(uuid.uuid4()), created_by=admin.id)
    db.add(code)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=302)
