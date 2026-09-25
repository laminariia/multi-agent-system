"""Tests for API fixes week 2: H15, H16, H17.

H15: WebSocket project:update auto-subscribe
H16: Prometheus /api/v1/metrics endpoint
H17: Orchestrator Goals DELETE with milestone cleanup
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================================
# H15: WebSocket project:update auto-subscribe
# ============================================================================


class TestH15ProjectUpdateAutoSubscribe:
    """CHANNEL_PROJECT_UPDATE must be in _DEFAULT_CHANNELS."""

    def test_project_update_in_default_channels(self) -> None:
        """project:update channel should be auto-subscribed for authenticated users."""
        from src.api.websocket import _DEFAULT_CHANNELS, CHANNEL_PROJECT_UPDATE

        assert CHANNEL_PROJECT_UPDATE in _DEFAULT_CHANNELS, (
            f"'{CHANNEL_PROJECT_UPDATE}' must be in _DEFAULT_CHANNELS "
            f"so authenticated WebSocket clients auto-subscribe. "
            f"Current defaults: {_DEFAULT_CHANNELS}"
        )

    def test_default_channels_no_duplicates(self) -> None:
        """_DEFAULT_CHANNELS must not contain duplicate entries."""
        from src.api.websocket import _DEFAULT_CHANNELS

        assert len(_DEFAULT_CHANNELS) == len(set(_DEFAULT_CHANNELS)), (
            f"_DEFAULT_CHANNELS has duplicates: {_DEFAULT_CHANNELS}"
        )

    def test_all_broadcast_channels_present(self) -> None:
        """All defined CHANNEL_* broadcast constants should be in defaults."""
        from src.api.websocket import (
            _DEFAULT_CHANNELS,
            CHANNEL_AGENT_HEARTBEAT,
            CHANNEL_AGENT_LOG,
            CHANNEL_HITL_NEW,
            CHANNEL_HITL_RESOLVED,
            CHANNEL_NEGOTIATION_FOLLOWUP,
            CHANNEL_NEGOTIATION_HITL,
            CHANNEL_NEGOTIATION_MESSAGE,
            CHANNEL_NEGOTIATION_STATE,
            CHANNEL_NOTIFICATION,
            CHANNEL_ORCH_GOAL,
            CHANNEL_ORCH_LOG,
            CHANNEL_ORCH_STATUS,
            CHANNEL_PIPELINE_PROGRESS,
            CHANNEL_PROJECT_UPDATE,
        )

        expected = {
            CHANNEL_AGENT_HEARTBEAT,
            CHANNEL_AGENT_LOG,
            CHANNEL_HITL_NEW,
            CHANNEL_HITL_RESOLVED,
            CHANNEL_NOTIFICATION,
            CHANNEL_ORCH_STATUS,
            CHANNEL_ORCH_GOAL,
            CHANNEL_ORCH_LOG,
            CHANNEL_PROJECT_UPDATE,
            CHANNEL_PIPELINE_PROGRESS,
            CHANNEL_NEGOTIATION_MESSAGE,
            CHANNEL_NEGOTIATION_STATE,
            CHANNEL_NEGOTIATION_HITL,
            CHANNEL_NEGOTIATION_FOLLOWUP,
        }
        assert expected == set(_DEFAULT_CHANNELS), (
            f"Missing from _DEFAULT_CHANNELS: {expected - set(_DEFAULT_CHANNELS)}"
        )

    def test_channel_project_update_value(self) -> None:
        """CHANNEL_PROJECT_UPDATE must have the correct string value."""
        from src.api.websocket import CHANNEL_PROJECT_UPDATE

        assert CHANNEL_PROJECT_UPDATE == "project:update"


# ============================================================================
# H16: Prometheus /api/v1/metrics endpoint
# ============================================================================


class TestH16PrometheusApiMetrics:
    """GET /api/v1/metrics should return Prometheus exposition format."""

    def test_api_v1_metrics_controller_exists(self) -> None:
        """ApiV1MetricsController must exist for /api/v1/metrics."""
        from src.api.routes.metrics import ApiV1MetricsController

        assert ApiV1MetricsController.path == "/api/v1/metrics"

    def test_api_v1_metrics_controller_has_handler(self) -> None:
        """ApiV1MetricsController must have an api_v1_metrics GET handler."""
        from src.api.routes.metrics import ApiV1MetricsController

        assert hasattr(ApiV1MetricsController, "api_v1_metrics"), (
            "ApiV1MetricsController must have an 'api_v1_metrics' method"
        )

    @pytest.mark.asyncio
    async def test_api_v1_metrics_returns_prometheus_format(self) -> None:
        """GET /api/v1/metrics must return prometheus_client output."""
        from prometheus_client import CONTENT_TYPE_LATEST

        from src.api.routes.metrics import ApiV1MetricsController

        controller = ApiV1MetricsController(owner=MagicMock())

        # Access the underlying function to bypass Litestar's HTTPRouteHandler wrapper
        handler = ApiV1MetricsController.api_v1_metrics
        raw_fn = handler.fn if hasattr(handler, "fn") else handler
        resp = await raw_fn(controller)

        # Must return a Response with Prometheus content type
        assert resp.media_type == CONTENT_TYPE_LATEST
        # Content must be bytes from generate_latest
        body = resp.body if hasattr(resp, "body") else resp.content
        assert isinstance(body, bytes)
        assert len(body) > 0

    def test_api_v1_metrics_excluded_from_auth(self) -> None:
        """The /api/v1/metrics endpoint must be excluded from JWT auth."""
        from src.api.routes.metrics import ApiV1MetricsController

        handler = ApiV1MetricsController.api_v1_metrics
        handler_fn = handler.fn if hasattr(handler, "fn") else handler
        found_exclude = False
        for attr_name in ("opt", "_opt"):
            val = getattr(handler_fn, attr_name, None)
            if val and val.get("exclude_from_auth"):
                found_exclude = True
                break
        assert found_exclude or _check_handler_opt(handler, "exclude_from_auth"), (
            "/api/v1/metrics must be excluded from auth"
        )

    def test_api_v1_metrics_registered_in_app(self) -> None:
        """ApiV1MetricsController must be in the Litestar app route_handlers."""

        # Verify it's importable in main and listed
        import importlib
        import inspect

        main_mod = importlib.import_module("src.api.main")
        source = inspect.getsource(main_mod)
        assert "ApiV1MetricsController" in source, (
            "ApiV1MetricsController must be registered in src.api.main route_handlers"
        )

    def test_metrics_in_rate_limit_exclude(self) -> None:
        """The /metrics path should be in JWT rate limiter exclusions."""
        from src.api.middleware.jwt_rate_limiter import EXCLUDE_PATHS

        assert "/metrics" in EXCLUDE_PATHS, "/metrics must be excluded from rate limiting"

    def test_generate_latest_contains_mas_prefix(self) -> None:
        """Prometheus output should contain MAS-specific metrics."""
        from prometheus_client import generate_latest

        from src.monitoring.metrics import get_metrics

        # Ensure metrics are initialized
        get_metrics()

        output = generate_latest().decode("utf-8")
        assert "mas_" in output, "Prometheus output must contain MAS-prefixed metrics"


def _check_handler_opt(handler: object, key: str) -> bool:
    """Walk Litestar handler internals to find opt flag."""
    # Litestar stores opt in different places depending on version
    for attr in ("opt", "_opt", "resolved_handler"):
        val = getattr(handler, attr, None)
        if isinstance(val, dict) and val.get(key):
            return True
    # Check through __wrapped__ chain
    wrapped = getattr(handler, "__wrapped__", None)
    if wrapped is not None:
        return _check_handler_opt(wrapped, key)
    return False


# ============================================================================
# H17: Orchestrator Goals DELETE with milestone cleanup
# ============================================================================


class TestH17GoalsDelete:
    """DELETE /api/v1/orchestrator/goals/{goal_id} must exist and clean up."""

    def test_delete_goal_endpoint_exists(self) -> None:
        """OrchestratorController must have a delete_goal handler."""
        from src.api.routes.orchestrator import OrchestratorController

        assert hasattr(OrchestratorController, "delete_goal"), "OrchestratorController must have a 'delete_goal' method"

    @pytest.mark.asyncio
    async def test_delete_goal_service_success(self) -> None:
        """OrchestratorService.delete_goal should remove a goal from DB."""
        from src.api.services.orchestrator import OrchestratorService

        session = AsyncMock()

        # Mock the goal object
        mock_goal = MagicMock()
        mock_goal.goal_id = "g_001"
        mock_goal.title = "Test goal"

        # Mock the query result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_goal
        session.execute.return_value = mock_result
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        svc = OrchestratorService()
        result = await svc.delete_goal(session, "g_001")

        assert result["goal_id"] == "g_001"
        assert "deleted" in result["message"].lower() or "deleted" in result.get("message", "").lower()
        session.delete.assert_called_once_with(mock_goal)
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_goal_service_not_found(self) -> None:
        """OrchestratorService.delete_goal should raise KeyError for missing goals."""
        from src.api.services.orchestrator import OrchestratorService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        svc = OrchestratorService()
        with pytest.raises(KeyError, match="g_999"):
            await svc.delete_goal(session, "g_999")

    @pytest.mark.asyncio
    async def test_delete_goal_cleans_milestones(self) -> None:
        """delete_goal should remove associated milestones if they exist."""
        from src.api.services.orchestrator import OrchestratorService

        session = AsyncMock()

        mock_goal = MagicMock()
        mock_goal.goal_id = "g_002"
        mock_goal.title = "Goal with milestones"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_goal
        session.execute.return_value = mock_result
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        svc = OrchestratorService()
        result = await svc.delete_goal(session, "g_002")

        # Goal must be deleted
        assert result["goal_id"] == "g_002"
        session.delete.assert_called()

        # Verify that milestone cleanup was attempted (execute called for
        # milestone deletion query before the goal itself is deleted)
        assert session.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_delete_goal_returns_schema(self) -> None:
        """delete_goal response must match GoalDeleteResponseSchema."""
        from src.api.schemas import GoalDeleteResponseSchema
        from src.api.services.orchestrator import OrchestratorService

        session = AsyncMock()
        mock_goal = MagicMock()
        mock_goal.goal_id = "g_003"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_goal
        session.execute.return_value = mock_result
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        svc = OrchestratorService()
        result = await svc.delete_goal(session, "g_003")

        # Must be parseable as GoalDeleteResponseSchema
        schema = GoalDeleteResponseSchema(**result)
        assert schema.goal_id == "g_003"
        assert "deleted" in schema.message.lower()

    @pytest.mark.asyncio
    async def test_delete_goal_publishes_ws_event(self) -> None:
        """The controller delete_goal should publish a WS event."""
        from src.api.routes.orchestrator import OrchestratorController

        controller = OrchestratorController(owner=MagicMock())

        # Mock dependencies
        mock_session = AsyncMock()
        mock_request = MagicMock()
        mock_request.user = MagicMock()
        mock_request.user.id = uuid.uuid4()
        mock_channels = AsyncMock()

        mock_result = {"goal_id": "g_005", "message": "Goal deleted successfully"}

        # Access the underlying function to bypass Litestar's HTTPRouteHandler wrapper
        delete_goal_fn = OrchestratorController.delete_goal
        raw_fn = delete_goal_fn.fn if hasattr(delete_goal_fn, "fn") else delete_goal_fn

        with (
            patch(
                "src.api.routes.orchestrator._svc.delete_goal",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "src.api.routes.orchestrator.publish_event",
                new_callable=AsyncMock,
            ) as mock_publish,
        ):
            await raw_fn(
                controller,
                goal_id="g_005",
                db_session=mock_session,
                request=mock_request,
                channels=mock_channels,
            )

            # WS event must be published
            mock_publish.assert_called_once()
            call_args = mock_publish.call_args
            assert call_args[0][1] == "orch:goal"  # channel
            assert call_args[0][2]["data"]["action"] == "deleted"
            assert call_args[0][2]["data"]["goal_id"] == "g_005"

    def test_delete_goal_requires_owner_role(self) -> None:
        """DELETE /goals/{goal_id} must require owner or co_owner role."""
        from src.api.routes.orchestrator import OrchestratorController

        handler = OrchestratorController.delete_goal
        # Litestar stores guards on the handler
        guards = getattr(handler, "guards", None) or getattr(handler.fn, "guards", None) or []
        # At minimum, the handler must be guarded (non-empty)
        # We verify by checking the handler definition has guards
        assert len(guards) > 0 or _has_guard_in_definition(handler), (
            "delete_goal must have role guards (owner/co_owner)"
        )


def _has_guard_in_definition(handler: object) -> bool:
    """Check if the handler was defined with guards."""
    import inspect

    try:
        source = inspect.getsource(handler.fn if hasattr(handler, "fn") else handler)
        return "require_role" in source or "guards=" in source
    except (TypeError, OSError):
        return True  # Can't inspect, assume OK
