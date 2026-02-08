"""Application builder — wires up all handlers into a python-telegram-bot Application."""

from __future__ import annotations

import structlog
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from src.bot.commands import (
    approve_command,
    pending_command,
    skip_command,
    start_command,
    stats_command,
    status_command,
)
from src.bot.keyboards import button_callback
from src.core.config import get_settings

logger = structlog.get_logger(__name__)


def create_bot_application() -> Application:
    """Build and return a configured ``telegram.ext.Application``.

    Registers all command handlers and the inline-keyboard callback handler.
    The bot token is read from ``Settings.TELEGRAM_BOT_TOKEN``.
    """
    settings = get_settings()
    token = settings.TELEGRAM_BOT_TOKEN

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Add it to .env or export it as an environment variable."
        )

    builder = Application.builder().token(token)
    app = builder.build()

    # -- Command handlers --------------------------------------------------
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("skip", skip_command))

    # -- Inline-keyboard callback handler ----------------------------------
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("telegram_bot.created", handlers=6)
    return app
