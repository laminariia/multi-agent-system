"""Inline keyboards and callback-query handler for HITL cards.

Each HITL item type gets a tailored set of action buttons.  Callback data
is kept within Telegram's 64-byte limit by using a compact format:

    ``hitl:<action>:<uuid_hex>``

where ``<uuid_hex>`` is the 32-character hex representation of the UUID
(no dashes), yielding a total of ~46 bytes max.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.core.database import get_db_session
from src.core.models import HITLQueue, User

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Type → emoji mapping
# ---------------------------------------------------------------------------

_TYPE_EMOJI: dict[str, str] = {
    "bid_approval": "\U0001f4dd",  # memo
    "code_review": "\U0001f50d",  # magnifying glass
    "delivery": "\U0001f4e6",  # package
    "revision": "\U0001f504",  # arrows counterclockwise
    "alert": "\u26a0\ufe0f",  # warning sign
    "design_review": "\U0001f3a8",  # palette
    "design_client_approval": "\U0001f465",  # busts in silhouette
}

# ---------------------------------------------------------------------------
# Type → available buttons
# ---------------------------------------------------------------------------

_TYPE_BUTTONS: dict[str, list[tuple[str, str]]] = {
    "bid_approval": [
        ("\u2705 Approve", "approve"),
        ("\u274c Skip", "skip"),
        ("\u23f8\ufe0f Later", "later"),
    ],
    "code_review": [
        ("\u2705 Approve", "approve"),
        ("\u274c Skip", "skip"),
    ],
    "delivery": [
        ("\u2705 Approve", "approve"),
        ("\u23f8\ufe0f Later", "later"),
    ],
    "revision": [
        ("\u2705 Approve", "approve"),
        ("\u274c Skip", "skip"),
        ("\u23f8\ufe0f Later", "later"),
    ],
    "alert": [
        ("\u2705 Ack", "approve"),
        ("\u274c Skip", "skip"),
    ],
    "design_review": [
        ("\u2705 Approve", "design_approve"),
        ("\u270f\ufe0f Revise", "design_revise"),
        ("\u274c Reject", "design_reject"),
    ],
    "design_client_approval": [
        ("\u2705 Approved", "client_approved"),
        ("\u270f\ufe0f Changes", "client_changes"),
        ("\u274c Rejected", "client_rejected"),
    ],
}

# Fallback buttons for unknown types
_DEFAULT_BUTTONS: list[tuple[str, str]] = [
    ("\u2705 Approve", "approve"),
    ("\u274c Skip", "skip"),
]


# ---------------------------------------------------------------------------
# Public: send a HITL card to a chat
# ---------------------------------------------------------------------------


async def send_hitl_card(bot: Bot, chat_id: int, item: HITLQueue) -> None:
    """Format and send a single HITL item as a Telegram message with inline buttons."""
    type_emoji = _TYPE_EMOJI.get(item.type, "\u2753")
    priority_label = item.priority.upper() if item.priority else "NORMAL"

    # Build message body
    lines = [
        f"{type_emoji} <b>{_esc(item.title)}</b>",
        f"Type: {_esc(item.type)} | Priority: <b>{_esc(priority_label)}</b>",
    ]

    if item.description:
        desc = item.description[:300]
        if len(item.description) > 300:
            desc += "..."
        lines.append(f"\n{_esc(desc)}")

    # Show key payload details (compact)
    if item.payload:
        payload_lines = _format_payload(item.payload)
        if payload_lines:
            lines.append("")
            lines.extend(payload_lines)

    lines.append(f"\nID: <code>{item.id}</code>")

    if item.expires_at:
        lines.append(f"Expires: {item.expires_at.strftime('%Y-%m-%d %H:%M UTC')}")

    text = "\n".join(lines)

    # Build keyboard
    uuid_hex = item.id.hex
    button_defs = _TYPE_BUTTONS.get(item.type, _DEFAULT_BUTTONS)

    # Only include buttons whose action is in the item's available_actions
    available = set(item.available_actions) if item.available_actions else set()
    buttons = []
    for label, action in button_defs:
        if not available or action in available:
            callback_data = f"hitl:{action}:{uuid_hex}"
            buttons.append(InlineKeyboardButton(text=label, callback_data=callback_data))

    # Arrange in rows (max 3 per row)
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 3):
        keyboard_rows.append(buttons[i : i + 3])

    markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle presses on inline-keyboard buttons attached to HITL cards.

    Expected callback_data format: ``hitl:<action>:<uuid_hex>``
    """
    query = update.callback_query
    if query is None:
        return

    await query.answer()  # Acknowledge immediately to remove the spinner

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "hitl":
        await query.answer(text="Unknown action.", show_alert=True)
        return

    action = parts[1]
    uuid_hex = parts[2]

    # Parse UUID
    try:
        hitl_id = uuid.UUID(hex=uuid_hex)
    except ValueError:
        await query.edit_message_text(text="Invalid item ID.")
        return

    # Verify user is linked
    tg_user_id = update.effective_user.id  # type: ignore[union-attr]
    async with get_db_session() as session:
        user_stmt = select(User).where(User.telegram_chat_id == tg_user_id)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

    if user is None:
        await query.edit_message_text(text="Your Telegram account is not linked. Use /start first.")
        return

    # Load and resolve the HITL item
    now = datetime.now(UTC)

    async with get_db_session() as session:
        stmt = select(HITLQueue).where(HITLQueue.id == hitl_id)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()

        if item is None:
            await query.edit_message_text(
                text=f"\u274c Item not found: <code>{hitl_id}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if item.status != "pending":
            await query.edit_message_text(
                text=(f"Item already <b>{_esc(item.status)}</b> (resolution: {_esc(item.resolution or 'n/a')})."),
                parse_mode=ParseMode.HTML,
            )
            return

        # Check if action is available
        if item.available_actions and action not in item.available_actions:
            await query.edit_message_text(
                text=(
                    f"Action <b>{_esc(action)}</b> is not available.\nAvailable: {', '.join(item.available_actions)}"
                ),
                parse_mode=ParseMode.HTML,
            )
            return

        # Apply resolution
        item.status = "resolved"
        item.resolution = action
        item.resolved_by = user.id
        item.resolved_at = now

    logger.info(
        "telegram.hitl_callback_resolved",
        hitl_id=str(hitl_id),
        action=action,
        resolved_by=str(user.id),
    )

    action_label = action.capitalize()
    await query.edit_message_text(
        text=(f"\u2705 <b>Resolved:</b> {_esc(action_label)}\nItem: <code>{hitl_id}</code>"),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_payload(payload: dict[str, Any], max_items: int = 5) -> list[str]:
    """Format a JSONB payload dict into readable Telegram HTML lines."""
    lines: list[str] = []
    shown = 0
    for key, value in payload.items():
        if shown >= max_items:
            lines.append("  ...")
            break
        display_value = str(value)
        if len(display_value) > 100:
            display_value = display_value[:100] + "..."
        lines.append(f"  <b>{_esc(key)}:</b> {_esc(display_value)}")
        shown += 1
    return lines


def _esc(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
