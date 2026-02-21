"""Telegram channel adapter — reads parsed jobs from a Valkey queue.

The :class:`TelegramListener` (a separate process) monitors Telegram channels
via Telethon, parses posts with an LLM, and pushes normalised job dicts into
the ``mas:tg_jobs:pending`` Valkey list.

This adapter implements the same ``fetch_jobs()`` interface as FL.ru / Kwork
adapters so the Scout agent can consume Telegram jobs transparently.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.adapters.rate_limiter import AdaptiveRateLimiter

logger = structlog.get_logger(__name__)

# Valkey key where the listener pushes parsed jobs.
VALKEY_QUEUE_KEY = "mas:tg_jobs:pending"


class TelegramChannelAdapter:
    """Adapter that pops parsed Telegram jobs from a Valkey queue.

    Parameters
    ----------
    valkey:
        ``redis.asyncio.Redis`` client connected to Valkey.
    rate_limiter:
        Optional adaptive rate limiter (platform key: ``"telegram"``).
    """

    def __init__(
        self,
        valkey: Any,
        rate_limiter: AdaptiveRateLimiter | None = None,
    ) -> None:
        self._valkey = valkey
        self._rate_limiter = rate_limiter
        self._log = logger.bind(platform="telegram")

    async def fetch_jobs(self, max_results: int = 50) -> list[dict[str, Any]]:
        """Pop up to *max_results* jobs from the Valkey queue.

        Each item is a JSON-serialised dict pushed by the Telegram listener.
        Returns a list of normalised job dicts matching the Scout adapter
        interface (``platform``, ``external_id``, ``title``, etc.).
        """
        if self._rate_limiter:
            await self._rate_limiter.acquire("telegram")

        jobs: list[dict[str, Any]] = []
        for _ in range(max_results):
            raw = await self._valkey.rpop(VALKEY_QUEUE_KEY)
            if raw is None:
                break
            try:
                job = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                jobs.append(job)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._log.warning("telegram_adapter.invalid_queue_item", error=str(exc))
                continue

        self._log.info("fetch_jobs_done", count=len(jobs))
        return jobs

    async def close(self) -> None:
        """No-op — Valkey client is owned by the DI container."""
