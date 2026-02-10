"""Integration tests for API endpoints.

These tests construct a lightweight Litestar app with the same route handlers
as the production app, but with mocked database sessions and Valkey client.
This avoids requiring live PostgreSQL / Valkey infrastructure.

Tests cover:
- GET /health (unauthenticated)
- POST /api/v1/auth/register (first user = owner)
- POST /api/v1/auth/login (valid credentials)
- GET /api/v1/auth/me (authenticated)
- GET protected route without JWT -> 401
- GET /api/v1/hitl (authenticated, mocked DB)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import redis.asyncio as aioredis
from litestar import Litestar
from litestar.di import Provide
from litestar.security.jwt import JWTAuth, Token
from litestar.testing import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.auth import AuthController
from src.api.routes.health import health_check
from src.api.routes.hitl import HITLController
from src.core.config import Settings, get_settings
from src.core.models import User

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_test_user(
    *,
    user_id: uuid.UUID | None = None,
    email: str = "test@example.com",
    name: str = "Test User",
    role: str = "owner",
    status: str = "active",
    password_hash: str | None = None,
) -> User:
    """Build a User ORM instance for testing (no DB required).

    Uses the normal SQLAlchemy constructor so _sa_instance_state is initialized.
    """
    user = User(
        email=email,
        name=name,
        role=role,
        status=status,
        password_hash=password_hash,
    )
    user.id = user_id or uuid.uuid4()
    user.telegram_chat_id = None
    user.settings = {}
    user.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    user.last_login_at = None
    return user


def _make_mock_db_session() -> AsyncMock:
    """Build a mock AsyncSession with the right spec for Litestar type validation."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    return session


def _make_mock_valkey() -> AsyncMock:
    """Build a mock Valkey (redis.asyncio.Redis) client with the right spec."""
    valkey = AsyncMock(spec=aioredis.Redis)
    valkey.ping = AsyncMock(return_value=True)
    valkey.get = AsyncMock(return_value=None)
    valkey.set = AsyncMock(return_value=True)
    valkey.setex = AsyncMock(return_value=True)
    valkey.delete = AsyncMock(return_value=1)
    return valkey


# ---------------------------------------------------------------------------
# Test app factory
# ---------------------------------------------------------------------------


def _build_test_app(
    *,
    db_session: AsyncMock | None = None,
    valkey: AsyncMock | None = None,
    settings: Settings | None = None,
    retrieve_user: Any | None = None,
) -> Litestar:
    """Build a lightweight Litestar test app with mocked dependencies.

    This avoids the production app's ChannelsPlugin, rate limiting, and
    lifespan hooks that require real infrastructure.
    """
    _db = db_session or _make_mock_db_session()
    _valkey = valkey or _make_mock_valkey()
    _settings = settings or get_settings()

    # If no custom user retriever, build one that returns None (401)
    if retrieve_user is None:
        async def retrieve_user(token: Token, connection: Any) -> User | None:
            return None

    jwt = JWTAuth[User](
        retrieve_user_handler=retrieve_user,
        token_secret=_settings.JWT_SECRET_KEY,
        algorithm="HS256",
        exclude=["/health", "/schema"],
    )

    async def provide_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield _db  # type: ignore[misc]

    async def provide_valkey() -> aioredis.Redis:
        return _valkey  # type: ignore[return-value]

    def provide_settings() -> Settings:
        return _settings

    return Litestar(
        route_handlers=[
            health_check,
            AuthController,
            HITLController,
        ],
        dependencies={
            "db_session": Provide(provide_db_session),
            "valkey": Provide(provide_valkey),
            "settings": Provide(provide_settings, sync_to_thread=False),
        },
        on_app_init=[jwt.on_app_init],
        debug=True,
    )


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


