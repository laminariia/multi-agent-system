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
from src.bot.orchestrator_commands import (
    add_goal_command,
    goals_command,
    health_command,
    logs_command,
    milestones_command,
    orch_button_callback,
    orch_command,
    run_command,
    stop_command,
)
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

    # -- HITL command handlers -----------------------------------------------
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("skip", skip_command))

    # -- Orchestrator command handlers ----------------------------------------
    app.add_handler(CommandHandler("run", run_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("orch", orch_command))
    app.add_handler(CommandHandler("goals", goals_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("milestones", milestones_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("add_goal", add_goal_command))

    # -- Inline-keyboard callback handlers ---------------------------------
    app.add_handler(CallbackQueryHandler(orch_button_callback, pattern=r"^orch:"))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("telegram_bot.created", handlers=16)
    return app
