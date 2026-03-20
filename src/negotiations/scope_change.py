"""Scope change detection and handling.

Uses LLM to compare a client's latest message against the original job
requirements and bid proposal.  When a scope change is detected, the handler
generates three strategic options (include, phase_2, revise) with estimated
cost and time deltas.

Spec: docs/Full_work/specs/negotiation-spec.md section 7 (Scope Change Handler)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.core.json_repair import extract_json

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Agent name used for LLMClient model selection
# ---------------------------------------------------------------------------

_AGENT_NAME = "negotiation_responder"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeChangeOption:
    """A single response option for a scope change."""

    name: str  # "include" | "phase_2" | "revise"
    description: str
    cost_delta: float
    time_delta_days: int


@dataclass(slots=True)
class ScopeChangePayload:
    """Result of scope change analysis, suitable for HITL payload."""

    original_scope: str
    requested_changes: str
    estimated_additional_cost: float
    estimated_additional_time_days: int
    options: list[dict[str, Any]] = field(default_factory=list)
    conversation_summary: str = ""


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_SCOPE_CHANGE_SYSTEM_PROMPT = """\
You are an expert freelance project analyst. Your task is to compare a \
client's latest message with the original job requirements and bid proposal \
to determine if the client is requesting a scope change.

Analyze carefully:
1. Is this a genuine scope change (new requirement NOT in the original brief)?
2. If yes, estimate the additional cost in USD and additional time in days.
3. Generate exactly 3 response options with different strategies.

Respond with ONLY valid JSON in this exact format:
{
  "is_scope_change": true,
  "new_requirements": "Brief description of what the client is requesting",
  "estimated_additional_cost": 0,
  "estimated_additional_days": 0,
  "options": [
    {
      "name": "include",
      "description": "Include the changes in the current project",
      "cost_delta": 0,
      "time_delta_days": 0
    },
    {
      "name": "phase_2",
      "description": "Defer the changes to a Phase 2",
      "cost_delta": 0,
      "time_delta_days": 0
    },
    {
      "name": "revise",
      "description": "Revise scope — include new, reduce something else",
      "cost_delta": 0,
      "time_delta_days": 0
    }
  ]
}

