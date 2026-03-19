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
LLM: Gemini 2.5 Flash (Tier 5: Extraction).
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
from src.core.exceptions import LLMException, LLMInvalidResponseError, PlatformException
from src.core.heartbeat import HeartbeatMonitor
from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import AgentLog, HITLQueue, Job, PlatformAccount
from src.core.state import AgentState, update_state
from src.prompts.scout import SCOUT_SYSTEM_PROMPT
from src.security.encryption import decrypt_credentials

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
_MAX_JOBS_PER_BATCH = 10


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

        # 0. Load custom rules from Scout config (Dashboard Settings).
        custom_rules = await self._load_custom_rules()
        if custom_rules:
            self._log.info("scout_custom_rules_loaded", count=len(custom_rules))

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

        # 2b. Category pre-filter: reduce LLM token cost by filtering
        # jobs that don't match selected categories from Settings.
        scout_config = await self._load_scout_config()
        if scout_config:
            pre_filter_count = len(new_jobs)
            new_jobs = self._filter_by_categories(new_jobs, scout_config)
            filtered_out = pre_filter_count - len(new_jobs)
            if filtered_out > 0:
                self._log.info(
                    "category_pre_filter",
                    before=pre_filter_count,
                    after=len(new_jobs),
                    filtered_out=filtered_out,
                )
            if not new_jobs:
                self._log.info("all_jobs_filtered_by_category")
                return update_state(state, current_agent="scout", next_agent=None, status="active")

        # 3. Score and classify each new job via LLM.
        qualified_jobs: list[dict[str, Any]] = []
        review_jobs: list[dict[str, Any]] = []
        rejected_jobs: list[dict[str, Any]] = []

        for batch_start in range(0, len(new_jobs), _MAX_JOBS_PER_BATCH):
            batch = new_jobs[batch_start : batch_start + _MAX_JOBS_PER_BATCH]
            try:
                scored = await self._score_jobs(batch, custom_rules=custom_rules)
            except (LLMException, KeyError, ValueError):
                logger.exception("Failed to score batch of %d jobs, marking as review", len(batch))
                for job in batch:
                    job["match_score"] = 0.5
                    job["score_reason"] = "scoring_failed"
                    job["recommendation"] = "review"
                scored = batch

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
    # Custom rules loading
    # ------------------------------------------------------------------

    async def _load_custom_rules(self) -> list[str] | None:
        """Load custom rules from Scout config (Dashboard Settings).

        Returns a list of rule strings, or ``None`` if no rules are configured.
        Errors are swallowed -- missing rules must never block scanning.
        """
        try:
            async with get_db_session() as session:
                config = await load_scout_config(session)
            rules = config.get("custom_rules", [])
            if rules and isinstance(rules, list):
                # Filter out empty strings and limit to 50 rules.
                filtered = [r.strip() for r in rules if isinstance(r, str) and r.strip()]
                return filtered[:50] if filtered else None
            return None
        except Exception:  # noqa: BLE001
            self._log.debug("scout_custom_rules_load_failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Scout config loading (for category pre-filter)
    # ------------------------------------------------------------------

    async def _load_scout_config(self) -> dict[str, Any] | None:
        """Load full scout configuration from DB for category filtering.

        Returns the config dict, or ``None`` if unavailable.
        Errors are swallowed -- missing config must never block scanning.
        """
        try:
            async with get_db_session() as session:
                config = await load_scout_config(session)
            return config
        except Exception:  # noqa: BLE001
            self._log.debug("scout_config_load_failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Category pre-filter (reduces LLM token cost)
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_by_categories(
        jobs: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Filter jobs by selected categories from Scout config.

        Jobs whose ``category`` field matches either ``categories_auto``
        or ``categories_suggest`` in the config are kept.  Jobs without
        a ``category`` field always pass through (cannot be filtered).

        When no categories are configured (empty or missing lists),
        all jobs pass through unfiltered.

        Args:
            jobs: Raw job dicts from platform adapters.
            config: Scout configuration dict (from Dashboard Settings).

        Returns:
            Filtered list of jobs matching the configured categories.
        """
        if not jobs:
            return []

        auto = config.get("categories_auto", [])
        suggest = config.get("categories_suggest", [])

        # If no categories configured, pass all through
        if not auto and not suggest:
            return jobs

        # Build a lowercase set of allowed categories
        allowed: set[str] = set()
        for cat in auto:
            if isinstance(cat, str):
                allowed.add(cat.lower())
        for cat in suggest:
            if isinstance(cat, str):
                allowed.add(cat.lower())

        filtered: list[dict[str, Any]] = []
        for job in jobs:
            category = job.get("category")
            if category is None:
                # Jobs without category always pass through
                filtered.append(job)
                continue
            if isinstance(category, str) and category.lower() in allowed:
                filtered.append(job)

        return filtered

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
                tasks.append(asyncio.create_task(self._safe_fetch(platform_name, adapter.fetch_jobs, "websites", 20)))
            elif platform_name in ("flru", "kwork", "upwork", "telegram"):
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

    async def _score_jobs(
        self,
        jobs: list[dict[str, Any]],
        custom_rules: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Send a batch of jobs to the LLM for scoring and classification.

        Parameters
        ----------
        jobs:
            List of raw job dicts to evaluate.
        custom_rules:
            Optional free-text rules from Dashboard Settings that the
            operator defined.  Each rule is injected into the prompt so
            the LLM applies them during scoring.
        """
        jobs_text = json.dumps(jobs, indent=2, default=str, ensure_ascii=False)

        # Build the user prompt, optionally enriched with custom rules.
        prompt_parts: list[str] = [
            "Evaluate the following jobs and return a JSON array of scored objects.",
        ]

        if custom_rules:
            prompt_parts.append("\n# Custom Rules (from operator)")
            prompt_parts.append("Apply the following additional rules when scoring:")
            for idx, rule in enumerate(custom_rules, 1):
                prompt_parts.append(f"  {idx}. {rule}")

        prompt_parts.append(f"\nJobs:\n{jobs_text}")

        messages = [
            SystemMessage(content=SCOUT_SYSTEM_PROMPT),
            HumanMessage(content="\n".join(prompt_parts)),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.2)
        raw_text = str(response_msg.content)
        return self._parse_scored_response(raw_text, expected_count=len(jobs))

    def _parse_scored_response(
        self,
        raw: str,
        expected_count: int,
    ) -> list[dict[str, Any]]:
        """Best-effort parse of the LLM JSON output using extract_json."""
        try:
            parsed = extract_json(raw, expected_type=list)
        except ValueError:
            # Fallback: try as dict
            try:
                parsed = extract_json(raw, expected_type=dict)
            except ValueError as exc:
                self._log.error("llm_json_parse_error", raw_preview=raw[:300], error=str(exc))
                raise LLMInvalidResponseError(
                    message="Scout LLM returned non-JSON response",
                    agent_name="scout",
                    raw_response=raw[:500],
                ) from exc

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise LLMInvalidResponseError(
                message="Scout LLM returned unexpected type",
                agent_name="scout",
                raw_response=raw[:500],
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
        """Insert scored jobs into the ``jobs`` table using upsert (ON CONFLICT DO NOTHING).

        Returns a list of stringified UUIDs for the newly-created rows.
        Duplicate (platform, external_id) pairs are silently skipped.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

        if not scored_jobs:
            return []

        created_ids: list[str] = []

        async with get_db_session() as session:
            for sj in scored_jobs:
                job_id = uuid.uuid4()
                values = {
                    "id": job_id,
                    "platform": sj.get("platform", "unknown"),
                    "external_id": str(sj.get("external_id", sj.get("job_id", ""))),
                    "title": sj.get("title", "")[:500],
                    "description": sj.get("description", ""),
                    "budget_min": (Decimal(str(sj["budget_min"])) if sj.get("budget_min") is not None else None),
                    "budget_max": (Decimal(str(sj["budget_max"])) if sj.get("budget_max") is not None else None),
                    "currency": sj.get("currency", "USD"),
                    "skills_required": sj.get("skills_required"),
                    "score": Decimal(str(round(sj.get("match_score", 0.0), 2))),
                    "status": status,
                    "disqualify_reason": (sj.get("reasoning", "")[:255] if status == "disqualified" else None),
                    "url": sj.get("url"),
                    "raw_data": sj.get("raw_data"),
                    "client_info": sj.get("client_info"),
                }
                stmt = (
                    pg_insert(Job).values(**values).on_conflict_do_nothing(index_elements=["platform", "external_id"])
                )
                result = await session.execute(stmt)
                if result.rowcount > 0:
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
                message=(f"Scout scan complete: {qualified} qualified, {review} for review, {rejected} rejected"),
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
# Scout config loading + category modifiers
# ======================================================================


async def load_scout_config(session: Any) -> dict[str, Any]:
    """Load scout configuration from DB, returning defaults if not stored."""
    stmt = select(PlatformAccount).where(
        PlatformAccount.platform == "__scout_config__",
    )
    result = await session.execute(stmt)
    account = result.scalar_one_or_none()

    if account is None:
        from src.api.schemas import ScoutConfigSchema  # noqa: PLC0415

        return ScoutConfigSchema().model_dump()

    return decrypt_credentials(account.credentials)


def apply_category_modifier(category: str, config: dict[str, Any]) -> float:
    """Return score modifier based on category classification.

    - auto categories: 0.0 (no modifier)
    - suggest categories: -0.2 (routes to HITL)
    - unknown/missing: 0.0
    """
    if category in config.get("categories_auto", []):
        return 0.0
    if category in config.get("categories_suggest", []):
        return -0.2
    return 0.0


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def scout_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node function that creates and invokes the Scout Agent.

    This is the entry-point wired into the ``StateGraph``.  It pulls
    configuration from the environment/settings to construct adapter
    clients, then delegates to :class:`ScoutAgent`.

    Infrastructure (LLM client, heartbeat, loop detector, browser pool)
    is obtained from the shared DI container.
    """
    from src.adapters.fl_ru import FlRuClient  # noqa: PLC0415
    from src.core.config import get_settings  # noqa: PLC0415
    from src.core.container import get_container  # noqa: PLC0415

    settings = get_settings()
    container = get_container()

    # -- Build adapters (only those with configured credentials) ----------
    adapters: dict[str, Any] = {}

    # Try DB credentials for Freelancer, fall back to env vars
    freelancer_id = settings.FREELANCER_CLIENT_ID or ""
    freelancer_secret = settings.FREELANCER_CLIENT_SECRET or ""
    user_id = state.get("user_id")  # type: ignore[typeddict-item]
    if user_id:
        try:
            from src.core.credential_loader import load_platform_credentials  # noqa: PLC0415

            db_creds = await load_platform_credentials("freelancer", user_id=user_id)
            if db_creds:
                freelancer_id = db_creds.get("client_id", "") or freelancer_id
                freelancer_secret = db_creds.get("client_secret", "") or freelancer_secret
        except Exception:  # noqa: BLE001
            logger.debug("scout_credential_load_fallback", exc_info=True)

    if freelancer_id:
        from src.adapters.freelancer import FreelancerClient  # noqa: PLC0415

        adapters["freelancer"] = FreelancerClient(
            client_id=freelancer_id,
            client_secret=freelancer_secret,
        )

    # FL.ru is always available (public RSS, no auth required).
    adapters["flru"] = FlRuClient()

    # Telegram channel adapter (reads from Valkey queue, no auth needed here).
    telegram_adapter = container.telegram_adapter
    if telegram_adapter is not None:
        adapters["telegram"] = telegram_adapter

    # Browser-based adapters (use shared pool from container).
    pool = container.browser_pool
    if pool is not None:
        from src.adapters.kwork import KworkClient  # noqa: PLC0415
        from src.adapters.upwork import UpworkClient  # noqa: PLC0415

        adapters["upwork"] = UpworkClient(browser_pool=pool)
        adapters["kwork"] = KworkClient(browser_pool=pool)

    # -- Invoke agent with try/finally for adapter cleanup ----------------
    agent = ScoutAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
        adapters=adapters,
    )

    try:
        result = await agent.invoke(state)
    finally:
        # Clean up adapter HTTP clients (pool is owned by container).
        for adapter in adapters.values():
            if hasattr(adapter, "close"):
                try:
                    await adapter.close()
                except Exception:  # noqa: BLE001
                    logger.debug("scout_adapter_close_error", exc_info=True)

    return result
