"""Runtime credential loader that merges DB-stored keys with environment variables.

DB-stored credentials (from Settings page) take precedence over .env values,
allowing users on Railway to manage keys via the dashboard.

Three public functions:

* :func:`load_runtime_credentials` -- all API keys merged (DB > env).
* :func:`get_api_key` -- single key lookup with DB-first fallback.
* :func:`load_platform_credentials` -- credentials for a specific platform
  account (freelancer, upwork, etc.).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

# Sentinel platform used by the Settings API to store API keys.
_API_KEYS_PLATFORM = "__api_keys__"

# Maps lowercase credential names used in the DB (``__api_keys__`` row)
# to the uppercase attribute names on :class:`src.core.config.Settings`.
_KEY_NAME_TO_SETTINGS_ATTR: dict[str, str] = {
    "gemini_api_key": "GEMINI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "e2b_api_key": "E2B_API_KEY",
    "hunter_api_key": "HUNTER_API_KEY",
    "apollo_api_key": "APOLLO_API_KEY",
    "langsmith_api_key": "LANGSMITH_API_KEY",
}


async def load_runtime_credentials(
    user_id: str | None = None,
) -> dict[str, str]:
    """Load all API keys, merging environment defaults with DB-stored overrides.

    The returned dictionary uses the *lowercase* key names
    (``gemini_api_key``, ``anthropic_api_key``, etc.).

    Merge strategy: DB values **override** environment values when both are
    present.  This lets operators set baseline keys via ``.env`` while users
    on Railway can override individual keys through the dashboard.

    Args:
        user_id: Optional user UUID (as string).  If provided the function
            queries the ``PlatformAccount`` table for the ``__api_keys__``
            sentinel row belonging to that user.

    Returns:
        A flat ``{key_name: value}`` dict.  Keys with empty/None values are
        omitted.
    """
    from src.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()

    # 1. Collect env-level defaults
    result: dict[str, str] = {}
    for key_name, settings_attr in _KEY_NAME_TO_SETTINGS_ATTR.items():
        env_value = getattr(settings, settings_attr, None)
        if env_value:
            result[key_name] = str(env_value)

    # 2. Overlay DB-stored keys (if user_id given)
    if user_id is not None:
        db_keys = await _fetch_api_keys_from_db(user_id)
        if db_keys:
            for key_name, value in db_keys.items():
                if value:
                    result[key_name] = str(value)

    return result


async def get_api_key(
    key_name: str,
    user_id: str | None = None,
) -> str | None:
    """Return a single API key value, checking the DB first then env vars.

    Args:
        key_name: Lowercase key name, e.g. ``"gemini_api_key"``.
        user_id: Optional user UUID string.  When given, the DB is checked
            first for a user-stored override.

    Returns:
        The key value, or ``None`` if not configured anywhere.
    """
    # 1. Try DB first (user-stored override)
    if user_id is not None:
        db_keys = await _fetch_api_keys_from_db(user_id)
        if db_keys:
            db_value = db_keys.get(key_name)
            if db_value:
                logger.debug(
                    "credential_loader.db_hit",
                    key=key_name,
                    source="database",
                )
                return str(db_value)

    # 2. Fall back to env var from Settings
    from src.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    settings_attr = _KEY_NAME_TO_SETTINGS_ATTR.get(key_name, key_name.upper())
    env_value = getattr(settings, settings_attr, None)
    if env_value:
        logger.debug(
            "credential_loader.env_hit",
            key=key_name,
            source="environment",
        )
        return str(env_value)

    return None


async def load_platform_credentials(
    platform: str,
    user_id: str | None = None,
) -> dict[str, str] | None:
    """Load decrypted credentials for a specific platform account.

    Looks up the ``PlatformAccount`` row matching the given *platform* name.
    If *user_id* is provided, only accounts belonging to that user are
    considered.  Otherwise the first active owner's account is returned.

    Args:
        platform: Platform identifier (``"freelancer"``, ``"upwork"``, etc.).
        user_id: Optional user UUID string to scope the lookup.

    Returns:
        A ``{field: value}`` dict of decrypted credentials, or ``None`` if no
        matching account exists or decryption fails.
    """
    from src.core.database import get_db_session  # noqa: PLC0415
    from src.core.models import PlatformAccount, User  # noqa: PLC0415
    from src.security.encryption import decrypt_credentials  # noqa: PLC0415

    try:
        async with get_db_session() as session:
            if user_id is not None:
                uid = uuid.UUID(user_id)
                stmt = select(PlatformAccount).where(
                    PlatformAccount.user_id == uid,
                    PlatformAccount.platform == platform,
                    PlatformAccount.status == "active",
                )
            else:
                # Pick the first active account for this platform owned by
                # an active owner user (fallback when no user context).
                stmt = (
                    select(PlatformAccount)
                    .join(User, PlatformAccount.user_id == User.id)
                    .where(
                        PlatformAccount.platform == platform,
                        PlatformAccount.status == "active",
                        User.role == "owner",
                        User.status == "active",
                    )
                    .order_by(PlatformAccount.created_at.asc())
                    .limit(1)
                )

            result = await session.execute(stmt)
            account = result.scalar_one_or_none()

            if account is None:
                logger.debug(
                    "credential_loader.platform_not_found",
                    platform=platform,
                    user_id=user_id,
                )
                return None

            plain = decrypt_credentials(account.credentials)
            # Ensure all values are strings
            return {k: str(v) for k, v in plain.items() if v}

    except Exception:
        logger.warning(
            "credential_loader.platform_load_failed",
            platform=platform,
            user_id=user_id,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_api_keys_from_db(user_id: str) -> dict[str, Any] | None:
    """Query the ``__api_keys__`` sentinel row and decrypt it.

    Returns the decrypted dict, or ``None`` on any error (missing row,
    decryption failure, DB unavailable).  Errors are logged as warnings so
    the caller can gracefully fall back to environment variables.
    """
    from src.core.database import get_db_session  # noqa: PLC0415
    from src.core.models import PlatformAccount  # noqa: PLC0415
    from src.security.encryption import decrypt_credentials  # noqa: PLC0415

    try:
        uid = uuid.UUID(user_id)
        async with get_db_session() as session:
            stmt = select(PlatformAccount).where(
                PlatformAccount.user_id == uid,
                PlatformAccount.platform == _API_KEYS_PLATFORM,
            )
            result = await session.execute(stmt)
            account = result.scalar_one_or_none()

            if account is None:
                return None

            return decrypt_credentials(account.credentials)

    except Exception:
        logger.warning(
            "credential_loader.db_fetch_failed",
            user_id=user_id,
            exc_info=True,
        )
        return None
