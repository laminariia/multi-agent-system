"""Tests for user registration approval, login blocking, guards, and user management.

Covers:
- Registration: first user → owner/active, subsequent → viewer/pending_approval
- Login: blocks non-active users with status-specific messages
- Guards: retrieve_user_handler rejects non-active, require_role varargs
- UserController: list, approve, reject, role change, status change, delete
- Schema validation: regex patterns, defaults
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import (
    ClientException,
    NotAuthorizedException,
    NotFoundException,
    PermissionDeniedException,
)

from src.api.guards import require_role, retrieve_user_handler
from src.api.routes.auth import AuthController
from src.api.routes.users import UserController
from src.api.schemas import (
    LoginRequestSchema,
    RegisterRequestSchema,
    UserApproveRequestSchema,
    UserRoleUpdateSchema,
    UserStatusUpdateSchema,
)
from src.core.models import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    *,
    role: str = "owner",
    status: str = "active",
    email: str = "owner@test.com",
    name: str | None = "Test Owner",
    password_hash: str = "$2b$12$fakehashfakehashfakehashfakehashfakehashfakehashfake",
) -> User:
    """Create a User ORM instance for testing (no DB round-trip)."""
    return User(
        id=uuid.uuid4(),
        email=email,
        name=name,
        role=role,
        status=status,
        password_hash=password_hash,
        telegram_chat_id=None,
        settings={},
        created_at=datetime.now(UTC),
        last_login_at=None,
    )


def _get_handler_fn(handler):
    """Extract the raw async function from a Litestar HTTPRouteHandler.

    Litestar decorators (@post, @get, etc.) wrap methods in HTTPRouteHandler.
    The original function is stored in handler.fn (a Ref object) whose .value
    attribute is the actual callable.
    """
    if hasattr(handler, "fn"):
        ref = handler.fn
        return ref.value if hasattr(ref, "value") else ref
    return handler


def _mock_request(user: User) -> MagicMock:
    """Build a mock Litestar Request with .user set."""
    request = MagicMock()
    request.user = user
    return request


# Unwrap controller methods once at module level for reuse in all tests
_register_fn = _get_handler_fn(AuthController.register)
_login_fn = _get_handler_fn(AuthController.login)
_list_users_fn = _get_handler_fn(UserController.list_users)
_approve_user_fn = _get_handler_fn(UserController.approve_user)
_reject_user_fn = _get_handler_fn(UserController.reject_user)
_update_role_fn = _get_handler_fn(UserController.update_role)
_update_status_fn = _get_handler_fn(UserController.update_status)
_delete_user_fn = _get_handler_fn(UserController.delete_user)
_transfer_ownership_fn = _get_handler_fn(UserController.transfer_ownership)


# ===========================================================================
# Registration tests
# ===========================================================================


class TestRegistration:
    """Test AuthController.register() — first-user vs subsequent flow."""

    @pytest.mark.anyio
    async def test_first_user_becomes_owner(self):
        """First registered user gets role=owner, status=active, HTTP 200 with tokens."""
        session = AsyncMock()

        # 1st execute: check existing email → None
        r_existing = MagicMock()
        r_existing.scalar_one_or_none.return_value = None
        # 2nd execute: count users → 0
        r_count = MagicMock()
        r_count.scalar_one.return_value = 0

        session.execute = AsyncMock(side_effect=[r_existing, r_count])

        # flush() must simulate DB-generated fields (id, created_at)
        added_users: list[User] = []
        session.add = MagicMock(side_effect=lambda u: added_users.append(u))

        async def _simulate_flush():
            for u in added_users:
                if u.id is None:
                    u.id = uuid.uuid4()
                if u.created_at is None:
                    u.created_at = datetime.now(UTC)

        session.flush = AsyncMock(side_effect=_simulate_flush)

        data = RegisterRequestSchema(email="first@test.com", password="secret123", name="First")

        with patch("src.api.routes.auth.hash_password", return_value="$2b$12$hashed"), \
             patch("src.api.routes.auth.create_access_token", return_value="access-tok"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="refresh-tok"):
            self_obj = object.__new__(AuthController)
            response = await _register_fn(self_obj, data=data, db_session=session)

        assert response.status_code == 200
        content = response.content
        assert content.access_token == "access-tok"
        assert content.refresh_token == "refresh-tok"
        assert content.user.role == "owner"

        # Verify user was added to session
        session.add.assert_called_once()
        added_user = session.add.call_args[0][0]
        assert added_user.role == "owner"
        assert added_user.status == "active"

    @pytest.mark.anyio
    async def test_subsequent_user_pending_approval(self):
        """Non-first user gets role=viewer, status=pending_approval, HTTP 202."""
        session = AsyncMock()

        r_existing = MagicMock()
        r_existing.scalar_one_or_none.return_value = None
        r_count = MagicMock()
        r_count.scalar_one.return_value = 1  # already have 1 user

        session.execute = AsyncMock(side_effect=[r_existing, r_count])

        added_users: list[User] = []
        session.add = MagicMock(side_effect=lambda u: added_users.append(u))

        async def _simulate_flush():
            for u in added_users:
                if u.id is None:
                    u.id = uuid.uuid4()
                if u.created_at is None:
                    u.created_at = datetime.now(UTC)

        session.flush = AsyncMock(side_effect=_simulate_flush)

        data = RegisterRequestSchema(email="second@test.com", password="secret123")

        with patch("src.api.routes.auth.hash_password", return_value="$2b$12$hashed"):
            self_obj = object.__new__(AuthController)
            response = await _register_fn(self_obj, data=data, db_session=session)

        assert response.status_code == 202
        content = response.content
        assert content.status == "pending_approval"
        assert "approve" in content.message.lower()

        added_user = session.add.call_args[0][0]
        assert added_user.role == "viewer"
        assert added_user.status == "pending_approval"

    @pytest.mark.anyio
    async def test_duplicate_email_returns_409(self):
        """Registering with an existing email raises 409 Conflict."""
        existing_user = _make_user(email="dupe@test.com")
        session = AsyncMock()

        r_existing = MagicMock()
        r_existing.scalar_one_or_none.return_value = existing_user
        session.execute = AsyncMock(return_value=r_existing)

        data = RegisterRequestSchema(email="dupe@test.com", password="secret123")

        self_obj = object.__new__(AuthController)
        with pytest.raises(ClientException) as exc_info:
            await _register_fn(self_obj, data=data, db_session=session)

        assert exc_info.value.status_code == 409
        assert "already exists" in str(exc_info.value.detail)


# ===========================================================================
# Login blocking tests
# ===========================================================================


class TestLoginBlocking:
    """Test AuthController.login() — blocks non-active users."""

    @pytest.mark.anyio
    async def test_active_user_can_login(self):
        """Active user with correct password gets tokens."""
        user = _make_user(status="active")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = LoginRequestSchema(email=user.email, password="secret123")

        with patch("src.api.routes.auth.verify_password", return_value=True), \
             patch("src.api.routes.auth.create_access_token", return_value="at"), \
             patch("src.api.routes.auth.create_refresh_token", return_value="rt"):
            self_obj = object.__new__(AuthController)
            result = await _login_fn(self_obj, data=data, db_session=session)

        assert result.access_token == "at"
        assert result.refresh_token == "rt"

    @pytest.mark.anyio
    async def test_pending_user_blocked(self):
        """Pending-approval user cannot login."""
        user = _make_user(status="pending_approval")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = LoginRequestSchema(email=user.email, password="secret123")

        with patch("src.api.routes.auth.verify_password", return_value=True):
            self_obj = object.__new__(AuthController)
            with pytest.raises(NotAuthorizedException) as exc_info:
                await _login_fn(self_obj, data=data, db_session=session)

        assert "awaiting" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_rejected_user_blocked(self):
        """Rejected user cannot login."""
        user = _make_user(status="rejected")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = LoginRequestSchema(email=user.email, password="secret123")

        with patch("src.api.routes.auth.verify_password", return_value=True):
            self_obj = object.__new__(AuthController)
            with pytest.raises(NotAuthorizedException) as exc_info:
                await _login_fn(self_obj, data=data, db_session=session)

        assert "not approved" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_suspended_user_blocked(self):
        """Suspended user cannot login."""
        user = _make_user(status="suspended")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = LoginRequestSchema(email=user.email, password="secret123")

        with patch("src.api.routes.auth.verify_password", return_value=True):
            self_obj = object.__new__(AuthController)
            with pytest.raises(NotAuthorizedException) as exc_info:
                await _login_fn(self_obj, data=data, db_session=session)

        assert "suspended" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_wrong_password_rejected(self):
        """Wrong password returns 401 regardless of status."""
        user = _make_user(status="active")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = LoginRequestSchema(email=user.email, password="wrongpass")

        with patch("src.api.routes.auth.verify_password", return_value=False):
            self_obj = object.__new__(AuthController)
            with pytest.raises(NotAuthorizedException) as exc_info:
                await _login_fn(self_obj, data=data, db_session=session)

        assert "invalid" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_nonexistent_user_rejected(self):
        """Login with unknown email returns 401."""
        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=r)

        data = LoginRequestSchema(email="nobody@test.com", password="secret123")

        self_obj = object.__new__(AuthController)
        with pytest.raises(NotAuthorizedException):
            await _login_fn(self_obj, data=data, db_session=session)


# ===========================================================================
# Guard tests
# ===========================================================================


class TestGuards:
    """Test retrieve_user_handler and require_role."""

    @pytest.mark.anyio
    async def test_retrieve_user_rejects_pending(self):
        """retrieve_user_handler returns None for pending_approval users."""
        user = _make_user(status="pending_approval")
        token = MagicMock()
        token.sub = str(user.id)
        token.jti = "jti-123"

        mock_session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        mock_session.execute = AsyncMock(return_value=r)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.api.guards.async_session_factory", return_value=mock_session), \
             patch("src.core.database.get_valkey") as mock_get_valkey:
            mock_valkey = AsyncMock()
            mock_valkey.get = AsyncMock(return_value=None)
            mock_get_valkey.return_value = mock_valkey

            result = await retrieve_user_handler(token, MagicMock())

        assert result is None

    @pytest.mark.anyio
    async def test_retrieve_user_rejects_suspended(self):
        """retrieve_user_handler returns None for suspended users."""
        user = _make_user(status="suspended")
        token = MagicMock()
        token.sub = str(user.id)
        token.jti = "jti-456"

        mock_session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        mock_session.execute = AsyncMock(return_value=r)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.api.guards.async_session_factory", return_value=mock_session), \
             patch("src.core.database.get_valkey") as mock_get_valkey:
            mock_valkey = AsyncMock()
            mock_valkey.get = AsyncMock(return_value=None)
            mock_get_valkey.return_value = mock_valkey

            result = await retrieve_user_handler(token, MagicMock())

        assert result is None

    @pytest.mark.anyio
    async def test_retrieve_user_accepts_active(self):
        """retrieve_user_handler returns User for active users."""
        user = _make_user(status="active")
        token = MagicMock()
        token.sub = str(user.id)
        token.jti = "jti-789"

        mock_session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        mock_session.execute = AsyncMock(return_value=r)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.api.guards.async_session_factory", return_value=mock_session), \
             patch("src.core.database.get_valkey") as mock_get_valkey:
            mock_valkey = AsyncMock()
            mock_valkey.get = AsyncMock(return_value=None)
            mock_get_valkey.return_value = mock_valkey

            result = await retrieve_user_handler(token, MagicMock())

        assert result is user

    @pytest.mark.anyio
    async def test_require_role_single(self):
        """require_role('owner') allows owner, blocks viewer."""
        guard = require_role("owner")

        # Owner passes
        conn_ok = MagicMock()
        conn_ok.user = _make_user(role="owner")
        await guard(conn_ok, MagicMock())  # should not raise

        # Viewer fails
        conn_bad = MagicMock()
        conn_bad.user = _make_user(role="viewer")
        with pytest.raises(PermissionDeniedException):
            await guard(conn_bad, MagicMock())

    @pytest.mark.anyio
    async def test_require_role_varargs(self):
        """require_role('owner', 'moderator') accepts both, blocks viewer."""
        guard = require_role("owner", "moderator")

        for role in ("owner", "moderator"):
            conn = MagicMock()
            conn.user = _make_user(role=role)
            await guard(conn, MagicMock())  # should not raise

        conn_bad = MagicMock()
        conn_bad.user = _make_user(role="viewer")
        with pytest.raises(PermissionDeniedException):
            await guard(conn_bad, MagicMock())

    @pytest.mark.anyio
    async def test_require_role_unauthenticated(self):
        """require_role raises NotAuthorizedException when user is None."""
        guard = require_role("owner")
        conn = MagicMock()
        conn.user = None

        with pytest.raises(NotAuthorizedException):
            await guard(conn, MagicMock())


# ===========================================================================
# UserController tests
# ===========================================================================


class TestUserController:
    """Test UserController — owner-only user management endpoints."""

    # -----------------------------------------------------------------------
    # List
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_list_users_no_filter(self):
        """GET /users returns all users with total count."""
        u1 = _make_user(email="a@test.com")
        u2 = _make_user(email="b@test.com", role="viewer")

        session = AsyncMock()
        r_count = MagicMock()
        r_count.scalar_one.return_value = 2
        r_users = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [u1, u2]
        r_users.scalars.return_value = scalars_mock

        session.execute = AsyncMock(side_effect=[r_count, r_users])

        self_obj = object.__new__(UserController)
        result = await _list_users_fn(self_obj, db_session=session)

        assert result.total == 2
        assert len(result.users) == 2

    @pytest.mark.anyio
    async def test_list_users_with_status_filter(self):
        """GET /users?status=pending_approval filters correctly."""
        u1 = _make_user(email="p@test.com", status="pending_approval", role="viewer")

        session = AsyncMock()
        r_count = MagicMock()
        r_count.scalar_one.return_value = 1
        r_users = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [u1]
        r_users.scalars.return_value = scalars_mock

        session.execute = AsyncMock(side_effect=[r_count, r_users])

        self_obj = object.__new__(UserController)
        result = await _list_users_fn(self_obj, db_session=session, status="pending_approval")

        assert result.total == 1
        assert result.users[0].status == "pending_approval"

    # -----------------------------------------------------------------------
    # Approve
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_approve_pending_user(self):
        """POST /users/{id}/approve sets status=active and assigns role."""
        user = _make_user(status="pending_approval", role="viewer", email="new@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserApproveRequestSchema(role="moderator")
        request = _mock_request(_make_user(role="owner"))

        self_obj = object.__new__(UserController)
        result = await _approve_user_fn(self_obj, user_id=user.id, data=data, request=request, db_session=session)

        assert result.status == "active"
        assert result.role == "moderator"
        assert user.status == "active"
        assert user.role == "moderator"

    @pytest.mark.anyio
    async def test_approve_non_pending_raises_409(self):
        """Cannot approve a user that is not pending_approval."""
        user = _make_user(status="active")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = UserApproveRequestSchema(role="viewer")
        request = _mock_request(_make_user(role="owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _approve_user_fn(self_obj, user_id=user.id, data=data, request=request, db_session=session)

        assert exc_info.value.status_code == 409

    # -----------------------------------------------------------------------
    # Reject
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_reject_pending_user(self):
        """POST /users/{id}/reject sets status=rejected."""
        user = _make_user(status="pending_approval", role="viewer", email="rej@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        self_obj = object.__new__(UserController)
        result = await _reject_user_fn(self_obj, user_id=user.id, db_session=session)

        assert result.status == "rejected"
        assert user.status == "rejected"

    @pytest.mark.anyio
    async def test_reject_non_pending_raises_409(self):
        """Cannot reject a user that is not pending_approval."""
        user = _make_user(status="active")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _reject_user_fn(self_obj, user_id=user.id, db_session=session)

        assert exc_info.value.status_code == 409

    # -----------------------------------------------------------------------
    # Role change
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_update_role(self):
        """PATCH /users/{id}/role changes role for non-owner users."""
        user = _make_user(role="viewer", email="v@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserRoleUpdateSchema(role="moderator")
        request = _mock_request(_make_user(role="owner"))

        self_obj = object.__new__(UserController)
        result = await _update_role_fn(
            self_obj, user_id=user.id, data=data, request=request, db_session=session,
        )

        assert result.role == "moderator"
        assert user.role == "moderator"

    @pytest.mark.anyio
    async def test_cannot_change_owner_role(self):
        """Cannot change the role of an owner user (403)."""
        owner = _make_user(role="owner", email="owner2@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = owner
        session.execute = AsyncMock(return_value=r)

        data = UserRoleUpdateSchema(role="viewer")
        request = _mock_request(_make_user(role="owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_role_fn(
                self_obj, user_id=owner.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    # -----------------------------------------------------------------------
    # Status change (suspend / reactivate)
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_suspend_user(self):
        """PATCH /users/{id}/status with status=suspended suspends the user."""
        user = _make_user(role="viewer", status="active", email="sus@test.com")
        requester = _make_user(role="owner")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserStatusUpdateSchema(status="suspended")
        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        result = await _update_status_fn(
            self_obj, user_id=user.id, data=data, request=request, db_session=session,
        )

        assert result.status == "suspended"

    @pytest.mark.anyio
    async def test_reactivate_user(self):
        """PATCH /users/{id}/status with status=active reactivates the user."""
        user = _make_user(role="viewer", status="suspended", email="react@test.com")
        requester = _make_user(role="owner")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserStatusUpdateSchema(status="active")
        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        result = await _update_status_fn(
            self_obj, user_id=user.id, data=data, request=request, db_session=session,
        )

        assert result.status == "active"

    @pytest.mark.anyio
    async def test_cannot_change_own_status(self):
        """Owner cannot suspend themselves (403)."""
        requester = _make_user(role="owner")

        session = AsyncMock()
        data = UserStatusUpdateSchema(status="suspended")
        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_status_fn(
                self_obj, user_id=requester.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403
        assert "own status" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_cannot_suspend_owner(self):
        """Cannot suspend another owner account (403)."""
        target_owner = _make_user(role="owner", email="other_owner@test.com")
        requester = _make_user(role="owner")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = target_owner
        session.execute = AsyncMock(return_value=r)

        data = UserStatusUpdateSchema(status="suspended")
        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_status_fn(
                self_obj, user_id=target_owner.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    # -----------------------------------------------------------------------
    # Delete
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_delete_user(self):
        """DELETE /users/{id} permanently removes a non-owner user."""
        user = _make_user(role="viewer", email="del@test.com")
        requester = _make_user(role="owner")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        result = await _delete_user_fn(
            self_obj, user_id=user.id, request=request, db_session=session,
        )

        assert "deleted" in result.message.lower() or user.email in result.message
        session.delete.assert_called_once_with(user)

    @pytest.mark.anyio
    async def test_cannot_delete_self(self):
        """Owner cannot delete themselves (403)."""
        requester = _make_user(role="owner")

        session = AsyncMock()
        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _delete_user_fn(
                self_obj, user_id=requester.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403
        assert "own account" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_cannot_delete_owner(self):
        """Cannot delete an owner account (403)."""
        target_owner = _make_user(role="owner", email="keep@test.com")
        requester = _make_user(role="owner")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = target_owner
        session.execute = AsyncMock(return_value=r)

        request = _mock_request(requester)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _delete_user_fn(
                self_obj, user_id=target_owner.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    # -----------------------------------------------------------------------
    # 404
    # -----------------------------------------------------------------------

    @pytest.mark.anyio
    async def test_get_user_not_found(self):
        """_get_user raises NotFoundException for unknown UUID."""
        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=r)

        with pytest.raises(NotFoundException):
            await UserController._get_user(session, uuid.uuid4())


# ===========================================================================
# Schema validation tests
# ===========================================================================


class TestSchemaValidation:
    """Test Pydantic schema constraints."""

    def test_approve_schema_default_viewer(self):
        """UserApproveRequestSchema defaults to viewer."""
        schema = UserApproveRequestSchema()
        assert schema.role == "viewer"

    def test_approve_schema_valid_roles(self):
        """UserApproveRequestSchema accepts viewer and moderator."""
        for role in ("viewer", "moderator"):
            schema = UserApproveRequestSchema(role=role)
            assert schema.role == role

    def test_approve_schema_invalid_role(self):
        """UserApproveRequestSchema rejects invalid role."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UserApproveRequestSchema(role="owner")

    def test_role_update_schema_valid(self):
        """UserRoleUpdateSchema accepts viewer and moderator."""
        for role in ("viewer", "moderator"):
            schema = UserRoleUpdateSchema(role=role)
            assert schema.role == role

    def test_role_update_schema_rejects_owner(self):
        """UserRoleUpdateSchema rejects owner role."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UserRoleUpdateSchema(role="owner")

    def test_status_update_schema_valid(self):
        """UserStatusUpdateSchema accepts active and suspended."""
        for status in ("active", "suspended"):
            schema = UserStatusUpdateSchema(status=status)
            assert schema.status == status

    def test_status_update_schema_rejects_pending(self):
        """UserStatusUpdateSchema rejects pending_approval."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UserStatusUpdateSchema(status="pending_approval")

    def test_register_pending_response(self):
        """RegisterPendingResponseSchema serializes correctly."""
        from src.api.schemas import RegisterPendingResponseSchema

        schema = RegisterPendingResponseSchema(
            message="Awaiting approval",
            status="pending_approval",
        )
        assert schema.message == "Awaiting approval"
        assert schema.status == "pending_approval"

    def test_approve_schema_accepts_co_owner(self):
        """UserApproveRequestSchema accepts co_owner role."""
        schema = UserApproveRequestSchema(role="co_owner")
        assert schema.role == "co_owner"

    def test_role_update_schema_accepts_co_owner(self):
        """UserRoleUpdateSchema accepts co_owner role."""
        schema = UserRoleUpdateSchema(role="co_owner")
        assert schema.role == "co_owner"

    def test_approve_schema_still_rejects_owner(self):
        """UserApproveRequestSchema still rejects owner role."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UserApproveRequestSchema(role="owner")

    def test_role_update_schema_still_rejects_owner(self):
        """UserRoleUpdateSchema still rejects owner role."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UserRoleUpdateSchema(role="owner")


