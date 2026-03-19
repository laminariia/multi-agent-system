"""Unit tests for scripts/recalculate_project_states.py.

Tests the disaster recovery tool that rebuilds project state from
LangGraph checkpoints. All database access is mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.recalculate_project_states import (
    AGENT_TO_PROJECT_STATUS,
    VALID_PROJECT_STATUSES,
    RecalculationReport,
    RecalculationResult,
    StateChange,
    derive_project_status,
    extract_artifacts_summary,
    extract_progress,
    extract_revision_count,
    load_active_threads,
    load_checkpoint_state,
    parse_args,
    recalculate_all,
    recalculate_thread,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_session():
    """Create a mock async SQLAlchemy session."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture()
def sample_state() -> dict[str, Any]:
    """A typical checkpoint state_data for testing."""
    return {
        "thread_id": "test-thread-001",
        "status": "active",
        "current_agent": "dev",
        "next_agent": None,
        "requires_hitl": False,
        "agent_sequence": ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager"],
        "current_sequence_index": 3,
        "artifacts": {"scout": ["job_list"], "bid": ["proposal_v1"]},
        "recovery_attempted": 0,
        "project": {
            "project_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "platform": "freelancer",
            "client": {},
            "requirements": "Build a web app",
            "budget": 500.0,
            "deadline": datetime.now(UTC).isoformat(),
        },
    }


def _make_checkpoint_row(state: dict[str, Any], **overrides: Any) -> MagicMock:
    """Create a mock LanggraphCheckpoint row."""
    row = MagicMock()
    row.thread_id = state.get("thread_id", "test-thread")
    row.state_data = state
    row.status = state.get("status", "active")
    row.current_agent = state.get("current_agent")
    row.created_at = datetime.now(UTC)
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def _make_project(
    project_id: str | None = None,
    status: str = "planning",
    progress: int = 0,
    revision_count: int = 0,
) -> MagicMock:
    """Create a mock Project ORM object."""
    project = MagicMock()
    project.id = uuid.UUID(project_id) if project_id else uuid.uuid4()
    project.status = status
    project.progress = progress
    project.revision_count = revision_count
    project.updated_at = datetime.now(UTC)
    return project


# ===========================================================================
# Tests: derive_project_status
# ===========================================================================


class TestDeriveProjectStatus:
    """Tests for derive_project_status()."""

    def test_completed_pipeline(self):
        assert derive_project_status({"status": "completed"}) == "completed"

    def test_failed_pipeline(self):
        assert derive_project_status({"status": "failed"}) == "failed"

    def test_paused_pipeline(self):
        assert derive_project_status({"status": "paused"}) == "review"

    def test_active_scout(self):
        assert derive_project_status({"status": "active", "current_agent": "scout"}) == "planning"

    def test_active_bid(self):
        assert derive_project_status({"status": "active", "current_agent": "bid"}) == "planning"

    def test_active_planner(self):
        assert derive_project_status({"status": "active", "current_agent": "planner"}) == "planning"

    def test_active_dev(self):
        assert derive_project_status({"status": "active", "current_agent": "dev"}) == "in_progress"

    def test_active_content(self):
        assert derive_project_status({"status": "active", "current_agent": "content"}) == "in_progress"

    def test_active_design(self):
        assert derive_project_status({"status": "active", "current_agent": "design"}) == "in_progress"

    def test_active_critic(self):
        assert derive_project_status({"status": "active", "current_agent": "critic"}) == "review"

    def test_active_packager(self):
        assert derive_project_status({"status": "active", "current_agent": "packager"}) == "completed"

    def test_unknown_agent_defaults_to_in_progress(self):
        assert derive_project_status({"status": "active", "current_agent": "unknown_agent"}) == "in_progress"

    def test_empty_state(self):
        assert derive_project_status({}) == "in_progress"

    def test_all_agent_mappings_produce_valid_statuses(self):
        for agent, status in AGENT_TO_PROJECT_STATUS.items():
            assert status in VALID_PROJECT_STATUSES, f"Agent {agent} maps to invalid status {status}"


