"""Unit tests for src.api.routes.metrics.

Tests the MetricsController which exposes Prometheus metrics at GET /metrics.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class TestMetricsController:
    """Tests for MetricsController configuration."""

    def test_controller_path_is_metrics(self) -> None:
        """Should set controller path to /metrics."""
        from src.api.routes.metrics import MetricsController

        assert MetricsController.path == "/metrics"

    def test_controller_tags_is_monitoring(self) -> None:
        """Should tag the controller with 'monitoring'."""
        from src.api.routes.metrics import MetricsController

        assert MetricsController.tags == ["monitoring"]


class TestGetMetrics:
    """Tests for get_metrics handler."""

    @pytest.mark.asyncio
    async def test_get_metrics_returns_response(self) -> None:
        """Should return a Response object."""
        from litestar import Response

        from src.api.routes.metrics import MetricsController

        controller = MetricsController(owner=MagicMock())
        result = await MetricsController.get_metrics.fn(controller)

        assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_get_metrics_content_is_prometheus_metrics_bytes(self) -> None:
        """Should return Prometheus metrics as bytes in content."""
        from src.api.routes.metrics import MetricsController

        controller = MetricsController(owner=MagicMock())
        result = await MetricsController.get_metrics.fn(controller)

        assert isinstance(result.content, bytes)
        # Prometheus metrics format starts with # HELP or # TYPE comments
        assert result.content.startswith(b"#") or result.content == b""

    @pytest.mark.asyncio
    async def test_get_metrics_media_type_is_content_type_latest(self) -> None:
        """Should set media_type to prometheus CONTENT_TYPE_LATEST."""
        from prometheus_client import CONTENT_TYPE_LATEST

        from src.api.routes.metrics import MetricsController

        controller = MetricsController(owner=MagicMock())
        result = await MetricsController.get_metrics.fn(controller)

        assert result.media_type == CONTENT_TYPE_LATEST

    @pytest.mark.asyncio
    async def test_get_metrics_handler_is_excluded_from_auth(self) -> None:
        """Should have exclude_from_auth=True in handler opts."""
        from src.api.routes.metrics import MetricsController

        handler = MetricsController.get_metrics
        assert handler.opt.get("exclude_from_auth") is True

    @pytest.mark.asyncio
    async def test_get_metrics_calls_generate_latest(self) -> None:
        """Should call prometheus_client.generate_latest()."""
        with patch("src.api.routes.metrics.generate_latest") as mock_generate:
            mock_generate.return_value = b"# Mock metrics\n"

            from src.api.routes.metrics import MetricsController

            controller = MetricsController(owner=MagicMock())
            result = await MetricsController.get_metrics.fn(controller)

            mock_generate.assert_called_once()
            assert result.content == b"# Mock metrics\n"

    @pytest.mark.asyncio
    async def test_get_metrics_works_with_empty_metrics_registry(self) -> None:
        """Should work when metrics registry is empty."""
        with patch("src.api.routes.metrics.generate_latest") as mock_generate:
            mock_generate.return_value = b""

            from src.api.routes.metrics import MetricsController

            controller = MetricsController(owner=MagicMock())
            result = await MetricsController.get_metrics.fn(controller)

            assert result.content == b""
            assert isinstance(result.content, bytes)

    @pytest.mark.asyncio
    async def test_get_metrics_returns_valid_prometheus_format(self) -> None:
        """Should return content in valid Prometheus exposition format."""
        from src.api.routes.metrics import MetricsController

        controller = MetricsController(owner=MagicMock())
        result = await MetricsController.get_metrics.fn(controller)

        # Valid Prometheus metrics either start with # (comments) or are empty
        # Empty is valid when no metrics registered
        assert result.content == b"" or result.content.startswith(b"#")
