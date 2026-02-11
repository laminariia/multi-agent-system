"""Unit tests for the orchestrator Telegram bot commands.

Tests the 8 new commands in ``src.bot.orchestrator_commands`` and
the notification formatter in ``src.bot.orchestrator_notify``.
All tests use mocks — no real filesystem, process, or Telegram API calls.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.orchestrator_commands import (
    _esc,
    _format_runner_log_line,
    _is_runner_alive,
    _parse_goals_yaml,
    _parse_health_report,
    _parse_vision_md,
    _priority_emoji,
    _tail_file,
    add_goal_command,
    goals_command,
    health_command,
    logs_command,
    milestones_command,
    orch_command,
    run_command,
    stop_command,
)
from src.bot.orchestrator_notify import (
    format_all_goals_done,
    format_critical_error,
    format_session_complete,
    notify,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_GOALS_YAML = textwrap.dedent("""\
    goals:
      - id: g_001
        title: "First goal"
        priority: high
        category: testing
        status: completed
        completed_at: "2026-02-09T03:53"

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

SAMPLE_RUNNER_LOG = textwrap.dedent("""\
    [2026-02-09 06:00:00] [INFO] === Orchestrator Runner started (Mode: self-direct) ===
    [2026-02-09 06:00:01] [INFO] --- Session 1 ---
    [2026-02-09 06:37:12] [WARN] Session timed out after 59min
    [2026-02-09 06:37:12] [WARN] Session 1 error (code: 1)
    [2026-02-09 06:37:12] [INFO] After session 1: 3 pending goals
    [2026-02-09 06:37:12] [INFO] Self-Directed complete (1 sessions, 1h)
    [2026-02-09 06:37:12] [INFO] Runner finished
""")


def _make_update() -> MagicMock:
    """Create a mocked Telegram Update."""
    update = MagicMock()
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat = MagicMock()
    update.effective_chat.id = 12345
    return update


def _make_context(args: list[str] | None = None) -> MagicMock:
    """Create a mocked Context."""
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class TestEsc:
    def test_escapes_ampersand(self) -> None:
        assert _esc("a & b") == "a &amp; b"

    def test_escapes_lt_gt(self) -> None:
        assert _esc("<tag>") == "&lt;tag&gt;"

    def test_no_change(self) -> None:
        assert _esc("hello world") == "hello world"


class TestPriorityEmoji:
    def test_critical(self) -> None:
        assert _priority_emoji("critical") == "\U0001f534"

    def test_high(self) -> None:
        assert _priority_emoji("high") == "\U0001f534"

    def test_medium(self) -> None:
        assert _priority_emoji("medium") == "\U0001f7e1"

    def test_low(self) -> None:
        assert _priority_emoji("low") == "\U0001f7e2"

    def test_unknown_defaults_green(self) -> None:
        assert _priority_emoji("whatever") == "\U0001f7e2"


class TestFormatRunnerLogLine:
    def test_standard_line(self) -> None:
        line = "[2026-02-09 06:37:12] [INFO] Session completed"
        assert _format_runner_log_line(line) == "06:37 [INFO] Session completed"

    def test_non_matching_passthrough(self) -> None:
        line = "some random line"
        assert _format_runner_log_line(line) == "some random line"


class TestTailFile:
    def test_tail_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.log"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = _tail_file(f, 3)
        assert result == ["line3", "line4", "line5"]

    def test_tail_file_fewer_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "test.log"
        f.write_text("line1\nline2\n")
        result = _tail_file(f, 10)
        assert result == ["line1", "line2"]

    def test_tail_file_missing(self, tmp_path: Path) -> None:
        result = _tail_file(tmp_path / "nope.log")
        assert result == []


# ---------------------------------------------------------------------------
# Test YAML/MD parsers
# ---------------------------------------------------------------------------


