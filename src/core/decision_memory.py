"""RAG Decision Memory — learn from project outcomes (P3.13).

Records completed/failed pipeline outcomes as searchable decision patterns
in the knowledge base.  The Planner agent queries these patterns to inform
its plans with historical data: what approach worked for similar projects,
how long it took, and what went wrong.

Storage uses the existing :mod:`src.knowledge` infrastructure with
``kb_type="decision_pattern"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_KB_TYPE = "decision_pattern"


# ---------------------------------------------------------------------------
# Pattern dataclass
# ---------------------------------------------------------------------------


@dataclass
class DecisionPattern:
    """A recorded project decision and its outcome."""

    project_type: str
    requirements_summary: str
    approach: list[str]
    outcome: str  # "success" | "failed" | "partial"
    timeline_days: int
    lessons_learned: list[str] = field(default_factory=list)

    def to_content(self) -> str:
        """Render as searchable text for embedding."""
        lines = [
            f"Project type: {self.project_type}",
            f"Requirements: {self.requirements_summary}",
            f"Approach: {', '.join(self.approach)}",
            f"Outcome: {self.outcome}",
            f"Timeline: {self.timeline_days} days",
        ]
        if self.lessons_learned:
            lines.append("Lessons: " + "; ".join(self.lessons_learned))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "project_type": self.project_type,
            "requirements_summary": self.requirements_summary,
            "approach": list(self.approach),
            "outcome": self.outcome,
            "timeline_days": self.timeline_days,
            "lessons_learned": list(self.lessons_learned),
        }


# ---------------------------------------------------------------------------
# Similar-pattern result
# ---------------------------------------------------------------------------


@dataclass
class SimilarPattern:
    """A past pattern retrieved by similarity search."""

    content: str
    similarity: float
    title: str


# ---------------------------------------------------------------------------
# Decision Memory
# ---------------------------------------------------------------------------


class DecisionMemory:
    """Records and retrieves project decision patterns via RAG.

    Args:
        ingestion: A :class:`KnowledgeIngestionPipeline` instance.
        retriever: A :class:`KnowledgeRetriever` instance.
    """

    def __init__(self, ingestion: Any, retriever: Any) -> None:
        self._ingestion = ingestion
        self._retriever = retriever

    async def record_outcome(self, pattern: DecisionPattern) -> str:
        """Store a decision pattern in the knowledge base.

        Returns the document UUID.
        """
        success_rate = 1.0 if pattern.outcome == "success" else 0.0
        title = f"{pattern.project_type}: {pattern.requirements_summary[:80]}"

        doc_id = await self._ingestion.ingest_document(
            title=title,
            content=pattern.to_content(),
            kb_type=_KB_TYPE,
            category=pattern.project_type,
            success_rate=success_rate,
        )

        logger.info(
            "decision_pattern_recorded",
            doc_id=doc_id,
            project_type=pattern.project_type,
            outcome=pattern.outcome,
        )
        return doc_id

    async def find_similar(
        self,
        requirements: str,
        *,
        top_k: int = 3,
        min_similarity: float = 0.7,
    ) -> list[SimilarPattern]:
        """Find similar past decision patterns.

        Args:
            requirements: The new project requirements to match against.
            top_k: Maximum results to return.
            min_similarity: Minimum cosine similarity threshold.

        Returns:
            List of similar patterns sorted by relevance.
        """
        results = await self._retriever.search(
            requirements,
            kb_type=_KB_TYPE,
            top_k=top_k,
            min_similarity=min_similarity,
        )

        return [
            SimilarPattern(
                content=r.content,
                similarity=r.similarity,
                title=r.title,
            )
            for r in results
        ]


# ---------------------------------------------------------------------------
# State extraction helpers
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = frozenset({"completed", "delivered", "failed", "timeout"})


def extract_pattern_from_state(state: dict[str, Any]) -> DecisionPattern | None:
    """Build a DecisionPattern from a finished pipeline state.

    Returns ``None`` if the pipeline is still in progress.
    """
    status = state.get("status", "")
    if status not in _TERMINAL_STATUSES:
        return None

    project = state.get("project") or {}
    requirements = project.get("requirements", "")
    approach = state.get("agent_sequence", [])
    errors = state.get("errors", [])

    outcome = "success" if status in ("completed", "delivered") else "failed"

    # Timeline from timestamps
    timeline_days = 1
    created = state.get("created_at")
    updated = state.get("updated_at")
    if created and updated:
        try:
            t_start = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            t_end = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            delta = (t_end - t_start).days
            timeline_days = max(delta, 1)
        except (ValueError, TypeError):
            pass

    # Lessons from errors
    lessons: list[str] = []
    if errors:
        lessons = [str(e)[:200] for e in errors[:5]]

    project_type = infer_project_type(requirements)

    return DecisionPattern(
        project_type=project_type,
        requirements_summary=requirements[:500],
        approach=list(approach),
        outcome=outcome,
        timeline_days=timeline_days,
        lessons_learned=lessons,
    )


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------

_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "web_development",
        ["landing", "website", "react", "vue", "angular", "frontend", "html", "css", "next.js", "remix"],
    ),
    (
        "api_backend",
        ["api", "rest", "graphql", "backend", "server", "endpoint", "microservice", "fastapi", "django", "flask"],
    ),
    ("design", ["logo", "brand", "figma", "ui/ux", "design", "wireframe", "mockup", "prototype"]),
    ("mobile", ["mobile", "ios", "android", "react native", "flutter", "app store"]),
    ("bot", ["bot", "telegram", "discord", "chatbot", "slack"]),
    ("devops", ["docker", "kubernetes", "ci/cd", "deploy", "infrastructure", "terraform", "aws", "server setup"]),
    ("data", ["scraping", "parsing", "data", "etl", "pipeline", "analytics", "dashboard"]),
    ("ml_ai", ["machine learning", "ml", "ai", "neural", "model", "nlp", "gpt", "classification"]),
]


def infer_project_type(requirements: str) -> str:
    """Guess the project type from requirement text."""
    text = requirements.lower()
    best_type = "general"
    best_count = 0

    for ptype, keywords in _TYPE_PATTERNS:
        count = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", text))
        if count > best_count:
            best_count = count
            best_type = ptype

    return best_type
