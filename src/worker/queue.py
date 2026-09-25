"""Valkey-backed task queue using RPUSH/BLPOP.

Simple, reliable task queue for the MAS worker. Tasks are serialized as JSON
and processed one at a time with error handling and logging.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

QUEUE_KEY = "mas:task_queue"
DEAD_LETTER_KEY = "mas:task_dead_letter"
MAX_RETRIES = 3


class TaskQueue:
    """Valkey RPUSH/BLPOP task queue.

    Args:
        queue_key: Valkey key for the main task queue.
        dead_letter_key: Valkey key for failed tasks.
    """

    def __init__(
        self,
        *,
        queue_key: str = QUEUE_KEY,
        dead_letter_key: str = DEAD_LETTER_KEY,
    ) -> None:
        self._queue_key = queue_key
        self._dead_letter_key = dead_letter_key

    def _get_valkey(self) -> Any:
        from src.core.database import get_valkey

        return get_valkey()

    async def enqueue(self, task_type: str, payload: dict[str, Any] | None = None) -> None:
        """Add a task to the queue.

        Args:
            task_type: Registered task name (e.g. ``"scout_cycle"``).
            payload: Optional task-specific data.
        """
        valkey = self._get_valkey()
        message = json.dumps(
            {
                "type": task_type,
                "payload": payload or {},
                "retry_count": 0,
            }
        )
        await valkey.rpush(self._queue_key, message)
        logger.info("task_enqueued", task_type=task_type)

    async def dequeue(self, timeout: int = 5) -> dict[str, Any] | None:
        """Block-pop one task from the queue.

        Args:
            timeout: BLPOP timeout in seconds.

        Returns:
            Parsed task dict or ``None`` if the queue is empty after timeout.
        """
        valkey = self._get_valkey()
        result = await valkey.blpop(self._queue_key, timeout=timeout)
        if result is None:
            return None

        _key, raw = result
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error("task_dequeue_parse_error", raw=str(raw)[:200])
            return None

    async def process_loop(self, shutdown_event: asyncio.Event) -> None:
        """Continuously process tasks until the shutdown event is set.

        This is the main loop for the queue consumer.
        """
        from src.worker.tasks import dispatch_task

        logger.info("queue_consumer_started")

        while not shutdown_event.is_set():
            try:
                task = await self.dequeue(timeout=2)
                if task is None:
                    continue

                task_type = task.get("type", "unknown")
                payload = task.get("payload", {})
                retry_count = task.get("retry_count", 0)

                try:
                    result = await dispatch_task(task_type, payload)
                    logger.info("task_completed", task_type=task_type, result_summary=str(result)[:200])
                except Exception:
                    logger.exception("task_failed", task_type=task_type, retry_count=retry_count)

                    if retry_count < MAX_RETRIES:
                        # Re-enqueue with incremented retry count
                        task["retry_count"] = retry_count + 1
                        valkey = self._get_valkey()
                        await valkey.rpush(self._queue_key, json.dumps(task))
                        logger.info("task_requeued", task_type=task_type, retry=retry_count + 1)
                    else:
                        # Move to dead letter queue
                        valkey = self._get_valkey()
                        await valkey.rpush(self._dead_letter_key, json.dumps(task))
                        logger.warning("task_dead_lettered", task_type=task_type)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("queue_consumer_error")
                await asyncio.sleep(1)

        logger.info("queue_consumer_stopped")

    async def queue_length(self) -> int:
        """Return the current number of tasks in the queue."""
        valkey = self._get_valkey()
        return await valkey.llen(self._queue_key)

    async def dead_letter_length(self) -> int:
        """Return the current number of tasks in the dead letter queue."""
        valkey = self._get_valkey()
        return await valkey.llen(self._dead_letter_key)
