"""Distributed locks via Valkey for concurrency safety.

Prevents concurrent graph invocations on the same thread_id (ThreadLock)
and concurrent bid modifications (NegotiationLock).

Uses Valkey SET NX EX for atomic acquire, Lua script for atomic release.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from src.core.exceptions import MASException

logger = structlog.get_logger(__name__)

# Lua script: atomic check-and-delete (only owner can release)
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class LockAcquisitionFailed(MASException):
    """Raised when a lock cannot be acquired after max retries."""


class DistributedLock:
    """Valkey-backed distributed lock with owner isolation.

    Args:
        valkey_client: Async Valkey/Redis client.
        name: Resource identifier (thread_id, job_id, etc.).
        ttl: Lock TTL in seconds (auto-expires on crash).
        retry_interval: Base delay between acquire retries.
        max_retries: Max number of retry attempts (0 = no retries).
    """

    _key_prefix = "lock:"

    def __init__(
        self,
        valkey_client: Any,
        name: str,
        *,
        ttl: int = 300,
        retry_interval: float = 0.5,
        max_retries: int = 10,
    ) -> None:
        self._valkey = valkey_client
        self._name = name
        self._ttl = ttl
        self._retry_interval = retry_interval
        self._max_retries = max_retries
        self._owner_id: str = uuid.uuid4().hex

    @property
    def _lock_key(self) -> str:
        return f"{self._key_prefix}{self._name}"

    # ------------------------------------------------------------------
    # Acquire / Release
    # ------------------------------------------------------------------

    async def acquire(self) -> bool:
        """Try to acquire the lock with retries and exponential backoff."""
        delay = self._retry_interval

        for attempt in range(1 + self._max_retries):
            acquired = await self._valkey.set(self._lock_key, self._owner_id, nx=True, ex=self._ttl)
            if acquired:
                logger.debug(
                    "lock.acquired",
                    key=self._lock_key,
                    owner=self._owner_id[:8],
                    attempt=attempt + 1,
                )
                return True

            if attempt < self._max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)  # cap at 30s

        logger.warning(
            "lock.failed",
            key=self._lock_key,
            owner=self._owner_id[:8],
            max_retries=self._max_retries,
        )
        return False

    async def release(self) -> bool:
        """Release the lock (only if we are the owner)."""
        result = await self._valkey.eval(_RELEASE_LUA, 1, self._lock_key, self._owner_id)
        released = result == 1
        if released:
            logger.debug("lock.released", key=self._lock_key, owner=self._owner_id[:8])
        else:
            logger.warning(
                "lock.release_failed",
                key=self._lock_key,
                owner=self._owner_id[:8],
            )
        return released

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> DistributedLock:
        acquired = await self.acquire()
        if not acquired:
            raise LockAcquisitionFailed(f"Failed to acquire lock '{self._lock_key}' after {self._max_retries} retries")
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: object, exc_tb: object) -> bool:
        await self.release()
        return False  # never suppress exceptions


class ThreadLock(DistributedLock):
    """Lock keyed on thread_id to prevent concurrent graph invocations."""

    _key_prefix = "lock:thread:"


class NegotiationLock(DistributedLock):
    """Lock keyed on job/bid id to prevent concurrent bid modifications."""

    _key_prefix = "lock:negotiation:"