# ===========================================================================
# Co-owner access and restriction tests
# ===========================================================================


class TestCoOwnerAccess:
    """Test co_owner permissions — can manage viewers/moderators but not owner/co_owners."""

    @pytest.mark.anyio
    async def test_co_owner_can_list_users(self):
        """Co-owner can call list_users (guard allows co_owner)."""
        u1 = _make_user(email="a@test.com")

        session = AsyncMock()
        r_count = MagicMock()
        r_count.scalar_one.return_value = 1
        r_users = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [u1]
        r_users.scalars.return_value = scalars_mock

        session.execute = AsyncMock(side_effect=[r_count, r_users])

        self_obj = object.__new__(UserController)
        result = await _list_users_fn(self_obj, db_session=session)

        assert result.total == 1

    @pytest.mark.anyio
    async def test_co_owner_can_approve_pending(self):
        """Co-owner can approve a pending user as viewer/moderator."""
        user = _make_user(status="pending_approval", role="viewer", email="new@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserApproveRequestSchema(role="moderator")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        result = await _approve_user_fn(self_obj, user_id=user.id, data=data, request=request, db_session=session)

        assert result.status == "active"
        assert result.role == "moderator"

    @pytest.mark.anyio
    async def test_co_owner_can_change_viewer_role(self):
        """Co-owner can change a viewer's role to moderator."""
        user = _make_user(role="viewer", email="v@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserRoleUpdateSchema(role="moderator")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        result = await _update_role_fn(
            self_obj, user_id=user.id, data=data, request=request, db_session=session,
        )

        assert result.role == "moderator"

    @pytest.mark.anyio
    async def test_co_owner_cannot_assign_co_owner_role(self):
        """Co-owner cannot promote someone to co_owner (403)."""
        user = _make_user(role="viewer", email="v@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = UserRoleUpdateSchema(role="co_owner")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_role_fn(
                self_obj, user_id=user.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403
        assert "owner" in str(exc_info.value.detail).lower()

    @pytest.mark.anyio
    async def test_co_owner_cannot_change_owner_role(self):
        """Co-owner cannot change the owner's role (403)."""
        owner = _make_user(role="owner", email="owner@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = owner
        session.execute = AsyncMock(return_value=r)

        data = UserRoleUpdateSchema(role="viewer")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_role_fn(
                self_obj, user_id=owner.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_co_owner_cannot_change_co_owner_role(self):
        """Co-owner cannot change another co_owner's role (403)."""
        other = _make_user(role="co_owner", email="coo@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = other
        session.execute = AsyncMock(return_value=r)

        data = UserRoleUpdateSchema(role="viewer")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_role_fn(
                self_obj, user_id=other.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_co_owner_cannot_suspend_owner(self):
        """Co-owner cannot suspend the owner (403)."""
        owner = _make_user(role="owner", email="owner@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = owner
        session.execute = AsyncMock(return_value=r)

        data = UserStatusUpdateSchema(status="suspended")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_status_fn(
                self_obj, user_id=owner.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_co_owner_cannot_suspend_co_owner(self):
        """Co-owner cannot suspend another co_owner (403)."""
        other = _make_user(role="co_owner", email="coo@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = other
        session.execute = AsyncMock(return_value=r)

        data = UserStatusUpdateSchema(status="suspended")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _update_status_fn(
                self_obj, user_id=other.id, data=data, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_co_owner_cannot_delete_owner(self):
        """Co-owner cannot delete the owner (403)."""
        owner = _make_user(role="owner", email="owner@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = owner
        session.execute = AsyncMock(return_value=r)

        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _delete_user_fn(
                self_obj, user_id=owner.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_co_owner_cannot_delete_co_owner(self):
        """Co-owner cannot delete another co_owner (403)."""
        other = _make_user(role="co_owner", email="coo@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = other
        session.execute = AsyncMock(return_value=r)

        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _delete_user_fn(
                self_obj, user_id=other.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_co_owner_can_suspend_viewer(self):
        """Co-owner can suspend a viewer."""
        user = _make_user(role="viewer", status="active", email="v@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        data = UserStatusUpdateSchema(status="suspended")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        result = await _update_status_fn(
            self_obj, user_id=user.id, data=data, request=request, db_session=session,
        )

        assert result.status == "suspended"

    @pytest.mark.anyio
    async def test_co_owner_can_delete_viewer(self):
        """Co-owner can delete a viewer."""
        user = _make_user(role="viewer", email="del@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        result = await _delete_user_fn(
            self_obj, user_id=user.id, request=request, db_session=session,
        )

        assert user.email in result.message
        session.delete.assert_called_once_with(user)

    @pytest.mark.anyio
    async def test_co_owner_cannot_approve_as_co_owner(self):
        """Co-owner cannot approve a pending user as co_owner role (403)."""
        user = _make_user(status="pending_approval", role="viewer", email="new@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=r)

        data = UserApproveRequestSchema(role="co_owner")
        request = _mock_request(_make_user(role="co_owner"))

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _approve_user_fn(self_obj, user_id=user.id, data=data, request=request, db_session=session)

        assert exc_info.value.status_code == 403


# ===========================================================================
# Ownership transfer tests
# ===========================================================================


class TestOwnershipTransfer:
    """Test UserController.transfer_ownership()."""

    @pytest.mark.anyio
    async def test_owner_can_transfer(self):
        """Owner transfers to active user — both roles update."""
        owner = _make_user(role="owner", email="owner@test.com")
        target = _make_user(role="viewer", status="active", email="target@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = target
        session.execute = AsyncMock(return_value=r)
        session.flush = AsyncMock()

        request = _mock_request(owner)

        self_obj = object.__new__(UserController)
        result = await _transfer_ownership_fn(
            self_obj, user_id=target.id, request=request, db_session=session,
        )

        assert "transferred" in result.message.lower()
        assert target.role == "owner"
        assert owner.role == "co_owner"

    @pytest.mark.anyio
    async def test_co_owner_cannot_transfer(self):
        """Co-owner cannot transfer ownership (403)."""
        co_owner = _make_user(role="co_owner", email="co@test.com")
        target = _make_user(role="viewer", status="active", email="target@test.com")

        session = AsyncMock()
        request = _mock_request(co_owner)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _transfer_ownership_fn(
                self_obj, user_id=target.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_viewer_cannot_transfer(self):
        """Viewer cannot transfer ownership (403)."""
        viewer = _make_user(role="viewer", email="v@test.com")
        target = _make_user(role="moderator", status="active", email="target@test.com")

        session = AsyncMock()
        request = _mock_request(viewer)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _transfer_ownership_fn(
                self_obj, user_id=target.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_cannot_transfer_to_self(self):
        """Owner cannot transfer ownership to themselves (400)."""
        owner = _make_user(role="owner", email="owner@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = owner
        session.execute = AsyncMock(return_value=r)

        request = _mock_request(owner)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _transfer_ownership_fn(
                self_obj, user_id=owner.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.anyio
    async def test_cannot_transfer_to_inactive_user(self):
        """Owner cannot transfer ownership to a suspended user (400)."""
        owner = _make_user(role="owner", email="owner@test.com")
        target = _make_user(role="viewer", status="suspended", email="sus@test.com")

        session = AsyncMock()
        r = MagicMock()
        r.scalar_one_or_none.return_value = target
        session.execute = AsyncMock(return_value=r)

        request = _mock_request(owner)

        self_obj = object.__new__(UserController)
        with pytest.raises(ClientException) as exc_info:
            await _transfer_ownership_fn(
                self_obj, user_id=target.id, request=request, db_session=session,
            )

        assert exc_info.value.status_code == 400
