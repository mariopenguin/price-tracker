import asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ── Helpers de formato ────────────────────────────────────────────────────────

def _fmt(price) -> str:
    """Formatea un precio al estilo español: 1.234,56 €"""
    if price is None:
        return "Sin datos"
    return f"{float(price):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _sparkline(prices: list) -> str:
    """Genera un sparkline Unicode a partir de una lista de precios."""
    blocks = "▁▂▃▄▅▆▇█"
    if not prices:
        return "—"
    lo, hi = min(prices), max(prices)
    if lo == hi:
        return blocks[3] * min(len(prices), 8)
    return "".join(blocks[round((p - lo) / (hi - lo) * 7)] for p in prices[-8:])


def _bar(value: float, lo: float, hi: float, width: int = 8) -> str:
    """Barra de progreso ASCII para historial."""
    if hi == lo:
        ratio = 1.0
    else:
        ratio = (value - lo) / (hi - lo)
    filled = max(1, round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def _trend(prices: list) -> str:
    if len(prices) < 2:
        return "➡️"
    diff = prices[-1] - prices[0]
    if diff < -0.01:
        return "📉"
    if diff > 0.01:
        return "📈"
    return "➡️"


def _day_es(dt: datetime) -> str:
    return ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"][dt.weekday()]


def _diff_str(diff: float, pct: float) -> str:
    """Formatea diferencia de precio con signo y porcentaje."""
    sign = "+" if diff >= 0 else "−"
    diff_fmt = f"{abs(diff):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    pct_fmt = f"{abs(pct):.1f}".replace(".", ",")
    return f"{sign}{diff_fmt} € ({sign}{pct_fmt}%)"


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_user_by_chat_id(chat_id: str):
    from app.database import SessionLocal
    from app.models import User
    from sqlalchemy import select
    async with SessionLocal() as db:
        r = await db.execute(select(User).where(User.telegram_chat_id == str(chat_id)))
        return r.scalar_one_or_none()


async def _link_user(code: str, chat_id: str) -> Optional[str]:
    """Vincula telegram_chat_id a la cuenta que usó ese código. Devuelve username o None."""
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
            return None  # ya vinculado
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


async def _get_product(chat_id: str, product_id: int):
    """Devuelve (user, product) o (user, None) si no existe o no pertenece al usuario."""
    user = await _get_user_by_chat_id(chat_id)
    if not user:
        return None, None
    from app.database import SessionLocal
    from app.models import Product
    async with SessionLocal() as db:
        product = await db.get(Product, product_id)
        if not product or product.user_id != user.id or not product.is_active:
            return user, None
        return user, product


async def _get_product_with_history(chat_id: str, product_id: int, days: int = 7):
    """Devuelve (user, product, history_list) con historial de los últimos N días."""
    from app.database import SessionLocal
    from app.models import Product, PriceHistory
    from sqlalchemy import select
    user = await _get_user_by_chat_id(chat_id)
    if not user:
        return None, None, []
    async with SessionLocal() as db:
        product = await db.get(Product, product_id)
        if not product or product.user_id != user.id or not product.is_active:
            return user, None, []
        since = datetime.now(timezone.utc) - timedelta(days=days)
        r = await db.execute(
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id, PriceHistory.recorded_at >= since)
            .order_by(PriceHistory.recorded_at)
        )
        history = r.scalars().all()
        return user, product, history


async def _add_product(chat_id: str, url: str) -> Optional[str]:
    """Devuelve nombre del producto, 'LIMIT' si se alcanzó el límite, o None si el usuario no existe."""
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


async def _update_alert(chat_id: str, product_id: int, *, target_price=None, target_pct=None, notify: bool) -> bool:
    user = await _get_user_by_chat_id(chat_id)
    if not user:
        return False
    from app.database import SessionLocal
    from app.models import Product
    async with SessionLocal() as db:
        product = await db.get(Product, product_id)
        if not product or product.user_id != user.id:
            return False
        product.notify_on_drop = notify
        product.target_price = target_price
        product.target_percentage = target_pct
        await db.commit()
        return True


# ── Vistas reutilizables ──────────────────────────────────────────────────────

