"""Unit tests for the orchestrator API routes and service layer.

Tests cover all endpoints of ``OrchestratorController`` plus the
``OrchestratorService`` business logic.  Goal-related methods use an
async mock session (goals now live in PostgreSQL).  File-based operations
(health, milestones, logs, runner state) are still mocked at the parser level.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.api.services.orchestrator import OrchestratorService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PARSERS = "src.api.services.orchestrator"


@pytest.fixture()
def svc() -> OrchestratorService:
    return OrchestratorService()


@pytest.fixture()
def sample_health() -> dict:
    return {
        "overall_grade": "A",
        "score": 93,
        "dimensions": {
            "code_completeness": {"grade": "A", "notes": "Good coverage"},
            "test_coverage": {"grade": "B+", "notes": "Needs more integration tests"},
        },
        "problems": [
            {"severity": "medium", "description": "Missing integration tests"},
        ],
    }


@pytest.fixture()
def sample_phases() -> list[dict]:
    return [
        {
            "number": 1,
            "title": "Foundation",
            "is_future": False,
            "milestones": [
                {"text": "Set up project structure", "done": True},
                {"text": "Implement Scout Agent", "done": True},
            ],
        },
        {
            "number": 2,
            "title": "Core Agents",
            "is_future": False,
            "milestones": [
                {"text": "Implement Dev Agent", "done": False},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Async DB mock helpers
# ---------------------------------------------------------------------------


def _make_goal(
    goal_id: str = "g_001",
    title: str = "Test goal",
    priority: str = "medium",
    category: str = "feature",
    status: str = "pending",
    result: str | None = None,
    completed_at=None,
    created_at=None,
) -> MagicMock:
    """Create a mock OrchestratorGoal ORM instance."""
    g = MagicMock()
    g.id = uuid.uuid4()
    g.goal_id = goal_id
    g.title = title
    g.priority = priority
    g.category = category
    g.status = status
    g.result = result
    g.completed_at = completed_at
    g.created_at = created_at
    return g


class _FakeScalars:
    """Mock for result.scalars().all()."""

    def __init__(self, items: list) -> None:
        self._items = items

    def all(self) -> list:
        return self._items


class _FakeResult:
    """Mock for session.execute() return value."""

    def __init__(self, value=None, items: list | None = None) -> None:
        self._value = value
        self._items = items or []

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._items)

    def all(self):
        return [(item,) for item in self._items]


class _FakeSession:
    """Lightweight async-compatible session mock.

    Routes mock results by inspecting the compiled statement's bound
    parameters and the query structure (COUNT, MAX, WHERE, SELECT).
    """

    def __init__(self, goals: list | None = None) -> None:
        self._goals = goals or []
        self._added: list = []
        self._deleted: list = []

    def _extract_status_param(self, stmt) -> str | None:
        """Extract the status literal from a WHERE clause."""
        try:
            compiled = stmt.compile(compile_kwargs={"literal_binds": True})
            compiled_str = str(compiled)
            for status in ("pending", "completed", "failed"):
                if f"'{status}'" in compiled_str:
                    return status
        except Exception:  # noqa: S110
            pass
        return None

    async def execute(self, stmt):
        """Route mock results based on the SQL query structure."""
        stmt_str = str(stmt).lower()

        # COUNT queries for status counts
        if "count" in stmt_str:
            status = self._extract_status_param(stmt)
            if status:
                return _FakeResult(value=len([g for g in self._goals if g.status == status]))
            return _FakeResult(value=0)

        # MAX query for goal_id generation
        if "max" in stmt_str:
            if self._goals:
                max_id = max(g.goal_id for g in self._goals)
                return _FakeResult(value=max_id)
            return _FakeResult(value=None)

        # SELECT with WHERE (for delete / single lookup)
        if "where" in stmt_str and "orchestrator_goals" in stmt_str:
            # Return first goal or None
            if self._goals:
                return _FakeResult(value=self._goals[0], items=[self._goals[0]])
            return _FakeResult(value=None)

        # Column-level SELECT for goal_id only (add_goal ID generation)
        # Matches: SELECT orchestrator_goals.goal_id FROM ...
        # But NOT: SELECT orchestrator_goals.id, orchestrator_goals.goal_id, ...
        if "goal_id" in stmt_str and "title" not in stmt_str and "count" not in stmt_str:
            return _FakeResult(items=[g.goal_id for g in self._goals])

        # General SELECT (list_goals)
        return _FakeResult(items=self._goals)

    def add(self, obj):
        self._added.append(obj)

    async def delete(self, obj):
        self._deleted.append(obj)

    async def flush(self):
        pass


# ===========================================================================
# get_status
# ===========================================================================


class TestGetStatus:
    """Tests for OrchestratorService.get_status()."""

    @pytest.mark.anyio()
    async def test_status_runner_dead(self, svc: OrchestratorService) -> None:
        session = _FakeSession()
        with patch(f"{PARSERS}.is_runner_alive", return_value=(False, None)), \
             patch(f"{PARSERS}.parse_health_report", return_value={}):
            result = await svc.get_status(session)

        assert result["alive"] is False
        assert result["pid"] is None
        assert result["uptime_seconds"] is None
        assert result["goals_pending"] == 0

    @pytest.mark.anyio()
    async def test_status_runner_alive(self, svc: OrchestratorService) -> None:
        goals = [
            _make_goal("g_001", status="pending"),
            _make_goal("g_002", status="completed"),
            _make_goal("g_003", status="failed"),
            _make_goal("g_004", status="pending"),
        ]
        session = _FakeSession(goals)

        mock_path = MagicMock(spec=Path)
        mock_stat = MagicMock()
        mock_stat.st_mtime = 1000.0

        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 1234)), \
             patch(f"{PARSERS}.parse_health_report", return_value={"overall_grade": "A", "score": 93}), \
             patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path), \
             patch(f"{PARSERS}.tail_file", return_value=["[2026-02-11] Mode: self-direct"]), \
             patch(f"{PARSERS}.PID_FILE") as mock_pid:
            mock_pid.stat.return_value = mock_stat
            result = await svc.get_status(session)

        assert result["alive"] is True
        assert result["pid"] == 1234
        assert result["mode"] == "self-direct"
        assert result["goals_pending"] == 2
        assert result["goals_completed"] == 1
        assert result["goals_failed"] == 1
        assert result["health_grade"] == "A"
        assert result["health_score"] == 93

    @pytest.mark.anyio()
    async def test_status_no_log_path(self, svc: OrchestratorService) -> None:
        session = _FakeSession()
        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 5678)), \
             patch(f"{PARSERS}.parse_health_report", return_value={}), \
             patch(f"{PARSERS}.get_runner_log_path", return_value=None):
            result = await svc.get_status(session)

        assert result["alive"] is True
        assert result["mode"] is None
        assert result["uptime_seconds"] is None


# ===========================================================================
# start_runner
# ===========================================================================


class TestStartRunner:
    """Tests for OrchestratorService.start_runner()."""

    def test_start_success(self, svc: OrchestratorService) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 9999

        with patch(f"{PARSERS}.is_runner_alive", return_value=(False, None)), \
             patch(f"{PARSERS}.RUNNER_SCRIPT") as mock_script, \
             patch(f"{PARSERS}.ORCH_DIR") as mock_dir, \
             patch(f"{PARSERS}.PID_FILE") as mock_pid, \
             patch("subprocess.Popen", return_value=mock_proc):
            mock_script.exists.return_value = True
            mock_dir.mkdir = MagicMock()
            mock_pid.write_text = MagicMock()

            result = svc.start_runner()

        assert result["status"] == "started"
        assert result["pid"] == 9999
        assert "agent-driven" in result["message"]

    def test_start_already_running(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 1234)):
            with pytest.raises(RuntimeError, match="already running"):
                svc.start_runner()

    def test_start_script_missing(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.is_runner_alive", return_value=(False, None)), \
             patch(f"{PARSERS}.RUNNER_SCRIPT") as mock_script:
            mock_script.exists.return_value = False

            with pytest.raises(FileNotFoundError, match="Runner script not found"):
                svc.start_runner()


# ===========================================================================
# stop_runner
# ===========================================================================


class TestStopRunner:
    """Tests for OrchestratorService.stop_runner()."""

    def test_stop_success(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 4321)), \
             patch("subprocess.run") as mock_run, \
             patch(f"{PARSERS}.PID_FILE") as mock_pid:
            mock_pid.unlink = MagicMock()
            result = svc.stop_runner()

        assert result["status"] == "stopped"
        assert "4321" in result["message"]
        mock_run.assert_called_once()

    def test_stop_not_running(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.is_runner_alive", return_value=(False, None)), \
             patch(f"{PARSERS}.PID_FILE") as mock_pid:
            mock_pid.unlink = MagicMock()

            with pytest.raises(RuntimeError, match="not running"):
                svc.stop_runner()

    def test_stop_taskkill_fails(self, svc: OrchestratorService) -> None:
        import subprocess

        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 1111)), \
             patch("subprocess.run", side_effect=subprocess.SubprocessError("kill failed")), \
             patch(f"{PARSERS}.PID_FILE"):
            with pytest.raises(RuntimeError, match="Failed to stop"):
                svc.stop_runner()


# ===========================================================================
# list_goals (async DB)
# ===========================================================================


class TestListGoals:
    """Tests for OrchestratorService.list_goals()."""

    @pytest.mark.anyio()
    async def test_all_goals(self, svc: OrchestratorService) -> None:
        goals = [
            _make_goal("g_001", status="completed"),
            _make_goal("g_002", status="pending"),
            _make_goal("g_003", status="failed"),
            _make_goal("g_004", status="pending"),
        ]
        session = _FakeSession(goals)
        result = await svc.list_goals(session)

        assert result["total"] == 4
        assert result["pending"] == 2
        assert result["completed"] == 1
        assert result["failed"] == 1

    @pytest.mark.anyio()
    async def test_empty_goals(self, svc: OrchestratorService) -> None:
        session = _FakeSession([])
        result = await svc.list_goals(session)

        assert result["total"] == 0
        assert result["pending"] == 0

    @pytest.mark.anyio()
    async def test_goal_dict_fields(self, svc: OrchestratorService) -> None:
        goals = [_make_goal("g_001", title="Test", priority="high", category="bugfix", status="pending")]
        session = _FakeSession(goals)
        result = await svc.list_goals(session)

        g = result["goals"][0]
        assert g["id"] == "g_001"
        assert g["title"] == "Test"
        assert g["priority"] == "high"
        assert g["category"] == "bugfix"
        assert g["status"] == "pending"


# ===========================================================================
# add_goal (async DB)
# ===========================================================================


class TestAddGoal:
    """Tests for OrchestratorService.add_goal()."""

    @pytest.mark.anyio()
    async def test_add_first_goal(self, svc: OrchestratorService) -> None:
        session = _FakeSession([])
        result = await svc.add_goal(session, title="New feature")

        assert result["id"] == "g_001"
        assert result["title"] == "New feature"
        assert result["message"] == "Goal added successfully"
        assert len(session._added) == 1

    @pytest.mark.anyio()
    async def test_add_auto_increments(self, svc: OrchestratorService) -> None:
        existing = [_make_goal("g_004")]
        session = _FakeSession(existing)
        result = await svc.add_goal(session, title="Next goal")

        assert result["id"] == "g_005"

    @pytest.mark.anyio()
    async def test_add_with_priority_and_category(self, svc: OrchestratorService) -> None:
        session = _FakeSession([])
        result = await svc.add_goal(session, title="Fix bug", priority="critical", category="bugfix")

        assert result["id"] == "g_001"
        added = session._added[0]
        assert added.priority == "critical"
        assert added.category == "bugfix"


# ===========================================================================
# delete_goal (async DB)
# ===========================================================================


class TestDeleteGoal:
    """Tests for OrchestratorService.delete_goal()."""

    @pytest.mark.anyio()
    async def test_delete_existing(self, svc: OrchestratorService) -> None:
        goal = _make_goal("g_003")
        session = _FakeSession([goal])
        result = await svc.delete_goal(session, "g_003")

        assert result["goal_id"] == "g_003"
        assert result["message"] == "Goal deleted successfully"
        assert len(session._deleted) == 1

    @pytest.mark.anyio()
    async def test_delete_not_found(self, svc: OrchestratorService) -> None:
        session = _FakeSession([])
        with pytest.raises(KeyError, match="Goal not found"):
            await svc.delete_goal(session, "g_999")


# ===========================================================================
# get_health
# ===========================================================================


class TestGetHealth:
    """Tests for OrchestratorService.get_health()."""

    def test_health_report(self, svc: OrchestratorService, sample_health: dict) -> None:
        with patch(f"{PARSERS}.parse_health_report", return_value=sample_health):
            result = svc.get_health()

        assert result["overall_grade"] == "A"
        assert result["score"] == 93
        assert "code_completeness" in result["dimensions"]
        assert result["dimensions"]["code_completeness"]["grade"] == "A"
        assert len(result["problems"]) == 1

    def test_health_empty(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.parse_health_report", return_value={}):
            result = svc.get_health()

        assert result["overall_grade"] == "?"
        assert result["score"] == 0
        assert result["dimensions"] == {}
        assert result["problems"] == []

    def test_health_missing_fields(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.parse_health_report", return_value={"score": 50}):
            result = svc.get_health()

        assert result["overall_grade"] == "?"
        assert result["score"] == 50


# ===========================================================================
# get_milestones
# ===========================================================================


class TestGetMilestones:
    """Tests for OrchestratorService.get_milestones()."""

    def test_milestones(self, svc: OrchestratorService, sample_phases: list) -> None:
        with patch(f"{PARSERS}.parse_vision_md", return_value=sample_phases):
            result = svc.get_milestones()

        assert len(result) == 2
        assert result[0]["number"] == 1
        assert len(result[0]["milestones"]) == 2
        assert result[0]["milestones"][0]["done"] is True

    def test_milestones_empty(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.parse_vision_md", return_value=[]):
            result = svc.get_milestones()

        assert result == []


# ===========================================================================
# get_logs
# ===========================================================================


class TestGetLogs:
    """Tests for OrchestratorService.get_logs()."""

    def test_logs_success(self, svc: OrchestratorService) -> None:
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.name = "runner_2026-02-11.log"

        raw_lines = [
            "[2026-02-11 10:00:00] [INFO] Session started",
            "[2026-02-11 10:00:05] [WARN] Low memory",
            "[2026-02-11 10:00:10] [ERROR] Task failed",
            "Plain log line without timestamp",
        ]

        with patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path), \
             patch(f"{PARSERS}.tail_file", return_value=raw_lines):
            result = svc.get_logs(n=20)

        assert result["total"] == 4
        assert result["log_file"] == "runner_2026-02-11.log"
        assert result["lines"][0]["level"] == "INFO"
        assert result["lines"][0]["timestamp"] == "2026-02-11 10:00:00"
        assert result["lines"][1]["level"] == "WARN"
        assert result["lines"][2]["level"] == "ERROR"
        assert result["lines"][3]["level"] == "INFO"  # default
        assert result["lines"][3]["timestamp"] is None

    def test_logs_no_file(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.get_runner_log_path", return_value=None):
            result = svc.get_logs()

        assert result["lines"] == []
        assert result["total"] == 0
        assert result["log_file"] is None

    def test_logs_file_not_exists(self, svc: OrchestratorService) -> None:
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False

        with patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path):
            result = svc.get_logs()

        assert result["lines"] == []

    def test_logs_with_date(self, svc: OrchestratorService) -> None:
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.name = "runner_2026-02-10.log"

        with patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path) as mock_get, \
             patch(f"{PARSERS}.tail_file", return_value=["line1"]):
            result = svc.get_logs(n=10, date="2026-02-10")

        mock_get.assert_called_once_with(date="2026-02-10")
        assert result["total"] == 1

    def test_logs_n_capped_at_200(self, svc: OrchestratorService) -> None:
        """Ensure the controller caps n at 200."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.name = "runner.log"

        with patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path), \
             patch(f"{PARSERS}.tail_file", return_value=[]) as mock_tail:
            svc.get_logs(n=200)

        mock_tail.assert_called_once_with(mock_path, 200)


