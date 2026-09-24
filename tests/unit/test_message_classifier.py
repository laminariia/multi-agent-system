"""Unit tests for src.negotiations.classifier -- MessageClassifier.

Tests cover: classify for each of 10 types, HITL routing, confidence clamping,
invalid type fallback, LLM parse error fallback, extracted_amount,
conversation_context formatting, sentiment/urgency validation.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from src.negotiations.classifier import (
    _VALID_SENTIMENTS,
    _VALID_URGENCIES,
    MESSAGE_TYPES,
    ClassificationResult,
    MessageClassifier,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm(response_dict: dict) -> AsyncMock:
    """Return an LLMClient mock whose call() returns the given dict as JSON."""
    client = AsyncMock()
    content = json.dumps(response_dict)
    ai_msg = AIMessage(content=content)
    metrics = MagicMock()
    client.call = AsyncMock(return_value=(ai_msg, metrics))
    return client


def _base_response(**overrides) -> dict:
    base = {
        "message_type": "general",
        "confidence": 0.85,
        "sentiment": "neutral",
        "urgency": "low",
        "contains_question": False,
        "extracted_amount": None,
        "reasoning": "test reasoning",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: Classification for each message type
# ---------------------------------------------------------------------------


class TestClassifyMessageTypes:
    """Test classify returns correct result for each of 10 message types."""

    @pytest.mark.parametrize("msg_type", list(MESSAGE_TYPES.keys()))
    async def test_classify_returns_correct_type(self, msg_type: str):
        llm = _make_llm(_base_response(message_type=msg_type))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("test message")

        assert isinstance(result, ClassificationResult)
        assert result.message_type == msg_type

    @pytest.mark.parametrize("msg_type", list(MESSAGE_TYPES.keys()))
    async def test_hitl_matches_config(self, msg_type: str):
        llm = _make_llm(_base_response(message_type=msg_type))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("test message")

        expected_hitl = MESSAGE_TYPES[msg_type]["hitl_required"]
        assert result.hitl_required is expected_hitl


# ---------------------------------------------------------------------------
# Tests: HITL routing specifics
# ---------------------------------------------------------------------------


class TestHITLRouting:
    """Test that HITL is required for the correct message types."""

    @pytest.mark.parametrize("msg_type", ["technical", "pricing", "counter_offer", "scope_change"])
    async def test_hitl_required_types(self, msg_type: str):
        llm = _make_llm(_base_response(message_type=msg_type))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.hitl_required is True

    @pytest.mark.parametrize(
        "msg_type", ["clarification", "timeline", "portfolio", "general", "acceptance", "rejection"]
    )
    async def test_non_hitl_types(self, msg_type: str):
        llm = _make_llm(_base_response(message_type=msg_type))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.hitl_required is False


# ---------------------------------------------------------------------------
# Tests: Confidence clamping
# ---------------------------------------------------------------------------


class TestConfidenceClamping:
    """Test confidence is clamped to [0.0, 1.0]."""

    async def test_confidence_above_1_clamped(self):
        llm = _make_llm(_base_response(confidence=1.5))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.confidence == 1.0

    async def test_confidence_below_0_clamped(self):
        llm = _make_llm(_base_response(confidence=-0.3))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.confidence == 0.0

    async def test_confidence_within_range_unchanged(self):
        llm = _make_llm(_base_response(confidence=0.72))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.confidence == 0.72

    async def test_confidence_exactly_0(self):
        llm = _make_llm(_base_response(confidence=0.0))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.confidence == 0.0

    async def test_confidence_exactly_1(self):
        llm = _make_llm(_base_response(confidence=1.0))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.confidence == 1.0

    async def test_confidence_non_numeric_defaults(self):
        llm = _make_llm(_base_response(confidence="not_a_number"))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.confidence == 0.5


# ---------------------------------------------------------------------------
# Tests: Invalid/unknown message_type fallback
# ---------------------------------------------------------------------------


class TestInvalidTypeFallback:
    """Test that unknown message_type from LLM falls back to 'general'."""

    async def test_unknown_type_falls_back_to_general(self):
        llm = _make_llm(_base_response(message_type="totally_unknown"))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.message_type == "general"
        assert result.hitl_required is False

    async def test_empty_type_falls_back_to_general(self):
        llm = _make_llm(_base_response(message_type=""))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.message_type == "general"

    async def test_type_with_spaces_trimmed(self):
        llm = _make_llm(_base_response(message_type="  pricing  "))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.message_type == "pricing"


# ---------------------------------------------------------------------------
# Tests: LLM parse error fallback
# ---------------------------------------------------------------------------


class TestLLMParseErrorFallback:
    """Test safe fallback when LLM call or JSON parsing fails."""

    async def test_llm_exception_returns_fallback(self):
        llm = AsyncMock()
        llm.call = AsyncMock(side_effect=RuntimeError("LLM down"))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("What is the status?")

        assert result.message_type == "general"
        assert result.confidence == 0.0
        assert result.sentiment == "neutral"
        assert result.urgency == "low"
        assert result.hitl_required is False
        assert "Fallback" in result.reasoning

    async def test_fallback_detects_question_mark(self):
        llm = AsyncMock()
        llm.call = AsyncMock(side_effect=RuntimeError("fail"))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("Can you help?")
        assert result.contains_question is True

    async def test_fallback_no_question_mark(self):
        llm = AsyncMock()
        llm.call = AsyncMock(side_effect=RuntimeError("fail"))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("I need help")
        assert result.contains_question is False

    async def test_malformed_json_returns_fallback(self):
        llm = AsyncMock()
        ai_msg = AIMessage(content="This is not JSON at all {{{")
        metrics = MagicMock()
        llm.call = AsyncMock(return_value=(ai_msg, metrics))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("test")
        assert result.message_type == "general"
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Tests: extracted_amount
# ---------------------------------------------------------------------------


class TestExtractedAmount:
    """Test extracted_amount parsing for pricing messages."""

    async def test_extracted_amount_present(self):
        llm = _make_llm(_base_response(message_type="pricing", extracted_amount=750.0))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("My budget is $750")
        assert result.extracted_amount == 750.0

    async def test_extracted_amount_none(self):
        llm = _make_llm(_base_response(extracted_amount=None))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.extracted_amount is None

    async def test_extracted_amount_invalid_string(self):
        llm = _make_llm(_base_response(extracted_amount="not-a-number"))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.extracted_amount is None

    async def test_extracted_amount_integer(self):
        llm = _make_llm(_base_response(message_type="counter_offer", extracted_amount=500))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("I can pay 500")
        assert result.extracted_amount == 500.0


# ---------------------------------------------------------------------------
# Tests: Conversation context
# ---------------------------------------------------------------------------


class TestConversationContext:
    """Test conversation_context passed to LLM prompt."""

    async def test_classify_with_context(self):
        llm = _make_llm(_base_response(message_type="clarification"))
        classifier = MessageClassifier(llm)

        context = [
            {"role": "agent", "text": "Here is my proposal"},
            {"role": "client", "text": "Looks interesting"},
        ]
        result = await classifier.classify("Tell me more", conversation_context=context)

        assert result.message_type == "clarification"
        # Verify the LLM was called (context was used in the prompt)
        llm.call.assert_awaited_once()

    async def test_classify_without_context(self):
        llm = _make_llm(_base_response(message_type="general"))
        classifier = MessageClassifier(llm)

        result = await classifier.classify("Hello", conversation_context=None)

        assert result.message_type == "general"
        llm.call.assert_awaited_once()

    def test_format_context_empty(self):
        result = MessageClassifier._format_context(None)
        assert result == ""

    def test_format_context_with_messages(self):
        ctx = [
            {"role": "agent", "text": "Hi"},
            {"role": "client", "text": "Hello"},
        ]
        result = MessageClassifier._format_context(ctx)
        assert "Operator: Hi" in result
        assert "Client: Hello" in result

    def test_format_context_empty_list(self):
        result = MessageClassifier._format_context([])
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: Sentiment and urgency validation
# ---------------------------------------------------------------------------


class TestSentimentUrgencyValidation:
    """Test sentiment and urgency values are validated and defaulted."""

    async def test_invalid_sentiment_defaults_to_neutral(self):
        llm = _make_llm(_base_response(sentiment="confused"))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.sentiment == "neutral"

    async def test_valid_sentiments(self):
        for sentiment in _VALID_SENTIMENTS:
            llm = _make_llm(_base_response(sentiment=sentiment))
            classifier = MessageClassifier(llm)
            result = await classifier.classify("test")
            assert result.sentiment == sentiment

    async def test_invalid_urgency_defaults_to_low(self):
        llm = _make_llm(_base_response(urgency="extreme"))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.urgency == "low"

    async def test_valid_urgencies(self):
        for urgency in _VALID_URGENCIES:
            llm = _make_llm(_base_response(urgency=urgency))
            classifier = MessageClassifier(llm)
            result = await classifier.classify("test")
            assert result.urgency == urgency


class TestContainsQuestion:
    """Test contains_question parsing."""

    async def test_contains_question_true(self):
        llm = _make_llm(_base_response(contains_question=True))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test?")
        assert result.contains_question is True

    async def test_contains_question_false(self):
        llm = _make_llm(_base_response(contains_question=False))
        classifier = MessageClassifier(llm)
        result = await classifier.classify("test")
        assert result.contains_question is False
