"""Unit tests for Deal Memory (src/core/deal_memory.py).

Tests cover: DealMemory CRUD, context retrieval, conversation summary,
edge cases, and concurrent access patterns.
"""

from __future__ import annotations

from src.core.deal_memory import DealContext, DealMemory

# ---------------------------------------------------------------------------
# DealContext dataclass
# ---------------------------------------------------------------------------


class TestDealContext:
    """Verify DealContext model."""

    def test_create_default(self):
        ctx = DealContext(deal_id="deal-001", lead_id="lead-001")
        assert ctx.deal_id == "deal-001"
        assert ctx.lead_id == "lead-001"
        assert ctx.key_facts == {}
        assert ctx.decisions == []
        assert ctx.client_preferences == {}
        assert ctx.agreed_scope is None
        assert ctx.conversation_summary is None

    def test_create_with_facts(self):
        ctx = DealContext(
            deal_id="d1",
            lead_id="l1",
            key_facts={"budget": "50k"},
        )
        assert ctx.key_facts["budget"] == "50k"

    def test_create_with_all_fields(self):
        ctx = DealContext(
            deal_id="d1",
            lead_id="l1",
            key_facts={"pain": "no website"},
            agreed_scope="Landing page",
            decisions=[{"decision": "use React", "reason": "client preference"}],
            client_preferences={"style": "minimalist"},
            conversation_summary="Discussed landing page options",
        )
        assert ctx.agreed_scope == "Landing page"
        assert len(ctx.decisions) == 1
        assert ctx.client_preferences["style"] == "minimalist"
        assert "landing" in ctx.conversation_summary.lower()


# ---------------------------------------------------------------------------
# DealMemory.save_fact / get_fact
# ---------------------------------------------------------------------------


