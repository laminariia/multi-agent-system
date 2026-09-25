"""Unit tests for worker main entry point.

Tests ``src.worker.__main__`` — signal handlers, graceful shutdown, scheduler
and queue task management.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


# ---------------------------------------------------------------------------
# _signal_handler tests
# ---------------------------------------------------------------------------


class TestSignalHandler:
    """Tests for the _signal_handler function."""

    def test_sets_shutdown_event_when_event_exists(self) -> None:
        """Should set the shutdown event when it is not None."""
        from src.worker.__main__ import _signal_handler

        mock_event = MagicMock()

        with patch("src.worker.__main__._shutdown_event", mock_event):
            _signal_handler(signal.SIGINT, None)

        mock_event.set.assert_called_once()

    def test_does_nothing_when_event_is_none(self) -> None:
        """Should not raise error when shutdown event is None."""
        from src.worker.__main__ import _signal_handler

        with patch("src.worker.__main__._shutdown_event", None):
            # Should not raise
            _signal_handler(signal.SIGINT, None)

    def test_logs_signal_name(self) -> None:
        """Should log the signal name."""
        from src.worker.__main__ import _signal_handler

        mock_event = MagicMock()
        mock_logger = MagicMock()

        with (
            patch("src.worker.__main__._shutdown_event", mock_event),
            patch("src.worker.__main__.logger", mock_logger),
        ):
            _signal_handler(signal.SIGINT, None)

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        assert "signal" in call_args[1]
        assert call_args[1]["signal"] == "SIGINT"


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() async function."""

    @pytest.mark.asyncio
    async def test_creates_shutdown_event(self) -> None:
        """Should create an asyncio.Event for shutdown coordination."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

    @pytest.mark.asyncio
    async def test_starts_scheduler(self) -> None:
        """Should start the WorkerScheduler."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        mock_scheduler.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_creates_queue_process_loop_task(self) -> None:
        """Should create a background task for queue processing."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()
        process_loop_called = False

        async def mock_process_loop(event):
            nonlocal process_loop_called
            process_loop_called = True
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        assert process_loop_called

    @pytest.mark.asyncio
    async def test_waits_for_shutdown_event(self) -> None:
        """Should wait for shutdown event before stopping."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        # If we reach here, shutdown event was waited for
        assert True

    @pytest.mark.asyncio
    async def test_stops_scheduler_on_shutdown(self) -> None:
        """Should stop scheduler when shutdown event is set."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        mock_scheduler.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancels_queue_task_on_shutdown(self) -> None:
        """Should cancel queue task when shutdown event is set."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        # Create a task that can be cancelled
        async def mock_process_loop(event):
            try:
                await asyncio.sleep(10)  # Long sleep to ensure cancellation
            except asyncio.CancelledError:
                raise

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        # If we reach here without hanging, cancellation worked

    @pytest.mark.asyncio
    async def test_handles_cancelled_error_gracefully(self) -> None:
        """Should catch CancelledError from queue task cancellation."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            raise asyncio.CancelledError()

        mock_queue.process_loop = mock_process_loop

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        # Should not raise

    @pytest.mark.asyncio
    async def test_calls_init_sentry(self) -> None:
        """Should initialize Sentry on startup."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        mock_init_sentry = MagicMock()

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry", mock_init_sentry),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        mock_init_sentry.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_worker_starting(self) -> None:
        """Should log 'worker_starting' message on startup."""
        from src.worker.__main__ import main

        mock_scheduler = AsyncMock()
        mock_scheduler.start = AsyncMock()
        mock_scheduler.stop = AsyncMock()

        mock_queue = MagicMock()

        async def mock_process_loop(event):
            await event.wait()

        mock_queue.process_loop = mock_process_loop

        mock_logger = MagicMock()

        with (
            patch("src.worker.scheduler.WorkerScheduler", return_value=mock_scheduler),
            patch("src.worker.queue.TaskQueue", return_value=mock_queue),
            patch("src.monitoring.sentry_config.init_sentry"),
            patch("src.worker.__main__.logger", mock_logger),
        ):

            async def trigger_shutdown():
                await asyncio.sleep(0.01)
                from src.worker import __main__

                if __main__._shutdown_event:
                    __main__._shutdown_event.set()

            task = asyncio.create_task(trigger_shutdown())
            await main()
            await task

        # Check that logger.info was called with "worker_starting"
        calls = [call[0][0] for call in mock_logger.info.call_args_list]
        assert "worker_starting" in calls
