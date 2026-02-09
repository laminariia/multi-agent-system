"""Unit tests for Auth API routes.

Tests AuthController at /api/v1/auth:
- register (first user = owner, subsequent = pending)
- login (credential validation, status blocking)
- refresh (token validation, type check)
- logout (token blacklisting)
- me (current user retrieval)
- telegram_link (code validation, linking)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar import Response
from litestar.exceptions import ClientException, NotAuthorizedException
from litestar.security.jwt import Token

from src.api.routes.auth import AuthController
from src.api.schemas import (
    LoginRequestSchema,
    LoginResponseSchema,
    MessageSchema,
    RegisterPendingResponseSchema,
    RegisterRequestSchema,
    TelegramLinkSchema,
    TokenRefreshResponseSchema,
    TokenRefreshSchema,
    UserResponseSchema,
)

# =============================================================================
# TestRegister
# =============================================================================


class TestRegister:
    """Test user registration endpoint."""

    @pytest.mark.asyncio
    async def test_register_first_user_becomes_owner(self) -> None:
        """First user gets owner role, active status, and auto-login."""
        controller = AuthController(owner=None)
        data = RegisterRequestSchema(
            email="first@test.com",
            password="secret123",  # noqa: S106
            name="First User",
        )

        # Mock DB session
        mock_session = AsyncMock()

        # First execute: duplicate check (returns None)
        mock_duplicate_result = MagicMock()
        mock_duplicate_result.scalar_one_or_none.return_value = None

        # Second execute: user count (returns 0)
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 0

        # Track the user object when added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        async def populate_user():
            # Simulate DB populating id and created_at after flush
            if added_user:
                added_user.id = uuid.uuid4()
                added_user.created_at = datetime.now(UTC)

        mock_session.execute = AsyncMock(
            side_effect=[mock_duplicate_result, mock_count_result]
        )
        mock_session.add = MagicMock(side_effect=capture_add)
        mock_session.flush = AsyncMock(side_effect=populate_user)

        # Mock password hashing and token generation
        with (
            patch("src.api.routes.auth.hash_password") as mock_hash,
            patch("src.api.routes.auth.create_access_token") as mock_access,
            patch("src.api.routes.auth.create_refresh_token") as mock_refresh,
        ):
            mock_hash.return_value = "$2b$12$hashed"  # noqa: S105
            mock_access.return_value = "access_token_123"  # noqa: S105
            mock_refresh.return_value = "refresh_token_456"  # noqa: S105

            response = await controller.register.fn(
                controller, data=data, db_session=mock_session
            )

        # Assertions
        assert isinstance(response, Response)
        assert response.status_code == 200
        assert isinstance(response.content, LoginResponseSchema)
        assert response.content.access_token == "access_token_123"  # noqa: S105
        assert response.content.refresh_token == "refresh_token_456"  # noqa: S105
        assert response.content.user.email == "first@test.com"
        assert response.content.user.role == "owner"
        assert response.content.user.status == "active"

        # Verify user was added to session
        mock_session.add.assert_called_once()
        added_user = mock_session.add.call_args[0][0]
        assert added_user.email == "first@test.com"
        assert added_user.role == "owner"
        assert added_user.status == "active"
        assert added_user.password_hash == "$2b$12$hashed"  # noqa: S105

    @pytest.mark.asyncio
    async def test_register_subsequent_user_pending_approval(self) -> None:
        """Subsequent users get viewer role, pending_approval status, HTTP 202."""
        controller = AuthController(owner=None)
        data = RegisterRequestSchema(
            email="second@test.com",
            password="secret456",  # noqa: S106
            name="Second User",
        )

        # Mock DB session
        mock_session = AsyncMock()

        # First execute: duplicate check (returns None)
        mock_duplicate_result = MagicMock()
        mock_duplicate_result.scalar_one_or_none.return_value = None

        # Second execute: user count (returns 1 — first user exists)
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 1

        mock_session.execute = AsyncMock(
            side_effect=[mock_duplicate_result, mock_count_result]
        )
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        with patch("src.api.routes.auth.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashed"  # noqa: S105

            response = await controller.register.fn(
                controller, data=data, db_session=mock_session
            )

        # Assertions
        assert isinstance(response, Response)
        assert response.status_code == 202
        assert isinstance(response.content, RegisterPendingResponseSchema)
        assert response.content.status == "pending_approval"
        assert "administrator must approve" in response.content.message

        # Verify user was added with correct role and status
        mock_session.add.assert_called_once()
        added_user = mock_session.add.call_args[0][0]
        assert added_user.email == "second@test.com"
        assert added_user.role == "viewer"
        assert added_user.status == "pending_approval"

    @pytest.mark.asyncio
    async def test_register_duplicate_email_raises_409(self) -> None:
        """Registering an existing email raises ClientException with 409."""
        controller = AuthController(owner=None)
        data = RegisterRequestSchema(
            email="existing@test.com",
            password="secret789",  # noqa: S106
            name="Duplicate",
        )

        # Mock DB session
        mock_session = AsyncMock()

        # First execute: duplicate check returns existing user
        mock_existing_user = MagicMock()
        mock_existing_user.email = "existing@test.com"
        mock_duplicate_result = MagicMock()
        mock_duplicate_result.scalar_one_or_none.return_value = mock_existing_user

        mock_session.execute = AsyncMock(return_value=mock_duplicate_result)

        with pytest.raises(ClientException) as exc_info:
            await controller.register.fn(controller, data=data, db_session=mock_session)

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail


# =============================================================================
# TestLogin
# =============================================================================


class TestLogin:
    """Test user login endpoint."""

    @pytest.mark.asyncio
    async def test_login_success_returns_tokens(self) -> None:
        """Successful login returns tokens and updates last_login_at."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="active@test.com",
            password="correct_password",  # noqa: S106
        )

        # Mock user
        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "active@test.com"
        mock_user.name = "Active User"
        mock_user.role = "owner"
        mock_user.status = "active"
        mock_user.password_hash = "$2b$12$correct_hash"  # noqa: S105
        mock_user.telegram_chat_id = None
        mock_user.created_at = datetime.now(UTC)
        mock_user.last_login_at = None

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()

        with (
            patch("src.api.routes.auth.verify_password") as mock_verify,
            patch("src.api.routes.auth.create_access_token") as mock_access,
            patch("src.api.routes.auth.create_refresh_token") as mock_refresh,
        ):
            mock_verify.return_value = True
            mock_access.return_value = "access_abc"  # noqa: S105
            mock_refresh.return_value = "refresh_xyz"  # noqa: S105

            response = await controller.login.fn(
                controller, data=data, db_session=mock_session
            )

        # Assertions
        assert isinstance(response, LoginResponseSchema)
        assert response.access_token == "access_abc"  # noqa: S105
        assert response.refresh_token == "refresh_xyz"  # noqa: S105
        assert response.user.email == "active@test.com"
        assert response.user.role == "owner"

        # Verify last_login_at was updated
        assert mock_user.last_login_at is not None
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_login_user_not_found_raises_not_authorized(self) -> None:
        """Login with non-existent email raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="nonexistent@test.com",
            password="any_password",  # noqa: S106
        )

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(NotAuthorizedException) as exc_info:
            await controller.login.fn(controller, data=data, db_session=mock_session)

        assert "Invalid email or password" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_wrong_password_raises_not_authorized(self) -> None:
        """Login with wrong password raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="user@test.com",
            password="wrong_password",  # noqa: S106
        )

        # Mock user
        mock_user = MagicMock()
        mock_user.email = "user@test.com"
        mock_user.password_hash = "$2b$12$correct_hash"  # noqa: S105

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.api.routes.auth.verify_password") as mock_verify:
            mock_verify.return_value = False

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.login.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "Invalid email or password" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_user_with_no_password_hash_raises_not_authorized(self) -> None:
        """User with None password_hash raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="user@test.com",
            password="any_password",  # noqa: S106
        )

        # Mock user with no password_hash
        mock_user = MagicMock()
        mock_user.email = "user@test.com"
        mock_user.password_hash = None

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(NotAuthorizedException) as exc_info:
            await controller.login.fn(controller, data=data, db_session=mock_session)

        assert "Invalid email or password" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_pending_approval_user_blocked(self) -> None:
        """Login with pending_approval status raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="pending@test.com",
            password="correct_password",  # noqa: S106
        )

        # Mock user
        mock_user = MagicMock()
        mock_user.email = "pending@test.com"
        mock_user.status = "pending_approval"
        mock_user.password_hash = "$2b$12$correct_hash"  # noqa: S105

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.api.routes.auth.verify_password") as mock_verify:
            mock_verify.return_value = True

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.login.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "awaiting administrator approval" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_rejected_user_blocked(self) -> None:
        """Login with rejected status raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="rejected@test.com",
            password="correct_password",  # noqa: S106
        )

        # Mock user
        mock_user = MagicMock()
        mock_user.email = "rejected@test.com"
        mock_user.status = "rejected"
        mock_user.password_hash = "$2b$12$correct_hash"  # noqa: S105

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.api.routes.auth.verify_password") as mock_verify:
            mock_verify.return_value = True

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.login.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "not approved" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_suspended_user_blocked(self) -> None:
        """Login with suspended status raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = LoginRequestSchema(
            email="suspended@test.com",
            password="correct_password",  # noqa: S106
        )

        # Mock user
        mock_user = MagicMock()
        mock_user.email = "suspended@test.com"
        mock_user.status = "suspended"
        mock_user.password_hash = "$2b$12$correct_hash"  # noqa: S105

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.api.routes.auth.verify_password") as mock_verify:
            mock_verify.return_value = True

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.login.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "suspended" in exc_info.value.detail


