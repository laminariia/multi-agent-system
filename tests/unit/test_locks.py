"""Unit tests for DistributedLock, ThreadLock, NegotiationLock (src/core/locks.py).

Tests cover: acquire/release, double-acquire prevention, owner isolation,
retry with backoff, TTL, context manager, and specialized lock subclasses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.locks import (
    DistributedLock,
    LockAcquisitionFailed,
    NegotiationLock,
    ThreadLock,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valkey_mock(*, locked: bool = False) -> AsyncMock:
    """Create a mock Valkey client.

    Args:
        locked: If True, SET NX returns False (lock held by someone else).
    """
    client = AsyncMock()
    client.set = AsyncMock(return_value=not locked)
    # eval for Lua release script
    client.eval = AsyncMock(return_value=1)
    client.get = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------


class TestDistributedLockBasic:
    """Core lock operations: acquire and release."""

    async def test_acquire_succeeds(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "test-resource", ttl=300)
        result = await lock.acquire()
        assert result is True
        valkey.set.assert_called_once()

    async def test_acquire_sets_nx_with_ttl(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "test-resource", ttl=60)
        await lock.acquire()
        call_kwargs = valkey.set.call_args
        # Should use NX and EX flags
        assert call_kwargs.kwargs.get("nx") is True
        assert call_kwargs.kwargs.get("ex") == 60

    async def test_release_succeeds(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "test-resource")
        await lock.acquire()
        result = await lock.release()
        assert result is True

    async def test_release_uses_lua_script(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "test-resource")
        await lock.acquire()
        await lock.release()
        # Should use eval (Lua script) for atomic check-and-delete
        valkey.eval.assert_called_once()

    async def test_lock_key_format(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "my-resource", ttl=300)
        assert "my-resource" in lock._lock_key


# ---------------------------------------------------------------------------
# Double acquire prevention
# ---------------------------------------------------------------------------


class TestDistributedLockContention:
    """Prevent concurrent holders from acquiring the same lock."""

    async def test_double_acquire_fails(self):
        valkey = _valkey_mock(locked=True)
        lock = DistributedLock(valkey, "contested", ttl=300, max_retries=0)
        result = await lock.acquire()
        assert result is False

    async def test_retry_on_contention(self):
        valkey = AsyncMock()
        # Fail twice, then succeed
        valkey.set = AsyncMock(side_effect=[False, False, True])
        valkey.eval = AsyncMock(return_value=1)

        lock = DistributedLock(valkey, "contested", ttl=300, retry_interval=0.01, max_retries=5)
        result = await lock.acquire()
        assert result is True
        assert valkey.set.call_count == 3

    async def test_acquisition_failed_after_max_retries(self):
        valkey = _valkey_mock(locked=True)
        lock = DistributedLock(valkey, "contested", ttl=300, retry_interval=0.01, max_retries=2)
        result = await lock.acquire()
        assert result is False
        # 1 initial + 2 retries = 3 attempts
        assert valkey.set.call_count == 3


# ---------------------------------------------------------------------------
# Owner isolation
# ---------------------------------------------------------------------------


class TestDistributedLockOwnerIsolation:
    """Only the lock holder can release the lock."""

    async def test_release_checks_owner(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "resource")
        await lock.acquire()
        await lock.release()

        # The Lua script should include the owner_id
        lua_args = valkey.eval.call_args
        # owner_id should be passed as an argument to the Lua script
        assert lock._owner_id in str(lua_args)

    async def test_different_owners_cannot_release(self):
        valkey = _valkey_mock()
        # eval returns 0 = owner mismatch
        valkey.eval = AsyncMock(return_value=0)
        lock = DistributedLock(valkey, "resource")
        await lock.acquire()
        result = await lock.release()
        assert result is False


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestDistributedLockContextManager:
    """Async context manager auto-acquires and releases."""

    async def test_context_manager_acquires_and_releases(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "resource")
        async with lock:
            valkey.set.assert_called_once()
        valkey.eval.assert_called_once()  # release

    async def test_context_manager_raises_on_failure(self):
        valkey = _valkey_mock(locked=True)
        lock = DistributedLock(valkey, "resource", max_retries=0)
        with pytest.raises(LockAcquisitionFailed, match="resource"):
            async with lock:
                pass  # should never reach here

    async def test_context_manager_releases_on_exception(self):
        valkey = _valkey_mock()
        lock = DistributedLock(valkey, "resource")
        with pytest.raises(ValueError):
            async with lock:
                raise ValueError("boom")
        # Should still release
        valkey.eval.assert_called_once()

    async def test_lock_acquisition_failed_is_mas_exception(self):
        from src.core.exceptions import MASException

        assert issubclass(LockAcquisitionFailed, MASException)


# ---------------------------------------------------------------------------
# Specialized locks
# ---------------------------------------------------------------------------


class TestThreadLock:
    """ThreadLock: keyed on thread_id."""

    async def test_thread_lock_key_contains_thread_id(self):
        valkey = _valkey_mock()
        lock = ThreadLock(valkey, "thread-abc-123")
        assert "thread-abc-123" in lock._lock_key

    async def test_thread_lock_acquires(self):
        valkey = _valkey_mock()
        lock = ThreadLock(valkey, "thread-abc-123")
        result = await lock.acquire()
        assert result is True


class TestNegotiationLock:
    """NegotiationLock: keyed on job/bid id."""

    async def test_negotiation_lock_key_contains_job_id(self):
        valkey = _valkey_mock()
        lock = NegotiationLock(valkey, "job-xyz-456")
        assert "job-xyz-456" in lock._lock_key

    async def test_negotiation_lock_acquires(self):
        valkey = _valkey_mock()
        lock = NegotiationLock(valkey, "job-xyz-456")
        result = await lock.acquire()
        assert result is True
