"""Valkey-backed per-user per-command rate limiting for Telegram bot commands.

Prevents abuse by enforcing RPM (requests per minute) quotas per user per
command. Uses Valkey INCR + EXPIRE sliding window (same pattern as
``src/core/llm_rate_limiter.py``).

Usage::

    limiter = TelegramRateLimiter(valkey_client)
    allowed = await limiter.check(user_id=12345, command="scan")
    if not allowed:
        await update.effective_message.reply_text(limiter.get_throttle_message("scan"))

Spec reference: ``docs/Full_work/specs/telegram-bot-spec.md`` (Rate Limiting section).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Default limits per command
# ---------------------------------------------------------------------------

DEFAULT_GENERAL_LIMIT = 30  # 30 cmd/min per user (general, per spec)
DEFAULT_SCAN_LIMIT = 5  # 5 cmd/min for /scan

_DEFAULT_COMMAND_LIMITS: dict[str, int] = {
    "scan": DEFAULT_SCAN_LIMIT,
}

_WINDOW_SECONDS = 60


# ---------------------------------------------------------------------------
# TelegramRateLimiter
# ---------------------------------------------------------------------------


class TelegramRateLimiter:
    """Per-user per-command rate limiter using Valkey INCR + EXPIRE.

    Args:
        valkey_client: Async Valkey/Redis client.
        command_limits: Optional dict of command -> max requests per minute.
            Overrides the defaults for specified commands.
        general_limit: Default limit for commands not in ``command_limits``.
    """

    def __init__(
        self,
        valkey_client: Any,
        *,
        command_limits: dict[str, int] | None = None,
        general_limit: int = DEFAULT_GENERAL_LIMIT,
    ) -> None:
        self._valkey = valkey_client
        self._general_limit = general_limit
        self._command_limits: dict[str, int] = {**_DEFAULT_COMMAND_LIMITS}
        if command_limits:
            self._command_limits.update(command_limits)

    def _key(self, user_id: int, command: str) -> str:
        """Generate a Valkey key for the rate limit counter."""
        return f"tg_rate:{user_id}:{command}"

    def _limit_for(self, command: str) -> int:
        """Return the RPM limit for a given command."""
        return self._command_limits.get(command, self._general_limit)

    async def check(self, user_id: int, command: str) -> bool:
        """Check whether a command from a user is allowed.

        Returns:
            ``True`` if the command is within the rate limit, ``False`` otherwise.
        """
        key = self._key(user_id, command)
        limit = self._limit_for(command)

        count = await self._valkey.incr(key)

        # First request in this window -- set TTL
        if count == 1:
            await self._valkey.expire(key, _WINDOW_SECONDS)

        if count <= limit:
            return True

        # Over limit -- roll back the increment to keep counter accurate
        await self._valkey.decr(key)
        logger.debug(
            "telegram.rate_limited",
            user_id=user_id,
            command=command,
            count=count,
            limit=limit,
        )
        return False

    def get_throttle_message(self, command: str) -> str:
        """Return a user-friendly throttle message for the given command."""
        limit = self._limit_for(command)
        return f"Too many requests. Rate limit: {limit} commands per minute. Please wait before trying again."


# ---------------------------------------------------------------------------
# Decorator for easy integration with command handlers
# ---------------------------------------------------------------------------


def rate_limited(
    limiter: TelegramRateLimiter,
    *,
    command_name: str,
) -> Callable:
    """Decorator that wraps a telegram command handler with rate limiting.

    If the user exceeds the rate limit, a throttle message is sent and
    the handler is not called.

    Usage::

        @rate_limited(limiter, command_name="scan")
        async def scan_command(update, context):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Any, context: Any) -> Any:
            user_id = update.effective_user.id
            allowed = await limiter.check(user_id=user_id, command=command_name)
            if not allowed:
                msg = limiter.get_throttle_message(command_name)
                await update.effective_message.reply_text(msg)
                return None
            return await func(update, context)

        return wrapper

    return decorator