# =============================================================================
# TestRefresh
# =============================================================================


class TestRefresh:
    """Test token refresh endpoint."""

    @pytest.mark.asyncio
    async def test_refresh_valid_token_returns_new_tokens(self) -> None:
        """Valid refresh token returns new access + refresh tokens."""
        controller = AuthController(owner=None)
        data = TokenRefreshSchema(refresh_token="valid_refresh_token")  # noqa: S106

        # Mock user
        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "user@test.com"

        # Mock DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Mock settings
        mock_settings = MagicMock()
        mock_settings.JWT_SECRET_KEY = "test_secret"  # noqa: S105

        # Mock token decode
        mock_token = MagicMock()
        mock_token.sub = str(user_id)
        mock_token.extras = {"type": "refresh"}

        with (
            patch("src.api.routes.auth.get_settings") as mock_get_settings,
            patch("src.api.routes.auth.Token.decode") as mock_decode,
            patch("src.api.routes.auth.create_access_token") as mock_access,
            patch("src.api.routes.auth.create_refresh_token") as mock_refresh,
        ):
            mock_get_settings.return_value = mock_settings
            mock_decode.return_value = mock_token
            mock_access.return_value = "new_access_token"  # noqa: S105
            mock_refresh.return_value = "new_refresh_token"  # noqa: S105

            response = await controller.refresh.fn(
                controller, data=data, db_session=mock_session
            )

        # Assertions
        assert isinstance(response, TokenRefreshResponseSchema)
        assert response.access_token == "new_access_token"  # noqa: S105
        assert response.refresh_token == "new_refresh_token"  # noqa: S105

    @pytest.mark.asyncio
    async def test_refresh_invalid_token_raises_not_authorized(self) -> None:
        """Invalid/expired token raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = TokenRefreshSchema(refresh_token="invalid_token")  # noqa: S106

        # Mock DB session
        mock_session = AsyncMock()

        # Mock settings
        mock_settings = MagicMock()
        mock_settings.JWT_SECRET_KEY = "test_secret"  # noqa: S105

        with (
            patch("src.api.routes.auth.get_settings") as mock_get_settings,
            patch("src.api.routes.auth.Token.decode") as mock_decode,
        ):
            mock_get_settings.return_value = mock_settings
            mock_decode.side_effect = Exception("Invalid token")

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.refresh.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "Invalid or expired" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_refresh_access_token_raises_not_authorized(self) -> None:
        """Token without type: refresh raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = TokenRefreshSchema(refresh_token="access_token_not_refresh")  # noqa: S106

        # Mock DB session
        mock_session = AsyncMock()

        # Mock settings
        mock_settings = MagicMock()
        mock_settings.JWT_SECRET_KEY = "test_secret"  # noqa: S105

        # Mock token decode (access token, not refresh)
        mock_token = MagicMock()
        mock_token.sub = str(uuid.uuid4())
        mock_token.extras = {"type": "access"}  # Wrong type!

        with (
            patch("src.api.routes.auth.get_settings") as mock_get_settings,
            patch("src.api.routes.auth.Token.decode") as mock_decode,
        ):
            mock_get_settings.return_value = mock_settings
            mock_decode.return_value = mock_token

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.refresh.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "not a refresh token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_refresh_user_not_found_raises_not_authorized(self) -> None:
        """Token for non-existent user raises NotAuthorizedException."""
        controller = AuthController(owner=None)
        data = TokenRefreshSchema(refresh_token="token_for_deleted_user")  # noqa: S106

        # Mock DB session (user not found)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Mock settings
        mock_settings = MagicMock()
        mock_settings.JWT_SECRET_KEY = "test_secret"  # noqa: S105

        # Mock token decode
        mock_token = MagicMock()
        mock_token.sub = str(uuid.uuid4())
        mock_token.extras = {"type": "refresh"}

        with (
            patch("src.api.routes.auth.get_settings") as mock_get_settings,
            patch("src.api.routes.auth.Token.decode") as mock_decode,
        ):
            mock_get_settings.return_value = mock_settings
            mock_decode.return_value = mock_token

            with pytest.raises(NotAuthorizedException) as exc_info:
                await controller.refresh.fn(
                    controller, data=data, db_session=mock_session
                )

        assert "User not found" in exc_info.value.detail


