"""Generate AI responses for negotiation messages using LLM + RAG.

Each of the 10 message classification types has a handler-specific system
prompt that guides the LLM toward the correct tone and content.  The
``ResponseGenerator`` retrieves similar successful negotiations from the
ExperienceStore (when available) and injects them as RAG context.

Spec: docs/Full_work/specs/negotiation-spec.md section 5 (Response Generator)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.core.json_repair import extract_json

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Agent name used for LLMClient model selection (Tier 3: Content+Review)
# ---------------------------------------------------------------------------

_AGENT_NAME = "negotiation_responder"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GeneratedResponse:
    """Result of response generation."""

    text: str
    handler: str  # which handler generated this
    rag_sources: list[str] = field(default_factory=list)  # IDs of similar negotiations used
    auto_send: bool = False  # True if safe to auto-send, False if needs HITL


# ---------------------------------------------------------------------------
# Handler-specific system prompts
# ---------------------------------------------------------------------------

# Handlers that are safe to auto-send without HITL review.
AUTO_SEND_HANDLERS: frozenset[str] = frozenset({"clarification", "timeline", "portfolio", "general"})

_BASE_TONE = (
    "Write like a real person -- a helpful neighbour, not a salesman. "
    "Be specific and direct. Answer the question first, then provide context. "
    "Use natural phrasing ('Let me check', 'Good question', 'Here is what I would suggest'). "
    "NEVER use AI markers ('As an AI', 'I would be happy to'). "
    "NEVER use urgency pressure ('limited time', 'act now'). "
    "Keep the response concise (2-5 sentences)."
)

HANDLER_PROMPTS: dict[str, str] = {
    "clarification": (
        "You are responding to a client's clarifying question about a freelance project.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client wants to understand the project process, approach, or details better. "
        "Give a clear, specific answer referencing the original bid proposal. "
        "If the answer is in the proposal, quote the relevant part briefly. "
        "If not, provide a thoughtful explanation based on the job requirements.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "technical": (
        "You are responding to a technical question about a freelance project.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client is asking about technology stack, architecture, tools, or implementation details. "
        "Be specific about technologies mentioned in the bid. "
        "Demonstrate technical competence without jargon overload. "
        "If you are unsure about a specific technical detail, say so honestly "
        "and offer to research it.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "timeline": (
        "You are responding to a question about project timeline or deadlines.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client wants to know about delivery dates, milestones, or scheduling. "
        "Reference the delivery timeline from the original bid. "
        "Break down the timeline into phases if the project is complex. "
        "Be realistic -- do not over-promise.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "pricing": (
        "You are responding to a pricing question from a freelance client.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client is asking about cost, budget, or discounts. "
        "This is a sensitive topic -- be transparent about what the price includes. "
        "Reference the value delivered, not just the cost. "
        "Do NOT commit to price changes without operator approval. "
        "If the client suggests a lower price, acknowledge it and explain "
        "that you will review and get back to them.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "portfolio": (
        "You are responding to a client's request for portfolio or examples.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client wants to see past work, examples, or similar projects. "
        "Reference relevant experience from the bid proposal and RAG context. "
        "Describe specific projects with concrete outcomes (metrics, results). "
        "Offer to share more details if they want a deeper look.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "acceptance": (
        "You are confirming a client's acceptance of your freelance bid.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client has indicated they want to hire you. "
        "Express genuine gratitude (not over-the-top). "
        "Briefly confirm next steps: what you need from them to start, "
        "expected first milestone, and how you will communicate. "
        "Keep it professional and enthusiastic.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "rejection": (
        "You are responding gracefully to a client who declined your bid.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client has decided not to proceed. "
        "Be gracious -- thank them for considering you. "
        "Keep it short (1-2 sentences). "
        "Optionally mention you are available if they need help in the future. "
        "Do NOT try to change their mind.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "counter_offer": (
        "You are responding to a client's counter-offer on price.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client has proposed a different price than your bid. "
        "Acknowledge their budget concerns respectfully. "
        "Do NOT accept or reject the counter-offer -- that decision requires operator review. "
        "Instead, confirm you have received their proposal and will review it. "
        "If helpful, briefly mention what the original price includes.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "scope_change": (
        "You are responding to a client who wants to change or expand the project scope.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client has requested additional features or changes not in the original brief. "
        "Acknowledge the request positively. "
        "Do NOT commit to cost or timeline changes -- that requires operator review. "
        "Mention that you will assess the impact and get back to them with options. "
        "Show that you understand what they are asking for.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
    "general": (
        "You are responding to a general message from a freelance client.\n\n"
        f"{_BASE_TONE}\n\n"
        "The client's message does not fall into a specific category. "
        "Respond naturally and helpfully. "
        "If the message is a greeting, respond warmly. "
        "If it contains useful information, acknowledge it. "
        "Keep the conversation moving toward project progress.\n\n"
        "Respond with ONLY the message text, no JSON, no metadata."
    ),
}

# Fallback prompt when handler_name is not in HANDLER_PROMPTS.
_FALLBACK_PROMPT = (
    "You are responding to a message in a freelance project negotiation.\n\n"
    f"{_BASE_TONE}\n\n"
    "Respond helpfully and professionally.\n\n"
    "Respond with ONLY the message text, no JSON, no metadata."
)


# ---------------------------------------------------------------------------
# ResponseGenerator
# ---------------------------------------------------------------------------


class ResponseGenerator:
    """Generate AI responses for negotiation messages using LLM + RAG.

    Parameters
    ----------
    llm_client:
        An :class:`~src.core.llm_client.LLMClient` instance for calling LLMs.
    experience_store:
        Optional :class:`~src.knowledge.experience_store.ExperienceStore` for
        retrieving similar successful negotiations as RAG context.
    """

    def __init__(
        self,
        llm_client: Any,
        experience_store: Any | None = None,
    ) -> None:
        self._llm = llm_client
        self._rag = experience_store

    async def generate(
        self,
        bid: dict[str, Any],
        conversation: list[dict[str, Any]],
        classification: dict[str, Any],
        handler_name: str,
    ) -> GeneratedResponse:
        """Generate a response based on message classification and handler.

        Parameters
        ----------
        bid:
            Dict with at least ``amount``, ``delivery_days``, ``proposal_text``,
            and optionally ``job_snapshot`` (job title/description).
        conversation:
            Ordered list of conversation messages.  Each dict should have at
            least ``role`` (``"client"`` or ``"agent"``) and ``content``.
        classification:
            The classifier output dict with ``message_type``, ``sentiment``,
            ``urgency``, ``confidence``, etc.
        handler_name:
            One of the 10 QUESTION_TYPES handler keys (e.g. ``"clarification"``,
            ``"counter_offer"``).

        Returns
        -------
        GeneratedResponse
            The generated text, handler metadata, RAG source IDs, and
            whether the response is safe to auto-send.
        """
        # 1. Retrieve similar negotiations from RAG (if available)
        rag_context: list[dict[str, Any]] = []
        rag_source_ids: list[str] = []

        if self._rag is not None:
            rag_context, rag_source_ids = await self._retrieve_rag_context(conversation)

        # 2. Build the user prompt with full negotiation context
        user_context = self._build_user_context(bid, conversation, classification, rag_context)

        # 3. Select handler-specific system prompt
        system_prompt = HANDLER_PROMPTS.get(handler_name, _FALLBACK_PROMPT)

        # 4. Call LLM
        text = await self._call_llm(system_prompt, user_context)

        # 5. Determine auto_send eligibility
        auto_send = handler_name in AUTO_SEND_HANDLERS

        return GeneratedResponse(
            text=text,
            handler=handler_name,
            rag_sources=rag_source_ids,
            auto_send=auto_send,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _retrieve_rag_context(
        self,
        conversation: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Retrieve similar negotiations from ExperienceStore.

        Returns a tuple of (context_list, source_ids).  Never raises -- on
        any failure an empty result is returned.
        """
        if not conversation:
            return [], []

        last_message = conversation[-1].get("content", "")
        if not last_message:
            return [], []

        try:
            results = await self._rag.retrieve_context(
                query=last_message,
                category="negotiation",
                top_k=3,
            )
        except Exception:  # noqa: BLE001 -- RAG must never block response generation
            logger.warning("negotiation.rag_retrieval_failed", exc_info=True)
            return [], []

        source_ids = [r.get("metadata", {}).get("id", "") for r in results if r.get("metadata", {}).get("id")]

        return results, source_ids

    def _build_user_context(
        self,
        bid: dict[str, Any],
        conversation: list[dict[str, Any]],
        classification: dict[str, Any],
        rag_context: list[dict[str, Any]],
    ) -> str:
        """Build the user-role prompt content with all negotiation context."""
        sections: list[str] = []

        # Job and bid context
        job_snapshot = bid.get("job_snapshot", {})
        sections.append(
            "=== JOB CONTEXT ===\n"
            f"Title: {job_snapshot.get('title', 'N/A')}\n"
            f"Description: {job_snapshot.get('description', 'N/A')[:500]}\n"
        )

        sections.append(
            "=== ORIGINAL BID ===\n"
            f"Amount: ${bid.get('amount', 0):.0f}\n"
            f"Delivery: {bid.get('delivery_days', 'N/A')} days\n"
            f"Proposal summary: {str(bid.get('proposal_text', ''))[:500]}\n"
        )

        # Conversation history (last 10 messages to stay within context)
        recent = conversation[-10:] if len(conversation) > 10 else conversation
        if recent:
            history_lines: list[str] = []
            for msg in recent:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                prefix = "Agent" if role == "agent" else "Client"
                history_lines.append(f"  {prefix}: {content}")
            sections.append("=== CONVERSATION HISTORY ===\n" + "\n".join(history_lines))

        # Classification result
        sections.append(
            "=== MESSAGE CLASSIFICATION ===\n"
            f"Type: {classification.get('message_type', 'unknown')}\n"
            f"Sentiment: {classification.get('sentiment', 'neutral')}\n"
            f"Urgency: {classification.get('urgency', 'medium')}\n"
            f"Confidence: {classification.get('confidence', 0):.0%}\n"
        )

        # RAG context from similar negotiations
        if rag_context:
            rag_lines: list[str] = []
            for i, ctx in enumerate(rag_context, 1):
                similarity = ctx.get("similarity_score", 0)
                content = str(ctx.get("content", ""))[:300]
                rag_lines.append(f"  [{i}] (similarity={similarity:.2f}) {content}")
            sections.append("=== SIMILAR SUCCESSFUL NEGOTIATIONS ===\n" + "\n".join(rag_lines))

        # Final instruction
        sections.append(
            "=== INSTRUCTION ===\n"
            "Generate a single response message to the client based on the "
            "context above. Write ONLY the message text."
        )

        return "\n\n".join(sections)

    async def _call_llm(self, system_prompt: str, user_content: str) -> str:
        """Call the LLM and extract the response text.

        Uses temperature=0.4 and max_tokens=800 per spec.
        """
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

        try:
            response, _metrics = await self._llm.call(
                _AGENT_NAME,
                messages,
                temperature=0.4,
                max_tokens=800,
            )
        except Exception:
            logger.exception("negotiation.llm_call_failed")
            raise

        text = str(response.content).strip()

        # Guard: if the LLM accidentally returned JSON, extract the text field
        if text.startswith("{"):
            try:
                data = extract_json(text)
                if isinstance(data, dict) and "text" in data:
                    text = str(data["text"]).strip()
            except (ValueError, KeyError):
                pass  # Not JSON -- use the raw text

        return text