# ===========================================================================
# Schema construction (verifies service → schema contract)
# ===========================================================================


class TestSchemaConstruction:
    """Verify that Pydantic schemas accept the dicts returned by the service layer."""

    @pytest.mark.anyio()
    async def test_status_schema_from_service(self, svc: OrchestratorService) -> None:
        from src.api.schemas import OrchestratorStatusSchema

        goals = [
            _make_goal("g_001", status="pending"),
            _make_goal("g_002", status="completed"),
        ]
        session = _FakeSession(goals)

        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 100)), \
             patch(f"{PARSERS}.parse_health_report", return_value={
                 "overall_grade": "A", "score": 93,
             }), \
             patch(f"{PARSERS}.get_runner_log_path", return_value=None):
            data = await svc.get_status(session)

        schema = OrchestratorStatusSchema(**data)
        assert schema.alive is True
        assert schema.pid == 100
        assert schema.goals_pending == 1
        assert schema.goals_completed == 1
        assert schema.health_grade == "A"

    @pytest.mark.anyio()
    async def test_goal_list_schema_from_service(self, svc: OrchestratorService) -> None:
        from src.api.schemas import GoalListResponseSchema, GoalSchema

        goals = [
            _make_goal("g_001", title="Fix login bug", priority="high", category="bugfix", status="completed"),
            _make_goal("g_002", title="Add dark mode", priority="medium", category="feature", status="pending"),
        ]
        session = _FakeSession(goals)
        data = await svc.list_goals(session)

        schema = GoalListResponseSchema(
            goals=[GoalSchema(**g) for g in data["goals"]],
            total=data["total"],
            pending=data["pending"],
            completed=data["completed"],
            failed=data["failed"],
        )
        assert schema.total == 2
        assert schema.pending == 1
        assert len(schema.goals) == 2

    def test_health_schema_from_service(self, svc: OrchestratorService, sample_health: dict) -> None:
        from src.api.schemas import HealthDimensionSchema, HealthProblemSchema, HealthReportSchema

        with patch(f"{PARSERS}.parse_health_report", return_value=sample_health):
            data = svc.get_health()

        schema = HealthReportSchema(
            overall_grade=data["overall_grade"],
            score=data["score"],
            dimensions={k: HealthDimensionSchema(**v) for k, v in data["dimensions"].items()},
            problems=[HealthProblemSchema(**p) for p in data["problems"]],
        )
        assert schema.overall_grade == "A"
        assert schema.score == 93
        assert "code_completeness" in schema.dimensions
        assert len(schema.problems) == 1

    def test_milestones_schema_from_service(self, svc: OrchestratorService, sample_phases: list) -> None:
        from src.api.schemas import MilestoneSchema, PhaseSchema

        with patch(f"{PARSERS}.parse_vision_md", return_value=sample_phases):
            data = svc.get_milestones()

        schemas = [
            PhaseSchema(
                number=p["number"],
                title=p["title"],
                is_future=p.get("is_future", False),
                milestones=[MilestoneSchema(**m) for m in p.get("milestones", [])],
            )
            for p in data
        ]
        assert len(schemas) == 2
        assert schemas[0].milestones[0].done is True
        assert schemas[1].milestones[0].done is False

    def test_logs_schema_from_service(self, svc: OrchestratorService) -> None:
        from src.api.schemas import LogLineSchema, LogResponseSchema

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.name = "runner.log"

        with patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path), \
             patch(f"{PARSERS}.tail_file", return_value=[
                 "[2026-02-11 10:00:00] [INFO] Test line",
             ]):
            data = svc.get_logs(n=10)

        schema = LogResponseSchema(
            lines=[LogLineSchema(**ln) for ln in data["lines"]],
            total=data["total"],
            log_file=data["log_file"],
        )
        assert schema.total == 1
        assert schema.lines[0].level == "INFO"
        assert schema.log_file == "runner.log"

    def test_start_response_schema(self, svc: OrchestratorService) -> None:
        from src.api.schemas import OrchestratorStartResponseSchema

        mock_proc = MagicMock()
        mock_proc.pid = 9999

        with patch(f"{PARSERS}.is_runner_alive", return_value=(False, None)), \
             patch(f"{PARSERS}.RUNNER_SCRIPT") as mock_script, \
             patch(f"{PARSERS}.ORCH_DIR") as mock_dir, \
             patch(f"{PARSERS}.PID_FILE") as mock_pid, \
             patch("subprocess.Popen", return_value=mock_proc):
            mock_script.exists.return_value = True
            mock_dir.mkdir = MagicMock()
            mock_pid.write_text = MagicMock()
            data = svc.start_runner()

        schema = OrchestratorStartResponseSchema(**data)
        assert schema.status == "started"
        assert schema.pid == 9999

    def test_stop_response_schema(self, svc: OrchestratorService) -> None:
        from src.api.schemas import OrchestratorStopResponseSchema

        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 4321)), \
             patch("subprocess.run"), \
             patch(f"{PARSERS}.PID_FILE") as mock_pid:
            mock_pid.unlink = MagicMock()
            data = svc.stop_runner()

        schema = OrchestratorStopResponseSchema(**data)
        assert schema.status == "stopped"

    @pytest.mark.anyio()
    async def test_delete_response_schema(self, svc: OrchestratorService) -> None:
        from src.api.schemas import GoalDeleteResponseSchema

        goal = _make_goal("g_005")
        session = _FakeSession([goal])
        data = await svc.delete_goal(session, "g_005")

        schema = GoalDeleteResponseSchema(**data)
        assert schema.goal_id == "g_005"
        assert schema.message == "Goal deleted successfully"