async def _send_product_detail(message, chat_id: str, product_id: int):
    """Envía la tarjeta completa de un producto con historial e inline buttons."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    _, product, history = await _get_product_with_history(chat_id, product_id, days=7)
    if not product:
        await message.reply_text("❌ Producto no encontrado.")
        return

    prices = [float(h.price) for h in history]
    current = float(product.current_price) if product.current_price else None
    if current and (not prices or current != prices[-1]):
        prices.append(current)

    lines = [f"📦 <b>{product.name}</b>\n"]
    lines.append(f"💶 <b>Precio actual:</b> {_fmt(product.current_price)}")

    if len(prices) >= 2:
        first = prices[0]
        diff = current - first if current else 0
        pct = (diff / first * 100) if first else 0
        lines.append(f"📅 <b>Hace 7 días:</b>   {_fmt(first)}  <i>({_diff_str(diff, pct)})</i>")
        lines.append(f"\n{_trend(prices)} <b>Tendencia:</b>  <code>{_sparkline(prices)}</code>")
        lines.append(f"                 <i>hace 7d → hoy</i>")
        lines.append(f"\n🏷️ <b>Mín 7d:</b> {_fmt(min(prices))}  |  <b>Máx:</b> {_fmt(max(prices))}")
    elif prices:
        lines.append("\n📊 <i>Aún sin historial — se acumula en cada chequeo automático.</i>")
    else:
        lines.append("\n📊 <i>Sin datos de precio.</i>")

    # Alerta
    lines.append("")
    if product.notify_on_drop:
        if product.target_price:
            lines.append(f"🔔 <b>Alerta:</b> aviso si baja de {_fmt(product.target_price)}")
        elif product.target_percentage:
            lines.append(f"🔔 <b>Alerta:</b> aviso si baja un {float(product.target_percentage):.1f}%")
        else:
            lines.append("🔔 <b>Alerta:</b> cualquier bajada")
    else:
        lines.append("🔕 <b>Alerta:</b> desactivada")

    if product.last_checked_at:
        ts = product.last_checked_at.strftime("%d/%m %H:%M")
        lines.append(f"\n⏱️ <i>Último chequeo: {ts} UTC</i>")

    keyboard = [
        [
            InlineKeyboardButton("📊 Historial 7d", callback_data=f"hist:{product_id}"),
            InlineKeyboardButton("🔔 Configurar alerta", callback_data=f"alerta:{product_id}"),
        ],
        [
            InlineKeyboardButton("🗑️ Eliminar", callback_data=f"borrar:{product_id}"),
            InlineKeyboardButton("🔗 Abrir web", url=product.url),
        ],
    ]
    await message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


async def _send_historial(message, chat_id: str, product_id: int):
    """Envía la tabla de historial de precios de la última semana."""
    _, product, history = await _get_product_with_history(chat_id, product_id, days=7)
    if not product:
        await message.reply_text("❌ Producto no encontrado.")
        return
    if not history:
        await message.reply_text(
            f"📊 <b>{product.name}</b>\n\n<i>Sin historial todavía — los precios se registran en cada chequeo automático.</i>",
            parse_mode="HTML",
        )
        return

    prices = [float(h.price) for h in history]
    lo, hi = min(prices), max(prices)

    lines = [f"📊 <b>Historial 7 días</b>\n<i>{product.name}</i>\n"]
    for i, h in enumerate(history):
        dt = h.recorded_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        label = f"{_day_es(dt)} {dt.strftime('%d/%m')}"
        bar = _bar(float(h.price), lo, hi)
        suffix = " ← hoy" if i == len(history) - 1 else ""
        lines.append(f"<code>{label}  {_fmt(h.price):>10}  {bar}{suffix}</code>")

    if len(prices) >= 2:
        diff = prices[-1] - prices[0]
        pct = (diff / prices[0] * 100) if prices[0] else 0
        lines.append(f"\n{_trend(prices)} {_diff_str(diff, pct)} en 7 días")
    lines.append(f"🏷️ Mín: {_fmt(lo)}  |  Máx: {_fmt(hi)}")

    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def _send_alerta_menu(message, chat_id: str, product_id: int):
    """Envía el menú interactivo de configuración de alertas."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    _, product = await _get_product(chat_id, product_id)
    if not product:
        await message.reply_text("❌ Producto no encontrado.")
        return

    lines = [
        f"🔔 <b>Alertas — {product.name}</b>",
        f"💶 Precio actual: {_fmt(product.current_price)}\n",
    ]
    if product.notify_on_drop:
        if product.target_price:
            lines.append(f"<b>Activa:</b> te aviso si baja de {_fmt(product.target_price)}")
        elif product.target_percentage:
            lines.append(f"<b>Activa:</b> te aviso si baja un {float(product.target_percentage):.1f}%")
        else:
            lines.append("<b>Activa:</b> cualquier bajada de precio")
    else:
        lines.append("<b>Estado:</b> desactivada 🔕")

    keyboard = [
        [
            InlineKeyboardButton("💶 Fijar precio objetivo", callback_data=f"ap:{product_id}"),
            InlineKeyboardButton("📉 Fijar % de bajada", callback_data=f"apct:{product_id}"),
        ],
        [InlineKeyboardButton("❌ Desactivar alertas", callback_data=f"aoff:{product_id}")],
    ]
    await message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Handlers de comandos ──────────────────────────────────────────────────────

