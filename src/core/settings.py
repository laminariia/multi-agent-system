"""Re-export settings from the canonical config module.

The authoritative Settings class lives in ``src.core.config``.
This module exists for convenience / backward compatibility.
"""

from src.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
