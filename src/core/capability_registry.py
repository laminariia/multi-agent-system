"""Capability Registry -- in-memory registry of system capabilities per agent.

Tracks what each agent can do, with confidence scores, tooling, examples,
and limitations. Used by Scout for Capability Reports and by Bid Agent
for honest proposal generation.

Supports outcome recording for future Dynamic Capability Registry
(confidence recalculation based on project results).

Spec: docs/Full_work/specs/infrastructure-spec.md SS4 Scout Capability Report
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Capability model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Capability:
    """A single capability the system can perform.

    Attributes:
        name: Human-readable capability name (e.g. "React / Next.js").
        category: Grouping category (frontend, backend, database, etc.).
        confidence: Confidence score 0.0-1.0 for successful delivery.
        agent: Primary agent responsible (dev, content, design, etc.).
        tools: External tools/CLIs used for this capability.
        examples: Example deliverables or project types.
        limitations: Known limitations or exclusions.
        last_used_at: Timestamp of last usage (for decay tracking).
    """

    name: str
    category: str
    confidence: float
    agent: str
    tools: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    last_used_at: datetime | None = None


# ---------------------------------------------------------------------------
# Default AGENT_CAPABILITIES from spec CAPABILITY_MATRIX
# ---------------------------------------------------------------------------

AGENT_CAPABILITIES: dict[str, Capability] = {
    # === Frontend ===
    "react_nextjs": Capability(
        name="React / Next.js",
        category="frontend",
        confidence=0.95,
        agent="dev",
        tools=["create-next-app", "vercel-cli"],
        examples=["SPA dashboard", "SSR landing page", "E-commerce storefront"],
        limitations=["No React Native (mobile)", "No Electron (desktop)"],
    ),
    "html_css_tailwind": Capability(
        name="HTML/CSS/Tailwind",
        category="frontend",
        confidence=0.98,
        agent="dev",
        tools=["tailwindcss", "postcss"],
        examples=["Landing pages", "Email templates", "Static sites"],
        limitations=[],
    ),
    "vue_svelte": Capability(
        name="Vue.js / Svelte",
        category="frontend",
        confidence=0.85,
        agent="dev",
        tools=["vue-cli", "sveltekit"],
        examples=["Interactive dashboards", "Admin panels"],
        limitations=["Less experience than React"],
    ),
    # === CMS ===
    "wordpress": Capability(
        name="WordPress",
        category="cms",
        confidence=0.90,
        agent="dev",
        tools=["wp-cli", "elementor"],
        examples=["Business sites", "Blogs", "WooCommerce stores"],
        limitations=["Custom plugin development may need review"],
    ),
    "shopify_webflow": Capability(
        name="Shopify / Webflow",
        category="cms",
        confidence=0.80,
        agent="dev",
        tools=["shopify-cli", "webflow-api"],
        examples=["E-commerce", "Portfolio sites"],
        limitations=["Limited custom backend logic"],
    ),
    # === Backend ===
    "python_litestar": Capability(
        name="Python / Litestar / FastAPI",
        category="backend",
        confidence=0.95,
        agent="dev",
        tools=["poetry", "alembic", "pytest"],
        examples=["REST APIs", "WebSocket servers", "Background workers"],
        limitations=[],
    ),
    "nodejs_express": Capability(
        name="Node.js / Express",
        category="backend",
        confidence=0.90,
        agent="dev",
        tools=["npm", "prisma"],
        examples=["REST APIs", "GraphQL servers", "Microservices"],
        limitations=[],
    ),
    # === Database ===
    "postgresql": Capability(
        name="PostgreSQL",
        category="database",
        confidence=0.95,
        agent="dev",
        tools=["psql", "pgvector", "alembic"],
        examples=["Schema design", "Migrations", "Vector search"],
        limitations=[],
    ),
    # === DevOps ===
    "docker_deploy": Capability(
        name="Docker / Deploy",
        category="devops",
        confidence=0.90,
        agent="dev",
        tools=["docker-compose", "railway-cli", "vercel-cli", "netlify-cli"],
        examples=["Containerized apps", "CI/CD pipelines", "Railway deploy"],
        limitations=["No Kubernetes", "No AWS/GCP/Azure native services"],
    ),
    # === Content ===
    "copywriting": Capability(
        name="Copywriting / Content",
        category="content",
        confidence=0.90,
        agent="content",
        tools=[],
        examples=["Landing page copy", "Blog posts", "Email sequences", "UI text"],
        limitations=["No video production", "No podcast editing"],
    ),
    # === Design ===
    "ui_ux_design": Capability(
        name="UI/UX Design",
        category="design",
        confidence=0.85,
        agent="design",
        tools=["pencil.dev", "figma (read-only)"],
        examples=["Wireframes", "Mockups", "Design systems", "Responsive layouts"],
        limitations=["No 3D modeling", "No animation (complex)", "No print design"],
    ),
}


# ---------------------------------------------------------------------------
# Reject categories -- project types we should not bid on
# ---------------------------------------------------------------------------

REJECT_CATEGORIES: dict[str, str] = {
    "mobile_app": "Native mobile app development (iOS/Android) is outside our capability set.",
    "ai_ml": "Custom AI/ML model training and deployment requires specialized infrastructure.",
    "blockchain": "Blockchain / Web3 / smart contract development is not supported.",
    "erp": "Enterprise ERP system implementation (SAP, Oracle, etc.) is out of scope.",
    "game_dev": "Game development (Unity, Unreal, etc.) is not in our capability set.",
    "embedded": "Embedded systems / IoT firmware development requires hardware expertise.",
    "desktop_app": "Desktop application development (Electron excluded, native Win/Mac/Linux) is not supported.",
}


# ---------------------------------------------------------------------------
# Outcome record
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _OutcomeRecord:
    """Internal record of a capability outcome."""

    success: bool
    quality_score: float | None
    recorded_at: str


# ---------------------------------------------------------------------------
# CapabilityRegistry
# ---------------------------------------------------------------------------


class CapabilityRegistry:
    """In-memory registry of system capabilities.

    Provides registration, discovery, reporting, confidence management,
    and outcome tracking for agent capabilities.
    """

    # Confidence recalculation weights (from spec):
    #   new_confidence = HISTORICAL_WEIGHT * old + RECENT_WEIGHT * recent_signal
    _HISTORICAL_WEIGHT: float = 0.7
    _RECENT_WEIGHT: float = 0.3
    _HIGH_CONFIDENCE_THRESHOLD: float = 0.9

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._outcome_history: dict[str, list[_OutcomeRecord]] = {}

    # --- Registration ---

    def register_capability(self, capability_id: str, capability: Capability) -> None:
        """Register or overwrite a capability by ID."""
        self._capabilities[capability_id] = capability
        logger.debug(
            "capability_registry.registered",
            capability_id=capability_id,
            name=capability.name,
            category=capability.category,
            agent=capability.agent,
        )

    def unregister(self, capability_id: str) -> bool:
        """Remove a capability. Returns True if it existed."""
        if capability_id in self._capabilities:
            del self._capabilities[capability_id]
            self._outcome_history.pop(capability_id, None)
            logger.info("capability_registry.unregistered", capability_id=capability_id)
            return True
        return False

    def get(self, capability_id: str) -> Capability | None:
        """Get a capability by ID, or None if not found."""
        return self._capabilities.get(capability_id)

    def list_all(self) -> dict[str, Capability]:
        """Return a copy of all registered capabilities."""
        return dict(self._capabilities)

    def clear(self) -> None:
        """Remove all capabilities and outcome history."""
        self._capabilities.clear()
        self._outcome_history.clear()
        logger.info("capability_registry.cleared")

    def load_defaults(self) -> None:
        """Load AGENT_CAPABILITIES into the registry (idempotent)."""
        for cap_id, cap in AGENT_CAPABILITIES.items():
            self._capabilities[cap_id] = Capability(
                name=cap.name,
                category=cap.category,
                confidence=cap.confidence,
                agent=cap.agent,
                tools=list(cap.tools),
                examples=list(cap.examples),
                limitations=list(cap.limitations),
            )
        logger.info(
            "capability_registry.defaults_loaded",
            count=len(AGENT_CAPABILITIES),
        )

    # --- Discovery ---

    def find_by_category(self, category: str) -> dict[str, Capability]:
        """Find all capabilities in a given category (case-insensitive)."""
        cat_lower = category.lower()
        return {cid: cap for cid, cap in self._capabilities.items() if cap.category.lower() == cat_lower}

    def find_by_name(self, query: str) -> dict[str, Capability]:
        """Find capabilities whose name contains the query (case-insensitive)."""
        q_lower = query.lower()
        return {cid: cap for cid, cap in self._capabilities.items() if q_lower in cap.name.lower()}

    def find_capable_agents(self, capability_id: str) -> list[str]:
        """Return list of agent names that have this capability ID."""
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return []
        return [cap.agent]

    def find_capable_agents_by_category(self, category: str) -> list[str]:
        """Return deduplicated list of agents that have capabilities in a category."""
        caps = self.find_by_category(category)
        seen: set[str] = set()
        result: list[str] = []
        for cap in caps.values():
            if cap.agent not in seen:
                seen.add(cap.agent)
                result.append(cap.agent)
        return result

    def list_categories(self) -> list[str]:
        """Return sorted list of all unique categories."""
        cats: set[str] = set()
        for cap in self._capabilities.values():
            cats.add(cap.category.lower())
        return sorted(cats)

    # --- Confidence management ---

    def update_confidence(self, capability_id: str, new_confidence: float) -> bool:
        """Update confidence for a capability (clamped to [0.0, 1.0]).

        Returns True if capability exists, False otherwise.
        """
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return False
        clamped = max(0.0, min(1.0, new_confidence))
        cap.confidence = clamped
        logger.debug(
            "capability_registry.confidence_updated",
            capability_id=capability_id,
            new_confidence=clamped,
        )
        return True

    # --- Outcome recording ---

    def record_outcome(
        self,
        capability_id: str,
        *,
        success: bool,
        quality_score: float | None = None,
    ) -> bool:
        """Record a project outcome and adjust confidence.

        Uses weighted formula from spec:
          new_confidence = 0.7 * historical + 0.3 * recent_signal

        Where recent_signal is quality_score (if provided) or 1.0/0.0
        based on success.

        Returns True if capability exists, False otherwise.
        """
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return False

        # Record to history
        record = _OutcomeRecord(
            success=success,
            quality_score=quality_score,
            recorded_at=datetime.now(UTC).isoformat(),
        )
        if capability_id not in self._outcome_history:
            self._outcome_history[capability_id] = []
        self._outcome_history[capability_id].append(record)

        # Recalculate confidence
        if quality_score is not None:
            recent_signal = quality_score
        else:
            recent_signal = 1.0 if success else 0.0

        new_conf = self._HISTORICAL_WEIGHT * cap.confidence + self._RECENT_WEIGHT * recent_signal
        cap.confidence = max(0.0, min(1.0, round(new_conf, 4)))

        logger.info(
            "capability_registry.outcome_recorded",
            capability_id=capability_id,
            success=success,
            quality_score=quality_score,
            new_confidence=cap.confidence,
        )
        return True

    def get_outcome_history(self, capability_id: str) -> list[dict[str, Any]]:
        """Return outcome history for a capability as list of dicts."""
        records = self._outcome_history.get(capability_id, [])
        return [
            {
                "success": r.success,
                "quality_score": r.quality_score,
                "recorded_at": r.recorded_at,
            }
            for r in records
        ]

    # --- Limitations ---

    def get_limitations(self, capability_id: str) -> list[str]:
        """Get limitations for a specific capability."""
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return []
        return list(cap.limitations)

    def get_all_limitations(self) -> dict[str, list[str]]:
        """Return limitations for all capabilities (keyed by capability_id)."""
        return {cid: list(cap.limitations) for cid, cap in self._capabilities.items() if cap.limitations}

    # --- Reporting ---

    def get_capability_report(self) -> dict[str, Any]:
        """Generate a full capability report across the registry.

        Returns:
            Dict with total_capabilities, categories (with counts and
            avg_confidence per category), agents list, avg_confidence,
            and high_confidence_count.
        """
        caps = self._capabilities

        if not caps:
            return {
                "total_capabilities": 0,
                "categories": {},
                "agents": [],
                "avg_confidence": 0.0,
                "high_confidence_count": 0,
            }

        # Aggregate by category
        category_data: dict[str, list[float]] = {}
        agents: set[str] = set()
        total_confidence = 0.0
        high_count = 0

        for cap in caps.values():
            cat = cap.category.lower()
            if cat not in category_data:
                category_data[cat] = []
            category_data[cat].append(cap.confidence)
            agents.add(cap.agent)
            total_confidence += cap.confidence
            if cap.confidence >= self._HIGH_CONFIDENCE_THRESHOLD:
                high_count += 1

        categories_report: dict[str, dict[str, Any]] = {}
        for cat, confidences in category_data.items():
            categories_report[cat] = {
                "count": len(confidences),
                "avg_confidence": round(sum(confidences) / len(confidences), 4),
            }

        avg_conf = round(total_confidence / len(caps), 4)

        return {
            "total_capabilities": len(caps),
            "categories": categories_report,
            "agents": sorted(agents),
            "avg_confidence": avg_conf,
            "high_confidence_count": high_count,
        }

    # --- Reject categories ---

    def should_reject(self, category: str) -> tuple[bool, str]:
        """Check whether a project category should be rejected.

        Args:
            category: The project category to check (case-insensitive,
                underscores and hyphens are normalised).

        Returns:
            A tuple of ``(should_reject, reason)``.  When the category
            is not in ``REJECT_CATEGORIES``, returns ``(False, "")``.
        """
        normalised = category.strip().lower().replace("-", "_")
        reason = REJECT_CATEGORIES.get(normalised, "")
        if reason:
            logger.info(
                "capability_registry.rejected_category",
                category=normalised,
                reason=reason,
            )
            return (True, reason)
        return (False, "")

    # --- Usage tracking ---

    def touch(self, capability_id: str) -> bool:
        """Update ``last_used_at`` to current UTC time for a capability.

        Returns True if the capability exists, False otherwise.
        """
        cap = self._capabilities.get(capability_id)
        if cap is None:
            return False
        cap.last_used_at = datetime.now(UTC)
        return True

    def apply_decay(self, *, days: int = 90, factor: float = 0.9) -> int:
        """Apply confidence decay to capabilities not used within *days*.

        Capabilities whose ``last_used_at`` is older than *days* ago (or
        is ``None``) have their confidence multiplied by *factor*.

        Args:
            days: Number of days of inactivity before decay applies.
            factor: Multiplicative decay factor (0.0-1.0).

        Returns:
            Number of capabilities that were decayed.
        """
        factor = max(0.0, min(1.0, factor))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        decayed = 0

        for cap_id, cap in self._capabilities.items():
            if cap.last_used_at is None or cap.last_used_at < cutoff:
                old_conf = cap.confidence
                cap.confidence = round(max(0.0, cap.confidence * factor), 4)
                decayed += 1
                logger.debug(
                    "capability_registry.decay_applied",
                    capability_id=cap_id,
                    old_confidence=old_conf,
                    new_confidence=cap.confidence,
                    last_used_at=cap.last_used_at,
                )

        if decayed > 0:
            logger.info(
                "capability_registry.decay_complete",
                decayed_count=decayed,
                days=days,
                factor=factor,
            )

        return decayed
