"""Unit tests for src.negotiations.response_generator -- ResponseGenerator.

Tests cover: generate returns GeneratedResponse, RAG context retrieval,
RAG failure graceful degradation, auto_send for safe/unsafe handlers,
LLM call parameters, handler-specific prompts, and JSON guard.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from src.negotiations.response_generator import (
    AUTO_SEND_HANDLERS,
    HANDLER_PROMPTS,
    GeneratedResponse,
    ResponseGenerator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm(response_text: str = "Sure, I can help with that.") -> AsyncMock:
    client = AsyncMock()
    ai_msg = AIMessage(content=response_text)
    metrics = MagicMock()
    client.call = AsyncMock(return_value=(ai_msg, metrics))
    return client


def _make_rag(results: list[dict] | None = None, raises: bool = False) -> AsyncMock:
    rag = AsyncMock()
    if raises:
        rag.retrieve_context = AsyncMock(side_effect=RuntimeError("RAG down"))
    else:
        rag.retrieve_context = AsyncMock(return_value=results or [])
    return rag


def _sample_bid() -> dict:
    return {
        "amount": 500,
        "delivery_days": 7,
        "proposal_text": "I will build a landing page with React and Tailwind.",
        "job_snapshot": {
            "title": "Landing Page",
            "description": "Build a responsive landing page for our product launch.",
        },
    }


def _sample_conversation() -> list[dict]:
    return [
        {"role": "agent", "content": "Here is my proposal for $500."},
        {"role": "client", "content": "Can you tell me more about your approach?"},
    ]


def _sample_classification(msg_type: str = "clarification") -> dict:
    return {
        "message_type": msg_type,
        "sentiment": "neutral",
        "urgency": "medium",
        "confidence": 0.85,
    }


# ---------------------------------------------------------------------------
# Tests: generate returns GeneratedResponse
# ---------------------------------------------------------------------------


class TestGenerateBasic:
    """Test that generate returns a properly structured GeneratedResponse."""

    async def test_returns_generated_response(self):
        llm = _make_llm("Here is what I suggest.")
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="clarification",
        )

        assert isinstance(result, GeneratedResponse)
        assert result.text == "Here is what I suggest."
        assert result.handler == "clarification"

    async def test_response_text_stripped(self):
        llm = _make_llm("  Some response with whitespace  \n")
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="general",
        )

        assert result.text == "Some response with whitespace"


# ---------------------------------------------------------------------------
# Tests: RAG context
# ---------------------------------------------------------------------------


class TestRAGContext:
    """Test RAG context retrieval from ExperienceStore."""

    async def test_rag_context_retrieved(self):
        rag_results = [
            {"content": "Similar negotiation text", "metadata": {"id": "exp-1"}, "similarity_score": 0.85},
            {"content": "Another negotiation", "metadata": {"id": "exp-2"}, "similarity_score": 0.72},
        ]
        rag = _make_rag(results=rag_results)
        llm = _make_llm()
        gen = ResponseGenerator(llm, experience_store=rag)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="clarification",
        )

        rag.retrieve_context.assert_awaited_once()
        assert result.rag_sources == ["exp-1", "exp-2"]

    async def test_rag_failure_graceful_degradation(self):
        rag = _make_rag(raises=True)
        llm = _make_llm("Fallback response without RAG")
        gen = ResponseGenerator(llm, experience_store=rag)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="clarification",
        )

        assert isinstance(result, GeneratedResponse)
        assert result.text == "Fallback response without RAG"
        assert result.rag_sources == []

    async def test_no_rag_when_experience_store_none(self):
        llm = _make_llm()
        gen = ResponseGenerator(llm, experience_store=None)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="general",
        )

        assert result.rag_sources == []

    async def test_rag_empty_conversation_skips_retrieval(self):
        rag = _make_rag()
        llm = _make_llm()
        gen = ResponseGenerator(llm, experience_store=rag)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=[],
            classification=_sample_classification(),
            handler_name="general",
        )

        rag.retrieve_context.assert_not_awaited()
        assert result.rag_sources == []


# ---------------------------------------------------------------------------
# Tests: auto_send for safe/unsafe handlers
# ---------------------------------------------------------------------------


class TestAutoSend:
    """Test auto_send is True only for safe handlers."""

    @pytest.mark.parametrize("handler", sorted(AUTO_SEND_HANDLERS))
    async def test_auto_send_true_for_safe_handlers(self, handler: str):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(handler),
            handler_name=handler,
        )

        assert result.auto_send is True

    @pytest.mark.parametrize(
        "handler", ["pricing", "counter_offer", "scope_change", "technical", "acceptance", "rejection"]
    )
    async def test_auto_send_false_for_hitl_handlers(self, handler: str):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(handler),
            handler_name=handler,
        )

        assert result.auto_send is False


# ---------------------------------------------------------------------------
# Tests: LLM call parameters
# ---------------------------------------------------------------------------


class TestLLMCallParams:
    """Test that LLM is called with correct parameters."""

    async def test_llm_called_with_correct_agent_name(self):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="clarification",
        )

        call_args = llm.call.call_args
        assert call_args[0][0] == "negotiation_responder"

    async def test_llm_called_with_correct_temperature(self):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="clarification",
        )

        call_kwargs = llm.call.call_args
        assert call_kwargs.kwargs["temperature"] == 0.4

    async def test_llm_called_with_correct_max_tokens(self):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="clarification",
        )

        call_kwargs = llm.call.call_args
        assert call_kwargs.kwargs["max_tokens"] == 800

    async def test_llm_exception_propagates(self):
        llm = AsyncMock()
        llm.call = AsyncMock(side_effect=RuntimeError("LLM down"))
        gen = ResponseGenerator(llm)

        with pytest.raises(RuntimeError, match="LLM down"):
            await gen.generate(
                bid=_sample_bid(),
                conversation=_sample_conversation(),
                classification=_sample_classification(),
                handler_name="clarification",
            )


# ---------------------------------------------------------------------------
# Tests: Handler-specific prompts
# ---------------------------------------------------------------------------


class TestHandlerPrompts:
    """Test that each handler uses a distinct system prompt."""

    @pytest.mark.parametrize("handler_name", list(HANDLER_PROMPTS.keys()))
    async def test_handler_prompt_used_for_each_type(self, handler_name: str):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(handler_name),
            handler_name=handler_name,
        )

        call_args = llm.call.call_args[0]
        messages = call_args[1]
        system_content = messages[0].content
        # The handler prompt should be used as the system message
        assert HANDLER_PROMPTS[handler_name] == system_content

    async def test_unknown_handler_uses_fallback_prompt(self):
        llm = _make_llm()
        gen = ResponseGenerator(llm)

        await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="nonexistent_handler",
        )

        call_args = llm.call.call_args[0]
        messages = call_args[1]
        system_content = messages[0].content
        assert "Respond helpfully and professionally" in system_content


# ---------------------------------------------------------------------------
# Tests: JSON guard in _call_llm
# ---------------------------------------------------------------------------


class TestJSONGuard:
    """Test that if LLM accidentally returns JSON, the text field is extracted."""

    async def test_json_response_with_text_field_extracted(self):
        json_response = json.dumps({"text": "The actual response", "tone": "friendly"})
        llm = _make_llm(json_response)
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="general",
        )

        assert result.text == "The actual response"

    async def test_json_response_without_text_field_uses_raw(self):
        json_response = json.dumps({"message": "No text field"})
        llm = _make_llm(json_response)
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="general",
        )

        # Should use the raw JSON string since there is no "text" key
        assert result.text == json_response

    async def test_non_json_response_used_as_is(self):
        llm = _make_llm("Plain text response")
        gen = ResponseGenerator(llm)

        result = await gen.generate(
            bid=_sample_bid(),
            conversation=_sample_conversation(),
            classification=_sample_classification(),
            handler_name="general",
        )

        assert result.text == "Plain text response"
