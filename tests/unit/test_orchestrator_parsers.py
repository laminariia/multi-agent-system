"""Unit tests for the shared orchestrator parsers module.

Tests ``src.orchestrator.parsers`` — YAML/Markdown parsers, runner state checks,
file utilities, and goal management. All tests use tmp_path — no real filesystem.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.orchestrator.parsers import (
    add_goal_to_yaml,
    get_runner_log_path,
    is_runner_alive,
    parse_goals_yaml,
    parse_health_report,
    parse_vision_md,
    tail_file,
)

# ---------------------------------------------------------------------------
# Sample data (same structure as test_bot_orchestrator.py)
# ---------------------------------------------------------------------------

SAMPLE_GOALS_YAML = textwrap.dedent("""\
    goals:
      - id: g_001
        title: "First goal"
        priority: high
        category: testing
        status: completed
        completed_at: "2026-02-09T03:53"
        result: "All tests green"

      - id: g_002
        title: "Second goal"
        priority: medium
        category: feature
        status: pending

      - id: g_003
        title: "Third goal"
        priority: critical
        category: bugfix
        status: failed

      - id: g_004
        title: "Fourth goal"
        priority: low
        category: docs
        status: pending
""")

SAMPLE_HEALTH_YAML = textwrap.dedent("""\
    # Health Report
    overall_grade: A+
    score: 99

    dimensions:
      code_completeness:
        grade: A+
        notes: "All agents implemented"
      test_coverage:
        grade: A+
        notes: "1199 tests pass"
      infrastructure:
        grade: B+
        notes: "Docker Compose ready"
      ci_cd:
        grade: A-
        notes: "GitHub Actions CI"
      documentation:
        grade: A
        notes: "26+ docs"
      api:
        grade: A+
        notes: "All routes tested"
      security:
        grade: A
        notes: "JWT + bcrypt"
      code_quality:
        grade: A+
        notes: "Zero ruff errors"

    problems_detected:
      - severity: info
        description: "7 pytest warnings from external packages"
      - severity: low
        description: "golden_set/ directory is empty"

    problems_fixed_this_session:
      - "Fixed TypedDict bug"
    improvement_areas:
      - "Phase 3 milestones"
""")

SAMPLE_VISION_MD = textwrap.dedent("""\
    # Strategic Vision

    ## Current Phase: Phase 1 — Scout + Bid Reliability

    ### Milestones

    - [x] **M1.1** Project skeleton
    - [x] **M1.2** Base agent class
    - [ ] **M1.3** Scout agent

    ---

    ## Phase 2 — Dev + Content + Critic

    - [x] Dev Agent in Docker sandbox
    - [x] Content Agent writes proposals
    - [ ] Critic Agent reviews

    ---

    ## Phase 3 — Geo Scout + Outreach (FUTURE)

    - [ ] Pipeline B operational
    - [ ] Email warm-up complete
