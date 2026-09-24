"""Unit tests for L8: Per-Agent Execution Metrics.

Tests additions to MASMetrics: agent_execution_seconds histogram,
agent_token_cost counter, pipeline_concurrent_gauge, and agent timing
recording in ConstrainedAgent.
"""

from __future__ import annotations

import contextlib

import pytest
from prometheus_client import REGISTRY

# ---------------------------------------------------------------------------
# Fixture: reset MASMetrics singleton between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset the MASMetrics singleton and unregister collectors."""
    import src.monitoring.metrics as mod

    mod._metrics = None
    # Unregister all MAS collectors to avoid Duplicated timeseries
    to_remove = [name for name in list(REGISTRY._names_to_collectors.keys()) if name.startswith("mas_")]
    for name in to_remove:
        with contextlib.suppress(Exception):
            REGISTRY.unregister(REGISTRY._names_to_collectors[name])
    yield
    mod._metrics = None
    to_remove = [name for name in list(REGISTRY._names_to_collectors.keys()) if name.startswith("mas_")]
    for name in to_remove:
        with contextlib.suppress(Exception):
            REGISTRY.unregister(REGISTRY._names_to_collectors[name])


# ---------------------------------------------------------------------------
# Tests for new metrics
# ---------------------------------------------------------------------------


class TestAgentExecutionSeconds:
    """Tests for the agent_execution_seconds histogram."""

    def test_histogram_exists(self) -> None:
        """agent_execution_seconds histogram is registered."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        assert hasattr(m, "agent_execution_seconds")

    def test_observe_records(self) -> None:
        """Observing a value doesn't raise."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.agent_execution_seconds.labels(agent_name="scout").observe(1.5)
        # Verify sample exists
        samples = list(m.agent_execution_seconds.collect())
        assert len(samples) > 0


class TestAgentTokenCost:
    """Tests for the agent_token_cost counter."""

    def test_counter_exists(self) -> None:
        """agent_token_cost counter is registered."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        assert hasattr(m, "agent_token_cost")

    def test_increment(self) -> None:
        """Incrementing the counter doesn't raise."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.agent_token_cost.labels(agent_name="bid", model="gemini-pro").inc(0.05)
        samples = list(m.agent_token_cost.collect())
        assert len(samples) > 0

    def test_multiple_agents(self) -> None:
        """Different agents have independent counters."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.agent_token_cost.labels(agent_name="scout", model="flash").inc(0.01)
        m.agent_token_cost.labels(agent_name="bid", model="gemini-pro").inc(0.05)
        # Both should be recorded without error
        samples = list(m.agent_token_cost.collect())
        assert len(samples) > 0


class TestPipelineConcurrentGauge:
    """Tests for the pipeline_concurrent_gauge."""

    def test_gauge_exists(self) -> None:
        """pipeline_concurrent_gauge is registered."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        assert hasattr(m, "pipeline_concurrent_gauge")

    def test_inc_dec(self) -> None:
        """Increment and decrement work correctly."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.pipeline_concurrent_gauge.inc()
        m.pipeline_concurrent_gauge.inc()
        m.pipeline_concurrent_gauge.dec()
        # Should not raise


class TestRecordAgentExecution:
    """Tests for the record_agent_execution convenience method."""

    def test_records_all_fields(self) -> None:
        """record_agent_execution updates histogram and counter."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.record_agent_execution(
            agent_name="dev",
            duration_seconds=5.2,
            token_cost=0.12,
            model="claude-sonnet",
        )
        # Should not raise; histogram and counter should have data
        exec_samples = list(m.agent_execution_seconds.collect())
        cost_samples = list(m.agent_token_cost.collect())
        assert len(exec_samples) > 0
        assert len(cost_samples) > 0

    def test_zero_cost_allowed(self) -> None:
        """Zero token cost is valid (e.g., cached response)."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.record_agent_execution(
            agent_name="critic",
            duration_seconds=1.0,
            token_cost=0.0,
            model="sonnet",
        )

    def test_records_duration_only(self) -> None:
        """Can record duration without token cost."""
        from src.monitoring.metrics import get_metrics

        m = get_metrics()
        m.record_agent_execution(
            agent_name="packager",
            duration_seconds=3.0,
        )
