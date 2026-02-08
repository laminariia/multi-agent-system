"""Unit tests for src.core.heartbeat.HeartbeatMonitor.

All tests use the mock Valkey and DB fixtures to avoid network calls.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import HeartbeatTimeoutError
from src.core.heartbeat import HeartbeatConfig, HeartbeatMonitor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_ping_records_heartbeat(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """ping() should update _last_seen with a current timestamp."""
    monitor = HeartbeatMonitor(valkey=mock_valkey, db_pool=mock_db_pool)

    before = time.time()
    await monitor.ping("scout", "scanning jobs")
    after = time.time()

    assert "scout" in monitor._last_seen
    assert before <= monitor._last_seen["scout"] <= after
    assert monitor._last_task["scout"] == "scanning jobs"

    # Valkey set should have been called for persistence.
    mock_valkey.set.assert_awaited()
    mock_valkey.publish.assert_awaited()


async def test_ping_resets_restart_count(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """A successful ping should reset the agent's restart counter to 0."""
    monitor = HeartbeatMonitor(valkey=mock_valkey, db_pool=mock_db_pool)
    monitor._restart_counts["scout"] = 2  # Simulate prior restarts.

    await monitor.ping("scout")

    assert monitor._restart_counts["scout"] == 0


async def test_get_agent_status_healthy(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """An agent that pinged recently should report status='healthy'."""
    monitor = HeartbeatMonitor(
        valkey=mock_valkey,
        db_pool=mock_db_pool,
        config=HeartbeatConfig(timeout_seconds=180),
    )

    await monitor.ping("bid", "generating proposal")
    status = await monitor.get_agent_status("bid")

    assert status["agent_name"] == "bid"
    assert status["status"] == "healthy"
    assert status["last_task"] == "generating proposal"
    assert status["restart_count"] == 0


async def test_get_agent_status_unknown(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """An agent that has never pinged should report status='unknown'."""
    monitor = HeartbeatMonitor(valkey=mock_valkey, db_pool=mock_db_pool)

    # Force the monitor to know the name without a real ping.
    monitor._last_seen["phantom"] = 0.0

    status = await monitor.get_agent_status("phantom")
    assert status["status"] == "unknown"


async def test_handle_timeout_increments_restart_count(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """_handle_timeout should increment the restart counter for the agent."""
    restart_cb = AsyncMock()
    monitor = HeartbeatMonitor(
        valkey=mock_valkey,
        db_pool=mock_db_pool,
        config=HeartbeatConfig(max_restarts=5),
        restart_callback=restart_cb,
    )
    monitor._last_seen["scout"] = time.time() - 999  # old heartbeat

    await monitor._handle_timeout("scout", elapsed=999.0)

    assert monitor._restart_counts["scout"] == 1
    restart_cb.assert_awaited_once_with("scout")


async def test_handle_timeout_exceeds_max_restarts_raises(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """When restart count exceeds max_restarts, HeartbeatTimeoutError should be raised."""
    hitl_cb = AsyncMock()
    monitor = HeartbeatMonitor(
        valkey=mock_valkey,
        db_pool=mock_db_pool,
        config=HeartbeatConfig(max_restarts=2),
        hitl_callback=hitl_cb,
    )

    # Simulate two prior timeout increments.
    monitor._restart_counts["scout"] = 2
    monitor._last_seen["scout"] = time.time() - 600

    with pytest.raises(HeartbeatTimeoutError) as exc_info:
        await monitor._handle_timeout("scout", elapsed=600.0)

    assert exc_info.value.restart_count == 3
    assert "scout" in str(exc_info.value)
    hitl_cb.assert_awaited_once()
