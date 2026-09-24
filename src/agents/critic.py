"""Critic Agent -- reviews all deliverables before client delivery.

The Critic Agent:
1. Collects all artifacts from state (dev, content, design).
2. Builds a review context with all artifacts.
3. Calls the LLM with CRITIC_SYSTEM_PROMPT for quality assessment.
4. Parses the JSON response with verdict, score, and issues.
5. Routes based on the verdict:
   - APPROVE (score >= 0.85): next_agent="packager"
   - REVISE (0.60--0.84, revision_count < 3): next_agent="dev"
   - REJECT (score < 0.60) or revision_count >= 3: requires_hitl=True
6. Logs the review decision to the ``agent_logs`` table.

Role constraints: can REVIEW and SCORE, CANNOT modify code directly.
LLM: Claude Sonnet 4.6 (Tier 3: Content+Review).
"""

from __future__ import annotations

import json
import uuid
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
from src.prompts.critic import CRITIC_SYSTEM_PROMPT
from src.security.semgrep_gate import SemgrepGate

logger = structlog.get_logger(__name__)

# Tools that the Critic Agent is allowed to invoke.
CRITIC_ALLOWED_TOOLS: list[str] = [
    "run_semgrep_scan",
    "check_text_quality",
    "compare_to_requirements",
    "request_revision",
]

# Decision thresholds.
_APPROVE_THRESHOLD = 0.85
_REVISE_THRESHOLD = 0.60

# Maximum revision cycles before HITL escalation.
_MAX_REVISION_CYCLES = 3

# Semgrep score penalties (per finding).
_SEMGREP_CRITICAL_PENALTY = 0.3
_SEMGREP_WARNING_PENALTY = 0.1


