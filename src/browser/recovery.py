"""Ban recovery logic for browser-based platform adapters.

When a platform ban, Cloudflare block, or CAPTCHA is detected, this module
coordinates the recovery process:

1. Release the browser slot for the affected platform.
2. Clear session cookies (the old session is compromised).
3. Set a cooldown period in Valkey to prevent immediate retry.
4. Log the event for auto-escalation monitoring.

The cooldown key layout::

    mas:ban_cooldown:{platform}  ->  "1"  (TTL = BAN_COOLDOWN_SECONDS)

Usage::

    from src.browser.recovery import handle_ban_recovery, is_platform_cooling_down

    if isinstance(error, PlatformBannedError):
        recovered = await handle_ban_recovery("fiverr", pool, error)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.browser.pool import BrowserPool

logger = structlog.get_logger(__name__)

# Default cooldown after a ban detection (30 minutes).
BAN_COOLDOWN_SECONDS: int = 1800

# Valkey key prefix for ban cooldowns.
_COOLDOWN_KEY_PREFIX = "mas:ban_cooldown"


def _cooldown_key(platform: str) -> str:
    """Return the Valkey key for a platform's ban cooldown."""
    return f"{_COOLDOWN_KEY_PREFIX}:{platform}"


async def handle_ban_recovery(
    platform: str,
    pool: BrowserPool,
    error: Exception,
    *,
    cooldown_seconds: int = BAN_COOLDOWN_SECONDS,
) -> bool:
    """Handle a platform ban by releasing the browser and setting a cooldown.

    Parameters
    ----------
    platform:
        The platform identifier (e.g. ``"fiverr"``, ``"kwork"``).
    pool:
        The :class:`BrowserPool` instance managing browser slots.
    error:
        The exception that triggered ban recovery (logged for diagnostics).
    cooldown_seconds:
        How long (in seconds) to block retries for this platform.
        Defaults to :data:`BAN_COOLDOWN_SECONDS` (1800 = 30 minutes).

    Returns
    -------
    ``True`` if recovery steps completed successfully, ``False`` on partial failure.
    """
    log = logger.bind(platform=platform, error_type=type(error).__name__)
    log.warning(
        "ban_recovery_started",
        error_message=str(error),
        cooldown_seconds=cooldown_seconds,
    )

    success = True

    # 1. Release the browser slot for this platform.
    try:
        await pool.release_platform(platform)
        log.info("browser_slot_released")
    except Exception as exc:  # noqa: BLE001
        log.error("browser_release_failed", release_error=str(exc))
        success = False

    # 2. Clear session cookies so the next session starts fresh.
    try:
        from src.browser.session import create_session_manager  # noqa: PLC0415

        session_mgr = create_session_manager()
        await session_mgr.clear_session(platform)
        log.info("session_cleared")
    except Exception as exc:  # noqa: BLE001
        log.error("session_clear_failed", clear_error=str(exc))
        success = False

    # 3. Set cooldown in Valkey to prevent immediate retry.
    try:
        from src.core.database import get_valkey  # noqa: PLC0415

        valkey = get_valkey()
        key = _cooldown_key(platform)
        cooldown_meta = f"{time.time():.0f}|{type(error).__name__}|{str(error)[:200]}"
        await valkey.set(key, cooldown_meta, ex=cooldown_seconds)
        log.info("cooldown_set", key=key, ttl_seconds=cooldown_seconds)
    except Exception as exc:  # noqa: BLE001
        log.error("cooldown_set_failed", cooldown_error=str(exc))
        success = False

    log.info("ban_recovery_completed", success=success)
    return success


async def is_platform_cooling_down(platform: str) -> bool:
    """Check whether *platform* is in a ban cooldown period.

    Returns ``True`` if a cooldown key exists in Valkey (meaning the platform
    was recently banned and should not be retried yet).
    """
    try:
        from src.core.database import get_valkey  # noqa: PLC0415

        valkey = get_valkey()
        key = _cooldown_key(platform)
        result = await valkey.exists(key)
        return bool(result)
    except Exception:  # noqa: BLE001
        # If Valkey is unreachable, assume NOT cooling down to avoid
        # permanently blocking the platform.
        logger.warning("cooldown_check_failed", platform=platform)
        return False


async def get_cooldown_info(platform: str) -> dict[str, Any] | None:
    """Return cooldown metadata for *platform*, or ``None`` if not cooling down.

    The returned dict contains:
    - ``platform``: the platform name
    - ``banned_at``: Unix timestamp when the ban was detected
    - ``error_type``: the exception class name
    - ``error_message``: truncated error message
    - ``ttl_seconds``: remaining cooldown time in seconds
    """
    try:
        from src.core.database import get_valkey  # noqa: PLC0415

        valkey = get_valkey()
        key = _cooldown_key(platform)
        raw: bytes | str | None = await valkey.get(key)
        if raw is None:
            return None

        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        ttl = await valkey.ttl(key)

        parts = raw_str.split("|", 2)
        return {
            "platform": platform,
            "banned_at": float(parts[0]) if len(parts) > 0 else 0.0,
            "error_type": parts[1] if len(parts) > 1 else "unknown",
            "error_message": parts[2] if len(parts) > 2 else "",
            "ttl_seconds": max(0, ttl),
        }
    except Exception:  # noqa: BLE001
        logger.warning("cooldown_info_failed", platform=platform)
        return None


async def clear_cooldown(platform: str) -> bool:
    """Manually clear the ban cooldown for *platform*.

    Returns ``True`` if the cooldown was cleared, ``False`` if it did not exist
    or could not be cleared.
    """
    try:
        from src.core.database import get_valkey  # noqa: PLC0415

        valkey = get_valkey()
        key = _cooldown_key(platform)
        deleted = await valkey.delete(key)
        if deleted:
            logger.info("cooldown_cleared", platform=platform)
        return bool(deleted)
    except Exception:  # noqa: BLE001
        logger.warning("cooldown_clear_failed", platform=platform)
        return False
