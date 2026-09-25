"""Unit tests for LLMRateLimiter (src/core/llm_rate_limiter.py).

Tests cover: under-limit pass, at-limit blocking, window reset,
per-provider isolation, and provider limits registry.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.llm_rate_limiter import (
    PROVIDER_LIMITS,
    LLMRateLimiter,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valkey_mock(current_count: int = 0) -> AsyncMock:
    """Create a mock Valkey client with configurable counter state."""
    client = AsyncMock()
    client.incr = AsyncMock(return_value=current_count + 1)
    client.expire = AsyncMock()
    client.get = AsyncMock(return_value=str(current_count).encode() if current_count else None)
    client.decr = AsyncMock(return_value=max(0, current_count - 1))
    return client


# ---------------------------------------------------------------------------
# Under limit
# ---------------------------------------------------------------------------


class TestLLMRateLimiterAllow:
    """Requests under the RPM limit should pass immediately."""

    async def test_allows_under_limit(self):
        valkey = _valkey_mock(current_count=50)
        limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
        # incr returns 51 (under 200)
        await limiter.acquire()
        valkey.incr.assert_called_once()

    async def test_sets_expire_on_new_window(self):
        valkey = _valkey_mock(current_count=0)
        # First request in window — incr returns 1
        valkey.incr = AsyncMock(return_value=1)
        limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
        await limiter.acquire()
        # Should set TTL on the key (60 seconds for 1-minute window)
        valkey.expire.assert_called_once()

    async def test_does_not_reset_expire_mid_window(self):
        valkey = _valkey_mock(current_count=10)
        # Mid-window request — incr returns > 1
        valkey.incr = AsyncMock(return_value=11)
        limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
        await limiter.acquire()
        # Should NOT set expire again (key already has TTL)
        valkey.expire.assert_not_called()


# ---------------------------------------------------------------------------
# At limit — blocking
# ---------------------------------------------------------------------------


class TestLLMRateLimiterBlock:
    """Requests at/over the RPM limit should wait."""

    async def test_waits_when_at_limit(self):
        valkey = AsyncMock()
        # First call: over limit; second call: under limit (after wait)
        valkey.incr = AsyncMock(side_effect=[201, 1])
        valkey.expire = AsyncMock()
        valkey.decr = AsyncMock()

        limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
        # Should eventually succeed (after internal retry)
        await asyncio.wait_for(limiter.acquire(), timeout=5.0)
        # Should have decremented the over-limit increment and retried
        assert valkey.incr.call_count >= 2

    async def test_over_limit_decrements_and_retries(self):
        valkey = AsyncMock()
        # Over limit, then under limit
        valkey.incr = AsyncMock(side_effect=[201, 50])
        valkey.expire = AsyncMock()
        valkey.decr = AsyncMock()

        limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
        await asyncio.wait_for(limiter.acquire(), timeout=5.0)
        # Should have decremented after the first over-limit attempt
        valkey.decr.assert_called_once()


# ---------------------------------------------------------------------------
# Provider isolation
# ---------------------------------------------------------------------------


class TestLLMRateLimiterIsolation:
    """Each provider has independent rate limit windows."""

    async def test_different_providers_use_different_keys(self):
        valkey_or = _valkey_mock()
        valkey_oai = _valkey_mock()

        limiter_or = LLMRateLimiter(valkey_or, provider="openrouter", rpm_limit=200)
        limiter_oai = LLMRateLimiter(valkey_oai, provider="openai", rpm_limit=500)

        await limiter_or.acquire()
        await limiter_oai.acquire()

        or_key = valkey_or.incr.call_args[0][0]
        oai_key = valkey_oai.incr.call_args[0][0]
        assert or_key != oai_key
        assert "openrouter" in or_key
        assert "openai" in oai_key


# ---------------------------------------------------------------------------
# Provider limits registry
# ---------------------------------------------------------------------------


class TestProviderLimitsRegistry:
    """Verify default provider limits are defined."""

    def test_openrouter_limit(self):
        assert PROVIDER_LIMITS["openrouter"] == 200

    def test_openai_limit(self):
        assert PROVIDER_LIMITS["openai"] == 500

    def test_all_limits_positive(self):
        for provider, limit in PROVIDER_LIMITS.items():
            assert limit > 0, f"{provider} has non-positive limit"


# ---------------------------------------------------------------------------
# Window key format
# ---------------------------------------------------------------------------


class TestLLMRateLimiterWindowKey:
    """Verify window key includes provider and minute granularity."""

    def test_window_key_contains_provider(self):
        valkey = _valkey_mock()
        limiter = LLMRateLimiter(valkey, provider="openrouter", rpm_limit=200)
        key = limiter._window_key()
        assert "openrouter" in key

    def test_window_key_contains_minute(self):
        valkey = _valkey_mock()
        limiter = LLMRateLimiter(valkey, provider="openai", rpm_limit=500)
        key = limiter._window_key()
        # Should contain time component (YYYY-MM-DDTHH:MM or similar)
        assert ":" in key  # time separator
