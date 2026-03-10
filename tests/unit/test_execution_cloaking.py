"""Tests for P1.6 — Execution Cloaking (Time-Value Arbitrage).

Covers:
- State fields: real_hours, proposed_days, min_delivery_at, scheduled_messages
- Planner: stores real_hours from total_estimated_hours
- Bid: calculates proposed_days + min_delivery_at
- Packager: enforces delivery schedule (warning when too early)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.state import create_initial_state, update_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_ctx(**overrides: Any) -> dict[str, Any]:
    base = {
        "project_id": "proj-cloak-001",
        "job_id": "job-cloak-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a REST API with authentication",
        "budget": 500.0,
        "deadline": datetime(2026, 4, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = create_initial_state(project=_project_ctx())
    if overrides:
        state = update_state(state, **overrides)
    return state


# ===========================================================================
# 1. State Field Tests
# ===========================================================================


class TestCloakingStateFields:
    """Verify new execution cloaking fields in AgentState."""

    def test_initial_state_has_real_hours_none(self):
        state = create_initial_state(project=_project_ctx())
        assert state.get("real_hours") is None

    def test_initial_state_has_proposed_days_none(self):
        state = create_initial_state(project=_project_ctx())
        assert state.get("proposed_days") is None

    def test_initial_state_has_min_delivery_at_none(self):
        state = create_initial_state(project=_project_ctx())
        assert state.get("min_delivery_at") is None

    def test_initial_state_has_scheduled_messages_empty(self):
        state = create_initial_state(project=_project_ctx())
        assert state.get("scheduled_messages") == []

    def test_update_state_sets_real_hours(self):
        state = _make_state()
        updated = update_state(state, real_hours=2.5)
        assert updated["real_hours"] == 2.5

    def test_update_state_sets_proposed_days(self):
        state = _make_state()
        updated = update_state(state, proposed_days=5)
        assert updated["proposed_days"] == 5

    def test_update_state_sets_min_delivery_at(self):
        dt = datetime(2026, 3, 15, tzinfo=UTC)
        state = _make_state()
        updated = update_state(state, min_delivery_at=dt)
        assert updated["min_delivery_at"] == dt

    def test_update_state_sets_scheduled_messages(self):
        msgs = [{"at": "2026-03-12T00:00:00Z", "text": "Progress update", "sent": False}]
        state = _make_state()
        updated = update_state(state, scheduled_messages=msgs)
        assert updated["scheduled_messages"] == msgs


# ===========================================================================
# 2. Planner — real_hours
# ===========================================================================


class TestPlannerRealHours:
    """Planner stores real_hours from total_estimated_hours."""

    @pytest.fixture()
    def planner_deps(self, mock_llm_client, mock_heartbeat, mock_loop_detector):
        return {
            "llm": mock_llm_client,
            "hb": mock_heartbeat,
            "ld": mock_loop_detector,
        }

    async def test_planner_stores_real_hours_from_plan(self, planner_deps, sample_state):
        """Planner should store total_estimated_hours as real_hours in state."""
        from src.agents.planner import PlannerAgent

        plan = {
            "phases": [
                {
                    "name": "Development",
                    "tasks": [
                        {"name": "Backend API", "agent": "dev", "estimated_hours": 3.0},
                        {"name": "Frontend", "agent": "content", "estimated_hours": 2.0},
                    ],
                }
            ],
            "total_estimated_hours": 5.0,
            "summary": "REST API project",
        }

        agent = PlannerAgent(
            llm_client=planner_deps["llm"],
            heartbeat=planner_deps["hb"],
            loop_detector=planner_deps["ld"],
        )

        state = {**sample_state, "current_agent": "planner"}

        with (
            patch.object(agent, "_generate_plan", new_callable=AsyncMock, return_value=plan),
            patch.object(agent, "_log_planning_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result.get("real_hours") == 5.0

    async def test_planner_real_hours_defaults_zero_on_missing(self, planner_deps, sample_state):
        """If total_estimated_hours is missing, real_hours should be 0.0."""
        from src.agents.planner import PlannerAgent

        plan = {
            "phases": [
                {
                    "name": "Development",
                    "tasks": [
                        {"name": "Task 1", "agent": "dev", "estimated_hours": 1.0},
                    ],
                }
            ],
            "summary": "Simple project",
            # No total_estimated_hours field
        }

        agent = PlannerAgent(
            llm_client=planner_deps["llm"],
            heartbeat=planner_deps["hb"],
            loop_detector=planner_deps["ld"],
        )

        state = {**sample_state, "current_agent": "planner"}

        with (
            patch.object(agent, "_generate_plan", new_callable=AsyncMock, return_value=plan),
            patch.object(agent, "_log_planning_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result.get("real_hours") == 0.0

    async def test_planner_real_hours_handles_none_value(self, planner_deps, sample_state):
        """If total_estimated_hours is None, real_hours should be 0.0."""
        from src.agents.planner import PlannerAgent

        plan = {
            "phases": [
                {
                    "name": "Dev",
                    "tasks": [{"name": "T", "agent": "dev", "estimated_hours": 1.0}],
                }
            ],
            "total_estimated_hours": None,
            "summary": "Project",
        }

        agent = PlannerAgent(
            llm_client=planner_deps["llm"],
            heartbeat=planner_deps["hb"],
            loop_detector=planner_deps["ld"],
        )

        state = {**sample_state, "current_agent": "planner"}

        with (
            patch.object(agent, "_generate_plan", new_callable=AsyncMock, return_value=plan),
            patch.object(agent, "_should_request_plan_review", return_value=False),
            patch.object(agent, "_log_planning_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        assert result.get("real_hours") == 0.0


# ===========================================================================
# 3. Bid Agent — proposed_days + min_delivery_at
# ===========================================================================


class TestBidCloaking:
    """Bid Agent calculates proposed_days and min_delivery_at."""

    @pytest.fixture()
    def bid_deps(self, mock_llm_client, mock_heartbeat, mock_loop_detector):
        return {
            "llm": mock_llm_client,
            "hb": mock_heartbeat,
            "ld": mock_loop_detector,
        }

    async def test_bid_stores_proposed_days(self, bid_deps):
        """Bid should store delivery_days as proposed_days in state."""
        from src.agents.bid import BidAgent

        proposal = {
            "proposal_text": "I will build your API...",
            "bid_amount": 400.0,
            "delivery_days": 5,
            "milestones": [],
            "requires_hitl": True,
        }

        agent = BidAgent(
            llm_client=bid_deps["llm"],
            heartbeat=bid_deps["hb"],
            loop_detector=bid_deps["ld"],
        )

        state = _make_state(
            current_agent="bid",
            artifacts={"scout": ["job-cloak-001"]},
        )

        with (
            patch.object(agent, "_load_jobs", new_callable=AsyncMock) as mock_load,
            patch.object(agent, "_generate_proposal", new_callable=AsyncMock, return_value=proposal),
            patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
            patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-001"),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-001"),
            patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
        ):
            mock_load.return_value = [{"id": "job-cloak-001", "title": "API", "description": "Build API"}]
            result = await agent._execute(state)

        assert result.get("proposed_days") == 5

    async def test_bid_calculates_min_delivery_at(self, bid_deps):
        """Bid should set min_delivery_at = created_at + proposed_days * 0.7."""
        from src.agents.bid import BidAgent

        proposal = {
            "proposal_text": "I will build your API...",
            "bid_amount": 400.0,
            "delivery_days": 10,
            "milestones": [],
            "requires_hitl": True,
        }

        agent = BidAgent(
            llm_client=bid_deps["llm"],
            heartbeat=bid_deps["hb"],
            loop_detector=bid_deps["ld"],
        )

        now = datetime.now(tz=UTC)
        state = _make_state(
            current_agent="bid",
            artifacts={"scout": ["job-cloak-001"]},
            created_at=now,
        )

        with (
            patch.object(agent, "_load_jobs", new_callable=AsyncMock) as mock_load,
            patch.object(agent, "_generate_proposal", new_callable=AsyncMock, return_value=proposal),
            patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
            patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-001"),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-001"),
            patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
        ):
            mock_load.return_value = [{"id": "job-cloak-001", "title": "API", "description": "Build API"}]
            result = await agent._execute(state)

        min_del = result.get("min_delivery_at")
        assert min_del is not None
        # Should be approximately created_at + 10 * 0.7 = 7 days
        expected = now + timedelta(days=10 * 0.7)
        assert abs((min_del - expected).total_seconds()) < 60  # within 1 minute

    async def test_bid_minimum_one_day_delivery(self, bid_deps):
        """Proposed days should never be less than 1."""
        from src.agents.bid import BidAgent

        proposal = {
            "proposal_text": "Quick fix...",
            "bid_amount": 50.0,
            "delivery_days": 0,
            "milestones": [],
            "requires_hitl": True,
        }

        agent = BidAgent(
            llm_client=bid_deps["llm"],
            heartbeat=bid_deps["hb"],
            loop_detector=bid_deps["ld"],
        )

        state = _make_state(
            current_agent="bid",
            artifacts={"scout": ["job-cloak-001"]},
        )

        with (
            patch.object(agent, "_load_jobs", new_callable=AsyncMock) as mock_load,
            patch.object(agent, "_generate_proposal", new_callable=AsyncMock, return_value=proposal),
            patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
            patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-001"),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-001"),
            patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
        ):
            mock_load.return_value = [{"id": "job-cloak-001", "title": "Fix", "description": "Small fix"}]
            result = await agent._execute(state)

        assert result.get("proposed_days", 0) >= 1

    async def test_bid_no_proposal_skips_cloaking(self, bid_deps):
        """If proposal generation fails, cloaking fields remain None."""
        from src.agents.bid import BidAgent

        agent = BidAgent(
            llm_client=bid_deps["llm"],
            heartbeat=bid_deps["hb"],
            loop_detector=bid_deps["ld"],
        )

        state = _make_state(
            current_agent="bid",
            artifacts={"scout": ["job-cloak-001"]},
        )

        with (
            patch.object(agent, "_load_jobs", new_callable=AsyncMock) as mock_load,
            patch.object(agent, "_generate_proposal", new_callable=AsyncMock, return_value=None),
            patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
        ):
            mock_load.return_value = [{"id": "job-cloak-001", "title": "API", "description": "Build API"}]
            result = await agent._execute(state)

        # No proposal generated, so cloaking fields remain at defaults
        assert result.get("proposed_days") is None
        assert result.get("min_delivery_at") is None


# ===========================================================================
# 4. Packager — delivery schedule enforcement
# ===========================================================================


class TestPackagerDeliverySchedule:
    """Packager warns when delivery is too early relative to min_delivery_at."""

    @pytest.fixture()
    def packager_deps(self, mock_llm_client, mock_heartbeat, mock_loop_detector):
        return {
            "llm": mock_llm_client,
            "hb": mock_heartbeat,
            "ld": mock_loop_detector,
        }

    async def test_packager_adds_early_delivery_warning(self, packager_deps):
        """Packager should warn in HITL when delivering before min_delivery_at."""
        from src.agents.packager import PackagerAgent

        delivery_info = {
            "delivery_summary": "API complete",
            "files": ["api.zip"],
            "files_count": 1,
            "instructions": "Deploy and configure",
        }

        agent = PackagerAgent(
            llm_client=packager_deps["llm"],
            heartbeat=packager_deps["hb"],
            loop_detector=packager_deps["ld"],
        )

        # min_delivery_at is 3 days in the future
        future = datetime.now(tz=UTC) + timedelta(days=3)
        state = _make_state(
            current_agent="packager",
            artifacts={"dev": ["code artifact"], "planner": ['{"phases": []}']},
            min_delivery_at=future,
            delivery_type="files",
        )

        with (
            patch.object(agent, "_generate_delivery_package", new_callable=AsyncMock, return_value=delivery_info),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-pkg-1"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        # Check that delivery_hold warning is in artifacts
        packager_artifacts = result.get("artifacts", {}).get("packager", [])
        assert packager_artifacts
        delivery_info = json.loads(packager_artifacts[-1])
        assert delivery_info.get("delivery_hold") is True

    async def test_packager_no_warning_when_past_min_delivery(self, packager_deps):
        """No warning when current time is past min_delivery_at."""
        from src.agents.packager import PackagerAgent

        delivery_info = {
            "delivery_summary": "API complete",
            "files": ["api.zip"],
            "files_count": 1,
            "instructions": "Deploy and configure",
        }

        agent = PackagerAgent(
            llm_client=packager_deps["llm"],
            heartbeat=packager_deps["hb"],
            loop_detector=packager_deps["ld"],
        )

        # min_delivery_at is in the past
        past = datetime.now(tz=UTC) - timedelta(days=1)
        state = _make_state(
            current_agent="packager",
            artifacts={"dev": ["code artifact"], "planner": ['{"phases": []}']},
            min_delivery_at=past,
            delivery_type="files",
        )

        with (
            patch.object(agent, "_generate_delivery_package", new_callable=AsyncMock, return_value=delivery_info),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-pkg-2"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        packager_artifacts = result.get("artifacts", {}).get("packager", [])
        assert packager_artifacts
        delivery_info = json.loads(packager_artifacts[-1])
        assert delivery_info.get("delivery_hold") is not True

    async def test_packager_no_min_delivery_at_backwards_compatible(self, packager_deps):
        """No min_delivery_at → no throttling (backwards-compatible)."""
        from src.agents.packager import PackagerAgent

        delivery_info = {
            "delivery_summary": "API complete",
            "files": ["api.zip"],
            "files_count": 1,
            "instructions": "Deploy and configure",
        }

        agent = PackagerAgent(
            llm_client=packager_deps["llm"],
            heartbeat=packager_deps["hb"],
            loop_detector=packager_deps["ld"],
        )

        state = _make_state(
            current_agent="packager",
            artifacts={"dev": ["code artifact"], "planner": ['{"phases": []}']},
            delivery_type="files",
            # No min_delivery_at
        )

        with (
            patch.object(agent, "_generate_delivery_package", new_callable=AsyncMock, return_value=delivery_info),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-pkg-3"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        packager_artifacts = result.get("artifacts", {}).get("packager", [])
        assert packager_artifacts
        delivery_info = json.loads(packager_artifacts[-1])
        assert delivery_info.get("delivery_hold") is not True

    async def test_packager_early_delivery_hours_in_warning(self, packager_deps):
        """Warning should include remaining hours info."""
        from src.agents.packager import PackagerAgent

        delivery_info = {
            "delivery_summary": "Done",
            "files": ["code.zip"],
            "files_count": 1,
            "instructions": "Run it",
        }

        agent = PackagerAgent(
            llm_client=packager_deps["llm"],
            heartbeat=packager_deps["hb"],
            loop_detector=packager_deps["ld"],
        )

        # 48 hours from now
        future = datetime.now(tz=UTC) + timedelta(hours=48)
        state = _make_state(
            current_agent="packager",
            artifacts={"dev": ["artifact"], "planner": ['{"phases": []}']},
            min_delivery_at=future,
            delivery_type="files",
        )

        with (
            patch.object(agent, "_generate_delivery_package", new_callable=AsyncMock, return_value=delivery_info),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-pkg-4"),
            patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        ):
            result = await agent._execute(state)

        packager_artifacts = result.get("artifacts", {}).get("packager", [])
        delivery_info = json.loads(packager_artifacts[-1])
        assert "delivery_hold_reason" in delivery_info
        assert "hour" in delivery_info["delivery_hold_reason"].lower()


# ===========================================================================
# 5. Scheduled Messages (structure)
# ===========================================================================


class TestScheduledMessages:
    """Scheduled messages structure in state."""

    def test_scheduled_message_structure(self):
        """Verify message structure: at, text, sent."""
        msg = {"at": "2026-03-12T10:00:00Z", "text": "Backend is ready", "sent": False}
        state = _make_state(scheduled_messages=[msg])
        msgs = state["scheduled_messages"]
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Backend is ready"
        assert msgs[0]["sent"] is False

    def test_multiple_scheduled_messages(self):
        """Multiple messages can be stored."""
        msgs = [
            {"at": "2026-03-12T10:00:00Z", "text": "Started development", "sent": False},
            {"at": "2026-03-13T10:00:00Z", "text": "Backend ready, working on frontend", "sent": False},
            {"at": "2026-03-14T10:00:00Z", "text": "Final review, will deliver tomorrow", "sent": False},
        ]
        state = _make_state(scheduled_messages=msgs)
        assert len(state["scheduled_messages"]) == 3

    def test_empty_scheduled_messages_default(self):
        """Default is empty list — backwards-compatible."""
        state = create_initial_state(project=_project_ctx())
        assert state.get("scheduled_messages") == []


# ===========================================================================
# 6. Edge Cases (from code review)
# ===========================================================================


class TestCloakingEdgeCases:
    """Edge cases identified during code review."""

    async def test_packager_naive_datetime_comparison(self):
        """Naive min_delivery_at (from checkpoint deserialization) should not crash."""
        from src.agents.packager import PackagerAgent

        delivery_info: dict[str, Any] = {"delivery_summary": "Test"}
        # Simulate a naive datetime (no tzinfo) from checkpoint deserialization
        naive_future = datetime(2030, 1, 1, 0, 0, 0)  # no tzinfo

        PackagerAgent._check_delivery_schedule(
            {"min_delivery_at": naive_future},
            delivery_info,
        )

        # Should not crash and should add delivery_hold since naive_future is far away
        assert delivery_info.get("delivery_hold") is True

    async def test_packager_exact_boundary_no_hold(self):
        """When now == min_delivery_at, no hold should trigger (condition is now < min_at)."""
        from src.agents.packager import PackagerAgent

        delivery_info: dict[str, Any] = {"delivery_summary": "Test"}
        now = datetime.now(tz=UTC)

        PackagerAgent._check_delivery_schedule(
            {"min_delivery_at": now},
            delivery_info,
        )

        # now is not strictly less than min_at, so no hold
        assert delivery_info.get("delivery_hold") is not True

    async def test_bid_delivery_days_none_fallback(self):
        """delivery_days=None in proposal should fallback to 7."""
        from src.agents.bid import BidAgent

        proposal = {
            "proposal_text": "I will build your API...",
            "bid_amount": 400.0,
            "delivery_days": None,
            "milestones": [],
            "requires_hitl": True,
        }

        agent = BidAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=AsyncMock(),
        )

        state = _make_state(
            current_agent="bid",
            artifacts={"scout": ["job-cloak-001"]},
        )

        with (
            patch.object(agent, "_load_jobs", new_callable=AsyncMock) as mock_load,
            patch.object(agent, "_generate_proposal", new_callable=AsyncMock, return_value=proposal),
            patch.object(agent, "_fetch_similar_bids", new_callable=AsyncMock, return_value=[]),
            patch.object(agent, "_store_bid", new_callable=AsyncMock, return_value="bid-001"),
            patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-001"),
            patch.object(agent, "_log_bid_generated", new_callable=AsyncMock),
        ):
            mock_load.return_value = [{"id": "job-cloak-001", "title": "API", "description": "Build"}]
            result = await agent._execute(state)

        # delivery_days=None → fallback to 7 via try/except
        assert result.get("proposed_days") == 7
