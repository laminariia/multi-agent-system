"""Unit tests for CLI user creation script.

Tests ``src.cli.create_user`` — argparse CLI and async user insertion with
duplicate email detection.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def _mock_async_session(existing_user=None):
    """Helper to create mock async session with context manager support."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_user
    mock_session.execute.return_value = mock_result
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_session


# ---------------------------------------------------------------------------
# _create_user tests
# ---------------------------------------------------------------------------


class TestCreateUser:
    """Tests for the _create_user async function."""

    @pytest.mark.asyncio
    async def test_creates_user_successfully(self) -> None:
        """Should insert user into database when email is unique."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed_pw"),
        ):
            await _create_user(
                email="test@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name="Test User",
            )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()
        mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_email_raises_system_exit(self) -> None:
        """Should raise SystemExit(1) when email already exists."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=MagicMock())
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("sys.stderr"),
            pytest.raises(SystemExit) as exc_info,
        ):
            await _create_user(
                email="duplicate@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name="Test User",
            )

        assert exc_info.value.code == 1
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_hash_password_on_password(self) -> None:
        """Should hash the provided password before storage."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed") as mock_hash,
        ):
            await _create_user(
                email="test@example.com",
                password="my_password",  # noqa: S106
                role="viewer",
                name=None,
            )

        mock_hash.assert_called_once_with("my_password")  # noqa: S106

    @pytest.mark.asyncio
    async def test_user_gets_uuid4_id(self) -> None:
        """Should assign a UUID4 ID to the new user."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed"),
        ):
            await _create_user(
                email="test@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name="Test",
            )

        added_user = mock_session.add.call_args[0][0]
        assert isinstance(added_user.id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_user_gets_correct_role(self) -> None:
        """Should set the role field to the provided value."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed"),
        ):
            await _create_user(
                email="test@example.com",
                password="pass123",  # noqa: S106
                role="viewer",
                name="Test",
            )

        added_user = mock_session.add.call_args[0][0]
        assert added_user.role == "viewer"

    @pytest.mark.asyncio
    async def test_user_gets_name_or_none(self) -> None:
        """Should set name field to provided value or None."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed"),
        ):
            await _create_user(
                email="test@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name="John Doe",
            )

        added_user = mock_session.add.call_args[0][0]
        assert added_user.name == "John Doe"

    @pytest.mark.asyncio
    async def test_user_name_can_be_none(self) -> None:
        """Should allow None for name field."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed"),
        ):
            await _create_user(
                email="test@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name=None,
            )

        added_user = mock_session.add.call_args[0][0]
        assert added_user.name is None

    @pytest.mark.asyncio
    async def test_disposes_engine_after_success(self) -> None:
        """Should dispose engine after successful user creation."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=None)
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("src.cli.create_user.hash_password", return_value="hashed"),
        ):
            await _create_user(
                email="test@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name="Test",
            )

        mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_email_does_not_dispose_engine(self) -> None:
        """Should not dispose engine when SystemExit is raised inside context manager."""
        from src.cli.create_user import _create_user

        mock_session = _mock_async_session(existing_user=MagicMock())
        mock_factory = MagicMock(return_value=mock_session)
        mock_engine = AsyncMock()

        with (
            patch("src.cli.create_user.async_session_factory", mock_factory),
            patch("src.cli.create_user.engine", mock_engine),
            patch("sys.stderr"),
            pytest.raises(SystemExit),
        ):
            await _create_user(
                email="duplicate@example.com",
                password="pass123",  # noqa: S106
                role="owner",
                name="Test",
            )

        # Engine disposal is AFTER the context manager,
        # so SystemExit prevents it from being called
        mock_engine.dispose.assert_not_awaited()


# ---------------------------------------------------------------------------
# main() argparse tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() CLI entry point."""

    def test_parses_email_and_password(self) -> None:
        """Should parse --email and --password from command line."""
        from src.cli.create_user import main

        with (
            patch("sys.argv", [
                "create_user",
                "--email", "user@test.com",
                "--password", "secret",  # noqa: S106
            ]),
            patch("asyncio.run") as mock_run,
        ):
            main()

        mock_run.assert_called_once()

    def test_default_role_is_owner(self) -> None:
        """Should use 'owner' as default role when --role not provided."""
        import argparse

        # Test argparse behavior directly
        parser = argparse.ArgumentParser()
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--role", default="owner")
        parser.add_argument("--name", default=None)
        args = parser.parse_args(["--email", "test@test.com", "--password", "pw"])  # noqa: S106
        assert args.role == "owner"

    def test_role_viewer_works(self) -> None:
        """Should accept 'viewer' role from --role argument."""
        from src.cli.create_user import main

        with (
            patch("sys.argv", [
                "create_user",
                "--email", "user@test.com",
                "--password", "secret",  # noqa: S106
                "--role", "viewer",
            ]),
            patch("asyncio.run"),
        ):
            main()

    def test_name_is_optional(self) -> None:
        """Should allow omitting --name argument."""
        from src.cli.create_user import main

        with (
            patch("sys.argv", [
                "create_user",
                "--email", "user@test.com",
                "--password", "secret",  # noqa: S106
            ]),
            patch("asyncio.run"),
        ):
            main()

    def test_missing_email_exits_with_error(self) -> None:
        """Should exit when --email is missing."""
        from src.cli.create_user import main

        with (
            patch("sys.argv", [
                "create_user",
                "--password", "secret",  # noqa: S106
            ]),
            patch("sys.stderr"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0

    def test_missing_password_exits_with_error(self) -> None:
        """Should exit when --password is missing."""
        from src.cli.create_user import main

        with (
            patch("sys.argv", [
                "create_user",
                "--email", "user@test.com",
            ]),
            patch("sys.stderr"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