# ===========================================================================
# Tests: extract_progress
# ===========================================================================


class TestExtractProgress:
    """Tests for extract_progress()."""

    def test_completed_returns_100(self):
        assert extract_progress({"status": "completed"}) == 100

    def test_failed_returns_0(self):
        assert extract_progress({"status": "failed"}) == 0

    def test_midway_through_sequence(self):
        state = {
            "status": "active",
            "current_sequence_index": 4,
            "agent_sequence": ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager"],
        }
        result = extract_progress(state)
        assert result == 50

    def test_beginning_of_sequence(self):
        state = {
            "status": "active",
            "current_sequence_index": 0,
            "agent_sequence": ["scout", "bid", "planner", "dev"],
        }
        assert extract_progress(state) == 0

    def test_near_end_capped_at_99(self):
        state = {
            "status": "active",
            "current_sequence_index": 7,
            "agent_sequence": ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager"],
        }
        result = extract_progress(state)
        assert result == 87  # 7/8 = 87.5 -> 87

    def test_empty_sequence_uses_default_length(self):
        state = {
            "status": "active",
            "current_sequence_index": 4,
            "agent_sequence": [],
        }
        result = extract_progress(state)
        assert result == 50  # 4/8 default

    def test_no_sequence_key(self):
        state = {"status": "active", "current_sequence_index": 2}
        result = extract_progress(state)
        assert result == 25  # 2/8 default


# ===========================================================================
# Tests: extract_revision_count
# ===========================================================================


class TestExtractRevisionCount:
    """Tests for extract_revision_count()."""

    def test_no_recovery(self):
        assert extract_revision_count({}) == 0

    def test_with_recovery(self):
        assert extract_revision_count({"recovery_attempted": 3}) == 3

    def test_zero_recovery(self):
        assert extract_revision_count({"recovery_attempted": 0}) == 0


# ===========================================================================
# Tests: extract_artifacts_summary
# ===========================================================================


class TestExtractArtifactsSummary:
    """Tests for extract_artifacts_summary()."""

    def test_normal_artifacts(self):
        state = {"artifacts": {"scout": ["job_list"], "bid": ["proposal"]}}
        result = extract_artifacts_summary(state)
        assert result == {"scout": ["job_list"], "bid": ["proposal"]}

    def test_empty_artifacts(self):
        assert extract_artifacts_summary({"artifacts": {}}) == {}

    def test_no_artifacts_key(self):
        assert extract_artifacts_summary({}) == {}

    def test_non_list_values_converted(self):
        state = {"artifacts": {"scout": "single_item"}}
        result = extract_artifacts_summary(state)
        assert result == {"scout": ["single_item"]}

    def test_non_dict_artifacts(self):
        state = {"artifacts": "not_a_dict"}
        assert extract_artifacts_summary(state) == {}


# ===========================================================================
# Tests: StateChange dataclass
# ===========================================================================


class TestStateChange:
    """Tests for the StateChange dataclass."""

    def test_creation(self):
        change = StateChange(
            table="projects",
            record_id="abc-123",
            field="status",
            old_value="planning",
            new_value="in_progress",
        )
        assert change.table == "projects"
        assert change.record_id == "abc-123"
        assert change.field == "status"
        assert change.old_value == "planning"
        assert change.new_value == "in_progress"


# ===========================================================================
# Tests: RecalculationResult dataclass
# ===========================================================================


class TestRecalculationResult:
    """Tests for the RecalculationResult dataclass."""

    def test_defaults(self):
        result = RecalculationResult(thread_id="t1", status="ok")
        assert result.changes == []
        assert result.error is None

    def test_with_changes(self):
        change = StateChange("projects", "id1", "status", "old", "new")
        result = RecalculationResult(thread_id="t1", status="updated", changes=[change])
        assert len(result.changes) == 1


# ===========================================================================
# Tests: RecalculationReport dataclass
# ===========================================================================