async def _start(update, context):
    chat_id = str(update.effective_chat.id)
    user = await _get_user_by_chat_id(chat_id)
    if user:
        await update.message.reply_text(
            f"Ya estás registrado como <b>{user.username}</b>. Usa /ayuda para ver los comandos.",
            parse_mode="HTML",
        )
        return
    await update.message.reply_text(
        "👋 <b>Bienvenido al bot de Price Tracker.</b>\n\n"
        "Para vincular tu cuenta:\n"
        "1. Regístrate en la web con tu código de invitación\n"
        "2. Envíame ese mismo código aquí",
        parse_mode="HTML",
    )


async def _lista(update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from app.database import SessionLocal
    from app.models import PriceHistory
    from sqlalchemy import select
    chat_id = str(update.effective_chat.id)
    products = await _get_products(chat_id)
    if not products:
        await update.message.reply_text(
            "No tienes productos trackeados.\nUsa /add <code>&lt;url&gt;</code> para añadir uno.",
            parse_mode="HTML",
        )
        return

    since = datetime.now(timezone.utc) - timedelta(days=7)
    lines = [f"📦 <b>Tus productos ({len(products)})</b>\n"]
    keyboard = []

    async with SessionLocal() as db:
        for p in products:
            r = await db.execute(
                select(PriceHistory.price)
                .where(PriceHistory.product_id == p.id, PriceHistory.recorded_at >= since)
                .order_by(PriceHistory.recorded_at)
            )
            prices = [float(row[0]) for row in r.all()]
            if p.current_price:
                cur = float(p.current_price)
                if not prices or cur != prices[-1]:
                    prices.append(cur)

            trend = _trend(prices) if len(prices) >= 2 else "🆕"
            spark = f"<code>{_sparkline(prices)}</code>" if prices else "<i>sin datos</i>"
            alert_icon = " 🔔" if p.notify_on_drop else ""

            lines.append(
                f"{trend} <b>[{p.id}]</b> {p.name}\n"
                f"     {spark}  {_fmt(p.current_price)}{alert_icon}"
            )
            keyboard.append([
                InlineKeyboardButton(
                    f"🔍 [{p.id}] {p.name[:35]}",
                    callback_data=f"detail:{p.id}",
                )
            ])

    lines.append("\nPulsa un producto para ver detalles y alertas.")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _precio(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /precio <code>&lt;id&gt;</code>", parse_mode="HTML")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return
    await _send_product_detail(update.message, chat_id, pid)


async def _historial(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /historial <code>&lt;id&gt;</code>", parse_mode="HTML")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return
    await _send_historial(update.message, chat_id, pid)


async def _alerta(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /alerta <code>&lt;id&gt;</code>", parse_mode="HTML")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return
    await _send_alerta_menu(update.message, chat_id, pid)


async def _add(update, context):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /add <code>&lt;url&gt;</code>", parse_mode="HTML")
        return
    url = context.args[0]
    await update.message.reply_text("⏳ Obteniendo precio, espera...")
    result = await _add_product(chat_id, url)
    if result == "LIMIT":
        await update.message.reply_text("⚠️ Has alcanzado el límite de 100 productos.")
    elif result:
        await update.message.reply_text(f"✅ Producto añadido: <b>{result}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ No se pudo añadir. ¿Estás registrado en el bot?")


async def _borrar_cmd(update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Uso: /borrar <code>&lt;id&gt;</code>", parse_mode="HTML")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return
    _, product = await _get_product(chat_id, pid)
    if not product:
        await update.message.reply_text("❌ Producto no encontrado.")
        return
    keyboard = [[
        InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"bconf:{pid}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"bcanc:{pid}"),
    ]]
    await update.message.reply_text(
        f"⚠️ ¿Eliminar <b>{product.name}</b>?\nEsta acción no se puede deshacer.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _ayuda(update, context):
    await update.message.reply_text(
        "📋 <b>Comandos disponibles:</b>\n\n"
        "/lista — Ver todos tus productos con tendencia\n"
        "/add <code>&lt;url&gt;</code> — Añadir producto para trackear\n"
        "/precio <code>&lt;id&gt;</code> — Detalle, tendencia y alertas\n"
        "/historial <code>&lt;id&gt;</code> — Gráfico de precios últimos 7 días\n"
        "/alerta <code>&lt;id&gt;</code> — Configurar alerta de bajada de precio\n"
        "/borrar <code>&lt;id&gt;</code> — Eliminar un producto\n"
        "/ayuda — Esta ayuda",
        parse_mode="HTML",
    )


# ── Handler de texto libre (códigos de invitación + valores de alerta) ────────

async def _handle_text(update, context):
    chat_id = str(update.effective_chat.id)
    user = await _get_user_by_chat_id(chat_id)

    # ── Valor de alerta pendiente ───────────────────────────────────────────
    alert_wait = context.user_data.get("alert_wait")
    if user and alert_wait:
        del context.user_data["alert_wait"]
        pid = alert_wait["pid"]
        kind = alert_wait["kind"]  # "price" o "pct"
        raw = update.message.text.strip().replace(",", ".")
        try:
            value = float(raw)
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Valor no válido. Envía un número positivo, por ejemplo: <code>250</code> o <code>10</code>",
                parse_mode="HTML",
            )
            return

        _, product = await _get_product(chat_id, pid)
        if not product:
            await update.message.reply_text("❌ Producto no encontrado.")
            return

        if kind == "price":
            ok = await _update_alert(chat_id, pid, target_price=value, target_pct=None, notify=True)
            if ok:
                await update.message.reply_text(
                    f"✅ Alerta configurada.\n"
                    f"Te avisaré cuando <b>{product.name}</b> baje de <b>{_fmt(value)}</b>.",
                    parse_mode="HTML",
                )
        else:
            ok = await _update_alert(chat_id, pid, target_price=None, target_pct=value, notify=True)
            if ok:
                await update.message.reply_text(
                    f"✅ Alerta configurada.\n"
                    f"Te avisaré cuando <b>{product.name}</b> baje un <b>{value:.1f}%</b>.",
                    parse_mode="HTML",
                )
        if not ok:
            await update.message.reply_text("❌ No se pudo guardar la alerta.")
        return

    # ── Código de invitación ────────────────────────────────────────────────
    if user:
        return  # ya vinculado, ignorar
    code = update.message.text.strip()
    username = await _link_user(code, chat_id)
    if username:
        await update.message.reply_text(
            f"✅ Cuenta vinculada como <b>{username}</b>. Usa /ayuda para ver los comandos.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "❌ Código no válido o ya usado. Genera uno nuevo en la web → Ajustes."
        )


# ── Callback queries (botones inline) ────────────────────────────────────────

async def _callback(update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    query = update.callback_query
    await query.answer()
    chat_id = str(query.from_user.id)
    data = query.data

    if data.startswith("detail:"):
        pid = int(data.split(":")[1])
        await _send_product_detail(query.message, chat_id, pid)

    elif data.startswith("hist:"):
        pid = int(data.split(":")[1])
        await _send_historial(query.message, chat_id, pid)

    elif data.startswith("alerta:"):
        pid = int(data.split(":")[1])
        await _send_alerta_menu(query.message, chat_id, pid)

    elif data.startswith("ap:"):      # fijar precio objetivo
        pid = int(data.split(":")[1])
        _, product = await _get_product(chat_id, pid)
        if not product:
            await query.message.reply_text("❌ Producto no encontrado.")
            return
        context.user_data["alert_wait"] = {"pid": pid, "kind": "price"}
        await query.message.reply_text(
            f"💶 <b>Precio objetivo para:</b>\n<i>{product.name}</i>\n\n"
            f"Precio actual: {_fmt(product.current_price)}\n\n"
            f"Envía el precio en € al que quieres que te avise (ej: <code>250</code>):",
            parse_mode="HTML",
        )

    elif data.startswith("apct:"):    # fijar porcentaje
        pid = int(data.split(":")[1])
        _, product = await _get_product(chat_id, pid)
        if not product:
            await query.message.reply_text("❌ Producto no encontrado.")
            return
        context.user_data["alert_wait"] = {"pid": pid, "kind": "pct"}
        await query.message.reply_text(
            f"📉 <b>% de bajada para:</b>\n<i>{product.name}</i>\n\n"
            f"Precio actual: {_fmt(product.current_price)}\n\n"
            f"Envía el porcentaje de bajada (ej: <code>10</code> para avisar si baja un 10%):",
            parse_mode="HTML",
        )

    elif data.startswith("aoff:"):    # desactivar alerta
        pid = int(data.split(":")[1])
        ok = await _update_alert(chat_id, pid, notify=False)
        await query.message.reply_text(
            "🔕 Alertas desactivadas." if ok else "❌ No se pudo actualizar."
        )

    elif data.startswith("borrar:"):  # pedir confirmación de borrado
        pid = int(data.split(":")[1])
        _, product = await _get_product(chat_id, pid)
        if not product:
            await query.message.reply_text("❌ Producto no encontrado.")
            return
        keyboard = [[
            InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"bconf:{pid}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"bcanc:{pid}"),
        ]]
        await query.message.reply_text(
            f"⚠️ ¿Eliminar <b>{product.name}</b>?\nEsta acción no se puede deshacer.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("bconf:"):   # borrado confirmado
        pid = int(data.split(":")[1])
        ok = await _delete_product(chat_id, pid)
        await query.message.reply_text(
            "✅ Producto eliminado." if ok else "❌ No se pudo eliminar."
        )

    elif data.startswith("bcanc:"):   # borrado cancelado
        await query.message.reply_text("↩️ Eliminación cancelada.")


# ── Runner ────────────────────────────────────────────────────────────────────

async def _bot_main() -> None:
    """Arranca el bot usando la API de bajo nivel de PTB en el loop del hilo daemon.

    Evita run_polling() porque llama a asyncio.run() internamente, lo que
    falla en un hilo daemon. Usamos app.start() + updater.start_polling()
    directamente. stop_signals solo existe en Application.run_polling(),
    NO en Updater.start_polling(). await asyncio.Future() mantiene la
    corutina viva; el hilo daemon muere con el proceso principal.
    """
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, filters,
    )

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start",    _start))
    app.add_handler(CommandHandler("lista",    _lista))
    app.add_handler(CommandHandler("add",      _add))
    app.add_handler(CommandHandler("precio",   _precio))
    app.add_handler(CommandHandler("historial", _historial))
    app.add_handler(CommandHandler("alerta",   _alerta))
    app.add_handler(CommandHandler("borrar",   _borrar_cmd))
    app.add_handler(CommandHandler("ayuda",    _ayuda))
    app.add_handler(CallbackQueryHandler(_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))

    async with app:
        await app.bot.set_my_commands([
            ("lista",     "📦 Ver todos tus productos"),
            ("add",       "➕ Añadir producto por URL"),
            ("precio",    "💶 Detalle, tendencia y alertas"),
            ("historial", "📊 Historial de precios 7 días"),
            ("alerta",    "🔔 Configurar alerta de bajada"),
            ("borrar",    "🗑️ Eliminar un producto"),
            ("ayuda",     "📋 Ver todos los comandos"),
        ])
        await app.start()
        await app.updater.start_polling()
        logger.info("Telegram bot started (polling)")
        await asyncio.Future()  # run forever — daemon thread killed on process exit


def run_bot() -> None:
    """Arranca el bot de Telegram en su propio event loop (llamar desde hilo daemon)."""
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled.")
        return
    try:
        asyncio.run(_bot_main())  # crea y gestiona su propio event loop
    except Exception as e:
        logger.error(f"Telegram bot crashed: {e}")


def start_bot_thread() -> None:
    thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    thread.start()
