"""Unit tests for src.monitoring.metrics and src.monitoring.sentry_config.

Prometheus registry is isolated between tests; sentry_sdk.init is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from src.monitoring.sentry_config import init_sentry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_metrics_singleton():
    """Reset the MASMetrics singleton between tests.

    Also unregister MAS collectors from the default registry so fresh
    MASMetrics instances can re-register them without ``ValueError``.
    """
    import src.monitoring.metrics as mod

    mod._metrics = None

    # Collect names of MAS-specific collectors to remove from the default registry.
    to_remove = []
    for collector in list(REGISTRY._names_to_collectors.values()):
        desc = getattr(collector, "_name", "") or ""
        if desc.startswith("mas_"):
            to_remove.append(collector)

    for c in set(to_remove):
        try:
            REGISTRY.unregister(c)
        except Exception:  # noqa: BLE001
            pass

    yield

    mod._metrics = None


# ---------------------------------------------------------------------------
# TestMASMetrics
# ---------------------------------------------------------------------------


class TestMASMetrics:
    """Tests for the MASMetrics class and get_metrics() singleton."""

    def test_get_metrics_returns_singleton(self) -> None:
        """Two successive calls to get_metrics() should return the same object."""
        from src.monitoring.metrics import get_metrics

        m1 = get_metrics()
        m2 = get_metrics()

        assert m1 is m2

    def test_record_agent_run_increments_counter(self) -> None:
        """record_agent_run should increment agent_runs_total for the given labels."""
        from src.monitoring.metrics import get_metrics

        metrics = get_metrics()
        metrics.record_agent_run("scout", status="success", duration_seconds=1.5)

        value = metrics.agent_runs_total.labels(agent_name="scout", status="success")._value.get()
        assert value == 1.0

    def test_record_llm_call_increments_counter(self) -> None:
        """record_llm_call should increment llm_calls_total for the given labels."""
        from src.monitoring.metrics import get_metrics

        metrics = get_metrics()
        metrics.record_llm_call(
            "dev",
            "anthropic/claude-opus-4.6",
            tokens_input=500,
            tokens_output=200,
            cost_usd=0.01,
            latency_seconds=2.0,
            status="success",
        )

        calls_value = metrics.llm_calls_total.labels(
            agent_name="dev", model="anthropic/claude-opus-4.6", status="success",
        )._value.get()
        assert calls_value == 1.0

        input_tokens = metrics.llm_tokens_total.labels(
            agent_name="dev", model="anthropic/claude-opus-4.6", direction="input",
        )._value.get()
        assert input_tokens == 500.0

    def test_record_agent_run_observes_duration(self) -> None:
        """record_agent_run should observe duration on agent_duration_seconds histogram."""
        from src.monitoring.metrics import get_metrics

        metrics = get_metrics()
        metrics.record_agent_run("planner", status="success", duration_seconds=3.7)

        # Histogram sum should contain the observed duration.
        hist_sum = metrics.agent_duration_seconds.labels(agent_name="planner")._sum.get()
        assert abs(hist_sum - 3.7) < 1e-6


# ---------------------------------------------------------------------------
# TestInitSentry
# ---------------------------------------------------------------------------


class TestInitSentry:
    """Tests for init_sentry() configuration."""

    def test_init_sentry_with_dsn(self) -> None:
        """init_sentry(dsn=...) should call sentry_sdk.init with correct params."""
        mock_init = MagicMock()
        mock_asyncio_int = MagicMock()
        mock_sqla_int = MagicMock()

        mock_sentry_module = MagicMock(init=mock_init)
        mock_asyncio_module = MagicMock(AsyncioIntegration=mock_asyncio_int)
        mock_sqla_module = MagicMock(SqlalchemyIntegration=mock_sqla_int)

        with patch.dict(
            "sys.modules",
            {
                "sentry_sdk": mock_sentry_module,
                "sentry_sdk.integrations": MagicMock(),
                "sentry_sdk.integrations.asyncio": mock_asyncio_module,
                "sentry_sdk.integrations.sqlalchemy": mock_sqla_module,
            },
        ):
            init_sentry("https://key@sentry.io/123", environment="staging")

            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args[1]
            assert call_kwargs["dsn"] == "https://key@sentry.io/123"
            assert call_kwargs["environment"] == "staging"
            assert call_kwargs["send_default_pii"] is False

    def test_init_sentry_without_dsn_skips(self) -> None:
        """init_sentry(dsn=None) should NOT call sentry_sdk.init."""
        mock_sentry_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "sentry_sdk": mock_sentry_module,
                "sentry_sdk.integrations": MagicMock(),
                "sentry_sdk.integrations.asyncio": MagicMock(),
                "sentry_sdk.integrations.sqlalchemy": MagicMock(),
            },
        ):
            init_sentry(None)
            mock_sentry_module.init.assert_not_called()

            init_sentry("")
            mock_sentry_module.init.assert_not_called()
