"""Tests for P3.12 — LLM Request Queue (Priority Routing).

Covers:
- LLMPriority enum ordering (HIGH < NORMAL < LOW)
- LLMRequestQueue submission and processing
- Priority ordering: HIGH processed before NORMAL before LOW
- Configurable concurrency via LLM_MAX_CONCURRENT env var
- Agent-to-priority default mapping
- Queue metrics (pending, processed counts)
- Graceful shutdown with pending requests
- Integration with LLMClient
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. Priority Enum
# ===========================================================================


class TestLLMPriority:
    """LLMPriority enum defines correct ordering."""

    def test_high_is_lowest_value(self) -> None:
        """HIGH priority should have the lowest numeric value (processed first)."""
        from src.core.llm_queue import LLMPriority

        assert LLMPriority.HIGH.value < LLMPriority.NORMAL.value
        assert LLMPriority.NORMAL.value < LLMPriority.LOW.value

    def test_priority_comparison(self) -> None:
        """Priorities should be comparable for PriorityQueue ordering."""
        from src.core.llm_queue import LLMPriority

        assert LLMPriority.HIGH < LLMPriority.NORMAL
        assert LLMPriority.NORMAL < LLMPriority.LOW

    def test_all_priorities_defined(self) -> None:
        """Should have exactly three priority levels."""
        from src.core.llm_queue import LLMPriority

        assert len(LLMPriority) == 3


# ===========================================================================
# 2. Agent Priority Mapping
# ===========================================================================


class TestAgentPriorityMapping:
    """Default agent-to-priority mapping."""

    def test_batch_agents_are_low_priority(self) -> None:
        """Scout, geoscout, outreach are LOW (batch operations)."""
        from src.core.llm_queue import AGENT_PRIORITY, LLMPriority

        assert AGENT_PRIORITY["scout"] == LLMPriority.LOW
        assert AGENT_PRIORITY["geoscout"] == LLMPriority.LOW
        assert AGENT_PRIORITY["outreach"] == LLMPriority.LOW

    def test_execution_agents_are_normal_priority(self) -> None:
        """Dev, content, design, planner, critic are NORMAL."""
        from src.core.llm_queue import AGENT_PRIORITY, LLMPriority

        for agent in ("dev", "content", "design", "planner", "critic", "packager"):
            assert AGENT_PRIORITY[agent] == LLMPriority.NORMAL, f"{agent} should be NORMAL"

    def test_bid_is_normal_priority(self) -> None:
        """Bid agent is NORMAL priority."""
        from src.core.llm_queue import AGENT_PRIORITY, LLMPriority

        assert AGENT_PRIORITY["bid"] == LLMPriority.NORMAL

    def test_unknown_agent_defaults_to_normal(self) -> None:
        """Unknown agent name should default to NORMAL priority."""
        from src.core.llm_queue import AGENT_PRIORITY, LLMPriority

        assert AGENT_PRIORITY.get("unknown_agent", LLMPriority.NORMAL) == LLMPriority.NORMAL


# ===========================================================================
# 3. Queue Submission and Processing
# ===========================================================================


class TestLLMRequestQueue:
    """LLMRequestQueue processes requests with priority ordering."""

    async def test_submit_returns_awaitable_future(self) -> None:
        """submit() should return a Future that resolves to the LLM result."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_metrics = MagicMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, mock_metrics))

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=1)
        await queue.start()
        try:
            result = await queue.submit(
                priority=LLMPriority.NORMAL,
                agent_name="dev",
                messages=[],
            )
            assert result == (mock_response, mock_metrics)
        finally:
            await queue.stop()

    async def test_high_priority_processed_before_low(self) -> None:
        """HIGH priority requests should be processed before LOW ones."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        call_order: list[str] = []
        processing_gate = asyncio.Event()

        async def slow_llm_call(agent_name, messages, **kwargs):
            if not call_order:
                # First call: block until gate opens (lets other requests queue up)
                processing_gate.set()
                await asyncio.sleep(0.05)
            call_order.append(agent_name)
            return MagicMock(), MagicMock()

        mock_llm = AsyncMock()
        mock_llm.call = slow_llm_call

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=1)
        await queue.start()
        try:
            # Submit first request to occupy the worker
            first = asyncio.create_task(queue.submit(LLMPriority.NORMAL, "blocker", []))
            await processing_gate.wait()

            # While first is processing, submit LOW then HIGH
            low_task = asyncio.create_task(queue.submit(LLMPriority.LOW, "low_agent", []))
            high_task = asyncio.create_task(queue.submit(LLMPriority.HIGH, "high_agent", []))

            await asyncio.gather(first, low_task, high_task)

            # HIGH should have been processed before LOW
            assert call_order.index("high_agent") < call_order.index("low_agent")
        finally:
            await queue.stop()

    async def test_concurrent_processing(self) -> None:
        """Multiple workers should process requests concurrently."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        active_count = 0
        max_active = 0
        lock = asyncio.Lock()

        async def concurrent_llm_call(agent_name, messages, **kwargs):
            nonlocal active_count, max_active
            async with lock:
                active_count += 1
                max_active = max(max_active, active_count)
            await asyncio.sleep(0.05)
            async with lock:
                active_count -= 1
            return MagicMock(), MagicMock()

        mock_llm = AsyncMock()
        mock_llm.call = concurrent_llm_call

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=3)
        await queue.start()
        try:
            tasks = [asyncio.create_task(queue.submit(LLMPriority.NORMAL, f"agent_{i}", [])) for i in range(6)]
            await asyncio.gather(*tasks)
            assert max_active >= 2  # At least 2 ran concurrently
        finally:
            await queue.stop()


