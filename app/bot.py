import asyncio
import logging
import threading
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ── DB helpers (async) ────────────────────────────────────────────────────────

async def _get_user_by_chat_id(chat_id: str):
    from app.database import SessionLocal
    from app.models import User
    from sqlalchemy import select
    async with SessionLocal() as db:
        r = await db.execute(select(User).where(User.telegram_chat_id == str(chat_id)))
        return r.scalar_one_or_none()


async def _link_user(code: str, chat_id: str) -> Optional[str]:
    """Links telegram_chat_id to the account that used this invite code. Returns username or None."""
    from app.database import SessionLocal
    from app.models import InviteCode, User
    from sqlalchemy import select
    async with SessionLocal() as db:
        ic = await db.execute(select(InviteCode).where(InviteCode.code == code))
        invite = ic.scalar_one_or_none()
        if not invite or not invite.used_by:
            return None
        user = await db.get(User, invite.used_by)
        if not user:
            return None
        if user.telegram_chat_id:
            return None  # already linked
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
    """Returns product name, 'LIMIT' if limit reached, or None if user not found."""
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
        if (count_r.scalar() or 0) >= 100:
            return "LIMIT"
        scrape_fn = scraper_for(url)
        result = scrape_fn(url)
        product = Product(
            user_id=user.id,
            url=url,
            name=result.name if result else "Producto",
            current_price=result.price if result else None,
            image_url=result.image_url if result else None,
        )
        db.add(product)
        await db.commit()
        return product.name


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

async def _start(update, context):
    chat_id = str(update.effective_chat.id)
    user = await _get_user_by_chat_id(chat_id)
    if user:
        await update.message.reply_text(
            f"Ya estás registrado como {user.username}. Usa /ayuda para ver los comandos."
        )
        return
    await update.message.reply_text(
        "👋 Bienvenido al bot de Price Tracker.\n\n"
        "Para vincular tu cuenta:\n"
        "1. Regístrate en la web con tu código de invitación\n"
        "2. Envíame ese mismo código aquí"
    )


async def _handle_text(update, context):
    """Any non-command text → try to link as invite code."""
    chat_id = str(update.effective_chat.id)
    user = await _get_user_by_chat_id(chat_id)
    if user:
        return  # already linked, ignore
    code = update.message.text.strip()
    username = await _link_user(code, chat_id)
    if username:
        await update.message.reply_text(
            f"✅ Cuenta vinculada como *{username}*. Usa /ayuda para ver los comandos.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ Código no válido o ya usado. Genera uno nuevo en la web → Ajustes.")


async def _lista(update, context):
    chat_id = str(update.effective_chat.id)
    products = await _get_products(chat_id)
    if not products:
        await update.message.reply_text("No tienes productos trackeados. Usa /add <url> para añadir uno.")
        return
    lines = []
    for p in products:
        price_str = (
            f"{float(p.current_price):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
            if p.current_price else "Sin datos"
        )
        lines.append(f"[{p.id}] {p.name} — {price_str}")
    await update.message.reply_text("\n".join(lines))


async def _add(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /add <url>")
        return
    url = context.args[0]
    await update.message.reply_text("⏳ Obteniendo precio, espera...")
    result = await _add_product(chat_id, url)
    if result == "LIMIT":
        await update.message.reply_text("⚠️ Has alcanzado el límite de 100 productos.")
    elif result:
        await update.message.reply_text(f"✅ Producto añadido: {result}")
    else:
        await update.message.reply_text("❌ No se pudo añadir. ¿Estás registrado en el bot?")


async def _precio(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /precio <id>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return
    products = await _get_products(chat_id)
    product = next((p for p in products if p.id == pid), None)
    if not product:
        await update.message.reply_text("Producto no encontrado.")
        return
    price_str = (
        f"{float(product.current_price):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
        if product.current_price else "Sin datos"
    )
    await update.message.reply_text(f"📦 {product.name}\n💶 {price_str}\n🔗 {product.url}")


async def _borrar(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /borrar <id>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return
    ok = await _delete_product(chat_id, pid)
    await update.message.reply_text("✅ Producto eliminado." if ok else "❌ No se pudo eliminar.")


async def _ayuda(update, context):
    await update.message.reply_text(
        "📋 *Comandos disponibles:*\n\n"
        "/lista — Ver todos tus productos\n"
        "/add <url> — Añadir producto para trackear\n"
        "/precio <id> — Ver precio actual de un producto\n"
        "/borrar <id> — Eliminar un producto\n"
        "/ayuda — Esta ayuda",
        parse_mode="Markdown",
    )


# ── Runner ────────────────────────────────────────────────────────────────────

async def _bot_main() -> None:
    """Coroutine that runs the bot using PTB's low-level API.

    run_polling() internally calls asyncio.run() which conflicts with an
    already-running loop. Instead we use app.start() + updater.start_polling()
    directly on our own event loop and await a Future that never resolves
    (the daemon thread is killed when the process exits).
    """
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("lista", _lista))
    app.add_handler(CommandHandler("add", _add))
    app.add_handler(CommandHandler("precio", _precio))
    app.add_handler(CommandHandler("borrar", _borrar))
    app.add_handler(CommandHandler("ayuda", _ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))

    async with app:
        await app.start()
        await app.updater.start_polling(stop_signals=None)
        logger.info("Telegram bot started (polling)")
        await asyncio.Future()  # run forever — daemon thread killed on process exit


def run_bot() -> None:
    """Starts the Telegram bot in its own event loop (call from a daemon thread)."""
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_bot_main())
    except Exception as e:
        logger.error(f"Telegram bot crashed: {e}")
    finally:
        loop.close()


def start_bot_thread() -> None:
    thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    thread.start()
