"""Unit tests for Crash Recovery (src/core/crash_recovery.py).

Tests cover: find stale threads, resume from crash, entry node detection,
crash HITL alerts, startup recovery orchestration, and edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.crash_recovery import (
    MAX_CRASH_RETRIES,
    STALE_THRESHOLD_MINUTES,
    CrashRecoveryResult,
    StaleThread,
    _determine_entry_node,
    create_crash_hitl,
    find_stale_threads,
    resume_from_crash,
    startup_crash_recovery,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_checkpoint_row(
    *,
    thread_id: str = "thread-1",
    status: str = "active",
    current_agent: str | None = "dev",
    requires_hitl: bool = False,
    state_data: dict | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    """Create a mock checkpoint row from DB query."""
    row = MagicMock()
    row.thread_id = thread_id
    row.status = status
    row.current_agent = current_agent
    row.requires_hitl = requires_hitl
    row.state_data = state_data or {
        "status": status,
        "current_agent": current_agent,
    }
    row.created_at = created_at or (datetime.now(UTC) - timedelta(minutes=10))
    return row


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestCrashRecoveryConstants:
    """Verify crash recovery constants match spec."""

    def test_max_crash_retries(self):
        assert MAX_CRASH_RETRIES == 3

    def test_stale_threshold_minutes(self):
        assert STALE_THRESHOLD_MINUTES == 5


# ---------------------------------------------------------------------------
# find_stale_threads
# ---------------------------------------------------------------------------


class TestFindStaleThreads:
    """Find threads stuck in 'active' status without recent heartbeat."""

    async def test_returns_stale_active_threads(self):
        stale_row = _make_checkpoint_row(
            thread_id="thread-stale",
            status="active",
            current_agent="dev",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [stale_row]
        session.execute.return_value = result_mock

        threads = await find_stale_threads(session)

        assert len(threads) == 1
        assert threads[0].thread_id == "thread-stale"
        assert threads[0].status == "active"

    async def test_excludes_hitl_pending_threads(self):
        """Threads with requires_hitl=True should not be auto-resumed."""
        _make_checkpoint_row(
            thread_id="thread-hitl",
            status="active",
            requires_hitl=True,
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session = AsyncMock()
        result_mock = MagicMock()
        # The query itself should exclude requires_hitl=True
        result_mock.scalars.return_value.all.return_value = []
        session.execute.return_value = result_mock

        threads = await find_stale_threads(session)

        assert len(threads) == 0

    async def test_returns_empty_when_no_stale(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute.return_value = result_mock

        threads = await find_stale_threads(session)

        assert threads == []

    async def test_returns_stale_thread_dataclass(self):
        row = _make_checkpoint_row(
            thread_id="t-1",
            current_agent="scout",
            state_data={"status": "active", "current_agent": "scout"},
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]
        session.execute.return_value = result_mock

        threads = await find_stale_threads(session)

        assert isinstance(threads[0], StaleThread)
        assert threads[0].current_agent == "scout"

    async def test_multiple_stale_threads(self):
        rows = [
            _make_checkpoint_row(thread_id="t-1", current_agent="dev"),
            _make_checkpoint_row(thread_id="t-2", current_agent="content"),
            _make_checkpoint_row(thread_id="t-3", current_agent="scout"),
        ]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows
        session.execute.return_value = result_mock

        threads = await find_stale_threads(session)

        assert len(threads) == 3
        thread_ids = {t.thread_id for t in threads}
        assert thread_ids == {"t-1", "t-2", "t-3"}


# ---------------------------------------------------------------------------
# _determine_entry_node
# ---------------------------------------------------------------------------


class TestDetermineEntryNode:
    """Entry node routing for crash resume."""

    def test_uses_current_agent(self):
        state = {"current_agent": "dev", "next_agent": "content"}
        assert _determine_entry_node(state) == "dev_node"

    def test_falls_back_to_next_agent(self):
        state = {"next_agent": "critic"}
        assert _determine_entry_node(state) == "critic_node"

    def test_falls_back_to_scout_node(self):
        state = {}
        assert _determine_entry_node(state) == "scout_node"

    def test_current_agent_none_uses_next_agent(self):
        state = {"current_agent": None, "next_agent": "planner"}
        assert _determine_entry_node(state) == "planner_node"


# ---------------------------------------------------------------------------
# resume_from_crash
# ---------------------------------------------------------------------------


class TestResumeFromCrash:
    """Resume a crashed pipeline thread from checkpoint."""

    async def test_no_checkpoint_returns_no_checkpoint(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        session.execute.return_value = result_mock

        result = await resume_from_crash("thread-missing", session)

        assert result.action == "no_checkpoint"
        assert result.thread_id == "thread-missing"

    async def test_hitl_pending_returns_wait(self):
        row = _make_checkpoint_row(
            thread_id="thread-hitl",
            status="active",
            requires_hitl=True,
            state_data={"status": "paused", "requires_hitl": True, "hitl_request_id": "h-123"},
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = row
        session.execute.return_value = result_mock

        result = await resume_from_crash("thread-hitl", session)

        assert result.action == "wait_for_hitl"

    async def test_failed_max_retries_escalates(self):
        row = _make_checkpoint_row(
            thread_id="thread-failed",
            status="active",
            state_data={
                "status": "failed",
                "current_agent": "dev",
                "_crash_retry_count": 3,
            },
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = row
        session.execute.return_value = result_mock

        with patch("src.core.crash_recovery.create_crash_hitl", new_callable=AsyncMock) as mock_hitl:
            result = await resume_from_crash("thread-failed", session)

        assert result.action == "escalated"
        mock_hitl.assert_called_once()

    @patch("src.core.graph.build_full_pipeline_graph")
    async def test_active_thread_resumes(self, mock_build):
        row = _make_checkpoint_row(
            thread_id="thread-active",
            status="active",
            current_agent="dev",
            state_data={"status": "active", "current_agent": "dev"},
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = row
        session.execute.return_value = result_mock

        # Mock graph invoke
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {"status": "completed", "current_agent": "packager"}
        mock_build.return_value = mock_graph

        result = await resume_from_crash("thread-active", session)

        assert result.action == "resumed"
        assert result.final_status == "completed"
        mock_graph.ainvoke.assert_called_once()

    @patch("src.core.graph.build_full_pipeline_graph")
    async def test_failed_thread_retries_with_incremented_count(self, mock_build):
        row = _make_checkpoint_row(
            thread_id="thread-retry",
            status="active",
            state_data={
                "status": "failed",
                "current_agent": "content",
                "_crash_retry_count": 1,
            },
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = row
        session.execute.return_value = result_mock

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {"status": "completed"}
        mock_build.return_value = mock_graph

        result = await resume_from_crash("thread-retry", session)

        assert result.action == "resumed"
        # The state passed to ainvoke should have incremented retry count
        invoked_state = mock_graph.ainvoke.call_args[0][0]
        assert invoked_state["_crash_retry_count"] == 2
        assert invoked_state["status"] == "active"

    @patch("src.core.graph.build_full_pipeline_graph")
    async def test_resume_graph_error_returns_error(self, mock_build):
        row = _make_checkpoint_row(
            thread_id="thread-err",
            status="active",
            state_data={"status": "active", "current_agent": "dev"},
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = row
        session.execute.return_value = result_mock

        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = RuntimeError("LLM timeout")
        mock_build.return_value = mock_graph

        result = await resume_from_crash("thread-err", session)

        assert result.action == "error"
        assert "LLM timeout" in result.error


# ---------------------------------------------------------------------------
# create_crash_hitl
# ---------------------------------------------------------------------------


class TestCreateCrashHITL:
    """Create HITL alert for unrecoverable crash."""

    async def test_creates_hitl_entry(self):
        session = AsyncMock()
        state = {
            "current_agent": "dev",
            "status": "failed",
            "_crash_retry_count": 3,
        }

        await create_crash_hitl("thread-crash", state, session)

        session.add.assert_called_once()
        hitl_entry = session.add.call_args[0][0]
        assert hitl_entry.type == "crash_recovery"
        assert hitl_entry.status == "pending"
        assert hitl_entry.priority == "urgent"

    async def test_hitl_payload_contains_thread_info(self):
        session = AsyncMock()
        state = {
            "current_agent": "content",
            "status": "failed",
            "_crash_retry_count": 3,
            "_last_error": "OOM killed",
        }

        await create_crash_hitl("thread-oom", state, session)

        hitl_entry = session.add.call_args[0][0]
        payload = hitl_entry.payload
        assert payload["thread_id"] == "thread-oom"
        assert payload["current_agent"] == "content"
        assert payload["retry_count"] == 3

    async def test_hitl_available_actions(self):
        session = AsyncMock()
        state = {"current_agent": "dev", "status": "failed"}

        await create_crash_hitl("thread-x", state, session)

        hitl_entry = session.add.call_args[0][0]
        assert "retry" in hitl_entry.available_actions
        assert "skip_agent" in hitl_entry.available_actions
        assert "abort_pipeline" in hitl_entry.available_actions


# ---------------------------------------------------------------------------
# startup_crash_recovery
# ---------------------------------------------------------------------------


class TestStartupCrashRecovery:
    """Startup recovery orchestration."""

    @patch("src.core.crash_recovery.find_stale_threads", new_callable=AsyncMock)
    async def test_no_stale_threads(self, mock_find):
        mock_find.return_value = []
        session = AsyncMock()

        summary = await startup_crash_recovery(session)

        assert summary["found"] == 0
        assert summary["resumed"] == 0

    @patch("src.core.crash_recovery.resume_from_crash", new_callable=AsyncMock)
    @patch("src.core.crash_recovery.find_stale_threads", new_callable=AsyncMock)
    async def test_resumes_stale_threads(self, mock_find, mock_resume):
        mock_find.return_value = [
            StaleThread(
                thread_id="t-1",
                status="active",
                current_agent="dev",
                state_data={"status": "active"},
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            ),
        ]
        mock_resume.return_value = CrashRecoveryResult(action="resumed", thread_id="t-1", final_status="completed")
        session = AsyncMock()

        summary = await startup_crash_recovery(session)

        assert summary["found"] == 1
        assert summary["resumed"] == 1
        mock_resume.assert_called_once()

    @patch("src.core.crash_recovery.resume_from_crash", new_callable=AsyncMock)
    @patch("src.core.crash_recovery.find_stale_threads", new_callable=AsyncMock)
    async def test_counts_escalated_threads(self, mock_find, mock_resume):
        mock_find.return_value = [
            StaleThread(
                thread_id="t-fail",
                status="active",
                current_agent="dev",
                state_data={"status": "failed", "_crash_retry_count": 3},
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            ),
        ]
        mock_resume.return_value = CrashRecoveryResult(action="escalated", thread_id="t-fail")
        session = AsyncMock()

        summary = await startup_crash_recovery(session)

        assert summary["found"] == 1
        assert summary["escalated"] == 1
        assert summary["resumed"] == 0

    @patch("src.core.crash_recovery.resume_from_crash", new_callable=AsyncMock)
    @patch("src.core.crash_recovery.find_stale_threads", new_callable=AsyncMock)
    async def test_handles_resume_exception_gracefully(self, mock_find, mock_resume):
        mock_find.return_value = [
            StaleThread(
                thread_id="t-boom",
                status="active",
                current_agent="scout",
                state_data={"status": "active"},
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            ),
        ]
        mock_resume.side_effect = RuntimeError("DB connection lost")
        session = AsyncMock()

        # Should NOT raise — graceful handling
        summary = await startup_crash_recovery(session)

        assert summary["found"] == 1
        assert summary["errors"] == 1

    @patch("src.core.crash_recovery.resume_from_crash", new_callable=AsyncMock)
    @patch("src.core.crash_recovery.find_stale_threads", new_callable=AsyncMock)
    async def test_multiple_threads_mixed_results(self, mock_find, mock_resume):
        mock_find.return_value = [
            StaleThread("t-1", "active", "dev", {}, datetime.now(UTC) - timedelta(minutes=10)),
            StaleThread("t-2", "active", "content", {}, datetime.now(UTC) - timedelta(minutes=15)),
            StaleThread("t-3", "active", "scout", {}, datetime.now(UTC) - timedelta(minutes=20)),
        ]
        mock_resume.side_effect = [
            CrashRecoveryResult(action="resumed", thread_id="t-1", final_status="completed"),
            CrashRecoveryResult(action="escalated", thread_id="t-2"),
            CrashRecoveryResult(action="wait_for_hitl", thread_id="t-3"),
        ]
        session = AsyncMock()

        summary = await startup_crash_recovery(session)

        assert summary["found"] == 3
        assert summary["resumed"] == 1
        assert summary["escalated"] == 1
        assert summary["skipped"] == 1