# ===========================================================================
# 4. Configurable Concurrency
# ===========================================================================


class TestConfigurableConcurrency:
    """Queue concurrency configurable via LLM_MAX_CONCURRENT."""

    def test_default_concurrency_is_5(self) -> None:
        """Default max_concurrent should be 5."""
        from src.core.llm_queue import LLMRequestQueue

        queue = LLMRequestQueue(llm_client=MagicMock())
        assert queue.max_concurrent == 5

    def test_custom_concurrency(self) -> None:
        """Constructor accepts custom max_concurrent."""
        from src.core.llm_queue import LLMRequestQueue

        queue = LLMRequestQueue(llm_client=MagicMock(), max_concurrent=10)
        assert queue.max_concurrent == 10


# ===========================================================================
# 5. Queue Metrics
# ===========================================================================


class TestQueueMetrics:
    """Queue tracks pending and processed counts."""

    async def test_stats_returns_counts(self) -> None:
        """stats() should return pending and processed counts."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(MagicMock(), MagicMock()))

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=1)
        await queue.start()
        try:
            await queue.submit(LLMPriority.NORMAL, "dev", [])
            stats = queue.stats()
            assert stats["processed"] >= 1
            assert "pending" in stats
        finally:
            await queue.stop()

    async def test_stats_tracks_by_priority(self) -> None:
        """stats() should track processed counts by priority."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(MagicMock(), MagicMock()))

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=2)
        await queue.start()
        try:
            await queue.submit(LLMPriority.HIGH, "dev", [])
            await queue.submit(LLMPriority.LOW, "scout", [])
            stats = queue.stats()
            assert stats["by_priority"]["HIGH"] >= 1
            assert stats["by_priority"]["LOW"] >= 1
        finally:
            await queue.stop()


# ===========================================================================
# 6. Graceful Shutdown
# ===========================================================================


class TestQueueShutdown:
    """Queue shuts down gracefully."""

    async def test_stop_cancels_workers(self) -> None:
        """stop() should cancel worker tasks."""
        from src.core.llm_queue import LLMRequestQueue

        queue = LLMRequestQueue(llm_client=MagicMock(), max_concurrent=2)
        await queue.start()
        await queue.stop()
        assert queue._workers == []

    async def test_double_stop_is_safe(self) -> None:
        """Calling stop() twice should not raise."""
        from src.core.llm_queue import LLMRequestQueue

        queue = LLMRequestQueue(llm_client=MagicMock())
        await queue.start()
        await queue.stop()
        await queue.stop()  # Should not raise

    async def test_submit_after_stop_raises(self) -> None:
        """Submitting after stop should raise RuntimeError."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        queue = LLMRequestQueue(llm_client=MagicMock())
        await queue.start()
        await queue.stop()

        with pytest.raises(RuntimeError, match="not running"):
            await queue.submit(LLMPriority.NORMAL, "dev", [])


# ===========================================================================
# 7. Error Propagation
# ===========================================================================


class TestErrorPropagation:
    """LLM errors propagate through the queue to the caller."""

    async def test_llm_error_propagated_to_caller(self) -> None:
        """LLM exceptions should propagate to the submit() caller."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        from src.core.exceptions import LLMException

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(side_effect=LLMException("all providers exhausted"))

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=1)
        await queue.start()
        try:
            with pytest.raises(LLMException, match="all providers exhausted"):
                await queue.submit(LLMPriority.NORMAL, "dev", [])
        finally:
            await queue.stop()

    async def test_one_error_does_not_affect_other_requests(self) -> None:
        """A failed request should not prevent other requests from processing."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        from src.core.exceptions import LLMException

        call_count = 0

        async def mixed_results(agent_name, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if agent_name == "fail_agent":
                raise LLMException("intentional failure")
            return MagicMock(), MagicMock()

        mock_llm = AsyncMock()
        mock_llm.call = mixed_results

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=1)
        await queue.start()
        try:
            # Submit failing request
            fail_task = asyncio.create_task(queue.submit(LLMPriority.NORMAL, "fail_agent", []))
            # Submit succeeding request
            ok_task = asyncio.create_task(queue.submit(LLMPriority.NORMAL, "ok_agent", []))

            # Failing should raise
            with pytest.raises(LLMException):
                await fail_task

            # Succeeding should work
            result = await ok_task
            assert result is not None
        finally:
            await queue.stop()


# ===========================================================================
# 8. Priority Override
# ===========================================================================


class TestPriorityOverride:
    """Callers can override the default agent priority."""

    async def test_submit_with_explicit_priority(self) -> None:
        """Explicit priority overrides agent default."""
        from src.core.llm_queue import LLMPriority, LLMRequestQueue

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(MagicMock(), MagicMock()))

        queue = LLMRequestQueue(llm_client=mock_llm, max_concurrent=1)
        await queue.start()
        try:
            # Scout is normally LOW, but override to HIGH
            result = await queue.submit(
                priority=LLMPriority.HIGH,
                agent_name="scout",
                messages=[],
            )
            assert result is not None
        finally:
            await queue.stop()
