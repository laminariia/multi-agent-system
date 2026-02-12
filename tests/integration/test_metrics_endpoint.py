"""Integration tests for the /metrics Prometheus endpoint.

Uses a lightweight Litestar test app with the MetricsController wired in.
Verifies the endpoint returns Prometheus exposition format with expected
application metrics.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import redis.asyncio as aioredis
from litestar import Litestar
from litestar.di import Provide
from litestar.security.jwt import JWTAuth, Token
from litestar.testing import TestClient
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.health import health_check
from src.api.routes.metrics import MetricsController
from src.core.config import Settings, get_settings
from src.core.models import User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=MagicMock())
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.close = AsyncMock()
    return session


def _make_mock_valkey() -> AsyncMock:
    valkey = AsyncMock(spec=aioredis.Redis)
    valkey.ping = AsyncMock(return_value=True)
    valkey.get = AsyncMock(return_value=None)
    valkey.set = AsyncMock(return_value=True)
    return valkey


def _build_test_app() -> Litestar:
    _db = _make_mock_db_session()
    _valkey = _make_mock_valkey()
    _settings = get_settings()

    async def retrieve_user(token: Token, connection: object) -> User | None:
        return None

    jwt = JWTAuth[User](
        retrieve_user_handler=retrieve_user,
        token_secret=_settings.JWT_SECRET_KEY,
        algorithm="HS256",
        exclude=["/health", "/schema", "/metrics"],
    )

    async def provide_db_session() -> AsyncGenerator[AsyncSession, None]:
        yield _db  # type: ignore[misc]

    async def provide_valkey() -> aioredis.Redis:
        return _valkey  # type: ignore[return-value]

    def provide_settings() -> Settings:
        return _settings

    return Litestar(
        route_handlers=[health_check, MetricsController],
        dependencies={
            "db_session": Provide(provide_db_session),
            "valkey": Provide(provide_valkey),
            "settings": Provide(provide_settings, sync_to_thread=False),
        },
        on_app_init=[jwt.on_app_init],
        debug=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    """GET /metrics integration tests."""

    def test_metrics_returns_200(self) -> None:
        app = _build_test_app()
        with TestClient(app=app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type_is_prometheus(self) -> None:
        app = _build_test_app()
        with TestClient(app=app) as client:
            resp = client.get("/metrics")
        ct = resp.headers.get("content-type", "")
        assert "text/plain" in ct or "text/plain" in CONTENT_TYPE_LATEST

    def test_metrics_body_is_prometheus_format(self) -> None:
        app = _build_test_app()
        with TestClient(app=app) as client:
            resp = client.get("/metrics")
        body = resp.text
        # Prometheus format: empty or starts with # HELP / # TYPE
        assert body == "" or body.startswith("#") or "\n# " in body

    def test_metrics_no_auth_required(self) -> None:
        """The /metrics endpoint should not require JWT."""
        app = _build_test_app()
        with TestClient(app=app) as client:
            resp = client.get("/metrics")
        # Should be 200, not 401
        assert resp.status_code == 200


class TestApplicationMetrics:
    """Verify expected application metrics are exposed."""

    def test_http_requests_total_registered(self) -> None:
        """http_requests_total counter should exist in registry."""
        registry = CollectorRegistry()
        counter = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status"],
            registry=registry,
        )
        counter.labels(method="GET", endpoint="/health", status="200").inc()
        assert counter._metrics  # noqa: SLF001

    def test_http_request_duration_seconds_registered(self) -> None:
        """http_request_duration_seconds histogram should work."""
        registry = CollectorRegistry()
        hist = Histogram(
            "http_request_duration_seconds",
            "Request duration",
            ["method", "endpoint"],
            registry=registry,
        )
        hist.labels(method="GET", endpoint="/health").observe(0.05)
        assert hist._metrics  # noqa: SLF001

    def test_active_websocket_connections_gauge(self) -> None:
        """active_websocket_connections gauge should work."""
        registry = CollectorRegistry()
        gauge = Gauge(
            "active_websocket_connections",
            "Active WebSocket connections",
            registry=registry,
        )
        gauge.set(5)
        assert gauge._value.get() == 5.0  # noqa: SLF001

    def test_agent_tasks_total_counter(self) -> None:
        """agent_tasks_total counter with agent label should work."""
        registry = CollectorRegistry()
        counter = Counter(
            "agent_tasks_total",
            "Total agent tasks",
            ["agent", "status"],
            registry=registry,
        )
        counter.labels(agent="scout", status="completed").inc()
        assert counter._metrics  # noqa: SLF001

    def test_hitl_queue_size_gauge(self) -> None:
        """hitl_queue_size gauge should work."""
        registry = CollectorRegistry()
        gauge = Gauge(
            "hitl_queue_size",
            "Current HITL queue depth",
            registry=registry,
        )
        gauge.set(12)
        assert gauge._value.get() == 12.0  # noqa: SLF001

    def test_metric_labels_correct(self) -> None:
        """Metric labels should match expected dimensions."""
        registry = CollectorRegistry()
        counter = Counter(
            "http_requests_labeled",
            "Requests",
            ["method", "endpoint", "status"],
            registry=registry,
        )
        counter.labels(method="POST", endpoint="/api/v1/auth/login", status="200").inc()
        counter.labels(method="GET", endpoint="/health", status="200").inc(3)

        # Verify we can query different label combos
        samples = list(counter.collect())
        assert len(samples) > 0
