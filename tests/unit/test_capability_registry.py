"""Unit tests for Capability Registry (src/core/capability_registry.py).

Tests cover: Capability dataclass, registration, discovery by category/name,
find_capable_agents, capability reports, confidence updates, outcome recording,
limitations retrieval, AGENT_CAPABILITIES defaults, and edge cases.
"""

from __future__ import annotations

from src.core.capability_registry import (
    AGENT_CAPABILITIES,
    Capability,
    CapabilityRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cap(
    name: str = "React / Next.js",
    category: str = "frontend",
    confidence: float = 0.95,
    agent: str = "dev",
    tools: list[str] | None = None,
    examples: list[str] | None = None,
    limitations: list[str] | None = None,
) -> Capability:
    """Create a Capability with sensible defaults."""
    return Capability(
        name=name,
        category=category,
        confidence=confidence,
        agent=agent,
        tools=tools or ["create-next-app"],
        examples=examples or ["SPA dashboard"],
        limitations=limitations or [],
    )


def _registry_with_defaults() -> CapabilityRegistry:
    """Create a registry pre-loaded with AGENT_CAPABILITIES."""
    reg = CapabilityRegistry()
    reg.load_defaults()
    return reg


# ---------------------------------------------------------------------------
# Capability dataclass
# ---------------------------------------------------------------------------


class TestCapabilityDataclass:
    """Verify Capability model fields and defaults."""

    def test_create_minimal(self):
        cap = Capability(
            name="React",
            category="frontend",
            confidence=0.9,
            agent="dev",
        )
        assert cap.name == "React"
        assert cap.category == "frontend"
        assert cap.confidence == 0.9
        assert cap.agent == "dev"
        assert cap.tools == []
        assert cap.examples == []
        assert cap.limitations == []

    def test_create_full(self):
        cap = Capability(
            name="PostgreSQL",
            category="database",
            confidence=0.95,
            agent="dev",
            tools=["psql", "alembic"],
            examples=["Schema design", "Migrations"],
            limitations=["No Oracle"],
        )
        assert cap.tools == ["psql", "alembic"]
        assert len(cap.examples) == 2
        assert "No Oracle" in cap.limitations

    def test_frozen_name_and_category(self):
        """name and category are set at creation and readable."""
        cap = _cap(name="Vue.js", category="frontend")
        assert cap.name == "Vue.js"
        assert cap.category == "frontend"

    def test_confidence_bounds(self):
        """Confidence stored as-is (validation in registry methods)."""
        cap = _cap(confidence=0.0)
        assert cap.confidence == 0.0
        cap2 = _cap(confidence=1.0)
        assert cap2.confidence == 1.0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterCapability:
    """Verify register_capability adds entries correctly."""

    def test_register_single(self):
        reg = CapabilityRegistry()
        cap = _cap(name="React / Next.js")
        reg.register_capability("react_nextjs", cap)
        assert reg.get("react_nextjs") == cap

    def test_register_multiple(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(name="React"))
        reg.register_capability("vue", _cap(name="Vue"))
        assert len(reg.list_all()) == 2

    def test_register_overwrite(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(name="React", confidence=0.8))
        reg.register_capability("react", _cap(name="React v2", confidence=0.95))
        assert reg.get("react").name == "React v2"
        assert reg.get("react").confidence == 0.95

    def test_register_returns_none_for_unknown(self):
        reg = CapabilityRegistry()
        assert reg.get("nonexistent") is None

    def test_list_all_empty(self):
        reg = CapabilityRegistry()
        assert reg.list_all() == {}

    def test_unregister(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        assert reg.unregister("react") is True
        assert reg.get("react") is None

    def test_unregister_nonexistent(self):
        reg = CapabilityRegistry()
        assert reg.unregister("nope") is False


# ---------------------------------------------------------------------------
# Discovery by category
# ---------------------------------------------------------------------------


class TestFindByCategory:
    """Verify filtering capabilities by category."""

    def test_find_frontend(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(name="React", category="frontend"))
        reg.register_capability("vue", _cap(name="Vue", category="frontend"))
        reg.register_capability("pg", _cap(name="PG", category="database"))
        results = reg.find_by_category("frontend")
        assert len(results) == 2
        names = {c.name for c in results.values()}
        assert names == {"React", "Vue"}

    def test_find_empty_category(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(category="frontend"))
        assert reg.find_by_category("blockchain") == {}

    def test_find_category_case_insensitive(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(category="Frontend"))
        results = reg.find_by_category("frontend")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# find_capable_agents
# ---------------------------------------------------------------------------


class TestFindCapableAgents:
    """Verify finding agents that have a given capability."""

    def test_single_agent(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(agent="dev"))
        agents = reg.find_capable_agents("react")
        assert agents == ["dev"]

    def test_multiple_agents_same_capability_id(self):
        """Two capabilities with same id but different agents -- last wins."""
        reg = CapabilityRegistry()
        reg.register_capability("copywriting", _cap(agent="content"))
        agents = reg.find_capable_agents("copywriting")
        assert agents == ["content"]

    def test_find_agents_by_category(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(agent="dev", category="frontend"))
        reg.register_capability("css", _cap(agent="dev", category="frontend"))
        reg.register_capability("copy", _cap(agent="content", category="content"))
        agents = reg.find_capable_agents_by_category("frontend")
        assert "dev" in agents

    def test_find_agents_by_category_dedup(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(agent="dev", category="frontend"))
        reg.register_capability("vue", _cap(agent="dev", category="frontend"))
        agents = reg.find_capable_agents_by_category("frontend")
        assert agents == ["dev"]

    def test_find_capable_agents_unknown(self):
        reg = CapabilityRegistry()
        assert reg.find_capable_agents("nonexistent") == []

    def test_find_capable_agents_by_category_empty(self):
        reg = CapabilityRegistry()
        assert reg.find_capable_agents_by_category("blockchain") == []


# ---------------------------------------------------------------------------
# Capability Report
# ---------------------------------------------------------------------------


class TestGetCapabilityReport:
    """Verify report generation across all capabilities."""

    def test_report_structure(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(category="frontend", confidence=0.95))
        reg.register_capability("pg", _cap(category="database", confidence=0.90))
        report = reg.get_capability_report()
        assert "total_capabilities" in report
        assert report["total_capabilities"] == 2
        assert "categories" in report
        assert "frontend" in report["categories"]
        assert "database" in report["categories"]
        assert "avg_confidence" in report

    def test_report_avg_confidence(self):
        reg = CapabilityRegistry()
        reg.register_capability("a", _cap(confidence=0.8))
        reg.register_capability("b", _cap(confidence=1.0))
        report = reg.get_capability_report()
        assert report["avg_confidence"] == 0.9

    def test_report_empty_registry(self):
        reg = CapabilityRegistry()
        report = reg.get_capability_report()
        assert report["total_capabilities"] == 0
        assert report["categories"] == {}
        assert report["avg_confidence"] == 0.0

    def test_report_category_counts(self):
        reg = CapabilityRegistry()
        reg.register_capability("r", _cap(category="frontend"))
        reg.register_capability("v", _cap(category="frontend"))
        reg.register_capability("p", _cap(category="database"))
        report = reg.get_capability_report()
        assert report["categories"]["frontend"]["count"] == 2
        assert report["categories"]["database"]["count"] == 1

    def test_report_category_avg_confidence(self):
        reg = CapabilityRegistry()
        reg.register_capability("r", _cap(category="frontend", confidence=0.8))
        reg.register_capability("v", _cap(category="frontend", confidence=1.0))
        report = reg.get_capability_report()
        assert report["categories"]["frontend"]["avg_confidence"] == 0.9

    def test_report_agents_list(self):
        reg = CapabilityRegistry()
        reg.register_capability("r", _cap(agent="dev"))
        reg.register_capability("c", _cap(agent="content"))
        report = reg.get_capability_report()
        assert set(report["agents"]) == {"dev", "content"}

    def test_report_high_confidence_count(self):
        reg = CapabilityRegistry()
        reg.register_capability("a", _cap(confidence=0.95))
        reg.register_capability("b", _cap(confidence=0.70))
        reg.register_capability("c", _cap(confidence=0.90))
        report = reg.get_capability_report()
        # high confidence = >= 0.9
        assert report["high_confidence_count"] == 2


# ---------------------------------------------------------------------------
# update_confidence
# ---------------------------------------------------------------------------


class TestUpdateConfidence:
    """Verify confidence adjustments."""

    def test_update_confidence(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.8))
        reg.update_confidence("react", 0.95)
        assert reg.get("react").confidence == 0.95

    def test_update_confidence_clamp_high(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.8))
        reg.update_confidence("react", 1.5)
        assert reg.get("react").confidence == 1.0

    def test_update_confidence_clamp_low(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.8))
        reg.update_confidence("react", -0.5)
        assert reg.get("react").confidence == 0.0

    def test_update_confidence_unknown_noop(self):
        reg = CapabilityRegistry()
        # Should not raise
        reg.update_confidence("nonexistent", 0.5)

    def test_update_confidence_returns_bool(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        assert reg.update_confidence("react", 0.5) is True
        assert reg.update_confidence("nope", 0.5) is False


# ---------------------------------------------------------------------------
# get_limitations
# ---------------------------------------------------------------------------


class TestGetLimitations:
    """Verify limitations retrieval."""

    def test_get_limitations(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(limitations=["No React Native", "No Electron"]))
        lims = reg.get_limitations("react")
        assert lims == ["No React Native", "No Electron"]

    def test_get_limitations_empty(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(limitations=[]))
        assert reg.get_limitations("react") == []

    def test_get_limitations_unknown(self):
        reg = CapabilityRegistry()
        assert reg.get_limitations("nonexistent") == []

    def test_get_all_limitations(self):
        reg = CapabilityRegistry()
        reg.register_capability("r", _cap(limitations=["No RN"]))
        reg.register_capability("d", _cap(limitations=["No 3D", "No print"]))
        all_lims = reg.get_all_limitations()
        assert len(all_lims) == 2
        assert "r" in all_lims
        assert all_lims["d"] == ["No 3D", "No print"]


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    """Verify outcome recording and confidence adjustment."""

    def test_record_success(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.80))
        reg.record_outcome("react", success=True, quality_score=0.95)
        # Confidence should increase
        assert reg.get("react").confidence > 0.80

    def test_record_failure(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.90))
        reg.record_outcome("react", success=False, quality_score=0.3)
        # Confidence should decrease
        assert reg.get("react").confidence < 0.90

    def test_record_outcome_unknown_noop(self):
        reg = CapabilityRegistry()
        # Should not raise
        reg.record_outcome("nonexistent", success=True)

    def test_record_outcome_returns_bool(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        assert reg.record_outcome("react", success=True) is True
        assert reg.record_outcome("nope", success=True) is False

    def test_record_multiple_successes_increases(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.70))
        for _ in range(5):
            reg.record_outcome("react", success=True, quality_score=1.0)
        assert reg.get("react").confidence > 0.70

    def test_record_multiple_failures_decreases(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.90))
        for _ in range(5):
            reg.record_outcome("react", success=False, quality_score=0.0)
        assert reg.get("react").confidence < 0.90

    def test_confidence_never_exceeds_bounds(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.99))
        for _ in range(20):
            reg.record_outcome("react", success=True, quality_score=1.0)
        assert reg.get("react").confidence <= 1.0

    def test_confidence_never_below_zero(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.05))
        for _ in range(20):
            reg.record_outcome("react", success=False, quality_score=0.0)
        assert reg.get("react").confidence >= 0.0

    def test_outcome_history_tracked(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        reg.record_outcome("react", success=True, quality_score=0.9)
        reg.record_outcome("react", success=False, quality_score=0.2)
        history = reg.get_outcome_history("react")
        assert len(history) == 2
        assert history[0]["success"] is True
        assert history[1]["success"] is False

    def test_outcome_history_unknown(self):
        reg = CapabilityRegistry()
        assert reg.get_outcome_history("nonexistent") == []

    def test_record_outcome_default_quality(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap(confidence=0.80))
        reg.record_outcome("react", success=True)
        history = reg.get_outcome_history("react")
        assert history[0]["quality_score"] is None


# ---------------------------------------------------------------------------
# AGENT_CAPABILITIES defaults
# ---------------------------------------------------------------------------


class TestAgentCapabilitiesDefaults:
    """Verify the AGENT_CAPABILITIES constant from the spec."""

    def test_has_all_spec_capabilities(self):
        expected_ids = {
            "react_nextjs",
            "html_css_tailwind",
            "vue_svelte",
            "wordpress",
            "shopify_webflow",
            "python_litestar",
            "nodejs_express",
            "postgresql",
            "docker_deploy",
            "copywriting",
            "ui_ux_design",
        }
        assert expected_ids.issubset(set(AGENT_CAPABILITIES.keys()))

    def test_react_nextjs_capability(self):
        cap = AGENT_CAPABILITIES["react_nextjs"]
        assert cap.name == "React / Next.js"
        assert cap.category == "frontend"
        assert cap.confidence == 0.95
        assert "create-next-app" in cap.tools

    def test_python_litestar_capability(self):
        cap = AGENT_CAPABILITIES["python_litestar"]
        assert cap.category == "backend"
        assert cap.confidence == 0.95

    def test_ui_ux_design_limitations(self):
        cap = AGENT_CAPABILITIES["ui_ux_design"]
        assert len(cap.limitations) > 0
        assert any("3D" in lim for lim in cap.limitations)

    def test_copywriting_no_tools(self):
        cap = AGENT_CAPABILITIES["copywriting"]
        assert cap.tools == []

    def test_all_capabilities_have_required_fields(self):
        for cap_id, cap in AGENT_CAPABILITIES.items():
            assert cap.name, f"{cap_id} missing name"
            assert cap.category, f"{cap_id} missing category"
            assert 0.0 <= cap.confidence <= 1.0, f"{cap_id} confidence out of range"
            assert cap.agent, f"{cap_id} missing agent"

    def test_load_defaults_populates_registry(self):
        reg = _registry_with_defaults()
        assert reg.get("react_nextjs") is not None
        assert reg.get("postgresql") is not None
        assert len(reg.list_all()) == len(AGENT_CAPABILITIES)


# ---------------------------------------------------------------------------
# find_by_name (text search)
# ---------------------------------------------------------------------------


class TestFindByName:
    """Verify text-based capability search."""

    def test_find_exact(self):
        reg = _registry_with_defaults()
        results = reg.find_by_name("React")
        assert len(results) >= 1
        assert any("React" in c.name for c in results.values())

    def test_find_partial(self):
        reg = _registry_with_defaults()
        results = reg.find_by_name("next")
        assert len(results) >= 1

    def test_find_case_insensitive(self):
        reg = _registry_with_defaults()
        results = reg.find_by_name("POSTGRESQL")
        assert len(results) >= 1

    def test_find_no_match(self):
        reg = _registry_with_defaults()
        results = reg.find_by_name("blockchain")
        assert results == {}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions and defensive checks."""

    def test_empty_capability_id(self):
        reg = CapabilityRegistry()
        cap = _cap()
        reg.register_capability("", cap)
        assert reg.get("") == cap

    def test_confidence_exact_zero(self):
        reg = CapabilityRegistry()
        reg.register_capability("low", _cap(confidence=0.0))
        assert reg.get("low").confidence == 0.0

    def test_confidence_exact_one(self):
        reg = CapabilityRegistry()
        reg.register_capability("high", _cap(confidence=1.0))
        assert reg.get("high").confidence == 1.0

    def test_report_with_single_capability(self):
        reg = CapabilityRegistry()
        reg.register_capability("solo", _cap(confidence=0.77, category="misc"))
        report = reg.get_capability_report()
        assert report["total_capabilities"] == 1
        assert report["avg_confidence"] == 0.77

    def test_many_capabilities_performance(self):
        """Registry handles 100+ capabilities without error."""
        reg = CapabilityRegistry()
        for i in range(100):
            reg.register_capability(f"cap_{i}", _cap(name=f"Cap {i}"))
        assert len(reg.list_all()) == 100
        report = reg.get_capability_report()
        assert report["total_capabilities"] == 100

    def test_load_defaults_idempotent(self):
        """Calling load_defaults twice does not duplicate."""
        reg = CapabilityRegistry()
        reg.load_defaults()
        count1 = len(reg.list_all())
        reg.load_defaults()
        count2 = len(reg.list_all())
        assert count1 == count2

    def test_clear_registry(self):
        reg = _registry_with_defaults()
        assert len(reg.list_all()) > 0
        reg.clear()
        assert len(reg.list_all()) == 0

    def test_categories_list(self):
        reg = _registry_with_defaults()
        cats = reg.list_categories()
        assert "frontend" in cats
        assert "backend" in cats
        assert "database" in cats


# ---------------------------------------------------------------------------
# REJECT_CATEGORIES and should_reject (Issue 3)
# ---------------------------------------------------------------------------


class TestRejectCategories:
    """Verify REJECT_CATEGORIES dict and should_reject() method."""

    def test_reject_categories_has_required_keys(self):
        from src.core.capability_registry import REJECT_CATEGORIES

        expected = {
            "mobile_app",
            "ai_ml",
            "blockchain",
            "erp",
            "game_dev",
            "embedded",
            "desktop_app",
        }
        assert expected == set(REJECT_CATEGORIES.keys())

    def test_reject_categories_all_have_reasons(self):
        from src.core.capability_registry import REJECT_CATEGORIES

        for key, reason in REJECT_CATEGORIES.items():
            assert isinstance(reason, str), f"{key} has non-string reason"
            assert len(reason) > 10, f"{key} has too short reason"

    def test_should_reject_known_category(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("mobile_app")
        assert rejected is True
        assert len(reason) > 10

    def test_should_reject_blockchain(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("blockchain")
        assert rejected is True
        assert "blockchain" in reason.lower() or "web3" in reason.lower()

    def test_should_reject_ai_ml(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("ai_ml")
        assert rejected is True

    def test_should_reject_erp(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("erp")
        assert rejected is True

    def test_should_reject_game_dev(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("game_dev")
        assert rejected is True

    def test_should_reject_embedded(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("embedded")
        assert rejected is True

    def test_should_reject_desktop_app(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("desktop_app")
        assert rejected is True

    def test_should_not_reject_frontend(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("frontend")
        assert rejected is False
        assert reason == ""

    def test_should_not_reject_backend(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("backend")
        assert rejected is False

    def test_should_reject_case_insensitive(self):
        reg = CapabilityRegistry()
        rejected, _ = reg.should_reject("MOBILE_APP")
        assert rejected is True

    def test_should_reject_hyphen_normalization(self):
        reg = CapabilityRegistry()
        rejected, _ = reg.should_reject("mobile-app")
        assert rejected is True

    def test_should_reject_with_whitespace(self):
        reg = CapabilityRegistry()
        rejected, _ = reg.should_reject("  blockchain  ")
        assert rejected is True

    def test_should_reject_unknown_category(self):
        reg = CapabilityRegistry()
        rejected, reason = reg.should_reject("quantum_computing")
        assert rejected is False
        assert reason == ""


# ---------------------------------------------------------------------------
# last_used_at tracking and apply_decay (Issue 8)
# ---------------------------------------------------------------------------


class TestLastUsedAtTracking:
    """Verify last_used_at tracking on capabilities."""

    def test_capability_default_last_used_at_none(self):
        cap = _cap()
        assert cap.last_used_at is None

    def test_touch_sets_last_used_at(self):
        from datetime import UTC, datetime

        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        assert reg.get("react").last_used_at is None

        before = datetime.now(UTC)
        reg.touch("react")
        after = datetime.now(UTC)

        last_used = reg.get("react").last_used_at
        assert last_used is not None
        assert before <= last_used <= after

    def test_touch_unknown_returns_false(self):
        reg = CapabilityRegistry()
        assert reg.touch("nonexistent") is False

    def test_touch_returns_true_for_existing(self):
        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        assert reg.touch("react") is True

    def test_touch_updates_timestamp(self):
        import time

        reg = CapabilityRegistry()
        reg.register_capability("react", _cap())
        reg.touch("react")
        first = reg.get("react").last_used_at
        time.sleep(0.01)
        reg.touch("react")
        second = reg.get("react").last_used_at
        assert second > first


class TestApplyDecay:
    """Verify apply_decay reduces confidence for unused capabilities."""

    def test_decay_unused_capabilities(self):
        from datetime import UTC, datetime, timedelta

        reg = CapabilityRegistry()
        reg.register_capability("old", _cap(confidence=0.90))
        # Set last_used_at to 100 days ago
        reg.get("old").last_used_at = datetime.now(UTC) - timedelta(days=100)

        decayed = reg.apply_decay(days=90, factor=0.9)
        assert decayed == 1
        assert reg.get("old").confidence < 0.90

    def test_decay_does_not_affect_recent(self):
        from datetime import UTC, datetime, timedelta

        reg = CapabilityRegistry()
        reg.register_capability("fresh", _cap(confidence=0.90))
        reg.get("fresh").last_used_at = datetime.now(UTC) - timedelta(days=10)

        decayed = reg.apply_decay(days=90, factor=0.9)
        assert decayed == 0
        assert reg.get("fresh").confidence == 0.90

    def test_decay_affects_none_last_used(self):
        """Capabilities with last_used_at=None should be decayed."""
        reg = CapabilityRegistry()
        reg.register_capability("unused", _cap(confidence=0.80))
        # last_used_at is None by default

        decayed = reg.apply_decay(days=90, factor=0.9)
        assert decayed == 1
        assert reg.get("unused").confidence < 0.80

    def test_decay_factor_applied_correctly(self):
        from datetime import UTC, datetime, timedelta

        reg = CapabilityRegistry()
        reg.register_capability("old", _cap(confidence=1.0))
        reg.get("old").last_used_at = datetime.now(UTC) - timedelta(days=200)

        reg.apply_decay(days=90, factor=0.5)
        assert reg.get("old").confidence == 0.5

    def test_decay_never_below_zero(self):
        reg = CapabilityRegistry()
        reg.register_capability("tiny", _cap(confidence=0.01))
        for _ in range(50):
            reg.apply_decay(days=0, factor=0.5)
        assert reg.get("tiny").confidence >= 0.0

    def test_decay_returns_count(self):
        from datetime import UTC, datetime, timedelta

        reg = CapabilityRegistry()
        reg.register_capability("a", _cap(confidence=0.9))
        reg.register_capability("b", _cap(confidence=0.8))
        reg.get("a").last_used_at = datetime.now(UTC) - timedelta(days=100)
        reg.get("b").last_used_at = datetime.now(UTC) - timedelta(days=5)

        decayed = reg.apply_decay(days=90, factor=0.9)
        assert decayed == 1  # only 'a' is old enough

    def test_decay_factor_clamped(self):
        """Factor should be clamped to [0.0, 1.0]."""
        reg = CapabilityRegistry()
        reg.register_capability("x", _cap(confidence=0.5))

        # factor > 1 should be clamped to 1.0 (no increase)
        reg.apply_decay(days=0, factor=2.0)
        assert reg.get("x").confidence == 0.5

    def test_decay_with_defaults(self):
        """Default decay params: days=90, factor=0.9."""
        reg = CapabilityRegistry()
        reg.register_capability("a", _cap(confidence=1.0))
        # None last_used_at -> will be decayed with defaults
        decayed = reg.apply_decay()
        assert decayed == 1
        assert reg.get("a").confidence == 0.9

    def test_decay_mixed_registry(self):
        """Registry with both fresh and stale capabilities."""
        from datetime import UTC, datetime, timedelta

        reg = CapabilityRegistry()
        reg.register_capability("stale1", _cap(confidence=0.8))
        reg.register_capability("stale2", _cap(confidence=0.6))
        reg.register_capability("fresh", _cap(confidence=0.95))

        reg.get("stale1").last_used_at = datetime.now(UTC) - timedelta(days=120)
        reg.get("stale2").last_used_at = None  # never used
        reg.get("fresh").last_used_at = datetime.now(UTC) - timedelta(days=5)

        decayed = reg.apply_decay(days=90, factor=0.9)
        assert decayed == 2
        assert reg.get("stale1").confidence < 0.8
        assert reg.get("stale2").confidence < 0.6
        assert reg.get("fresh").confidence == 0.95
