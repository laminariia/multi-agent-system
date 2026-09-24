"""Batch collector for notification aggregation.

Warnings are accumulated over a configurable time window (default: 30 minutes)
before being flushed to the delivery channels.  Uses Valkey for persistence
so that batches survive process restarts.

Flow::

    notify(warning) → BatchCollector.add() → Valkey list
                      ↓ (after 30 min or manual flush)
                      flush() → channels.send_batch(warnings)
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from redis.asyncio import Redis as AsyncRedis

from src.notifications.models import Notification, Severity

logger = structlog.get_logger(__name__)

# Valkey key prefix for batch queues.
_BATCH_KEY_PREFIX = "notifications:batch:"
# Key for the last-flush timestamp.
_FLUSH_TS_KEY_PREFIX = "notifications:flush_ts:"

# Default batch window in seconds (30 minutes).
DEFAULT_BATCH_WINDOW_SECONDS = 30 * 60


class BatchCollector:
    """Accumulates notifications in Valkey and flushes them after a time window.

    Each severity level has its own batch queue, but the primary use case
    is buffering ``WARNING`` notifications for 30-minute digest delivery.

    Args:
        valkey: Async Redis/Valkey client.
        window_seconds: Seconds between automatic flushes (default 1800).
        max_batch_size: Maximum items per flush (safety cap, default 200).
    """

    def __init__(
        self,
        valkey: AsyncRedis,
        *,
        window_seconds: int = DEFAULT_BATCH_WINDOW_SECONDS,
        max_batch_size: int = 200,
    ) -> None:
        self._valkey = valkey
        self._window_seconds = window_seconds
        self._max_batch_size = max_batch_size

    def _batch_key(self, severity: Severity) -> str:
        """Return the Valkey list key for a given severity."""
        return f"{_BATCH_KEY_PREFIX}{severity.value}"

    def _flush_ts_key(self, severity: Severity) -> str:
        """Return the Valkey key storing the last-flush timestamp."""
        return f"{_FLUSH_TS_KEY_PREFIX}{severity.value}"

    async def add(self, notification: Notification) -> int:
        """Append a notification to the batch queue.

        Returns:
            The current queue length after insertion.
        """
        payload = json.dumps(notification.to_dict(), default=str)
        key = self._batch_key(notification.severity)
        length = await self._valkey.rpush(key, payload)
        logger.debug(
            "batch.added",
            notification_id=notification.id,
            severity=notification.severity.value,
            queue_length=length,
        )
        return length

    async def size(self, severity: Severity) -> int:
        """Return the number of queued notifications for the given severity."""
        return await self._valkey.llen(self._batch_key(severity))

    async def is_window_elapsed(self, severity: Severity) -> bool:
        """Check if the batch window has elapsed since the last flush."""
        ts_key = self._flush_ts_key(severity)
        raw = await self._valkey.get(ts_key)
        if raw is None:
            # No previous flush recorded — window is elapsed.
            return True
        try:
            last_flush = float(raw)
        except (ValueError, TypeError):
            return True
        return (time.time() - last_flush) >= self._window_seconds

    async def flush(self, severity: Severity) -> list[Notification]:
        """Drain all queued notifications for the given severity.

        Updates the last-flush timestamp and returns the list of
        :class:`Notification` objects that were in the queue.

        Returns:
            List of notifications drained from the queue (may be empty).
        """
        key = self._batch_key(severity)
        ts_key = self._flush_ts_key(severity)

        # Atomic drain: LRANGE + DELETE in a pipeline.
        pipe = self._valkey.pipeline()
        pipe.lrange(key, 0, self._max_batch_size - 1)
        pipe.delete(key)
        results = await pipe.execute()

        raw_items: list[bytes | str] = results[0] if results else []

        notifications: list[Notification] = []
        for raw in raw_items:
            try:
                data = json.loads(raw)
                notifications.append(_dict_to_notification(data))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("batch.parse_error", error=str(exc), raw=str(raw)[:200])

        # Record flush timestamp.
        await self._valkey.set(ts_key, str(time.time()))

        logger.info(
            "batch.flushed",
            severity=severity.value,
            count=len(notifications),
        )
        return notifications

    async def flush_if_ready(self, severity: Severity) -> list[Notification]:
        """Flush only if the batch window has elapsed.

        Returns:
            List of flushed notifications, or empty list if window not elapsed.
        """
        if not await self.is_window_elapsed(severity):
            return []
        queue_size = await self.size(severity)
        if queue_size == 0:
            return []
        return await self.flush(severity)


def _dict_to_notification(data: dict[str, Any]) -> Notification:
    """Reconstruct a :class:`Notification` from its ``to_dict()`` output."""
    from datetime import datetime  # noqa: PLC0415

    severity = Severity(data["severity"])
    created_at_raw = data.get("created_at")
    if isinstance(created_at_raw, str):
        created_at = datetime.fromisoformat(created_at_raw)
    else:
        from datetime import UTC  # noqa: PLC0415

        created_at = datetime.now(UTC)

    return Notification(
        severity=severity,
        title=data["title"],
        message=data.get("message", ""),
        source=data.get("source", ""),
        agent_name=data.get("agent_name", ""),
        thread_id=data.get("thread_id", ""),
        metadata=data.get("metadata", {}),
        id=data.get("id", ""),
        created_at=created_at,
    )