# =============================================================================
# TestLogout
# =============================================================================


class TestLogout:
    """Test logout endpoint."""

    @pytest.mark.asyncio
    async def test_logout_blacklists_token(self) -> None:
        """Logout blacklists token in Valkey with correct TTL."""
        controller = AuthController(owner=None)

        # Mock user
        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock token
        mock_token = MagicMock(spec=Token)
        mock_token.jti = "test-jti-123"
        exp_time = datetime.now(UTC) + timedelta(hours=1)
        mock_token.exp = exp_time

        # Mock request
        mock_request = MagicMock()
        mock_request.user = mock_user
        mock_request.auth = mock_token

        # Mock Valkey
        mock_valkey = AsyncMock()
        mock_valkey.setex = AsyncMock()

        # Mock settings
        mock_settings = MagicMock()

        response = await controller.logout.fn(
            controller,
            request=mock_request,
            valkey=mock_valkey,
            settings=mock_settings,
        )

        # Assertions
        assert isinstance(response, MessageSchema)
        assert "logged out" in response.message

        # Verify token was blacklisted
        mock_valkey.setex.assert_called_once()
        call_args = mock_valkey.setex.call_args
        assert call_args.kwargs["name"] == "token:blacklist:test-jti-123"
        assert call_args.kwargs["value"] == "1"
        # TTL should be around 3600 seconds (1 hour), allow some margin
        assert 3595 <= call_args.kwargs["time"] <= 3600

    @pytest.mark.asyncio
    async def test_logout_calculates_correct_ttl_for_soon_to_expire_token(self) -> None:
        """Logout calculates correct TTL even for tokens expiring soon."""
        controller = AuthController(owner=None)

        # Mock user
        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock token expiring in 10 seconds
        mock_token = MagicMock(spec=Token)
        mock_token.jti = "expiring-jti"
        exp_time = datetime.now(UTC) + timedelta(seconds=10)
        mock_token.exp = exp_time

        # Mock request
        mock_request = MagicMock()
        mock_request.user = mock_user
        mock_request.auth = mock_token

        # Mock Valkey
        mock_valkey = AsyncMock()
        mock_valkey.setex = AsyncMock()

        # Mock settings
        mock_settings = MagicMock()

        await controller.logout.fn(
            controller,
            request=mock_request,
            valkey=mock_valkey,
            settings=mock_settings,
        )

        # Verify TTL is small but at least 1
        call_args = mock_valkey.setex.call_args
        assert 1 <= call_args.kwargs["time"] <= 15


