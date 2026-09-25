"""Unit tests for src/worker/queue.py - TaskQueue with Valkey backend."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.worker.queue import (
    DEAD_LETTER_KEY,
    MAX_RETRIES,
    QUEUE_KEY,
    TaskQueue,
)


class TestTaskQueueInit:
    """Test TaskQueue initialization."""

    def test_default_keys_match_constants(self) -> None:
        """Default queue keys should match module constants."""
        queue = TaskQueue()
        assert queue._queue_key == QUEUE_KEY
        assert queue._dead_letter_key == DEAD_LETTER_KEY

    def test_custom_keys_are_used(self) -> None:
        """Custom keys should override defaults."""
        queue = TaskQueue(queue_key="custom:queue", dead_letter_key="custom:dlq")
        assert queue._queue_key == "custom:queue"
        assert queue._dead_letter_key == "custom:dlq"


class TestEnqueue:
    """Test TaskQueue.enqueue."""

    @pytest.mark.asyncio
    async def test_calls_rpush_with_correct_queue_key(self) -> None:
        """Should call rpush with the configured queue key."""
        mock_valkey = AsyncMock()
        mock_valkey.rpush = AsyncMock(return_value=1)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            await queue.enqueue("scout_cycle", {"platform": "freelancer"})

            mock_valkey.rpush.assert_awaited_once()
            call_args = mock_valkey.rpush.call_args
            assert call_args[0][0] == QUEUE_KEY

    @pytest.mark.asyncio
    async def test_payload_included_in_message(self) -> None:
        """Enqueued message should contain the payload."""
        mock_valkey = AsyncMock()
        mock_valkey.rpush = AsyncMock(return_value=1)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            await queue.enqueue("scout_cycle", {"platform": "upwork"})

            call_args = mock_valkey.rpush.call_args
            message_json = call_args[0][1]
            message = json.loads(message_json)

            assert message["payload"] == {"platform": "upwork"}

    @pytest.mark.asyncio
    async def test_empty_payload_defaults_to_empty_dict(self) -> None:
        """None payload should default to empty dict."""
        mock_valkey = AsyncMock()
        mock_valkey.rpush = AsyncMock(return_value=1)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            await queue.enqueue("scout_cycle", None)

            call_args = mock_valkey.rpush.call_args
            message_json = call_args[0][1]
            message = json.loads(message_json)

            assert message["payload"] == {}

    @pytest.mark.asyncio
    async def test_retry_count_starts_at_zero(self) -> None:
        """Initial retry_count should be 0."""
        mock_valkey = AsyncMock()
        mock_valkey.rpush = AsyncMock(return_value=1)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            await queue.enqueue("scout_cycle", {})

            call_args = mock_valkey.rpush.call_args
            message_json = call_args[0][1]
            message = json.loads(message_json)

            assert message["retry_count"] == 0


class TestDequeue:
    """Test TaskQueue.dequeue."""

    @pytest.mark.asyncio
    async def test_returns_parsed_task_from_blpop(self) -> None:
        """Should parse and return task from blpop result."""
        task_data = {"type": "scout_cycle", "payload": {"platform": "all"}, "retry_count": 0}
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(return_value=(QUEUE_KEY, json.dumps(task_data)))

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            result = await queue.dequeue(timeout=5)

            assert result == task_data
            mock_valkey.blpop.assert_awaited_once_with(QUEUE_KEY, timeout=5)

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self) -> None:
        """Should return None when blpop times out."""
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(return_value=None)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            result = await queue.dequeue(timeout=2)

            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_json_decode_error(self) -> None:
        """Should return None when JSON is malformed."""
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(return_value=(QUEUE_KEY, "not-valid-json{"))

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            result = await queue.dequeue(timeout=5)

            assert result is None

    @pytest.mark.asyncio
    async def test_respects_timeout_parameter(self) -> None:
        """Should pass timeout to blpop."""
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(return_value=None)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            await queue.dequeue(timeout=10)

            mock_valkey.blpop.assert_awaited_once_with(QUEUE_KEY, timeout=10)


class TestProcessLoop:
    """Test TaskQueue.process_loop."""

    @pytest.mark.asyncio
    async def test_dispatches_dequeued_task(self) -> None:
        """Should call dispatch_task with dequeued task."""
        task_data = {"type": "scout_cycle", "payload": {}, "retry_count": 0}
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(
            side_effect=[
                (QUEUE_KEY, json.dumps(task_data)),
                None,  # Second call returns None to exit loop
            ]
        )

        shutdown_event = asyncio.Event()

        with (
            patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_dispatch.return_value = {"success": True}

            queue = TaskQueue()

            # Run process_loop for a short duration
            async def run_and_stop() -> None:
                task = asyncio.create_task(queue.process_loop(shutdown_event))
                await asyncio.sleep(0.1)
                shutdown_event.set()
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await run_and_stop()

            mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_re_enqueues_failed_task_with_incremented_retry(self) -> None:
        """Failed task should be re-enqueued with retry_count+1."""
        task_data = {"type": "scout_cycle", "payload": {}, "retry_count": 0}
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(
            side_effect=[
                (QUEUE_KEY, json.dumps(task_data)),
                None,  # Second call returns None to exit loop
            ]
        )
        mock_valkey.rpush = AsyncMock(return_value=1)

        shutdown_event = asyncio.Event()

        with (
            patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_dispatch.side_effect = Exception("Task failed")

            queue = TaskQueue()

            # Run process_loop for a short duration
            async def run_and_stop() -> None:
                task = asyncio.create_task(queue.process_loop(shutdown_event))
                await asyncio.sleep(0.1)
                shutdown_event.set()
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await run_and_stop()

            # Check that rpush was called with retry_count=1
            rpush_calls = [call for call in mock_valkey.rpush.call_args_list if call[0][0] == QUEUE_KEY]
            assert len(rpush_calls) > 0
            requeued_message = json.loads(rpush_calls[0][0][1])
            assert requeued_message["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_moves_to_dead_letter_after_max_retries(self) -> None:
        """Task should move to dead letter queue after MAX_RETRIES failures."""
        task_data = {"type": "scout_cycle", "payload": {}, "retry_count": MAX_RETRIES}
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(
            side_effect=[
                (QUEUE_KEY, json.dumps(task_data)),
                None,
            ]
        )
        mock_valkey.rpush = AsyncMock(return_value=1)

        shutdown_event = asyncio.Event()

        with (
            patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey),
            patch("src.worker.tasks.dispatch_task", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_dispatch.side_effect = Exception("Task failed")

            queue = TaskQueue()

            async def run_and_stop() -> None:
                task = asyncio.create_task(queue.process_loop(shutdown_event))
                await asyncio.sleep(0.1)
                shutdown_event.set()
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            await run_and_stop()

            # Check that task was sent to dead letter queue
            dlq_calls = [call for call in mock_valkey.rpush.call_args_list if call[0][0] == DEAD_LETTER_KEY]
            assert len(dlq_calls) > 0

    @pytest.mark.asyncio
    async def test_stops_when_shutdown_event_is_set(self) -> None:
        """Process loop should exit when shutdown_event is set."""
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(return_value=None)

        shutdown_event = asyncio.Event()
        shutdown_event.set()  # Set immediately

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            await queue.process_loop(shutdown_event)

            # Should exit immediately without blocking

    @pytest.mark.asyncio
    async def test_handles_asyncio_cancelled_error_gracefully(self) -> None:
        """CancelledError should break the loop gracefully."""
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(side_effect=asyncio.CancelledError)

        shutdown_event = asyncio.Event()

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()

            # Should not raise, just exit
            await queue.process_loop(shutdown_event)

    @pytest.mark.asyncio
    async def test_handles_general_exceptions_without_crashing(self) -> None:
        """General exceptions should be logged but not crash the loop."""
        mock_valkey = AsyncMock()
        mock_valkey.blpop = AsyncMock(
            side_effect=[
                Exception("Random error"),
                None,  # Second call returns None to allow clean exit
            ]
        )

        shutdown_event = asyncio.Event()

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()

            async def run_and_stop() -> None:
                task = asyncio.create_task(queue.process_loop(shutdown_event))
                await asyncio.sleep(0.2)
                shutdown_event.set()
                await asyncio.sleep(0.1)
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Should not raise, just continue
            await run_and_stop()


class TestQueueLength:
    """Test TaskQueue.queue_length."""

    @pytest.mark.asyncio
    async def test_returns_llen_of_main_queue(self) -> None:
        """Should return the length of the main queue."""
        mock_valkey = AsyncMock()
        mock_valkey.llen = AsyncMock(return_value=42)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            length = await queue.queue_length()

            assert length == 42
            mock_valkey.llen.assert_awaited_once_with(QUEUE_KEY)


class TestDeadLetterLength:
    """Test TaskQueue.dead_letter_length."""

    @pytest.mark.asyncio
    async def test_returns_llen_of_dead_letter_queue(self) -> None:
        """Should return the length of the dead letter queue."""
        mock_valkey = AsyncMock()
        mock_valkey.llen = AsyncMock(return_value=5)

        with patch.object(TaskQueue, "_get_valkey", return_value=mock_valkey):
            queue = TaskQueue()
            length = await queue.dead_letter_length()

            assert length == 5
            mock_valkey.llen.assert_awaited_once_with(DEAD_LETTER_KEY)
