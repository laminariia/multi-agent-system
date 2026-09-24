"""Prometheus metrics for the Multi-Agent Service.

All MAS-specific metrics are registered on module load via a thread-safe
singleton (``get_metrics()``).  The ``MASMetrics`` class groups counters,
histograms, and gauges into logical categories and exposes convenience
``record_*`` helpers so callers never have to touch raw label sets.

Metrics endpoint: ``GET /metrics`` (see ``src.api.routes.metrics``).
"""

from __future__ import annotations

import threading

import structlog
from prometheus_client import Counter, Gauge, Histogram, Info

logger = structlog.get_logger(__name__)


class MASMetrics:
    """Prometheus metrics for the Multi-Agent Service.

    Access via the module-level ``get_metrics()`` function to ensure only
    one set of collectors is ever registered with the default registry.
    """

    def __init__(self) -> None:
        # ------------------------------------------------------------------
        # Agent execution metrics
        # ------------------------------------------------------------------
        self.agent_runs_total = Counter(
            "mas_agent_runs_total",
            "Total agent invocations",
            ["agent_name", "status"],  # status: success, failed, hitl_paused
        )
        self.agent_duration_seconds = Histogram(
            "mas_agent_duration_seconds",
            "Agent execution duration",
            ["agent_name"],
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
        )

        # ------------------------------------------------------------------
        # LLM call metrics
        # ------------------------------------------------------------------
        self.llm_calls_total = Counter(
            "mas_llm_calls_total",
            "Total LLM API calls",
            ["agent_name", "model", "status"],  # status: success, rate_limited, error
        )
        self.llm_tokens_total = Counter(
            "mas_llm_tokens_total",
            "Total LLM tokens consumed",
            ["agent_name", "model", "direction"],  # direction: input, output
        )
        self.llm_cost_usd_total = Counter(
            "mas_llm_cost_usd_total",
            "Total LLM cost in USD",
            ["agent_name", "model"],
        )
        self.llm_latency_seconds = Histogram(
            "mas_llm_latency_seconds",
            "LLM call latency",
            ["agent_name", "model"],
            buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
        )

        # ------------------------------------------------------------------
        # Pipeline metrics
        # ------------------------------------------------------------------
        self.jobs_scanned_total = Counter(
            "mas_jobs_scanned_total",
            "Total jobs scanned by Scout",
            ["platform"],
        )
        self.bids_generated_total = Counter(
            "mas_bids_generated_total",
            "Total bids generated",
            ["platform"],
        )
        self.bids_approved_total = Counter(
            "mas_bids_approved_total",
            "Total bids approved via HITL",
        )
        self.bids_rejected_total = Counter(
            "mas_bids_rejected_total",
            "Total bids rejected via HITL",
        )

        # ------------------------------------------------------------------
        # HITL queue
        # ------------------------------------------------------------------
        self.hitl_queue_size = Gauge(
            "mas_hitl_queue_size",
            "Current HITL queue size",
            ["type"],  # bid_approval, final_review
        )
        self.hitl_response_time_seconds = Histogram(
            "mas_hitl_response_time_seconds",
            "Time to resolve HITL requests",
            buckets=(60, 300, 600, 1800, 3600, 7200, 86400),
        )

        # ------------------------------------------------------------------
        # Sandbox metrics
        # ------------------------------------------------------------------
        self.sandbox_executions_total = Counter(
            "mas_sandbox_executions_total",
            "Total sandbox code executions",
            ["executor", "status"],  # executor: docker, e2b; status: success, failed, timeout
        )

        # ------------------------------------------------------------------
        # Per-agent execution metrics (L8)
        # ------------------------------------------------------------------
        self.agent_execution_seconds = Histogram(
            "mas_agent_execution_seconds",
            "Per-agent execution duration (wall clock)",
            ["agent_name"],
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
        )
        self.agent_token_cost = Counter(
            "mas_agent_token_cost",
            "Cumulative LLM token cost per agent and model (USD)",
            ["agent_name", "model"],
        )
        self.pipeline_concurrent_gauge = Gauge(
            "mas_pipeline_concurrent",
            "Number of pipeline executions currently running",
        )

        # ------------------------------------------------------------------
        # System info
        # ------------------------------------------------------------------
        self.app_info = Info("mas_app", "Application info")
        self.app_info.info({"version": "4.2.0"})

    # ------------------------------------------------------------------
    # Convenience recording methods
    # ------------------------------------------------------------------

    def record_agent_run(
        self,
        agent_name: str,
        *,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Record an agent invocation.

        Args:
            agent_name: Canonical agent identifier (e.g. ``"scout"``).
            status: One of ``"success"``, ``"failed"``, ``"hitl_paused"``.
            duration_seconds: Wall-clock execution time.
        """
        self.agent_runs_total.labels(agent_name=agent_name, status=status).inc()
        self.agent_duration_seconds.labels(agent_name=agent_name).observe(
            duration_seconds,
        )

    def record_llm_call(
        self,
        agent_name: str,
        model: str,
        *,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_usd: float = 0.0,
        latency_seconds: float = 0.0,
        status: str = "success",
    ) -> None:
        """Record an LLM API call.

        Args:
            agent_name: Canonical agent identifier.
            model: Model identifier string (e.g. ``"anthropic/claude-opus-4.6"``).
            tokens_input: Number of input tokens consumed.
            tokens_output: Number of output tokens produced.
            cost_usd: Estimated cost in US dollars.
            latency_seconds: Round-trip latency.
            status: One of ``"success"``, ``"rate_limited"``, ``"error"``.
        """
        self.llm_calls_total.labels(
            agent_name=agent_name,
            model=model,
            status=status,
        ).inc()
        self.llm_tokens_total.labels(
            agent_name=agent_name,
            model=model,
            direction="input",
        ).inc(tokens_input)
        self.llm_tokens_total.labels(
            agent_name=agent_name,
            model=model,
            direction="output",
        ).inc(tokens_output)
        self.llm_cost_usd_total.labels(
            agent_name=agent_name,
            model=model,
        ).inc(cost_usd)
        self.llm_latency_seconds.labels(
            agent_name=agent_name,
            model=model,
        ).observe(latency_seconds)

    def record_sandbox_execution(
        self,
        executor: str,
        *,
        status: str,
    ) -> None:
        """Record a sandbox code execution.

        Args:
            executor: Sandbox type -- ``"docker"`` or ``"e2b"``.
            status: One of ``"success"``, ``"failed"``, ``"timeout"``.
        """
        self.sandbox_executions_total.labels(executor=executor, status=status).inc()

    def record_agent_execution(
        self,
        agent_name: str,
        *,
        duration_seconds: float,
        token_cost: float = 0.0,
        model: str = "unknown",
    ) -> None:
        """Record a per-agent execution with timing and optional cost.

        Args:
            agent_name: Canonical agent identifier (e.g. ``"scout"``).
            duration_seconds: Wall-clock execution time.
            token_cost: Estimated LLM token cost in USD (0 for cached/free).
            model: Model identifier used during execution.
        """
        self.agent_execution_seconds.labels(agent_name=agent_name).observe(
            duration_seconds,
        )
        if token_cost > 0:
            self.agent_token_cost.labels(
                agent_name=agent_name,
                model=model,
            ).inc(token_cost)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_metrics: MASMetrics | None = None
_lock = threading.Lock()


def get_metrics() -> MASMetrics:
    """Return the singleton ``MASMetrics`` instance.

    Thread-safe; uses double-checked locking so that subsequent calls after
    the first initialisation avoid acquiring the lock.
    """
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        with _lock:
            if _metrics is None:
                _metrics = MASMetrics()
    return _metrics
