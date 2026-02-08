"""Telegram bot command handlers.

Each command is a plain async function compatible with python-telegram-bot 21.x.
Database access uses the project's shared ``get_db_session`` / ``get_valkey``
helpers so the bot shares connection pools with the rest of the system.
"""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

import structlog
from sqlalchemy import case, func, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.bot.keyboards import send_hitl_card
from src.core.database import get_db_session, get_valkey
from src.core.models import AgentHeartbeat, HITLQueue, User

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Decorator: require a linked MAS account
# ---------------------------------------------------------------------------

_UNLINKED_MSG = (
    "Your Telegram account is not linked to a MAS account.\n"
    "Use /start to get a link code, then enter it in the Dashboard Settings."
)


def require_linked_account(func_):
    """Decorator that looks up the ``User`` by ``telegram_chat_id``.

    If the user is not found the bot replies with instructions and the
    wrapped handler is *not* called.  On success the ``User`` instance is
    stored in ``context.user_data["mas_user"]``.
    """

    @wraps(func_)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        tg_user_id = update.effective_user.id  # type: ignore[union-attr]
        async with get_db_session() as session:
            stmt = select(User).where(User.telegram_chat_id == tg_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

        if user is None:
            await update.effective_message.reply_text(_UNLINKED_MSG)  # type: ignore[union-attr]
            return None

        context.user_data["mas_user"] = user  # type: ignore[index]
        return await func_(update, context)

    return wrapper


# ---------------------------------------------------------------------------
# /start — Welcome + link-code generation
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/start``, ``/start auto``, and welcome returning users."""
    tg_user_id = update.effective_user.id  # type: ignore[union-attr]
    args = context.args or []

    # Check if already linked
    async with get_db_session() as session:
        stmt = select(User).where(User.telegram_chat_id == tg_user_id)
        result = await session.execute(stmt)
        existing_user = result.scalar_one_or_none()

    if existing_user is not None:
        name = existing_user.name or existing_user.email
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"Welcome back, <b>{_esc(name)}</b>!\n\n"
            "Your Telegram account is already linked.\n"
            "Use /status to see agent health, /pending for HITL items.",
            parse_mode=ParseMode.HTML,
        )
        return

    # --- /start auto — auto-link to the first owner user ------------------
    if args and args[0].lower() == "auto":
        async with get_db_session() as session:
            stmt = select(User).where(User.role == "owner").limit(1)
            result = await session.execute(stmt)
            owner = result.scalar_one_or_none()

            if owner is None:
                await update.effective_message.reply_text(  # type: ignore[union-attr]
                    "No owner account found in the database. "
                    "Create one via the API first."
                )
                return

            owner.telegram_chat_id = tg_user_id
            # session commits on exit

        logger.info("telegram.auto_linked", tg_user_id=tg_user_id, user_id=str(owner.id))
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"Auto-linked to account <b>{_esc(owner.name or owner.email)}</b>.\n"
            "Use /status to get started.",
            parse_mode=ParseMode.HTML,
        )
        return

    # --- Default: generate a 6-char link code -----------------------------
    code = _generate_link_code()
    valkey = get_valkey()
    await valkey.setex(f"telegram_link:{code}", 600, str(tg_user_id))

    logger.info("telegram.link_code_generated", tg_user_id=tg_user_id, code=code)

    await update.effective_message.reply_text(  # type: ignore[union-attr]
        f"Welcome to the <b>Multi-Agent Service</b> bot!\n\n"
        f"Your link code is: <code>{code}</code>\n\n"
        "Enter this code in <b>Dashboard Settings</b> to link your account.\n"
        "The code expires in 10 minutes.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /status — Agent health overview
# ---------------------------------------------------------------------------


@require_linked_account
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a summary of agent health and pending HITL count."""
    now = datetime.now(UTC)
    healthy_threshold = now - timedelta(minutes=3)

    async with get_db_session() as session:
        # All heartbeats
        stmt = select(AgentHeartbeat)
        result = await session.execute(stmt)
        heartbeats = result.scalars().all()

        # Pending HITL count
        hitl_stmt = select(func.count()).select_from(
            select(HITLQueue).where(HITLQueue.status == "pending").subquery()
        )
        pending_count = (await session.execute(hitl_stmt)).scalar_one()

    if not heartbeats:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "No agents have reported a heartbeat yet."
        )
        return

    healthy = 0
    idle = 0
    working = 0
    dead = 0
    lines: list[str] = []

    for hb in heartbeats:
        is_alive = hb.last_heartbeat is not None and hb.last_heartbeat >= healthy_threshold
        if is_alive:
            healthy += 1
            if hb.status == "working":
                working += 1
                emoji = "\U0001f7e2"  # green circle
            else:
                idle += 1
                emoji = "\U0001f535"  # blue circle
        else:
            dead += 1
            emoji = "\U0001f534"  # red circle

        task_info = f" — {_esc(hb.current_task)}" if hb.current_task else ""
        lines.append(f"{emoji} <b>{_esc(hb.agent_name)}</b> [{hb.status}]{task_info}")

    header = (
        f"<b>Agent Status</b>\n"
        f"Healthy: {healthy} | Working: {working} | Idle: {idle} | Dead: {dead}\n"
        f"Pending HITL items: {pending_count}\n"
        f"{'=' * 30}\n"
    )

    await update.effective_message.reply_text(  # type: ignore[union-attr]
        header + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /pending — Show pending HITL items
# ---------------------------------------------------------------------------


@require_linked_account
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show up to 5 pending HITL items as interactive cards."""
    priority_order = case(
        (HITLQueue.priority == "urgent", 0),
        (HITLQueue.priority == "normal", 1),
        (HITLQueue.priority == "low", 2),
        else_=3,
    )

    async with get_db_session() as session:
        stmt = (
            select(HITLQueue)
            .where(HITLQueue.status == "pending")
            .order_by(priority_order, HITLQueue.created_at.desc())
            .limit(5)
        )
        result = await session.execute(stmt)
        items = result.scalars().all()

    if not items:
        await update.effective_message.reply_text("No pending HITL items.")  # type: ignore[union-attr]
        return

    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    bot = context.bot

    await update.effective_message.reply_text(  # type: ignore[union-attr]
        f"<b>{len(items)} pending item(s):</b>",
        parse_mode=ParseMode.HTML,
    )

    for item in items:
        await send_hitl_card(bot, chat_id, item)


# ---------------------------------------------------------------------------
# /stats — Today's HITL statistics
# ---------------------------------------------------------------------------


@require_linked_account
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show today's HITL queue statistics."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with get_db_session() as session:
        base = select(HITLQueue).where(HITLQueue.created_at >= today_start)

        pending = (
            await session.execute(
                select(func.count()).select_from(
                    base.where(HITLQueue.status == "pending").subquery()
                )
            )
        ).scalar_one()

        resolved = (
            await session.execute(
                select(func.count()).select_from(
                    base.where(HITLQueue.status == "resolved").subquery()
                )
            )
        ).scalar_one()

        expired = (
            await session.execute(
                select(func.count()).select_from(
                    base.where(HITLQueue.status == "expired").subquery()
                )
            )
        ).scalar_one()

    total = pending + resolved + expired
    text = (
        f"<b>HITL Stats — Today</b>\n"
        f"{'=' * 25}\n"
        f"Pending:  {pending}\n"
        f"Resolved: {resolved}\n"
        f"Expired:  {expired}\n"
        f"Total:    {total}"
    )

    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# /approve {id} — Resolve HITL item as approved
# ---------------------------------------------------------------------------


@require_linked_account
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resolve a HITL item with ``resolution=approve``."""
    await _resolve_command(update, context, resolution="approve")


# ---------------------------------------------------------------------------
# /skip {id} — Resolve HITL item as skipped
# ---------------------------------------------------------------------------


@require_linked_account
async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resolve a HITL item with ``resolution=skip``."""
    await _resolve_command(update, context, resolution="skip")


# ---------------------------------------------------------------------------
# Shared resolution logic
# ---------------------------------------------------------------------------


async def _resolve_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    resolution: str,
) -> None:
    """Shared logic for /approve and /skip commands."""
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"Usage: /{resolution} &lt;hitl-item-id&gt;"
        )
        return

    # Parse UUID
    try:
        hitl_id = uuid.UUID(args[0])
    except ValueError:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"Invalid ID format. Expected a UUID, got: {_esc(args[0])}",
            parse_mode=ParseMode.HTML,
        )
        return

    user: User = context.user_data["mas_user"]  # type: ignore[assignment]
    now = datetime.now(UTC)

    async with get_db_session() as session:
        stmt = select(HITLQueue).where(HITLQueue.id == hitl_id)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()

        if item is None:
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                f"HITL item <code>{hitl_id}</code> not found.",
                parse_mode=ParseMode.HTML,
            )
            return

        if item.status != "pending":
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                f"Item is already <b>{_esc(item.status)}</b> "
                f"(resolution: {_esc(item.resolution or 'n/a')}).",
                parse_mode=ParseMode.HTML,
            )
            return

        # Check if action is valid for this item
        if resolution not in item.available_actions:
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                f"Action <b>{resolution}</b> is not available for this item.\n"
                f"Available: {', '.join(item.available_actions)}",
                parse_mode=ParseMode.HTML,
            )
            return

        # Apply resolution
        item.status = "resolved"
        item.resolution = resolution
        item.resolved_by = user.id
        item.resolved_at = now

    logger.info(
        "telegram.hitl_resolved",
        hitl_id=str(hitl_id),
        resolution=resolution,
        resolved_by=str(user.id),
    )

    emoji = "\u2705" if resolution == "approve" else "\u23ed\ufe0f"
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        f"{emoji} Item <code>{hitl_id}</code> — <b>{resolution}d</b>.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_link_code(length: int = 6) -> str:
    """Generate a random alphanumeric link code."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _esc(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
