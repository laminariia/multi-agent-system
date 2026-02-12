"""Unit tests for the Jobs API scan and run-pipeline endpoints.

Tests the ``JobController`` endpoints at ``/api/v1/jobs``:
- POST /api/v1/jobs/scan --- trigger manual Scout scan
- POST /api/v1/jobs/{job_id}/run-pipeline --- run full Pipeline A for a job

We test route handlers directly via ``.fn()`` to avoid needing a full app instance.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import NotFoundException

from src.api.routes.jobs import JobController
from src.api.schemas import JobScanRequestSchema
from src.core.exceptions import MASException

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Mock Factories
# ---------------------------------------------------------------------------


def _create_mock_request(user_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Request with authenticated user."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.id = user_id or uuid.uuid4()
    return request


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.flush = AsyncMock()
    return session


def _create_mock_job(
    job_id: uuid.UUID | None = None,
    status: str = "qualified",
    platform: str = "freelancer",
    title: str = "Build a website",
    description: str = "Need a landing page for my business",
    budget_min: Decimal = Decimal("100"),
    budget_max: Decimal = Decimal("500"),
) -> MagicMock:
    """Create a mock Job ORM instance."""
    mock_job = MagicMock()
    mock_job.id = job_id or uuid.uuid4()
    mock_job.platform = platform
    mock_job.title = title
    mock_job.description = description
    mock_job.status = status
    mock_job.budget_min = budget_min
    mock_job.budget_max = budget_max
    return mock_job


# ---------------------------------------------------------------------------
# Tests: start_scan (POST /api/v1/jobs/scan)
# ---------------------------------------------------------------------------


class TestStartScan:
    """Tests for the POST /api/v1/jobs/scan endpoint."""

    @patch("src.worker.tasks.run_scout_cycle", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_start_scan_default_platform(
        self,
        mock_asyncio: MagicMock,
        _mock_run_scout: AsyncMock,
    ) -> None:
        """Should default to platform='all' when no data is provided."""
        controller = JobController(owner=MagicMock())
        request = _create_mock_request()

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.start_scan.fn(
            controller,
            request=request,
            data=None,
        )

        assert result["status"] == "started"
        assert result["platform"] == "all"
        assert "all" in result["message"]

    @patch("src.worker.tasks.run_scout_cycle", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_start_scan_specific_platform(
        self,
        mock_asyncio: MagicMock,
        _mock_run_scout: AsyncMock,
    ) -> None:
        """Should use the provided platform when given in request data."""
        controller = JobController(owner=MagicMock())
        request = _create_mock_request()

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.start_scan.fn(
            controller,
            request=request,
            data=JobScanRequestSchema(platform="freelancer"),
        )

        assert result["status"] == "started"
        assert result["platform"] == "freelancer"
        assert "freelancer" in result["message"]

    async def test_start_scan_invalid_platform_rejected_by_schema(
        self,
    ) -> None:
        """Schema should reject an invalid platform value via regex pattern."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            JobScanRequestSchema(platform="invalid_platform")

    @patch("src.worker.tasks.run_scout_cycle", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_start_scan_creates_background_task(
        self,
        mock_asyncio: MagicMock,
        _mock_run_scout: AsyncMock,
    ) -> None:
        """Should create an asyncio background task for the scout cycle."""
        controller = JobController(owner=MagicMock())
        request = _create_mock_request()

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        await controller.start_scan.fn(
            controller,
            request=request,
            data=JobScanRequestSchema(platform="freelancer"),
        )

        mock_asyncio.create_task.assert_called_once()
        mock_task.add_done_callback.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: run_pipeline (POST /api/v1/jobs/{job_id}/run-pipeline)
# ---------------------------------------------------------------------------


class TestRunPipeline:
    """Tests for the POST /api/v1/jobs/{job_id}/run-pipeline endpoint."""

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_run_pipeline_qualified_job(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should accept a job with status 'qualified' and set it to 'in_progress'."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="qualified")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.run_pipeline.fn(
            controller,
            job_id=job_id,
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "started"
        assert result["job_id"] == str(job_id)
        assert result["thread_id"] == f"pipeline-{job_id}"
        assert job.status == "in_progress"
        db_session.flush.assert_awaited_once()

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_run_pipeline_won_job(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should accept a job with status 'won' and set it to 'in_progress'."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="won")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.run_pipeline.fn(
            controller,
            job_id=job_id,
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "started"
        assert job.status == "in_progress"

    async def test_run_pipeline_job_not_found(self) -> None:
        """Should raise NotFoundException when the job does not exist."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match=f"Job {job_id} not found"):
            await controller.run_pipeline.fn(
                controller,
                job_id=job_id,
                db_session=db_session,
                request=request,
            )

    async def test_run_pipeline_invalid_status(self) -> None:
        """Should raise MASException when job status is not in allowed set."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="disqualified")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        with pytest.raises(MASException, match="Cannot run pipeline for job in 'disqualified' status"):
            await controller.run_pipeline.fn(
                controller,
                job_id=job_id,
                db_session=db_session,
                request=request,
            )

        # Status should NOT have changed
        assert job.status == "disqualified"
        db_session.flush.assert_not_awaited()

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_run_pipeline_creates_background_task(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should create an asyncio background task for the project pipeline."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="qualified")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        await controller.run_pipeline.fn(
            controller,
            job_id=job_id,
            db_session=db_session,
            request=request,
        )

        mock_asyncio.create_task.assert_called_once()
        mock_task.add_done_callback.assert_called_once()

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_run_pipeline_bid_sent_status(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should accept a job with status 'bid_sent'."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="bid_sent")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.run_pipeline.fn(
            controller,
            job_id=job_id,
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "started"
        assert job.status == "in_progress"

    async def test_run_pipeline_new_status_rejected(self) -> None:
        """Should reject a job with status 'new' (not in the allowed set)."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="new")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        with pytest.raises(MASException, match="Cannot run pipeline for job in 'new' status"):
            await controller.run_pipeline.fn(
                controller,
                job_id=job_id,
                db_session=db_session,
                request=request,
            )

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.jobs.asyncio")
    async def test_run_pipeline_payload_contains_correct_fields(
        self,
        mock_asyncio: MagicMock,
        mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should pass the correct payload to run_project_pipeline."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(
            job_id=job_id,
            status="qualified",
            platform="upwork",
            description="Build an API",
            budget_max=Decimal("1000"),
            budget_min=Decimal("500"),
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        await controller.run_pipeline.fn(
            controller,
            job_id=job_id,
            db_session=db_session,
            request=request,
        )

        # Verify the coroutine was created with the right payload
        mock_run_pipeline.assert_called_once_with({
            "project_id": str(job_id),
            "job_id": str(job_id),
            "platform": "upwork",
            "requirements": "Build an API",
            "budget": 1000.0,
            "user_id": str(request.user.id),
        })
