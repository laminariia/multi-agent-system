"""Unit tests for src/core/config.py — Settings validation and computed fields.

Tests default field values, computed properties (async_database_url, valkey_redis_url,
cors_origins), and the get_settings() singleton cache.
"""

from __future__ import annotations

import pytest

from src.core.config import Settings, get_settings

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class TestSettings:
    """Test Settings class field defaults and computed properties."""

    def test_default_database_url(self):
        """Settings uses default PostgreSQL URL."""
        settings = Settings(_env_file=None)
        assert settings.DATABASE_URL == "postgresql://mas:mas_password@localhost:5432/mas"

    def test_default_valkey_url(self):
        """Settings uses default Valkey URL."""
        settings = Settings(_env_file=None)
        assert settings.VALKEY_URL == "valkey://localhost:6379"

    def test_default_jwt_secret_key(self):
        """Settings has default JWT secret key."""
        settings = Settings(_env_file=None)
        assert settings.JWT_SECRET_KEY == "change-me-in-production"  # noqa: S105

    def test_default_app_version(self):
        """Settings has default app version."""
        settings = Settings(_env_file=None)
        assert settings.APP_VERSION == "1.0.0"

    def test_default_debug_false(self):
        """Settings defaults to debug=False."""
        settings = Settings(_env_file=None)
        assert settings.DEBUG is False

    def test_default_log_level_info(self):
        """Settings defaults to LOG_LEVEL=INFO."""
        settings = Settings(_env_file=None)
        assert settings.LOG_LEVEL == "INFO"

    def test_default_llm_api_keys_empty(self):
        """LLM API keys default to empty strings."""
        settings = Settings(_env_file=None)
        assert settings.OPENROUTER_API_KEY == ""
        assert settings.GEMINI_API_KEY == ""
        assert settings.ANTHROPIC_API_KEY == ""
        assert settings.OPENAI_API_KEY == ""

    def test_default_optional_keys_none(self):
        """Optional API keys default to None."""
        settings = Settings(_env_file=None)
        assert settings.E2B_API_KEY is None
        assert settings.FREELANCER_CLIENT_ID is None
        assert settings.FREELANCER_CLIENT_SECRET is None
        assert settings.HUNTER_API_KEY is None
        assert settings.APOLLO_API_KEY is None

    def test_default_brightdata_host(self):
        """BrightData host has default value."""
        settings = Settings(_env_file=None)
        assert settings.BRIGHTDATA_HOST == "brd.superproxy.io"

    def test_default_browser_pool_settings(self):
        """Browser pool settings have defaults."""
        settings = Settings(_env_file=None)
        assert settings.BROWSER_POOL_MAX == 3
        assert settings.BROWSER_PROXY_ROTATION_MINUTES == 45
        assert settings.BROWSER_HEADLESS is False

    def test_default_jwt_token_expiry(self):
        """JWT token expiry settings have defaults."""
        settings = Settings(_env_file=None)
        assert settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES == 15
        assert settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS == 7

    def test_default_cors_allowed_origins_string(self):
        """CORS_ALLOWED_ORIGINS is stored as JSON string."""
        settings = Settings(_env_file=None)
        assert settings.CORS_ALLOWED_ORIGINS == '["http://localhost:3000","http://localhost:5173"]'


class TestAsyncDatabaseUrl:
    """Test async_database_url computed property."""

    def test_converts_postgresql_to_asyncpg(self):
        """async_database_url converts postgresql:// to postgresql+asyncpg://."""
        settings = Settings(
            _env_file=None,
            DATABASE_URL="postgresql://user:pass@host:5432/db"
        )
        assert settings.async_database_url == "postgresql+asyncpg://user:pass@host:5432/db"

    def test_preserves_asyncpg_scheme(self):
        """async_database_url leaves postgresql+asyncpg:// unchanged."""
        settings = Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db"
        )
        assert settings.async_database_url == "postgresql+asyncpg://user:pass@host:5432/db"

    def test_raises_on_unsupported_scheme(self):
        """async_database_url raises ValueError for unsupported schemes."""
        settings = Settings(
            _env_file=None,
            DATABASE_URL="mysql://user:pass@host:3306/db"
        )
        with pytest.raises(ValueError, match="Unsupported DATABASE_URL scheme"):
            _ = settings.async_database_url


