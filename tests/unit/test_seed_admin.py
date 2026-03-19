"""Unit tests for L7: Admin User Seeding CLI.

Tests src.cli.seed_admin: idempotent admin creation, env var defaults,
CLI argument handling, role enforcement.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_user(email: str = "admin@example.com", role: str = "owner") -> MagicMock:
    """Create a mock User object."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = email
    user.role = role
    user.name = "Admin"
    user.status = "active"
    return user


# ---------------------------------------------------------------------------
# Tests for seed_admin_user
# ---------------------------------------------------------------------------


class TestSeedAdminUser:
    """Tests for the async seed_admin_user function."""

    @pytest.mark.asyncio
    async def test_creates_admin_when_no_users(self) -> None:
        """Creates an owner user when no users exist."""
        from src.cli.seed_admin import seed_admin_user

        mock_session = AsyncMock()
        # scalar_one_or_none returns None (no existing user)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with (
            patch("src.cli.seed_admin.async_session_factory") as mock_factory,
            patch("src.cli.seed_admin.hash_password", return_value="$2b$hashed"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await seed_admin_user(
                email="admin@example.com",
                password="secret123",
            )

        assert result is True
        mock_session.add.assert_called_once()
        added_user = mock_session.add.call_args[0][0]
        assert added_user.email == "admin@example.com"
        assert added_user.role == "owner"
        assert added_user.password_hash == "$2b$hashed"

    @pytest.mark.asyncio
    async def test_skips_when_user_exists(self) -> None:
        """Skips creation when a user with that email already exists."""
        from src.cli.seed_admin import seed_admin_user

        mock_session = AsyncMock()
        existing_user = _mock_user()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result

        with patch("src.cli.seed_admin.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await seed_admin_user(
                email="admin@example.com",
                password="secret123",
            )

        assert result is False
        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_env_defaults(self) -> None:
        """Uses ADMIN_EMAIL and ADMIN_PASSWORD env vars as defaults."""
        from src.cli.seed_admin import seed_admin_user

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with (
            patch("src.cli.seed_admin.async_session_factory") as mock_factory,
            patch("src.cli.seed_admin.hash_password", return_value="$2b$hashed"),
            patch.dict("os.environ", {"ADMIN_EMAIL": "env@test.com", "ADMIN_PASSWORD": "envpass"}),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            # Call without explicit email/password to use env defaults
            result = await seed_admin_user()

        assert result is True
        added_user = mock_session.add.call_args[0][0]
        assert added_user.email == "env@test.com"

    @pytest.mark.asyncio
    async def test_explicit_args_override_env(self) -> None:
        """Explicit email/password override env vars."""
        from src.cli.seed_admin import seed_admin_user

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with (
            patch("src.cli.seed_admin.async_session_factory") as mock_factory,
            patch("src.cli.seed_admin.hash_password", return_value="$2b$hashed"),
            patch.dict("os.environ", {"ADMIN_EMAIL": "env@test.com", "ADMIN_PASSWORD": "envpass"}),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await seed_admin_user(
                email="explicit@test.com",
                password="explicit_pass",
            )

        assert result is True
        added_user = mock_session.add.call_args[0][0]
        assert added_user.email == "explicit@test.com"

    @pytest.mark.asyncio
    async def test_no_email_raises(self) -> None:
        """Raises ValueError when no email is provided and env is empty."""
        from src.cli.seed_admin import seed_admin_user

        with patch.dict("os.environ", {}, clear=False):
            # Ensure ADMIN_EMAIL is not in env
            import os

            os.environ.pop("ADMIN_EMAIL", None)
            os.environ.pop("ADMIN_PASSWORD", None)

            with pytest.raises(ValueError, match="email"):
                await seed_admin_user()

    @pytest.mark.asyncio
    async def test_no_password_raises(self) -> None:
        """Raises ValueError when no password is provided."""
        import os

        from src.cli.seed_admin import seed_admin_user

        os.environ.pop("ADMIN_PASSWORD", None)

        with pytest.raises(ValueError, match="password"):
            await seed_admin_user(email="test@test.com")

    @pytest.mark.asyncio
    async def test_role_is_always_owner(self) -> None:
        """The seeded user always has role='owner'."""
        from src.cli.seed_admin import seed_admin_user

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with (
            patch("src.cli.seed_admin.async_session_factory") as mock_factory,
            patch("src.cli.seed_admin.hash_password", return_value="$2b$hashed"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await seed_admin_user(email="admin@x.com", password="pass")

        added = mock_session.add.call_args[0][0]
        assert added.role == "owner"
        assert added.status == "active"


class TestSeedAdminCLI:
    """Tests for the CLI argument parsing."""

    def test_parse_args_email_password(self) -> None:
        """CLI parses --email and --password."""
        from src.cli.seed_admin import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--email", "a@b.com", "--password", "pass"])
        assert args.email == "a@b.com"
        assert args.password == "pass"

    def test_parse_args_defaults_none(self) -> None:
        """CLI defaults to None for email/password (env vars will be used)."""
        from src.cli.seed_admin import _build_parser

        parser = _build_parser()
        args = parser.parse_args([])
        assert args.email is None
        assert args.password is None
