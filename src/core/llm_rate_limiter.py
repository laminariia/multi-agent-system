"""Per-provider LLM rate limiting via Valkey sliding window.

Prevents 429 errors from LLM providers by enforcing RPM (requests per minute)
quotas. Each provider has an independent counter with 60-second TTL windows.

Usage:
    limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
    await limiter.acquire()  # blocks if at limit
    response = await llm_call(...)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Default RPM limits per provider
PROVIDER_LIMITS: dict[str, int] = {
    "openrouter": 200,
    "openai": 500,
}

_WINDOW_SECONDS = 60
_RETRY_DELAY = 0.5  # seconds to wait before retrying when at limit


class LLMRateLimiter:
    """Per-provider rate limiter using Valkey INCR + EXPIRE.

    Args:
        valkey_client: Async Valkey/Redis client.
        provider: Provider name (e.g. "openrouter", "openai").
        rpm_limit: Max requests per minute for this provider.
    """

    def __init__(
        self,
        valkey_client: Any,
        *,
        provider: str,
        rpm_limit: int,
    ) -> None:
        self._valkey = valkey_client
        self._provider = provider
        self._rpm_limit = rpm_limit

    def _window_key(self) -> str:
        """Generate a Valkey key scoped to the current minute window."""
        now = datetime.now(UTC)
        minute_stamp = now.strftime("%Y-%m-%dT%H:%M")
        return f"llm_rate:{self._provider}:{minute_stamp}"

    async def acquire(self) -> None:
        """Wait until a request slot is available within the current window."""
        while True:
            key = self._window_key()
            count = await self._valkey.incr(key)

            # First request in this window — set TTL
            if count == 1:
                await self._valkey.expire(key, _WINDOW_SECONDS)

            if count <= self._rpm_limit:
                return  # slot acquired

            # Over limit — roll back the increment and wait
            await self._valkey.decr(key)
            logger.debug(
                "llm_rate.throttled",
                provider=self._provider,
                count=count,
                limit=self._rpm_limit,
            )
            await asyncio.sleep(_RETRY_DELAY)
