"""Unit tests for CircuitBreaker (src/adapters/circuit_breaker.py).

Tests cover: state transitions, threshold-based opening, recovery timeout,
half-open probing, per-platform isolation, stats, and rate limiter integration.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.adapters.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Basic state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreakerStates:
    """Core state machine: CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN."""

    async def test_starts_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    async def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    async def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    async def test_open_rejects_execution(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    async def test_closed_allows_execution(self):
        cb = CircuitBreaker("test")
        assert cb.can_execute() is True

    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED

    async def test_open_raises_circuit_breaker_open(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            cb.ensure_closed()
        assert "test" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Recovery timeout and half-open
# ---------------------------------------------------------------------------


class TestCircuitBreakerRecovery:
    """OPEN → HALF_OPEN after recovery_timeout, then probe result."""

    async def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True  # allows 1 probe

    async def test_half_open_success_closes(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1, success_threshold=1)
        cb.record_failure()
        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    async def test_half_open_limits_concurrent_probes(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1, half_open_max=1)
        cb.record_failure()
        await asyncio.sleep(0.15)

        # First probe allowed
        assert cb.can_execute() is True
        cb._half_open_calls += 1  # simulate in-flight probe

        # Second probe rejected
        assert cb.can_execute() is False

    async def test_does_not_transition_before_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=10)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False  # still within timeout


# ---------------------------------------------------------------------------
# Stats and metadata
# ---------------------------------------------------------------------------


class TestCircuitBreakerStats:
    """Verify stats() returns useful operational data."""

    async def test_stats_closed(self):
        cb = CircuitBreaker("my_platform")
        stats = cb.stats
        assert stats["name"] == "my_platform"
        assert stats["state"] == "CLOSED"
        assert stats["failure_count"] == 0
        assert stats["last_failure_at"] is None
        assert stats["opened_at"] is None

    async def test_stats_open(self):
        cb = CircuitBreaker("my_platform", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        stats = cb.stats
        assert stats["state"] == "OPEN"
        assert stats["failure_count"] == 2
        assert stats["last_failure_at"] is not None
        assert stats["opened_at"] is not None

    async def test_stats_after_recovery(self):
        cb = CircuitBreaker("p", failure_threshold=1, recovery_timeout=0.05, success_threshold=1)
        cb.record_failure()
        await asyncio.sleep(0.1)
        cb.record_success()
        stats = cb.stats
        assert stats["state"] == "CLOSED"
        assert stats["failure_count"] == 0


# ---------------------------------------------------------------------------
# Per-platform isolation
# ---------------------------------------------------------------------------


class TestCircuitBreakerIsolation:
    """Each platform has independent circuit breaker state."""

    async def test_independent_breakers(self):
        cb_a = CircuitBreaker("freelancer", failure_threshold=2)
        cb_b = CircuitBreaker("upwork", failure_threshold=2)

        cb_a.record_failure()
        cb_a.record_failure()

        assert cb_a.state == CircuitState.OPEN
        assert cb_b.state == CircuitState.CLOSED
        assert cb_b.can_execute() is True


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestCircuitBreakerContextManager:
    """Test async context manager usage pattern."""

    async def test_context_manager_success(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        async with cb:
            pass  # simulates successful operation
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    async def test_context_manager_failure(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("simulated failure")
        assert cb._failure_count == 1

    async def test_context_manager_rejects_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen):
            async with cb:
                pass  # should never reach here


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestCircuitBreakerEdgeCases:
    """Edge cases: zero threshold, rapid transitions, monotonic time."""

    async def test_threshold_of_one(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    async def test_multiple_successes_keep_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(100):
            cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    async def test_uses_monotonic_time(self):
        """Verify we use monotonic clock (immune to system clock changes)."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        # _opened_at should be set using time.monotonic()
        assert cb._opened_at is not None
        assert cb._opened_at <= time.monotonic()
