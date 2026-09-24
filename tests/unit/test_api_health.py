"""Unit tests for the health check endpoint.

Tests the ``GET /health`` route handler (``src.api.routes.health.health_check``)
with mocked database and Valkey dependencies. We test the handler function
directly rather than using TestClient to avoid needing a full app instance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes.health import health_check
from src.api.schemas import HealthResponseSchema

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    return session


def _create_mock_valkey() -> AsyncMock:
    """Create a mock Valkey/Redis client."""
    valkey = AsyncMock()
    valkey.ping = AsyncMock(return_value=True)
    return valkey


def _create_mock_settings(version: str = "1.0.0-test") -> MagicMock:
    """Create a mock Settings object with only the fields health_check uses."""
    settings = MagicMock()
    settings.APP_VERSION = version
    return settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for the health_check route handler."""

    @pytest.mark.asyncio
    async def test_healthy_when_both_services_up(self) -> None:
        """Should return status='healthy' when both PostgreSQL and Valkey are reachable."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert isinstance(result, HealthResponseSchema)
        assert result.status == "healthy"
        assert result.db_connected is True
        assert result.valkey_connected is True
        assert result.version == "1.0.0-test"
        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo == UTC

        db_session.execute.assert_awaited_once()
        valkey.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unhealthy_when_database_down(self) -> None:
        """Should return status='degraded' when PostgreSQL is unreachable but Valkey is up."""
        db_session = _create_mock_db_session()
        db_session.execute.side_effect = RuntimeError("connection refused")
        valkey = _create_mock_valkey()
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.status == "degraded"
        assert result.db_connected is False
        assert result.valkey_connected is True

    @pytest.mark.asyncio
    async def test_unhealthy_when_valkey_down(self) -> None:
        """Should return status='degraded' when Valkey is unreachable but PostgreSQL is up."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        valkey.ping.side_effect = Exception("connection timeout")
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.status == "degraded"
        assert result.db_connected is True
        assert result.valkey_connected is False

    @pytest.mark.asyncio
    async def test_unhealthy_when_both_services_down(self) -> None:
        """Should return status='unhealthy' when both PostgreSQL and Valkey are unreachable."""
        db_session = _create_mock_db_session()
        db_session.execute.side_effect = RuntimeError("connection refused")
        valkey = _create_mock_valkey()
        valkey.ping.side_effect = Exception("connection timeout")
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.status == "unhealthy"
        assert result.db_connected is False
        assert result.valkey_connected is False

    @pytest.mark.asyncio
    async def test_response_includes_all_required_fields(self) -> None:
        """Should return a response with all expected fields populated."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert isinstance(result.status, str)
        assert isinstance(result.db_connected, bool)
        assert isinstance(result.valkey_connected, bool)
        assert isinstance(result.version, str)
        assert isinstance(result.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_valkey_returns_false_ping(self) -> None:
        """Should handle Valkey returning False from ping()."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        valkey.ping.return_value = False
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.status == "degraded"
        assert result.db_connected is True
        assert result.valkey_connected is False

    @pytest.mark.asyncio
    async def test_handles_generic_database_exception(self) -> None:
        """Should catch any Exception from database, not just OperationalError."""
        db_session = _create_mock_db_session()
        db_session.execute.side_effect = RuntimeError("unexpected database error")
        valkey = _create_mock_valkey()
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.status == "degraded"
        assert result.db_connected is False
        assert result.valkey_connected is True

    @pytest.mark.asyncio
    async def test_handles_generic_valkey_exception(self) -> None:
        """Should catch any Exception from Valkey, not just connection errors."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        valkey.ping.side_effect = ValueError("unexpected valkey error")
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.status == "degraded"
        assert result.db_connected is True
        assert result.valkey_connected is False

    @pytest.mark.asyncio
    async def test_version_from_settings(self) -> None:
        """Should use the APP_VERSION from settings in the response."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        settings = _create_mock_settings("2.5.1-beta")

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.version == "2.5.1-beta"

    @pytest.mark.asyncio
    async def test_timestamp_is_utc(self) -> None:
        """Should return a timestamp in UTC timezone."""
        db_session = _create_mock_db_session()
        valkey = _create_mock_valkey()
        settings = _create_mock_settings()

        result = await health_check.fn(db_session=db_session, valkey=valkey, settings=settings)

        assert result.timestamp.tzinfo == UTC
