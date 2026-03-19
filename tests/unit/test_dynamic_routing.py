"""Tests for dynamic agent_sequence routing.

Verifies that _route_next_in_sequence() correctly routes agents based on
the dynamic agent_sequence set by the Planner, and that Dev/Content/Design
routing functions delegate to it properly.
"""

from langgraph.graph import END

from src.core.graph import (
    _route_after_content,
    _route_after_design,
    _route_after_dev,
    _route_next_in_sequence,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_state(
    sequence=None,
    index=0,
    revision_severity=None,
    revision_target=None,
    status="active",
    **kwargs,
):
    """Build a minimal state dict for routing tests."""
    return {
        "thread_id": "test-thread-1",
        "agent_sequence": sequence or [],
        "current_sequence_index": index,
        "revision_severity": revision_severity,
        "revision_target": revision_target,
        "status": status,
        "next_agent": None,
        **kwargs,
    }


# ===========================================================================
# _route_next_in_sequence -- core routing logic
# ===========================================================================


class TestRouteNextInSequence:
    """Unit tests for the unified execution-phase routing function."""

    # -----------------------------------------------------------------------
    # 1. test_sequence_dev_only_skips_content_design
    # -----------------------------------------------------------------------

    def test_sequence_dev_only_skips_content_design(self):
        """sequence=["dev"], index=1 (after Dev completes) -> critic_node.

        Content and Design are not in the sequence and must be skipped.
        """
        state = _make_state(sequence=["dev"], index=1)
        assert _route_next_in_sequence(state) == "critic_node"

    # -----------------------------------------------------------------------
    # 2. test_sequence_dev_content_skips_design
    # -----------------------------------------------------------------------

    def test_sequence_dev_content_skips_design(self):
        """sequence=["dev", "content"], index=2 (after Content) -> critic_node.

        Design is not in the sequence and must be skipped.
        """
        state = _make_state(sequence=["dev", "content"], index=2)
        assert _route_next_in_sequence(state) == "critic_node"

    # -----------------------------------------------------------------------
    # 3. test_sequence_design_only
    # -----------------------------------------------------------------------

    def test_sequence_design_only(self):
        """sequence=["design"], index=1 (after Design) -> critic_node."""
        state = _make_state(sequence=["design"], index=1)
        assert _route_next_in_sequence(state) == "critic_node"

    def test_sequence_design_only_at_index_zero(self):
        """sequence=["design"], index=0 -> design_node (routes TO design)."""
        state = _make_state(sequence=["design"], index=0)
        assert _route_next_in_sequence(state) == "design_node"

    # -----------------------------------------------------------------------
    # 4. test_empty_sequence_goes_to_packager
    # -----------------------------------------------------------------------

    def test_empty_sequence_goes_to_packager(self):
        """Empty agent_sequence -> packager_node (consulting project)."""
        state = _make_state(sequence=[])
        assert _route_next_in_sequence(state) == "packager_node"

    def test_missing_sequence_key_goes_to_packager(self):
        """State without agent_sequence key at all -> packager_node."""
        state = {"thread_id": "t1", "status": "active"}
        assert _route_next_in_sequence(state) == "packager_node"

    # -----------------------------------------------------------------------
    # 5. test_full_sequence_dev_content_design
    # -----------------------------------------------------------------------

    def test_full_sequence_dev_content_design_index_0(self):
        """Full sequence at index=0 -> routes to dev_node (first agent)."""
        state = _make_state(sequence=["dev", "content", "design"], index=0)
        assert _route_next_in_sequence(state) == "dev_node"

    def test_full_sequence_dev_content_design_index_1(self):
        """Full sequence at index=1 -> routes to content_node (second agent)."""
        state = _make_state(sequence=["dev", "content", "design"], index=1)
        assert _route_next_in_sequence(state) == "content_node"

    def test_full_sequence_dev_content_design_index_2(self):
        """Full sequence at index=2 -> routes to design_node (third agent)."""
        state = _make_state(sequence=["dev", "content", "design"], index=2)
        assert _route_next_in_sequence(state) == "design_node"

    def test_full_sequence_dev_content_design_exhausted(self):
        """Full sequence at index=3 (past end) -> routes to critic_node."""
        state = _make_state(sequence=["dev", "content", "design"], index=3)
        assert _route_next_in_sequence(state) == "critic_node"

    # -----------------------------------------------------------------------
    # 6. test_revision_severity_prevents_index_increment_dev
    #    NOTE: revision_severity is a state field used by agents to decide
    #    whether to advance the index. The routing function itself does not
    #    inspect revision_severity -- it routes based on the current index.
    #    When revision_severity is set, the agent keeps the same index, so
    #    the routing function sees the same index and routes back to the
    #    same agent node.
    # -----------------------------------------------------------------------

    def test_revision_severity_prevents_index_increment_dev(self):
        """With revision_severity set, Dev keeps same index -> routes to dev_node again.

        The agent is responsible for NOT incrementing current_sequence_index
        when revision_severity is present. The routing function sees index=0
        and routes to sequence[0] = "dev".
        """
        state = _make_state(
            sequence=["dev", "content", "design"],
            index=0,
            revision_severity="minor",
        )
        # Index was not advanced by the agent, so routing returns dev_node
        assert _route_next_in_sequence(state) == "dev_node"

    # -----------------------------------------------------------------------
    # 7. test_revision_severity_prevents_index_increment_content
    # -----------------------------------------------------------------------

    def test_revision_severity_prevents_index_increment_content(self):
        """With revision_severity set, Content keeps same index -> routes to content_node.

        Same principle as Dev: the agent does not advance the index.
        """
        state = _make_state(
            sequence=["dev", "content", "design"],
            index=1,
            revision_severity="minor",
        )
        assert _route_next_in_sequence(state) == "content_node"

    # -----------------------------------------------------------------------
    # 8. test_revision_target_routes_to_specific_agent
    # -----------------------------------------------------------------------

    def test_revision_target_routes_to_specific_agent(self):
        """revision_target="content" -> routes to content_node regardless of index."""
        state = _make_state(
            sequence=["dev", "content", "design"],
            index=3,
            revision_target="content",
        )
        assert _route_next_in_sequence(state) == "content_node"

    def test_revision_target_routes_to_dev(self):
        """revision_target="dev" -> routes to dev_node."""
        state = _make_state(
            sequence=["dev", "content"],
            index=2,
            revision_target="dev",
        )
        assert _route_next_in_sequence(state) == "dev_node"

    def test_revision_target_routes_to_design(self):
        """revision_target="design" -> routes to design_node."""
        state = _make_state(
            sequence=["dev"],
            index=1,
            revision_target="design",
        )
        assert _route_next_in_sequence(state) == "design_node"

    def test_revision_target_invalid_routes_to_end(self):
        """Invalid revision_target (e.g. "scout") -> END (not a valid execution node)."""
        state = _make_state(
            sequence=["dev"],
            index=0,
            revision_target="scout",
        )
        assert _route_next_in_sequence(state) == END

    # -----------------------------------------------------------------------
    # 12. test_index_out_of_bounds_routes_to_critic
    # -----------------------------------------------------------------------

    def test_index_out_of_bounds_routes_to_critic(self):
        """current_sequence_index=99, sequence=["dev"] -> critic_node."""
        state = _make_state(sequence=["dev"], index=99)
        assert _route_next_in_sequence(state) == "critic_node"


# ===========================================================================
# _route_after_dev / _route_after_content / _route_after_design -- delegation
# ===========================================================================


class TestRouteAfterDevDelegation:
    """Tests for _route_after_dev delegating to _route_next_in_sequence."""

    # -----------------------------------------------------------------------
    # 9. test_route_after_dev_failed_returns_end
    # -----------------------------------------------------------------------

    def test_route_after_dev_failed_returns_end(self):
        """status="failed" -> returns END."""
        state = _make_state(sequence=["dev", "content"], index=0, status="failed")
        assert _route_after_dev(state) == END

    def test_route_after_dev_delegates_to_sequence(self):
        """Dev delegates to _route_next_in_sequence for normal routing."""
        state = _make_state(sequence=["dev", "content", "design"], index=1)
        assert _route_after_dev(state) == "content_node"

    def test_route_after_dev_sequence_exhausted(self):
        """Dev at end of sequence -> critic_node."""
        state = _make_state(sequence=["dev"], index=1)
        assert _route_after_dev(state) == "critic_node"

    def test_route_after_dev_empty_sequence(self):
        """Dev with empty sequence -> packager_node."""
        state = _make_state(sequence=[], index=0)
        assert _route_after_dev(state) == "packager_node"

    def test_route_after_dev_revision_target(self):
        """Dev with revision_target -> routes to target agent."""
        state = _make_state(
            sequence=["dev", "content"],
            index=1,
            revision_target="dev",
        )
        assert _route_after_dev(state) == "dev_node"


class TestRouteAfterContentDelegation:
    """Tests for _route_after_content delegating to _route_next_in_sequence."""

    # -----------------------------------------------------------------------
    # 10. test_route_after_content_failed_returns_end
    # -----------------------------------------------------------------------

    def test_route_after_content_failed_returns_end(self):
        """status="failed" -> returns END."""
        state = _make_state(sequence=["dev", "content"], index=1, status="failed")
        assert _route_after_content(state) == END

    def test_route_after_content_delegates_to_sequence(self):
        """Content delegates to _route_next_in_sequence for normal routing."""
        state = _make_state(sequence=["dev", "content", "design"], index=2)
        assert _route_after_content(state) == "design_node"

    def test_route_after_content_sequence_exhausted(self):
        """Content at end of sequence -> critic_node."""
        state = _make_state(sequence=["dev", "content"], index=2)
        assert _route_after_content(state) == "critic_node"

    def test_route_after_content_revision_target(self):
        """Content with revision_target -> routes to target agent."""
        state = _make_state(
            sequence=["dev", "content"],
            index=2,
            revision_target="content",
        )
        assert _route_after_content(state) == "content_node"


class TestRouteAfterDesignDelegation:
    """Tests for _route_after_design delegating to _route_next_in_sequence."""

    # -----------------------------------------------------------------------
    # 11. test_route_after_design_failed_returns_end
    # -----------------------------------------------------------------------

    def test_route_after_design_failed_returns_end(self):
        """status="failed" -> returns END."""
        state = _make_state(sequence=["dev", "design"], index=1, status="failed")
        assert _route_after_design(state) == END

    def test_route_after_design_delegates_to_sequence(self):
        """Design delegates to _route_next_in_sequence for normal routing."""
        state = _make_state(sequence=["dev", "content", "design"], index=3)
        assert _route_after_design(state) == "critic_node"

    def test_route_after_design_revision_target(self):
        """Design with revision_target -> routes to target agent."""
        state = _make_state(
            sequence=["dev", "design"],
            index=2,
            revision_target="design",
        )
        assert _route_after_design(state) == "design_node"


# ===========================================================================
# Priority ordering
# ===========================================================================


class TestRoutingPriorityOrder:
    """Verify the documented priority order in _route_next_in_sequence.

    Priority:
        1. status == "failed" -> END
        2. requires_hitl -> hitl_review_node
        3. revision_target -> target agent node
        4. empty sequence -> packager_node
        5. index >= len(sequence) -> critic_node
        6. sequence[index] -> next agent node
    """

    def test_failed_beats_hitl(self):
        """Failed status takes priority over HITL."""
        state = _make_state(
            sequence=["dev"],
            index=0,
            status="failed",
            requires_hitl=True,
        )
        assert _route_next_in_sequence(state) == END

    def test_hitl_beats_revision_target(self):
        """HITL takes priority over revision_target."""
        state = _make_state(
            sequence=["dev"],
            index=0,
            revision_target="content",
            requires_hitl=True,
        )
        assert _route_next_in_sequence(state) == "hitl_review_node"

    def test_revision_target_beats_sequence(self):
        """revision_target takes priority over normal sequence routing."""
        state = _make_state(
            sequence=["dev"],
            index=0,
            revision_target="content",
        )
        assert _route_next_in_sequence(state) == "content_node"

    def test_failed_beats_everything(self):
        """Failed status with all flags set still returns END."""
        state = _make_state(
            sequence=["dev", "content"],
            index=0,
            status="failed",
            requires_hitl=True,
            revision_target="content",
        )
        assert _route_next_in_sequence(state) == END


# ===========================================================================
# Skipped agents
# ===========================================================================


class TestSkippedAgents:
    """Verify that skipped_agents are bypassed during sequence traversal."""

    def test_skipped_agent_advances_past(self):
        """Agent in skipped_agents is bypassed, routing to the next valid agent."""
        state = _make_state(
            sequence=["dev", "content", "design"],
            index=0,
            skipped_agents=["dev"],
        )
        assert _route_next_in_sequence(state) == "content_node"

    def test_all_remaining_skipped_goes_to_critic(self):
        """When all remaining agents are skipped, routes to critic_node."""
        state = _make_state(
            sequence=["dev", "content"],
            index=0,
            skipped_agents=["dev", "content"],
        )
        assert _route_next_in_sequence(state) == "critic_node"

    def test_skipped_in_middle(self):
        """Skipping a middle agent advances past it."""
        state = _make_state(
            sequence=["dev", "content", "design"],
            index=1,
            skipped_agents=["content"],
        )
        assert _route_next_in_sequence(state) == "design_node"


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Additional edge cases for _route_next_in_sequence."""

    def test_negative_index_routes_to_end(self):
        """Negative current_sequence_index -> END (safety guard)."""
        state = _make_state(sequence=["dev"], index=-1)
        assert _route_next_in_sequence(state) == END

    def test_invalid_agent_name_in_sequence_routes_to_end(self):
        """Invalid agent name (e.g. "scout") in sequence -> END."""
        state = _make_state(sequence=["scout"], index=0)
        assert _route_next_in_sequence(state) == END

    def test_hitl_required_routes_to_hitl_review(self):
        """requires_hitl=True -> hitl_review_node."""
        state = _make_state(
            sequence=["dev"],
            index=0,
            requires_hitl=True,
        )
        assert _route_next_in_sequence(state) == "hitl_review_node"


# ===========================================================================
# State fields from create_initial_state
# ===========================================================================


class TestDynamicRoutingStateFields:
    """Tests for dynamic routing fields in create_initial_state."""

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
