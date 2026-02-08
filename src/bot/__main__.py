"""Entry point for the Telegram bot process.

Run with: ``python -m src.bot`` from the project root.
"""
from src.bot.handler import create_bot_application


def main() -> None:
    app = create_bot_application()
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
