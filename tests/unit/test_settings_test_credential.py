"""Unit tests for the Settings credential test endpoint.

Tests the ``SettingsController.test_credential`` route handler
(``POST /api/v1/settings/test-credential``) with mocked database and
HTTP dependencies.  We test handler functions directly via ``.fn()``
to avoid needing a full app instance.

The ``test_credential`` method does a lazy import of ``get_api_key``
from ``src.core.credential_loader``, so all patches target that module.
The ``_test_key`` static helper uses ``httpx.AsyncClient`` as a context
manager, which is patched via ``httpx.AsyncClient`` in the ``settings``
module namespace.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.api.routes.settings import SettingsController
from src.api.schemas import CredentialTestRequestSchema, CredentialTestResponseSchema

# Patch target for the lazy import inside test_credential().
_GET_API_KEY = "src.core.credential_loader.get_api_key"

# Patch target for httpx.AsyncClient used in _test_key().
_HTTPX_CLIENT = "src.api.routes.settings.httpx.AsyncClient"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_mock_request(user_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Request with an authenticated user and auth token."""
    if user_id is None:
        user_id = uuid.uuid4()

    mock_request = MagicMock()
    mock_request.user = MagicMock()
    mock_request.user.id = user_id
    mock_request.auth = MagicMock()
    mock_request.auth.sub = str(user_id)
    return mock_request


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    return AsyncMock()


def _make_httpx_client(status_code: int = 200, side_effect: Exception | None = None) -> AsyncMock:
    """Create a mock httpx.AsyncClient context manager.

    If *side_effect* is given, ``client.get()`` will raise that exception
    instead of returning a response.
    """
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    if side_effect is not None:
        mock_client.get = AsyncMock(side_effect=side_effect)
    else:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_controller_self() -> MagicMock:
    """Return a mock usable as ``self`` in ``.fn()`` calls.

    The ``test_credential`` method calls ``self._test_key(...)`` which is a
    ``@staticmethod``.  We attach the real static method to the mock so
    it executes the actual logic rather than returning a mock.
    """
    ctrl = MagicMock()
    ctrl._test_key = SettingsController._test_key
    return ctrl


class TestTestCredentialNotConfigured:
    """Tests when the requested key is not configured."""

    @pytest.mark.asyncio
    async def test_test_credential_not_configured(self) -> None:
        """Should return success=False when key is not found."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="gemini_api_key")

        with patch(_GET_API_KEY, new_callable=AsyncMock, return_value=None):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert isinstance(result, CredentialTestResponseSchema)
        assert result.success is False
        assert "not configured" in result.message.lower()
        assert result.latency_ms is None


class TestTestGeminiKey:
    """Tests for Gemini API key validation."""

    @pytest.mark.asyncio
    async def test_test_gemini_key_valid(self) -> None:
        """Should return success=True when Gemini key returns HTTP 200."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="gemini_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="test-gemini-key"),
            patch(_HTTPX_CLIENT, return_value=_make_httpx_client(200)),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is True
        assert result.latency_ms is not None
        assert "valid" in result.message.lower()

    @pytest.mark.asyncio
    async def test_test_gemini_key_invalid(self) -> None:
        """Should return success=False when Gemini key returns non-200."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="gemini_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="bad-gemini-key"),
            patch(_HTTPX_CLIENT, return_value=_make_httpx_client(403)),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is False
        assert "403" in result.message


class TestTestAnthropicKey:
    """Tests for Anthropic API key validation."""

    @pytest.mark.asyncio
    async def test_test_anthropic_key_valid(self) -> None:
        """Should return success=True when Anthropic key returns HTTP 200."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="anthropic_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="test-anthropic-key"),
            patch(_HTTPX_CLIENT, return_value=_make_httpx_client(200)),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is True
        assert result.latency_ms is not None
        assert "valid" in result.message.lower()


class TestTestOpenAIKey:
    """Tests for OpenAI API key validation."""

    @pytest.mark.asyncio
    async def test_test_openai_key_valid(self) -> None:
        """Should return success=True when OpenAI key returns HTTP 200."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="openai_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="test-openai-key"),
            patch(_HTTPX_CLIENT, return_value=_make_httpx_client(200)),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is True
        assert result.latency_ms is not None


class TestTestHunterKey:
    """Tests for Hunter.io API key validation."""

    @pytest.mark.asyncio
    async def test_test_hunter_key_valid(self) -> None:
        """Should return success=True when Hunter key returns HTTP 200."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="hunter_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="test-hunter-key"),
            patch(_HTTPX_CLIENT, return_value=_make_httpx_client(200)),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is True
        assert result.latency_ms is not None


class TestTestE2BKey:
    """Tests for E2B API key (presence-only check)."""

    @pytest.mark.asyncio
    async def test_test_e2b_key_configured(self) -> None:
        """Should return success=True with presence message when E2B key exists."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="e2b_api_key")

        with patch(_GET_API_KEY, new_callable=AsyncMock, return_value="some-e2b-key"):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is True
        assert "configured" in result.message.lower()
        assert result.latency_ms is None


class TestTestUnknownKey:
    """Tests for keys with no automated test endpoint."""

    @pytest.mark.asyncio
    async def test_test_unknown_key(self) -> None:
        """Should return success=True with no-automated-test message."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="brightdata_username")

        with patch(_GET_API_KEY, new_callable=AsyncMock, return_value="some-value"):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is True
        assert "no automated test" in result.message.lower()
        assert result.latency_ms is None


class TestTestCredentialNetworkErrors:
    """Tests for network-level failures during credential testing."""

    @pytest.mark.asyncio
    async def test_test_credential_network_error(self) -> None:
        """Should return success=False when httpx raises ConnectError."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="gemini_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="test-key"),
            patch(
                _HTTPX_CLIENT,
                return_value=_make_httpx_client(side_effect=httpx.ConnectError("Connection refused")),
            ),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is False
        assert "failed" in result.message.lower() or "Connection refused" in result.message

    @pytest.mark.asyncio
    async def test_test_credential_timeout(self) -> None:
        """Should return success=False when httpx raises TimeoutException."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        ctrl = _make_controller_self()

        data = CredentialTestRequestSchema(key_name="anthropic_api_key")

        with (
            patch(_GET_API_KEY, new_callable=AsyncMock, return_value="test-key"),
            patch(
                _HTTPX_CLIENT,
                return_value=_make_httpx_client(side_effect=httpx.TimeoutException("Timed out")),
            ),
        ):
            result = await SettingsController.test_credential.fn(
                self=ctrl,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result.success is False
        assert "timed out" in result.message.lower()
        assert result.latency_ms is None
