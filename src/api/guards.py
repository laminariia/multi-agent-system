"""JWT authentication and role-based authorisation guards for Litestar.

Uses ``litestar.security.jwt.JWTAuth`` with bcrypt password hashing.
The ``retrieve_user_handler`` callback fetches the user from PostgreSQL
on every authenticated request using the JWT ``sub`` claim (user UUID).

Password utilities use ``bcrypt`` directly (via ``passlib`` context)
as specified in ``docs/auth_specification.md``.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import bcrypt
import structlog
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.security.jwt import JWTAuth, Token
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database import async_session_factory
from src.core.models import User

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Password hashing helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt.

    Returns:
        The bcrypt hash as a UTF-8 string suitable for DB storage.
    """
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify *plain_password* against a bcrypt *hashed_password*.

    Returns:
        ``True`` when the password matches, ``False`` otherwise.
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# JWT user retrieval
# ---------------------------------------------------------------------------


async def retrieve_user_handler(token: Token, connection: ASGIConnection) -> User | None:
    """Retrieve the ``User`` ORM instance from the database using the JWT ``sub`` claim.

    This function is called by Litestar on every request that requires
    authentication.  Returning ``None`` triggers an automatic 401 response.

    Args:
        token: Decoded JWT payload.
        connection: The current ASGI connection (unused but required by the interface).

    Returns:
        The matching ``User`` or ``None`` if no user is found for ``token.sub``.
    """
    try:
        user_id = uuid.UUID(token.sub)
    except (ValueError, AttributeError):
        return None

    async with async_session_factory() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

    if user is None:
        return None

    # Check whether the token has been blacklisted (logout)
    from src.core.database import get_valkey

    valkey = get_valkey()
    try:
        is_blacklisted = await valkey.get(f"token:blacklist:{token.jti}")
        if is_blacklisted:
            return None
    except Exception:
        # If Valkey is unreachable we still allow the request through to avoid
        # total service disruption.
        logger.warning("valkey_token_check_failed", exc_info=True)

    return user


# ---------------------------------------------------------------------------
# JWTAuth instance
# ---------------------------------------------------------------------------

_settings = get_settings()

jwt_auth: JWTAuth[User] = JWTAuth[User](
    retrieve_user_handler=retrieve_user_handler,
    token_secret=_settings.JWT_SECRET_KEY,
    default_token_expiration=timedelta(minutes=_settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    algorithm="HS256",
    exclude=["/health", "/schema"],
)


# ---------------------------------------------------------------------------
# Role guard factory
# ---------------------------------------------------------------------------


def require_role(role: str):
    """Return a Litestar guard that ensures the authenticated user has the given *role*.

    Usage::

        @post("/admin-action", guards=[require_role("owner")])
        async def admin_action(self, request: Request[User, Token, Any]) -> dict: ...

    Raises:
        PermissionDeniedException: When the user does not hold the required role.
    """

    async def _role_guard(connection: ASGIConnection, handler: BaseRouteHandler) -> None:
        user: User | None = connection.user
        if user is None:
            raise NotAuthorizedException(detail="Authentication required")
        if user.role != role:
            raise PermissionDeniedException(
                detail=f"Role '{role}' required. Current role: '{user.role}'",
            )

    _role_guard.__doc__ = f"Guard: require role '{role}'"
    _role_guard.__qualname__ = f"require_role.<{role}>"
    return _role_guard


# ---------------------------------------------------------------------------
# Token creation helpers
# ---------------------------------------------------------------------------


def create_access_token(user: User) -> str:
    """Create a short-lived JWT access token for the given user.

    The token includes custom claims (``email``, ``role``) in addition to
    the standard ``sub`` (user UUID) claim.
    """
    return jwt_auth.create_token(
        identifier=str(user.id),
        token_expiration=timedelta(minutes=_settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        token_extras={"email": user.email, "role": user.role},
    )


def create_refresh_token(user: User) -> str:
    """Create a long-lived JWT refresh token for the given user.

    The token carries a ``type: refresh`` claim so the refresh endpoint
    can distinguish it from access tokens.
    """
    return jwt_auth.create_token(
        identifier=str(user.id),
        token_expiration=timedelta(days=_settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        token_extras={"type": "refresh", "email": user.email, "role": user.role},
    )
