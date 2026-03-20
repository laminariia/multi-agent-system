"""LLM-based message classifier for negotiation conversations.

Classifies incoming client messages into one of 10 negotiation message types,
extracting sentiment, urgency, and pricing information. Used by the Negotiation
Engine to decide whether a message requires HITL escalation or can be handled
automatically.

Spec: docs/Full_work/specs/negotiation-spec.md section 3
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Message type configuration
# ---------------------------------------------------------------------------

MESSAGE_TYPES: dict[str, dict[str, Any]] = {
    "clarification": {
        "hitl_required": False,
        "description": "Client asking for clarification",
    },
    "technical": {
        "hitl_required": True,
        "description": "Technical questions about implementation",
    },
    "timeline": {
        "hitl_required": False,
        "description": "Questions about timeline/delivery",
    },
    "pricing": {
        "hitl_required": True,
        "description": "Price negotiation or counter-offer",
    },
    "portfolio": {
        "hitl_required": False,
        "description": "Asking for portfolio/examples",
    },
    "acceptance": {
        "hitl_required": False,
        "description": "Client accepts proposal",
    },
    "rejection": {
        "hitl_required": False,
        "description": "Client rejects/declines",
    },
    "counter_offer": {
        "hitl_required": True,
        "description": "Client proposes different terms",
    },
    "scope_change": {
        "hitl_required": True,
        "description": "Client wants to change project scope",
    },
    "general": {
        "hitl_required": False,
        "description": "General conversation",
    },
}

_VALID_TYPES = frozenset(MESSAGE_TYPES)
_VALID_SENTIMENTS = frozenset({"positive", "neutral", "negative"})
_VALID_URGENCIES = frozenset({"low", "medium", "high"})


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Result of classifying a negotiation message."""

    message_type: str
    confidence: float  # 0.0-1.0
    sentiment: str  # positive/neutral/negative
    urgency: str  # low/medium/high
    contains_question: bool
    extracted_amount: float | None  # if pricing/counter_offer
    reasoning: str
    hitl_required: bool


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_CLASSIFIER_SYSTEM_PROMPT = """You are a message classifier for freelance platform negotiations.

Given a client message and conversation context, classify it into ONE of these types:
- clarification: Client asking about project details, process, approach
- technical: Client asking about tech stack, architecture, tools
- timeline: Client asking about deadlines, delivery dates
- pricing: Client discussing price, budget, discounts (ALWAYS requires human review)
- portfolio: Client requesting examples, portfolio, past work
- acceptance: Client hiring / confirming the project
- rejection: Client declining / going with someone else
- counter_offer: Client proposing a different price
- scope_change: Client adding new requirements not in original brief
- general: Anything else

Respond with a JSON object containing exactly these fields:
- "message_type": one of the types listed above
- "confidence": float from 0.0 to 1.0 indicating classification confidence
- "sentiment": "positive", "neutral", or "negative"
- "urgency": "low", "medium", or "high"
- "contains_question": true or false
- "extracted_amount": number if a price/budget is mentioned, null otherwise
- "reasoning": brief explanation of why this classification was chosen

Respond ONLY with the JSON object, no markdown fences or extra text."""

_USER_PROMPT_TEMPLATE = """Classify the following client message.

{context_section}Message:
{message}"""


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class MessageClassifier:
    """LLM-based classifier for negotiation messages.

    Determines message type, sentiment, urgency, and whether HITL escalation
    is required based on the configured ``MESSAGE_TYPES`` rules.

    Args:
        llm_client: An ``LLMClient`` instance for making LLM calls.
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def classify(
        self,
        message_content: str,
        conversation_context: list[dict] | None = None,
    ) -> ClassificationResult:
        """Classify a client message into one of the negotiation message types.

        Builds a prompt with the message and optional conversation context,
        calls the LLM, and parses the structured JSON response. Falls back
        to ``"general"`` with low confidence on any parse error.

        Args:
            message_content: The raw text of the client message.
            conversation_context: Optional list of prior messages, each a dict
                with ``"role"`` and ``"text"`` keys.

        Returns:
            A ``ClassificationResult`` with the determined type and metadata.
        """
        context_section = self._format_context(conversation_context)
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            context_section=context_section,
            message=message_content,
        )

        messages = [
            SystemMessage(content=_CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        try:
            response, _metrics = await self._llm.call(
                "negotiation_classifier",
                messages,
                temperature=0.2,
                max_tokens=500,
            )

            raw = str(response.content)
            data = extract_json(raw, expected_type=dict)
            return self._parse_result(data)

        except Exception:
            logger.warning(
                "classifier.parse_failed",
                message_preview=message_content[:100],
                exc_info=True,
            )
            return self._fallback_result(message_content)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_context(conversation_context: list[dict] | None) -> str:
        """Format conversation context into a prompt section."""
        if not conversation_context:
            return ""

        lines: list[str] = []
        for msg in conversation_context:
            role = msg.get("role", "unknown")
            text = msg.get("text", "")
            prefix = "Operator" if role == "agent" else "Client"
            lines.append(f"{prefix}: {text}")

        return "Conversation context:\n" + "\n".join(lines) + "\n\n"

    @staticmethod
    def _parse_result(data: dict[str, Any]) -> ClassificationResult:
        """Parse and validate the LLM JSON response into a ClassificationResult."""
        # Validate message_type
        message_type = str(data.get("message_type", "general")).lower().strip()
        if message_type not in _VALID_TYPES:
            logger.warning(
                "classifier.unknown_type",
                raw_type=message_type,
                fallback="general",
            )
            message_type = "general"

        # Clamp confidence to 0.0-1.0
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        # Validate sentiment
        sentiment = str(data.get("sentiment", "neutral")).lower().strip()
        if sentiment not in _VALID_SENTIMENTS:
            sentiment = "neutral"

        # Validate urgency
        urgency = str(data.get("urgency", "low")).lower().strip()
        if urgency not in _VALID_URGENCIES:
            urgency = "low"

        # Parse contains_question
        contains_question = bool(data.get("contains_question", False))

        # Parse extracted_amount
        raw_amount = data.get("extracted_amount")
        extracted_amount: float | None = None
        if raw_amount is not None:
            try:
                extracted_amount = float(raw_amount)
            except (TypeError, ValueError):
                extracted_amount = None

        reasoning = str(data.get("reasoning", ""))

        # Set hitl_required from MESSAGE_TYPES config (authoritative source)
        hitl_required = MESSAGE_TYPES[message_type]["hitl_required"]

        return ClassificationResult(
            message_type=message_type,
            confidence=confidence,
            sentiment=sentiment,
            urgency=urgency,
            contains_question=contains_question,
            extracted_amount=extracted_amount,
            reasoning=reasoning,
            hitl_required=hitl_required,
        )

    @staticmethod
    def _fallback_result(message_content: str) -> ClassificationResult:
        """Return a safe fallback classification when parsing fails."""
        has_question = "?" in message_content
        return ClassificationResult(
            message_type="general",
            confidence=0.0,
            sentiment="neutral",
            urgency="low",
            contains_question=has_question,
            extracted_amount=None,
            reasoning="Fallback classification due to LLM parse failure",
            hitl_required=False,
        )
