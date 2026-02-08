"""Shared pytest fixtures for the Multi-Agent Service test suite.

Provides mock infrastructure (Valkey, DB, LLM, heartbeat, loop detector)
and pre-built state objects so that unit tests never touch real services.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.core.heartbeat import HeartbeatConfig, HeartbeatMonitor
from src.core.llm_client import CallMetrics, CostTracker, LLMClient
from src.core.loop_detector import LoopDetector
from src.core.state import AgentState, ProjectContext, create_initial_state


# ---------------------------------------------------------------------------
# Infrastructure mocks
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_valkey() -> AsyncMock:
    """AsyncMock of ``redis.asyncio.Redis`` (Valkey-compatible client).

    Common methods are pre-configured to return sensible defaults.
    """
    valkey = AsyncMock()
    valkey.set = AsyncMock(return_value=True)
    valkey.get = AsyncMock(return_value=None)
    valkey.delete = AsyncMock(return_value=1)
    valkey.publish = AsyncMock(return_value=1)
    valkey.incr = AsyncMock(return_value=1)
    valkey.expire = AsyncMock(return_value=True)
    valkey.hset = AsyncMock(return_value=1)
    valkey.hget = AsyncMock(return_value=None)
    valkey.pipeline = MagicMock(return_value=AsyncMock())

    # scan_iter returns an async iterator yielding nothing by default.
    async def _empty_scan(*_args: Any, **_kwargs: Any):
        return
        yield  # noqa: unreachable — makes this an async generator

    valkey.scan_iter = _empty_scan

    # ft() for RediSearch — return a mock that has .create_index and .search
    ft_mock = MagicMock()
    ft_mock.create_index = AsyncMock(return_value=True)
    ft_mock.search = AsyncMock(return_value=MagicMock(docs=[]))
    valkey.ft = MagicMock(return_value=ft_mock)

    return valkey


@pytest.fixture()
def mock_db_pool() -> AsyncMock:
    """AsyncMock of ``asyncpg.Pool``.

    ``acquire()`` returns an async context manager whose ``conn`` has
    ``execute``, ``fetch``, ``fetchrow`` stubs.
    """
    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="DELETE 0")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)

    # Make pool.acquire() usable as ``async with pool.acquire() as conn:``
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)

    # Expose the inner connection for test assertions.
    pool._test_conn = conn
    return pool


@pytest.fixture()
def mock_llm_client() -> AsyncMock:
    """AsyncMock of :class:`LLMClient`.

    ``call()`` returns a canned ``(AIMessage, CallMetrics)`` tuple by default.
    Tests can override ``mock_llm_client.call.return_value`` to customise.
    """
    client = AsyncMock(spec=LLMClient)
    default_response = AIMessage(content='{"result": "ok"}')
    default_metrics = CallMetrics(
        agent_name="test",
        model_id="gemini-3-flash",
        provider="google",
        tokens_input=100,
        tokens_output=50,
        cost_usd=0.0001,
        latency_ms=120.0,
        was_fallback=False,
        attempt=1,
    )
    client.call = AsyncMock(return_value=(default_response, default_metrics))
    client.cost_tracker = CostTracker()
    return client


@pytest.fixture()
def mock_heartbeat(mock_valkey: AsyncMock, mock_db_pool: AsyncMock) -> HeartbeatMonitor:
    """Real :class:`HeartbeatMonitor` wired to mock Valkey and DB.

    ``ping()`` works against the in-memory dicts without hitting any network.
    """
    return HeartbeatMonitor(
        valkey=mock_valkey,
        db_pool=mock_db_pool,
        config=HeartbeatConfig(
            interval_seconds=90,
            timeout_seconds=180,
            max_restarts=3,
            monitor_poll_seconds=30,
        ),
    )


@pytest.fixture()
def mock_loop_detector() -> LoopDetector:
    """Real :class:`LoopDetector` with generous limits for normal tests."""
    return LoopDetector(max_iterations=50, max_identical_steps=5)


# ---------------------------------------------------------------------------
# Project / State fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_project() -> ProjectContext:
    """A valid :class:`ProjectContext` for testing."""
    return ProjectContext(
        project_id="proj-test-001",
        job_id="job-test-001",
        platform="freelancer",
        client={
            "name": "Test Client",
            "rating": 4.8,
            "reviews": 25,
            "hire_rate": 0.85,
        },
        requirements="Build a responsive landing page with React and Tailwind CSS",
        budget=500.0,
        deadline=datetime(2026, 3, 15, tzinfo=timezone.utc),
    )


@pytest.fixture()
def sample_state(sample_project: ProjectContext) -> AgentState:
    """A valid :class:`AgentState` built from :func:`create_initial_state`."""
    return create_initial_state(
        project=sample_project,
        first_agent="scout",
        thread_id="thread-test-001",
    )