class TestValkeyRedisUrl:
    """Test valkey_redis_url computed property."""

    def test_converts_valkey_to_redis(self):
        """valkey_redis_url converts valkey:// to redis://."""
        settings = Settings(
            _env_file=None,
            VALKEY_URL="valkey://localhost:6379/0"
        )
        assert settings.valkey_redis_url == "redis://localhost:6379/0"

    def test_preserves_redis_scheme(self):
        """valkey_redis_url leaves redis:// unchanged."""
        settings = Settings(
            _env_file=None,
            VALKEY_URL="redis://localhost:6379/0"
        )
        assert settings.valkey_redis_url == "redis://localhost:6379/0"

    def test_converts_valkey_with_auth(self):
        """valkey_redis_url converts valkey:// with auth credentials."""
        settings = Settings(
            _env_file=None,
            VALKEY_URL="valkey://user:pass@host:6379/1"
        )
        assert settings.valkey_redis_url == "redis://user:pass@host:6379/1"


class TestCorsOrigins:
    """Test cors_origins computed property parsing."""

    def test_parses_json_array_format(self):
        """cors_origins parses valid JSON array."""
        settings = Settings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS='["https://example.com","https://test.com"]'
        )
        assert settings.cors_origins == ["https://example.com", "https://test.com"]

    def test_parses_bracket_format_without_quotes(self):
        """cors_origins parses [url1,url2] without JSON quotes."""
        settings = Settings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS="[https://example.com,https://test.com]"
        )
        assert settings.cors_origins == ["https://example.com", "https://test.com"]

    def test_parses_comma_separated_format(self):
        """cors_origins parses comma-separated URLs."""
        settings = Settings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS="https://example.com,https://test.com"
        )
        assert settings.cors_origins == ["https://example.com", "https://test.com"]

    def test_parses_single_origin(self):
        """cors_origins parses single origin without commas."""
        settings = Settings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS="https://example.com"
        )
        assert settings.cors_origins == ["https://example.com"]

    def test_strips_whitespace_from_origins(self):
        """cors_origins strips whitespace from each origin."""
        settings = Settings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS=" https://example.com , https://test.com "
        )
        assert settings.cors_origins == ["https://example.com", "https://test.com"]

    def test_ignores_empty_strings_in_split(self):
        """cors_origins filters out empty strings from split."""
        settings = Settings(
            _env_file=None,
            CORS_ALLOWED_ORIGINS="https://example.com,,https://test.com"
        )
        assert settings.cors_origins == ["https://example.com", "https://test.com"]


class TestGetSettings:
    """Test get_settings() singleton function."""

    def test_returns_settings_instance(self):
        """get_settings returns a Settings instance."""
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_singleton_cached(self):
        """get_settings returns the same cached instance."""
        get_settings.cache_clear()
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_cache_clear_creates_new_instance(self):
        """get_settings.cache_clear() creates a new instance."""
        get_settings.cache_clear()
        settings1 = get_settings()
        get_settings.cache_clear()
        settings2 = get_settings()
        assert settings1 is not settings2


class TestSettingsWithEnvVars:
    """Test Settings with environment variable overrides."""

    def test_env_var_overrides_database_url(self, monkeypatch):
        """Environment variable overrides DATABASE_URL default."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://custom:pass@host:5432/db")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.DATABASE_URL == "postgresql://custom:pass@host:5432/db"

    def test_env_var_overrides_jwt_secret(self, monkeypatch):
        """Environment variable overrides JWT_SECRET_KEY default."""
        monkeypatch.setenv("JWT_SECRET_KEY", "production-secret-key-123")  # noqa: S105
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.JWT_SECRET_KEY == "production-secret-key-123"  # noqa: S105

    def test_env_var_sets_optional_key(self, monkeypatch):
        """Environment variable sets optional API key."""
        monkeypatch.setenv("E2B_API_KEY", "e2b-test-key-123")  # noqa: S105
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.E2B_API_KEY == "e2b-test-key-123"  # noqa: S105

    def test_env_var_overrides_debug_flag(self, monkeypatch):
        """Environment variable overrides DEBUG flag."""
        monkeypatch.setenv("DEBUG", "true")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.DEBUG is True
