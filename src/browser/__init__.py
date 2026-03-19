"""Browser automation module -- anti-detection Playwright wrappers.

Public API::

    from src.browser import (
        BrowserPool,
        PoolConfig,
        SessionManager,
        StealthBrowser,
        StealthConfig,
        StealthContext,
        StealthPage,
        handle_ban_recovery,
        is_platform_cooling_down,
    )
"""

from __future__ import annotations

from src.browser.pool import BrowserPool, PoolConfig
from src.browser.recovery import handle_ban_recovery, is_platform_cooling_down
from src.browser.session import SessionManager
from src.browser.stealth import StealthBrowser, StealthConfig, StealthContext, StealthPage

__all__ = [
    "BrowserPool",
    "PoolConfig",
    "SessionManager",
    "StealthBrowser",
    "StealthConfig",
    "StealthContext",
    "StealthPage",
    "handle_ban_recovery",
    "is_platform_cooling_down",
]
