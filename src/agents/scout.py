"""Scout Agent — discovers and qualifies freelance jobs across platforms.

Platform priority: Freelancer=Primary, FL.ru=Primary, Kwork=Secondary, Upwork=Optional.

The Scout Agent:
1. Fetches new listings from all configured platform adapters in parallel.
2. Deduplicates against jobs already in the database.
3. Sends each new job to the LLM for scoring (using SCOUT_SYSTEM_PROMPT).
4. Stores qualified jobs (score > 0.7) and routes them to the Bid Agent.
5. Flags borderline jobs (0.5-0.7) for HITL review.
6. Logs every decision to the ``agent_logs`` table.

Role constraints: can READ jobs, CANNOT submit bids, CANNOT modify projects.
LLM: Gemini 3 Flash (fallback Claude Haiku).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.exceptions import LLMInvalidResponseError, PlatformException
from src.core.heartbeat import HeartbeatMonitor
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import AgentLog, HITLQueue, Job
from src.core.state import AgentState, update_state
from src.prompts.scout import SCOUT_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Scout Agent is allowed to invoke.
SCOUT_ALLOWED_TOOLS: list[str] = [
    "fetch_freelancer_jobs",
    "fetch_flru_rss",
    "fetch_kwork_jobs",
    "fetch_upwork_jobs",
    "analyze_client_profile",
]

# Score thresholds.
_SCORE_BID_THRESHOLD = 0.7
_SCORE_REVIEW_THRESHOLD = 0.5

# Maximum jobs to evaluate in one LLM call to avoid context-window overflow.
_MAX_JOBS_PER_BATCH = 25


class ScoutAgent(ConstrainedAgent):
    """Discovers and qualifies freelance jobs from multiple platforms.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Gemini 3 Flash primary, Claude Haiku fallback).
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    adapters:
        Mapping of platform name to adapter client instance, e.g.
        ``{"freelancer": FreelancerClient(...), "flru": FlRuClient(...)}``.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
        adapters: dict[str, Any],
    ) -> None:
        super().__init__(
            agent_name="scout",
            allowed_tools=SCOUT_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )
        self.adapters = adapters

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Run the full scout pipeline: fetch -> deduplicate -> score -> route."""
        self._log.info("scout_execute_start", thread_id=state["thread_id"])

        # 1. Fetch jobs from all platforms in parallel.
        raw_jobs = await self._fetch_all_platforms()
        if not raw_jobs:
            self._log.info("no_new_jobs_found")
            return update_state(state, current_agent="scout", next_agent=None, status="active")

        # 2. Deduplicate against DB.
        new_jobs = await self._deduplicate(raw_jobs)
        self._log.info("dedup_result", total_fetched=len(raw_jobs), new_count=len(new_jobs))
        if not new_jobs:
            return update_state(state, current_agent="scout", next_agent=None, status="active")

        # 3. Score and classify each new job via LLM.
        qualified_jobs: list[dict[str, Any]] = []
        review_jobs: list[dict[str, Any]] = []
        rejected_jobs: list[dict[str, Any]] = []

        for batch_start in range(0, len(new_jobs), _MAX_JOBS_PER_BATCH):
            batch = new_jobs[batch_start: batch_start + _MAX_JOBS_PER_BATCH]
            scored = await self._score_jobs(batch)

            for scored_job in scored:
                score = scored_job.get("match_score", 0.0)
                recommendation = scored_job.get("recommendation", "skip")

                if score >= _SCORE_BID_THRESHOLD and recommendation == "bid":
                    qualified_jobs.append(scored_job)
                elif score >= _SCORE_REVIEW_THRESHOLD or recommendation == "review":
                    review_jobs.append(scored_job)
                else:
                    rejected_jobs.append(scored_job)

        # 4. Persist all jobs to DB.
        stored_job_ids = await self._store_jobs(qualified_jobs, status="qualified")
        await self._store_jobs(review_jobs, status="new")
        await self._store_jobs(rejected_jobs, status="disqualified")

        # 5. Create HITL entries for review-band jobs.
        for rj in review_jobs:
            await self._create_hitl_review(rj)

        # 6. Log summary.
        await self._log_decision_summary(
            qualified=len(qualified_jobs),
            review=len(review_jobs),
            rejected=len(rejected_jobs),
            thread_id=state["thread_id"],
        )

        # 7. Build updated state.
        artifacts = dict(state.get("artifacts") or {})
        artifacts["scout"] = stored_job_ids

        next_agent = "bid" if qualified_jobs else None
        return update_state(
            state,
            current_agent="scout",
            next_agent=next_agent,
            artifacts=artifacts,
            status="active",
        )

    # ------------------------------------------------------------------
    # Platform fetching
    # ------------------------------------------------------------------

    async def _fetch_all_platforms(self) -> list[dict[str, Any]]:
        """Fetch jobs from every configured adapter in parallel."""
        tasks: list[asyncio.Task[list[dict[str, Any]]]] = []
        platform_names: list[str] = []

        for platform_name, adapter in self.adapters.items():
            platform_names.append(platform_name)
            if platform_name == "freelancer":
                tasks.append(
                    asyncio.create_task(self._safe_fetch(platform_name, adapter.fetch_jobs, "websites", 20))
                )
            elif platform_name in ("flru", "kwork", "upwork"):
                tasks.append(asyncio.create_task(self._safe_fetch(platform_name, adapter.fetch_jobs)))
            else:
                self._log.warning("unknown_platform_adapter", platform=platform_name)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs: list[dict[str, Any]] = []
        for idx, result in enumerate(results):
            pname = platform_names[idx] if idx < len(platform_names) else "unknown"
            if isinstance(result, Exception):
                self._log.error("platform_fetch_failed", platform=pname, error=str(result))
                continue
            if isinstance(result, list):
                all_jobs.extend(result)

        return all_jobs

    async def _safe_fetch(
        self,
        platform: str,
        fetch_fn: Any,
        *args: Any,
    ) -> list[dict[str, Any]]:
        """Call a platform fetch function, catching platform exceptions."""
        try:
            return await fetch_fn(*args)  # type: ignore[no-any-return]
        except PlatformException as exc:
            self._log.warning("platform_error", platform=platform, error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    async def _deduplicate(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out jobs already in the database by (platform, external_id)."""
        if not jobs:
            return []

        pairs = [(j["platform"], str(j["external_id"])) for j in jobs]

        async with get_db_session() as session:
            stmt = select(Job.platform, Job.external_id).where(
                Job.platform.in_([p for p, _ in pairs]),
                Job.external_id.in_([e for _, e in pairs]),
            )
            result = await session.execute(stmt)
            existing: set[tuple[str, str]] = {(row.platform, row.external_id) for row in result}

        return [j for j in jobs if (j["platform"], str(j["external_id"])) not in existing]

    # ------------------------------------------------------------------
    # LLM scoring
    # ------------------------------------------------------------------

    async def _score_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Send a batch of jobs to the LLM for scoring and classification."""
        jobs_text = json.dumps(jobs, indent=2, default=str, ensure_ascii=False)

        messages = [
            SystemMessage(content=SCOUT_SYSTEM_PROMPT),
            HumanMessage(content=(
                "Evaluate the following jobs and return a JSON array of scored objects.\n\n"
                f"Jobs:\n{jobs_text}"
            )),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.2)
        raw_text = str(response_msg.content)
        return self._parse_scored_response(raw_text, expected_count=len(jobs))

    def _parse_scored_response(
        self,
        raw: str,
        expected_count: int,
    ) -> list[dict[str, Any]]:
        """Best-effort parse of the LLM JSON output."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log.error("llm_json_parse_error", raw_preview=text[:300], error=str(exc))
            raise LLMInvalidResponseError(
                message="Scout LLM returned non-JSON response",
                agent_name="scout",
                raw_response=text[:500],
            ) from exc

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise LLMInvalidResponseError(
                message="Scout LLM returned unexpected type",
                agent_name="scout",
                raw_response=text[:500],
            )

        validated: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                item["match_score"] = float(item.get("match_score", 0.0))
            except (TypeError, ValueError):
                item["match_score"] = 0.0
            if item.get("recommendation") not in ("bid", "skip", "review"):
                item["recommendation"] = "skip"
            validated.append(item)

        return validated

    # ------------------------------------------------------------------
    # Database persistence
    # ------------------------------------------------------------------

    async def _store_jobs(
        self,
        scored_jobs: list[dict[str, Any]],
        *,
        status: str,
    ) -> list[str]:
        """Insert scored jobs into the ``jobs`` table.

        Returns a list of stringified UUIDs for the newly-created rows.
        """
        if not scored_jobs:
            return []

        created_ids: list[str] = []

        async with get_db_session() as session:
            for sj in scored_jobs:
                job_id = uuid.uuid4()
                job = Job(
                    id=job_id,
                    platform=sj.get("platform", "unknown"),
                    external_id=str(sj.get("external_id", sj.get("job_id", ""))),
                    title=sj.get("title", "")[:500],
                    description=sj.get("description", ""),
                    budget_min=(
                        Decimal(str(sj["budget_min"])) if sj.get("budget_min") is not None else None
                    ),
                    budget_max=(
                        Decimal(str(sj["budget_max"])) if sj.get("budget_max") is not None else None
                    ),
                    currency=sj.get("currency", "USD"),
                    skills_required=sj.get("skills_required"),
                    score=Decimal(str(round(sj.get("match_score", 0.0), 2))),
                    status=status,
                    disqualify_reason=(
                        sj.get("reasoning", "")[:255] if status == "disqualified" else None
                    ),
                    url=sj.get("url"),
                    raw_data=sj.get("raw_data"),
                    client_info=sj.get("client_info"),
                )
                session.add(job)
                created_ids.append(str(job_id))

        self._log.info("jobs_stored", count=len(created_ids), status=status)
        return created_ids

    # ------------------------------------------------------------------
    # HITL review entries
    # ------------------------------------------------------------------

    async def _create_hitl_review(self, scored_job: dict[str, Any]) -> None:
        """Create an HITL queue entry for a borderline job (score 0.5-0.7)."""
        async with get_db_session() as session:
            hitl = HITLQueue(
                id=uuid.uuid4(),
                type="job_review",
                priority="low",
                title=f"Review job: {scored_job.get('title', 'Unknown')[:200]}",
                description=(
                    f"Score: {scored_job.get('match_score', 0):.2f} | "
                    f"Platform: {scored_job.get('platform', '?')} | "
                    f"Reasoning: {scored_job.get('reasoning', 'N/A')}"
                ),
                payload={
                    "scored_job": scored_job,
                    "source_agent": "scout",
                },
                available_actions=["approve_for_bid", "reject", "later"],
                status="pending",
            )
            session.add(hitl)

        self._log.info("hitl_review_created", title=scored_job.get("title", "")[:80])

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_decision_summary(
        self,
        *,
        qualified: int,
        review: int,
        rejected: int,
        thread_id: str,
    ) -> None:
        """Write a summary log entry to ``agent_logs``."""
        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="scout",
                event_type="scan_complete",
                message=(
                    f"Scout scan complete: {qualified} qualified, "
                    f"{review} for review, {rejected} rejected"
                ),
                details={
                    "qualified": qualified,
                    "review": review,
                    "rejected": rejected,
                    "thread_id": thread_id,
                },
            )
            session.add(log_entry)

        self._log.info(
            "scan_summary",
            qualified=qualified,
            review=review,
            rejected=rejected,
        )


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================

