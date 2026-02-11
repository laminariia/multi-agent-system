"""Platform account and API key management routes.

Provides CRUD operations for platform accounts (Freelancer, Upwork, etc.)
and a unified endpoint for managing API keys.  All credentials are encrypted
at rest using Fernet and never returned in plain text.

Mounted at ``/api/v1/settings``.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from cryptography.fernet import InvalidToken
from litestar import Controller, Request, delete, get, put
from litestar.exceptions import ClientException, NotFoundException
from litestar.handlers import post
from litestar.security.jwt import Token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.core.models import PlatformAccount, User
from src.security.encryption import (
    decrypt_credentials,
    encrypt_credentials,
    mask_dict,
    mask_value,
)

logger = structlog.get_logger(__name__)

# The sentinel platform name used to store API keys as a PlatformAccount row.
_API_KEYS_PLATFORM = "__api_keys__"

# Well-known API key field names (mapped from Settings attribute names).
_KNOWN_API_KEYS: list[str] = [
    "gemini_api_key",
    "anthropic_api_key",
    "openai_api_key",
    "openrouter_api_key",
    "e2b_api_key",
    "hunter_api_key",
    "apollo_api_key",
    "langsmith_api_key",
]

# Allowed platform names for platform accounts.
_ALLOWED_PLATFORMS: set[str] = {
    "freelancer",
    "upwork",
    "fl_ru",
    "kwork",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_account(account: PlatformAccount) -> dict[str, Any]:
    """Serialise a PlatformAccount to a dict with credentials masked."""
    try:
        plain_creds = decrypt_credentials(account.credentials)
        masked_creds = mask_dict(plain_creds)
    except (InvalidToken, Exception):
        # If decryption fails (key rotation, corruption), show opaque marker.
        masked_creds = {"_status": "encrypted (cannot preview)"}

    return {
        "id": str(account.id),
        "platform": account.platform,
        "username": account.username,
        "status": account.status,
        "profile_url": account.profile_url,
        "stats": account.stats,
        "last_health_check": (
            account.last_health_check.isoformat() if account.last_health_check else None
        ),
        "credentials_masked": masked_creds,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


def _build_api_key_summary(settings_obj: Any, user_account: PlatformAccount | None) -> dict[str, Any]:
    """Build the ``api_keys`` section of the credentials overview.

    Merges environment-level keys from Settings with any user-stored keys.
    """
    summary: dict[str, Any] = {}

    # 1. Check environment-level keys from Settings
    env_map: dict[str, str] = {
        "gemini_api_key": "GEMINI_API_KEY",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "e2b_api_key": "E2B_API_KEY",
        "hunter_api_key": "HUNTER_API_KEY",
        "apollo_api_key": "APOLLO_API_KEY",
        "langsmith_api_key": "LANGSMITH_API_KEY",
    }

    for key_name, settings_attr in env_map.items():
        env_value = getattr(settings_obj, settings_attr, None) or ""
        if env_value:
            summary[key_name] = {
                "configured": True,
                "source": "environment",
                "masked": mask_value(str(env_value)),
            }
        else:
            summary[key_name] = {
                "configured": False,
                "source": None,
                "masked": None,
            }

    # 2. Overlay user-stored keys (take precedence for display)
    if user_account is not None:
        try:
            plain = decrypt_credentials(user_account.credentials)
            for key_name in _KNOWN_API_KEYS:
                val = plain.get(key_name, "")
                if val:
                    summary[key_name] = {
                        "configured": True,
                        "source": "user",
                        "masked": mask_value(str(val)),
                    }
        except (InvalidToken, Exception):
            logger.warning("settings.api_keys_decrypt_failed")

    return summary


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class SettingsController(Controller):
    """Manage platform accounts and API keys."""

    path = "/api/v1/settings"
    tags = ["settings"]
    guards = [require_role("owner", "co_owner")]

    # -----------------------------------------------------------------
    # GET /api/v1/settings/credentials
    # -----------------------------------------------------------------

    @get(
        "/credentials",
        summary="Credentials overview (masked)",
        description=(
            "Returns a summary of configured API keys (from environment and "
            "user storage) and all platform accounts.  No raw secrets are "
            "ever included in the response."
        ),
    )
    async def get_credentials_overview(
        self,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Build a combined view of API keys and platform accounts."""
        user_id = uuid.UUID(request.auth.sub)

        # Fetch the user's API-keys account (if any)
        api_keys_account = await self._get_api_keys_account(db_session, user_id)

        # Fetch all platform accounts for the user (excluding the sentinel)
        stmt = (
            select(PlatformAccount)
            .where(
                PlatformAccount.user_id == user_id,
                PlatformAccount.platform != _API_KEYS_PLATFORM,
            )
            .order_by(PlatformAccount.created_at.desc())
        )
        result = await db_session.execute(stmt)
        accounts = list(result.scalars().all())

        # Build API keys summary — lazy import to avoid circular deps at module level
        from src.core.config import get_settings  # noqa: PLC0415

        settings_obj = get_settings()
        api_keys = _build_api_key_summary(settings_obj, api_keys_account)

        return {
            "api_keys": api_keys,
            "platform_accounts": [_mask_account(a) for a in accounts],
        }

    # -----------------------------------------------------------------
    # GET /api/v1/settings/platform-accounts
    # -----------------------------------------------------------------

    @get(
        "/platform-accounts",
        summary="List platform accounts",
        description="Lists all platform accounts for the current user with credentials masked.",
    )
    async def list_platform_accounts(
        self,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Return platform accounts for the authenticated user."""
        user_id = uuid.UUID(request.auth.sub)

        stmt = (
            select(PlatformAccount)
            .where(
                PlatformAccount.user_id == user_id,
                PlatformAccount.platform != _API_KEYS_PLATFORM,
            )
            .order_by(PlatformAccount.created_at.desc())
        )
        result = await db_session.execute(stmt)
        accounts = list(result.scalars().all())

        return {
            "accounts": [_mask_account(a) for a in accounts],
            "total": len(accounts),
        }

    # -----------------------------------------------------------------
    # POST /api/v1/settings/platform-accounts
    # -----------------------------------------------------------------

    @post(
        "/platform-accounts",
        summary="Create a platform account",
        description="Creates a new platform account with encrypted credentials.",
        status_code=201,
    )
    async def create_platform_account(
        self,
        data: dict[str, Any],
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Create a new platform account."""
        user_id = uuid.UUID(request.auth.sub)

        platform = data.get("platform", "")
        if platform not in _ALLOWED_PLATFORMS:
            raise ClientException(
                detail=f"Invalid platform '{platform}'. Allowed: {sorted(_ALLOWED_PLATFORMS)}",
                status_code=400,
            )

        credentials = data.get("credentials")
        if not credentials or not isinstance(credentials, dict):
            raise ClientException(
                detail="Field 'credentials' is required and must be a JSON object",
                status_code=400,
            )

        # Check for duplicate platform+user
        dup_stmt = select(PlatformAccount).where(
            PlatformAccount.user_id == user_id,
            PlatformAccount.platform == platform,
        )
        dup_result = await db_session.execute(dup_stmt)
        if dup_result.scalar_one_or_none() is not None:
            raise ClientException(
                detail=f"A '{platform}' account already exists. Use PUT to update it.",
                status_code=409,
            )

        encrypted = encrypt_credentials(credentials)

        account = PlatformAccount(
            user_id=user_id,
            platform=platform,
            username=data.get("username"),
            credentials=encrypted,
            status="active",
            profile_url=data.get("profile_url"),
        )
        db_session.add(account)
        await db_session.flush()

        logger.info(
            "settings.platform_account_created",
            user_id=str(user_id),
            platform=platform,
            account_id=str(account.id),
        )

        return _mask_account(account)

    # -----------------------------------------------------------------
    # PUT /api/v1/settings/platform-accounts/{account_id}
    # -----------------------------------------------------------------

    @put(
        "/platform-accounts/{account_id:uuid}",
        summary="Update a platform account",
        description="Updates an existing platform account. New credentials are re-encrypted.",
    )
    async def update_platform_account(
        self,
        account_id: uuid.UUID,
        data: dict[str, Any],
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Update an existing platform account."""
        user_id = uuid.UUID(request.auth.sub)
        account = await self._get_account(db_session, account_id)

        # Only the owner of the account (or an admin) can update
        if account.user_id != user_id:
            raise ClientException(
                detail="You can only update your own platform accounts",
                status_code=403,
            )

        # Disallow changing the sentinel platform
        if account.platform == _API_KEYS_PLATFORM:
            raise ClientException(
                detail="Use the PUT /api/v1/settings/api-keys endpoint to update API keys",
                status_code=400,
            )

        # Update optional fields
        if "username" in data:
            account.username = data["username"]
        if "profile_url" in data:
            account.profile_url = data["profile_url"]
        if "status" in data and data["status"] in {"active", "inactive", "suspended"}:
            account.status = data["status"]

        # Re-encrypt credentials if provided
        if "credentials" in data:
            credentials = data["credentials"]
            if not isinstance(credentials, dict):
                raise ClientException(
                    detail="Field 'credentials' must be a JSON object",
                    status_code=400,
                )
            account.credentials = encrypt_credentials(credentials)

        await db_session.flush()

        logger.info(
            "settings.platform_account_updated",
            user_id=str(user_id),
            account_id=str(account_id),
            platform=account.platform,
        )

        return _mask_account(account)

    # -----------------------------------------------------------------
    # DELETE /api/v1/settings/platform-accounts/{account_id}
    # -----------------------------------------------------------------

    @delete(
        "/platform-accounts/{account_id:uuid}",
        summary="Delete a platform account",
        status_code=200,
    )
    async def delete_platform_account(
        self,
        account_id: uuid.UUID,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Delete a platform account.

        Only the account owner or an owner/co_owner can delete.
        """
        user_id = uuid.UUID(request.auth.sub)
        caller = request.user
        account = await self._get_account(db_session, account_id)

        # Allow deletion by account owner or system owner/co_owner
        if account.user_id != user_id and caller.role not in {"owner", "co_owner"}:
            raise ClientException(
                detail="You can only delete your own platform accounts",
                status_code=403,
            )

        # Disallow deleting the sentinel api-keys account via this endpoint
        if account.platform == _API_KEYS_PLATFORM:
            raise ClientException(
                detail="API keys cannot be deleted via this endpoint",
                status_code=400,
            )

        platform = account.platform
        await db_session.delete(account)
        await db_session.flush()

        logger.info(
            "settings.platform_account_deleted",
            user_id=str(user_id),
            account_id=str(account_id),
            platform=platform,
        )

        return {"message": f"Platform account '{platform}' deleted", "id": str(account_id)}

    # -----------------------------------------------------------------
    # PUT /api/v1/settings/api-keys
    # -----------------------------------------------------------------

    @put(
        "/api-keys",
        summary="Save or update API keys",
        description=(
            "Stores API keys for the current user.  Keys are encrypted "
            "at rest and stored as a special platform account."
        ),
    )
    async def update_api_keys(
        self,
        data: dict[str, Any],
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Create or update the user's API key store."""
        user_id = uuid.UUID(request.auth.sub)

        # Validate: only known key names allowed
        unknown = set(data.keys()) - set(_KNOWN_API_KEYS)
        if unknown:
            raise ClientException(
                detail=f"Unknown API key names: {sorted(unknown)}. Allowed: {sorted(_KNOWN_API_KEYS)}",
                status_code=400,
            )

        # Filter out empty values so we don't store blanks
        clean_data = {k: v for k, v in data.items() if v}

        account = await self._get_api_keys_account(db_session, user_id)

        if account is None:
            # Create new sentinel row
            encrypted = encrypt_credentials(clean_data)
            account = PlatformAccount(
                user_id=user_id,
                platform=_API_KEYS_PLATFORM,
                username=None,
                credentials=encrypted,
                status="active",
            )
            db_session.add(account)
        else:
            # Merge with existing keys (new values overwrite old ones)
            try:
                existing = decrypt_credentials(account.credentials)
            except (InvalidToken, Exception):
                existing = {}

            merged = {**existing, **clean_data}
            # Remove keys set to empty string explicitly
            for k, v in data.items():
                if not v and k in merged:
                    del merged[k]

            account.credentials = encrypt_credentials(merged)

        await db_session.flush()

        # Build masked response
        try:
            plain = decrypt_credentials(account.credentials)
        except (InvalidToken, Exception):
            plain = {}

        masked_keys: dict[str, Any] = {}
        for key_name in _KNOWN_API_KEYS:
            val = plain.get(key_name, "")
            masked_keys[key_name] = {
                "configured": bool(val),
                "masked": mask_value(str(val)) if val else None,
            }

        logger.info(
            "settings.api_keys_updated",
            user_id=str(user_id),
            keys_count=len(clean_data),
        )

        return {"api_keys": masked_keys, "message": "API keys updated"}

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    async def _get_account(db_session: AsyncSession, account_id: uuid.UUID) -> PlatformAccount:
        """Fetch a platform account by ID or raise 404."""
        stmt = select(PlatformAccount).where(PlatformAccount.id == account_id)
        result = await db_session.execute(stmt)
        account = result.scalar_one_or_none()

        if account is None:
            raise NotFoundException(detail="Platform account not found")

        return account

    @staticmethod
    async def _get_api_keys_account(
        db_session: AsyncSession,
        user_id: uuid.UUID,
    ) -> PlatformAccount | None:
        """Fetch the sentinel ``__api_keys__`` account for a user, or ``None``."""
        stmt = select(PlatformAccount).where(
            PlatformAccount.user_id == user_id,
            PlatformAccount.platform == _API_KEYS_PLATFORM,
        )
        result = await db_session.execute(stmt)
        return result.scalar_one_or_none()
