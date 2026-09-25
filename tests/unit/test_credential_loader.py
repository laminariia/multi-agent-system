"""Unit tests for the credential loader module.

Tests ``src.core.credential_loader``:
- :func:`load_runtime_credentials` -- merge env + DB keys
- :func:`get_api_key` -- single key lookup with fallback
- :func:`load_platform_credentials` -- platform account decryption

All database and encryption operations are mocked so tests never touch real
services.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.credential_loader import (
    get_api_key,
    load_platform_credentials,
    load_runtime_credentials,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TEST_USER_ID = str(uuid.uuid4())


def _make_settings(**overrides: str) -> MagicMock:
    """Create a mock Settings object with sensible API key defaults.

    By default every known key is empty (``""``).  Pass keyword arguments to
    set specific keys, e.g. ``_make_settings(GEMINI_API_KEY="gk-test")``.
    """
    defaults = {
        "GEMINI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "E2B_API_KEY": "",
        "HUNTER_API_KEY": "",
        "APOLLO_API_KEY": "",
        "LANGSMITH_API_KEY": "",
    }
    defaults.update(overrides)

    settings = MagicMock()
    for attr, value in defaults.items():
        setattr(settings, attr, value)
    return settings


def _mock_db_session_ctx(account: MagicMock | None = None):
    """Return an ``asynccontextmanager`` that yields a mock session.

    The session's ``execute()`` returns a result whose
    ``scalar_one_or_none()`` returns *account*.
    """
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = account
    mock_session.execute.return_value = mock_result

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx


def _make_platform_account(
    credentials: dict | None = None,
    platform: str = "freelancer",
) -> MagicMock:
    """Create a mock PlatformAccount ORM instance."""
    account = MagicMock()
    account.platform = platform
    account.credentials = credentials or {"_encrypted": "gAAAAABk_fake"}
    return account


# ---------------------------------------------------------------------------
# Tests: load_runtime_credentials
# ---------------------------------------------------------------------------


class TestLoadRuntimeCredentials:
    """Tests for :func:`load_runtime_credentials`."""

    @patch("src.core.config.get_settings")
    async def test_load_runtime_credentials_env_only(
        self,
        mock_get_settings: MagicMock,
    ) -> None:
        """Should return env vars from Settings when no user_id is given."""
        mock_get_settings.return_value = _make_settings(
            GEMINI_API_KEY="gk-env-123",
            ANTHROPIC_API_KEY="ak-env-456",
        )

        result = await load_runtime_credentials()

        assert result["gemini_api_key"] == "gk-env-123"
        assert result["anthropic_api_key"] == "ak-env-456"
        # Keys with empty values should be excluded
        assert "openai_api_key" not in result

    @patch("src.core.credential_loader._fetch_api_keys_from_db", new_callable=AsyncMock)
    @patch("src.core.config.get_settings")
    async def test_load_runtime_credentials_db_overrides_env(
        self,
        mock_get_settings: MagicMock,
        mock_fetch_db: AsyncMock,
    ) -> None:
        """DB-stored key should override the env key for the same name."""
        mock_get_settings.return_value = _make_settings(
            GEMINI_API_KEY="gk-env-original",
            ANTHROPIC_API_KEY="ak-env-keep",
        )
        mock_fetch_db.return_value = {
            "gemini_api_key": "gk-db-override",
        }

        result = await load_runtime_credentials(user_id=_TEST_USER_ID)

        # DB value wins
        assert result["gemini_api_key"] == "gk-db-override"
        # Env value kept when no DB override
        assert result["anthropic_api_key"] == "ak-env-keep"

    @patch("src.core.credential_loader._fetch_api_keys_from_db", new_callable=AsyncMock)
    @patch("src.core.config.get_settings")
    async def test_load_runtime_credentials_empty_values_excluded(
        self,
        mock_get_settings: MagicMock,
        mock_fetch_db: AsyncMock,
    ) -> None:
        """Empty and None values should not appear in the result dict."""
        mock_get_settings.return_value = _make_settings(
            GEMINI_API_KEY="gk-env",
            ANTHROPIC_API_KEY="",
        )
        mock_fetch_db.return_value = {
            "openai_api_key": "",
            "hunter_api_key": None,
        }

        result = await load_runtime_credentials(user_id=_TEST_USER_ID)

        assert "gemini_api_key" in result
        assert "anthropic_api_key" not in result
        assert "openai_api_key" not in result
        assert "hunter_api_key" not in result


# ---------------------------------------------------------------------------
# Tests: get_api_key
# ---------------------------------------------------------------------------


class TestGetApiKey:
    """Tests for :func:`get_api_key`."""

    @patch("src.core.config.get_settings")
    async def test_get_api_key_from_env(
        self,
        mock_get_settings: MagicMock,
    ) -> None:
        """Should return the env value when no user_id is given."""
        mock_get_settings.return_value = _make_settings(
            GEMINI_API_KEY="gk-env-value",
        )

        result = await get_api_key("gemini_api_key")

        assert result == "gk-env-value"

    @patch("src.core.config.get_settings")
    @patch("src.core.credential_loader._fetch_api_keys_from_db", new_callable=AsyncMock)
    async def test_get_api_key_from_db(
        self,
        mock_fetch_db: AsyncMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Should return the DB value when user_id is given and DB has the key."""
        mock_fetch_db.return_value = {
            "gemini_api_key": "gk-db-value",
        }
        # Env also has a value -- DB should win
        mock_get_settings.return_value = _make_settings(
            GEMINI_API_KEY="gk-env-fallback",
        )

        result = await get_api_key("gemini_api_key", user_id=_TEST_USER_ID)

        assert result == "gk-db-value"

    @patch("src.core.config.get_settings")
    async def test_get_api_key_not_configured(
        self,
        mock_get_settings: MagicMock,
    ) -> None:
        """Should return None when the key is in neither env nor DB."""
        mock_get_settings.return_value = _make_settings()  # all empty

        result = await get_api_key("gemini_api_key")

        assert result is None

    @patch("src.core.config.get_settings")
    @patch("src.core.credential_loader._fetch_api_keys_from_db", new_callable=AsyncMock)
    async def test_get_api_key_db_fails_falls_back_to_env(
        self,
        mock_fetch_db: AsyncMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Should fall back to env value when the DB lookup returns None (failure)."""
        mock_fetch_db.return_value = None  # simulates DB failure / no row

        mock_get_settings.return_value = _make_settings(
            ANTHROPIC_API_KEY="ak-env-fallback",
        )

        result = await get_api_key("anthropic_api_key", user_id=_TEST_USER_ID)

        assert result == "ak-env-fallback"


# ---------------------------------------------------------------------------
# Tests: load_platform_credentials
# ---------------------------------------------------------------------------


class TestLoadPlatformCredentials:
    """Tests for :func:`load_platform_credentials`."""

    @patch("src.security.encryption.decrypt_credentials")
    @patch("src.core.database.get_db_session")
    async def test_load_platform_credentials_found(
        self,
        mock_get_db_session: MagicMock,
        mock_decrypt: MagicMock,
    ) -> None:
        """Should return decrypted credentials when a platform account exists."""
        account = _make_platform_account(
            credentials={"_encrypted": "gAAAAABk_test"},
            platform="freelancer",
        )
        mock_get_db_session.return_value = _mock_db_session_ctx(account)()

        decrypted = {
            "client_id": "fc-123",
            "api_endpoint": "https://freelancer.com/api",
        }
        mock_decrypt.return_value = decrypted

        result = await load_platform_credentials("freelancer", user_id=_TEST_USER_ID)

        assert result is not None
        assert result["client_id"] == "fc-123"
        assert result["api_endpoint"] == "https://freelancer.com/api"
        mock_decrypt.assert_called_once_with(account.credentials)

    @patch("src.security.encryption.decrypt_credentials")
    @patch("src.core.database.get_db_session")
    async def test_load_platform_credentials_not_found(
        self,
        mock_get_db_session: MagicMock,
        mock_decrypt: MagicMock,
    ) -> None:
        """Should return None when no matching platform account exists."""
        mock_get_db_session.return_value = _mock_db_session_ctx(None)()

        result = await load_platform_credentials("upwork", user_id=_TEST_USER_ID)

        assert result is None
        mock_decrypt.assert_not_called()

    @patch("src.security.encryption.decrypt_credentials")
    @patch("src.core.database.get_db_session")
    async def test_load_platform_credentials_decrypt_error(
        self,
        mock_get_db_session: MagicMock,
        mock_decrypt: MagicMock,
    ) -> None:
        """Should return None when decryption raises an exception."""
        account = _make_platform_account(
            credentials={"_encrypted": "corrupted"},
            platform="freelancer",
        )
        mock_get_db_session.return_value = _mock_db_session_ctx(account)()

        mock_decrypt.side_effect = ValueError("Invalid Fernet data")

        result = await load_platform_credentials("freelancer", user_id=_TEST_USER_ID)

        assert result is None