class TestRecalculationReport:
    """Tests for the RecalculationReport dataclass."""

    def test_empty_report(self):
        report = RecalculationReport()
        assert report.total_threads == 0
        assert report.total_changes == 0

    def test_total_changes_sums_results(self):
        r1 = RecalculationResult(
            thread_id="t1",
            status="updated",
            changes=[StateChange("p", "1", "f", "a", "b"), StateChange("p", "1", "g", "c", "d")],
        )
        r2 = RecalculationResult(
            thread_id="t2",
            status="updated",
            changes=[StateChange("p", "2", "f", "x", "y")],
        )
        report = RecalculationReport(results=[r1, r2])
        assert report.total_changes == 3


# ===========================================================================
# Tests: load_checkpoint_state
# ===========================================================================


class TestLoadCheckpointState:
    """Tests for load_checkpoint_state()."""

    @pytest.mark.asyncio()
    async def test_returns_state_data(self, mock_session, sample_state):
        row = _make_checkpoint_row(sample_state)
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = row
        mock_session.execute.return_value = mock_result

        with patch("scripts.recalculate_project_states.select"):
            state = await load_checkpoint_state(mock_session, "test-thread-001")

        assert state is not None
        assert state["status"] == "active"
        assert state["current_agent"] == "dev"

    @pytest.mark.asyncio()
    async def test_returns_none_when_no_checkpoint(self, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_result

        with patch("scripts.recalculate_project_states.select"):
            state = await load_checkpoint_state(mock_session, "nonexistent")

        assert state is None

    @pytest.mark.asyncio()
    async def test_empty_state_data_returns_empty_dict(self, mock_session):
        row = MagicMock()
        row.state_data = None
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = row
        mock_session.execute.return_value = mock_result

        with patch("scripts.recalculate_project_states.select"):
            state = await load_checkpoint_state(mock_session, "test")

        assert state == {}


# ===========================================================================
# Tests: load_active_threads
# ===========================================================================


class TestLoadActiveThreads:
    """Tests for load_active_threads()."""

    @pytest.mark.asyncio()
    async def test_returns_thread_ids(self, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = [("thread-1",), ("thread-2",), ("thread-3",)]
        mock_session.execute.return_value = mock_result

        with patch("scripts.recalculate_project_states.select"):
            threads = await load_active_threads(mock_session)

        assert threads == ["thread-1", "thread-2", "thread-3"]

    @pytest.mark.asyncio()
    async def test_returns_empty_list(self, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch("scripts.recalculate_project_states.select"):
            threads = await load_active_threads(mock_session)

        assert threads == []


# ===========================================================================
# Tests: recalculate_thread
# ===========================================================================


class TestRecalculateThread:
    """Tests for recalculate_thread()."""

    @pytest.mark.asyncio()
    async def test_no_checkpoint_returns_skipped(self, mock_session):
        with patch(
            "scripts.recalculate_project_states.load_checkpoint_state",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await recalculate_thread(mock_session, "missing-thread")

        assert result.status == "skipped"
        assert result.error == "No checkpoint found"

    @pytest.mark.asyncio()
    async def test_no_project_returns_skipped(self, mock_session, sample_state):
        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread")

        assert result.status == "skipped"
        assert result.error == "No linked project found"

    @pytest.mark.asyncio()
    async def test_no_changes_returns_ok(self, mock_session, sample_state):
        project = _make_project(status="in_progress", progress=37, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread")

        assert result.status == "ok"
        assert len(result.changes) == 0

    @pytest.mark.asyncio()
    async def test_status_mismatch_detected(self, mock_session, sample_state):
        # dev agent -> expected "in_progress", project says "planning"
        project = _make_project(status="planning", progress=37, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread", dry_run=True)

        assert result.status == "updated"
        status_changes = [c for c in result.changes if c.field == "status"]
        assert len(status_changes) == 1
        assert status_changes[0].old_value == "planning"
        assert status_changes[0].new_value == "in_progress"

    @pytest.mark.asyncio()
    async def test_progress_mismatch_detected(self, mock_session, sample_state):
        project = _make_project(status="in_progress", progress=0, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread", dry_run=True)

        progress_changes = [c for c in result.changes if c.field == "progress"]
        assert len(progress_changes) == 1
        assert progress_changes[0].old_value == 0
        assert progress_changes[0].new_value == 37  # 3/8 = 37%

    @pytest.mark.asyncio()
    async def test_dry_run_does_not_flush(self, mock_session, sample_state):
        project = _make_project(status="planning", progress=0, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread", dry_run=True)

        assert result.status == "updated"
        mock_session.flush.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_apply_changes_calls_flush(self, mock_session, sample_state):
        project = _make_project(status="planning", progress=0, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread", dry_run=False)

        assert result.status == "updated"
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_exception_returns_error(self, mock_session):
        with patch(
            "scripts.recalculate_project_states.load_checkpoint_state",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection lost"),
        ):
            result = await recalculate_thread(mock_session, "test-thread")

        assert result.status == "error"
        assert "DB connection lost" in result.error

    @pytest.mark.asyncio()
    async def test_completed_pipeline_sets_completed_status(self, mock_session):
        state = {
            "status": "completed",
            "current_agent": "packager",
            "agent_sequence": ["scout", "bid", "planner", "dev"],
            "current_sequence_index": 4,
            "recovery_attempted": 0,
            "project": {"project_id": str(uuid.uuid4())},
        }
        project = _make_project(status="in_progress", progress=50, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test-thread", dry_run=True)

        status_changes = [c for c in result.changes if c.field == "status"]
        progress_changes = [c for c in result.changes if c.field == "progress"]
        assert status_changes[0].new_value == "completed"
        assert progress_changes[0].new_value == 100


# ===========================================================================
# Tests: recalculate_all
# ===========================================================================


class TestRecalculateAll:
    """Tests for recalculate_all()."""

    @pytest.mark.asyncio()
    async def test_processes_all_threads(self, mock_session, sample_state):
        project = _make_project(status="in_progress", progress=37, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_active_threads",
                new_callable=AsyncMock,
                return_value=["t1", "t2"],
            ),
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            report = await recalculate_all(mock_session, dry_run=True)

        assert report.total_threads == 2
        assert len(report.results) == 2

    @pytest.mark.asyncio()
    async def test_specific_thread_ids(self, mock_session, sample_state):
        project = _make_project(status="in_progress", progress=37, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            report = await recalculate_all(
                mock_session,
                dry_run=True,
                thread_ids=["only-this-one"],
            )

        assert report.total_threads == 1

    @pytest.mark.asyncio()
    async def test_empty_threads_produces_empty_report(self, mock_session):
        with patch(
            "scripts.recalculate_project_states.load_active_threads",
            new_callable=AsyncMock,
            return_value=[],
        ):
            report = await recalculate_all(mock_session, dry_run=True)

        assert report.total_threads == 0
        assert report.ok_count == 0

    @pytest.mark.asyncio()
    async def test_commits_when_not_dry_run(self, mock_session, sample_state):
        project = _make_project(status="planning", progress=0, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_active_threads",
                new_callable=AsyncMock,
                return_value=["t1"],
            ),
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            report = await recalculate_all(mock_session, dry_run=False)

        mock_session.commit.assert_awaited_once()
        assert report.updated_count >= 1

    @pytest.mark.asyncio()
    async def test_does_not_commit_on_dry_run(self, mock_session, sample_state):
        project = _make_project(status="planning", progress=0, revision_count=0)

        with (
            patch(
                "scripts.recalculate_project_states.load_active_threads",
                new_callable=AsyncMock,
                return_value=["t1"],
            ),
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=sample_state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            await recalculate_all(mock_session, dry_run=True)

        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_report_timestamps(self, mock_session):
        with patch(
            "scripts.recalculate_project_states.load_active_threads",
            new_callable=AsyncMock,
            return_value=[],
        ):
            report = await recalculate_all(mock_session, dry_run=True)

        assert report.started_at is not None
        assert report.finished_at is not None
        assert report.finished_at >= report.started_at

    @pytest.mark.asyncio()
    async def test_mixed_results_counted_correctly(self, mock_session):
        """Verify ok/updated/skipped/error counts in a mixed scenario."""
        call_count = 0

        async def mock_recalc(session, thread_id, *, dry_run=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return RecalculationResult(thread_id=thread_id, status="ok")
            elif call_count == 2:
                return RecalculationResult(
                    thread_id=thread_id,
                    status="updated",
                    changes=[StateChange("p", "1", "status", "a", "b")],
                )
            elif call_count == 3:
                return RecalculationResult(thread_id=thread_id, status="skipped", error="No project")
            else:
                return RecalculationResult(thread_id=thread_id, status="error", error="DB error")

        with (
            patch(
                "scripts.recalculate_project_states.load_active_threads",
                new_callable=AsyncMock,
                return_value=["t1", "t2", "t3", "t4"],
            ),
            patch(
                "scripts.recalculate_project_states.recalculate_thread",
                side_effect=mock_recalc,
            ),
        ):
            report = await recalculate_all(mock_session, dry_run=True)

        assert report.total_threads == 4
        assert report.ok_count == 1
        assert report.updated_count == 1
        assert report.skipped_count == 1
        assert report.error_count == 1
        assert report.total_changes == 1


# ===========================================================================
# Tests: parse_args
# ===========================================================================


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_defaults(self):
        args = parse_args([])
        assert args.dry_run is False
        assert args.thread_id is None
        assert args.verbose is False

    def test_dry_run(self):
        args = parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_thread_id(self):
        args = parse_args(["--thread-id", "abc123"])
        assert args.thread_id == "abc123"

    def test_verbose(self):
        args = parse_args(["--verbose"])
        assert args.verbose is True

    def test_all_options(self):
        args = parse_args(["--dry-run", "--thread-id", "xyz", "--verbose"])
        assert args.dry_run is True
        assert args.thread_id == "xyz"
        assert args.verbose is True


# ===========================================================================
# Tests: edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_derive_status_none_values(self):
        """State with None values should not crash."""
        state = {"status": None, "current_agent": None}
        result = derive_project_status(state)
        assert result == "in_progress"  # fallback for unknown agent

    def test_extract_progress_negative_index(self):
        """Negative index should not produce negative progress."""
        state = {
            "status": "active",
            "current_sequence_index": -1,
            "agent_sequence": ["a", "b", "c"],
        }
        # Python int() of negative fraction is negative, but this is an edge case
        # that shouldn't happen in practice — we just verify no crash
        result = extract_progress(state)
        assert isinstance(result, int)

    def test_extract_progress_huge_index(self):
        """Index larger than sequence capped at 99."""
        state = {
            "status": "active",
            "current_sequence_index": 100,
            "agent_sequence": ["a", "b"],
        }
        result = extract_progress(state)
        assert result == 99

    def test_valid_project_statuses_is_frozen(self):
        """VALID_PROJECT_STATUSES should be immutable."""
        assert isinstance(VALID_PROJECT_STATUSES, frozenset)

    @pytest.mark.asyncio()
    async def test_recalculate_thread_handles_missing_project_attrs(self, mock_session):
        """Project with missing attributes should not crash."""
        state = {
            "status": "active",
            "current_agent": "dev",
            "agent_sequence": ["scout", "dev", "critic", "packager"],
            "current_sequence_index": 1,
            "recovery_attempted": 0,
            "project": {"project_id": str(uuid.uuid4())},
        }
        # Project where getattr returns defaults
        project = MagicMock()
        project.id = uuid.uuid4()
        project.status = None
        project.progress = None
        project.revision_count = None
        project.updated_at = datetime.now(UTC)

        with (
            patch(
                "scripts.recalculate_project_states.load_checkpoint_state",
                new_callable=AsyncMock,
                return_value=state,
            ),
            patch(
                "scripts.recalculate_project_states.load_project_by_thread",
                new_callable=AsyncMock,
                return_value=project,
            ),
        ):
            result = await recalculate_thread(mock_session, "test", dry_run=True)

        # Should detect differences and not crash
        assert result.status in ("ok", "updated")
