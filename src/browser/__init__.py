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
    )
"""

from __future__ import annotations

from src.browser.pool import BrowserPool, PoolConfig
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
]
