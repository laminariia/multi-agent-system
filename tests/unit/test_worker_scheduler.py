"""Unit tests for src.worker.queue, src.worker.scheduler, and src.worker.tasks.

All Valkey and APScheduler interactions are mocked.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker.queue import DEAD_LETTER_KEY, QUEUE_KEY, TaskQueue
from src.worker.scheduler import WorkerScheduler, _run_pipeline_b_scan
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


@pytest.fixture()
def _mock_settings():
    """Patch get_settings to return a mock with scheduler defaults."""
    mock_settings = MagicMock()
    mock_settings.SCOUT_INTERVAL_MINUTES = 5
    mock_settings.METRICS_INTERVAL_SECONDS = 60
    mock_settings.HEARTBEAT_CLEANUP_MINUTES = 10
    mock_settings.PIPELINE_B_SCAN_INTERVAL_HOURS = 24
    mock_settings.PIPELINE_B_CITIES = ""
    mock_settings.DATA_RETENTION_INTERVAL_HOURS = 6
    with patch("src.core.config.get_settings", return_value=mock_settings):
        yield mock_settings


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

    @pytest.mark.usefixtures("_mock_settings")
    async def test_scheduler_starts_and_stops(self) -> None:
        """start() should set running=True; stop() calls shutdown without error."""
        scheduler = WorkerScheduler()

        assert scheduler.running is False

        await scheduler.start()
        assert scheduler.running is True

        # stop() calls shutdown(wait=False) which is scheduled via call_soon_threadsafe.
        await scheduler.stop()
        # Yield control so the event loop processes the scheduled shutdown callback.
        await asyncio.sleep(0)
        assert scheduler.running is False

    @pytest.mark.usefixtures("_mock_settings")
    async def test_scheduler_registers_base_jobs_no_cities(self) -> None:
        """With no cities configured, scheduler should have 4 jobs (no pipeline_b)."""
        scheduler = WorkerScheduler()

        await scheduler.start()

        jobs = scheduler._scheduler.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "scout_cycle" in job_ids
        assert "metrics_collection" in job_ids
        assert "heartbeat_cleanup" in job_ids
        assert "dispatch_scheduled_messages" in job_ids
        assert "pipeline_b_scan" not in job_ids
        assert len(jobs) == 5  # +data_retention

        await scheduler.stop()

    @pytest.mark.usefixtures("_mock_settings")
    async def test_scheduler_uses_configured_intervals(self) -> None:
        """Scheduler should read intervals from Settings."""
        scheduler = WorkerScheduler()
        assert scheduler._scout_interval == 5
        assert scheduler._metrics_interval == 60
        assert scheduler._heartbeat_interval == 10
        assert scheduler._pipeline_b_interval == 24

        await scheduler.start()
        await scheduler.stop()

    @pytest.mark.usefixtures("_mock_settings")
    async def test_scheduler_accepts_overrides(self) -> None:
        """Constructor kwargs should override Settings values."""
        scheduler = WorkerScheduler(
            scout_interval_minutes=15,
            metrics_interval_seconds=120,
            heartbeat_cleanup_minutes=30,
            pipeline_b_scan_interval_hours=12,
        )
        assert scheduler._scout_interval == 15
        assert scheduler._metrics_interval == 120
        assert scheduler._heartbeat_interval == 30
        assert scheduler._pipeline_b_interval == 12

    @pytest.mark.usefixtures("_mock_settings")
    async def test_scheduler_registers_pipeline_b_with_cities(self) -> None:
        """When cities are configured, scheduler should register pipeline_b_scan too."""
        scheduler = WorkerScheduler(
            pipeline_b_cities=["Berlin", "Munich"],
        )

        await scheduler.start()

        jobs = scheduler._scheduler.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "pipeline_b_scan" in job_ids
        assert len(jobs) == 6  # +data_retention

        await scheduler.stop()

    @pytest.mark.usefixtures("_mock_settings")
    async def test_scheduler_parses_cities_from_settings(self) -> None:
        """Settings.PIPELINE_B_CITIES comma string should be parsed into a list."""
        with patch("src.core.config.get_settings") as mock_gs:
            mock_s = MagicMock()
            mock_s.SCOUT_INTERVAL_MINUTES = 5
            mock_s.METRICS_INTERVAL_SECONDS = 60
            mock_s.HEARTBEAT_CLEANUP_MINUTES = 10
            mock_s.PIPELINE_B_SCAN_INTERVAL_HOURS = 24
            mock_s.DATA_RETENTION_INTERVAL_HOURS = 6
            mock_s.PIPELINE_B_CITIES = " Berlin , Munich , Hamburg "
            mock_gs.return_value = mock_s

            scheduler = WorkerScheduler()

        assert scheduler._pipeline_b_cities == ["Berlin", "Munich", "Hamburg"]

    @pytest.mark.usefixtures("_mock_settings")
    async def test_empty_cities_string_yields_empty_list(self) -> None:
        """An empty PIPELINE_B_CITIES should result in an empty list."""
        scheduler = WorkerScheduler()
        assert scheduler._pipeline_b_cities == []


# ---------------------------------------------------------------------------
# TestPipelineBScan
# ---------------------------------------------------------------------------


class TestPipelineBScan:
    """Tests for the _run_pipeline_b_scan scheduled job function."""

    async def test_pipeline_b_dispatches_for_each_city(self, mock_valkey: AsyncMock) -> None:
        """_run_pipeline_b_scan should dispatch a task for each city."""
        mock_valkey.set = AsyncMock(return_value=True)  # lock acquired

        mock_dispatch = AsyncMock(return_value={"task": "pipeline_b_scan", "status": "ok"})

        with (
            patch("src.core.database.get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", mock_dispatch),
        ):
            await _run_pipeline_b_scan(cities=["Berlin", "Munich", "Hamburg"])

        assert mock_dispatch.await_count == 3
        mock_dispatch.assert_any_await("pipeline_b_scan", {"city": "Berlin"})
        mock_dispatch.assert_any_await("pipeline_b_scan", {"city": "Munich"})
        mock_dispatch.assert_any_await("pipeline_b_scan", {"city": "Hamburg"})

        # Lock should be acquired and released
        mock_valkey.set.assert_awaited_once_with("pipeline_b:lock", "1", nx=True, ex=3600)
        mock_valkey.delete.assert_awaited_once_with("pipeline_b:lock")

    async def test_pipeline_b_skips_when_lock_held(self, mock_valkey: AsyncMock) -> None:
        """_run_pipeline_b_scan should skip when the distributed lock is held."""
        mock_valkey.set = AsyncMock(return_value=False)  # lock NOT acquired

        mock_dispatch = AsyncMock()

        with (
            patch("src.core.database.get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", mock_dispatch),
        ):
            await _run_pipeline_b_scan(cities=["Berlin"])

        mock_dispatch.assert_not_awaited()

    async def test_pipeline_b_continues_on_city_failure(self, mock_valkey: AsyncMock) -> None:
        """If one city fails, remaining cities should still be dispatched."""
        mock_valkey.set = AsyncMock(return_value=True)

        call_count = 0

        async def _side_effect(task_type: str, payload: dict) -> dict:
            nonlocal call_count
            call_count += 1
            if payload.get("city") == "Munich":
                raise RuntimeError("Munich scan failed")
            return {"task": task_type, "status": "ok"}

        with (
            patch("src.core.database.get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", side_effect=_side_effect),
        ):
            await _run_pipeline_b_scan(cities=["Berlin", "Munich", "Hamburg"])

        # All 3 cities attempted despite Munich failure
        assert call_count == 3

    async def test_pipeline_b_releases_lock_on_failure(self, mock_valkey: AsyncMock) -> None:
        """Lock should be released even if an unexpected error occurs."""
        mock_valkey.set = AsyncMock(return_value=True)

        with (
            patch("src.core.database.get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", side_effect=RuntimeError("boom")),
        ):
            # Should not raise — errors are caught and logged
            await _run_pipeline_b_scan(cities=["Berlin"])

        mock_valkey.delete.assert_awaited_once_with("pipeline_b:lock")
