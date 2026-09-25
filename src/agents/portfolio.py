"""Portfolio Agent -- automatically populates portfolio with completed projects.

The 11th agent in the MAS system. Activated after Packager approval, it:
1. Classifies the project complexity (simple/complex).
2. Generates professional descriptions in RU and EN via LLM.
3. Adapts descriptions for 6 target platforms (Kwork, FL.ru, YouDo, Fiverr,
   Freelancer.com, Telegram).
4. Builds a structured portfolio artifact (meta.json + descriptions + adaptations).
5. Creates an HITL entry for operator review before publication.
6. Supports portfolio audit (freshness, diversity, stack balance).

Role constraints: can GENERATE descriptions, CANNOT publish without HITL,
CANNOT modify project code or content.
LLM: DeepSeek V3.2 (Tier 6: Simple).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.heartbeat import HeartbeatMonitor
from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import AgentLog, HITLQueue
from src.core.state import AgentState, update_state
from src.prompts.portfolio import (
    PORTFOLIO_ADAPTATION_PROMPT,
    PORTFOLIO_AUDIT_PROMPT,
    PORTFOLIO_SYSTEM_PROMPT,
)

logger = structlog.get_logger(__name__)

# Tools that the Portfolio Agent is allowed to invoke.
PORTFOLIO_ALLOWED_TOOLS: list[str] = [
    "generate_portfolio_entry",
    "take_screenshots",
    "adapt_for_platform",
    "run_portfolio_audit",
    "save_portfolio_entry",
]

# Target platforms for portfolio adaptation.
PORTFOLIO_PLATFORMS: frozenset[str] = frozenset(
    {
        "kwork",
        "fl_ru",
        "youdo",
        "fiverr",
        "freelancer",
        "telegram",
    }
)

# Audit triggers: every N completed projects.
_AUDIT_PROJECT_INTERVAL = 5

# Projects older than this many days are candidates for replacement.
_FRESHNESS_THRESHOLD_DAYS = 180

# Keywords that indicate a simple (visual, frontend) project.
_SIMPLE_KEYWORDS: frozenset[str] = frozenset(
    {
        "landing",
        "landing page",
        "website",
        "static site",
        "portfolio",
        "homepage",
        "ui",
        "frontend",
        "web page",
        "webpage",
        "site",
        "html",
        "web design",
        "brochure",
    }
)

# Keywords that indicate a complex (backend, non-visual) project.
_COMPLEX_KEYWORDS: frozenset[str] = frozenset(
    {
        "bot",
        "telegram bot",
        "crm",
        "dashboard",
        "admin panel",
        "api",
        "cli",
        "backend",
        "auth",
        "authentication",
        "scraper",
        "parser",
        "automation",
        "integration",
        "microservice",
    }
)


class PortfolioAgent(ConstrainedAgent):
    """Generates portfolio entries from completed project deliveries.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (DeepSeek V3.2 via OpenRouter).
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
    ) -> None:
        super().__init__(
            agent_name="portfolio",
            allowed_tools=PORTFOLIO_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Run the full portfolio pipeline: extract -> classify -> generate -> adapt -> save -> HITL.

        Flow:
        1. Extract delivery info from packager artifacts.
        2. Classify project complexity (simple/complex).
        3. Generate descriptions in RU and EN.
        4. Adapt descriptions for all target platforms.
        5. Build meta.json and portfolio artifact.
        6. Create HITL entry for operator review.
        7. Set requires_hitl=True, status='paused'.
        """
        self._log.info("portfolio_execute_start", thread_id=state["thread_id"])

        project = state.get("project") or {}
        project_id = project.get("project_id", "unknown")
        all_artifacts = state.get("artifacts") or {}

        # 1. Extract delivery info from packager artifacts.
        delivery_info = self._extract_delivery_info(all_artifacts)
        if delivery_info is None:
            self._log.warning("no_packager_artifacts", project_id=project_id)
            await self._log_portfolio_action(
                project_id=project_id,
                thread_id=state["thread_id"],
                note="No packager artifacts found -- skipping portfolio entry",
            )
            return update_state(
                state,
                current_agent="portfolio",
                next_agent=None,
                status="active",
            )

        # 2. Classify project complexity.
        complexity = self._classify_project(delivery_info, project)
        self._log.info(
            "project_classified",
            project_id=project_id,
            complexity=complexity,
        )

        # 3. Generate descriptions (RU + EN).
        descriptions = await self._generate_descriptions(state)
        if descriptions is None:
            self._log.warning(
                "description_generation_failed",
                project_id=project_id,
            )
            descriptions = self._build_fallback_descriptions(project, delivery_info)

        # Override complexity from classification (LLM may disagree).
        descriptions["complexity"] = complexity

        # 4. Adapt descriptions for platforms.
        adaptations = await self._adapt_for_platforms(descriptions)
        # adaptations may be None on LLM failure -- that is acceptable.

        # 5. Build meta.json.
        new_id = _next_project_id(
            self._get_existing_project_metas(all_artifacts),
        )
        meta = self._build_meta_json(
            project_id=new_id,
            descriptions=descriptions,
            delivery_info=delivery_info,
            project=project,
            complexity=complexity,
        )

        # 6. Build portfolio artifact.
        portfolio_artifact = self._build_portfolio_artifact(
            meta,
            descriptions,
            adaptations,
        )

        # 7. Store artifact in state.
        artifacts = dict(all_artifacts)
        existing = list(artifacts.get("portfolio", []))
        existing.append(portfolio_artifact)
        artifacts["portfolio"] = existing

        # 8. Create HITL entry for operator review.
        hitl_id = await self._create_hitl_entry(
            project,
            {
                "meta": meta,
                "descriptions": descriptions,
                "adaptations": adaptations,
                "complexity": complexity,
            },
            state["thread_id"],
        )

        # 9. Log the action.
        await self._log_portfolio_action(
            project_id=project_id,
            thread_id=state["thread_id"],
            note=f"Portfolio entry {new_id} created (complexity={complexity})",
        )

        self._log.info(
            "portfolio_entry_ready",
            project_id=project_id,
            portfolio_id=new_id,
            complexity=complexity,
            hitl_id=hitl_id,
            has_adaptations=adaptations is not None,
        )

        # 10. Trigger audit if project count is a multiple of the interval.
        existing_metas = self._get_existing_project_metas(artifacts)
        if self._should_trigger_audit(project_count=len(existing_metas)):
            audit_result = await self._run_audit(existing_metas)
            if audit_result:
                self._log.info(
                    "portfolio_audit_completed",
                    project_id=project_id,
                    recommendations=len(audit_result.get("recommendations", [])),
                )

        # 11. Save portfolio artifact to disk.
        self._save_to_portfolio(new_id, portfolio_artifact)

        # 12. CRITICAL: requires_hitl MUST always be True before publication.
        return update_state(
            state,
            current_agent="portfolio",
            next_agent=None,
            artifacts=artifacts,
            requires_hitl=True,
            hitl_request_id=hitl_id,
            status="paused",
        )

    # ------------------------------------------------------------------
    # Screenshots (stub -- future: Playwright integration)
    # ------------------------------------------------------------------

    async def _take_screenshots(self, deploy_url: str) -> list[str]:
        """Take screenshots of a deployed project.

        Currently a stub that logs and returns an empty list.
        Future: Playwright integration for automated screenshots of simple projects.

        Parameters
        ----------
        deploy_url:
            The URL to screenshot.

        Returns
        -------
        list[str]
            List of screenshot file paths (empty until Playwright integration).
        """
        self._log.info(
            "screenshots_not_available",
            deploy_url=deploy_url,
            reason="Playwright integration not yet implemented",
        )
        return []

    # ------------------------------------------------------------------
    # Portfolio saving (stub)
    # ------------------------------------------------------------------

    def _save_to_portfolio(self, project_id: str, artifact: str) -> None:
        """Save a portfolio artifact to the portfolio/ directory structure.

        Creates ``portfolio/{project_id}/artifact.json`` with the serialised
        portfolio entry.  The directory is created if it does not exist.

        Parameters
        ----------
        project_id:
            The portfolio project ID (e.g. ``"001"``).
        artifact:
            JSON-serialised portfolio artifact string.
        """
        import pathlib  # noqa: PLC0415

        portfolio_dir = pathlib.Path("portfolio") / project_id
        try:
            portfolio_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = portfolio_dir / "artifact.json"
            artifact_path.write_text(artifact, encoding="utf-8")
            self._log.info(
                "portfolio_artifact_saved",
                project_id=project_id,
                path=str(artifact_path),
            )
        except OSError as exc:
            self._log.warning(
                "portfolio_save_failed",
                project_id=project_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Delivery info extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_delivery_info(
        all_artifacts: dict[str, list[str]],
    ) -> dict[str, Any] | None:
        """Extract the most recent delivery info from packager artifacts.

        Returns the parsed delivery dict or ``None`` if no packager artifacts.
        """
        packager_artifacts = all_artifacts.get("packager", [])
        if not packager_artifacts:
            return None

        # Take the last packager artifact (most recent delivery).
        raw = packager_artifacts[-1]
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Project classification
    # ------------------------------------------------------------------

    def _classify_project(
        self,
        delivery_info: dict[str, Any],
        project: dict[str, Any],
    ) -> str:
        """Determine project complexity: 'simple' or 'complex'.

        Simple projects (landing pages, static sites) can have automated
        Playwright screenshots. Complex projects (CRM, bots, CLI) require
        manual screenshots from the operator.

        Classification logic:
        1. Has deploy_url -> simple (something visual to screenshot).
        2. Includes 'frontend' or 'screenshots' -> simple.
        3. Requirements match simple keywords -> simple.
        4. Requirements match complex keywords -> complex.
        5. Default -> complex (conservative).
        """
        includes = delivery_info.get("includes") or []
        includes_lower = [i.lower() for i in includes]

        # Deploy URL present -> visual project.
        if delivery_info.get("deploy_url"):
            return "simple"

        # Frontend or screenshots in includes -> simple.
        if "frontend" in includes_lower or "screenshots" in includes_lower:
            return "simple"

        # Check requirements text.
        requirements = (project.get("requirements") or "").lower()

        # Check for complex keywords first (higher priority).
        for keyword in _COMPLEX_KEYWORDS:
            if keyword in requirements:
                return "complex"

        # Check for simple keywords.
        for keyword in _SIMPLE_KEYWORDS:
            if keyword in requirements:
                return "simple"

        # Default: complex (conservative -- operator provides screenshots).
        return "complex"

    # ------------------------------------------------------------------
    # Description generation (LLM)
    # ------------------------------------------------------------------

    async def _generate_descriptions(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call the LLM to generate base descriptions in RU and EN.

        Returns a dict with title_ru, title_en, description_ru, description_en,
        stack, categories, market, complexity. Returns None on failure.
        """
        project = state.get("project") or {}
        all_artifacts = state.get("artifacts") or {}

        # Build context from available artifacts.
        delivery_info = self._extract_delivery_info(all_artifacts) or {}
        project_text = json.dumps(project, indent=2, default=str, ensure_ascii=False)
        delivery_text = json.dumps(delivery_info, indent=2, default=str, ensure_ascii=False)

        messages = [
            SystemMessage(content=PORTFOLIO_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Generate portfolio descriptions for the following completed project.\n\n"
                    f"Project context:\n{project_text}\n\n"
                    f"Delivery info:\n{delivery_text}\n\n"
                    "Return a single JSON object matching the output format specified "
                    "in your instructions."
                )
            ),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.5)
        raw_text = str(response_msg.content)
        return self._parse_description_response(raw_text)

    def _parse_description_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM description generation response."""
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error(
                "portfolio_description_parse_error",
                raw_preview=raw[:300],
                error=str(exc),
            )
            return None

        if not isinstance(parsed, dict):
            return None

        # Validate required fields.
        required = {"title_ru", "title_en", "description_ru", "description_en"}
        if not required.issubset(parsed.keys()):
            self._log.warning(
                "portfolio_description_missing_fields",
                present=list(parsed.keys()),
                required=list(required),
            )
            # Try to fill missing fields with empty strings rather than failing.
            for field in required:
                parsed.setdefault(field, "")

        # Ensure list fields.
        if not isinstance(parsed.get("stack"), list):
            parsed["stack"] = []
        if not isinstance(parsed.get("categories"), list):
            parsed["categories"] = []

        parsed.setdefault("market", "ru")
        parsed.setdefault("complexity", "simple")

        return parsed

    # ------------------------------------------------------------------
    # Platform adaptation (LLM)
    # ------------------------------------------------------------------

    async def _adapt_for_platforms(
        self,
        descriptions: dict[str, Any],
    ) -> dict[str, str] | None:
        """Call the LLM to adapt descriptions for each target platform.

        Returns a dict keyed by platform name with adapted text.
        Returns None on failure.
        """
        prompt = PORTFOLIO_ADAPTATION_PROMPT.format(
            description_ru=descriptions.get("description_ru", ""),
            description_en=descriptions.get("description_en", ""),
            stack=", ".join(descriptions.get("stack", [])),
            categories=", ".join(descriptions.get("categories", [])),
        )

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(
                content=(
                    "Adapt the portfolio descriptions for all 6 platforms. "
                    "Return a single JSON object with platform keys."
                )
            ),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.6)
        raw_text = str(response_msg.content)

        try:
            parsed = extract_json(raw_text, expected_type=dict)
        except ValueError as exc:
            self._log.error(
                "portfolio_adaptation_parse_error",
                raw_preview=raw_text[:300],
                error=str(exc),
            )
            return None

        if not isinstance(parsed, dict):
            return None

        # Filter to only known platforms.
        result: dict[str, str] = {}
        for platform in PORTFOLIO_PLATFORMS:
            if platform in parsed and isinstance(parsed[platform], str):
                result[platform] = parsed[platform]

        return result if result else None

    # ------------------------------------------------------------------
    # Meta.json building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_meta_json(
        *,
        project_id: str,
        descriptions: dict[str, Any],
        delivery_info: dict[str, Any],
        project: dict[str, Any],
        complexity: str,
    ) -> dict[str, Any]:
        """Build a meta.json structure for the portfolio entry.

        Follows the spec in docs/Full_work/portfolio/portfolio-agent.md section 12.
        """
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

        return {
            "id": project_id,
            "delivery_id": delivery_info.get("delivery_id", ""),
            "project_id": project.get("project_id", "unknown"),
            "title": descriptions.get("title_ru", "Untitled"),
            "title_en": descriptions.get("title_en", "Untitled"),
            "date": today,
            "client": "anonymous",
            "stack": descriptions.get("stack", []),
            "categories": descriptions.get("categories", []),
            "market": descriptions.get("market", "ru"),
            "platforms_published": {"ru": [], "en": []},
            "screenshots": [],
            "deployed_url": delivery_info.get("deploy_url"),
            "complexity": complexity,
            "auto_screenshot": complexity == "simple",
            "added_at": today,
            "last_audit": None,
            "conversion_count": 0,
            "status": "active",
            "audit_notes": None,
        }

    # ------------------------------------------------------------------
    # Portfolio artifact building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_portfolio_artifact(
        meta: dict[str, Any],
        descriptions: dict[str, Any],
        adaptations: dict[str, str] | None,
    ) -> str:
        """Build the serialised portfolio artifact for state storage.

        The artifact contains meta.json, base descriptions, and platform versions.
        Returns a JSON string.
        """
        artifact = {
            "meta": meta,
            "description_ru": descriptions.get("description_ru", ""),
            "description_en": descriptions.get("description_en", ""),
            "platform_versions": adaptations or {},
        }
        return json.dumps(artifact, default=str, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Fallback description builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fallback_descriptions(
        project: dict[str, Any],
        delivery_info: dict[str, Any],
    ) -> dict[str, Any]:
        """Build basic descriptions when LLM generation fails.

        Uses available project data to construct minimal but usable descriptions.
        """
        requirements = project.get("requirements", "Project")
        project_id = project.get("project_id", "unknown")
        delivery_msg = delivery_info.get("delivery_message", "")

        title = requirements[:100] if requirements else f"Project {project_id}"

        return {
            "title_ru": title,
            "title_en": title,
            "description_ru": delivery_msg or f"Выполнен проект: {title}",
            "description_en": delivery_msg or f"Completed project: {title}",
            "stack": [],
            "categories": [],
            "market": "ru",
            "complexity": "complex",
        }

    # ------------------------------------------------------------------
    # Existing project metadata extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _get_existing_project_metas(
        all_artifacts: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Extract meta objects from existing portfolio artifacts in state.

        Used for generating the next project ID.
        """
        portfolio_artifacts = all_artifacts.get("portfolio", [])
        metas: list[dict[str, Any]] = []
        for raw in portfolio_artifacts:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "meta" in parsed:
                    metas.append(parsed["meta"])
            except (json.JSONDecodeError, TypeError):
                continue
        return metas

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def _run_audit(
        self,
        existing_projects: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Run a portfolio audit using the LLM.

        Analyses freshness, diversity, stack balance, and platform coverage.
        Returns recommendations dict or None on failure.
        """
        portfolio_json = json.dumps(
            existing_projects,
            indent=2,
            default=str,
            ensure_ascii=False,
        )
        prompt = PORTFOLIO_AUDIT_PROMPT.format(portfolio_json=portfolio_json)

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(
                content=(
                    "Analyse the portfolio and provide recommendations. "
                    "Return a single JSON object with recommendations and metrics."
                )
            ),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.3)
        raw_text = str(response_msg.content)

        try:
            parsed = extract_json(raw_text, expected_type=dict)
        except ValueError as exc:
            self._log.error(
                "portfolio_audit_parse_error",
                raw_preview=raw_text[:300],
                error=str(exc),
            )
            return None

        if not isinstance(parsed, dict):
            return None

        # Validate structure.
        if "recommendations" not in parsed or "metrics" not in parsed:
            self._log.warning(
                "portfolio_audit_missing_fields",
                keys=list(parsed.keys()),
            )
            return None

        return parsed

    def _should_trigger_audit(self, *, project_count: int) -> bool:
        """Check whether an audit should be triggered based on project count.

        Audit triggers every _AUDIT_PROJECT_INTERVAL completed projects.
        """
        return project_count > 0 and project_count % _AUDIT_PROJECT_INTERVAL == 0

    # ------------------------------------------------------------------
    # HITL queue entry
    # ------------------------------------------------------------------

    async def _create_hitl_entry(
        self,
        project: dict[str, Any],
        portfolio_data: dict[str, Any],
        thread_id: str = "",
    ) -> str:
        """Create an HITL queue entry for portfolio review before publication.

        Returns the stringified UUID of the HITL request.
        """
        hitl_id = uuid.uuid4()
        project_id = project.get("project_id", "unknown")
        meta = portfolio_data.get("meta", {})

        async with get_db_session() as session:
            hitl = HITLQueue(
                id=hitl_id,
                type="portfolio_review",
                priority="medium",
                title=f"Portfolio review: {meta.get('title', project_id)[:200]}",
                description=(
                    f"New portfolio entry for project '{project_id}'. "
                    f"Complexity: {meta.get('complexity', 'unknown')}. "
                    f"Stack: {', '.join(meta.get('stack', []))}. "
                    f"Platforms: {len(portfolio_data.get('adaptations') or {})} adapted."
                ),
                payload={
                    "project": project,
                    "portfolio_data": portfolio_data,
                    "source_agent": "portfolio",
                    "thread_id": thread_id,
                },
                available_actions=[
                    "approve_all",
                    "approve_selected",
                    "edit",
                    "regenerate",
                    "reject",
                ],
                status="pending",
            )
            session.add(hitl)

        self._log.info(
            "hitl_portfolio_review_created",
            hitl_id=str(hitl_id),
            project_id=project_id,
        )
        return str(hitl_id)

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_portfolio_action(
        self,
        *,
        project_id: str,
        thread_id: str,
        note: str = "",
    ) -> None:
        """Write an audit log entry for the portfolio action."""
        message = f"Portfolio action for project '{project_id}'"
        if note:
            message += f": {note}"

        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="portfolio",
                event_type="portfolio_entry_created",
                message=message,
                details={
                    "project_id": project_id,
                    "thread_id": thread_id,
                },
                llm_model="deepseek-v3.2",
            )
            session.add(log_entry)


# ======================================================================
# Utility functions
# ======================================================================


def _next_project_id(existing_metas: list[dict[str, Any]]) -> str:
    """Generate the next sequential project ID (zero-padded to 3 digits).

    Scans existing meta objects for the highest numeric ID and increments.
    """
    max_id = 0
    for meta in existing_metas:
        raw_id = meta.get("id", "0")
        try:
            numeric = int(raw_id)
            max_id = max(max_id, numeric)
        except (ValueError, TypeError):
            continue
    return str(max_id + 1).zfill(3)


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def portfolio_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node function that creates and invokes the Portfolio Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = PortfolioAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
