"""Tests for environment variable validation via Settings.

Verifies that required settings raise errors when missing, optional
settings have sensible defaults, and format expectations are met.
"""

from __future__ import annotations

import pytest

from src.core.config import Settings


class TestDatabaseUrl:
    """DATABASE_URL should always have a usable default."""

    def test_default_database_url(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.DATABASE_URL.startswith("postgresql://")

    def test_custom_database_url(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            DATABASE_URL="postgresql://u:p@db:5432/mydb",
        )
        assert s.DATABASE_URL == "postgresql://u:p@db:5432/mydb"

    def test_async_database_url_conversion(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            DATABASE_URL="postgresql://u:p@localhost:5432/db",
        )
        assert s.async_database_url.startswith("postgresql+asyncpg://")

    def test_async_database_url_already_async(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        )
        assert s.async_database_url == "postgresql+asyncpg://u:p@localhost:5432/db"

    def test_invalid_database_url_scheme_raises(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            DATABASE_URL="mysql://u:p@localhost:3306/db",
        )
        with pytest.raises(ValueError, match="Unsupported DATABASE_URL scheme"):
            _ = s.async_database_url


class TestAPIKeyFormats:
    """API keys should accept any string; empty is the default."""

    def test_anthropic_key_accepted(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            ANTHROPIC_API_KEY="sk-ant-test-key-12345",
        )
        assert s.ANTHROPIC_API_KEY == "sk-ant-test-key-12345"

    def test_openai_key_accepted(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            OPENAI_API_KEY="sk-test-key-67890",
        )
        assert s.OPENAI_API_KEY == "sk-test-key-67890"

    def test_empty_api_keys_default(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.ANTHROPIC_API_KEY == ""
        assert s.OPENAI_API_KEY == ""
        assert s.GEMINI_API_KEY == ""


class TestOptionalVarsDontFail:
    """Optional vars (platforms, enrichment, monitoring) must not fail when missing."""

    def test_freelancer_keys_none(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.FREELANCER_CLIENT_ID is None
        assert s.FREELANCER_CLIENT_SECRET is None

    def test_enrichment_keys_none(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.HUNTER_API_KEY is None
        assert s.APOLLO_API_KEY is None

    def test_proxy_keys_none(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.BRIGHTDATA_USERNAME is None
        assert s.BRIGHTDATA_PASSWORD is None

    def test_monitoring_keys_none(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.LANGSMITH_API_KEY is None
        assert s.SENTRY_DSN is None

    def test_telegram_keys_none(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.TELEGRAM_BOT_TOKEN is None
        assert s.TELEGRAM_CHAT_ID is None

    def test_e2b_key_none(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.E2B_API_KEY is None


class TestValkeyUrlDefaults:
    """Valkey URL should default to localhost and convert to redis:// scheme."""

    def test_default_valkey_url(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.VALKEY_URL == "valkey://localhost:6379"

    def test_valkey_redis_url_conversion(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            VALKEY_URL="valkey://myhost:6380",
        )
        assert s.valkey_redis_url == "redis://myhost:6380"

    def test_valkey_redis_url_passthrough(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            VALKEY_URL="redis://myhost:6380",
        )
        assert s.valkey_redis_url == "redis://myhost:6380"


class TestCORSSettings:
    """CORS origin parsing handles JSON and comma-separated formats."""

    def test_default_origins(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        origins = s.cors_origins
        assert "http://localhost:3000" in origins
        assert "http://localhost:5173" in origins

    def test_custom_json_origins(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            CORS_ALLOWED_ORIGINS='["https://app.example.com"]',
        )
        assert s.cors_origins == ["https://app.example.com"]

    def test_comma_separated_fallback(self) -> None:
        s = Settings(
            _env_file=None,
            DEBUG=True,
            CORS_ALLOWED_ORIGINS="https://a.com,https://b.com",
        )
        assert "https://a.com" in s.cors_origins
        assert "https://b.com" in s.cors_origins


class TestHeartbeatDefaults:
    """Heartbeat settings should have sensible defaults."""

    def test_heartbeat_defaults(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.HEARTBEAT_INTERVAL_SECONDS == 90
        assert s.HEARTBEAT_TIMEOUT_SECONDS == 180
        assert s.HEARTBEAT_MAX_RESTARTS == 3
        assert s.HEARTBEAT_MONITOR_POLL_SECONDS == 30


class TestSemanticCacheDefaults:
    """Semantic cache settings should have documented defaults."""

    def test_cache_defaults(self) -> None:
        s = Settings(_env_file=None, DEBUG=True)
        assert s.SEMANTIC_CACHE_SIMILARITY_THRESHOLD == 0.92
        assert s.SEMANTIC_CACHE_TTL_PROPOSAL == 86_400
        assert s.SEMANTIC_CACHE_TTL_CODE == 3_600
        assert s.SEMANTIC_CACHE_TTL_DEFAULT == 21_600