class CriticAgent(ConstrainedAgent):
    """Reviews and scores all deliverables before they reach the client.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Claude Sonnet 4.6 primary, Tier 3).
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
            agent_name="critic",
            allowed_tools=CRITIC_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Review all artifacts and route based on quality verdict.

        Flow:
        1. Collect all artifacts from state.
        2. Build review context.
        3. Call LLM with CRITIC_SYSTEM_PROMPT.
        4. Parse JSON response (verdict, score, issues).
        5. Route based on decision.
        6. Log the review decision.
        7. Return updated state.
        """
        self._log.info("critic_execute_start", thread_id=state["thread_id"])

        # 1. Collect all artifacts for review.
        review_context = self._collect_review_artifacts(state)
        if not review_context:
            self._log.warning("no_artifacts_to_review")
            return update_state(state, current_agent="critic", next_agent=None, status="active")

        # 1b. Run Semgrep security scan on dev artifacts (pre-LLM gate).
        semgrep_result = await self._run_semgrep_scan(review_context)
        semgrep_score_penalty = 0.0
        if semgrep_result is not None:
            semgrep_score_penalty = self._compute_semgrep_penalty(semgrep_result)

            if semgrep_result.blocked:
                self._log.warning(
                    "semgrep_blocked",
                    critical_count=semgrep_result.critical_count,
                    findings=[f.rule_id for f in semgrep_result.findings],
                )
                artifacts = dict(state.get("artifacts") or {})
                semgrep_detail = json.dumps(
                    {
                        "verdict": "reject",
                        "score": 0.0,
                        "source": "semgrep_gate",
                        "issues": [
                            {
                                "rule_id": f.rule_id,
                                "severity": f.severity,
                                "message": f.message,
                                "path": f.path,
                                "line": f.line,
                            }
                            for f in semgrep_result.findings
                        ],
                    },
                    default=str,
                    ensure_ascii=False,
                )
                artifacts["critic"] = [semgrep_detail]
                await self._log_review_decision(
                    thread_id=state["thread_id"],
                    verdict="reject",
                    score=0.0,
                    issues_count=len(semgrep_result.findings),
                    revision_count=self._get_revision_count(state),
                )
                return update_state(
                    state,
                    current_agent="critic",
                    next_agent="dev",
                    artifacts=artifacts,
                    status="active",
                )

        # 2. Build LLM prompt.
        user_content = self._build_review_prompt(state, review_context)

        messages = [
            SystemMessage(content=CRITIC_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        # 3. Call LLM for quality review.
        response_msg, _metrics = await self._call_llm(messages, temperature=0.2, max_tokens=8000)
        raw_text = str(response_msg.content)

        # 4. Parse the review response.
        review = self._parse_review_response(raw_text)
        if review is None:
            self._log.error("critic_review_parse_failed", raw_preview=raw_text[:300])
            return update_state(
                state,
                current_agent="critic",
                next_agent=None,
                status="active",
                errors=[*state["errors"], "Critic Agent: failed to parse LLM review response"],
            )

        verdict = review.get("verdict", "reject")
        score = review.get("score", 0.0)
        issues = review.get("issues", [])

        # Apply Semgrep score penalty (CRITICAL→-0.3, WARNING→-0.1 each).
        if semgrep_score_penalty > 0:
            original_score = score
            score = max(0.0, score - semgrep_score_penalty)
            review["score"] = score
            self._log.info(
                "semgrep_score_adjusted",
                original=original_score,
                penalty=semgrep_score_penalty,
                adjusted=score,
            )

        # 5. Determine revision count from artifacts.
        revision_count = self._get_revision_count(state)

        # 6. Store critic artifacts.
        artifacts = dict(state.get("artifacts") or {})
        review_data = json.dumps(review, default=str, ensure_ascii=False)
        artifacts["critic"] = [review_data]

        # Track revision count in a dedicated key.
        artifacts["_critic_revision_count"] = [str(revision_count)]

        # 7. Route based on verdict and revision count.
        if revision_count >= _MAX_REVISION_CYCLES:
            # Max revisions exceeded -- escalate to HITL regardless.
            hitl_id = str(uuid.uuid4())
            self._log.warning(
                "max_revisions_exceeded",
                revision_count=revision_count,
                verdict=verdict,
                score=score,
            )
            await self._log_review_decision(
                thread_id=state["thread_id"],
                verdict="escalate_hitl",
                score=score,
                issues_count=len(issues),
                revision_count=revision_count,
            )
            await self._create_hitl_escalation(
                thread_id=state["thread_id"],
                hitl_id=hitl_id,
                reason=f"revision_limit_exceeded ({revision_count} cycles)",
                score=score,
                revision_count=revision_count,
                project=state.get("project"),
                hitl_type="revision_escalation",
            )
            return update_state(
                state,
                current_agent="critic",
                next_agent=None,
                artifacts=artifacts,
                requires_hitl=True,
                hitl_request_id=hitl_id,
                status="paused",
            )

        if verdict == "approve" and score >= _APPROVE_THRESHOLD:
            # Quality is acceptable -- route to packager.
            self._log.info("critic_approved", score=score)
            await self._log_review_decision(
                thread_id=state["thread_id"],
                verdict="approve",
                score=score,
                issues_count=len(issues),
                revision_count=revision_count,
            )
            return update_state(
                state,
                current_agent="critic",
                next_agent="packager",
                artifacts=artifacts,
                status="active",
            )

        if verdict == "revise" and score >= _REVISE_THRESHOLD:
            # Classify the type of revision needed.
            revision_type = review.get("revision_type", "minor")
            new_revision_count = revision_count + 1
            artifacts["_critic_revision_count"] = [str(new_revision_count)]
            artifacts["_critic_revision_type"] = [revision_type]

            if revision_type == "scope_creep":
                # Scope creep -- escalate to HITL immediately.
                hitl_id = str(uuid.uuid4())
                self._log.warning(
                    "critic_scope_creep_detected",
                    score=score,
                    revision_count=new_revision_count,
                )
                await self._log_review_decision(
                    thread_id=state["thread_id"],
                    verdict="revise_scope_creep",
                    score=score,
                    issues_count=len(issues),
                    revision_count=new_revision_count,
                )
                await self._create_hitl_escalation(
                    thread_id=state["thread_id"],
                    hitl_id=hitl_id,
                    reason="scope_creep",
                    score=score,
                    revision_count=new_revision_count,
                    project=state.get("project"),
                )
                return update_state(
                    state,
                    current_agent="critic",
                    next_agent=None,
                    artifacts=artifacts,
                    requires_hitl=True,
                    hitl_request_id=hitl_id,
                    status="paused",
                )

            if revision_type == "major":
                # Major revision -- route back to planner for re-decomposition.
                self._log.info(
                    "critic_major_revision_requested",
                    score=score,
                    revision_count=new_revision_count,
                )
                await self._log_review_decision(
                    thread_id=state["thread_id"],
                    verdict="revise_major",
                    score=score,
                    issues_count=len(issues),
                    revision_count=new_revision_count,
                )
                return update_state(
                    state,
                    current_agent="critic",
                    next_agent="planner",
                    artifacts=artifacts,
                    status="active",
                )

            # Minor revision -- route to target agent via revision_target.
            revision_agent = self._determine_revision_target(issues)
            self._log.info(
                "critic_minor_revision_requested",
                score=score,
                revision_count=new_revision_count,
                revision_target=revision_agent,
            )
            await self._log_review_decision(
                thread_id=state["thread_id"],
                verdict="revise",
                score=score,
                issues_count=len(issues),
                revision_count=new_revision_count,
            )
            return update_state(
                state,
                current_agent="critic",
                next_agent=revision_agent,
                revision_target=revision_agent,
                revision_severity="minor",
                artifacts=artifacts,
                status="active",
            )

        # REJECT or score below thresholds -- escalate to HITL.
        hitl_id = str(uuid.uuid4())
        self._log.warning("critic_rejected", verdict=verdict, score=score)
        await self._log_review_decision(
            thread_id=state["thread_id"],
            verdict="reject",
            score=score,
            issues_count=len(issues),
            revision_count=revision_count,
        )
        await self._create_hitl_escalation(
            thread_id=state["thread_id"],
            hitl_id=hitl_id,
            reason=f"rejected (score={score:.2f})",
            score=score,
            revision_count=revision_count,
            project=state.get("project"),
        )
        return update_state(
            state,
            current_agent="critic",
            next_agent=None,
            artifacts=artifacts,
            requires_hitl=True,
            hitl_request_id=hitl_id,
            status="paused",
        )

    # ------------------------------------------------------------------
    # Semgrep score penalties
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_semgrep_penalty(result: Any) -> float:
        """Compute score penalty from Semgrep findings.

        CRITICAL (severity=ERROR) findings subtract 0.3 each.
        WARNING findings subtract 0.1 each.
        """
        penalty = result.critical_count * _SEMGREP_CRITICAL_PENALTY
        penalty += result.warning_count * _SEMGREP_WARNING_PENALTY
        return penalty

    # ------------------------------------------------------------------
    # Semgrep security scan
    # ------------------------------------------------------------------

    async def _run_semgrep_scan(self, review_context: dict[str, Any]) -> Any:
        """Run Semgrep on dev artifacts if present.

        Returns a :class:`ScanResult` or ``None`` if no dev files to scan.
        """
        dev_artifacts = review_context.get("dev")
        if not dev_artifacts:
            return None

        files: list[dict[str, str]] = []
        for item in dev_artifacts:
            try:
                parsed = json.loads(item)
                if isinstance(parsed, dict) and "files" in parsed:
                    files.extend(parsed["files"])
            except (json.JSONDecodeError, TypeError):
                continue

        if not files:
            return None

        gate = SemgrepGate()
        return await gate.scan_files(files)

    # ------------------------------------------------------------------
    # Artifact collection
    # ------------------------------------------------------------------

    def _collect_review_artifacts(self, state: AgentState) -> dict[str, Any]:
        """Collect all reviewable artifacts from the state.

        Returns a dict keyed by agent name with their artifact data.
        """
        artifacts = state.get("artifacts") or {}
        review_context: dict[str, Any] = {}

        for agent_name in ("dev", "content", "design"):
            agent_artifacts = artifacts.get(agent_name)
            if agent_artifacts:
                review_context[agent_name] = agent_artifacts

        return review_context

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_review_prompt(self, state: AgentState, review_context: dict[str, Any]) -> str:
        """Build the user-facing prompt with all artifacts for review."""
        project = state.get("project") or {}
        parts: list[str] = []

        # Project context.
        parts.append("## Project Requirements")
        parts.append(f"Requirements: {project.get('requirements', 'N/A')}")
        parts.append(f"Budget: ${project.get('budget', 'N/A')}")

        # Artifacts to review.
        for agent_name, agent_artifacts in review_context.items():
            parts.append(f"\n## Artifacts from {agent_name.title()} Agent")
            if isinstance(agent_artifacts, list):
                for idx, artifact in enumerate(agent_artifacts):
                    # Try to parse JSON artifacts for better context.
                    try:
                        parsed = json.loads(artifact)
                        parts.append(f"### Artifact {idx + 1}")
                        parts.append(json.dumps(parsed, indent=2, default=str, ensure_ascii=False)[:3000])
                    except (json.JSONDecodeError, TypeError):
                        parts.append(f"### Artifact {idx + 1}")
                        parts.append(str(artifact)[:1000])
            else:
                parts.append(str(agent_artifacts)[:2000])

        # Revision history if available.
        revision_count = self._get_revision_count(state)
        if revision_count > 0:
            parts.append("\n## Revision History")
            parts.append(f"This is revision cycle #{revision_count + 1}.")
            parts.append("Previous review feedback may be in the critic artifacts.")

        parts.append(
            "\n\nReview all artifacts above against the project requirements. "
            "Return a single JSON object matching the output format specified in your instructions."
        )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_review_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's review JSON response using extract_json.

        Returns ``None`` on parse failure.
        """
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error("critic_json_parse_error", raw_preview=raw[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("critic_unexpected_type", type=type(parsed).__name__)
            return None

        # Validate and normalise verdict.
        verdict = parsed.get("verdict", "reject")
        if verdict not in ("approve", "revise", "reject"):
            parsed["verdict"] = "reject"

        # Validate and normalise score.
        try:
            parsed["score"] = max(0.0, min(1.0, float(parsed.get("score", 0.0))))
        except (TypeError, ValueError):
            parsed["score"] = 0.0

        # Ensure issues is a list.
        if not isinstance(parsed.get("issues"), list):
            parsed["issues"] = []

        # Ensure passed_checks and failed_checks are lists.
        if not isinstance(parsed.get("passed_checks"), list):
            parsed["passed_checks"] = []
        if not isinstance(parsed.get("failed_checks"), list):
            parsed["failed_checks"] = []

        # Ensure revision_instructions is a string.
        if not isinstance(parsed.get("revision_instructions"), str):
            parsed["revision_instructions"] = ""

        # Validate and normalise revision_type.
        revision_type = parsed.get("revision_type", "none")
        if revision_type not in ("minor", "major", "scope_creep", "none"):
            parsed["revision_type"] = "minor" if parsed["verdict"] == "revise" else "none"
        else:
            parsed["revision_type"] = revision_type

        return parsed

    # ------------------------------------------------------------------
    # Revision target determination
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_revision_target(issues: list[dict[str, Any]]) -> str:
        """Determine which agent should handle minor revisions based on issues.

        Inspects issue categories to route to the most appropriate agent.
        Defaults to ``"dev"`` when no clear signal exists.
        """
        design_keywords = {"design", "layout", "visual", "ui", "ux", "css", "style", "color", "font"}
        content_keywords = {"content", "copy", "text", "wording", "grammar", "tone", "spelling"}

        design_hits = 0
        content_hits = 0

        for issue in issues:
            category = (issue.get("category", "") or "").lower()
            description = (issue.get("description", "") or "").lower()
            combined = f"{category} {description}"

            if any(kw in combined for kw in design_keywords):
                design_hits += 1
            if any(kw in combined for kw in content_keywords):
                content_hits += 1

        if design_hits > content_hits and design_hits > 0:
            return "design"
        if content_hits > design_hits and content_hits > 0:
            return "content"
        return "dev"

    # ------------------------------------------------------------------
    # Revision count tracking
    # ------------------------------------------------------------------

    def _get_revision_count(self, state: AgentState) -> int:
        """Get the current revision count from artifacts metadata."""
        artifacts = state.get("artifacts") or {}
        count_data = artifacts.get("_critic_revision_count", [])
        if count_data and isinstance(count_data, list) and len(count_data) > 0:
            try:
                return int(count_data[0])
            except (ValueError, TypeError):
                pass
        return 0

    # ------------------------------------------------------------------
    # HITL queue entry creation
    # ------------------------------------------------------------------

    async def _create_hitl_escalation(
        self,
        *,
        thread_id: str,
        hitl_id: str,
        reason: str,
        score: float,
        revision_count: int,
        project: dict[str, Any] | None = None,
        hitl_type: str = "revision_escalation",
    ) -> None:
        """Persist a HITL queue entry so the dashboard shows the escalation.

        Args:
            hitl_type: The HITL queue entry type. Defaults to ``"revision_escalation"``.
        """
        project = project or {}
        title = f"Critic escalation: {reason}"
        if project.get("title"):
            title = f"Critic escalation: {project['title'][:150]} — {reason}"

        try:
            parsed_id = uuid.UUID(hitl_id)
        except (ValueError, AttributeError):
            parsed_id = uuid.uuid5(uuid.NAMESPACE_DNS, hitl_id)

        async with get_db_session() as session:
            hitl = HITLQueue(
                id=parsed_id,
                type=hitl_type,
                priority="urgent",
                title=title[:500],
                description=(f"Score: {score:.2f} | Revisions: {revision_count} | Reason: {reason}"),
                payload={
                    "thread_id": thread_id,
                    "score": score,
                    "revision_count": revision_count,
                    "reason": reason,
                    "source_agent": "critic",
                },
                available_actions=["approve", "reject", "edit", "skip"],
                status="pending",
            )
            session.add(hitl)

        self._log.info(
            "hitl_escalation_created",
            hitl_id=hitl_id,
            reason=reason,
            score=score,
        )

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_review_decision(
        self,
        *,
        thread_id: str,
        verdict: str,
        score: float,
        issues_count: int,
        revision_count: int,
    ) -> None:
        """Write an audit log entry for the review decision."""
        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="critic",
                event_type="review_complete",
                message=(
                    f"Critic review: verdict={verdict}, score={score:.2f}, "
                    f"issues={issues_count}, revision_cycle={revision_count}"
                ),
                details={
                    "thread_id": thread_id,
                    "verdict": verdict,
                    "score": score,
                    "issues_count": issues_count,
                    "revision_count": revision_count,
                },
                llm_model="claude-sonnet-4-6",
            )
            session.add(log_entry)

        self._log.info(
            "review_decision",
            verdict=verdict,
            score=score,
            issues_count=issues_count,
            revision_count=revision_count,
        )


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def critic_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node function that creates and invokes the Critic Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = CriticAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
