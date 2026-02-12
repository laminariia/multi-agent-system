"""Job discovery and management routes.

Provides endpoints for listing, viewing, disqualifying jobs
discovered by the Scout agent, triggering manual scans, and
running the full Pipeline A for a specific job.

Mounted at ``/api/v1/jobs``.
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import structlog
from litestar import Controller, Request, get, post
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.guards import require_role
from src.api.schemas import (
    BidSummarySchema,
    JobDisqualifyResponseSchema,
    JobDisqualifySchema,
    JobListResponseSchema,
    JobResponseSchema,
)
from src.core.models import Job, User

logger = structlog.get_logger(__name__)

# Strong references to background tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task[Any]] = set()


class JobController(Controller):
    """Browse and manage discovered freelance jobs."""

    path = "/api/v1/jobs"
    tags = ["jobs"]

    # -----------------------------------------------------------------
    # GET /api/v1/jobs
    # -----------------------------------------------------------------

    @get(
        "/",
        summary="List discovered jobs",
        description="Paginated job list filterable by status, platform, and minimum match score.",
    )
    async def list_jobs(
        self,
        db_session: AsyncSession,
        status: str | None = Parameter(
            default=None,
            description="Filter: new | qualified | disqualified | bid_sent | won | lost",
        ),
        platform: str | None = Parameter(
            default=None,
            description="Filter: freelancer | upwork | fl_ru | kwork",
        ),
        min_score: float | None = Parameter(
            default=None,
            ge=0.0,
            le=1.0,
            description="Minimum match score (0.0 - 1.0)",
        ),
        search: str | None = Parameter(
            default=None,
            description="Search jobs by title (case-insensitive substring)",
        ),
        limit: int = Parameter(default=20, ge=1, le=100),
        offset: int = Parameter(default=0, ge=0),
    ) -> JobListResponseSchema:
        """Return a filtered, paginated list of jobs ordered by discovery date."""
        stmt = select(Job).options(selectinload(Job.bids))

        if status is not None:
            stmt = stmt.where(Job.status == status)
        if platform is not None:
            stmt = stmt.where(Job.platform == platform)
        if min_score is not None:
            stmt = stmt.where(Job.score >= Decimal(str(min_score)))
        if search is not None:
            stmt = stmt.where(Job.title.ilike(f"%{search}%"))

        # Total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        # Fetch page
        stmt = stmt.order_by(Job.discovered_at.desc()).limit(limit).offset(offset)
        result = await db_session.execute(stmt)
        rows = result.scalars().unique().all()

        jobs = [_job_to_schema(row) for row in rows]

        return JobListResponseSchema(jobs=jobs, total=total)

    # -----------------------------------------------------------------
    # GET /api/v1/jobs/stats
    # -----------------------------------------------------------------

    @get(
        "/stats",
        summary="Job statistics for dashboard charts",
    )
    async def stats(
        self,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Return aggregated job stats: counts by platform and by status."""
        # By platform
        platform_stmt = (
            select(Job.platform, func.count())
            .group_by(Job.platform)
        )
        platform_rows = (await db_session.execute(platform_stmt)).all()
        by_platform = [
            {"platform": row[0], "count": row[1]}
            for row in platform_rows
        ]

        # By status
        status_stmt = (
            select(Job.status, func.count())
            .group_by(Job.status)
        )
        status_rows = (await db_session.execute(status_stmt)).all()
        by_status = {row[0]: row[1] for row in status_rows}

        # Total
        total_stmt = select(func.count()).select_from(Job)
        total = (await db_session.execute(total_stmt)).scalar_one()

        return {
            "total": total,
            "by_platform": by_platform,
            "by_status": by_status,
        }

    # -----------------------------------------------------------------
    # GET /api/v1/jobs/{job_id}
    # -----------------------------------------------------------------

    @get(
        "/{job_id:uuid}",
        summary="Job detail",
        description="Full job detail including all associated bids.",
    )
    async def get_job(
        self,
        job_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> JobResponseSchema:
        """Retrieve a single job with its bids.

        Raises:
            NotFoundException: When no job matches the given UUID.
        """
        stmt = select(Job).where(Job.id == job_id).options(selectinload(Job.bids))
        result = await db_session.execute(stmt)
        job = result.scalar_one_or_none()

        if job is None:
            raise NotFoundException(detail=f"Job {job_id} not found")

        return _job_to_schema(job)

    # -----------------------------------------------------------------
    # POST /api/v1/jobs/{job_id}/disqualify
    # -----------------------------------------------------------------

    @post(
        "/{job_id:uuid}/disqualify",
        summary="Manually disqualify a job",
        guards=[require_role("owner")],
    )
    async def disqualify(
        self,
        job_id: uuid.UUID,
        data: JobDisqualifySchema,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> JobDisqualifyResponseSchema:
        """Mark a job as disqualified with a human-provided reason.

        Only jobs in ``new`` or ``qualified`` status may be disqualified.

        Raises:
            NotFoundException: When the job does not exist.
        """
        stmt = select(Job).where(Job.id == job_id)
        result = await db_session.execute(stmt)
        job = result.scalar_one_or_none()

        if job is None:
            raise NotFoundException(detail=f"Job {job_id} not found")

        if job.status not in ("new", "qualified"):
            from src.core.exceptions import MASException

            raise MASException(
                f"Cannot disqualify job in '{job.status}' status. Only 'new' or 'qualified' jobs are eligible.",
                details={"error_code": "CONFLICT", "status_code": 409},
            )

        job.status = "disqualified"
        job.disqualify_reason = data.reason
        await db_session.flush()

        logger.info(
            "job.disqualified",
            job_id=str(job_id),
            reason=data.reason,
            by=str(request.user.id),
        )

        return JobDisqualifyResponseSchema(
            id=job.id,
            status="disqualified",
            disqualify_reason=data.reason,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/jobs/scan
    # -----------------------------------------------------------------

    @post(
        "/scan",
        summary="Trigger manual Scout scan",
        description="Start a background Scout agent cycle to discover new jobs.",
        guards=[require_role("owner", "co_owner", "moderator")],
    )
    async def start_scan(
        self,
        request: Request[User, Token, Any],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Manually trigger a Scout agent scan.

        Accepts an optional body ``{"platform": "freelancer"}`` to restrict
        the scan to a single platform.  Defaults to ``"all"``.
        """
        platform = (data or {}).get("platform", "all")
        valid_platforms = {"freelancer", "upwork", "fl_ru", "kwork", "all"}
        if platform not in valid_platforms:
            platform = "all"

        from src.worker.tasks import run_scout_cycle  # noqa: PLC0415

        task = asyncio.create_task(run_scout_cycle({"platform": platform}))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info(
            "scout_scan_triggered",
            platform=platform,
            by=str(request.user.id),
        )

        return {
            "status": "started",
            "platform": platform,
            "message": (
                f"Scout scan started for platform '{platform}'. "
                "Check /api/v1/jobs for new results."
            ),
        }

    # -----------------------------------------------------------------
    # POST /api/v1/jobs/{job_id}/run-pipeline
    # -----------------------------------------------------------------

    @post(
        "/{job_id:uuid}/run-pipeline",
        summary="Run full Pipeline A for a job",
        description="Trigger the Planner pipeline for a specific qualified/won job.",
        guards=[require_role("owner", "co_owner")],
    )
    async def run_pipeline(
        self,
        job_id: uuid.UUID,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> dict[str, Any]:
        """Run the full Pipeline A (Planner -> Dev/Content/Design -> Critic -> Packager)
        for a specific job.

        Only jobs with status ``qualified``, ``bid_sent``, or ``won`` are eligible.

        Raises:
            NotFoundException: When the job does not exist.
            MASException: When the job status does not allow pipeline execution (409).
        """
        stmt = select(Job).where(Job.id == job_id)
        result = await db_session.execute(stmt)
        job = result.scalar_one_or_none()

        if job is None:
            raise NotFoundException(detail=f"Job {job_id} not found")

        allowed_statuses = ("qualified", "bid_sent", "won")
        if job.status not in allowed_statuses:
            from src.core.exceptions import MASException  # noqa: PLC0415

            raise MASException(
                f"Cannot run pipeline for job in '{job.status}' status. "
                f"Only jobs with status {allowed_statuses!r} are eligible.",
                details={"error_code": "CONFLICT", "status_code": 409},
            )

        thread_id = f"pipeline-{job.id}"

        from src.worker.tasks import run_project_pipeline  # noqa: PLC0415

        payload = {
            "project_id": str(job.id),
            "job_id": str(job.id),
            "platform": job.platform,
            "requirements": job.description or job.title,
            "budget": float(job.budget_max or job.budget_min or 0),
        }

        task = asyncio.create_task(run_project_pipeline(payload))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        job.status = "in_progress"
        await db_session.flush()

        logger.info(
            "pipeline_a_triggered",
            job_id=str(job.id),
            thread_id=thread_id,
            by=str(request.user.id),
        )

        return {
            "status": "started",
            "job_id": str(job.id),
            "thread_id": thread_id,
            "message": (
                f"Pipeline A started for job {job.id}. "
                f"Thread ID: {thread_id}."
            ),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_to_schema(job: Job) -> JobResponseSchema:
    """Convert a ``Job`` ORM instance (with eagerly-loaded bids) to a response schema."""
    bid_schemas = [
        BidSummarySchema(
            id=bid.id,
            bid_amount=bid.bid_amount,
            status=bid.status,
            created_at=bid.created_at,
        )
        for bid in (job.bids or [])
    ]

    return JobResponseSchema(
        id=job.id,
        platform=job.platform,
        external_id=job.external_id,
        title=job.title,
        description=job.description,
        budget_min=job.budget_min,
        budget_max=job.budget_max,
        budget_type=job.budget_type,
        currency=job.currency,
        client_info=job.client_info,
        skills_required=job.skills_required,
        deadline=job.deadline,
        status=job.status,
        score=job.score,
        disqualify_reason=job.disqualify_reason,
        discovered_at=job.discovered_at,
        url=job.url,
        bids=bid_schemas,
    )