""")


# ---------------------------------------------------------------------------
# TestParseGoalsYaml
# ---------------------------------------------------------------------------


class TestParseGoalsYaml:
    def test_parse_valid_goals(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text(SAMPLE_GOALS_YAML, encoding="utf-8")
        goals = parse_goals_yaml(f)
        assert len(goals) == 4
        assert goals[0]["id"] == "g_001"
        assert goals[1]["id"] == "g_002"
        assert goals[2]["id"] == "g_003"
        assert goals[3]["id"] == "g_004"

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text("", encoding="utf-8")
        result = parse_goals_yaml(f)
        assert result == []

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        result = parse_goals_yaml(tmp_path / "nonexistent.yaml")
        assert result == []

    def test_parse_goals_status_fields(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text(SAMPLE_GOALS_YAML, encoding="utf-8")
        goals = parse_goals_yaml(f)
        first = goals[0]
        assert first["status"] == "completed"
        assert first["completed_at"] == "2026-02-09T03:53"
        assert first["result"] == "All tests green"

    def test_parse_goals_partial_fields(self, tmp_path: Path) -> None:
        """Goals with missing optional fields (completed_at, result) still parse."""
        f = tmp_path / "goals.yaml"
        f.write_text(SAMPLE_GOALS_YAML, encoding="utf-8")
        goals = parse_goals_yaml(f)
        second = goals[1]  # g_002 has no completed_at / result
        assert second["id"] == "g_002"
        assert second["title"] == "Second goal"
        assert second["status"] == "pending"
        assert "completed_at" not in second
        assert "result" not in second


# ---------------------------------------------------------------------------
# TestParseHealthReport
# ---------------------------------------------------------------------------


class TestParseHealthReport:
    def test_parse_valid_report(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text(SAMPLE_HEALTH_YAML, encoding="utf-8")
        report = parse_health_report(f)
        assert report["overall_grade"] == "A+"
        assert report["score"] == 99
        assert "dimensions" in report
        assert "problems" in report

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text("", encoding="utf-8")
        result = parse_health_report(f)
        # Empty file still returns dict with empty dimensions/problems
        assert result["dimensions"] == {}
        assert result["problems"] == []

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        result = parse_health_report(tmp_path / "nope.yaml")
        assert result == {}

    def test_parse_dimensions(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text(SAMPLE_HEALTH_YAML, encoding="utf-8")
        report = parse_health_report(f)
        dims = report["dimensions"]
        assert len(dims) == 8
        assert dims["code_completeness"]["grade"] == "A+"
        assert dims["code_completeness"]["notes"] == "All agents implemented"
        assert dims["infrastructure"]["grade"] == "B+"
        assert dims["infrastructure"]["notes"] == "Docker Compose ready"
        assert dims["security"]["grade"] == "A"
        assert dims["code_quality"]["grade"] == "A+"

    def test_parse_problems(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text(SAMPLE_HEALTH_YAML, encoding="utf-8")
        report = parse_health_report(f)
        problems = report["problems"]
        assert len(problems) == 2
        assert problems[0]["severity"] == "info"
        assert problems[0]["description"] == "7 pytest warnings from external packages"
        assert problems[1]["severity"] == "low"
        assert problems[1]["description"] == "golden_set/ directory is empty"


# ---------------------------------------------------------------------------
# TestParseVisionMd
# ---------------------------------------------------------------------------


class TestParseVisionMd:
    def test_parse_phases(self, tmp_path: Path) -> None:
        f = tmp_path / "vision.md"
        f.write_text(SAMPLE_VISION_MD, encoding="utf-8")
        phases = parse_vision_md(f)
        assert len(phases) == 3
        assert phases[0]["number"] == 1
        assert phases[0]["title"] == "Scout + Bid Reliability"
        assert phases[0]["is_future"] is False
        assert phases[1]["number"] == 2
        assert phases[1]["is_future"] is False
        assert phases[2]["number"] == 3
        assert phases[2]["is_future"] is True

    def test_parse_milestones(self, tmp_path: Path) -> None:
        f = tmp_path / "vision.md"
        f.write_text(SAMPLE_VISION_MD, encoding="utf-8")
        phases = parse_vision_md(f)
        # Phase 1: 3 milestones (2 done, 1 not)
        p1 = phases[0]
        assert len(p1["milestones"]) == 3
        assert p1["milestones"][0]["done"] is True
        assert p1["milestones"][1]["done"] is True
        assert p1["milestones"][2]["done"] is False
        # Phase 2: 3 milestones (2 done, 1 not)
        p2 = phases[1]
        assert len(p2["milestones"]) == 3
        assert p2["milestones"][0]["done"] is True
        assert p2["milestones"][2]["done"] is False
        # Phase 3 (FUTURE): 2 milestones, all not done
        p3 = phases[2]
        assert len(p3["milestones"]) == 2
        assert all(not m["done"] for m in p3["milestones"])

    def test_parse_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "vision.md"
        f.write_text("", encoding="utf-8")
        result = parse_vision_md(f)
        assert result == []

    def test_parse_missing(self, tmp_path: Path) -> None:
        result = parse_vision_md(tmp_path / "nope.md")
        assert result == []


# ---------------------------------------------------------------------------
# TestIsRunnerAlive
# ---------------------------------------------------------------------------


class TestIsRunnerAlive:
    def test_no_pid_file(self, tmp_path: Path) -> None:
        with patch("src.orchestrator.parsers.PID_FILE", tmp_path / "runner.pid"):
            alive, pid = is_runner_alive()
            assert alive is False
            assert pid is None

    def test_invalid_pid_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "runner.pid"
        pid_file.write_text("not_a_number")
        with patch("src.orchestrator.parsers.PID_FILE", pid_file):
            alive, pid = is_runner_alive()
            assert alive is False
            assert pid is None

    def test_stale_pid(self, tmp_path: Path) -> None:
        """PID file exists with valid number but process is dead."""
        pid_file = tmp_path / "runner.pid"
        pid_file.write_text("99999")
        with patch("src.orchestrator.parsers.PID_FILE", pid_file):
            # Mock ctypes so OpenProcess returns 0 (process not found)
            mock_kernel32 = MagicMock()
            mock_kernel32.OpenProcess.return_value = 0
            with patch("ctypes.windll", create=True) as mock_windll:
                mock_windll.kernel32 = mock_kernel32
                alive, pid = is_runner_alive()
                assert alive is False
                assert pid == 99999


# ---------------------------------------------------------------------------
# TestTailFile
# ---------------------------------------------------------------------------


class TestTailFile:
    def test_tail_file_normal(self, tmp_path: Path) -> None:
        f = tmp_path / "test.log"
        f.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        result = tail_file(f, 3)
        assert result == ["line3", "line4", "line5"]

    def test_tail_file_missing(self, tmp_path: Path) -> None:
        result = tail_file(tmp_path / "nope.log")
        assert result == []


# ---------------------------------------------------------------------------
# TestAddGoalToYaml
# ---------------------------------------------------------------------------


class TestAddGoalToYaml:
    def test_add_goal_existing_file(self, tmp_path: Path) -> None:
        goals_file = tmp_path / "goals.yaml"
        goals_file.write_text(SAMPLE_GOALS_YAML, encoding="utf-8")
        with patch("src.orchestrator.parsers.GOALS_FILE", goals_file):
            new_id = add_goal_to_yaml("New test goal")
        assert new_id == "g_005"
        content = goals_file.read_text(encoding="utf-8")
        assert "g_005" in content
        assert "New test goal" in content

    def test_add_goal_creates_file(self, tmp_path: Path) -> None:
        goals_file = tmp_path / "sub" / "goals.yaml"
        with patch("src.orchestrator.parsers.GOALS_FILE", goals_file):
            new_id = add_goal_to_yaml("First goal ever")
        assert new_id == "g_001"
        assert goals_file.exists()
        content = goals_file.read_text(encoding="utf-8")
        assert "g_001" in content
        assert "First goal ever" in content

    def test_add_goal_custom_priority(self, tmp_path: Path) -> None:
        goals_file = tmp_path / "goals.yaml"
        goals_file.write_text(SAMPLE_GOALS_YAML, encoding="utf-8")
        with patch("src.orchestrator.parsers.GOALS_FILE", goals_file):
            new_id = add_goal_to_yaml("Critical bug", priority="critical", category="bugfix")
        assert new_id == "g_005"
        content = goals_file.read_text(encoding="utf-8")
        assert "priority: critical" in content
        assert "category: bugfix" in content


# ---------------------------------------------------------------------------
# TestGetRunnerLogPath
# ---------------------------------------------------------------------------


class TestGetRunnerLogPath:
    def test_get_latest_log(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "runner_2026-02-08.log").write_text("old log")
        (log_dir / "runner_2026-02-09.log").write_text("new log")
        with patch("src.orchestrator.parsers.LOG_DIR", log_dir):
            result = get_runner_log_path()
        assert result is not None
        assert result.name == "runner_2026-02-09.log"

    def test_get_log_by_date(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "runner_2026-02-08.log").write_text("target log")
        (log_dir / "runner_2026-02-09.log").write_text("other log")
        with patch("src.orchestrator.parsers.LOG_DIR", log_dir):
            result = get_runner_log_path(date="2026-02-08")
        assert result is not None
        assert result.name == "runner_2026-02-08.log"

    def test_no_logs(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        with patch("src.orchestrator.parsers.LOG_DIR", log_dir):
            result = get_runner_log_path()
        assert result is None
