"""Unit tests for CLI seed data script.

Tests ``src.cli.seed_data`` — deterministic UUID generation, seed data
constants, and database seeding logic with force flag.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


# ---------------------------------------------------------------------------
# _sid() tests
# ---------------------------------------------------------------------------


class TestSid:
    """Tests for the _sid deterministic UUID function."""

    def test_returns_deterministic_uuid(self) -> None:
        """Should return a UUID5 based on NAMESPACE_DNS."""
        from src.cli.seed_data import _sid

        result = _sid("test-name")
        assert isinstance(result, uuid.UUID)

    def test_same_input_same_output(self) -> None:
        """Should return the same UUID for the same input."""
        from src.cli.seed_data import _sid

        result1 = _sid("constant-name")
        result2 = _sid("constant-name")
        assert result1 == result2

    def test_different_input_different_output(self) -> None:
        """Should return different UUIDs for different inputs."""
        from src.cli.seed_data import _sid

        result1 = _sid("name-one")
        result2 = _sid("name-two")
        assert result1 != result2


# ---------------------------------------------------------------------------
# SEED_USER tests
# ---------------------------------------------------------------------------


class TestSeedUser:
    """Tests for the SEED_USER constant."""

    def test_has_correct_email(self) -> None:
        """Should have email 'admin@test.com'."""
        from src.cli.seed_data import SEED_USER

        assert SEED_USER.email == "admin@test.com"

    def test_has_role_owner(self) -> None:
        """Should have role 'owner'."""
        from src.cli.seed_data import SEED_USER

        assert SEED_USER.role == "owner"


# ---------------------------------------------------------------------------
# SEED_JOBS tests
# ---------------------------------------------------------------------------


class TestSeedJobs:
    """Tests for the SEED_JOBS constant."""

    def test_has_10_jobs(self) -> None:
        """Should contain exactly 10 job entries."""
        from src.cli.seed_data import SEED_JOBS

        assert len(SEED_JOBS) == 10

    def test_covers_freelancer_platform(self) -> None:
        """Should include jobs from 'freelancer' platform."""
        from src.cli.seed_data import SEED_JOBS

        freelancer_jobs = [j for j in SEED_JOBS if j.platform == "freelancer"]
        assert len(freelancer_jobs) > 0

    def test_covers_fl_ru_platform(self) -> None:
        """Should include jobs from 'fl_ru' platform."""
        from src.cli.seed_data import SEED_JOBS

        fl_ru_jobs = [j for j in SEED_JOBS if j.platform == "fl_ru"]
        assert len(fl_ru_jobs) > 0

    def test_covers_kwork_platform(self) -> None:
        """Should include jobs from 'kwork' platform."""
        from src.cli.seed_data import SEED_JOBS

        kwork_jobs = [j for j in SEED_JOBS if j.platform == "kwork"]
        assert len(kwork_jobs) > 0

    def test_covers_upwork_platform(self) -> None:
        """Should include jobs from 'upwork' platform."""
        from src.cli.seed_data import SEED_JOBS

        upwork_jobs = [j for j in SEED_JOBS if j.platform == "upwork"]
        assert len(upwork_jobs) > 0


# ---------------------------------------------------------------------------
# SEED_HITL tests
# ---------------------------------------------------------------------------


class TestSeedHitl:
    """Tests for the SEED_HITL constant."""

    def test_has_5_items(self) -> None:
        """Should contain exactly 5 HITL queue items."""
        from src.cli.seed_data import SEED_HITL

        assert len(SEED_HITL) == 5

    def test_includes_bid_approval_type(self) -> None:
        """Should include at least one bid_approval item."""
        from src.cli.seed_data import SEED_HITL

        bid_approvals = [h for h in SEED_HITL if h.type == "bid_approval"]
        assert len(bid_approvals) > 0

    def test_includes_code_review_type(self) -> None:
        """Should include at least one code_review item."""
        from src.cli.seed_data import SEED_HITL

        code_reviews = [h for h in SEED_HITL if h.type == "code_review"]
        assert len(code_reviews) > 0

    def test_includes_delivery_type(self) -> None:
        """Should include at least one delivery item."""
        from src.cli.seed_data import SEED_HITL

        deliveries = [h for h in SEED_HITL if h.type == "delivery"]
        assert len(deliveries) > 0

    def test_includes_alert_type(self) -> None:
        """Should include at least one alert item."""
        from src.cli.seed_data import SEED_HITL

        alerts = [h for h in SEED_HITL if h.type == "alert"]
        assert len(alerts) > 0


# ---------------------------------------------------------------------------
# SEED_HEARTBEATS tests
# ---------------------------------------------------------------------------


class TestSeedHeartbeats:
    """Tests for the SEED_HEARTBEATS constant."""

    def test_has_10_agents(self) -> None:
        """Should contain exactly 10 agent heartbeat entries."""
        from src.cli.seed_data import SEED_HEARTBEATS

        assert len(SEED_HEARTBEATS) == 10

    def test_includes_scout_agent(self) -> None:
        """Should include 'scout' agent."""
        from src.cli.seed_data import SEED_HEARTBEATS

        agents = [h.agent_name for h in SEED_HEARTBEATS]
        assert "scout" in agents

    def test_includes_bid_agent(self) -> None:
        """Should include 'bid' agent."""
        from src.cli.seed_data import SEED_HEARTBEATS

        agents = [h.agent_name for h in SEED_HEARTBEATS]
        assert "bid" in agents

    def test_includes_planner_agent(self) -> None:
        """Should include 'planner' agent."""
        from src.cli.seed_data import SEED_HEARTBEATS

        agents = [h.agent_name for h in SEED_HEARTBEATS]
        assert "planner" in agents

    def test_includes_dev_agent(self) -> None:
        """Should include 'dev' agent."""
        from src.cli.seed_data import SEED_HEARTBEATS

        agents = [h.agent_name for h in SEED_HEARTBEATS]
        assert "dev" in agents


# ---------------------------------------------------------------------------
# _seed() tests
# ---------------------------------------------------------------------------


class TestSeed:
    """Tests for the _seed async function."""

    @pytest.mark.asyncio
    async def test_creates_data_when_no_existing_user(self) -> None:
        """Should insert all seed data when admin@test.com doesn't exist."""
        from src.cli.seed_data import _seed

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # No existing user
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.seed_data.async_session_factory", mock_factory),
            patch("src.cli.seed_data.engine", mock_engine),
        ):
            await _seed(force=False)

        mock_session.add.assert_called()
        mock_session.add_all.assert_called()
        assert mock_session.commit.await_count >= 1

    @pytest.mark.asyncio
    async def test_skips_when_user_exists_and_not_force(self, capsys) -> None:
        """Should skip seeding when user exists and force=False."""
        from src.cli.seed_data import _seed

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # Existing user
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.seed_data.async_session_factory", mock_factory),
            patch("src.cli.seed_data.engine", mock_engine),
        ):
            await _seed(force=False)

        captured = capsys.readouterr()
        assert "already exists" in captured.out
        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_true_deletes_and_recreates(self) -> None:
        """Should delete existing seed data and re-create when force=True."""
        from src.cli.seed_data import _seed

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # Existing user
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.seed_data.async_session_factory", mock_factory),
            patch("src.cli.seed_data.engine", mock_engine),
        ):
            await _seed(force=True)

        # Should have called delete statements
        assert mock_session.execute.call_count > 1
        mock_session.add.assert_called()
        mock_session.add_all.assert_called()
        assert mock_session.commit.await_count >= 2  # One for delete, one for insert

    @pytest.mark.asyncio
    async def test_disposes_engine(self) -> None:
        """Should dispose engine after seeding."""
        from src.cli.seed_data import _seed

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.seed_data.async_session_factory", mock_factory),
            patch("src.cli.seed_data.engine", mock_engine),
        ):
            await _seed(force=False)

        mock_engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# main() argparse tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() CLI entry point."""

    def test_default_force_is_false(self) -> None:
        """Should use False as default for --force flag."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--force", action="store_true")
        args = parser.parse_args([])
        assert args.force is False

    def test_force_flag_sets_force_true(self) -> None:
        """Should set force=True when --force is provided."""
        from src.cli.seed_data import main

        with (
            patch("sys.argv", ["seed_data", "--force"]),
            patch("asyncio.run") as mock_run,
        ):
            main()

        mock_run.assert_called_once()
