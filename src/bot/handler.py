"""Application builder — wires up all handlers into a python-telegram-bot Application.

Supports two modes:
- **Polling** (default): ``app.run_polling()`` — the bot actively polls Telegram.
- **Webhook**: set ``TELEGRAM_WEBHOOK_URL`` to receive updates via HTTP POST
  at ``/api/v1/telegram/webhook``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from src.bot.commands import (
    approve_command,
    pending_command,
    scan_command,
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
from src.bot.rate_limiter import TelegramRateLimiter
from src.core.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Webhook helpers
# ---------------------------------------------------------------------------

_WEBHOOK_PATH = "/api/v1/telegram/webhook"
_ALLOWED_UPDATES = ["message", "callback_query"]


def get_bot_mode() -> str:
    """Return the bot operating mode: ``"polling"`` or ``"webhook"``.

    Determined by the ``TELEGRAM_WEBHOOK_URL`` setting. When set, the bot
    expects updates via POST to the webhook endpoint instead of polling.
    """
    settings = get_settings()
    webhook_url = getattr(settings, "TELEGRAM_WEBHOOK_URL", "")
    if webhook_url:
        return "webhook"
    return "polling"


def build_webhook_url(base_url: str) -> str:
    """Build the full webhook URL from a base URL.

    Args:
        base_url: The base URL of the Litestar API (e.g. ``https://api.example.com``).

    Returns:
        Full webhook URL with the ``/api/v1/telegram/webhook`` path appended.

    Raises:
        ValueError: If the base URL does not use HTTPS.
    """
    stripped = base_url.rstrip("/")
    if not stripped.lower().startswith("https://"):
        msg = f"Webhook base URL must use HTTPS, got: {base_url}"
        raise ValueError(msg)
    return stripped + _WEBHOOK_PATH


async def register_webhook(
    bot: Any,
    *,
    webhook_url: str,
    secret_token: str = "",
    max_connections: int = 100,
) -> bool:
    """Register a webhook URL with Telegram.

    Args:
        bot: The ``telegram.Bot`` instance.
        webhook_url: Full URL for the webhook endpoint.
        secret_token: Secret token for request validation.
        max_connections: Maximum concurrent connections from Telegram.

    Returns:
        ``True`` if the webhook was set successfully, ``False`` otherwise.
    """
    try:
        result = await bot.set_webhook(
            url=webhook_url,
            secret_token=secret_token,
            max_connections=max_connections,
            allowed_updates=_ALLOWED_UPDATES,
        )
        logger.info(
            "telegram_bot.webhook_registered",
            url=webhook_url,
            max_connections=max_connections,
            result=result,
        )
        return bool(result)
    except Exception:
        logger.error("telegram_bot.webhook_registration_failed", exc_info=True)
        return False


async def unregister_webhook(bot: Any) -> bool:
    """Remove the webhook so the bot can switch back to polling.

    Args:
        bot: The ``telegram.Bot`` instance.

    Returns:
        ``True`` if the webhook was deleted successfully, ``False`` otherwise.
    """
    try:
        result = await bot.delete_webhook()
        logger.info("telegram_bot.webhook_unregistered", result=result)
        return bool(result)
    except Exception:
        logger.error("telegram_bot.webhook_unregister_failed", exc_info=True)
        return False


def create_bot_application() -> Application:
    """Build and return a configured ``telegram.ext.Application``.

    Registers all command handlers and the inline-keyboard callback handler.
    The bot token is read from ``Settings.TELEGRAM_BOT_TOKEN``.
    """
    settings = get_settings()
    token = settings.TELEGRAM_BOT_TOKEN

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Add it to .env or export it as an environment variable.")

    builder = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
    )
    app = builder.build()

    # -- Rate limiter (Valkey-backed, per-user per-command) -------------------
    try:
        from src.core.database import get_valkey  # noqa: PLC0415

        _rate_limiter = TelegramRateLimiter(get_valkey())
    except Exception:
        _rate_limiter = None
        logger.warning("telegram_bot.rate_limiter_init_failed", exc_info=True)

    # -- HITL command handlers -----------------------------------------------
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("skip", skip_command))
    app.add_handler(CommandHandler("scan", scan_command))

    # Store rate limiter in bot_data for command handlers to use.
    app.bot_data["rate_limiter"] = _rate_limiter

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

    logger.info("telegram_bot.created", handlers=17)

    # -- Register bot menu commands (visible in Telegram UI) -------------------
    async def _post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Welcome + command list"),
                BotCommand("status", "Agent health overview"),
                BotCommand("pending", "Pending HITL items"),
                BotCommand("stats", "Today's HITL statistics"),
                BotCommand("orch", "Orchestrator control panel"),
                BotCommand("run", "Start orchestrator session"),
                BotCommand("stop", "Stop orchestrator"),
                BotCommand("goals", "View goal queue"),
                BotCommand("health", "System health check"),
                BotCommand("milestones", "Project milestones"),
                BotCommand("logs", "Recent session logs"),
                BotCommand("scan", "Pipeline B geo scan"),
                BotCommand("add_goal", "Add a new goal"),
                BotCommand("approve", "Approve HITL item"),
                BotCommand("skip", "Skip HITL item"),
            ]
        )
        logger.info("telegram_bot.commands_registered")

    app.post_init = _post_init

    # -- Dashboard sync: subscribe to Valkey channels for notifications ------
    async def _dashboard_sync_listener(application: Application) -> None:
        """Background task: listen for dashboard events via Valkey pub/sub."""
        from src.core.config import get_settings
        from src.core.database import get_valkey

        bot_settings = get_settings()
        chat_id = bot_settings.TELEGRAM_CHAT_ID
        if not chat_id:
            logger.warning("telegram_bot.no_chat_id", msg="TELEGRAM_CHAT_ID not set, skipping dashboard sync")
            return

        valkey = get_valkey()
        pubsub = valkey.pubsub()
        await pubsub.subscribe("hitl:resolved:bot", "orch:event:bot")
        logger.info("telegram_bot.dashboard_sync_started", channels=["hitl:resolved:bot", "orch:event:bot"])

        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        channel = message["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode()

                        if channel == "hitl:resolved:bot":
                            text = (
                                f"\U0001f4cb HITL resolved from dashboard\n\n"
                                f"Type: {data.get('type', '?')}\n"
                                f"Title: {data.get('title', '?')}\n"
                                f"Action: {data.get('action', '?')}\n"
                                f"Next: {data.get('next_action', '?')}\n"
                                f"By: {data.get('resolved_by', '?')}"
                            )
                            await application.bot.send_message(chat_id=chat_id, text=text)

                        elif channel == "orch:event:bot":
                            event_type = data.get("event", "unknown")
                            text = f"\U0001f916 Orchestrator event: {event_type}\n{data.get('message', '')}"
                            await application.bot.send_message(chat_id=chat_id, text=text)

                    except Exception:
                        logger.warning("telegram_bot.sync_message_error", exc_info=True)

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("telegram_bot.dashboard_sync_stopped")
        except Exception:
            logger.error("telegram_bot.dashboard_sync_error", exc_info=True)
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    async def _post_startup(application: Application) -> None:
        """Start the dashboard sync listener as a background task."""
        application.bot_data["_sync_task"] = asyncio.create_task(
            _dashboard_sync_listener(application),
            name="dashboard-sync",
        )
        logger.info("telegram_bot.sync_task_scheduled")

    app.post_init = _post_init
    app.post_shutdown = lambda app: app.bot_data.get("_sync_task") and app.bot_data["_sync_task"].cancel()

    # Schedule sync listener after bot starts polling
    original_post_init = _post_init

    async def _combined_post_init(application: Application) -> None:
        await original_post_init(application)
        await _post_startup(application)

    app.post_init = _combined_post_init

    return app
