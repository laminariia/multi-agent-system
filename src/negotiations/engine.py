"""Negotiation Engine — lifecycle management for client negotiations.

In-memory store for Phase 2. Database-backed in Phase 3+.

Spec: docs/Full_work/specs/negotiation-spec.md
Plan: docs/plans/2026-03-11-negotiation-engine-plan.md
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog

from src.negotiations.models import Negotiation
from src.negotiations.state_machine import NegotiationStateMachine

logger = structlog.get_logger(__name__)


class NegotiationEngine:
    """Manage negotiation lifecycles.

    Phase 2: in-memory dict store.
    Phase 3+: backed by PostgreSQL via asyncpg.
    """

    def __init__(self) -> None:
        self._negotiations: dict[str, Negotiation] = {}

    def create_negotiation(
        self,
        bid_id: str,
        platform: str,
        initial_state: str = "initial",
    ) -> Negotiation:
        """Create a new negotiation for a bid.

        Returns the created :class:`Negotiation`.
        """
        neg_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        neg = Negotiation(
            id=neg_id,
            bid_id=bid_id,
            platform=platform,
            state_machine=NegotiationStateMachine(current_state=initial_state),
            messages=[],
            created_at=now,
            updated_at=now,
        )

        self._negotiations[neg_id] = neg

        logger.info(
            "negotiation.created",
            id=neg_id,
            bid_id=bid_id,
            platform=platform,
            state=initial_state,
        )
        return neg

    def get_negotiation(self, negotiation_id: str) -> Negotiation | None:
        """Retrieve a negotiation by ID. Returns None if not found."""
        return self._negotiations.get(negotiation_id)

    def transition(
        self,
        negotiation_id: str,
        target: str,
        *,
        reason: str,
        actor: str = "system",
    ) -> bool:
        """Execute a state transition on a negotiation.

        Returns True on success, False if negotiation not found.
        Raises :class:`InvalidTransitionError` on invalid transition.
        """
        neg = self._negotiations.get(negotiation_id)
        if neg is None:
            return False

        neg.state_machine.transition(target, reason=reason, actor=actor)
        neg.updated_at = datetime.now(UTC)

        logger.info(
            "negotiation.transitioned",
            id=negotiation_id,
            to=target,
            reason=reason,
            actor=actor,
        )
        return True

    def get_active(self) -> list[Negotiation]:
        """Return all non-terminal negotiations."""
        return [neg for neg in self._negotiations.values() if not neg.state_machine.is_terminal]

    def mark_stale(self, threshold_days: int = 14) -> int:
        """Mark negotiations as stale if no activity for *threshold_days*.

        Skips terminal states and already-stale negotiations.
        Returns the number of negotiations marked stale.
        """
        cutoff = datetime.now(UTC) - timedelta(days=threshold_days)
        count = 0

        for neg in self._negotiations.values():
            if neg.state_machine.is_terminal:
                continue
            if neg.state == "stale":
                continue
            if neg.updated_at < cutoff:
                neg.state_machine.transition(
                    "stale",
                    reason=f"no activity for {threshold_days} days",
                    actor="system",
                )
                neg.updated_at = datetime.now(UTC)
                count += 1

                logger.info(
                    "negotiation.marked_stale",
                    id=neg.id,
                    threshold_days=threshold_days,
                )

        return count
