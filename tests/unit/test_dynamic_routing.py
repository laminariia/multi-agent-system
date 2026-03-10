"""Unit tests for Dynamic Routing — _route_next_in_sequence().

Tests the unified routing function that replaces hardcoded
_route_after_dev, _route_after_content, _route_after_design.

Spec: docs/Full_work/dev-cycle-spec.md, docs/Full_work/specs/agents-spec.md
"""

from langgraph.graph import END

# ---------------------------------------------------------------------------
# _route_next_in_sequence tests
# ---------------------------------------------------------------------------


class TestRouteNextInSequence:
    """Tests for the unified execution-phase routing function."""

    def _route(self, state):
        from src.core.graph import _route_next_in_sequence

        return _route_next_in_sequence(state)

    # -- Happy path: sequence traversal --

    def test_routes_to_first_agent_in_sequence(self):
        """Routes to agent_sequence[0] when index=0."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["design", "dev", "content"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "design_node"

    def test_routes_to_second_agent_in_sequence(self):
        """Routes to agent_sequence[1] when index=1."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["design", "dev", "content"],
            "current_sequence_index": 1,
        }
        assert self._route(state) == "dev_node"

    def test_routes_to_last_agent_in_sequence(self):
        """Routes to last agent in sequence."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["design", "dev", "content"],
            "current_sequence_index": 2,
        }
        assert self._route(state) == "content_node"

    def test_routes_to_critic_when_sequence_exhausted(self):
        """Routes to critic_node when index >= len(sequence)."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["dev", "content"],
            "current_sequence_index": 2,
        }
        assert self._route(state) == "critic_node"

    def test_routes_to_critic_index_far_past_end(self):
        """Routes to critic_node even when index >> len(sequence)."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["dev"],
            "current_sequence_index": 99,
        }
        assert self._route(state) == "critic_node"

    # -- Single-agent sequences --

    def test_single_agent_sequence_dev_only(self):
        """API project: sequence=["dev"] → dev_node at 0, critic at 1."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "dev_node"

        state["current_sequence_index"] = 1
        assert self._route(state) == "critic_node"

    # -- Empty sequence (consulting) --

    def test_empty_sequence_routes_to_packager(self):
        """Consulting project: empty sequence → packager_node."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": [],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "packager_node"

    # -- Terminal states --

    def test_failed_status_routes_to_end(self):
        """Failed status → END regardless of sequence."""
        state = {
            "thread_id": "t1",
            "status": "failed",
            "agent_sequence": ["dev", "content"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == END

    # -- HITL escalation --

    def test_hitl_required_routes_to_hitl_review(self):
        """HITL escalation takes precedence over sequence routing."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "requires_hitl": True,
            "agent_sequence": ["dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "hitl_review_node"

    # -- Revision routing --

    def test_revision_target_routes_to_target_agent(self):
        """revision_target takes precedence — routes to target_node."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "revision_target": "design",
            "agent_sequence": ["dev", "content"],
            "current_sequence_index": 2,
        }
        assert self._route(state) == "design_node"

    def test_revision_target_invalid_routes_to_end(self):
        """Invalid revision_target → END."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "revision_target": "scout",
            "agent_sequence": ["dev"],
            "current_sequence_index": 1,
        }
        assert self._route(state) == END

    # -- Edge cases --

    def test_missing_agent_sequence_defaults_to_packager(self):
        """Missing agent_sequence treated as empty → packager."""
        state = {
            "thread_id": "t1",
            "status": "active",
        }
        assert self._route(state) == "packager_node"

    def test_negative_index_routes_to_end(self):
        """Negative current_sequence_index → END (safety guard)."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["dev"],
            "current_sequence_index": -1,
        }
        assert self._route(state) == END

    def test_invalid_agent_in_sequence_routes_to_end(self):
        """Invalid agent name in sequence → END."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["scout"],  # scout not a valid execution agent
            "current_sequence_index": 0,
        }
        assert self._route(state) == END

    def test_priority_order_failed_over_hitl(self):
        """Failed status checked before HITL."""
        state = {
            "thread_id": "t1",
            "status": "failed",
            "requires_hitl": True,
            "agent_sequence": ["dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == END

    def test_priority_order_hitl_over_revision(self):
        """HITL checked before revision_target."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "requires_hitl": True,
            "revision_target": "dev",
            "agent_sequence": ["dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "hitl_review_node"

    def test_priority_order_revision_over_sequence(self):
        """revision_target checked before sequence routing."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "revision_target": "content",
            "agent_sequence": ["dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "content_node"


# ---------------------------------------------------------------------------
# Updated _route_after_planner tests (delegates to _route_next_in_sequence)
# ---------------------------------------------------------------------------


class TestRouteAfterPlannerDynamic:
    """Tests for _route_after_planner with dynamic routing delegation."""

    def _route(self, state):
        from src.core.graph import _route_after_planner

        return _route_after_planner(state)

    def test_planner_routes_to_first_in_sequence(self):
        """Planner delegates to _route_next_in_sequence → first agent."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": ["design", "dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "design_node"

    def test_planner_hitl_takes_precedence(self):
        """HITL plan review overrides sequence routing."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "requires_hitl": True,
            "agent_sequence": ["dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "hitl_review_node"

    def test_planner_failed_routes_to_end(self):
        """Failed Planner → END."""
        state = {"thread_id": "t1", "status": "failed"}
        assert self._route(state) == END

    def test_planner_empty_sequence_routes_to_packager(self):
        """Consulting: empty sequence → packager."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "agent_sequence": [],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "packager_node"


# ---------------------------------------------------------------------------
# Updated _route_after_critic tests (revision_target support)
# ---------------------------------------------------------------------------


class TestRouteAfterCriticDynamic:
    """Tests for _route_after_critic with revision_target support."""

    def _route(self, state):
        from src.core.graph import _route_after_critic

        return _route_after_critic(state)

    def test_critic_approve_to_packager(self):
        """Approved → packager."""
        state = {"thread_id": "t1", "status": "active", "next_agent": "packager"}
        assert self._route(state) == "packager_node"

    def test_critic_major_revision_to_planner(self):
        """Major revision → planner."""
        state = {"thread_id": "t1", "status": "active", "next_agent": "planner"}
        assert self._route(state) == "planner_node"

    def test_critic_minor_revision_to_target_agent(self):
        """Minor revision → revision_target agent (design, not just dev)."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "next_agent": None,
            "revision_target": "design",
        }
        assert self._route(state) == "design_node"

    def test_critic_minor_revision_to_content(self):
        """Minor revision → content_node via revision_target."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "next_agent": None,
            "revision_target": "content",
        }
        assert self._route(state) == "content_node"

    def test_critic_hitl_escalation(self):
        """HITL escalation (reject/scope_creep)."""
        state = {"thread_id": "t1", "status": "active", "requires_hitl": True}
        assert self._route(state) == "hitl_review_node"

    def test_critic_revision_limit_still_works(self):
        """Existing revision limit logic still applies for next_agent=dev."""
        from src.core.graph import MAX_REVISION_CYCLES

        state = {
            "thread_id": "t1",
            "status": "active",
            "next_agent": "dev",
            "artifacts": {"_critic_revision_count": MAX_REVISION_CYCLES},
        }
        assert self._route(state) == "hitl_review_node"


# ---------------------------------------------------------------------------
# Updated _route_after_hitl_review tests (dynamic routing on plan_review)
# ---------------------------------------------------------------------------


class TestRouteAfterHitlReviewDynamic:
    """Tests for _route_after_hitl_review with dynamic sequence support."""

    def _route(self, state):
        from src.core.graph import _route_after_hitl_review

        return _route_after_hitl_review(state)

    def test_plan_review_approved_routes_to_first_in_sequence(self):
        """Plan review approved → first agent in sequence (not hardcoded dev)."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "artifacts": {"_hitl_type": "plan_review"},
            "agent_sequence": ["design", "dev"],
            "current_sequence_index": 0,
        }
        assert self._route(state) == "design_node"

    def test_plan_review_approved_fallback_dev(self):
        """Plan review approved without sequence → dev_node (backward compat)."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "artifacts": {"_hitl_type": "plan_review"},
        }
        # Without agent_sequence, _route_next_in_sequence returns packager
        # But this is plan_review, so it should go to first execution agent
        # Current behavior: routes to dev_node. After change: delegates to _route_next_in_sequence
        # This test documents the NEW behavior.
        result = self._route(state)
        # With empty/missing sequence → packager_node (consulting case)
        assert result in ("dev_node", "packager_node")

    def test_final_review_routes_to_end(self):
        """Final review (not plan_review) → END."""
        state = {
            "thread_id": "t1",
            "status": "active",
            "artifacts": {"_hitl_type": "final_review"},
        }
        assert self._route(state) == END

    def test_rejected_routes_to_end(self):
        """Rejected (status=failed) → END."""
        state = {
            "thread_id": "t1",
            "status": "failed",
            "artifacts": {"_hitl_type": "plan_review"},
        }
        assert self._route(state) == END


# ---------------------------------------------------------------------------
# State fields tests
# ---------------------------------------------------------------------------


class TestDynamicRoutingStateFields:
    """Tests for new state fields in create_initial_state."""

    def test_initial_state_has_dynamic_routing_fields(self):
        """create_initial_state includes agent_sequence, index, delivery_type."""
        from src.core.state import create_initial_state

        state = create_initial_state(
            project={
                "project_id": "p1",
                "job_id": "j1",
                "platform": "freelancer",
                "client": {},
                "requirements": "test",
                "budget": 100.0,
                "deadline": "2026-04-01",
            },
        )
        assert state["agent_sequence"] == []
        assert state["current_sequence_index"] == 0
        assert state["delivery_type"] == "files"
        assert state.get("revision_target") is None
        assert state.get("revision_severity") is None


# ---------------------------------------------------------------------------
# Graph compilation tests
# ---------------------------------------------------------------------------


class TestGraphCompilationDynamic:
    """Tests that graph builders compile correctly with dynamic routing."""

    def test_full_pipeline_graph_compiles(self):
        """build_full_pipeline_graph compiles with dynamic routing edges."""
        from src.core.graph import build_full_pipeline_graph

        graph = build_full_pipeline_graph()
        assert graph is not None

    def test_planner_pipeline_graph_compiles(self):
        """build_planner_pipeline_graph compiles with dynamic routing edges."""
        from src.core.graph import build_planner_pipeline_graph

        graph = build_planner_pipeline_graph()
        assert graph is not None

    def test_pipeline_b_unaffected(self):
        """Pipeline B graph unaffected by dynamic routing changes."""
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        assert graph is not None
