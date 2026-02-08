"""Unit tests for src.worker.queue, src.worker.scheduler, and src.worker.tasks.

All Valkey and APScheduler interactions are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker.queue import DEAD_LETTER_KEY, QUEUE_KEY, TaskQueue
from src.worker.scheduler import WorkerScheduler
from src.worker.tasks import TASK_REGISTRY, dispatch_task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def task_queue(mock_valkey: AsyncMock) -> TaskQueue:
    """TaskQueue wired to the mock Valkey from conftest."""
    q = TaskQueue()
    # Patch _get_valkey to return our mock instead of the real singleton.
    q._get_valkey = MagicMock(return_value=mock_valkey)  # type: ignore[method-assign]
    return q


# ---------------------------------------------------------------------------
# TestTaskQueue
# ---------------------------------------------------------------------------


class TestTaskQueue:
    """Tests for the Valkey-backed TaskQueue."""

    async def test_enqueue_pushes_json_to_valkey(
        self,
        task_queue: TaskQueue,
        mock_valkey: AsyncMock,
    ) -> None:
        """enqueue() should RPUSH a JSON-encoded message to the queue key."""
        await task_queue.enqueue("scout_cycle", {"platform": "freelancer"})

        mock_valkey.rpush.assert_awaited_once()
        call_args = mock_valkey.rpush.call_args
        assert call_args[0][0] == QUEUE_KEY
        payload = json.loads(call_args[0][1])
        assert payload["type"] == "scout_cycle"
        assert payload["payload"] == {"platform": "freelancer"}
        assert payload["retry_count"] == 0

    async def test_dequeue_returns_parsed_task(
        self,
        task_queue: TaskQueue,
        mock_valkey: AsyncMock,
    ) -> None:
        """dequeue() should parse the BLPOP result into a dict."""
        raw_message = json.dumps({"type": "scout_cycle", "payload": {}, "retry_count": 0})
        mock_valkey.blpop = AsyncMock(return_value=(QUEUE_KEY, raw_message))

        result = await task_queue.dequeue(timeout=5)

        assert result is not None
        assert result["type"] == "scout_cycle"
        assert result["retry_count"] == 0

    async def test_dequeue_returns_none_on_timeout(
        self,
        task_queue: TaskQueue,
        mock_valkey: AsyncMock,
    ) -> None:
        """dequeue() should return None when BLPOP times out (returns None)."""
        mock_valkey.blpop = AsyncMock(return_value=None)

        result = await task_queue.dequeue(timeout=1)

        assert result is None

    async def test_dequeue_handles_invalid_json(
        self,
        task_queue: TaskQueue,
        mock_valkey: AsyncMock,
    ) -> None:
        """dequeue() should return None when the payload is not valid JSON."""
        mock_valkey.blpop = AsyncMock(return_value=(QUEUE_KEY, "not-json{{{"))

        result = await task_queue.dequeue()

        assert result is None

    async def test_queue_length(
        self,
        task_queue: TaskQueue,
        mock_valkey: AsyncMock,
    ) -> None:
        """queue_length() should return the LLEN of the main queue key."""
        mock_valkey.llen = AsyncMock(return_value=42)

        length = await task_queue.queue_length()

        assert length == 42
        mock_valkey.llen.assert_awaited_once_with(QUEUE_KEY)

    async def test_dead_letter_length(
        self,
        task_queue: TaskQueue,
        mock_valkey: AsyncMock,
    ) -> None:
        """dead_letter_length() should return the LLEN of the dead-letter key."""
        mock_valkey.llen = AsyncMock(return_value=7)

        length = await task_queue.dead_letter_length()

        assert length == 7
        mock_valkey.llen.assert_awaited_once_with(DEAD_LETTER_KEY)


# ---------------------------------------------------------------------------
# TestDispatchTask
# ---------------------------------------------------------------------------


class TestDispatchTask:
    """Tests for the dispatch_task() function and TASK_REGISTRY."""

    async def test_dispatch_known_task(self) -> None:
        """dispatch_task() should call the registered handler for a known type."""
        mock_handler = AsyncMock(return_value={"task": "scout_cycle", "jobs_found": 5})

        with patch.dict(TASK_REGISTRY, {"scout_cycle": mock_handler}):
            result = await dispatch_task("scout_cycle", {"platform": "all"})

        assert result["jobs_found"] == 5
        mock_handler.assert_awaited_once_with({"platform": "all"})

    async def test_dispatch_unknown_task_raises(self) -> None:
        """dispatch_task() should raise ValueError for an unregistered task type."""
        with pytest.raises(ValueError, match="Unknown task type"):
            await dispatch_task("nonexistent_task_type")


# ---------------------------------------------------------------------------
# TestWorkerScheduler
# ---------------------------------------------------------------------------


class TestWorkerScheduler:
    """Tests for the APScheduler-backed WorkerScheduler."""

    async def test_scheduler_starts_and_stops(self) -> None:
        """start() should set running=True; stop() calls shutdown without error."""
        import asyncio

        scheduler = WorkerScheduler()

        assert scheduler.running is False

        await scheduler.start()
        assert scheduler.running is True

        # stop() calls shutdown(wait=False) which is scheduled via call_soon_threadsafe.
        await scheduler.stop()
        # Yield control so the event loop processes the scheduled shutdown callback.
        await asyncio.sleep(0)
        assert scheduler.running is False

    async def test_scheduler_registers_three_jobs(self) -> None:
        """After start(), the internal scheduler should have 3 registered jobs."""
        scheduler = WorkerScheduler()

        await scheduler.start()

        jobs = scheduler._scheduler.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "scout_cycle" in job_ids
        assert "metrics_collection" in job_ids
        assert "heartbeat_cleanup" in job_ids
        assert len(jobs) == 3

        await scheduler.stop()
