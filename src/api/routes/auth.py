"""Authentication routes.

Provides login, token refresh, logout, and current-user endpoints
at ``/api/v1/auth``.  Rate-limited to 10 requests per minute as
specified in the API specification.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
import structlog
from litestar import Controller, Request, Response, get, post
from litestar.exceptions import NotAuthorizedException
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
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
from src.core.config import Settings, get_settings
from src.core.models import User

logger = structlog.get_logger(__name__)

# Rate limit applied to the entire auth controller.
# Set high because Railway reverse proxy makes all requests appear from the
# same internal IP — IP-based rate limiting is effectively global, not per-user.
auth_rate_limit = RateLimitConfig(rate_limit=("minute", 60), exclude=["/api/v1/auth/me"])


class AuthController(Controller):
    """Handles user authentication, token lifecycle, and session management."""

    path = "/api/v1/auth"
    tags = ["auth"]
    middleware = [auth_rate_limit.middleware]

    # -----------------------------------------------------------------
    # POST /api/v1/auth/register
    # -----------------------------------------------------------------

    @post(
        "/register",
        summary="Create a new user account",
        exclude_from_auth=True,
    )
    async def register(
        self,
        data: RegisterRequestSchema,
        db_session: AsyncSession,
    ) -> Response[LoginResponseSchema | RegisterPendingResponseSchema]:
        """Register a new user.

        The first registered user is assigned the ``owner`` role and
        auto-logged in.  Subsequent registrations require owner approval
        and return HTTP 202 with a pending status message.

        Raises:
            litestar.exceptions.ClientException: When the email is already
                registered (409 Conflict).
        """
        from litestar.exceptions import ClientException

        stmt = select(User).where(User.email == data.email)
        result = await db_session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            raise ClientException(
                detail="A user with this email already exists",
                status_code=409,
            )

        # Count existing users to determine role/status
        count_stmt = select(func.count()).select_from(User)
        count_result = await db_session.execute(count_stmt)
        user_count = count_result.scalar_one()

        if user_count == 0:
            # First user → owner, auto-approved
            user = User(
                email=data.email,
                password_hash=hash_password(data.password),
                name=data.name,
                role="owner",
                status="active",
            )
            db_session.add(user)
            await db_session.flush()

            access_token = create_access_token(user)
            refresh_token = create_refresh_token(user)

            logger.info("auth.register_owner", user_id=str(user.id), email=user.email)

            return Response(
                content=LoginResponseSchema(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    user=UserResponseSchema.model_validate(user),
                ),
                status_code=200,
            )

        # Subsequent users → pending approval
        user = User(
            email=data.email,
            password_hash=hash_password(data.password),
            name=data.name,
            role="viewer",
            status="pending_approval",
        )
        db_session.add(user)
        await db_session.flush()

        logger.info("auth.register_pending", user_id=str(user.id), email=user.email)

        return Response(
            content=RegisterPendingResponseSchema(
                message="Registration submitted. An administrator must approve your account before you can sign in.",
                status="pending_approval",
            ),
            status_code=202,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/auth/login
    # -----------------------------------------------------------------

    @post(
        "/login",
        summary="Authenticate with email and password",
        exclude_from_auth=True,
        status_code=200,
    )
    async def login(
        self,
        data: LoginRequestSchema,
        db_session: AsyncSession,
    ) -> LoginResponseSchema:
        """Validate credentials and return JWT access + refresh token pair.

        Raises:
            NotAuthorizedException: When the email is not found or the password
                does not match.
        """
        stmt = select(User).where(User.email == data.email)
        result = await db_session.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None or user.password_hash is None:
            logger.info("auth.login_failed", email=data.email, reason="user_not_found")
            raise NotAuthorizedException(detail="Invalid email or password")

        if not verify_password(data.password, user.password_hash):
            logger.info("auth.login_failed", email=data.email, reason="bad_password")
            raise NotAuthorizedException(detail="Invalid email or password")

        # Block non-active users
        if user.status != "active":
            status_messages = {
                "pending_approval": "Your account is awaiting administrator approval.",
                "rejected": "Your registration was not approved.",
                "suspended": "Your account has been suspended.",
            }
            msg = status_messages.get(user.status, "Your account is not active.")
            logger.info("auth.login_blocked", email=data.email, status=user.status)
            raise NotAuthorizedException(detail=msg)

        # Update last login timestamp
        user.last_login_at = datetime.now(UTC)
        await db_session.flush()

        access_token = create_access_token(user)
        refresh_token = create_refresh_token(user)

        logger.info("auth.login_success", user_id=str(user.id), email=user.email)

        return LoginResponseSchema(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponseSchema.model_validate(user),
        )

    # -----------------------------------------------------------------
    # POST /api/v1/auth/refresh
    # -----------------------------------------------------------------

    @post(
        "/refresh",
        summary="Refresh an access token",
        exclude_from_auth=True,
        status_code=200,
    )
    async def refresh(
        self,
        data: TokenRefreshSchema,
        db_session: AsyncSession,
    ) -> TokenRefreshResponseSchema:
        """Exchange a valid refresh token for a new access + refresh token pair.

        Raises:
            NotAuthorizedException: When the refresh token is invalid, expired,
                or does not carry a ``type: refresh`` claim.
        """
        settings = get_settings()
        try:
            payload = Token.decode(
                encoded_token=data.refresh_token,
                secret=settings.JWT_SECRET_KEY,
                algorithm="HS256",
            )
        except Exception as exc:
            raise NotAuthorizedException(detail="Invalid or expired refresh token") from exc

        # Ensure this is actually a refresh token
        extras: dict[str, Any] = payload.extras or {}
        if extras.get("type") != "refresh":
            raise NotAuthorizedException(detail="Token is not a refresh token")

        stmt = select(User).where(User.id == payload.sub)
        result = await db_session.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            raise NotAuthorizedException(detail="User not found")

        new_access = create_access_token(user)
        new_refresh = create_refresh_token(user)

        logger.info("auth.token_refreshed", user_id=str(user.id))

        return TokenRefreshResponseSchema(
            access_token=new_access,
            refresh_token=new_refresh,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/auth/logout
    # -----------------------------------------------------------------

    @post(
        "/logout",
        summary="Invalidate the current access token",
        status_code=200,
    )
    async def logout(
        self,
        request: Request[User, Token, Any],
        valkey: aioredis.Redis,
        settings: Settings,
    ) -> MessageSchema:
        """Add the current JWT to a blacklist stored in Valkey.

        The blacklist entry TTL matches the token's remaining lifetime so
        it is automatically cleaned up after expiration.
        """
        token = request.auth
        user = request.user

        # Calculate remaining TTL in seconds
        now_ts = int(datetime.now(UTC).timestamp())
        ttl = max(int(token.exp.timestamp()) - now_ts, 1)

        # Add token JTI to the blacklist
        await valkey.setex(
            name=f"token:blacklist:{token.jti}",
            time=ttl,
            value="1",
        )

        logger.info("auth.logout", user_id=str(user.id))

        return MessageSchema(message="Successfully logged out")

    # -----------------------------------------------------------------
    # GET /api/v1/auth/me
    # -----------------------------------------------------------------

    @get(
        "/me",
        summary="Get current authenticated user",
    )
    async def me(
        self,
        request: Request[User, Token, Any],
    ) -> UserResponseSchema:
        """Return the profile of the currently authenticated user."""
        return UserResponseSchema.model_validate(request.user)

    # -----------------------------------------------------------------
    # POST /api/v1/auth/telegram/link
    # -----------------------------------------------------------------

    @post(
        "/telegram/link",
        summary="Link a Telegram account using a bot-generated code",
        status_code=200,
    )
    async def telegram_link(
        self,
        data: TelegramLinkSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
        valkey: aioredis.Redis,
    ) -> MessageSchema:
        """Link the authenticated user's account to a Telegram chat.

        The user first obtains a 6-character code from the Telegram bot
        via the ``/start`` command.  That code is stored in Valkey with a
        10-minute TTL under the key ``telegram_link:<code>``.

        This endpoint validates the code, updates the user's
        ``telegram_chat_id``, and deletes the Valkey key.

        Raises:
            litestar.exceptions.ClientException: When the code is invalid,
                expired, or not found.
        """
        from litestar.exceptions import ClientException

        valkey_key = f"telegram_link:{data.code}"
        telegram_user_id_str: str | None = await valkey.get(valkey_key)

        if telegram_user_id_str is None:
            raise ClientException(
                detail="Invalid or expired link code. Request a new one from the Telegram bot via /start.",
                status_code=400,
            )

        try:
            telegram_user_id = int(telegram_user_id_str)
        except (ValueError, TypeError) as exc:
            raise ClientException(
                detail="Corrupted link code data. Request a new one via /start.",
                status_code=400,
            ) from exc

        # Update the authenticated user's telegram_chat_id
        user = request.user
        user.telegram_chat_id = telegram_user_id
        db_session.add(user)
        await db_session.flush()

        # Remove the consumed code
        await valkey.delete(valkey_key)

        logger.info(
            "auth.telegram_linked",
            user_id=str(user.id),
            telegram_chat_id=telegram_user_id,
        )

        return MessageSchema(message="Telegram account linked successfully.")
