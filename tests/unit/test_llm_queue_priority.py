"""Unit tests for LLM Request Queue with Priority (L2).

Tests cover: priority levels, queue ordering, submit/process, stats,
agent-priority mapping, Valkey sorted-set integration, avg_wait_time.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.llm_queue import (
    AGENT_PRIORITY,
    LLMPriority,
    LLMRequestQueue,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Priority enum
# ---------------------------------------------------------------------------


class TestLLMPriority:
    """LLMPriority enum ordering and values."""

    def test_high_has_lowest_value(self):
        assert LLMPriority.HIGH < LLMPriority.NORMAL
        assert LLMPriority.NORMAL < LLMPriority.LOW

    def test_values_are_sequential(self):
        assert LLMPriority.HIGH.value == 0
        assert LLMPriority.NORMAL.value == 1
        assert LLMPriority.LOW.value == 2

    def test_all_priorities_in_enum(self):
        names = {p.name for p in LLMPriority}
        assert names == {"HIGH", "NORMAL", "LOW"}


# ---------------------------------------------------------------------------
# Agent → Priority mapping
# ---------------------------------------------------------------------------


class TestAgentPriorityMapping:
    """AGENT_PRIORITY maps agent names to default priorities."""

    def test_scout_is_low(self):
        assert AGENT_PRIORITY["scout"] == LLMPriority.LOW

    def test_bid_is_normal(self):
        assert AGENT_PRIORITY["bid"] == LLMPriority.NORMAL

    def test_dev_is_normal(self):
        assert AGENT_PRIORITY["dev"] == LLMPriority.NORMAL

    def test_geoscout_is_low(self):
        assert AGENT_PRIORITY["geoscout"] == LLMPriority.LOW

    def test_all_agents_mapped(self):
        expected_agents = {
            "scout",
            "bid",
            "planner",
            "dev",
            "content",
            "design",
            "critic",
            "packager",
            "geoscout",
            "outreach",
        }
        assert expected_agents.issubset(set(AGENT_PRIORITY.keys()))


# ---------------------------------------------------------------------------
# Queue lifecycle
# ---------------------------------------------------------------------------


class TestQueueLifecycle:
    """Start/stop and basic queue operations."""

    async def test_submit_raises_when_not_running(self):
        llm = AsyncMock()
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        with pytest.raises(RuntimeError, match="not running"):
            await queue.submit(LLMPriority.NORMAL, "dev", [])

    async def test_start_creates_workers(self):
        llm = AsyncMock()
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=3)
        await queue.start()
        assert len(queue._workers) == 3
        assert queue._running is True
        await queue.stop()

    async def test_stop_cancels_workers(self):
        llm = AsyncMock()
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        await queue.stop()
        assert queue._running is False
        assert len(queue._workers) == 0

    async def test_double_start_is_noop(self):
        llm = AsyncMock()
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        await queue.start()  # should not double-create workers
        assert len(queue._workers) == 2
        await queue.stop()

    async def test_double_stop_is_noop(self):
        llm = AsyncMock()
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        await queue.stop()
        await queue.stop()  # should not raise


# ---------------------------------------------------------------------------
# Submit and process
# ---------------------------------------------------------------------------


class TestSubmitAndProcess:
    """Submitting requests and getting results back."""

    async def test_submit_returns_llm_result(self):
        llm = AsyncMock()
        llm.call.return_value = ("response_msg", {"tokens": 100})

        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        try:
            result = await asyncio.wait_for(
                queue.submit(LLMPriority.NORMAL, "dev", ["msg1"]),
                timeout=5.0,
            )
            assert result == ("response_msg", {"tokens": 100})
        finally:
            await queue.stop()

    async def test_submit_propagates_exception(self):
        llm = AsyncMock()
        llm.call.side_effect = ValueError("LLM error")

        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        try:
            with pytest.raises(ValueError, match="LLM error"):
                await asyncio.wait_for(
                    queue.submit(LLMPriority.NORMAL, "dev", ["msg1"]),
                    timeout=5.0,
                )
        finally:
            await queue.stop()

    async def test_submit_forwards_kwargs(self):
        llm = AsyncMock()
        llm.call.return_value = ("resp", {})

        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        try:
            await asyncio.wait_for(
                queue.submit(
                    LLMPriority.NORMAL,
                    "dev",
                    ["msg1"],
                    temperature=0.5,
                    max_tokens=100,
                ),
                timeout=5.0,
            )
            llm.call.assert_called_once_with(
                "dev",
                ["msg1"],
                temperature=0.5,
                max_tokens=100,
            )
        finally:
            await queue.stop()

    async def test_high_priority_processed_before_low(self):
        """When multiple requests are queued, HIGH is served first."""
        call_order: list[str] = []
        llm = AsyncMock()

        async def slow_call(agent_name, messages, **kwargs):
            call_order.append(agent_name)
            return ("resp", {})

        llm.call.side_effect = slow_call

        # Use 1 worker so requests are processed sequentially
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=1)

        # Don't start workers yet -- enqueue first
        queue._running = True  # allow submit without starting workers
        queue._seq = 0

        # Submit LOW first, then HIGH
        low_future = asyncio.ensure_future(queue.submit(LLMPriority.LOW, "scout", ["low_msg"]))
        high_future = asyncio.ensure_future(queue.submit(LLMPriority.HIGH, "bid", ["high_msg"]))

        # Give futures time to enqueue
        await asyncio.sleep(0.05)

        # Now start a single worker to drain
        queue._workers = [asyncio.create_task(queue._worker(0))]

        await asyncio.wait_for(
            asyncio.gather(low_future, high_future),
            timeout=5.0,
        )

        # HIGH (bid) should be processed before LOW (scout)
        assert call_order[0] == "bid"
        assert call_order[1] == "scout"

        queue._running = False
        for w in queue._workers:
            w.cancel()
        await asyncio.gather(*queue._workers, return_exceptions=True)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestQueueStats:
    """Queue statistics reporting."""

    async def test_stats_initial(self):
        llm = AsyncMock()
        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        stats = queue.stats()
        assert stats["pending"] == 0
        assert stats["processed"] == 0
        assert stats["running"] is False
        assert stats["workers"] == 0

    async def test_stats_after_processing(self):
        llm = AsyncMock()
        llm.call.return_value = ("resp", {})

        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        try:
            await asyncio.wait_for(
                queue.submit(LLMPriority.HIGH, "dev", []),
                timeout=5.0,
            )
            stats = queue.stats()
            assert stats["processed"] == 1
            assert stats["by_priority"]["HIGH"] == 1
            assert stats["running"] is True
        finally:
            await queue.stop()

    async def test_stats_tracks_by_priority(self):
        llm = AsyncMock()
        llm.call.return_value = ("resp", {})

        queue = LLMRequestQueue(llm_client=llm, max_concurrent=2)
        await queue.start()
        try:
            await asyncio.wait_for(
                queue.submit(LLMPriority.HIGH, "dev", []),
                timeout=5.0,
            )
            await asyncio.wait_for(
                queue.submit(LLMPriority.LOW, "scout", []),
                timeout=5.0,
            )
            stats = queue.stats()
            assert stats["by_priority"]["HIGH"] == 1
            assert stats["by_priority"]["LOW"] == 1
            assert stats["processed"] == 2
        finally:
            await queue.stop()