class TestParseGoalsYaml:
    def test_parse_goals(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text(SAMPLE_GOALS_YAML)
        goals = _parse_goals_yaml(f)
        assert len(goals) == 4

    def test_goal_fields(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text(SAMPLE_GOALS_YAML)
        goals = _parse_goals_yaml(f)
        first = goals[0]
        assert first["id"] == "g_001"
        assert first["title"] == "First goal"
        assert first["priority"] == "high"
        assert first["status"] == "completed"

    def test_pending_count(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text(SAMPLE_GOALS_YAML)
        goals = _parse_goals_yaml(f)
        pending = [g for g in goals if g.get("status") == "pending"]
        assert len(pending) == 2

    def test_missing_file(self, tmp_path: Path) -> None:
        result = _parse_goals_yaml(tmp_path / "nonexistent.yaml")
        assert result == []

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "goals.yaml"
        f.write_text("")
        result = _parse_goals_yaml(f)
        assert result == []


class TestParseHealthReport:
    def test_overall_grade(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text(SAMPLE_HEALTH_YAML)
        report = _parse_health_report(f)
        assert report["overall_grade"] == "A+"
        assert report["score"] == 99

    def test_dimensions(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text(SAMPLE_HEALTH_YAML)
        report = _parse_health_report(f)
        dims = report["dimensions"]
        assert len(dims) == 8
        assert dims["code_completeness"]["grade"] == "A+"
        assert dims["infrastructure"]["grade"] == "B+"

    def test_problems(self, tmp_path: Path) -> None:
        f = tmp_path / "health.yaml"
        f.write_text(SAMPLE_HEALTH_YAML)
        report = _parse_health_report(f)
        assert len(report["problems"]) == 2
        assert report["problems"][0]["severity"] == "info"

    def test_missing_file(self, tmp_path: Path) -> None:
        result = _parse_health_report(tmp_path / "nope.yaml")
        assert result == {}


class TestParseVisionMd:
    def test_phase_count(self, tmp_path: Path) -> None:
        f = tmp_path / "vision.md"
        f.write_text(SAMPLE_VISION_MD, encoding="utf-8")
        phases = _parse_vision_md(f)
        assert len(phases) == 3

    def test_phase_1_milestones(self, tmp_path: Path) -> None:
        f = tmp_path / "vision.md"
        f.write_text(SAMPLE_VISION_MD, encoding="utf-8")
        phases = _parse_vision_md(f)
        p1 = phases[0]
        assert p1["number"] == 1
        assert p1["title"] == "Scout + Bid Reliability"
        assert len(p1["milestones"]) == 3
        assert p1["milestones"][0]["done"] is True
        assert p1["milestones"][2]["done"] is False

    def test_future_phase(self, tmp_path: Path) -> None:
        f = tmp_path / "vision.md"
        f.write_text(SAMPLE_VISION_MD, encoding="utf-8")
        phases = _parse_vision_md(f)
        p3 = phases[2]
        assert p3["is_future"] is True
        assert p3["number"] == 3

    def test_missing_file(self, tmp_path: Path) -> None:
        result = _parse_vision_md(tmp_path / "nope.md")
        assert result == []


# ---------------------------------------------------------------------------
# Test runner alive check
# ---------------------------------------------------------------------------


class TestIsRunnerAlive:
    def test_no_pid_file(self, tmp_path: Path) -> None:
        with patch("src.orchestrator.parsers.PID_FILE", tmp_path / "runner.pid"):
            alive, pid = _is_runner_alive()
            assert alive is False
            assert pid is None

    def test_invalid_pid_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "runner.pid"
        pid_file.write_text("not_a_number")
        with patch("src.orchestrator.parsers.PID_FILE", pid_file):
            alive, pid = _is_runner_alive()
            assert alive is False
            assert pid is None


# ---------------------------------------------------------------------------
# Test commands (async)
# ---------------------------------------------------------------------------


class TestRunCommand:
    @pytest.mark.anyio()
    async def test_already_running(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(True, 1234)):
            await run_command(update, ctx)
        reply = update.effective_message.reply_text
        reply.assert_awaited_once()
        assert "уже запущен" in reply.call_args[0][0]

    @pytest.mark.anyio()
    async def test_missing_script(self, tmp_path: Path) -> None:
        update = _make_update()
        ctx = _make_context()
        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(False, None)),
            patch("src.bot.orchestrator_commands.RUNNER_SCRIPT", tmp_path / "nonexistent.ps1"),
        ):
            await run_command(update, ctx)
        reply = update.effective_message.reply_text
        reply.assert_awaited_once()
        assert "не найден" in reply.call_args[0][0]

    @pytest.mark.anyio()
    async def test_successful_start(self, tmp_path: Path) -> None:
        update = _make_update()
        ctx = _make_context(["4", "90"])

        script = tmp_path / "runner.ps1"
        script.write_text("# dummy")
        pid_file = tmp_path / "runner.pid"
        orch_dir = tmp_path

        mock_proc = MagicMock()
        mock_proc.pid = 9999

        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(False, None)),
            patch("src.bot.orchestrator_commands.RUNNER_SCRIPT", script),
            patch("src.bot.orchestrator_commands.PID_FILE", pid_file),
            patch("src.bot.orchestrator_commands.ORCH_DIR", orch_dir),
            patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[
                {"status": "pending"}, {"status": "completed"},
            ]),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            await run_command(update, ctx)

        reply = update.effective_message.reply_text
        reply.assert_awaited_once()
        text = reply.call_args[0][0]
        assert "запущен" in text
        assert "4ч" in text
        assert "90мин" in text
        assert "1 целей" in text

    @pytest.mark.anyio()
    async def test_invalid_args(self) -> None:
        update = _make_update()
        ctx = _make_context(["abc"])
        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(False, None)),
            patch("src.bot.orchestrator_commands.RUNNER_SCRIPT", MagicMock(exists=MagicMock(return_value=True))),
        ):
            await run_command(update, ctx)
        reply = update.effective_message.reply_text
        assert "Usage" in reply.call_args[0][0]


