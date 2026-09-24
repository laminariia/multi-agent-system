"""Entry point for the Telegram bot process.

Run with: ``python -m src.bot`` from the project root.

Supports two modes controlled by ``TELEGRAM_WEBHOOK_URL``:
- **Polling** (default): ``app.run_polling()``
- **Webhook**: registers webhook with Telegram and waits for HTTP updates
  via the Litestar ``/api/v1/telegram/webhook`` endpoint.
"""

import asyncio

from src.bot.handler import (
    build_webhook_url,
    create_bot_application,
    get_bot_mode,
    register_webhook,
)
from src.core.config import get_settings


def main() -> None:
    mode = get_bot_mode()
    app = create_bot_application()

    if mode == "webhook":
        settings = get_settings()
        webhook_url = build_webhook_url(settings.TELEGRAM_WEBHOOK_URL)
        secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        max_conn = getattr(settings, "TELEGRAM_WEBHOOK_MAX_CONNECTIONS", 100)

        # Register webhook then run the updater in webhook mode
        async def _setup_and_run() -> None:
            async with app:
                await app.initialize()
                await register_webhook(
                    app.bot,
                    webhook_url=webhook_url,
                    secret_token=secret,
                    max_connections=max_conn,
                )
                await app.start()
                # Keep alive until interrupted
                stop_event = asyncio.Event()
                try:
                    await stop_event.wait()
                except asyncio.CancelledError:
                    pass
                finally:
                    await app.stop()

        asyncio.run(_setup_and_run())
    else:
        app.run_polling(
            allowed_updates=["message", "callback_query"],
            bootstrap_retries=3,
        )


if __name__ == "__main__":
    main()
