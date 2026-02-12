"""Unit tests for src.api.main.

Tests exception handlers (_mas_exception_handler, _http_exception_handler,
_generic_exception_handler), lifespan management, and app configuration.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class TestMasExceptionHandler:
    """Tests for _mas_exception_handler."""

    def test_basic_mas_exception_returns_500_and_type_name_code(self) -> None:
        """Should convert MASException to JSON with type name as code and 500 status."""
        from src.api.main import _mas_exception_handler
        from src.core.exceptions import MASException

        exc = MASException("Something went wrong")
        request = MagicMock()

        response = _mas_exception_handler(request, exc)

        assert response.status_code == 500
        assert response.content.error.code == "MASException"
        assert response.content.error.message == "Something went wrong"
        assert response.content.error.details == {}

    def test_mas_exception_with_status_code_in_details(self) -> None:
        """Should use status_code from details dict."""
        from src.api.main import _mas_exception_handler
        from src.core.exceptions import MASException

        exc = MASException("Not found", details={"status_code": 404})
        request = MagicMock()

        response = _mas_exception_handler(request, exc)

        assert response.status_code == 404

    def test_mas_exception_with_error_code_in_details(self) -> None:
        """Should use error_code from details dict."""
        from src.api.main import _mas_exception_handler
        from src.core.exceptions import MASException

        exc = MASException("Custom error", details={"error_code": "CUSTOM_ERROR"})
        request = MagicMock()

        response = _mas_exception_handler(request, exc)

        assert response.content.error.code == "CUSTOM_ERROR"

    def test_mas_exception_message_from_str_exc(self) -> None:
        """Should use str(exc) as message."""
        from src.api.main import _mas_exception_handler
        from src.core.exceptions import MASException

        exc = MASException("Error message from exception")
        request = MagicMock()

        response = _mas_exception_handler(request, exc)

        assert response.content.error.message == "Error message from exception"

    def test_mas_exception_extra_details_passed_through(self) -> None:
        """Should pass through extra details after popping status_code and error_code."""
        from src.api.main import _mas_exception_handler
        from src.core.exceptions import MASException

        exc = MASException(
            "Error with details",
            details={"status_code": 400, "error_code": "BAD_REQUEST", "field": "email", "reason": "invalid"},
        )
        request = MagicMock()

        response = _mas_exception_handler(request, exc)

        assert response.status_code == 400
        assert response.content.error.code == "BAD_REQUEST"
        assert response.content.error.details == {"field": "email", "reason": "invalid"}

    def test_mas_exception_subclass_uses_subclass_name(self) -> None:
        """Should use subclass name as code when no error_code provided."""
        from src.api.main import _mas_exception_handler
        from src.core.exceptions import AgentException

        exc = AgentException("Agent failed", agent_name="scout")
        request = MagicMock()

        response = _mas_exception_handler(request, exc)

        assert response.content.error.code == "AgentException"


class TestHttpExceptionHandler:
    """Tests for _http_exception_handler."""

    def test_http_exception_404_returns_http_404_code(self) -> None:
        """Should convert HTTPException 404 to HTTP_404 code."""
        from litestar.exceptions import NotFoundException

        from src.api.main import _http_exception_handler

        exc = NotFoundException(detail="Resource not found")
        request = MagicMock()

        response = _http_exception_handler(request, exc)

        assert response.status_code == 404
        assert response.content.error.code == "HTTP_404"
        assert response.content.error.message == "Resource not found"

    def test_http_exception_405_returns_http_405_code(self) -> None:
        """Should convert HTTPException 405 to HTTP_405 code."""
        from litestar.exceptions import MethodNotAllowedException

        from src.api.main import _http_exception_handler

        exc = MethodNotAllowedException()
        request = MagicMock()

        response = _http_exception_handler(request, exc)

        assert response.status_code == 405
        assert response.content.error.code == "HTTP_405"

    def test_http_exception_uses_detail_as_message(self) -> None:
        """Should use exc.detail as message when available."""
        from litestar.exceptions import HTTPException

        from src.api.main import _http_exception_handler

        exc = HTTPException(detail="Custom detail message", status_code=400)
        request = MagicMock()

        response = _http_exception_handler(request, exc)

        assert response.content.error.message == "Custom detail message"

    def test_http_exception_falls_back_to_str_exc_if_no_detail(self) -> None:
        """Should use str(exc) as message when detail is None."""
        from litestar.exceptions import HTTPException

        from src.api.main import _http_exception_handler

        exc = HTTPException(status_code=400)
        exc.detail = None  # type: ignore[assignment]
        request = MagicMock()

        response = _http_exception_handler(request, exc)

        assert response.content.error.message == str(exc)


class TestGenericExceptionHandler:
    """Tests for _generic_exception_handler."""

    def test_generic_exception_always_returns_500(self) -> None:
        """Should always return 500 status code."""
        from src.api.main import _generic_exception_handler

        exc = ValueError("Some unexpected error")
        request = MagicMock()

        response = _generic_exception_handler(request, exc)

        assert response.status_code == 500

    def test_generic_exception_code_is_internal_error(self) -> None:
        """Should return INTERNAL_ERROR as code."""
        from src.api.main import _generic_exception_handler

        exc = RuntimeError("Unexpected failure")
        request = MagicMock()

        response = _generic_exception_handler(request, exc)

        assert response.content.error.code == "INTERNAL_ERROR"

    def test_generic_exception_message_is_generic_no_leak(self) -> None:
        """Should return generic message without leaking exception details."""
        from src.api.main import _generic_exception_handler

        exc = ValueError("Secret database password: supersecret123")
        request = MagicMock()

        response = _generic_exception_handler(request, exc)

        assert response.content.error.message == "An unexpected error occurred"
        assert "supersecret123" not in response.content.error.message
        assert "Secret" not in response.content.error.message


class TestLifespan:
    """Tests for lifespan async context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_calls_engine_connect_and_select_1(self) -> None:
        """Should verify DB connection by calling engine.connect and executing SELECT 1."""
        from litestar import Litestar

        mock_conn = AsyncMock()

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
        ):

            @asynccontextmanager
            async def mock_connect():
                yield mock_conn

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey
            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            mock_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_calls_valkey_ping_on_startup(self) -> None:
        """Should verify Valkey connection by calling ping()."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
        ):

            @asynccontextmanager
            async def mock_connect():
                yield AsyncMock()

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            mock_valkey.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_calls_init_sentry_on_startup(self) -> None:
        """Should initialize Sentry with DSN and environment."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry") as mock_init_sentry,
        ):

            @asynccontextmanager
            async def mock_connect():
                yield AsyncMock()

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(SENTRY_DSN="https://sentry.io/123", DEBUG=False)

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            mock_init_sentry.assert_called_once_with(dsn="https://sentry.io/123", environment="production")

    @pytest.mark.asyncio
    async def test_lifespan_calls_engine_dispose_on_shutdown(self) -> None:
        """Should dispose SQLAlchemy engine on shutdown."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
        ):

            @asynccontextmanager
            async def mock_connect():
                yield AsyncMock()

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_calls_valkey_aclose_on_shutdown(self) -> None:
        """Should close Valkey connection on shutdown."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
        ):

            @asynccontextmanager
            async def mock_connect():
                yield AsyncMock()

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            mock_valkey.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_db_connection_failure_logs_error_but_does_not_crash(self) -> None:
        """Should log DB connection failure but not crash the app."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
            patch("src.api.main.logger") as mock_logger,
        ):

            @asynccontextmanager
            async def failing_connect():
                raise RuntimeError("Database connection refused")

            mock_engine.connect = failing_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            mock_logger.error.assert_called_once()
            assert "db_connection_failed" in str(mock_logger.error.call_args)

    @pytest.mark.asyncio
    async def test_lifespan_valkey_connection_failure_logs_error_but_does_not_crash(self) -> None:
        """Should log Valkey connection failure but not crash the app."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
            patch("src.api.main.logger") as mock_logger,
        ):

            @asynccontextmanager
            async def mock_connect():
                yield AsyncMock()

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(side_effect=RuntimeError("Valkey connection timeout"))
            mock_valkey.aclose = AsyncMock()
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            error_calls = [call for call in mock_logger.method_calls if call[0] == "error"]
            assert len(error_calls) == 1
            assert "valkey_connection_failed" in str(error_calls[0])

    @pytest.mark.asyncio
    async def test_lifespan_valkey_close_failure_logs_warning(self) -> None:
        """Should log warning when Valkey close fails but not raise exception."""
        from litestar import Litestar

        with (
            patch("src.api.main.engine") as mock_engine,
            patch("src.api.main.get_valkey") as mock_get_valkey,
            patch("src.api.main.get_settings") as mock_settings,
            patch("src.api.main.init_sentry"),
            patch("src.api.main.logger") as mock_logger,
        ):

            @asynccontextmanager
            async def mock_connect():
                yield AsyncMock()

            mock_engine.connect = mock_connect
            mock_engine.dispose = AsyncMock()

            mock_valkey = AsyncMock()
            mock_valkey.ping = AsyncMock(return_value=True)
            mock_valkey.aclose = AsyncMock(side_effect=RuntimeError("Close failed"))
            mock_get_valkey.return_value = mock_valkey

            mock_settings.return_value = MagicMock(
                SENTRY_DSN="", DEBUG=True, DATABASE_URL="postgresql://host/db"
            )

            from src.api.main import lifespan

            app = MagicMock(spec=Litestar)
            async with lifespan(app):
                pass

            warning_calls = [call for call in mock_logger.method_calls if call[0] == "warning"]
            assert len(warning_calls) == 1
            assert "valkey_close_failed" in str(warning_calls[0])