class TestStopCommand:
    @pytest.mark.anyio()
    async def test_not_running(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(False, None)),
            patch("src.bot.orchestrator_commands.PID_FILE", MagicMock(exists=MagicMock(return_value=False))),
        ):
            await stop_command(update, ctx)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "не запущен" in text

    @pytest.mark.anyio()
    async def test_successful_stop(self, tmp_path: Path) -> None:
        update = _make_update()
        ctx = _make_context()
        pid_file = tmp_path / "runner.pid"
        pid_file.write_text("1234")

        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(True, 1234)),
            patch("src.bot.orchestrator_commands.PID_FILE", pid_file),
            patch("subprocess.run"),
            patch("src.bot.orchestrator_commands._get_runner_log_path", return_value=None),
            patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[
                {"status": "pending"}, {"status": "pending"}, {"status": "completed"},
            ]),
        ):
            await stop_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "остановлен" in text.lower()
        assert "2 целей" in text


class TestOrchCommand:
    @pytest.mark.anyio()
    async def test_running_status(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(True, 5678)),
            patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[
                {"status": "pending"},
                {"status": "completed"},
                {"status": "completed"},
            ]),
            patch("src.bot.orchestrator_commands._parse_health_report", return_value={
                "overall_grade": "A", "score": 95,
            }),
            patch("src.bot.orchestrator_commands._get_runner_log_path", return_value=None),
        ):
            await orch_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "РАБОТАЕТ" in text
        assert "5678" in text

    @pytest.mark.anyio()
    async def test_stopped_status(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with (
            patch("src.bot.orchestrator_commands._is_runner_alive", return_value=(False, None)),
            patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[{"status": "pending"}]),
            patch("src.bot.orchestrator_commands._parse_health_report", return_value={
                "overall_grade": "B+", "score": 85,
            }),
            patch("src.bot.orchestrator_commands._get_runner_log_path", return_value=None),
        ):
            await orch_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "ОСТАНОВЛЕН" in text
        assert "1 целей" in text


