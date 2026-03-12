"""Unit tests for Sales Conversation Engine.

Tests cover: SalesStage enum, SALES_TRANSITIONS, SalesAction/ClientMessage/
ConceptData models, SalesConversationEngine methods, stage transitions,
HITL detection, first contact generation, and full flows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.negotiations.sales_conversation import (
    SALES_TRANSITIONS,
    ClientMessage,
    ConceptData,
    SalesAction,
    SalesConversationEngine,
    SalesStage,
)

# ---------------------------------------------------------------------------
# SalesStage enum
# ---------------------------------------------------------------------------


class TestSalesStage:
    """Verify SalesStage enum values."""

    def test_has_eleven_stages(self):
        assert len(SalesStage) == 11

    def test_first_contact(self):
        assert SalesStage.FIRST_CONTACT == "first_contact"

    def test_awaiting_reply(self):
        assert SalesStage.AWAITING_REPLY == "awaiting_reply"

    def test_discovery(self):
        assert SalesStage.DISCOVERY == "discovery"

    def test_analysis(self):
        assert SalesStage.ANALYSIS == "analysis"

    def test_concept(self):
        assert SalesStage.CONCEPT == "concept"

    def test_concept_review(self):
        assert SalesStage.CONCEPT_REVIEW == "concept_review"

    def test_concept_sent(self):
        assert SalesStage.CONCEPT_SENT == "concept_sent"

    def test_closing(self):
        assert SalesStage.CLOSING == "closing"

    def test_won(self):
        assert SalesStage.WON == "won"

    def test_lost(self):
        assert SalesStage.LOST == "lost"

    def test_stale(self):
        assert SalesStage.STALE == "stale"

    def test_is_string(self):
        assert isinstance(SalesStage.DISCOVERY, str)

    def test_terminal_states(self):
        # won and lost have no outgoing transitions
        assert len(SALES_TRANSITIONS["won"]) == 0
        assert len(SALES_TRANSITIONS["lost"]) == 0


# ---------------------------------------------------------------------------
# SALES_TRANSITIONS matrix
# ---------------------------------------------------------------------------


class TestSalesTransitions:
    """Verify transition matrix per spec."""

    def test_all_stages_have_transitions(self):
        for stage in SalesStage:
            assert stage.value in SALES_TRANSITIONS, f"Missing transitions for {stage}"

    def test_first_contact_to_awaiting(self):
        assert "awaiting_reply" in SALES_TRANSITIONS["first_contact"]

    def test_awaiting_to_discovery_or_lost_or_stale(self):
        t = SALES_TRANSITIONS["awaiting_reply"]
        assert "discovery" in t
        assert "lost" in t
        assert "stale" in t

    def test_discovery_transitions(self):
        t = SALES_TRANSITIONS["discovery"]
        assert "analysis" in t
        assert "awaiting_reply" in t
        assert "lost" in t

    def test_analysis_transitions(self):
        t = SALES_TRANSITIONS["analysis"]
        assert "concept" in t
        assert "discovery" in t

    def test_concept_to_review(self):
        assert "concept_review" in SALES_TRANSITIONS["concept"]

    def test_concept_review_hitl(self):
        t = SALES_TRANSITIONS["concept_review"]
        assert "concept_sent" in t  # approve
        assert "concept" in t  # reject → redo

    def test_concept_sent_transitions(self):
        t = SALES_TRANSITIONS["concept_sent"]
        assert "closing" in t
        assert "lost" in t

    def test_closing_transitions(self):
        t = SALES_TRANSITIONS["closing"]
        assert "won" in t
        assert "lost" in t

    def test_stale_can_revive(self):
        assert "discovery" in SALES_TRANSITIONS["stale"]

    def test_no_transition_to_self(self):
        for stage, targets in SALES_TRANSITIONS.items():
            assert stage not in targets, f"{stage} can transition to itself"


# ---------------------------------------------------------------------------
# SalesAction dataclass
# ---------------------------------------------------------------------------


class TestSalesAction:
    """Verify SalesAction model."""

    def test_respond_action(self):
        a = SalesAction(type="respond", message="Hello!")
        assert a.type == "respond"
        assert a.message == "Hello!"
        assert a.auto_send is False
        assert a.hitl_type is None
        assert a.new_stage is None

    def test_escalate_action(self):
        a = SalesAction(
            type="escalate",
            hitl_type="concept_review",
            draft_message="Draft concept",
        )
        assert a.type == "escalate"
        assert a.hitl_type == "concept_review"
        assert a.draft_message == "Draft concept"

    def test_transition_action(self):
        a = SalesAction(type="transition", new_stage="analysis", message="Moving on")
        assert a.new_stage == "analysis"


# ---------------------------------------------------------------------------
# ClientMessage dataclass
# ---------------------------------------------------------------------------


class TestClientMessage:
    """Verify ClientMessage model."""

    def test_create_message(self):
        m = ClientMessage(text="Привет!", channel="telegram")
        assert m.text == "Привет!"
        assert m.channel == "telegram"
        assert m.timestamp is not None

    def test_channel_options(self):
        for ch in ("telegram", "email", "whatsapp"):
            m = ClientMessage(text="hi", channel=ch)
            assert m.channel == ch


# ---------------------------------------------------------------------------
# ConceptData dataclass
# ---------------------------------------------------------------------------


class TestConceptData:
    """Verify ConceptData model."""

    def test_create_concept(self):
        c = ConceptData(
            title="Лендинг с онлайн-записью",
            services=["дизайн", "разработка"],
            description="Landing page with booking",
            timeline="2-3 недели",
            estimated_cost=45000.0,
            rationale="Конкуренты получают +40% звонков",
        )
        assert c.title == "Лендинг с онлайн-записью"
        assert len(c.services) == 2
        assert c.estimated_cost == 45000.0

    def test_why_this_helps_default(self):
        c = ConceptData(
            title="t",
            services=[],
            description="d",
            timeline="1w",
            estimated_cost=0,
            rationale="r",
        )
        assert c.why_this_helps == []


# ---------------------------------------------------------------------------
# SalesConversationEngine — can_transition
# ---------------------------------------------------------------------------


class TestCanTransition:
    """Verify transition validation."""

    def test_valid_transition(self):
        engine = SalesConversationEngine()
        assert engine.can_transition("first_contact", "awaiting_reply") is True

    def test_invalid_transition(self):
        engine = SalesConversationEngine()
        assert engine.can_transition("first_contact", "won") is False

    def test_terminal_state_no_transitions(self):
        engine = SalesConversationEngine()
        assert engine.can_transition("won", "first_contact") is False
        assert engine.can_transition("lost", "discovery") is False

    def test_stale_can_revive_to_discovery(self):
        engine = SalesConversationEngine()
        assert engine.can_transition("stale", "discovery") is True


# ---------------------------------------------------------------------------
# SalesConversationEngine — needs_hitl
# ---------------------------------------------------------------------------


class TestNeedsHitl:
    """Verify HITL detection at various stages."""

    def test_concept_review_needs_hitl(self):
        engine = SalesConversationEngine()
        assert engine.needs_hitl("concept_review") is True

    def test_discovery_no_hitl(self):
        engine = SalesConversationEngine()
        assert engine.needs_hitl("discovery") is False

    def test_closing_no_hitl(self):
        engine = SalesConversationEngine()
        assert engine.needs_hitl("closing") is False


# ---------------------------------------------------------------------------
# SalesConversationEngine — detect_stage_transition
# ---------------------------------------------------------------------------


class TestDetectStageTransition:
    """Verify stage detection from LLM response."""

    def test_detect_discovery_from_reply(self):
        engine = SalesConversationEngine()
        new_stage = engine.detect_stage_transition(
            current_stage="awaiting_reply",
            response_text="Расскажите подробнее о вашем бизнесе",
            client_replied=True,
        )
        assert new_stage == "discovery"

    def test_detect_lost_from_explicit_no(self):
        engine = SalesConversationEngine()
        new_stage = engine.detect_stage_transition(
            current_stage="awaiting_reply",
            response_text="Клиент отказался",
            client_replied=True,
            client_message="не интересно",
        )
        assert new_stage == "lost"

    def test_no_transition_same_stage(self):
        engine = SalesConversationEngine()
        new_stage = engine.detect_stage_transition(
            current_stage="discovery",
            response_text="Ещё пару вопросов...",
            client_replied=True,
        )
        # Still in discovery — no transition
        assert new_stage is None

    def test_analysis_after_discovery(self):
        engine = SalesConversationEngine()
        new_stage = engine.detect_stage_transition(
            current_stage="discovery",
            response_text="Готов проанализировать конкурентов",
            client_replied=True,
            enough_info=True,
        )
        assert new_stage == "analysis"

    def test_concept_after_analysis(self):
        engine = SalesConversationEngine()
        new_stage = engine.detect_stage_transition(
            current_stage="analysis",
            response_text="Формирую концепцию",
            client_replied=False,
            analysis_complete=True,
        )
        assert new_stage == "concept"


# ---------------------------------------------------------------------------
# SalesConversationEngine — format_conversation
# ---------------------------------------------------------------------------


class TestFormatConversation:
    """Verify conversation formatting for LLM context."""

    def test_format_empty(self):
        engine = SalesConversationEngine()
        result = engine.format_conversation([])
        assert result == ""

    def test_format_single_message(self):
        engine = SalesConversationEngine()
        msgs = [{"role": "agent", "text": "Привет!"}]
        result = engine.format_conversation(msgs)
        assert "Привет!" in result

    def test_format_multi_turn(self):
        engine = SalesConversationEngine()
        msgs = [
            {"role": "agent", "text": "Привет!"},
            {"role": "client", "text": "Здравствуйте"},
            {"role": "agent", "text": "Расскажите о бизнесе"},
        ]
        result = engine.format_conversation(msgs)
        assert "Привет!" in result
        assert "Здравствуйте" in result
        assert "Расскажите" in result


# ---------------------------------------------------------------------------
# SalesConversationEngine — generate_first_contact
# ---------------------------------------------------------------------------


class TestGenerateFirstContact:
    """Verify first contact message generation."""

    @pytest.mark.asyncio
    async def test_generate_first_contact(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "Привет! Заметил вашу стоматологию на картах..."
        mock_llm.chat.return_value = (mock_response, {"tokens": 100})

        engine = SalesConversationEngine(llm_client=mock_llm)
        lead_info = {
            "business_name": "Стоматология Улыбка",
            "city": "Ростов-на-Дону",
            "category": "стоматология",
        }
        battlecard = {"key_selling_points": ["+40% звонков с сайтом"]}

        text = await engine.generate_first_contact(
            lead_info=lead_info,
            battlecard=battlecard,
            operator_name="Алексей",
        )
        assert isinstance(text, str)
        assert len(text) > 0
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_first_contact_no_llm_raises(self):
        engine = SalesConversationEngine()
        with pytest.raises(ValueError, match="LLM client"):
            await engine.generate_first_contact(
                lead_info={"business_name": "Test"},
                battlecard={},
                operator_name="Test",
            )


# ---------------------------------------------------------------------------
# SalesConversationEngine — generate_concept
# ---------------------------------------------------------------------------


class TestGenerateConcept:
    """Verify concept generation."""

    @pytest.mark.asyncio
    async def test_generate_concept(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = (
            '{"title": "Лендинг", "services": ["дизайн"], '
            '"description": "Landing", "timeline": "2 недели", '
            '"estimated_cost": 45000, "rationale": "Рост трафика", '
            '"why_this_helps": ["+40% звонков"]}'
        )
        mock_llm.chat.return_value = (mock_response, {"tokens": 200})

        engine = SalesConversationEngine(llm_client=mock_llm)
        concept = await engine.generate_concept(
            business={"name": "Clinic", "city": "Moscow"},
            client_needs={"pain": "no website"},
            battlecard={"key_selling_points": ["more calls"]},
        )
        assert isinstance(concept, ConceptData)
        assert concept.title == "Лендинг"
        assert concept.estimated_cost == 45000

    @pytest.mark.asyncio
    async def test_generate_concept_bad_json_returns_none(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "Not valid JSON at all"
        mock_llm.chat.return_value = (mock_response, {"tokens": 50})

        engine = SalesConversationEngine(llm_client=mock_llm)
        concept = await engine.generate_concept(
            business={},
            client_needs={},
            battlecard={},
        )
        assert concept is None


# ---------------------------------------------------------------------------
# SalesConversationEngine — process_client_reply
# ---------------------------------------------------------------------------


class TestProcessClientReply:
    """Verify client reply processing."""

    @pytest.mark.asyncio
    async def test_process_reply_returns_action(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "Расскажите подробнее о вашем бизнесе"
        mock_response.tool_calls = []
        mock_llm.chat.return_value = (mock_response, {"tokens": 100})

        engine = SalesConversationEngine(llm_client=mock_llm)
        action = await engine.process_client_reply(
            deal_id="deal-1",
            sales_stage="awaiting_reply",
            client_message=ClientMessage(text="Привет, расскажите", channel="telegram"),
            conversation_history=[],
            deal_context={},
            battlecard={},
            operator_name="Алексей",
        )
        assert isinstance(action, SalesAction)
        assert action.type in ("respond", "transition", "escalate")

    @pytest.mark.asyncio
    async def test_process_reply_detects_lost(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "Понял, спасибо за ответ. Удачи!"
        mock_response.tool_calls = []
        mock_llm.chat.return_value = (mock_response, {"tokens": 50})

        engine = SalesConversationEngine(llm_client=mock_llm)
        action = await engine.process_client_reply(
            deal_id="deal-1",
            sales_stage="awaiting_reply",
            client_message=ClientMessage(text="не интересно", channel="telegram"),
            conversation_history=[],
            deal_context={},
            battlecard={},
            operator_name="Алексей",
        )
        assert action.new_stage == "lost" or action.type == "transition"
