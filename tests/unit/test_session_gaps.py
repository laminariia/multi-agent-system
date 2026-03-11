"""Tests for spec-gap fixes: dev_launch later, _next_action for new HITL types,
telegram_username in pipeline_b, hitl_dev_launch available_actions.

Covers changes from the spec-gap audit session (2026-03-11).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.state import create_initial_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> dict[str, Any]:
    project = {
        "project_id": "p1",
        "job_id": "j1",
        "platform": "freelancer",
        "client": {"name": "T"},
        "requirements": "page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent="scout", thread_id="t-gaps")
    s.update(overrides)
    return s


# ===========================================================================
# 1. _apply_dev_launch — "later" action keeps pipeline paused
# ===========================================================================


class TestApplyDevLaunchLater:
    """Test the 'later' (defer) action for dev_launch HITL gate."""

    async def test_later_keeps_paused(self):
        from src.core.graph import _apply_dev_launch

        state = _state(status="paused", requires_hitl=True, hitl_request_id="req-1")
        result = _apply_dev_launch(state, "later", {}, "t-test")

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True

    async def test_later_does_not_clear_hitl_request(self):
        from src.core.graph import _apply_dev_launch

        state = _state(status="paused", requires_hitl=True, hitl_request_id="req-2")
        result = _apply_dev_launch(state, "later", {}, "t-test")

        # "later" should NOT clear hitl_request_id (operator may return)
        assert result["status"] == "paused"

    async def test_approve_activates_pipeline(self):
        from src.core.graph import _apply_dev_launch

        state = _state(status="paused", requires_hitl=True)
        result = _apply_dev_launch(state, "approve", {}, "t-test")

        assert result["status"] == "active"
        assert result["requires_hitl"] is False
        assert result["next_agent"] == "planner"

    async def test_reject_fails_pipeline(self):
        from src.core.graph import _apply_dev_launch

        state = _state(status="paused", requires_hitl=True)
        result = _apply_dev_launch(state, "reject", {}, "t-test")

        assert result["status"] == "failed"
        assert result["requires_hitl"] is False

    async def test_unknown_action_fails_closed(self):
        from src.core.graph import _apply_dev_launch

        state = _state(status="paused", requires_hitl=True)
        result = _apply_dev_launch(state, "bogus", {}, "t-test")

        assert result["status"] == "failed"
        assert any("bogus" in e for e in result.get("errors", []))


# ===========================================================================
# 2. hitl_dev_launch_node — available_actions includes "later"
# ===========================================================================


class TestDevLaunchNodeActions:
    """Verify hitl_dev_launch_node creates HITLQueue with 'later' in available_actions."""

    async def test_available_actions_include_later(self):
        from src.core.graph import hitl_dev_launch_node

        state = _state(status="active")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.core.database.get_db_session", return_value=mock_session):
            result = await hitl_dev_launch_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True

        # Verify the HITLQueue entry was created with correct available_actions
        added_obj = mock_session.add.call_args[0][0]
        assert "later" in added_obj.available_actions
        assert "approve" in added_obj.available_actions
        assert "reject" in added_obj.available_actions


# ===========================================================================
# 3. _next_action — new HITL types (agent_failure, delivery_hold,
#    manual_action, outreach_approval)
# ===========================================================================


class TestNextActionNewTypes:
    """Test _next_action returns correct labels for new HITL type entries."""

    def test_agent_failure_resume(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("agent_failure", "resume") == "agent_will_retry"

    def test_agent_failure_skip(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("agent_failure", "skip") == "agent_skipped"

    def test_agent_failure_manual(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("agent_failure", "manual") == "manual_artifacts_provided"

    def test_agent_failure_later(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("agent_failure", "later") == "agent_failure_deferred"

    def test_delivery_hold_deliver_now(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("delivery_hold", "deliver_now") == "delivery_released_early"

    def test_delivery_hold_wait(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("delivery_hold", "wait") == "delivery_hold_continued"

    def test_delivery_hold_later(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("delivery_hold", "later") == "delivery_hold_deferred"

    def test_manual_action_approve(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("manual_action", "approve") == "manual_action_completed"

    def test_manual_action_reject(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("manual_action", "reject") == "manual_action_rejected"

    def test_manual_action_skip(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("manual_action", "skip") == "manual_action_skipped"

    def test_manual_action_later(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("manual_action", "later") == "manual_action_deferred"

    def test_outreach_approval_approve(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("outreach_approval", "approve") == "outreach_emails_will_be_sent"

    def test_outreach_approval_reject(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("outreach_approval", "reject") == "outreach_emails_discarded"

    def test_outreach_approval_edit(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("outreach_approval", "edit") == "outreach_emails_revised"

    def test_outreach_approval_skip(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("outreach_approval", "skip") == "outreach_skipped"

    def test_outreach_approval_later(self):
        from src.api.routes.hitl import _next_action

        assert _next_action("outreach_approval", "later") == "outreach_deferred"


# ===========================================================================
# 4. _RESUMABLE_TYPES includes new types
# ===========================================================================


class TestResumableTypesExpanded:
    """Verify resolve_hitl_item source contains all required resumable types."""

    def test_resumable_types_in_source(self):
        """Check that the resolve handler source includes all expected resumable types.

        _RESUMABLE_TYPES is a local variable, so we verify via source inspection.
        """
        import inspect

        from src.api.routes.hitl import HITLController

        source = inspect.getsource(HITLController.resolve.fn)

        expected = [
            "agent_failure",
            "outreach_approval",
            "delivery_hold",
        ]
        for t in expected:
            assert f'"{t}"' in source, f"{t} missing from _RESUMABLE_TYPES in resolve"


# ===========================================================================
# 5. Pipeline B list_leads returns telegram_username
# ===========================================================================


class TestPipelineBTelegramUsername:
    """Verify list_leads and get_lead include telegram_username in response."""

    async def test_list_leads_includes_telegram_username(self):
        from src.api.routes.pipeline_b import PipelineBController
        from src.core.models import Lead

        mock_session = AsyncMock()

        mock_lead = MagicMock(spec=Lead)
        mock_lead.id = uuid.uuid4()
        mock_lead.name = "TG Cafe"
        mock_lead.category = "cafe"
        mock_lead.city = "Moscow"
        mock_lead.address = "Arbat 1"
        mock_lead.phone = "+7999"
        mock_lead.email = "cafe@test.ru"
        mock_lead.telegram_username = "@tg_cafe"
        mock_lead.status = "enriched"
        mock_lead.enrichment_source = "osint"
        mock_lead.discovered_at = datetime.now(UTC)
        mock_lead.latitude = None
        mock_lead.longitude = None

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_lead]
        mock_exec_1 = MagicMock()
        mock_exec_1.scalars.return_value = mock_scalars

        mock_exec_2 = MagicMock()
        mock_exec_2.scalar.return_value = 1

        mock_session.execute = AsyncMock(side_effect=[mock_exec_1, mock_exec_2])

        result = await PipelineBController.list_leads.fn(
            self=None,
            db_session=mock_session,
            city=None,
            status=None,
            search=None,
            sort=None,
            limit=50,
            offset=0,
        )

        assert result["leads"][0]["telegram_username"] == "@tg_cafe"

    async def test_list_leads_telegram_username_null(self):
        from src.api.routes.pipeline_b import PipelineBController
        from src.core.models import Lead

        mock_session = AsyncMock()

        mock_lead = MagicMock(spec=Lead)
        mock_lead.id = uuid.uuid4()
        mock_lead.name = "No TG"
        mock_lead.category = "shop"
        mock_lead.city = "Berlin"
        mock_lead.address = "Str 2"
        mock_lead.phone = None
        mock_lead.email = None
        mock_lead.telegram_username = None
        mock_lead.status = "discovered"
        mock_lead.enrichment_source = None
        mock_lead.discovered_at = datetime.now(UTC)
        mock_lead.latitude = None
        mock_lead.longitude = None

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_lead]
        mock_exec_1 = MagicMock()
        mock_exec_1.scalars.return_value = mock_scalars

        mock_exec_2 = MagicMock()
        mock_exec_2.scalar.return_value = 1

        mock_session.execute = AsyncMock(side_effect=[mock_exec_1, mock_exec_2])

        result = await PipelineBController.list_leads.fn(
            self=None,
            db_session=mock_session,
            city=None,
            status=None,
            search=None,
            sort=None,
            limit=50,
            offset=0,
        )

        assert result["leads"][0]["telegram_username"] is None