class TestGoalsCommand:
    @pytest.mark.anyio()
    async def test_all_goals(self) -> None:
        update = _make_update()
        ctx = _make_context()
        done = {"id": "g_001", "title": "Done goal", "status": "completed",
                "priority": "high", "completed_at": "2026-02-09"}
        open_g = {"id": "g_002", "title": "Open goal", "status": "pending", "priority": "medium"}
        with patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[done, open_g]):
            await goals_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "1 pending" in text
        assert "Open goal" in text

    @pytest.mark.anyio()
    async def test_filter_pending(self) -> None:
        update = _make_update()
        ctx = _make_context(["pending"])
        with patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[
            {"id": "g_001", "title": "Done", "status": "completed", "priority": "high"},
            {"id": "g_002", "title": "Open", "status": "pending", "priority": "medium"},
        ]):
            await goals_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "PENDING" in text
        # Should NOT show completed section
        assert "COMPLETED" not in text

    @pytest.mark.anyio()
    async def test_filter_done(self) -> None:
        update = _make_update()
        ctx = _make_context(["done"])
        with patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[
            {"id": "g_001", "title": "Done", "status": "completed", "priority": "high", "completed_at": "2026-02-09"},
            {"id": "g_002", "title": "Open", "status": "pending", "priority": "medium"},
        ]):
            await goals_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        # Should show completed but not pending section
        assert "PENDING" not in text

    @pytest.mark.anyio()
    async def test_empty_goals(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[]):
            await goals_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "не найден" in text or "пуст" in text


