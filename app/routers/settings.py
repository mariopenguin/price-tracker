from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
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
        "settings.html",
        {"request": request, "user": user, "success": "Contraseña actualizada correctamente"},
    )
