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
    """Returns True if a notification should be sent based on price change and user settings."""
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
    old_fmt = f"{old_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    new_fmt = f"{new_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return (
        f"📉 {name}\n"
        f"Precio anterior: {old_fmt} €\n"
        f"Precio actual:   {new_fmt} € (-{drop_pct:.1f}%)\n"
        f"🔗 {url}"
    )


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