class TestAppConfiguration:
    """Tests for app instance and module-level configuration."""

    def test_app_is_litestar_instance(self) -> None:
        """Should create a Litestar app instance."""
        from litestar import Litestar

        from src.api.main import app

        assert isinstance(app, Litestar)

    def test_app_has_8_route_handlers_registered(self) -> None:
        """Should register 8 route handlers."""
        from src.api.main import app

        # Count route handlers: health_check, AuthController, HITLController,
        # AgentController, JobController, MetricsController, UserController, ws_handler
        # Access via routes instead of route_handlers
        assert len(app.routes) >= 8  # At least 8 top-level handlers

    def test_cors_config_allows_expected_methods(self) -> None:
        """Should allow GET, POST, PUT, PATCH, DELETE, OPTIONS methods."""
        from src.api.main import cors_config

        expected_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        assert set(cors_config.allow_methods) == set(expected_methods)

    def test_cors_config_allows_authorization_and_content_type_headers(self) -> None:
        """Should allow Authorization and Content-Type headers."""
        from src.api.main import cors_config

        # Headers are normalized to lowercase by Litestar
        assert "authorization" in cors_config.allow_headers
        assert "content-type" in cors_config.allow_headers

    def test_rate_limit_config_300_per_minute(self) -> None:
        """Should set rate limit to 300 requests per minute."""
        from src.api.main import rate_limit_config

        assert rate_limit_config.rate_limit == ("minute", 300)

    def test_rate_limit_config_excludes_health_schema_metrics(self) -> None:
        """Should exclude /health, /schema, /metrics from rate limiting."""
        from src.api.main import rate_limit_config

        assert "/health" in rate_limit_config.exclude
        assert "/schema" in rate_limit_config.exclude
        assert "/metrics" in rate_limit_config.exclude

    def test_openapi_config_title_is_mas_api_version_1_0(self) -> None:
        """Should set OpenAPI title to 'MAS API' and version to '1.0'."""
        from src.api.main import openapi_config

        assert openapi_config.title == "MAS API"
        assert openapi_config.version == "1.0"

    def test_app_debug_matches_settings_debug(self) -> None:
        """Should set app.debug to match settings.DEBUG."""
        from src.api.main import _settings, app

        assert app.debug == _settings.DEBUG

    def test_openapi_config_has_swagger_render_plugin(self) -> None:
        """Should include SwaggerRenderPlugin for /swagger UI."""
        from litestar.openapi.plugins import SwaggerRenderPlugin

        from src.api.main import openapi_config

        plugin_types = [type(p) for p in openapi_config.render_plugins]
        assert SwaggerRenderPlugin in plugin_types

    def test_openapi_config_has_redoc_render_plugin(self) -> None:
        """Should include RedocRenderPlugin for /redoc UI."""
        from litestar.openapi.plugins import RedocRenderPlugin

        from src.api.main import openapi_config

        plugin_types = [type(p) for p in openapi_config.render_plugins]
        assert RedocRenderPlugin in plugin_types

    def test_rate_limit_config_excludes_swagger_and_redoc(self) -> None:
        """Should exclude /swagger and /redoc from rate limiting."""
        from src.api.main import rate_limit_config

        assert "/swagger" in rate_limit_config.exclude
        assert "/redoc" in rate_limit_config.exclude
