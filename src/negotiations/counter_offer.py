"""Counter-offer engine with 3 pricing bands.

Analyzes the difference between the original bid and a client's counter-offer,
then recommends one of three strategies: accept, negotiate, or decline/reduce
scope.  The engine is pure logic -- no LLM calls, no I/O -- so it can be used
synchronously in both the negotiation handler and the HITL payload builder.

Spec: docs/Full_work/specs/negotiation-spec.md section 6 (Counter-Offer Engine)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class CounterOfferPayload:
    """Result of counter-offer analysis, suitable for HITL payload."""

    original_bid: float
    counter_amount: float
    difference_percent: float
    recommendation: str  # accept_possible / negotiate / decline_or_reduce_scope
    suggested_responses: list[str]
    conversation_summary: str
    client_profile: dict[str, Any] | None = None


class CounterOfferEngine:
    """Analyze counter-offers and recommend negotiation strategy.

    Three pricing bands determine the recommendation:

    * **accept** (<=10% difference): The counter is close enough to accept.
    * **negotiate** (11-25% difference): There is room for a middle ground.
    * **decline_or_reduce_scope** (>25% difference): Too far apart -- reduce
      scope or walk away.

    All thresholds are class-level constants and can be overridden via
    subclassing for platform-specific tuning.
    """

    BAND_ACCEPT: float = 0.10  # <= 10%
    BAND_NEGOTIATE: float = 0.25  # 11-25%
    # > 25% = decline or reduce scope

    def analyze(
        self,
        original_amount: float,
        counter_amount: float,
        conversation_summary: str = "",
        client_profile: dict[str, Any] | None = None,
    ) -> CounterOfferPayload:
        """Analyze a counter-offer and recommend a strategy.

        Parameters
        ----------
        original_amount:
            The original bid amount in USD.
        counter_amount:
            The client's proposed amount in USD.
        conversation_summary:
            Brief summary of the negotiation so far (for HITL context).
        client_profile:
            Optional dict with client metadata (rating, hire_rate,
            total_spent) for display in the HITL interface.

        Returns
        -------
        CounterOfferPayload
            Analysis result with recommendation and suggested response
            texts ready for the HITL payload.

        Raises
        ------
        ValueError
            If ``original_amount`` is negative or zero.
        """
        if original_amount <= 0:
            msg = f"original_amount must be positive, got {original_amount}"
            raise ValueError(msg)

        if counter_amount < 0:
            msg = f"counter_amount must be non-negative, got {counter_amount}"
            raise ValueError(msg)

        diff = abs(original_amount - counter_amount)
        diff_pct = diff / original_amount

        recommendation, suggestions = self._evaluate_band(original_amount, counter_amount, diff_pct)

        payload = CounterOfferPayload(
            original_bid=original_amount,
            counter_amount=counter_amount,
            difference_percent=round(diff_pct * 100, 1),
            recommendation=recommendation,
            suggested_responses=suggestions,
            conversation_summary=conversation_summary,
            client_profile=client_profile,
        )

        logger.info(
            "counter_offer.analyzed",
            original=original_amount,
            counter=counter_amount,
            diff_pct=payload.difference_percent,
            recommendation=recommendation,
        )

        return payload

    def _evaluate_band(
        self,
        original: float,
        counter: float,
        diff_pct: float,
    ) -> tuple[str, list[str]]:
        """Determine pricing band and generate suggested responses.

        Returns
        -------
        tuple[str, list[str]]
            A (recommendation, suggested_responses) pair.
        """
        if diff_pct <= self.BAND_ACCEPT:
            return "accept_possible", [
                f"I can work within ${counter:.0f}. Shall we proceed?",
                "That works for me. Let's finalize the details.",
            ]

        if diff_pct <= self.BAND_NEGOTIATE:
            midpoint = (original + counter) / 2
            return "negotiate", [
                f"How about we meet in the middle at ${midpoint:.0f}?",
                (f"I could adjust to ${midpoint:.0f} if we trim the scope slightly."),
                (
                    f"I understand ${counter:.0f} is your target. "
                    f"Could we settle at ${midpoint:.0f} with a slightly "
                    "adjusted timeline?"
                ),
            ]

        # > 25% difference
        return "decline_or_reduce_scope", [
            ("That's quite far from my estimate. Could we discuss reducing the scope?"),
            (f"At ${counter:.0f} I'd need to simplify the approach significantly. Want me to propose a reduced scope?"),
            (
                f"The gap between ${original:.0f} and ${counter:.0f} is "
                "substantial. I could offer a phased approach -- core "
                "features first, then add-ons in Phase 2."
            ),
        ]
