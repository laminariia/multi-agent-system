"""Unit tests for Pipeline B graph — Lead Card HITL + Design Review B phases."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END

from src.core.graph import (
    MAX_DESIGN_REVISIONS_B,
    _route_after_design_b,
    _route_after_design_b_entry,
    _route_after_hitl_concept_review,
    _route_after_lead_card,
    _route_after_outreach,
    build_pipeline_b_graph,
    hitl_design_review_b_node,
    hitl_lead_card_node,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_state(**overrides: Any) -> dict[str, Any]:
    """Create minimal test state for Pipeline B."""
    base: dict[str, Any] = {
        "thread_id": "test-thread-b",
        "mas_checkpoint_id": "test-cp-b",
        "project": {
            "project_id": "pb1",
            "job_id": "",
            "platform": "outreach",
            "title": "Test Project B",
            "client": {},
            "requirements": "Berlin",
            "budget": 0,
            "deadline": datetime.now(UTC),
        },
        "current_agent": "outreach",
        "current_task": None,
        "artifacts": {},
        "messages": [],
        "next_agent": None,
        "requires_hitl": False,
        "hitl_request_id": None,
        "retry_count": 0,
        "errors": [],
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "status": "active",
    }
    base.update(overrides)
    return base


# ===========================================================================
# _route_after_outreach — now routes to hitl_lead_card_node
# ===========================================================================


class TestRouteAfterOutreachLeadCard:
    """Outreach routing: lead replied -> lead card HITL."""

    def test_lead_replied_routes_to_lead_card(self):
        state = _make_state(artifacts={"_lead_replied": True})
        assert _route_after_outreach(state) == "hitl_lead_card_node"

    def test_requires_hitl_routes_to_hitl_outreach(self):
        state = _make_state(requires_hitl=True)
        assert _route_after_outreach(state) == "hitl_outreach_node"

    def test_failed_routes_to_end(self):
        state = _make_state(status="failed")
        assert _route_after_outreach(state) == END

    def test_no_lead_reply_routes_to_end(self):
        state = _make_state(artifacts={})
        assert _route_after_outreach(state) == END


# ===========================================================================
# hitl_lead_card_node
# ===========================================================================


class TestHitlLeadCardNode:
    """Lead Card HITL node creates entry and pauses."""

    @pytest.mark.asyncio
    async def test_creates_hitl_entry_and_pauses(self):
        state = _make_state(
            artifacts={
                "_lead_temperature": "hot",
                "_lead_name": "Test Cafe",
                "_lead_category": "cafe",
                "_lead_city": "Berlin",
                "_lead_rating": 4.5,
                "_lead_phone": "+49123",
                "_lead_email": "cafe@test.de",
                "_lead_telegram": "@testcafe",
                "_analysis_result": {"score": 85},
                "_battlecard_summary": "Strong local presence",
                "_competitor_count": 3,
            },
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock, return_value=True),
        ):
            result = await hitl_lead_card_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["current_agent"] == "hitl_lead_card"
        assert result["hitl_request_id"] is not None
        mock_session.add.assert_called_once()
        hitl_obj = mock_session.add.call_args[0][0]
        assert hitl_obj.type == "lead_card"
        assert hitl_obj.priority == "urgent"  # hot lead
        assert hitl_obj.available_actions == ["approve", "manual", "skip"]
        assert hitl_obj.payload["lead_name"] == "Test Cafe"
        assert hitl_obj.payload["contacts"]["phone"] == "+49123"

    @pytest.mark.asyncio
    async def test_warm_lead_normal_priority(self):
        state = _make_state(
            artifacts={
                "_lead_temperature": "warm",
                "_lead_name": "Warm Lead",
                "_lead_city": "Munich",
            },
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock, return_value=True),
        ):
            result = await hitl_lead_card_node(state)

        assert result["status"] == "paused"
        hitl_obj = mock_session.add.call_args[0][0]
        assert hitl_obj.priority == "normal"

    @pytest.mark.asyncio
    async def test_already_paused_returns_unchanged(self):
        state = _make_state(status="paused")
        result = await hitl_lead_card_node(state)
        assert result is state

    @pytest.mark.asyncio
    async def test_db_failure_still_pauses(self):
        state = _make_state(
            artifacts={"_lead_temperature": "hot", "_lead_name": "Fail Lead", "_lead_city": "Berlin"},
        )

        with patch("src.core.database.get_db_session", side_effect=RuntimeError("DB down")):
            result = await hitl_lead_card_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["current_agent"] == "hitl_lead_card"

    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_block(self):
        state = _make_state(
            artifacts={"_lead_temperature": "hot", "_lead_name": "TG Fail", "_lead_city": "Berlin"},
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch(
                "src.core.graph._send_hitl_telegram_notification",
                new_callable=AsyncMock,
                side_effect=RuntimeError("TG down"),
            ),
        ):
            result = await hitl_lead_card_node(state)

        # Should still pause successfully despite TG failure
        assert result["status"] == "paused"
        assert result["requires_hitl"] is True

    @pytest.mark.asyncio
    async def test_payload_includes_all_fields(self):
        state = _make_state(
            artifacts={
                "_lead_temperature": "hot",
                "_lead_name": "Full Fields",
                "_lead_category": "restaurant",
                "_lead_city": "Hamburg",
                "_lead_rating": 4.2,
                "_lead_phone": "+49456",
                "_lead_email": "r@test.de",
                "_lead_telegram": "@rest",
                "_analysis_result": {"tier": "deep", "competitors": 5},
                "_battlecard_summary": "Market leader in area",
                "_competitor_count": 5,
            },
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock, return_value=True),
        ):
            await hitl_lead_card_node(state)

        hitl_obj = mock_session.add.call_args[0][0]
        payload = hitl_obj.payload
        assert payload["lead_category"] == "restaurant"
        assert payload["lead_city"] == "Hamburg"
        assert payload["lead_rating"] == 4.2
        assert payload["contacts"]["email"] == "r@test.de"
        assert payload["contacts"]["telegram"] == "@rest"
        assert payload["analysis_result"]["tier"] == "deep"
        assert payload["battlecard_summary"] == "Market leader in area"
        assert payload["competitor_count"] == 5


# ===========================================================================
# _route_after_lead_card
# ===========================================================================


class TestRouteAfterLeadCard:
    """Lead card HITL routing decisions."""

    def test_approve_routes_to_sales_agent(self):
        state = _make_state(
            status="active",
            requires_hitl=False,
            artifacts={"_lead_card_resolution": "approve"},
        )
        assert _route_after_lead_card(state) == "sales_agent_node"

    def test_skip_routes_to_end(self):
        state = _make_state(
            status="active",
            requires_hitl=False,
            artifacts={"_lead_card_resolution": "skip"},
        )
        assert _route_after_lead_card(state) == END

    def test_manual_routes_to_end(self):
        state = _make_state(
            status="active",
            requires_hitl=False,
            artifacts={"_lead_card_resolution": "manual"},
        )
        assert _route_after_lead_card(state) == END

    def test_failed_routes_to_end(self):
        state = _make_state(status="failed")
        assert _route_after_lead_card(state) == END

    def test_still_paused_routes_to_end(self):
        state = _make_state(status="paused", requires_hitl=True)
        assert _route_after_lead_card(state) == END

    def test_active_no_resolution_defaults_to_sales_agent(self):
        state = _make_state(status="active", requires_hitl=False, artifacts={})
        assert _route_after_lead_card(state) == "sales_agent_node"


# ===========================================================================
# _route_after_hitl_concept_review — now routes to design on approve
# ===========================================================================


class TestRouteAfterConceptReviewDesign:
    """Concept review routing: approve -> design_node."""

    def test_concept_approved_routes_to_design(self):
        state = _make_state(
            status="active",
            artifacts={"_concept_approved": True},
        )
        assert _route_after_hitl_concept_review(state) == "design_node"

    def test_concept_edited_routes_to_sales_agent(self):
        state = _make_state(
            status="active",
            artifacts={"_concept_edited": True},
        )
        assert _route_after_hitl_concept_review(state) == "sales_agent_node"

    def test_concept_edited_takes_priority_over_approved(self):
        state = _make_state(
            status="active",
            artifacts={"_concept_edited": True, "_concept_approved": True},
        )
        # Edit takes priority (comes first in the function)
        assert _route_after_hitl_concept_review(state) == "sales_agent_node"

    def test_failed_routes_to_end(self):
        state = _make_state(status="failed")
        assert _route_after_hitl_concept_review(state) == END

    def test_no_flags_routes_to_end(self):
        state = _make_state(status="active", artifacts={})
        assert _route_after_hitl_concept_review(state) == END


# ===========================================================================
# hitl_design_review_b_node
# ===========================================================================


class TestHitlDesignReviewBNode:
    """Design Review B HITL node creates entry and pauses."""

    @pytest.mark.asyncio
    async def test_creates_hitl_entry_and_pauses(self):
        state = _make_state(
            artifacts={"design_mockup": ['{"screenshot_path": "/tmp/shot.png"}']},
            project={"title": "Test Deal Design"},
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock, return_value=True),
        ):
            result = await hitl_design_review_b_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["current_agent"] == "hitl_design_review_b"
        assert result["hitl_request_id"] is not None
        mock_session.add.assert_called_once()
        hitl_obj = mock_session.add.call_args[0][0]
        assert hitl_obj.type == "design_review"
        assert hitl_obj.priority == "high"
        assert hitl_obj.payload["pipeline"] == "B"
        assert hitl_obj.available_actions == ["approve", "request_changes", "reject"]

    @pytest.mark.asyncio
    async def test_already_paused_returns_unchanged(self):
        state = _make_state(status="paused")
        result = await hitl_design_review_b_node(state)
        assert result is state

    @pytest.mark.asyncio
    async def test_db_failure_still_pauses(self):
        state = _make_state(
            artifacts={},
            project={"title": "Fail Design"},
        )

        with patch("src.core.database.get_db_session", side_effect=RuntimeError("DB down")):
            result = await hitl_design_review_b_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        assert result["current_agent"] == "hitl_design_review_b"

    @pytest.mark.asyncio
    async def test_revision_count_in_description(self):
        state = _make_state(
            design_revision=2,
            artifacts={},
            project={"title": "Rev Design"},
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock, return_value=True),
        ):
            await hitl_design_review_b_node(state)

        hitl_obj = mock_session.add.call_args[0][0]
        assert "3/3" in hitl_obj.description  # revision 2 + 1 = round 3

    @pytest.mark.asyncio
    async def test_screenshot_url_extracted(self):
        state = _make_state(
            artifacts={"design_mockup": ['{"screenshot_path": "/tmp/design.png"}']},
            project={"title": "Screenshot Design"},
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.core.database.get_db_session", return_value=mock_ctx),
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock, return_value=True),
        ):
            await hitl_design_review_b_node(state)

        hitl_obj = mock_session.add.call_args[0][0]
        assert hitl_obj.payload["screenshot_url"] == "/tmp/design.png"


# ===========================================================================
# _route_after_design_b_entry
# ===========================================================================


class TestRouteAfterDesignBEntry:
    """Design Agent in Pipeline B -> HITL design review B."""

    def test_active_routes_to_hitl_review(self):
        state = _make_state(status="active")
        assert _route_after_design_b_entry(state) == "hitl_design_review_b_node"

    def test_failed_routes_to_end(self):
        state = _make_state(status="failed")
        assert _route_after_design_b_entry(state) == END


# ===========================================================================
# _route_after_design_b
# ===========================================================================


class TestRouteAfterDesignB:
    """Design Review B HITL routing decisions."""

    def test_approved_operator_routes_to_end(self):
        state = _make_state(
            status="active",
            artifacts={"_design_operator_approved": True},
        )
        assert _route_after_design_b(state) == END

    def test_design_approved_routes_to_end(self):
        state = _make_state(status="active", design_approved=True, artifacts={})
        assert _route_after_design_b(state) == END

    def test_rejected_routes_to_end(self):
        state = _make_state(status="active", next_agent="planner", artifacts={})
        assert _route_after_design_b(state) == END

    def test_request_changes_routes_to_design(self):
        state = _make_state(
            status="active",
            design_revision=1,
            design_feedback="Make it blue",
            artifacts={},
        )
        assert _route_after_design_b(state) == "design_node"

    def test_revision_limit_exceeded_routes_to_end(self):
        state = _make_state(
            status="active",
            design_revision=MAX_DESIGN_REVISIONS_B,
            design_feedback="More changes",
            artifacts={},
        )
        assert _route_after_design_b(state) == END

    def test_revision_at_max_minus_one_still_loops(self):
        state = _make_state(
            status="active",
            design_revision=MAX_DESIGN_REVISIONS_B - 1,
            design_feedback="One more change",
            artifacts={},
        )
        assert _route_after_design_b(state) == "design_node"

    def test_failed_routes_to_end(self):
        state = _make_state(status="failed")
        assert _route_after_design_b(state) == END

    def test_paused_routes_to_end(self):
        state = _make_state(status="paused")
        assert _route_after_design_b(state) == END

    def test_active_no_flags_defaults_to_end(self):
        state = _make_state(status="active", artifacts={})
        assert _route_after_design_b(state) == END


# ===========================================================================
# build_pipeline_b_graph — structural tests
# ===========================================================================


class TestBuildPipelineBGraph:
    """Verify Pipeline B graph compiles and has correct nodes/edges."""

    def test_graph_compiles(self):
        graph = build_pipeline_b_graph()
        assert graph is not None

    def test_graph_has_lead_card_node(self):
        graph = build_pipeline_b_graph()
        assert "hitl_lead_card_node" in graph.nodes

    def test_graph_has_design_node(self):
        graph = build_pipeline_b_graph()
        assert "design_node" in graph.nodes

    def test_graph_has_design_review_b_node(self):
        graph = build_pipeline_b_graph()
        assert "hitl_design_review_b_node" in graph.nodes

    def test_graph_has_all_expected_nodes(self):
        graph = build_pipeline_b_graph()
        node_names = set(graph.nodes)
        expected = {
            "geo_scout_node",
            "outreach_node",
            "hitl_outreach_node",
            "message_dispatch_node",
            "hitl_lead_card_node",
            "sales_agent_node",
            "hitl_concept_review_node",
            "design_node",
            "hitl_design_review_b_node",
        }
        assert expected.issubset(node_names)

    def test_graph_compiles_with_checkpointer(self):
        from langgraph.checkpoint.memory import InMemorySaver

        saver = InMemorySaver()
        graph = build_pipeline_b_graph(checkpointer=saver)
        assert graph is not None


# ===========================================================================
# MAX_DESIGN_REVISIONS_B constant
# ===========================================================================


class TestMaxDesignRevisionsB:
    """Verify the Pipeline B design revision limit."""

    def test_max_revisions_is_three(self):
        assert MAX_DESIGN_REVISIONS_B == 3
