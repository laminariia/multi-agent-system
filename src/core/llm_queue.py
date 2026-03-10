"""LLM Request Queue with priority routing (P3.12).

Provides a priority-aware queue that controls concurrent access to LLM
providers.  Requests are served in priority order so that interactive
HITL-dependent calls are never starved by batch workloads.

Priority levels:
    HIGH   — Operator is waiting (HITL-dependent)  target < 5 s
    NORMAL — Regular agent pipeline work           target < 30 s
    LOW    — Batch operations (scan, email)        target < 5 min

Usage::

    queue = LLMRequestQueue(llm_client=client, max_concurrent=5)
    await queue.start()
    result = await queue.submit(LLMPriority.HIGH, "dev", messages)
    await queue.stop()
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Priority enum
# ---------------------------------------------------------------------------


class LLMPriority(enum.IntEnum):
    """Request priority — lower value = higher priority."""

    HIGH = 0
    NORMAL = 1
    LOW = 2


# ---------------------------------------------------------------------------
# Agent → default priority mapping
# ---------------------------------------------------------------------------

AGENT_PRIORITY: dict[str, LLMPriority] = {
    "scout": LLMPriority.LOW,
    "bid": LLMPriority.NORMAL,
    "planner": LLMPriority.NORMAL,
    "dev": LLMPriority.NORMAL,
    "content": LLMPriority.NORMAL,
    "design": LLMPriority.NORMAL,
    "critic": LLMPriority.NORMAL,
    "packager": LLMPriority.NORMAL,
    "geoscout": LLMPriority.LOW,
    "outreach": LLMPriority.LOW,
}


# ---------------------------------------------------------------------------
# Internal request wrapper
# ---------------------------------------------------------------------------

_counter = 0


@dataclass(order=True)
class _QueueEntry:
    """Wrapper that makes requests sortable by (priority, insertion_order)."""

    priority: int
    seq: int = field(compare=True)
    agent_name: str = field(compare=False)
    messages: list[Any] = field(compare=False, repr=False)
    kwargs: dict[str, Any] = field(compare=False, repr=False, default_factory=dict)
    future: asyncio.Future[Any] = field(compare=False, repr=False, default_factory=asyncio.Future)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class LLMRequestQueue:
    """Priority queue for LLM requests with bounded concurrency.

    Args:
        llm_client: The ``LLMClient`` instance to delegate calls to.
        max_concurrent: Maximum number of concurrent LLM calls.
    """

    def __init__(
        self,
        llm_client: Any,
        max_concurrent: int = 5,
    ) -> None:
        self._llm = llm_client
        self.max_concurrent = max_concurrent
        self._queue: asyncio.PriorityQueue[_QueueEntry] = asyncio.PriorityQueue()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._processed = 0
        self._processed_by_priority: dict[str, int] = {p.name: 0 for p in LLMPriority}
        self._seq = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start worker tasks that drain the queue."""
        if self._running:
            return
        self._running = True
        self._workers = [asyncio.create_task(self._worker(i)) for i in range(self.max_concurrent)]
        logger.info("llm_queue_started", workers=self.max_concurrent)

    async def stop(self) -> None:
        """Cancel workers and clear state."""
        if not self._running:
            return
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        logger.info("llm_queue_stopped", processed=self._processed)

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    async def submit(
        self,
        priority: LLMPriority,
        agent_name: str,
        messages: list[Any],
        **kwargs: Any,
    ) -> Any:
        """Submit a request and wait for its result.

        Args:
            priority: Request priority level.
            agent_name: Agent making the request.
            messages: LangChain message list.
            **kwargs: Extra keyword args forwarded to ``LLMClient.call()``.

        Returns:
            The ``(response, metrics)`` tuple from ``LLMClient.call()``.

        Raises:
            RuntimeError: If the queue is not running.
            LLMException: Propagated from the underlying LLM call.
        """
        if not self._running:
            raise RuntimeError("LLM queue is not running")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        self._seq += 1
        entry = _QueueEntry(
            priority=priority.value,
            seq=self._seq,
            agent_name=agent_name,
            messages=messages,
            kwargs=kwargs,
            future=future,
        )

        await self._queue.put(entry)
        logger.debug(
            "llm_queue_submit",
            agent=agent_name,
            priority=priority.name,
            pending=self._queue.qsize(),
        )

        return await future

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        """Process queue entries sequentially."""
        while self._running:
            try:
                entry = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                if not self._running:
                    break
                continue

            try:
                result = await self._llm.call(
                    entry.agent_name,
                    entry.messages,
                    **entry.kwargs,
                )
                entry.future.set_result(result)
                self._processed += 1
                pname = LLMPriority(entry.priority).name
                self._processed_by_priority[pname] = self._processed_by_priority.get(pname, 0) + 1
            except Exception as exc:  # noqa: BLE001
                entry.future.set_exception(exc)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return queue statistics."""
        return {
            "pending": self._queue.qsize(),
            "processed": self._processed,
            "by_priority": dict(self._processed_by_priority),
            "workers": len(self._workers),
            "running": self._running,
        }
