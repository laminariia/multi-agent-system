"""Prometheus metrics scrape endpoint.

Exposes ``GET /metrics`` in the standard Prometheus exposition format.
This endpoint is excluded from JWT authentication so that Prometheus can
scrape it without credentials.
"""
from __future__ import annotations

from litestar import Controller, Response, get
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


class MetricsController(Controller):
    """Prometheus metrics endpoint."""

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