# =============================================================================
# TestMe
# =============================================================================


class TestMe:
    """Test current user endpoint."""

    @pytest.mark.asyncio
    async def test_me_returns_current_user(self) -> None:
        """GET /me returns UserResponseSchema from request.user."""
        controller = AuthController(owner=None)

        # Mock user
        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "current@test.com"
        mock_user.name = "Current User"
        mock_user.role = "moderator"
        mock_user.status = "active"
        mock_user.telegram_chat_id = 123456789
        mock_user.created_at = datetime.now(UTC)
        mock_user.last_login_at = datetime.now(UTC) - timedelta(hours=2)

        # Mock request
        mock_request = MagicMock()
        mock_request.user = mock_user

        response = await controller.me.fn(controller, request=mock_request)

        # Assertions
        assert isinstance(response, UserResponseSchema)
        assert response.email == "current@test.com"
        assert response.role == "moderator"
        assert response.status == "active"
        assert response.telegram_chat_id == 123456789


# =============================================================================
# TestTelegramLink
# =============================================================================


class TestTelegramLink:
    """Test Telegram account linking endpoint."""

    @pytest.mark.asyncio
    async def test_telegram_link_valid_code_links_account(self) -> None:
        """Valid code links telegram_chat_id and deletes Valkey key."""
        controller = AuthController(owner=None)
        data = TelegramLinkSchema(code="ABC123")

        # Mock user
        user_id = uuid.uuid4()
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "user@test.com"
        mock_user.telegram_chat_id = None

        # Mock request
        mock_request = MagicMock()
        mock_request.user = mock_user

        # Mock DB session
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        # Mock Valkey
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value="987654321")
        mock_valkey.delete = AsyncMock()

        response = await controller.telegram_link.fn(
            controller,
            data=data,
            request=mock_request,
            db_session=mock_session,
            valkey=mock_valkey,
        )

        # Assertions
        assert isinstance(response, MessageSchema)
        assert "linked successfully" in response.message

        # Verify telegram_chat_id was updated
        assert mock_user.telegram_chat_id == 987654321
        mock_session.add.assert_called_once_with(mock_user)
        mock_session.flush.assert_called_once()

        # Verify Valkey key was deleted
        mock_valkey.delete.assert_called_once_with("telegram_link:ABC123")

    @pytest.mark.asyncio
    async def test_telegram_link_invalid_code_raises_400(self) -> None:
        """Invalid/expired code (None from Valkey) raises ClientException 400."""
        controller = AuthController(owner=None)
        data = TelegramLinkSchema(code="INV123")

        # Mock user
        mock_user = MagicMock()
        mock_request = MagicMock()
        mock_request.user = mock_user

        # Mock DB session
        mock_session = AsyncMock()

        # Mock Valkey (code not found)
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=None)

        with pytest.raises(ClientException) as exc_info:
            await controller.telegram_link.fn(
                controller,
                data=data,
                request=mock_request,
                db_session=mock_session,
                valkey=mock_valkey,
            )

        assert exc_info.value.status_code == 400
        assert "Invalid or expired" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_telegram_link_corrupted_data_raises_400(self) -> None:
        """Corrupted code data (non-numeric) raises ClientException 400."""
        controller = AuthController(owner=None)
        data = TelegramLinkSchema(code="BAD123")

        # Mock user
        mock_user = MagicMock()
        mock_request = MagicMock()
        mock_request.user = mock_user

        # Mock DB session
        mock_session = AsyncMock()

        # Mock Valkey (returns non-numeric data)
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value="not_a_number")

        with pytest.raises(ClientException) as exc_info:
            await controller.telegram_link.fn(
                controller,
                data=data,
                request=mock_request,
                db_session=mock_session,
                valkey=mock_valkey,
            )

        assert exc_info.value.status_code == 400
        assert "Corrupted" in exc_info.value.detail
