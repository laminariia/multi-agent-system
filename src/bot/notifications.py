"""Outbound Telegram notifications via httpx.

This module is designed to be imported from the Litestar process (or any
other async context) without depending on python-telegram-bot.  It uses
``httpx.AsyncClient`` to call the Telegram Bot API directly, which avoids
pulling the bot's event-loop machinery into the web server.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.core.config import get_settings
from src.core.models import HITLQueue, User

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Type → emoji mapping (duplicated from keyboards.py to keep this module
# free of python-telegram-bot imports)
# ---------------------------------------------------------------------------

_TYPE_EMOJI: dict[str, str] = {
    "bid_approval": "\U0001f4dd",
    "code_review": "\U0001f50d",
    "delivery": "\U0001f4e6",
    "revision": "\U0001f504",
    "alert": "\u26a0\ufe0f",
    "outreach_approval": "\U0001f4e7",
    "dev_launch": "\U0001f680",
    "final_review": "\U0001f3c1",
    "design_review": "\U0001f3a8",
    "design_client_approval": "\U0001f465",
}

# ---------------------------------------------------------------------------
# Type → inline-keyboard markup (dict form for the Bot API)
# ---------------------------------------------------------------------------

_TYPE_BUTTONS: dict[str, list[dict[str, str]]] = {
    "bid_approval": [
        {"text": "\u2705 Approve", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Skip", "callback_data": "hitl:skip:{uuid_hex}"},
        {"text": "\u23f8\ufe0f Later", "callback_data": "hitl:later:{uuid_hex}"},
    ],
    "code_review": [
        {"text": "\u2705 Approve", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Skip", "callback_data": "hitl:skip:{uuid_hex}"},
    ],
    "delivery": [
        {"text": "\u2705 Approve", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u23f8\ufe0f Later", "callback_data": "hitl:later:{uuid_hex}"},
    ],
    "revision": [
        {"text": "\u2705 Approve", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Skip", "callback_data": "hitl:skip:{uuid_hex}"},
        {"text": "\u23f8\ufe0f Later", "callback_data": "hitl:later:{uuid_hex}"},
    ],
    "alert": [
        {"text": "\u2705 Ack", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Skip", "callback_data": "hitl:skip:{uuid_hex}"},
    ],
    "outreach_approval": [
        {"text": "\u2705 Approve", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Skip", "callback_data": "hitl:skip:{uuid_hex}"},
        {"text": "\u270f\ufe0f Edit", "callback_data": "hitl:edit:{uuid_hex}"},
    ],
    "dev_launch": [
        {"text": "\u2705 Launch", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Skip", "callback_data": "hitl:skip:{uuid_hex}"},
        {"text": "\u23f8\ufe0f Later", "callback_data": "hitl:later:{uuid_hex}"},
    ],
    "final_review": [
        {"text": "\u2705 Approve", "callback_data": "hitl:approve:{uuid_hex}"},
        {"text": "\u274c Reject", "callback_data": "hitl:reject:{uuid_hex}"},
        {"text": "\u23f8\ufe0f Later", "callback_data": "hitl:later:{uuid_hex}"},
    ],
    "design_review": [
        {"text": "\u2705 Approve", "callback_data": "hitl:design_approve:{uuid_hex}"},
        {"text": "\u270f\ufe0f Revise", "callback_data": "hitl:design_revise:{uuid_hex}"},
        {"text": "\u274c Reject", "callback_data": "hitl:design_reject:{uuid_hex}"},
    ],
    "design_client_approval": [
        {"text": "\u2705 Approved", "callback_data": "hitl:client_approved:{uuid_hex}"},
        {"text": "\u270f\ufe0f Changes", "callback_data": "hitl:client_changes:{uuid_hex}"},
        {"text": "\u274c Rejected", "callback_data": "hitl:client_rejected:{uuid_hex}"},
    ],
}


def _build_reply_markup(hitl_type: str, uuid_hex: str) -> dict[str, Any] | None:
    """Build an ``InlineKeyboardMarkup`` dict for the Telegram Bot API."""
    templates = _TYPE_BUTTONS.get(hitl_type)
    if not templates:
        return None

    buttons = []
    for tpl in templates:
        buttons.append(
            {
                "text": tpl["text"],
                "callback_data": tpl["callback_data"].format(uuid_hex=uuid_hex),
            }
        )

    # Single row
    return {"inline_keyboard": [buttons]}


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------


class TelegramNotifier:
    """Sends messages via the Telegram Bot API using ``httpx``.

    Intended to be used from the Litestar web process or background workers.
    Does not depend on ``python-telegram-bot``.

    Usage::

        notifier = TelegramNotifier()
        await notifier.send_message(chat_id=123456, text="Hello!")
        await notifier.notify_new_hitl(user, hitl_item)
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(self, token: str | None = None, timeout: float = 10.0) -> None:
        settings = get_settings()
        self._token = token or settings.TELEGRAM_BOT_TOKEN
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        self._timeout = timeout

    # -- Low-level send ----------------------------------------------------

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send a text message via the Telegram Bot API.

        Returns:
            The parsed JSON response from Telegram, or ``None`` on failure.
        """
        url = f"{self.BASE_URL}/bot{self._token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    logger.error(
                        "telegram.send_message_failed",
                        chat_id=chat_id,
                        error=data.get("description"),
                    )
                    return None
                return data
        except httpx.HTTPError as exc:
            logger.error(
                "telegram.send_message_error",
                chat_id=chat_id,
                error=str(exc),
            )
            return None

    # -- High-level: new HITL notification ---------------------------------

    async def notify_new_hitl(self, user: User, hitl_item: HITLQueue) -> bool:
        """Send a HITL notification card to a user's linked Telegram account.

        Args:
            user: The MAS user who should receive the notification.
            hitl_item: The HITL queue item to notify about.

        Returns:
            ``True`` if the message was sent successfully, ``False`` otherwise.
        """
        if user.telegram_chat_id is None:
            logger.warning(
                "telegram.notify_skipped_no_chat_id",
                user_id=str(user.id),
            )
            return False

        type_emoji = _TYPE_EMOJI.get(hitl_item.type, "\u2753")
        priority_label = (hitl_item.priority or "normal").upper()

        lines = [
            f"{type_emoji} <b>New HITL Item</b>",
            f"<b>{_esc(hitl_item.title)}</b>",
            f"Type: {_esc(hitl_item.type)} | Priority: <b>{_esc(priority_label)}</b>",
        ]

        if hitl_item.description:
            desc = hitl_item.description[:200]
            if len(hitl_item.description) > 200:
                desc += "..."
            lines.append(f"\n{_esc(desc)}")

        lines.append(f"\nID: <code>{hitl_item.id}</code>")

        if hitl_item.expires_at:
            lines.append(f"Expires: {hitl_item.expires_at.strftime('%Y-%m-%d %H:%M UTC')}")

        text = "\n".join(lines)

        # Build inline keyboard
        uuid_hex = hitl_item.id.hex
        reply_markup = _build_reply_markup(hitl_item.type, uuid_hex)

        result = await self.send_message(
            chat_id=user.telegram_chat_id,
            text=text,
            reply_markup=reply_markup,
        )

        success = result is not None
        if success:
            logger.info(
                "telegram.hitl_notification_sent",
                user_id=str(user.id),
                hitl_id=str(hitl_item.id),
                hitl_type=hitl_item.type,
            )
        return success


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
