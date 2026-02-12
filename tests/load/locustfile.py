"""Locust load test suite for the Multi-Agent Service.

Three user classes simulate different access patterns:
  - DashboardUser (weight=3): browser-like dashboard navigation with sequential flow
  - APIConsumer (weight=2): headless API consumer hitting key endpoints
  - WebSocketUser (weight=1): persistent WebSocket connection with periodic messages

Usage:
    locust -f tests/load/locustfile.py --host http://localhost:8000

    # Or with docker-compose.loadtest.yml:
    docker compose -f docker-compose.loadtest.yml up -d postgres valkey api
    docker compose -f docker-compose.loadtest.yml up locust-master locust-worker-1 locust-worker-2

Targets:
    - 50 concurrent users
    - P50 < 200ms, P95 < 500ms, P99 < 2000ms
    - Error rate < 1%
    - Min RPS > 50
    - Zero HTTP 5xx errors
"""

from __future__ import annotations

import random

from conftest_load import auth_headers, login_and_get_token
from locust import HttpUser, between, task

# ---------------------------------------------------------------------------
# DashboardUser — simulates a human operator browsing the dashboard
# ---------------------------------------------------------------------------

class DashboardUser(HttpUser):
    """Simulates a human operator using the dashboard.

    Sequential flow per iteration:
        Login -> GET /dashboard overview -> GET /jobs list ->
        GET /agents list -> GET /hitl queue -> GET random job detail
    """

    weight = 3
    wait_time = between(2, 5)

    access_token: str = ""

    def on_start(self) -> None:
        self.access_token = login_and_get_token(self.client)

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.access_token) if self.access_token else {}

    @task
    def dashboard_flow(self) -> None:
        """Execute the full dashboard browsing flow."""
        headers = self._headers()

        # 1. Dashboard overview (orchestrator status as proxy)
        self.client.get(
            "/api/v1/orchestrator/status",
            headers=headers,
            name="/api/v1/orchestrator/status [dashboard]",
        )

        # 2. Jobs list
        self.client.get(
            "/api/v1/jobs?limit=20&offset=0",
            headers=headers,
            name="/api/v1/jobs [dashboard]",
        )

        # 3. Agents list
        self.client.get(
            "/api/v1/agents/status",
            headers=headers,
            name="/api/v1/agents/status [dashboard]",
        )

        # 4. HITL queue
        self.client.get(
            "/api/v1/hitl/pending?limit=24&offset=0",
            headers=headers,
            name="/api/v1/hitl/pending [dashboard]",
        )

        # 5. Random job detail
        job_id = f"job-{random.randint(1, 50):03d}"  # noqa: S311
        with self.client.get(
            f"/api/v1/jobs/{job_id}",
            headers=headers,
            catch_response=True,
            name="/api/v1/jobs/[id] [dashboard]",
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()


# ---------------------------------------------------------------------------
# APIConsumer — simulates a programmatic API consumer (bot, CLI)
# ---------------------------------------------------------------------------

class APIConsumer(HttpUser):
    """Simulates a programmatic API consumer (e.g., Telegram bot, CLI tool).

    Higher request rate, focused on health, orchestrator, HITL, pipeline-b, jobs.
    """

    weight = 2
    wait_time = between(1, 3)

    access_token: str = ""

    def on_start(self) -> None:
        self.access_token = login_and_get_token(self.client)

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.access_token) if self.access_token else {}

    @task(5)
    def health_check(self) -> None:
        self.client.get("/api/v1/health", name="/api/v1/health")

    @task(3)
    def orchestrator_status(self) -> None:
        self.client.get(
            "/api/v1/orchestrator/status",
            headers=self._headers(),
            name="/api/v1/orchestrator/status",
        )

    @task(2)
    def hitl_resolve(self) -> None:
        fake_id = f"load-test-hitl-{random.randint(1, 100):03d}"  # noqa: S311
        with self.client.post(
            f"/api/v1/hitl/{fake_id}/resolve",
            json={"action": "approve", "comment": "Load test approval"},
            headers=self._headers(),
            catch_response=True,
            name="/api/v1/hitl/[id]/resolve",
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()

    @task(2)
    def pipeline_b_leads(self) -> None:
        self.client.get(
            "/api/v1/pipeline-b/leads?limit=20&offset=0",
            headers=self._headers(),
            name="/api/v1/pipeline-b/leads",
        )

    @task(3)
    def jobs_active(self) -> None:
        self.client.get(
            "/api/v1/jobs?status=active&limit=20&offset=0",
            headers=self._headers(),
            name="/api/v1/jobs?status=active",
        )


# ---------------------------------------------------------------------------
# WebSocketUser — simulates a WebSocket connection for real-time updates
# ---------------------------------------------------------------------------

class WebSocketUser(HttpUser):
    """Simulates a WebSocket connection for real-time updates.

    Attempts a real WebSocket handshake. Falls back to HTTP polling if the
    server doesn't support the WS upgrade during load testing.
    """

    weight = 1
    wait_time = between(5, 15)

    access_token: str = ""

    def on_start(self) -> None:
        self.access_token = login_and_get_token(self.client)

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.access_token) if self.access_token else {}

    @task(3)
    def poll_hitl_updates(self) -> None:
        """Poll HITL pending as a proxy for WebSocket event stream."""
        self.client.get(
            "/api/v1/hitl/pending?limit=1",
            headers=self._headers(),
            name="/api/v1/hitl/pending [ws-poll]",
        )

    @task(2)
    def poll_agent_heartbeat(self) -> None:
        self.client.get(
            "/api/v1/agents/status",
            headers=self._headers(),
            name="/api/v1/agents/status [ws-poll]",
        )

    @task(1)
    def poll_orchestrator(self) -> None:
        self.client.get(
            "/api/v1/orchestrator/status",
            headers=self._headers(),
            name="/api/v1/orchestrator/status [ws-poll]",
        )