class TestHealthCommand:
    @pytest.mark.anyio()
    async def test_shows_report(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._parse_health_report", return_value={
            "overall_grade": "A+",
            "score": 99,
            "dimensions": {
                "code_completeness": {"grade": "A+"},
                "test_coverage": {"grade": "A"},
            },
            "problems": [{"severity": "info", "description": "minor warning"}],
        }):
            await health_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "A+" in text
        assert "99" in text
        assert "Code" in text

    @pytest.mark.anyio()
    async def test_missing_report(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._parse_health_report", return_value={}):
            await health_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "не найден" in text


class TestMilestonesCommand:
    @pytest.mark.anyio()
    async def test_shows_milestones(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._parse_vision_md", return_value=[
            {
                "number": 1,
                "title": "Scout",
                "milestones": [
                    {"text": "M1.1 done", "done": True},
                    {"text": "M1.2 open", "done": False},
                ],
                "is_future": False,
            },
        ]):
            await milestones_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "Phase 1" in text
        assert "1/2" in text

    @pytest.mark.anyio()
    async def test_empty_vision(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._parse_vision_md", return_value=[]):
            await milestones_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "не найден" in text or "пуст" in text


class TestLogsCommand:
    @pytest.mark.anyio()
    async def test_shows_log_tail(self, tmp_path: Path) -> None:
        log = tmp_path / "runner_2026-02-09.log"
        log.write_text(SAMPLE_RUNNER_LOG)
        update = _make_update()
        ctx = _make_context(["5"])
        with patch("src.bot.orchestrator_commands._get_runner_log_path", return_value=log):
            await logs_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "Runner Log" in text
        assert "Runner finished" in text

    @pytest.mark.anyio()
    async def test_no_log_file(self) -> None:
        update = _make_update()
        ctx = _make_context()
        with patch("src.bot.orchestrator_commands._get_runner_log_path", return_value=None):
            await logs_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "не найден" in text


class TestAddGoalCommand:
    @pytest.mark.anyio()
    async def test_add_goal(self, tmp_path: Path) -> None:
        goals_file = tmp_path / "goals.yaml"
        goals_file.write_text(SAMPLE_GOALS_YAML)

        update = _make_update()
        ctx = _make_context(["Fix", "login", "page", "CSS", "bug"])

        with (
            patch("src.orchestrator.parsers.GOALS_FILE", goals_file),
            patch("src.bot.orchestrator_commands._parse_goals_yaml") as mock_parse,
        ):
            mock_parse.return_value = [
                {"id": "g_001"}, {"id": "g_002"}, {"id": "g_003"}, {"id": "g_004"},
            ]
            await add_goal_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "добавлена" in text
        assert "g_005" in text
        assert "Fix login page CSS bug" in text

        # Verify it was appended to file
        content = goals_file.read_text()
        assert "g_005" in content
        assert "Fix login page CSS bug" in content

    @pytest.mark.anyio()
    async def test_add_goal_no_args(self) -> None:
        update = _make_update()
        ctx = _make_context()
        await add_goal_command(update, ctx)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "Usage" in text

    @pytest.mark.anyio()
    async def test_add_goal_creates_file(self, tmp_path: Path) -> None:
        goals_file = tmp_path / "sub" / "goals.yaml"

        update = _make_update()
        ctx = _make_context(["New", "goal"])

        with (
            patch("src.orchestrator.parsers.GOALS_FILE", goals_file),
            patch("src.bot.orchestrator_commands._parse_goals_yaml", return_value=[]),
        ):
            await add_goal_command(update, ctx)

        assert goals_file.exists()
        text = update.effective_message.reply_text.call_args[0][0]
        assert "добавлена" in text
        assert "g_001" in text


# ---------------------------------------------------------------------------
# Test notification formatters
# ---------------------------------------------------------------------------


class TestNotificationFormatters:
    def test_session_complete(self) -> None:
        text = format_session_complete({
            "session": 3,
            "duration_min": 42,
            "goals_completed": 2,
            "health_before": "A-",
            "health_after": "A",
            "commits": 3,
        })
        assert "#3" in text
        assert "42мин" in text
        assert "+2" in text
        assert "A-" in text
        assert "3 commits" in text

    def test_session_complete_minimal(self) -> None:
        text = format_session_complete({"session": 1, "duration_min": 10})
        assert "#1" in text
        assert "10мин" in text

    def test_critical_error(self) -> None:
        text = format_critical_error({
            "consecutive": 3,
            "exit_code": 1,
            "last_error": "timeout",
        })
        assert "3 ошибки" in text
        assert "exit code 1" in text
        assert "timeout" in text

    def test_all_goals_done(self) -> None:
        text = format_all_goals_done({"phase": 2, "milestones": 5})
        assert "Phase 2" in text
        assert "5 milestones" in text
        assert "/run" in text


class TestNotifyFunction:
    @pytest.mark.anyio()
    async def test_missing_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch("src.bot.orchestrator_notify._get_env", return_value=""):
                result = await notify("session_complete", {"session": 1})
                assert result is False

    @pytest.mark.anyio()
    async def test_unknown_event(self) -> None:
        with (
            patch("src.bot.orchestrator_notify._get_env", side_effect=lambda k: "test_val"),
        ):
            result = await notify("unknown_event", {})
            assert result is False

    @pytest.mark.anyio()
    async def test_successful_notify(self) -> None:
        with (
            patch("src.bot.orchestrator_notify._get_env", side_effect=lambda k: "test_val"),
            patch("src.bot.orchestrator_notify.send_telegram_message", new_callable=AsyncMock, return_value=True),
        ):
            result = await notify("session_complete", {"session": 1, "duration_min": 30})
            assert result is True


# ---------------------------------------------------------------------------
# Test handler registration
# ---------------------------------------------------------------------------


class TestHandlerRegistration:
    def test_orchestrator_commands_imported_in_handler(self) -> None:
        """Verify that handler.py imports all 8 orchestrator commands."""
        from src.bot import handler as handler_module

        # Check that the module has the create function
        assert hasattr(handler_module, "create_bot_application")

        # Check imports exist
        assert hasattr(handler_module, "run_command")
        assert hasattr(handler_module, "stop_command")
        assert hasattr(handler_module, "orch_command")
        assert hasattr(handler_module, "goals_command")
        assert hasattr(handler_module, "health_command")
        assert hasattr(handler_module, "milestones_command")
        assert hasattr(handler_module, "logs_command")
        assert hasattr(handler_module, "add_goal_command")
