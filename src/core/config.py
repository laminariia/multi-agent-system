"""Application configuration via pydantic-settings.

All environment variables are loaded from the .env file and/or the process environment.
Canonical variable names follow TECH_STACK.md exactly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings for the Multi-Agent Service.

    Required variables will raise a validation error at startup if absent.
    Optional variables default to ``None`` (or a sensible default) so the
    system can start in development mode without every external service key.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://mas:mas_password@localhost:5432/mas"
    VALKEY_URL: str = "valkey://localhost:6379"

    # ── LLM APIs (OpenRouter — single key for all providers) ─────────────
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Legacy per-provider keys (unused when OpenRouter is configured)
    GEMINI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # ── Sandbox ──────────────────────────────────────────────────────────
    E2B_API_KEY: str | None = None

    # ── Freelance Platforms ──────────────────────────────────────────────
    FREELANCER_CLIENT_ID: str | None = None
    FREELANCER_CLIENT_SECRET: str | None = None

    # ── Enrichment ───────────────────────────────────────────────────────
    HUNTER_API_KEY: str | None = None
    APOLLO_API_KEY: str | None = None

    # ── Proxy (BrightData) ───────────────────────────────────────────────
    BRIGHTDATA_USERNAME: str | None = None
    BRIGHTDATA_PASSWORD: str | None = None
    BRIGHTDATA_HOST: str = "brd.superproxy.io"

    # ── Auth ─────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "change-me-in-production"  # noqa: S105
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ENCRYPTION_KEY: str = "change-me-in-production"

    # ── CORS ───────────────────────────────────────────────────────────
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # ── Application ────────────────────────────────────────────────────
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Notifications ────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    # ── Monitoring ───────────────────────────────────────────────────────
    LANGSMITH_API_KEY: str | None = None
    SENTRY_DSN: str | None = None

    # ── Computed ─────────────────────────────────────────────────────────

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """Convert the standard ``postgresql://`` URL to ``postgresql+asyncpg://``."""
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql+asyncpg://"):
            return url
        raise ValueError(f"Unsupported DATABASE_URL scheme: {url}")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def valkey_redis_url(self) -> str:
        """Return a ``redis://`` URL compatible with ``redis-py``.

        Valkey uses ``valkey://`` by convention, but the ``redis`` Python
        client only understands ``redis://``.
        """
        url = self.VALKEY_URL
        if url.startswith("valkey://"):
            return url.replace("valkey://", "redis://", 1)
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of :class:`Settings`."""
    return Settings()