If the message is NOT a scope change, return:
{
  "is_scope_change": false,
  "new_requirements": "",
  "estimated_additional_cost": 0,
  "estimated_additional_days": 0,
  "options": []
}"""


# ---------------------------------------------------------------------------
# ScopeChangeHandler
# ---------------------------------------------------------------------------


class ScopeChangeHandler:
    """Detect and analyze scope change requests from clients.

    Uses LLM to compare the client's message against original requirements,
    then generates three strategic options for the operator to choose from.

    Parameters
    ----------
    llm_client:
        An :class:`~src.core.llm_client.LLMClient` instance.  If ``None``,
        the handler falls back to a heuristic-only analysis (no LLM call).
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm = llm_client

    async def analyze(
        self,
        original_requirements: str,
        message_content: str,
        current_amount: float,
        current_days: int,
        *,
        proposal_text: str = "",
        conversation_summary: str = "",
    ) -> ScopeChangePayload:
        """Analyze a potential scope change request.

        Parameters
        ----------
        original_requirements:
            The original job description / requirements text.
        message_content:
            The client's latest message that may contain a scope change.
        current_amount:
            Current bid amount in USD (used for proportional estimation).
        current_days:
            Current delivery timeline in days.
        proposal_text:
            The original bid proposal text (optional, improves accuracy).
        conversation_summary:
            Summary of conversation so far (for context in the HITL payload).

        Returns
        -------
        ScopeChangePayload
            Analysis result with options.  If the LLM determines this is NOT
            a scope change, ``estimated_additional_cost`` and
            ``estimated_additional_time_days`` will both be 0 and ``options``
            will be empty.
        """
        if self._llm is not None:
            return await self._analyze_with_llm(
                original_requirements,
                message_content,
                current_amount,
                current_days,
                proposal_text,
                conversation_summary,
            )

        # Fallback: heuristic-only analysis when no LLM is available
        return self._analyze_heuristic(
            original_requirements,
            message_content,
            current_amount,
            current_days,
            conversation_summary,
        )

    # ------------------------------------------------------------------
    # LLM-powered analysis
    # ------------------------------------------------------------------

    async def _analyze_with_llm(
        self,
        original_requirements: str,
        message_content: str,
        current_amount: float,
        current_days: int,
        proposal_text: str,
        conversation_summary: str,
    ) -> ScopeChangePayload:
        """Run full LLM analysis for scope change detection."""
        user_prompt = (
            f"=== ORIGINAL JOB REQUIREMENTS ===\n"
            f"{original_requirements[:2000]}\n\n"
            f"=== ORIGINAL BID PROPOSAL ===\n"
            f"{proposal_text[:1000] if proposal_text else 'N/A'}\n\n"
            f"=== CURRENT BID ===\n"
            f"Amount: ${current_amount:.0f}\n"
            f"Delivery: {current_days} days\n\n"
            f"=== CLIENT'S LATEST MESSAGE ===\n"
            f"{message_content}\n\n"
            f"Analyze whether this message contains a scope change request."
        )

        messages = [
            SystemMessage(content=_SCOPE_CHANGE_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        try:
            response, _metrics = await self._llm.call(
                _AGENT_NAME,
                messages,
                temperature=0.3,
                max_tokens=1000,
            )
        except Exception:
            logger.exception("scope_change.llm_call_failed")
            # Fall back to heuristic on LLM failure
            return self._analyze_heuristic(
                original_requirements,
                message_content,
                current_amount,
                current_days,
                conversation_summary,
            )

        raw = str(response.content).strip()

        try:
            data = extract_json(raw)
        except ValueError:
            logger.warning(
                "scope_change.json_parse_failed",
                raw_preview=raw[:200],
            )
            return self._analyze_heuristic(
                original_requirements,
                message_content,
                current_amount,
                current_days,
                conversation_summary,
            )

        if not isinstance(data, dict):
            return self._analyze_heuristic(
                original_requirements,
                message_content,
                current_amount,
                current_days,
                conversation_summary,
            )

        return self._build_payload_from_llm(data, original_requirements, message_content, conversation_summary)

    def _build_payload_from_llm(
        self,
        data: dict[str, Any],
        original_requirements: str,
        message_content: str,
        conversation_summary: str,
    ) -> ScopeChangePayload:
        """Build a ScopeChangePayload from parsed LLM JSON output."""
        is_scope_change = bool(data.get("is_scope_change", False))

        if not is_scope_change:
            return ScopeChangePayload(
                original_scope=original_requirements[:500],
                requested_changes="",
                estimated_additional_cost=0.0,
                estimated_additional_time_days=0,
                options=[],
                conversation_summary=conversation_summary,
            )

        # Extract and normalize options
        raw_options = data.get("options", [])
        options: list[dict[str, Any]] = []
        for opt in raw_options:
            if not isinstance(opt, dict):
                continue
            options.append(
                {
                    "name": str(opt.get("name", "unknown")),
                    "description": str(opt.get("description", "")),
                    "cost_delta": float(opt.get("cost_delta", 0)),
                    "time_delta_days": int(opt.get("time_delta_days", 0)),
                }
            )

        # Ensure we always have 3 options if scope change detected
        if len(options) < 3:
            options = self._generate_default_options(
                float(data.get("estimated_additional_cost", 0)),
                int(data.get("estimated_additional_days", 0)),
            )

        additional_cost = max(0.0, float(data.get("estimated_additional_cost", 0)))
        additional_days = max(0, int(data.get("estimated_additional_days", 0)))

        return ScopeChangePayload(
            original_scope=original_requirements[:500],
            requested_changes=str(data.get("new_requirements", message_content[:300])),
            estimated_additional_cost=additional_cost,
            estimated_additional_time_days=additional_days,
            options=options,
            conversation_summary=conversation_summary,
        )

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _analyze_heuristic(
        self,
        original_requirements: str,
        message_content: str,
        current_amount: float,
        current_days: int,
        conversation_summary: str,
    ) -> ScopeChangePayload:
        """Simple heuristic analysis when LLM is unavailable.

        Assumes any message reaching this handler IS a scope change (since
        the classifier already tagged it as ``scope_change``).  Estimates
        additional cost as 20% of current bid and 3 extra days.
        """
        estimated_cost = round(current_amount * 0.20, 2)
        estimated_days = max(1, current_days // 5) if current_days > 0 else 3

        options = self._generate_default_options(estimated_cost, estimated_days)

        logger.info(
            "scope_change.heuristic_fallback",
            estimated_cost=estimated_cost,
            estimated_days=estimated_days,
        )

        return ScopeChangePayload(
            original_scope=original_requirements[:500],
            requested_changes=message_content[:300],
            estimated_additional_cost=estimated_cost,
            estimated_additional_time_days=estimated_days,
            options=options,
            conversation_summary=conversation_summary,
        )

    @staticmethod
    def _generate_default_options(
        additional_cost: float,
        additional_days: int,
    ) -> list[dict[str, Any]]:
        """Generate the 3 standard scope change options.

        Returns
        -------
        list[dict[str, Any]]
            Three options: include, phase_2, revise.
        """
        return [
            {
                "name": "include",
                "description": (
                    f"Include the changes in the current project. "
                    f"This adds approximately ${additional_cost:.0f} "
                    f"and {additional_days} day(s) to the timeline."
                ),
                "cost_delta": additional_cost,
                "time_delta_days": additional_days,
            },
            {
                "name": "phase_2",
                "description": (
                    "Defer the new requirements to a Phase 2 after "
                    "initial delivery. No change to current cost or timeline."
                ),
                "cost_delta": 0.0,
                "time_delta_days": 0,
            },
            {
                "name": "revise",
                "description": (
                    "Include the new requirements but reduce scope elsewhere "
                    "to stay within the original budget and timeline."
                ),
                "cost_delta": 0.0,
                "time_delta_days": 0,
            },
        ]