def test_health_returns_200_healthy():
    """GET /health returns 200 with healthy status when DB and Valkey are reachable."""
    mock_db = _make_mock_db_session()
    mock_db.execute = AsyncMock(return_value=MagicMock())

    mock_valkey = _make_mock_valkey()
    mock_valkey.ping = AsyncMock(return_value=True)

    app = _build_test_app(db_session=mock_db, valkey=mock_valkey)
    with TestClient(app=app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["db_connected"] is True
    assert data["valkey_connected"] is True
    assert "version" in data
    assert "timestamp" in data


def test_health_degraded_when_valkey_down():
    """GET /health returns degraded when Valkey is unreachable."""
    mock_db = _make_mock_db_session()
    mock_db.execute = AsyncMock(return_value=MagicMock())

    mock_valkey = _make_mock_valkey()
    mock_valkey.ping = AsyncMock(side_effect=ConnectionError("Valkey unreachable"))

    app = _build_test_app(db_session=mock_db, valkey=mock_valkey)
    with TestClient(app=app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["db_connected"] is True
    assert data["valkey_connected"] is False


def test_health_unhealthy_when_both_down():
    """GET /health returns unhealthy when both DB and Valkey fail."""
    mock_db = _make_mock_db_session()
    mock_db.execute = AsyncMock(side_effect=ConnectionError("DB unreachable"))

    mock_valkey = _make_mock_valkey()
    mock_valkey.ping = AsyncMock(side_effect=ConnectionError("Valkey unreachable"))

    app = _build_test_app(db_session=mock_db, valkey=mock_valkey)
    with TestClient(app=app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["db_connected"] is False
    assert data["valkey_connected"] is False


# ---------------------------------------------------------------------------
# Auth endpoint tests
# ---------------------------------------------------------------------------


def test_register_first_user_becomes_owner():
    """POST /api/v1/auth/register creates the first user as owner with tokens."""
    mock_db = _make_mock_db_session()

    # First query: check for existing user -> not found
    mock_result_existing = MagicMock()
    mock_result_existing.scalar_one_or_none = MagicMock(return_value=None)

    # Second query: count users -> 0 (first user)
    mock_result_count = MagicMock()
    mock_result_count.scalar_one = MagicMock(return_value=0)

    call_count = 0

    async def side_effect_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_result_existing
        return mock_result_count

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)

    # Capture the user object added to session to set its id
    def capture_add(obj):
        if isinstance(obj, User):
            obj.id = uuid.uuid4()
            obj.created_at = datetime(2026, 1, 1, tzinfo=UTC)
            obj.last_login_at = None

    mock_db.add = MagicMock(side_effect=capture_add)

    app = _build_test_app(db_session=mock_db)
    with TestClient(app=app) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "securepass123",
                "name": "First Owner",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "owner@example.com"
    assert data["user"]["role"] == "owner"


def test_login_with_valid_credentials():
    """POST /api/v1/auth/login returns JWT tokens for valid credentials."""
    from src.api.guards import hash_password

    password = "mypassword123"  # noqa: S105
    hashed = hash_password(password)

    test_user = _make_test_user(
        email="login@example.com",
        password_hash=hashed,
        role="owner",
        status="active",
    )

    mock_db = _make_mock_db_session()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=test_user)
    mock_db.execute = AsyncMock(return_value=mock_result)

    app = _build_test_app(db_session=mock_db)
    with TestClient(app=app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": password},
        )

    assert response.status_code in (200, 201), f"Expected 200/201, got {response.status_code}"
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "login@example.com"
    assert data["user"]["role"] == "owner"


def test_login_with_wrong_password():
    """POST /api/v1/auth/login returns 401 for wrong password."""
    from src.api.guards import hash_password

    test_user = _make_test_user(
        email="wrong@example.com",
        password_hash=hash_password("correctpassword"),
    )

    mock_db = _make_mock_db_session()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=test_user)
    mock_db.execute = AsyncMock(return_value=mock_result)

    app = _build_test_app(db_session=mock_db)
    with TestClient(app=app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "wrong@example.com", "password": "wrongpassword"},
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Protected route tests
# ---------------------------------------------------------------------------


def test_protected_route_without_jwt_returns_401():
    """GET /api/v1/auth/me without JWT returns 401."""
    app = _build_test_app()
    with TestClient(app=app) as client:
        response = client.get("/api/v1/auth/me")

    assert response.status_code == 401


def test_protected_route_with_valid_jwt():
    """GET /api/v1/auth/me with valid JWT returns the user profile."""
    test_user = _make_test_user(email="authed@example.com", role="owner")
    settings = get_settings()

    # Create a retrieve_user handler that returns our test user
    async def retrieve_user(token: Token, connection: Any) -> User | None:
        return test_user

    jwt = JWTAuth[User](
        retrieve_user_handler=retrieve_user,
        token_secret=settings.JWT_SECRET_KEY,
        algorithm="HS256",
        exclude=["/health", "/schema"],
    )

    # Generate a valid token
    token = jwt.create_token(identifier=str(test_user.id))

    app = _build_test_app(retrieve_user=retrieve_user)
    with TestClient(app=app) as client:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "authed@example.com"
    assert data["role"] == "owner"


# ---------------------------------------------------------------------------
# HITL endpoint tests
# ---------------------------------------------------------------------------


def test_hitl_pending_returns_items():
    """GET /api/v1/hitl/pending returns HITL pending items (authenticated)."""
    test_user = _make_test_user(email="hitl@example.com", role="owner")
    settings = get_settings()

    async def retrieve_user(token: Token, connection: Any) -> User | None:
        return test_user

    jwt = JWTAuth[User](
        retrieve_user_handler=retrieve_user,
        token_secret=settings.JWT_SECRET_KEY,
        algorithm="HS256",
        exclude=["/health", "/schema"],
    )
    token = jwt.create_token(identifier=str(test_user.id))

    # Mock DB: the HITL controller runs 3 queries:
    # 1) count total, 2) count urgent, 3) fetch items
    mock_db = _make_mock_db_session()

    mock_result_count = MagicMock()
    mock_result_count.scalar_one = MagicMock(return_value=0)

    mock_result_urgent = MagicMock()
    mock_result_urgent.scalar_one = MagicMock(return_value=0)

    mock_result_items = MagicMock()
    mock_result_items.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    call_idx = 0

    async def db_execute(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return mock_result_count
        if call_idx == 2:
            return mock_result_urgent
        return mock_result_items

    mock_db.execute = AsyncMock(side_effect=db_execute)

    app = _build_test_app(db_session=mock_db, retrieve_user=retrieve_user)
    with TestClient(app=app) as client:
        response = client.get(
            "/api/v1/hitl/pending",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 0
    assert data["items"] == []
    assert data["pending_urgent"] == 0


def test_hitl_without_auth_returns_401():
    """GET /api/v1/hitl/pending without JWT returns 401."""
    app = _build_test_app()
    with TestClient(app=app) as client:
        response = client.get("/api/v1/hitl/pending")

    assert response.status_code == 401