async def scout_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the Scout Agent.

    This is the entry-point wired into the ``StateGraph``.  It pulls
    configuration from the environment/settings to construct adapter
    clients, an LLM client, heartbeat, and loop detector, then
    delegates to :class:`ScoutAgent`.

    In production the adapters and infrastructure objects are assembled
    from the DI container.  Here we construct lightweight defaults.
    """
    from src.adapters.fl_ru import FlRuClient  # noqa: PLC0415
    from src.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()

    # -- Build adapters (only those with configured credentials) ----------
    adapters: dict[str, Any] = {}

    if settings.FREELANCER_CLIENT_ID:
        from src.adapters.freelancer import FreelancerClient  # noqa: PLC0415
        adapters["freelancer"] = FreelancerClient(
            client_id=settings.FREELANCER_CLIENT_ID,
            client_secret=settings.FREELANCER_CLIENT_SECRET or "",
        )

    # FL.ru is always available (public RSS, no auth required).
    adapters["flru"] = FlRuClient()

    # -- Infrastructure: LLM client, heartbeat, loop detector -------------
    llm_client = LLMClient()
    heartbeat = HeartbeatMonitor(
        valkey=_get_valkey_client(),
        db_pool=None,  # DB pool injected at app startup in production
    )
    loop_detector = LoopDetector(max_iterations=50, max_identical_steps=3)

    agent = ScoutAgent(
        llm_client=llm_client,
        heartbeat=heartbeat,
        loop_detector=loop_detector,
        adapters=adapters,
    )

    result = await agent.invoke(state)

    # Clean up adapter HTTP clients.
    for adapter in adapters.values():
        if hasattr(adapter, "close"):
            await adapter.close()

    return result


def _get_valkey_client() -> Any:
    """Return the shared Valkey (redis-py) async client."""
    from src.core.database import get_valkey  # noqa: PLC0415
    return get_valkey()
