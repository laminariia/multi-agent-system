"""Deal Memory — key-value context store for sales deals.

Manages facts, decisions, preferences, and conversation summaries
for each deal. In-memory store (Phase 2), ready for DB migration
to client_context table (Phase 3+).

Spec: docs/Full_work/specs/sales-agent-spec.md §Deal Memory
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class DealContext:
    """Full context for a single deal."""

    deal_id: str
    lead_id: str
    key_facts: dict[str, str] = field(default_factory=dict)
    agreed_scope: str | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    client_preferences: dict[str, str] = field(default_factory=dict)
    conversation_summary: str | None = None


class DealMemory:
    """In-memory deal context store.

    Thread-safe for single-process use. For multi-process,
    migrate to DB-backed client_context table.
    """

    def __init__(self) -> None:
        self._contexts: dict[str, DealContext] = {}

    # --- Context lifecycle ---

    def create_context(self, deal_id: str, lead_id: str) -> DealContext:
        """Create a new deal context. Returns existing if already present."""
        if deal_id in self._contexts:
            return self._contexts[deal_id]
        ctx = DealContext(deal_id=deal_id, lead_id=lead_id)
        self._contexts[deal_id] = ctx
        logger.info("deal_memory.created", deal_id=deal_id, lead_id=lead_id)
        return ctx

    def get_context(self, deal_id: str) -> DealContext | None:
        """Get full deal context or None."""
        return self._contexts.get(deal_id)

    def delete_context(self, deal_id: str) -> bool:
        """Delete deal context. Returns True if existed."""
        if deal_id in self._contexts:
            del self._contexts[deal_id]
            logger.info("deal_memory.deleted", deal_id=deal_id)
            return True
        return False

    def list_deals(self) -> list[str]:
        """Return all deal IDs with active contexts."""
        return list(self._contexts.keys())

    # --- Facts CRUD ---

    def save_fact(self, deal_id: str, key: str, value: str) -> None:
        """Save or overwrite a fact for a deal."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return
        ctx.key_facts[key] = value
        logger.debug("deal_memory.fact_saved", deal_id=deal_id, key=key)

    def get_fact(self, deal_id: str, key: str) -> str | None:
        """Get a single fact by key, or None."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return None
        return ctx.key_facts.get(key)

    def get_all_facts(self, deal_id: str) -> dict[str, str]:
        """Get all facts for a deal. Returns empty dict if deal not found."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return {}
        return dict(ctx.key_facts)

    def delete_fact(self, deal_id: str, key: str) -> bool:
        """Delete a fact. Returns True if existed."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return False
        if key in ctx.key_facts:
            del ctx.key_facts[key]
            return True
        return False

    # --- Scope, decisions, preferences ---

    def set_agreed_scope(self, deal_id: str, scope: str) -> None:
        """Set the agreed project scope."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return
        ctx.agreed_scope = scope

    def add_decision(self, deal_id: str, decision: str, *, reason: str = "") -> None:
        """Record a decision with optional reason."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return
        entry: dict[str, Any] = {
            "decision": decision,
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        ctx.decisions.append(entry)

    def set_preference(self, deal_id: str, key: str, value: str) -> None:
        """Set a client preference."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return
        ctx.client_preferences[key] = value

    def set_conversation_summary(self, deal_id: str, summary: str) -> None:
        """Update the conversation summary."""
        ctx = self._contexts.get(deal_id)
        if ctx is None:
            return
        ctx.conversation_summary = summary
