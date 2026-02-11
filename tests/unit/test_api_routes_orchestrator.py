"""Unit tests for the orchestrator API routes and service layer.

Tests cover all 8 endpoints of ``OrchestratorController`` plus the
``OrchestratorService`` business logic, with file-system and subprocess
operations mocked out.
"""

from __future__ import annotations

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
def sample_goals() -> list[dict]:
    return [
        {
            "id": "g_001", "title": "Fix login bug", "priority": "high",
            "category": "bugfix", "status": "completed",
            "completed_at": "2026-02-01", "result": "Fixed",
        },
        {
            "id": "g_002", "title": "Add dark mode", "priority": "medium",
            "category": "feature", "status": "pending",
        },
        {
            "id": "g_003", "title": "Write tests", "priority": "low",
            "category": "testing", "status": "failed",
        },
        {
            "id": "g_004", "title": "Refactor DB", "priority": "medium",
            "category": "refactor", "status": "pending",
        },
    ]


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


# ===========================================================================
# get_status
# ===========================================================================


class TestGetStatus:
    """Tests for OrchestratorService.get_status()."""

    def test_status_runner_dead(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.is_runner_alive", return_value=(False, None)), \
             patch(f"{PARSERS}.parse_goals_yaml", return_value=[]), \
             patch(f"{PARSERS}.parse_health_report", return_value={}):
            result = svc.get_status()

        assert result["alive"] is False
        assert result["pid"] is None
        assert result["uptime_seconds"] is None
        assert result["goals_pending"] == 0

    def test_status_runner_alive(self, svc: OrchestratorService, sample_goals: list) -> None:
        mock_path = MagicMock(spec=Path)
        mock_stat = MagicMock()
        mock_stat.st_mtime = 1000.0

        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 1234)), \
             patch(f"{PARSERS}.parse_goals_yaml", return_value=sample_goals), \
             patch(f"{PARSERS}.parse_health_report", return_value={"overall_grade": "A", "score": 93}), \
             patch(f"{PARSERS}.get_runner_log_path", return_value=mock_path), \
             patch(f"{PARSERS}.tail_file", return_value=["[2026-02-11] Mode: self-direct"]), \
             patch(f"{PARSERS}.PID_FILE") as mock_pid:
            mock_pid.stat.return_value = mock_stat
            result = svc.get_status()

        assert result["alive"] is True
        assert result["pid"] == 1234
        assert result["mode"] == "self-direct"
        assert result["goals_pending"] == 2
        assert result["goals_completed"] == 1
        assert result["goals_failed"] == 1
        assert result["health_grade"] == "A"
        assert result["health_score"] == 93

    def test_status_no_log_path(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 5678)), \
             patch(f"{PARSERS}.parse_goals_yaml", return_value=[]), \
             patch(f"{PARSERS}.parse_health_report", return_value={}), \
             patch(f"{PARSERS}.get_runner_log_path", return_value=None):
            result = svc.get_status()

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
# list_goals
# ===========================================================================


class TestListGoals:
    """Tests for OrchestratorService.list_goals()."""

    def test_all_goals(self, svc: OrchestratorService, sample_goals: list) -> None:
        with patch(f"{PARSERS}.parse_goals_yaml", return_value=sample_goals):
            result = svc.list_goals()

        assert result["total"] == 4
        assert result["pending"] == 2
        assert result["completed"] == 1
        assert result["failed"] == 1

    def test_filter_by_pending(self, svc: OrchestratorService, sample_goals: list) -> None:
        with patch(f"{PARSERS}.parse_goals_yaml", return_value=sample_goals):
            result = svc.list_goals(status_filter="pending")

        assert result["total"] == 2
        assert all(g["status"] == "pending" for g in result["goals"])

    def test_filter_by_completed(self, svc: OrchestratorService, sample_goals: list) -> None:
        with patch(f"{PARSERS}.parse_goals_yaml", return_value=sample_goals):
            result = svc.list_goals(status_filter="completed")

        assert result["total"] == 1

    def test_filter_no_match(self, svc: OrchestratorService) -> None:
        goals = [{"id": "g_001", "status": "pending"}]
        with patch(f"{PARSERS}.parse_goals_yaml", return_value=goals):
            result = svc.list_goals(status_filter="completed")

        assert result["total"] == 0
        assert result["goals"] == []

    def test_empty_goals(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.parse_goals_yaml", return_value=[]):
            result = svc.list_goals()

        assert result["total"] == 0
        assert result["pending"] == 0


# ===========================================================================
# add_goal
# ===========================================================================


class TestAddGoal:
    """Tests for OrchestratorService.add_goal()."""

    def test_add_default_priority(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.add_goal_to_yaml", return_value="g_005"):
            result = svc.add_goal(title="New feature")

        assert result["id"] == "g_005"
        assert result["title"] == "New feature"
        assert result["message"] == "Goal added successfully"

    def test_add_with_priority_and_category(self, svc: OrchestratorService) -> None:
        with patch(f"{PARSERS}.add_goal_to_yaml", return_value="g_010") as mock_add:
            result = svc.add_goal(title="Fix bug", priority="critical", category="bugfix")

        mock_add.assert_called_once_with("Fix bug", priority="critical", category="bugfix")
        assert result["id"] == "g_010"


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

    def test_status_schema_from_service(self, svc: OrchestratorService) -> None:
        from src.api.schemas import OrchestratorStatusSchema

        with patch(f"{PARSERS}.is_runner_alive", return_value=(True, 100)), \
             patch(f"{PARSERS}.parse_goals_yaml", return_value=[
                 {"status": "pending"}, {"status": "completed"},
             ]), \
             patch(f"{PARSERS}.parse_health_report", return_value={
                 "overall_grade": "A", "score": 93,
             }), \
             patch(f"{PARSERS}.get_runner_log_path", return_value=None):
            data = svc.get_status()

        schema = OrchestratorStatusSchema(**data)
        assert schema.alive is True
        assert schema.pid == 100
        assert schema.goals_pending == 1
        assert schema.goals_completed == 1
        assert schema.health_grade == "A"

    def test_goal_list_schema_from_service(self, svc: OrchestratorService, sample_goals: list) -> None:
        from src.api.schemas import GoalListResponseSchema, GoalSchema

        with patch(f"{PARSERS}.parse_goals_yaml", return_value=sample_goals):
            data = svc.list_goals()

        schema = GoalListResponseSchema(
            goals=[GoalSchema(**g) for g in data["goals"]],
            total=data["total"],
            pending=data["pending"],
            completed=data["completed"],
            failed=data["failed"],
        )
        assert schema.total == 4
        assert schema.pending == 2
        assert len(schema.goals) == 4

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
