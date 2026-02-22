"""Adaptive rate limiter with Valkey-backed state.

Adjusts request rates based on platform response signals:
- Success -> gradually increase allowed rate
- 429/timeout -> halve rate + exponential backoff
- Captcha/ban -> 30-min pause + Telegram alert

State is stored in Valkey so it survives process restarts.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import structlog

from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
    PlatformRateLimitError,
)

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default per-platform limits (requests per window).
_DEFAULT_LIMITS: dict[str, dict[str, float]] = {
    "freelancer": {"max_rpm": 30.0, "min_rpm": 2.0, "initial_rpm": 15.0},
    "fl_ru": {"max_rpm": 10.0, "min_rpm": 1.0, "initial_rpm": 5.0},
    "kwork": {"max_rpm": 6.0, "min_rpm": 1.0, "initial_rpm": 3.0},
    "upwork": {"max_rpm": 6.0, "min_rpm": 1.0, "initial_rpm": 3.0},
    "telegram": {"max_rpm": 60.0, "min_rpm": 5.0, "initial_rpm": 30.0},
}

# How long to pause after captcha/ban detection (seconds).
_BAN_PAUSE_SECONDS = 30 * 60  # 30 minutes

# Backoff multiplier on rate-limit / timeout.
_BACKOFF_FACTOR = 0.5

# Rate increase factor on success.
_SUCCESS_INCREASE = 1.1

# Valkey key prefix.
_VALKEY_PREFIX = "mas:rate_limiter"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class PlatformRateState:
    """In-memory rate state for a single platform."""

    current_rpm: float = 10.0
    max_rpm: float = 30.0
    min_rpm: float = 2.0
    last_request_ts: float = 0.0
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    paused_until: float = 0.0
    total_requests: int = 0
    total_429s: int = 0
    total_bans: int = 0


class AdaptiveRateLimiter:
    """Per-platform adaptive rate limiter with optional Valkey persistence.

    Parameters
    ----------
    valkey:
        Optional Valkey/Redis client for state persistence.  When ``None``,
        state is kept in-memory only (suitable for tests).
    telegram_notify:
        Optional async callable ``(message: str) -> None`` for ban/captcha
        alerts.  When ``None``, alerts are logged but not sent.
    """

    def __init__(
        self,
        valkey: Any | None = None,
        telegram_notify: Callable[..., Any] | None = None,
    ) -> None:
        self._valkey = valkey
        self._telegram_notify = telegram_notify
        self._states: dict[str, PlatformRateState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _get_state(self, platform: str) -> PlatformRateState:
        """Get or create in-memory state for *platform*."""
        if platform not in self._states:
            limits = _DEFAULT_LIMITS.get(platform, {})
            self._states[platform] = PlatformRateState(
                current_rpm=limits.get("initial_rpm", 10.0),
                max_rpm=limits.get("max_rpm", 30.0),
                min_rpm=limits.get("min_rpm", 2.0),
            )
        return self._states[platform]

    def _get_lock(self, platform: str) -> asyncio.Lock:
        if platform not in self._locks:
            self._locks[platform] = asyncio.Lock()
        return self._locks[platform]

    async def _save_state(self, platform: str) -> None:
        """Persist current state to Valkey (if available)."""
        if self._valkey is None:
            return
        state = self._get_state(platform)
        key = f"{_VALKEY_PREFIX}:{platform}"
        data = {
            "current_rpm": str(state.current_rpm),
            "consecutive_successes": str(state.consecutive_successes),
            "consecutive_failures": str(state.consecutive_failures),
            "paused_until": str(state.paused_until),
            "total_requests": str(state.total_requests),
            "total_429s": str(state.total_429s),
            "total_bans": str(state.total_bans),
        }
        try:
            await self._valkey.hset(key, mapping=data)
            await self._valkey.expire(key, 86400)  # 24h TTL
        except (OSError, ConnectionError):
            logger.debug("rate_limiter.valkey_save_failed", platform=platform, exc_info=True)

    async def _load_state(self, platform: str) -> None:
        """Load persisted state from Valkey (if available)."""
        if self._valkey is None:
            return
        key = f"{_VALKEY_PREFIX}:{platform}"
        try:
            data = await self._valkey.hgetall(key)
            if not data:
                return
            state = self._get_state(platform)

            # Valkey returns bytes or str depending on decode_responses.
            def _val(k: str, default: float = 0.0) -> float:
                v = data.get(k) or data.get(k.encode())
                if v is None:
                    return default
                return float(v if isinstance(v, str) else v.decode())

            state.current_rpm = max(
                state.min_rpm,
                min(state.max_rpm, _val("current_rpm", state.current_rpm)),
            )
            state.consecutive_successes = int(_val("consecutive_successes"))
            state.consecutive_failures = int(_val("consecutive_failures"))
            state.paused_until = _val("paused_until")
            state.total_requests = int(_val("total_requests"))
            state.total_429s = int(_val("total_429s"))
            state.total_bans = int(_val("total_bans"))
        except (OSError, ConnectionError, ValueError):
            logger.debug("rate_limiter.valkey_load_failed", platform=platform, exc_info=True)

    # ------------------------------------------------------------------
    # Wait / acquire
    # ------------------------------------------------------------------

    async def acquire(self, platform: str) -> None:
        """Wait until the next request is allowed for *platform*.

        Raises :class:`PlatformBannedError` if the platform is paused due to
        a ban/captcha and the pause hasn't expired yet.
        """
        lock = self._get_lock(platform)
        async with lock:
            state = self._get_state(platform)

            # Check ban pause.
            now = time.monotonic()
            if state.paused_until > now:
                remaining = state.paused_until - now
                logger.warning(
                    "rate_limiter.platform_paused",
                    platform=platform,
                    remaining_s=round(remaining),
                )
                raise PlatformBannedError(
                    message=f"Platform {platform} paused for {int(remaining)}s due to ban/captcha",
                    platform=platform,
                    operation="rate_limiter_acquire",
                )

            # Enforce minimum interval.
            if state.last_request_ts > 0 and state.current_rpm > 0:
                min_interval = 60.0 / state.current_rpm
                elapsed = now - state.last_request_ts
                if elapsed < min_interval:
                    wait_time = min_interval - elapsed
                    await asyncio.sleep(wait_time)

            state.last_request_ts = time.monotonic()
            state.total_requests += 1

    # ------------------------------------------------------------------
    # Signal feedback
    # ------------------------------------------------------------------

    async def record_success(self, platform: str) -> None:
        """Record a successful request — gradually increase rate."""
        state = self._get_state(platform)
        state.consecutive_successes += 1
        state.consecutive_failures = 0

        # Increase rate every 5 consecutive successes.
        if state.consecutive_successes % 5 == 0:
            new_rpm = min(state.current_rpm * _SUCCESS_INCREASE, state.max_rpm)
            if new_rpm != state.current_rpm:
                logger.debug(
                    "rate_limiter.rate_increased",
                    platform=platform,
                    old_rpm=round(state.current_rpm, 2),
                    new_rpm=round(new_rpm, 2),
                )
                state.current_rpm = new_rpm

        await self._save_state(platform)

    async def record_rate_limit(self, platform: str) -> None:
        """Record a 429/rate-limit — halve the rate."""
        state = self._get_state(platform)
        state.consecutive_successes = 0
        state.consecutive_failures += 1
        state.total_429s += 1

        new_rpm = max(state.current_rpm * _BACKOFF_FACTOR, state.min_rpm)
        logger.warning(
            "rate_limiter.rate_halved",
            platform=platform,
            old_rpm=round(state.current_rpm, 2),
            new_rpm=round(new_rpm, 2),
            consecutive_failures=state.consecutive_failures,
        )
        state.current_rpm = new_rpm
        await self._save_state(platform)

    async def record_ban(self, platform: str, reason: str = "") -> None:
        """Record a ban/captcha — pause for 30 min + alert."""
        state = self._get_state(platform)
        state.consecutive_successes = 0
        state.consecutive_failures += 1
        state.total_bans += 1
        state.paused_until = time.monotonic() + _BAN_PAUSE_SECONDS

        # Reset rate to minimum.
        state.current_rpm = state.min_rpm

        logger.error(
            "rate_limiter.ban_detected",
            platform=platform,
            reason=reason,
            pause_minutes=_BAN_PAUSE_SECONDS // 60,
        )

        # Telegram alert.
        if self._telegram_notify is not None:
            msg = (
                f"Platform {platform} ban/captcha detected.\n"
                f"Reason: {reason or 'unknown'}\n"
                f"Paused for {_BAN_PAUSE_SECONDS // 60} minutes.\n"
                f"Total bans: {state.total_bans}"
            )
            try:
                await self._telegram_notify(msg)
            except Exception:  # noqa: BLE001 — intentional: external callback may raise anything
                logger.debug("rate_limiter.telegram_notify_failed", exc_info=True)

        await self._save_state(platform)

    async def record_timeout(self, platform: str) -> None:
        """Record a timeout — treat similarly to rate-limit."""
        await self.record_rate_limit(platform)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_stats(self, platform: str) -> dict[str, Any]:
        """Return current rate limiter stats for *platform*."""
        state = self._get_state(platform)
        now = time.monotonic()
        return {
            "platform": platform,
            "current_rpm": round(state.current_rpm, 2),
            "max_rpm": state.max_rpm,
            "min_rpm": state.min_rpm,
            "consecutive_successes": state.consecutive_successes,
            "consecutive_failures": state.consecutive_failures,
            "is_paused": state.paused_until > now,
            "paused_remaining_s": max(0, round(state.paused_until - now)),
            "total_requests": state.total_requests,
            "total_429s": state.total_429s,
            "total_bans": state.total_bans,
        }


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def retry_on_transient(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    rate_limiter: AdaptiveRateLimiter | None = None,
    platform: str = "",
) -> Callable[[F], F]:
    """Decorator that retries on transient platform errors.

    Retries on:
    - ``PlatformRateLimitError`` (429)
    - ``asyncio.TimeoutError``
    - ``PlatformAPIError`` (non-fatal)

    Does NOT retry on:
    - ``PlatformBannedError``
    - ``CaptchaDetectedError``
    - ``CloudflareBlockError``
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            delay = initial_delay

            for attempt in range(max_retries + 1):
                try:
                    result = await func(*args, **kwargs)
                    # Record success if rate limiter is provided.
                    if rate_limiter is not None and platform:
                        await rate_limiter.record_success(platform)
                    return result

                except PlatformRateLimitError as exc:
                    last_exc = exc
                    if rate_limiter is not None and platform:
                        await rate_limiter.record_rate_limit(platform)
                    if attempt < max_retries:
                        wait = exc.retry_after_seconds if exc.retry_after_seconds else delay
                        logger.warning(
                            "retry_on_transient.rate_limit",
                            platform=platform,
                            attempt=attempt + 1,
                            wait_s=round(wait, 1),
                        )
                        await asyncio.sleep(wait)
                        delay *= backoff_factor
                        continue
                    raise

                except TimeoutError as exc:
                    last_exc = exc
                    if rate_limiter is not None and platform:
                        await rate_limiter.record_timeout(platform)
                    if attempt < max_retries:
                        logger.warning(
                            "retry_on_transient.timeout",
                            platform=platform,
                            attempt=attempt + 1,
                            wait_s=round(delay, 1),
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                        continue
                    raise

                except (PlatformBannedError, CaptchaDetectedError, CloudflareBlockError) as exc:
                    # Non-retryable — record ban and re-raise immediately.
                    if rate_limiter is not None and platform:
                        reason = str(exc)
                        await rate_limiter.record_ban(platform, reason=reason)
                    raise

            # Should not reach here, but just in case.
            if last_exc is not None:
                raise last_exc  # pragma: no cover

        return wrapper  # type: ignore[return-value]

    return decorator
