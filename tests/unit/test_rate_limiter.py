"""Unit tests for AdaptiveRateLimiter and retry_on_transient decorator."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from src.adapters.rate_limiter import (
    _BACKOFF_FACTOR,
    _DEFAULT_LIMITS,
    _SUCCESS_INCREASE,
    AdaptiveRateLimiter,
    retry_on_transient,
)
from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
    PlatformRateLimitError,
)

# ---------------------------------------------------------------------------
# AdaptiveRateLimiter basics
# ---------------------------------------------------------------------------


class TestAdaptiveRateLimiterInit:
    """Tests for initialization and state management."""

    def test_creates_state_for_known_platform(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        assert state.current_rpm == _DEFAULT_LIMITS["freelancer"]["initial_rpm"]
        assert state.max_rpm == _DEFAULT_LIMITS["freelancer"]["max_rpm"]
        assert state.min_rpm == _DEFAULT_LIMITS["freelancer"]["min_rpm"]

    def test_creates_state_for_unknown_platform(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("unknown_platform")
        assert state.current_rpm == 10.0  # default
        assert state.max_rpm == 30.0

    def test_same_state_returned_on_second_call(self) -> None:
        limiter = AdaptiveRateLimiter()
        s1 = limiter._get_state("fl_ru")
        s2 = limiter._get_state("fl_ru")
        assert s1 is s2

    def test_different_platforms_have_separate_states(self) -> None:
        limiter = AdaptiveRateLimiter()
        s1 = limiter._get_state("freelancer")
        s2 = limiter._get_state("kwork")
        assert s1 is not s2


class TestAcquire:
    """Tests for the acquire method."""

    @pytest.mark.asyncio
    async def test_acquire_succeeds_normally(self) -> None:
        limiter = AdaptiveRateLimiter()
        await limiter.acquire("freelancer")
        state = limiter._get_state("freelancer")
        assert state.total_requests == 1

    @pytest.mark.asyncio
    async def test_acquire_raises_when_paused(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        state.paused_until = time.monotonic() + 1000  # paused for 1000s

        with pytest.raises(PlatformBannedError, match="paused"):
            await limiter.acquire("freelancer")

    @pytest.mark.asyncio
    async def test_acquire_allowed_after_pause_expires(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        state.paused_until = time.monotonic() - 1  # already expired

        await limiter.acquire("freelancer")
        assert state.total_requests == 1


class TestRecordSuccess:
    """Tests for recording successful requests."""

    @pytest.mark.asyncio
    async def test_increments_consecutive_successes(self) -> None:
        limiter = AdaptiveRateLimiter()
        await limiter.record_success("freelancer")
        state = limiter._get_state("freelancer")
        assert state.consecutive_successes == 1
        assert state.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_resets_consecutive_failures(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        state.consecutive_failures = 5
        await limiter.record_success("freelancer")
        assert state.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_increases_rate_every_5_successes(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        initial_rpm = state.current_rpm
        for _ in range(5):
            await limiter.record_success("freelancer")
        assert state.current_rpm == pytest.approx(initial_rpm * _SUCCESS_INCREASE)

    @pytest.mark.asyncio
    async def test_rate_capped_at_max(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        state.current_rpm = state.max_rpm  # already at max
        for _ in range(10):
            await limiter.record_success("freelancer")
        assert state.current_rpm == state.max_rpm


class TestRecordRateLimit:
    """Tests for recording rate-limit responses."""

    @pytest.mark.asyncio
    async def test_halves_rate(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        initial_rpm = state.current_rpm
        await limiter.record_rate_limit("freelancer")
        assert state.current_rpm == pytest.approx(initial_rpm * _BACKOFF_FACTOR)

    @pytest.mark.asyncio
    async def test_rate_floored_at_min(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        state.current_rpm = state.min_rpm
        await limiter.record_rate_limit("freelancer")
        assert state.current_rpm == state.min_rpm

    @pytest.mark.asyncio
    async def test_resets_consecutive_successes(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        state.consecutive_successes = 10
        await limiter.record_rate_limit("freelancer")
        assert state.consecutive_successes == 0

    @pytest.mark.asyncio
    async def test_increments_429_counter(self) -> None:
        limiter = AdaptiveRateLimiter()
        await limiter.record_rate_limit("freelancer")
        state = limiter._get_state("freelancer")
        assert state.total_429s == 1


class TestRecordBan:
    """Tests for recording ban/captcha events."""

    @pytest.mark.asyncio
    async def test_sets_pause(self) -> None:
        limiter = AdaptiveRateLimiter()
        await limiter.record_ban("freelancer", reason="captcha")
        state = limiter._get_state("freelancer")
        assert state.paused_until > time.monotonic()
        assert state.total_bans == 1
        assert state.current_rpm == state.min_rpm

    @pytest.mark.asyncio
    async def test_sends_telegram_alert(self) -> None:
        notify = AsyncMock()
        limiter = AdaptiveRateLimiter(telegram_notify=notify)
        await limiter.record_ban("freelancer", reason="captcha detected")
        notify.assert_awaited_once()
        msg = notify.call_args[0][0]
        assert "freelancer" in msg
        assert "captcha detected" in msg

    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_raise(self) -> None:
        notify = AsyncMock(side_effect=RuntimeError("network error"))
        limiter = AdaptiveRateLimiter(telegram_notify=notify)
        # Should not raise despite telegram failure.
        await limiter.record_ban("freelancer", reason="test")


class TestRecordTimeout:
    """Tests for recording timeout events."""

    @pytest.mark.asyncio
    async def test_treats_like_rate_limit(self) -> None:
        limiter = AdaptiveRateLimiter()
        state = limiter._get_state("freelancer")
        initial_rpm = state.current_rpm
        await limiter.record_timeout("freelancer")
        assert state.current_rpm == pytest.approx(initial_rpm * _BACKOFF_FACTOR)
        assert state.consecutive_failures == 1


class TestGetStats:
    """Tests for stats reporting."""

    def test_returns_stats_dict(self) -> None:
        limiter = AdaptiveRateLimiter()
        stats = limiter.get_stats("freelancer")
        assert stats["platform"] == "freelancer"
        assert "current_rpm" in stats
        assert "is_paused" in stats
        assert stats["is_paused"] is False


# ---------------------------------------------------------------------------
# Valkey persistence
# ---------------------------------------------------------------------------


class TestValkeyPersistence:
    """Tests for Valkey-backed state persistence."""

    @pytest.mark.asyncio
    async def test_save_state_calls_hset(self) -> None:
        valkey = AsyncMock()
        limiter = AdaptiveRateLimiter(valkey=valkey)
        limiter._get_state("freelancer")
        await limiter._save_state("freelancer")
        valkey.hset.assert_awaited_once()
        valkey.expire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_state_handles_valkey_error(self) -> None:
        valkey = AsyncMock()
        valkey.hset.side_effect = ConnectionError("valkey down")
        limiter = AdaptiveRateLimiter(valkey=valkey)
        limiter._get_state("freelancer")
        # Should not raise.
        await limiter._save_state("freelancer")

    @pytest.mark.asyncio
    async def test_load_state_restores_values(self) -> None:
        valkey = AsyncMock()
        valkey.hgetall.return_value = {
            "current_rpm": "8.5",
            "consecutive_successes": "3",
            "consecutive_failures": "1",
            "paused_until": "0.0",
            "total_requests": "100",
            "total_429s": "5",
            "total_bans": "1",
        }
        limiter = AdaptiveRateLimiter(valkey=valkey)
        await limiter._load_state("freelancer")
        state = limiter._get_state("freelancer")
        assert state.current_rpm == 8.5
        assert state.consecutive_successes == 3
        assert state.total_requests == 100

    @pytest.mark.asyncio
    async def test_load_state_handles_empty_data(self) -> None:
        valkey = AsyncMock()
        valkey.hgetall.return_value = {}
        limiter = AdaptiveRateLimiter(valkey=valkey)
        await limiter._load_state("freelancer")
        # State should remain at defaults.
        state = limiter._get_state("freelancer")
        assert state.current_rpm == _DEFAULT_LIMITS["freelancer"]["initial_rpm"]

    @pytest.mark.asyncio
    async def test_load_state_handles_valkey_error(self) -> None:
        valkey = AsyncMock()
        valkey.hgetall.side_effect = ConnectionError("valkey down")
        limiter = AdaptiveRateLimiter(valkey=valkey)
        # Should not raise.
        await limiter._load_state("freelancer")

    @pytest.mark.asyncio
    async def test_load_state_clamps_to_bounds(self) -> None:
        valkey = AsyncMock()
        valkey.hgetall.return_value = {
            "current_rpm": "999",  # way above max
            "consecutive_successes": "0",
            "consecutive_failures": "0",
            "paused_until": "0",
            "total_requests": "0",
            "total_429s": "0",
            "total_bans": "0",
        }
        limiter = AdaptiveRateLimiter(valkey=valkey)
        await limiter._load_state("freelancer")
        state = limiter._get_state("freelancer")
        assert state.current_rpm == state.max_rpm


# ---------------------------------------------------------------------------
# retry_on_transient decorator
# ---------------------------------------------------------------------------


class TestRetryOnTransient:
    """Tests for the retry_on_transient decorator."""

    @pytest.mark.asyncio
    async def test_returns_on_first_success(self) -> None:
        calls = 0

        @retry_on_transient(max_retries=3)
        async def good_func() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        result = await good_func()
        assert result == "ok"
        assert calls == 1

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit(self) -> None:
        calls = 0

        @retry_on_transient(max_retries=3, initial_delay=0.01)
        async def flaky_func() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise PlatformRateLimitError(platform="test", operation="test")
            return "ok"

        result = await flaky_func()
        assert result == "ok"
        assert calls == 3

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self) -> None:
        calls = 0

        @retry_on_transient(max_retries=2, initial_delay=0.01)
        async def timeout_func() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise TimeoutError("timed out")
            return "ok"

        result = await timeout_func()
        assert result == "ok"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        @retry_on_transient(max_retries=2, initial_delay=0.01)
        async def always_fails() -> str:
            raise PlatformRateLimitError(platform="test", operation="test")

        with pytest.raises(PlatformRateLimitError):
            await always_fails()

    @pytest.mark.asyncio
    async def test_does_not_retry_on_ban(self) -> None:
        calls = 0

        @retry_on_transient(max_retries=3)
        async def banned_func() -> str:
            nonlocal calls
            calls += 1
            raise PlatformBannedError(platform="test", operation="test")

        with pytest.raises(PlatformBannedError):
            await banned_func()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_on_captcha(self) -> None:
        calls = 0

        @retry_on_transient(max_retries=3)
        async def captcha_func() -> str:
            nonlocal calls
            calls += 1
            raise CaptchaDetectedError(platform="test", operation="test")

        with pytest.raises(CaptchaDetectedError):
            await captcha_func()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_on_cloudflare(self) -> None:
        calls = 0

        @retry_on_transient(max_retries=3)
        async def cf_func() -> str:
            nonlocal calls
            calls += 1
            raise CloudflareBlockError(platform="test", operation="test")

        with pytest.raises(CloudflareBlockError):
            await cf_func()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_records_success_with_rate_limiter(self) -> None:
        limiter = AdaptiveRateLimiter()

        @retry_on_transient(max_retries=1, rate_limiter=limiter, platform="freelancer")
        async def good_func() -> str:
            return "ok"

        await good_func()
        state = limiter._get_state("freelancer")
        assert state.consecutive_successes == 1

    @pytest.mark.asyncio
    async def test_records_rate_limit_with_limiter(self) -> None:
        limiter = AdaptiveRateLimiter()
        calls = 0

        @retry_on_transient(max_retries=1, initial_delay=0.01, rate_limiter=limiter, platform="freelancer")
        async def flaky_func() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise PlatformRateLimitError(platform="freelancer", operation="test")
            return "ok"

        await flaky_func()
        state = limiter._get_state("freelancer")
        assert state.total_429s == 1

    @pytest.mark.asyncio
    async def test_records_ban_with_limiter(self) -> None:
        limiter = AdaptiveRateLimiter()

        @retry_on_transient(max_retries=3, rate_limiter=limiter, platform="kwork")
        async def banned_func() -> str:
            raise PlatformBannedError(platform="kwork", operation="test")

        with pytest.raises(PlatformBannedError):
            await banned_func()
        state = limiter._get_state("kwork")
        assert state.total_bans == 1
        assert state.paused_until > time.monotonic()

    @pytest.mark.asyncio
    async def test_uses_retry_after_from_exception(self) -> None:
        """Should use retry_after_seconds from exception if available."""
        calls = 0

        @retry_on_transient(max_retries=2, initial_delay=10.0)
        async def flaky_func() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise PlatformRateLimitError(
                    platform="test",
                    operation="test",
                    retry_after_seconds=0.01,  # very short
                )
            return "ok"

        result = await flaky_func()
        assert result == "ok"
        assert calls == 2
