"""Tests for the HeartbeatMonitor background monitoring loop.

Target: src/core/heartbeat.py:161-305 — _monitor_loop, _check_timeouts.
Pattern: tests/unit/test_heartbeat.py — mock Valkey/DB fixtures.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import HeartbeatTimeoutError
from src.core.heartbeat import HeartbeatConfig, HeartbeatMonitor

pytestmark = pytest.mark.asyncio

# Very fast config to keep tests under 1 second.
_FAST_CONFIG = HeartbeatConfig(
    interval_seconds=1,
    timeout_seconds=0.05,
    monitor_poll_seconds=0.02,
    max_restarts=2,
)


@pytest.fixture()
def monitor(mock_valkey, mock_db_pool) -> HeartbeatMonitor:
    """HeartbeatMonitor wired to mock infrastructure with fast timing."""
    return HeartbeatMonitor(
        valkey=mock_valkey,
        db_pool=mock_db_pool,
        config=_FAST_CONFIG,
    )


# ===== Lifecycle =============================================================


async def test_start_creates_background_task(monitor):
    """start_monitoring() creates an asyncio.Task stored in _monitor_task."""
    await monitor.start_monitoring()
    assert monitor._monitor_task is not None
    assert isinstance(monitor._monitor_task, asyncio.Task)
    await monitor.stop_monitoring()


async def test_stop_cancels_task(monitor):
    """stop_monitoring() cancels task and resets _monitor_task to None."""
    await monitor.start_monitoring()
    assert monitor._monitor_task is not None
    await monitor.stop_monitoring()
    assert monitor._monitor_task is None


async def test_start_idempotent(monitor):
    """Calling start_monitoring twice → same task, no duplicate."""
    await monitor.start_monitoring()
    task1 = monitor._monitor_task
    await monitor.start_monitoring()
    task2 = monitor._monitor_task
    assert task1 is task2
    await monitor.stop_monitoring()


# ===== Timeout detection =====================================================


async def test_stale_agent_triggers_restart(monitor):
    """Agent with stale heartbeat → restart_callback invoked."""
    restart_cb = AsyncMock()
    monitor._restart_callback = restart_cb

    # Seed an agent whose heartbeat is well beyond timeout
    monitor._last_seen["slow_agent"] = time.time() - 1.0
    monitor._restart_counts["slow_agent"] = 0

    await monitor._check_timeouts()
    restart_cb.assert_awaited_once_with("slow_agent")


async def test_healthy_agent_no_timeout(monitor):
    """Agent with recent heartbeat → no restart triggered."""
    restart_cb = AsyncMock()
    monitor._restart_callback = restart_cb

    await monitor.ping("fresh_agent", "working")
    await monitor._check_timeouts()
    restart_cb.assert_not_awaited()


async def test_loop_detects_timeout_within_poll(monitor):
    """Background loop detects stale agent automatically within one poll cycle."""
    restart_cb = AsyncMock()
    monitor._restart_callback = restart_cb

    # Pre-seed a stale agent
    monitor._last_seen["stale"] = time.time() - 1.0
    monitor._restart_counts["stale"] = 0

    await monitor.start_monitoring()
    await asyncio.sleep(0.08)  # at least one poll cycle (0.02s)
    await monitor.stop_monitoring()

    restart_cb.assert_awaited()


async def test_loop_survives_check_error(monitor):
    """_check_timeouts raising → loop continues (doesn't crash)."""
    call_count = 0

    async def failing_check():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure")

    monitor._check_timeouts = failing_check  # type: ignore[assignment]
    await monitor.start_monitoring()
    await asyncio.sleep(0.15)  # generous margin for ≥2 poll cycles (0.02s each)
    await monitor.stop_monitoring()
    assert call_count >= 2, "Loop should survive the first error and keep polling"


# ===== Full lifecycle: ping → timeout → restart × N → HITL ==================


async def test_full_lifecycle_ping_timeout_dead(mock_valkey, mock_db_pool):
    """ping → stale → restart ×2 → exceed max_restarts → HITL alert."""
    restart_cb = AsyncMock()
    hitl_cb = AsyncMock()

    mon = HeartbeatMonitor(
        valkey=mock_valkey,
        db_pool=mock_db_pool,
        config=HeartbeatConfig(
            interval_seconds=1,
            timeout_seconds=0.02,
            monitor_poll_seconds=0.01,
            max_restarts=2,
        ),
        restart_callback=restart_cb,
        hitl_callback=hitl_cb,
    )

    # 1. Agent pings
    await mon.ping("agent_x", "task_a")
    assert mon._restart_counts["agent_x"] == 0

    # 2. Wait for timeout
    await asyncio.sleep(0.05)

    # 3. First check → restart attempt #1
    await mon._check_timeouts()
    assert mon._restart_counts["agent_x"] == 1
    assert restart_cb.await_count == 1

    # 4. Still stale → restart attempt #2
    await mon._check_timeouts()
    assert mon._restart_counts["agent_x"] == 2
    assert restart_cb.await_count == 2

    # 5. Exceeded max_restarts → HITL + HeartbeatTimeoutError
    with pytest.raises(HeartbeatTimeoutError):
        await mon._check_timeouts()

    hitl_cb.assert_awaited_once()
    assert hitl_cb.call_args[0][0] == "agent_x"