class TestSaveFact:
    """Verify saving and retrieving individual facts."""

    def test_save_and_get_fact(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "client_pain", "Нет онлайн-записи")
        assert mem.get_fact("deal-1", "client_pain") == "Нет онлайн-записи"

    def test_save_overwrites_existing(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "budget", "50k")
        mem.save_fact("deal-1", "budget", "70k")
        assert mem.get_fact("deal-1", "budget") == "70k"

    def test_get_fact_missing_key_returns_none(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        assert mem.get_fact("deal-1", "nonexistent") is None

    def test_get_fact_missing_deal_returns_none(self):
        mem = DealMemory()
        assert mem.get_fact("no-such-deal", "key") is None

    def test_save_multiple_facts(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "pain", "no website")
        mem.save_fact("deal-1", "budget", "50k")
        mem.save_fact("deal-1", "decision_maker", "owner")
        facts = mem.get_all_facts("deal-1")
        assert len(facts) == 3
        assert facts["pain"] == "no website"
        assert facts["budget"] == "50k"


# ---------------------------------------------------------------------------
# DealMemory.delete_fact
# ---------------------------------------------------------------------------


class TestDeleteFact:
    """Verify fact deletion."""

    def test_delete_existing_fact(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "budget", "50k")
        deleted = mem.delete_fact("deal-1", "budget")
        assert deleted is True
        assert mem.get_fact("deal-1", "budget") is None

    def test_delete_missing_fact_returns_false(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        assert mem.delete_fact("deal-1", "nonexistent") is False

    def test_delete_missing_deal_returns_false(self):
        mem = DealMemory()
        assert mem.delete_fact("no-deal", "key") is False


# ---------------------------------------------------------------------------
# DealMemory.create_context / get_context
# ---------------------------------------------------------------------------


class TestCreateGetContext:
    """Verify context creation and retrieval."""

    def test_create_context(self):
        mem = DealMemory()
        ctx = mem.create_context("deal-1", "lead-1")
        assert isinstance(ctx, DealContext)
        assert ctx.deal_id == "deal-1"

    def test_create_duplicate_returns_existing(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "key", "value")
        ctx2 = mem.create_context("deal-1", "lead-1")
        # Should return existing, not overwrite
        assert ctx2.key_facts.get("key") == "value"

    def test_get_context_returns_full(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "pain", "no website")
        mem.save_fact("deal-1", "budget", "50k")
        mem.set_agreed_scope("deal-1", "Landing page + SEO")
        ctx = mem.get_context("deal-1")
        assert ctx is not None
        assert ctx.key_facts["pain"] == "no website"
        assert ctx.agreed_scope == "Landing page + SEO"

    def test_get_context_missing_returns_none(self):
        mem = DealMemory()
        assert mem.get_context("no-deal") is None


# ---------------------------------------------------------------------------
# DealMemory.set_agreed_scope / add_decision
# ---------------------------------------------------------------------------


class TestScopeAndDecisions:
    """Verify scope setting and decision recording."""

    def test_set_agreed_scope(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.set_agreed_scope("deal-1", "Landing + booking")
        ctx = mem.get_context("deal-1")
        assert ctx.agreed_scope == "Landing + booking"

    def test_add_decision(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.add_decision("deal-1", "Use React", reason="Client preference")
        ctx = mem.get_context("deal-1")
        assert len(ctx.decisions) == 1
        assert ctx.decisions[0]["decision"] == "Use React"
        assert ctx.decisions[0]["reason"] == "Client preference"

    def test_add_multiple_decisions(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.add_decision("deal-1", "Use React")
        mem.add_decision("deal-1", "Add SEO")
        mem.add_decision("deal-1", "Include analytics")
        ctx = mem.get_context("deal-1")
        assert len(ctx.decisions) == 3

    def test_set_client_preference(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.set_preference("deal-1", "style", "minimalist")
        mem.set_preference("deal-1", "color", "blue")
        ctx = mem.get_context("deal-1")
        assert ctx.client_preferences["style"] == "minimalist"
        assert ctx.client_preferences["color"] == "blue"


# ---------------------------------------------------------------------------
# DealMemory.set_conversation_summary
# ---------------------------------------------------------------------------


class TestConversationSummary:
    """Verify conversation summary management."""

    def test_set_summary(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.set_conversation_summary("deal-1", "Discussed landing page options")
        ctx = mem.get_context("deal-1")
        assert ctx.conversation_summary == "Discussed landing page options"

    def test_update_summary(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.set_conversation_summary("deal-1", "First call")
        mem.set_conversation_summary("deal-1", "First call + concept discussion")
        ctx = mem.get_context("deal-1")
        assert "concept" in ctx.conversation_summary


# ---------------------------------------------------------------------------
# DealMemory.list_deals / delete_context
# ---------------------------------------------------------------------------


class TestListAndDelete:
    """Verify listing and deletion of deal contexts."""

    def test_list_deals_empty(self):
        mem = DealMemory()
        assert mem.list_deals() == []

    def test_list_deals_returns_ids(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.create_context("deal-2", "lead-2")
        deals = mem.list_deals()
        assert set(deals) == {"deal-1", "deal-2"}

    def test_delete_context(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "key", "val")
        deleted = mem.delete_context("deal-1")
        assert deleted is True
        assert mem.get_context("deal-1") is None

    def test_delete_missing_context_returns_false(self):
        mem = DealMemory()
        assert mem.delete_context("no-deal") is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_key_fact(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "", "value")
        assert mem.get_fact("deal-1", "") == "value"

    def test_unicode_values(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.save_fact("deal-1", "pain", "Нет онлайн-записи, теряет 30% звонков")
        assert "30%" in mem.get_fact("deal-1", "pain")

    def test_large_fact_value(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        big_value = "x" * 10000
        mem.save_fact("deal-1", "description", big_value)
        assert len(mem.get_fact("deal-1", "description")) == 10000

    def test_operations_on_nonexistent_deal_safe(self):
        mem = DealMemory()
        assert mem.get_fact("nope", "k") is None
        assert mem.get_all_facts("nope") == {}
        assert mem.delete_fact("nope", "k") is False
        assert mem.delete_context("nope") is False
        # These should not raise
        mem.set_agreed_scope("nope", "scope")
        mem.add_decision("nope", "decision")
        mem.set_preference("nope", "k", "v")
        mem.set_conversation_summary("nope", "summary")

    def test_isolated_deal_contexts(self):
        mem = DealMemory()
        mem.create_context("deal-1", "lead-1")
        mem.create_context("deal-2", "lead-2")
        mem.save_fact("deal-1", "budget", "50k")
        mem.save_fact("deal-2", "budget", "100k")
        assert mem.get_fact("deal-1", "budget") == "50k"
        assert mem.get_fact("deal-2", "budget") == "100k"
