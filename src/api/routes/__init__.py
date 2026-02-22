"""Shared utilities for API route modules."""


def _escape_like(value: str) -> str:
    """Escape SQL LIKE/ILIKE wildcards to prevent wildcard injection."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
