"""Unit tests for JWT authentication and role-based authorization guards.

Tests ``src.api.guards`` — password hashing, token creation, user retrieval,
and role guard enforcement.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.guards import (
    hash_password,
    require_role,
    verify_password,
)

# ---------------------------------------------------------------------------
# Password hashing tests
# ---------------------------------------------------------------------------


class TestHashPassword:
    """Tests for the bcrypt hash_password function."""

    def test_returns_string(self) -> None:
        result = hash_password("testpass123")  # noqa: S106
        assert isinstance(result, str)

    def test_hash_starts_with_bcrypt_prefix(self) -> None:
        result = hash_password("testpass123")  # noqa: S106
        assert result.startswith("$2b$")

    def test_different_calls_produce_different_hashes(self) -> None:
        h1 = hash_password("same_password")  # noqa: S106
        h2 = hash_password("same_password")  # noqa: S106
        assert h1 != h2  # Different salts

    def test_hash_is_valid_bcrypt_length(self) -> None:
        result = hash_password("testpass123")  # noqa: S106
        assert len(result) == 60  # Standard bcrypt length


class TestVerifyPassword:
    """Tests for the bcrypt verify_password function."""

    def test_correct_password_returns_true(self) -> None:
        hashed = hash_password("mypassword")  # noqa: S106
        assert verify_password("mypassword", hashed) is True  # noqa: S106

    def test_wrong_password_returns_false(self) -> None:
        hashed = hash_password("mypassword")  # noqa: S106
        assert verify_password("wrongpassword", hashed) is False  # noqa: S106

    def test_empty_password_returns_false(self) -> None:
        hashed = hash_password("mypassword")  # noqa: S106
        assert verify_password("", hashed) is False


# ---------------------------------------------------------------------------
# Role guard tests
# ---------------------------------------------------------------------------


class TestRequireRole:
    """Tests for the require_role guard factory."""

    @pytest.mark.asyncio
    async def test_allows_matching_role(self) -> None:
        guard = require_role("owner")
        conn = MagicMock()
        conn.user = MagicMock()
        conn.user.role = "owner"
        handler = MagicMock()

        # Should not raise
        await guard(conn, handler)

    @pytest.mark.asyncio
    async def test_allows_any_of_multiple_roles(self) -> None:
        guard = require_role("owner", "moderator")
        conn = MagicMock()
        conn.user = MagicMock()
        conn.user.role = "moderator"
        handler = MagicMock()

        await guard(conn, handler)

    @pytest.mark.asyncio
    async def test_rejects_non_matching_role(self) -> None:
        from litestar.exceptions import PermissionDeniedException

        guard = require_role("owner")
        conn = MagicMock()
        conn.user = MagicMock()
        conn.user.role = "viewer"
        handler = MagicMock()

        with pytest.raises(PermissionDeniedException):
            await guard(conn, handler)

    @pytest.mark.asyncio
    async def test_rejects_none_user(self) -> None:
        from litestar.exceptions import NotAuthorizedException

        guard = require_role("owner")
        conn = MagicMock()
        conn.user = None
        handler = MagicMock()

        with pytest.raises(NotAuthorizedException):
            await guard(conn, handler)

    @pytest.mark.asyncio
    async def test_rejects_viewer_from_owner_only(self) -> None:
        from litestar.exceptions import PermissionDeniedException

        guard = require_role("owner")
        conn = MagicMock()
        conn.user = MagicMock()
        conn.user.role = "viewer"
        handler = MagicMock()

        with pytest.raises(PermissionDeniedException, match="owner"):
            await guard(conn, handler)

    @pytest.mark.asyncio
    async def test_co_owner_accepted_when_listed(self) -> None:
        guard = require_role("owner", "co_owner")
        conn = MagicMock()
        conn.user = MagicMock()
        conn.user.role = "co_owner"
        handler = MagicMock()

        await guard(conn, handler)

    @pytest.mark.asyncio
    async def test_co_owner_rejected_when_not_listed(self) -> None:
        from litestar.exceptions import PermissionDeniedException

        guard = require_role("owner")
        conn = MagicMock()
        conn.user = MagicMock()
        conn.user.role = "co_owner"
        handler = MagicMock()

        with pytest.raises(PermissionDeniedException):
            await guard(conn, handler)


# ---------------------------------------------------------------------------
# Token creation tests
# ---------------------------------------------------------------------------


class TestCreateAccessToken:
    """Tests for create_access_token."""

    def test_returns_string(self) -> None:
        from src.api.guards import create_access_token

        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()
        mock_user.email = "test@example.com"
        mock_user.role = "owner"

        token = create_access_token(mock_user)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_has_three_parts(self) -> None:
        """JWT tokens have header.payload.signature format."""
        from src.api.guards import create_access_token

        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()
        mock_user.email = "test@example.com"
        mock_user.role = "owner"

        token = create_access_token(mock_user)
        parts = token.split(".")
        assert len(parts) == 3


class TestCreateRefreshToken:
    """Tests for create_refresh_token."""

    def test_returns_string(self) -> None:
        from src.api.guards import create_refresh_token

        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()
        mock_user.email = "test@example.com"
        mock_user.role = "owner"

        token = create_refresh_token(mock_user)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_different_from_access_token(self) -> None:
        from src.api.guards import create_access_token, create_refresh_token

        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()
        mock_user.email = "test@example.com"
        mock_user.role = "owner"

        access = create_access_token(mock_user)
        refresh = create_refresh_token(mock_user)
        assert access != refresh


# ---------------------------------------------------------------------------
# retrieve_user_handler tests
# ---------------------------------------------------------------------------


class TestRetrieveUserHandler:
    """Tests for the JWT user retrieval callback."""

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_uuid(self) -> None:
        from src.api.guards import retrieve_user_handler

        token = MagicMock()
        token.sub = "not-a-uuid"
        conn = MagicMock()

        result = await retrieve_user_handler(token, conn)
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_for_none_sub(self) -> None:
        """uuid.UUID(None) raises TypeError which is not caught — expected behavior."""
        from src.api.guards import retrieve_user_handler

        token = MagicMock()
        token.sub = None
        conn = MagicMock()

        with pytest.raises(TypeError):
            await retrieve_user_handler(token, conn)

    @pytest.mark.asyncio
    async def test_returns_active_user(self) -> None:
        from src.api.guards import retrieve_user_handler

        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.status = "active"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=None)

        token = MagicMock()
        token.sub = str(user_id)
        token.jti = "test-jti"
        conn = MagicMock()

        with (
            patch("src.api.guards.async_session_factory") as mock_factory,
            patch("src.core.database.get_valkey", return_value=mock_valkey),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_ctx

            result = await retrieve_user_handler(token, conn)

        assert result is mock_user

    @pytest.mark.asyncio
    async def test_returns_none_for_inactive_user(self) -> None:
        from src.api.guards import retrieve_user_handler

        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.status = "pending_approval"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        token = MagicMock()
        token.sub = str(user_id)
        conn = MagicMock()

        with patch("src.api.guards.async_session_factory") as mock_factory:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_ctx

            result = await retrieve_user_handler(token, conn)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_user(self) -> None:
        from src.api.guards import retrieve_user_handler

        user_id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        token = MagicMock()
        token.sub = str(user_id)
        conn = MagicMock()

        with patch("src.api.guards.async_session_factory") as mock_factory:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_ctx

            result = await retrieve_user_handler(token, conn)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_blacklisted_token(self) -> None:
        from src.api.guards import retrieve_user_handler

        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.status = "active"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value="1")  # Token is blacklisted

        token = MagicMock()
        token.sub = str(user_id)
        token.jti = "blacklisted-jti"
        conn = MagicMock()

        with (
            patch("src.api.guards.async_session_factory") as mock_factory,
            patch("src.core.database.get_valkey", return_value=mock_valkey),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_ctx

            result = await retrieve_user_handler(token, conn)

        assert result is None

    @pytest.mark.asyncio
    async def test_allows_if_valkey_unreachable(self) -> None:
        """When Valkey is down, still allow the request through."""
        from src.api.guards import retrieve_user_handler

        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.status = "active"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(side_effect=ConnectionError("Valkey down"))

        token = MagicMock()
        token.sub = str(user_id)
        token.jti = "test-jti"
        conn = MagicMock()

        with (
            patch("src.api.guards.async_session_factory") as mock_factory,
            patch("src.core.database.get_valkey", return_value=mock_valkey),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_ctx

            result = await retrieve_user_handler(token, conn)

        # Should still return the user despite Valkey failure
        assert result is mock_user
