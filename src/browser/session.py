"""Browser session (cookie) persistence via Valkey.

Stores and restores Playwright browser cookies in Valkey so that sessions
survive across browser restarts, proxy rotations, and server reboots.

Key layout::

    browser:session:{platform}:cookies  ->  JSON-serialised cookie list
    browser:session:{platform}:meta     ->  JSON metadata (last_saved, count)

Both keys receive a configurable TTL (default 72 hours).
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis
import structlog

from src.browser.stealth import StealthContext

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

_KEY_PREFIX = "browser:session"


def _cookies_key(platform: str) -> str:
    """Return the Valkey key for a platform's cookies."""
    return f"{_KEY_PREFIX}:{platform}:cookies"


def _meta_key(platform: str) -> str:
    """Return the Valkey key for a platform's session metadata."""
    return f"{_KEY_PREFIX}:{platform}:meta"


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """Manages browser cookie persistence in Valkey.

    Parameters
    ----------
    valkey:
        An ``redis.asyncio.Redis`` client connected to the Valkey instance.
    ttl_hours:
        How many hours cookies remain valid before Valkey expires them.
        Defaults to 72 (3 days).
    """

    def __init__(
        self,
        valkey: aioredis.Redis,
        ttl_hours: int = 72,
    ) -> None:
        self._valkey = valkey
        self._ttl_seconds = ttl_hours * 3600
        self._log = logger.bind(component="session_manager")

    # -- public API --------------------------------------------------------

    async def save_session(self, platform: str, context: StealthContext) -> None:
        """Extract cookies from *context* and persist them in Valkey.

        Also writes session metadata (timestamp and cookie count).
        """
        cookies = await context.cookies()
        cookie_json = json.dumps(cookies, default=str)

        key = _cookies_key(platform)
        await self._valkey.set(key, cookie_json, ex=self._ttl_seconds)

        meta = {
            "last_saved": time.time(),
            "cookie_count": len(cookies),
            "platform": platform,
        }
        await self.save_meta(platform, meta)

        self._log.info(
            "session_saved",
            platform=platform,
            cookie_count=len(cookies),
            ttl_hours=self._ttl_seconds // 3600,
        )

    async def restore_session(self, platform: str, context: StealthContext) -> bool:
        """Restore previously-saved cookies into *context*.

        Returns ``True`` if cookies were found and applied, ``False`` otherwise.
        """
        key = _cookies_key(platform)
        raw_bytes: bytes | str | None = await self._valkey.get(key)

        if raw_bytes is None:
            self._log.debug("no_session_found", platform=platform)
            return False

        raw = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes

        try:
            cookies: list[dict[str, Any]] = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            self._log.warning(
                "session_decode_error",
                platform=platform,
                error=str(exc),
            )
            return False

        if not cookies:
            self._log.debug("empty_session", platform=platform)
            return False

        await context.add_cookies(cookies)
        self._log.info(
            "session_restored",
            platform=platform,
            cookie_count=len(cookies),
        )
        return True

    async def clear_session(self, platform: str) -> None:
        """Delete all session data for *platform*."""
        await self._valkey.delete(_cookies_key(platform))
        await self._valkey.delete(_meta_key(platform))
        self._log.info("session_cleared", platform=platform)

    async def get_session_meta(self, platform: str) -> dict[str, Any] | None:
        """Return stored metadata for *platform*, or ``None`` if absent."""
        raw_bytes: bytes | str | None = await self._valkey.get(_meta_key(platform))
        if raw_bytes is None:
            return None
        raw = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            return None

    async def save_meta(self, platform: str, meta: dict[str, Any]) -> None:
        """Persist session metadata to Valkey with the standard TTL."""
        key = _meta_key(platform)
        await self._valkey.set(key, json.dumps(meta, default=str), ex=self._ttl_seconds)
        self._log.debug("meta_saved", platform=platform, meta_keys=list(meta.keys()))


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def create_session_manager(ttl_hours: int = 72) -> SessionManager:
    """Create a :class:`SessionManager` using the shared Valkey connection.

    Convenience factory for use in graph nodes and adapters where explicit
    dependency injection is not yet wired up.
    """
    from src.core.database import get_valkey  # noqa: PLC0415

    valkey = get_valkey()
    return SessionManager(valkey=valkey, ttl_hours=ttl_hours)
