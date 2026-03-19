"""Unit tests for L6: Telegram /logs Pagination.

Tests the enhanced /logs command with pagination (max 20 lines per page),
"Next" button for more, --agent filter, and --since filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log_lines(count: int, agent: str | None = None) -> list[str]:
    """Generate fake runner log lines."""
    lines: list[str] = []
    base_time = datetime(2026, 3, 19, 10, 0, 0, tzinfo=UTC)
    for i in range(count):
        ts = base_time + timedelta(minutes=i)
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        agent_name = agent or ("scout" if i % 3 == 0 else "bid" if i % 3 == 1 else "planner")
        level = "INFO" if i % 4 != 0 else "WARN"
        lines.append(f"[{ts_str}] [{level}] [{agent_name}] Message line {i}")
    return lines


# ---------------------------------------------------------------------------
# Tests for _build_logs_text_paginated
# ---------------------------------------------------------------------------


class TestBuildLogsPaginated:
    """Tests for the paginated log builder."""

    def test_default_page_size_max_20(self) -> None:
        """Default call returns at most 20 lines."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        lines = _make_log_lines(50)
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(page=0, page_size=20)

        assert has_more is True
        assert next_offset == 20
        # Text should contain "Runner Log" header
        assert "Runner Log" in text

    def test_no_log_file(self) -> None:
        """Returns appropriate message when log file doesn't exist."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        with patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path:
            mock_path.return_value = None
            text, has_more, next_offset = _build_logs_text_paginated()

        assert "не найден" in text
        assert has_more is False
        assert next_offset == 0

    def test_fewer_lines_than_page_size(self) -> None:
        """When fewer lines than page_size, has_more is False."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        lines = _make_log_lines(5)
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(page=0, page_size=20)

        assert has_more is False
        assert next_offset == 0

    def test_second_page(self) -> None:
        """Page 1 returns the next batch of lines."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        lines = _make_log_lines(50)
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(page=1, page_size=20)

        assert has_more is True
        assert next_offset == 40

    def test_last_page_no_more(self) -> None:
        """Last page returns has_more=False."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        lines = _make_log_lines(25)
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(page=1, page_size=20)

        assert has_more is False
        assert next_offset == 0

    def test_agent_filter(self) -> None:
        """--agent filter only returns lines for that agent."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        lines = _make_log_lines(30)
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(
                page=0,
                page_size=20,
                agent_filter="scout",
            )

        # Only scout lines should appear (no bid/planner)
        assert "scout" in text.lower()

    def test_agent_filter_no_match(self) -> None:
        """Agent filter with no matching lines returns empty-ish message."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        lines = _make_log_lines(10)
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(
                page=0,
                page_size=20,
                agent_filter="nonexistent_agent",
            )

        assert has_more is False

    def test_since_filter_1h(self) -> None:
        """--since 1h filters lines older than 1 hour."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        now = datetime.now(UTC)
        recent_ts = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        old_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"[{old_ts}] [INFO] [scout] Old message",
            f"[{recent_ts}] [INFO] [scout] Recent message",
        ]
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(
                page=0,
                page_size=20,
                since="1h",
            )

        assert "Recent message" in text
        assert "Old message" not in text

    def test_since_filter_30m(self) -> None:
        """--since 30m filters correctly."""
        from src.bot.orchestrator_commands import _build_logs_text_paginated

        now = datetime.now(UTC)
        recent_ts = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        old_ts = (now - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"[{old_ts}] [INFO] [bid] Older than 30m",
            f"[{recent_ts}] [INFO] [bid] Within 30m",
        ]
        with (
            patch("src.bot.orchestrator_commands._get_runner_log_path") as mock_path,
            patch("src.bot.orchestrator_commands._tail_file") as mock_tail,
        ):
            mock_p = MagicMock()
            mock_p.exists.return_value = True
            mock_path.return_value = mock_p
            mock_tail.return_value = lines
            text, has_more, next_offset = _build_logs_text_paginated(
                page=0,
                page_size=20,
                since="30m",
            )

        assert "Within 30m" in text
        assert "Older than 30m" not in text


class TestParseLogArgs:
    """Tests for parsing /logs command arguments."""

    def test_no_args(self) -> None:
        """No args returns defaults."""
        from src.bot.orchestrator_commands import _parse_log_args

        result = _parse_log_args([])
        assert result["page"] == 0
        assert result["agent_filter"] is None
        assert result["since"] is None

    def test_agent_flag(self) -> None:
        """--agent scout extracts the agent filter."""
        from src.bot.orchestrator_commands import _parse_log_args

        result = _parse_log_args(["--agent", "scout"])
        assert result["agent_filter"] == "scout"

    def test_since_flag(self) -> None:
        """--since 1h extracts the since filter."""
        from src.bot.orchestrator_commands import _parse_log_args

        result = _parse_log_args(["--since", "1h"])
        assert result["since"] == "1h"

    def test_both_flags(self) -> None:
        """Both --agent and --since can be combined."""
        from src.bot.orchestrator_commands import _parse_log_args

        result = _parse_log_args(["--agent", "bid", "--since", "2h"])
        assert result["agent_filter"] == "bid"
        assert result["since"] == "2h"

    def test_numeric_arg_ignored(self) -> None:
        """A bare numeric argument is treated as page (backward compat)."""
        from src.bot.orchestrator_commands import _parse_log_args

        result = _parse_log_args(["scout"])
        assert result["agent_filter"] == "scout"

    def test_single_word_as_agent(self) -> None:
        """A single non-flag word is treated as agent filter."""
        from src.bot.orchestrator_commands import _parse_log_args

        result = _parse_log_args(["planner"])
        assert result["agent_filter"] == "planner"


class TestParseSinceDuration:
    """Tests for _parse_since_duration."""

    def test_hours(self) -> None:
        from src.bot.orchestrator_commands import _parse_since_duration

        delta = _parse_since_duration("2h")
        assert delta == timedelta(hours=2)

    def test_minutes(self) -> None:
        from src.bot.orchestrator_commands import _parse_since_duration

        delta = _parse_since_duration("30m")
        assert delta == timedelta(minutes=30)

    def test_days(self) -> None:
        from src.bot.orchestrator_commands import _parse_since_duration

        delta = _parse_since_duration("1d")
        assert delta == timedelta(days=1)

    def test_invalid_returns_none(self) -> None:
        from src.bot.orchestrator_commands import _parse_since_duration

        assert _parse_since_duration("invalid") is None
        assert _parse_since_duration("") is None

    def test_no_unit_returns_none(self) -> None:
        from src.bot.orchestrator_commands import _parse_since_duration

        assert _parse_since_duration("30") is None
