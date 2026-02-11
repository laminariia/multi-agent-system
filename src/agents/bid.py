"""Bid Agent — generates personalised proposals for qualified freelance jobs.

CRITICAL: Every bid MUST require HITL approval before submission.
The ``requires_hitl`` field is ALWAYS set to ``True`` before this agent
returns control.  No bid is ever auto-submitted.

Role constraints: can GENERATE proposals, CANNOT submit bids directly.
LLM: Gemini 3 Flash (fallback Claude Haiku).
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.heartbeat import HeartbeatMonitor
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import AgentLog, Bid, HITLQueue, Job, KnowledgeBase
from src.core.state import AgentState, update_state
from src.knowledge.retrieval import KnowledgeRetriever
from src.prompts.bid import BID_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Bid Agent is allowed to invoke.
BID_ALLOWED_TOOLS: list[str] = [
    "generate_proposal",
    "calculate_bid_price",
    "queue_bid_for_approval",
    "get_similar_won_bids",
]


class BidAgent(ConstrainedAgent):
    """Generates personalised proposals and queues them for HITL approval.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Gemini 3 Flash primary).
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    """

    _retriever: KnowledgeRetriever | None = None

    @classmethod
    def configure_retriever(cls, retriever: KnowledgeRetriever) -> None:
        """Set the shared :class:`KnowledgeRetriever` for vector-based RAG.

        Call once at application startup after creating the retriever with a
        live ``asyncpg.Pool`` and :class:`EmbeddingService`.
        """
        cls._retriever = retriever

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
    ) -> None:
        super().__init__(
            agent_name="bid",
            allowed_tools=BID_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Generate proposals for qualified jobs, then pause for HITL approval.

        Flow:
        1. Retrieve qualified job IDs from ``state["artifacts"]["scout"]``.
        2. For each job, fetch similar won bids from knowledge_base (RAG).
        3. Call LLM with BID_SYSTEM_PROMPT to generate proposal + pricing.
        4. Create Bid record in DB with ``status='hitl_pending'``.
        5. Create HITLQueue entry (type='bid_approval').
        6. Set ``requires_hitl=True`` and ``status='paused'`` in state.
        7. Return updated state (graph will interrupt for HITL).
        """
        self._log.info("bid_execute_start", thread_id=state["thread_id"])

        # 1. Get qualified job IDs from the Scout Agent's artifacts.
        scout_artifacts: list[str] = (state.get("artifacts") or {}).get("scout", [])
        if not scout_artifacts:
            self._log.warning("no_qualified_jobs_in_artifacts")
            return update_state(state, current_agent="bid", next_agent=None, status="active")

        # Load job records from DB.
        qualified_jobs = await self._load_jobs(scout_artifacts)
        if not qualified_jobs:
            self._log.warning("no_jobs_found_in_db", ids=scout_artifacts[:5])
            return update_state(state, current_agent="bid", next_agent=None, status="active")

        self._log.info("processing_qualified_jobs", count=len(qualified_jobs))

        # Process each job: RAG lookup -> LLM proposal -> DB persist -> HITL queue.
        created_bid_ids: list[str] = []
        hitl_request_ids: list[str] = []

        for job_dict in qualified_jobs:
            # 2. Fetch similar won bids from knowledge_base (RAG).
            similar_bids = await self._fetch_similar_bids(job_dict)

            # 3. Generate proposal via LLM.
            proposal = await self._generate_proposal(job_dict, similar_bids)
            if proposal is None:
                self._log.warning("proposal_generation_failed", job_id=job_dict.get("id"))
                continue

            # CRITICAL: Enforce requires_hitl invariant.
            proposal["requires_hitl"] = True

            # 4. Create Bid record in DB.
            bid_id = await self._store_bid(job_dict, proposal)
            created_bid_ids.append(bid_id)

            # 5. Create HITL queue entry.
            hitl_id = await self._create_hitl_entry(job_dict, proposal, bid_id, state["thread_id"])
            hitl_request_ids.append(hitl_id)

            # 6. Log the action.
            await self._log_bid_generated(job_dict, bid_id, proposal)

        # 7. Build updated state with HITL pause.
        artifacts = dict(state.get("artifacts") or {})
        artifacts["bid"] = created_bid_ids

        # CRITICAL: requires_hitl MUST always be True before any bid submission.
        # Use the last HITL request ID as the state's hitl_request_id.
        final_hitl_id = hitl_request_ids[-1] if hitl_request_ids else str(uuid.uuid4())

        return update_state(
            state,
            current_agent="bid",
            next_agent=None,  # Graph will interrupt; next agent set after HITL resolution.
            artifacts=artifacts,
            requires_hitl=True,
            hitl_request_id=final_hitl_id,
            status="paused",
        )

    # ------------------------------------------------------------------
    # Job loading
    # ------------------------------------------------------------------

    async def _load_jobs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        """Load qualified jobs from the database by UUID."""
        results: list[dict[str, Any]] = []

        async with get_db_session() as session:
            for jid_str in job_ids:
                try:
                    jid = uuid.UUID(jid_str)
                except ValueError:
                    self._log.warning("invalid_job_uuid", value=jid_str)
                    continue

                stmt = select(Job).where(Job.id == jid, Job.status == "qualified")
                result = await session.execute(stmt)
                job = result.scalar_one_or_none()
                if job is None:
                    continue

                results.append({
                    "id": str(job.id),
                    "platform": job.platform,
                    "external_id": job.external_id,
                    "title": job.title,
                    "description": job.description or "",
                    "budget_min": float(job.budget_min) if job.budget_min else None,
                    "budget_max": float(job.budget_max) if job.budget_max else None,
                    "currency": job.currency,
                    "skills_required": job.skills_required or [],
                    "client_info": job.client_info or {},
                    "url": job.url,
                    "score": float(job.score) if job.score else 0.0,
                })

        return results

    # ------------------------------------------------------------------
    # RAG: similar won bids
    # ------------------------------------------------------------------

    async def _fetch_similar_bids(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        """Query the knowledge_base for similar previously-won bids.

        When a :class:`KnowledgeRetriever` is configured (via
        :meth:`configure_retriever`), performs vector-similarity search using
        the job title, description, and skills as the query.  Falls back to a
        category-only SQL query when the retriever is unavailable or the vector
        search fails.
        """
        category = self._infer_category(job)

        # --- Vector search path (preferred) ---
        if self._retriever is not None:
            try:
                query_parts: list[str] = []
                if job.get("title"):
                    query_parts.append(job["title"])
                if job.get("description"):
                    query_parts.append(job["description"][:500])
                if job.get("skills_required"):
                    query_parts.append(", ".join(job["skills_required"]))

                query_text = " ".join(query_parts)
                results = await self._retriever.search(
                    query_text,
                    kb_type="proposal_template",
                    category=category,
                    top_k=3,
                )

                if results:
                    similar = [
                        {
                            "title": r.title,
                            "content": r.content,
                            "success_rate": r.success_rate,
                        }
                        for r in results
                    ]
                    self._log.debug(
                        "similar_bids_found",
                        count=len(similar),
                        category=category,
                        source="vector_search",
                    )
                    return similar
            except Exception:
                self._log.warning(
                    "vector_search_fallback",
                    category=category,
                    exc_info=True,
                )

        # --- Fallback: category-only SQL query ---
        similar: list[dict[str, Any]] = []

        async with get_db_session() as session:
            stmt = (
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.type == "proposal_template",
                    KnowledgeBase.category == category,
                )
                .order_by(KnowledgeBase.success_rate.desc().nulls_last())
                .limit(3)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            for row in rows:
                similar.append({
                    "title": row.title,
                    "content": row.content,
                    "success_rate": float(row.success_rate) if row.success_rate else None,
                })

        self._log.debug(
            "similar_bids_found",
            count=len(similar),
            category=category,
            source="category_fallback",
        )
        return similar

    @staticmethod
    def _infer_category(job: dict[str, Any]) -> str:
        """Map job skills/title to a knowledge_base category."""
        skills_lower = [s.lower() for s in (job.get("skills_required") or [])]
        title_lower = (job.get("title") or "").lower()
        combined = " ".join(skills_lower) + " " + title_lower

        if any(kw in combined for kw in ("react", "next", "vue", "angular", "frontend")):
            return "web_development"
        if any(kw in combined for kw in ("wordpress", "wp", "woocommerce")):
            return "wordpress"
        if any(kw in combined for kw in ("design", "ui", "ux", "figma")):
            return "design"
        if any(kw in combined for kw in ("copy", "writing", "content", "blog", "seo")):
            return "copywriting"
        if any(kw in combined for kw in ("landing", "page", "html", "css", "tailwind")):
            return "landing_pages"
        return "web_development"  # default

    # ------------------------------------------------------------------
    # LLM proposal generation
    # ------------------------------------------------------------------

    async def _generate_proposal(
        self,
        job: dict[str, Any],
        similar_bids: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Call the LLM to generate a complete proposal for a job.

        Returns a parsed proposal dict or ``None`` on failure.
        """
        # Build context for the LLM.
        job_text = json.dumps(job, indent=2, default=str, ensure_ascii=False)
        context_parts: list[str] = [f"Job to bid on:\n{job_text}"]

        if similar_bids:
            context_parts.append("\nSimilar successful proposals for reference:")
            for idx, sb in enumerate(similar_bids, 1):
                context_parts.append(
                    f"\n--- Reference {idx} (win rate: {sb.get('success_rate', 'N/A')}) ---\n"
                    f"{sb.get('content', '')[:500]}"
                )

        user_content = "\n".join(context_parts)
        user_content += (
            "\n\nGenerate a winning proposal for this job. "
            "Return a single JSON object matching the output format specified in your instructions."
        )

        messages = [
            SystemMessage(content=BID_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.7)
        raw_text = str(response_msg.content)
        return self._parse_proposal_response(raw_text)

    def _parse_proposal_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's proposal JSON response."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log.error("bid_llm_json_parse_error", raw_preview=text[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("bid_llm_unexpected_type", type=type(parsed).__name__)
            return None

        # Validate required fields.
        required_fields = ("proposal_text", "bid_amount", "delivery_days")
        for field in required_fields:
            if field not in parsed:
                self._log.warning("bid_missing_field", field=field)
                return None

        # Ensure numeric types.
        try:
            parsed["bid_amount"] = float(parsed["bid_amount"])
        except (TypeError, ValueError):
            self._log.warning("bid_invalid_amount", raw=parsed.get("bid_amount"))
            return None

        try:
            parsed["delivery_days"] = int(parsed["delivery_days"])
        except (TypeError, ValueError):
            parsed["delivery_days"] = 7

        # Ensure confidence_score is numeric.
        try:
            parsed["confidence_score"] = float(parsed.get("confidence_score", 0.5))
        except (TypeError, ValueError):
            parsed["confidence_score"] = 0.5

        # CRITICAL INVARIANT: requires_hitl must always be true.
        parsed["requires_hitl"] = True

        # Ensure milestones is a list.
        if not isinstance(parsed.get("milestones"), list):
            parsed["milestones"] = []

        return parsed

    # ------------------------------------------------------------------
    # Database persistence
    # ------------------------------------------------------------------

    async def _store_bid(self, job: dict[str, Any], proposal: dict[str, Any]) -> str:
        """Create a Bid record in the database with ``status='hitl_pending'``.

        Returns the stringified UUID of the new bid.
        """
        bid_id = uuid.uuid4()

        async with get_db_session() as session:
            bid = Bid(
                id=bid_id,
                job_id=uuid.UUID(job["id"]),
                proposal_text=proposal["proposal_text"],
                bid_amount=Decimal(str(proposal["bid_amount"])),
                estimated_days=proposal.get("delivery_days"),
                status="hitl_pending",
                generation_model="gemini-3-flash",
            )
            session.add(bid)

        self._log.info(
            "bid_stored",
            bid_id=str(bid_id),
            job_id=job["id"],
            amount=proposal["bid_amount"],
            status="hitl_pending",
        )
        return str(bid_id)

    # ------------------------------------------------------------------
    # HITL queue entry
    # ------------------------------------------------------------------

    async def _create_hitl_entry(
        self,
        job: dict[str, Any],
        proposal: dict[str, Any],
        bid_id: str,
        thread_id: str = "",
    ) -> str:
        """Create an HITL queue entry for bid approval.

        Returns the stringified UUID of the HITL request.
        """
        hitl_id = uuid.uuid4()

        async with get_db_session() as session:
            hitl = HITLQueue(
                id=hitl_id,
                type="bid_approval",
                priority="normal",
                bid_id=uuid.UUID(bid_id),
                title=f"Approve bid: {job.get('title', 'Unknown')[:200]}",
                description=(
                    f"Platform: {job.get('platform', '?')} | "
                    f"Amount: ${proposal.get('bid_amount', 0):.2f} | "
                    f"Delivery: {proposal.get('delivery_days', '?')} days | "
                    f"Confidence: {proposal.get('confidence_score', 0):.0%}"
                ),
                payload={
                    "job": job,
                    "proposal": proposal,
                    "bid_id": bid_id,
                    "source_agent": "bid",
                    "thread_id": thread_id,
                },
                available_actions=["approve", "edit", "skip", "later"],
                status="pending",
            )
            session.add(hitl)

        self._log.info(
            "hitl_bid_approval_created",
            hitl_id=str(hitl_id),
            bid_id=bid_id,
            job_title=job.get("title", "")[:80],
        )
        return str(hitl_id)

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_bid_generated(
        self,
        job: dict[str, Any],
        bid_id: str,
        proposal: dict[str, Any],
    ) -> None:
        """Write an audit log entry for the generated bid."""
        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="bid",
                job_id=uuid.UUID(job["id"]),
                event_type="bid_generated",
                message=(
                    f"Bid generated for '{job.get('title', '')[:100]}' "
                    f"at ${proposal.get('bid_amount', 0):.2f}"
                ),
                details={
                    "bid_id": bid_id,
                    "platform": job.get("platform"),
                    "bid_amount": proposal.get("bid_amount"),
                    "delivery_days": proposal.get("delivery_days"),
                    "confidence_score": proposal.get("confidence_score"),
                    "milestones_count": len(proposal.get("milestones", [])),
                },
                llm_model="gemini-3-flash",
            )
            session.add(log_entry)


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================

async def bid_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the Bid Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    llm_client = LLMClient()
    heartbeat = HeartbeatMonitor(
        valkey=_get_valkey_client(),
        db_pool=None,  # DB pool injected at app startup in production
    )
    loop_detector = LoopDetector(max_iterations=50, max_identical_steps=3)

    agent = BidAgent(
        llm_client=llm_client,
        heartbeat=heartbeat,
        loop_detector=loop_detector,
    )

    return await agent.invoke(state)


def _get_valkey_client() -> Any:
    """Return the shared Valkey (redis-py) async client."""
    from src.core.database import get_valkey  # noqa: PLC0415
    return get_valkey()
