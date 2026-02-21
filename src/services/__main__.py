"""Allow running the telegram listener via ``python -m src.services.telegram_listener``."""

from __future__ import annotations

import asyncio

from src.services.telegram_listener import _main

if __name__ == "__main__":
    asyncio.run(_main())
