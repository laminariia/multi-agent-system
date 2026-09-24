"""Prometheus metrics scrape endpoint.

Exposes ``GET /metrics`` and ``GET /api/v1/metrics`` in the standard
Prometheus exposition format.  Both endpoints are excluded from JWT
authentication so that Prometheus can scrape without credentials.
"""

from __future__ import annotations

from litestar import Controller, Response, get
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


class MetricsController(Controller):
    """Prometheus metrics endpoint at ``/metrics``."""

    path = "/metrics"
    tags = ["monitoring"]

    @get(
        path="/",
        opt={"exclude_from_auth": True},
        summary="Prometheus metrics scrape endpoint",
    )
    async def get_metrics(self) -> Response:
        """Return Prometheus metrics in exposition format."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )


class ApiV1MetricsController(Controller):
    """Prometheus metrics under the versioned API prefix.

    Mirrors ``/metrics`` at ``/api/v1/metrics`` so that internal
    services using the ``/api/v1/`` prefix can discover metrics
    without special-casing the path.
    """

    path = "/api/v1/metrics"
    tags = ["monitoring"]

    @get(
        path="/",
        opt={"exclude_from_auth": True},
        summary="Prometheus metrics (API v1)",
    )
    async def api_v1_metrics(self) -> Response:
        """Return Prometheus metrics in exposition format."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )
