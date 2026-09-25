"""Unit tests for the Jobs API routes.

Tests the JobController endpoints at ``/api/v1/jobs``:
- GET /api/v1/jobs — list jobs with filters and pagination
- GET /api/v1/jobs/{job_id} — single job detail
- POST /api/v1/jobs/{job_id}/disqualify — mark job as disqualified

We test route handlers directly via ``.fn()`` to avoid needing a full app instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.exceptions import NotFoundException

from src.api.routes.jobs import JobController, _job_to_schema
from src.api.schemas import JobDisqualifySchema
from src.core.exceptions import MASException

# ---------------------------------------------------------------------------
# Mock Factories
# ---------------------------------------------------------------------------


def _create_mock_job(
    job_id: uuid.UUID | None = None,
    platform: str = "freelancer",
    status: str = "new",
    score: Decimal = Decimal("0.85"),
    bids: list | None = None,
) -> MagicMock:
    """Create a mock Job ORM instance."""
    mock_job = MagicMock()
    mock_job.id = job_id or uuid.uuid4()
    mock_job.platform = platform
    mock_job.external_id = "ext-12345"
    mock_job.title = "Build a website"
    mock_job.description = "Need a landing page for my business"
    mock_job.budget_min = Decimal("100")
    mock_job.budget_max = Decimal("500")
    mock_job.budget_type = "fixed"
    mock_job.currency = "USD"
    mock_job.client_info = {"rating": 4.5, "reviews": 10}
    mock_job.skills_required = ["python", "react"]
    mock_job.deadline = None
    mock_job.status = status
    mock_job.score = score
    mock_job.disqualify_reason = None
    mock_job.discovered_at = datetime.now(UTC)
    mock_job.url = "https://example.com/job/12345"
    mock_job.bids = bids if bids is not None else []
    return mock_job


def _create_mock_bid(
    bid_id: uuid.UUID | None = None,
    bid_amount: Decimal = Decimal("250"),
    status: str = "pending",
) -> MagicMock:
    """Create a mock Bid ORM instance."""
    mock_bid = MagicMock()
    mock_bid.id = bid_id or uuid.uuid4()
    mock_bid.bid_amount = bid_amount
    mock_bid.status = status
    mock_bid.created_at = datetime.now(UTC)
    return mock_bid


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.flush = AsyncMock()
    return session


def _create_mock_request(user_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Request with authenticated user."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.id = user_id or uuid.uuid4()
    return request


# ---------------------------------------------------------------------------
# Tests: list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    """Tests for the GET /api/v1/jobs endpoint."""

    @pytest.mark.asyncio
    async def test_returns_paginated_results_with_total_count(self) -> None:
        """Should return paginated job list with total count."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        job1 = _create_mock_job(platform="freelancer", score=Decimal("0.9"))
        job2 = _create_mock_job(platform="upwork", score=Decimal("0.75"))

        # Mock two execute calls: count + fetch
        count_result = MagicMock()
        count_result.scalar_one.return_value = 2

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [job1, job2]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform=None,
            min_score=None,
            limit=20,
            offset=0,
        )

        assert result.total == 2
        assert len(result.jobs) == 2
        assert result.jobs[0].platform == "freelancer"
        assert result.jobs[1].platform == "upwork"

    @pytest.mark.asyncio
    async def test_filters_by_status(self) -> None:
        """Should filter jobs by status parameter."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        qualified_job = _create_mock_job(status="qualified")

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [qualified_job]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status="qualified",
            platform=None,
            min_score=None,
            limit=20,
            offset=0,
        )

        assert result.total == 1
        assert result.jobs[0].status == "qualified"

    @pytest.mark.asyncio
    async def test_filters_by_platform(self) -> None:
        """Should filter jobs by platform parameter."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        upwork_job = _create_mock_job(platform="upwork")

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [upwork_job]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform="upwork",
            min_score=None,
            limit=20,
            offset=0,
        )

        assert result.total == 1
        assert result.jobs[0].platform == "upwork"

    @pytest.mark.asyncio
    async def test_filters_by_min_score(self) -> None:
        """Should filter jobs by minimum score with Decimal conversion."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        high_score_job = _create_mock_job(score=Decimal("0.92"))

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [high_score_job]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform=None,
            min_score=0.90,
            limit=20,
            offset=0,
        )

        assert result.total == 1
        assert result.jobs[0].score == Decimal("0.92")

    @pytest.mark.asyncio
    async def test_default_pagination(self) -> None:
        """Should use default limit=20, offset=0 when not specified."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        job1 = _create_mock_job()

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [job1]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform=None,
            min_score=None,
            limit=20,
            offset=0,
        )

        assert result.total == 1
        assert len(result.jobs) == 1

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        """Should return empty list when no jobs match filters."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = []

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status="nonexistent",
            platform=None,
            min_score=None,
            limit=20,
            offset=0,
        )

        assert result.total == 0
        assert len(result.jobs) == 0

    @pytest.mark.asyncio
    async def test_returns_bid_summaries_for_each_job(self) -> None:
        """Should include bid summaries in job response."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        bid1 = _create_mock_bid(bid_amount=Decimal("200"), status="pending")
        bid2 = _create_mock_bid(bid_amount=Decimal("300"), status="approved")
        job = _create_mock_job(bids=[bid1, bid2])

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [job]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform=None,
            min_score=None,
            limit=20,
            offset=0,
        )

        assert len(result.jobs[0].bids) == 2
        assert result.jobs[0].bids[0].bid_amount == Decimal("200")
        assert result.jobs[0].bids[1].bid_amount == Decimal("300")


# ---------------------------------------------------------------------------
# Tests: stats
# ---------------------------------------------------------------------------


class TestJobStats:
    """Tests for the GET /api/v1/jobs/stats endpoint."""

    @pytest.mark.asyncio
    async def test_returns_by_platform_and_by_status(self) -> None:
        """Should return aggregated counts by platform and by status."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        # Mock three execute calls: by_platform, by_status, total
        platform_result = MagicMock()
        platform_result.all.return_value = [
            ("freelancer", 5),
            ("upwork", 3),
        ]

        status_result = MagicMock()
        status_result.all.return_value = [
            ("new", 4),
            ("qualified", 2),
            ("bid_sent", 2),
        ]

        total_result = MagicMock()
        total_result.scalar_one.return_value = 8

        db_session.execute.side_effect = [platform_result, status_result, total_result]

        result = await controller.stats.fn(controller, db_session=db_session)

        assert result["total"] == 8
        assert len(result["by_platform"]) == 2
        assert result["by_platform"][0] == {"platform": "freelancer", "count": 5}
        assert result["by_status"]["new"] == 4
        assert result["by_status"]["qualified"] == 2

    @pytest.mark.asyncio
    async def test_returns_empty_stats_when_no_jobs(self) -> None:
        """Should return zeros when there are no jobs."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        platform_result = MagicMock()
        platform_result.all.return_value = []

        status_result = MagicMock()
        status_result.all.return_value = []

        total_result = MagicMock()
        total_result.scalar_one.return_value = 0

        db_session.execute.side_effect = [platform_result, status_result, total_result]

        result = await controller.stats.fn(controller, db_session=db_session)

        assert result["total"] == 0
        assert result["by_platform"] == []
        assert result["by_status"] == {}


# ---------------------------------------------------------------------------
# Tests: list_jobs search
# ---------------------------------------------------------------------------


class TestListJobsSearch:
    """Tests for the search parameter on GET /api/v1/jobs."""

    @pytest.mark.asyncio
    async def test_search_filters_by_title(self) -> None:
        """Should pass search parameter to filter jobs by title."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        job = _create_mock_job()
        job.title = "React landing page"

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [job]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform=None,
            min_score=None,
            search="React",
            limit=20,
            offset=0,
        )

        assert result.total == 1
        assert len(result.jobs) == 1
        # Verify that execute was called (we trust the SQL filter works)
        assert db_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_search_none_returns_all(self) -> None:
        """Should return all jobs when search is None."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        job1 = _create_mock_job()
        job2 = _create_mock_job()

        count_result = MagicMock()
        count_result.scalar_one.return_value = 2

        fetch_result = MagicMock()
        fetch_result.scalars.return_value.unique.return_value.all.return_value = [job1, job2]

        db_session.execute.side_effect = [count_result, fetch_result]

        result = await controller.list_jobs.fn(
            controller,
            db_session=db_session,
            status=None,
            platform=None,
            min_score=None,
            search=None,
            limit=20,
            offset=0,
        )

        assert result.total == 2


# ---------------------------------------------------------------------------
# Tests: get_job
# ---------------------------------------------------------------------------


class TestGetJob:
    """Tests for the GET /api/v1/jobs/{job_id} endpoint."""

    @pytest.mark.asyncio
    async def test_returns_job_with_bids(self) -> None:
        """Should return full job detail including bids."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        job_id = uuid.uuid4()
        bid = _create_mock_bid()
        job = _create_mock_job(job_id=job_id, bids=[bid])

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        result = await controller.get_job.fn(
            controller,
            job_id=job_id,
            db_session=db_session,
        )

        assert result.id == job_id
        assert len(result.bids) == 1
        assert result.bids[0].id == bid.id

    @pytest.mark.asyncio
    async def test_raises_404_when_job_not_found(self) -> None:
        """Should raise NotFoundException when job does not exist."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()

        job_id = uuid.uuid4()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match=f"Job {job_id} not found"):
            await controller.get_job.fn(
                controller,
                job_id=job_id,
                db_session=db_session,
            )


# ---------------------------------------------------------------------------
# Tests: disqualify
# ---------------------------------------------------------------------------


class TestDisqualify:
    """Tests for the POST /api/v1/jobs/{job_id}/disqualify endpoint."""

    @pytest.mark.asyncio
    async def test_successful_disqualification_of_new_job(self) -> None:
        """Should successfully disqualify a job in 'new' status."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="new")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        data = JobDisqualifySchema(reason="Budget too low")

        result = await controller.disqualify.fn(
            controller,
            job_id=job_id,
            data=data,
            db_session=db_session,
            request=request,
        )

        assert result.id == job_id
        assert result.status == "disqualified"
        assert result.disqualify_reason == "Budget too low"
        assert job.status == "disqualified"
        assert job.disqualify_reason == "Budget too low"
        db_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_disqualification_of_qualified_job(self) -> None:
        """Should successfully disqualify a job in 'qualified' status."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="qualified")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        data = JobDisqualifySchema(reason="Client has bad reviews")

        result = await controller.disqualify.fn(
            controller,
            job_id=job_id,
            data=data,
            db_session=db_session,
            request=request,
        )

        assert result.id == job_id
        assert result.status == "disqualified"
        assert job.status == "disqualified"

    @pytest.mark.asyncio
    async def test_raises_404_when_job_not_found(self) -> None:
        """Should raise NotFoundException when job does not exist."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        data = JobDisqualifySchema(reason="Test")

        with pytest.raises(NotFoundException, match=f"Job {job_id} not found"):
            await controller.disqualify.fn(
                controller,
                job_id=job_id,
                data=data,
                db_session=db_session,
                request=request,
            )

    @pytest.mark.asyncio
    async def test_cannot_disqualify_bid_sent_status_job(self) -> None:
        """Should raise MASException when trying to disqualify a job with 'bid_sent' status."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="bid_sent")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        data = JobDisqualifySchema(reason="Changed my mind")

        with pytest.raises(MASException, match="Cannot disqualify job in 'bid_sent' status"):
            await controller.disqualify.fn(
                controller,
                job_id=job_id,
                data=data,
                db_session=db_session,
                request=request,
            )

        # Should not have flushed
        db_session.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stores_disqualify_reason(self) -> None:
        """Should store the provided disqualify reason in the database."""
        controller = JobController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        job_id = uuid.uuid4()
        job = _create_mock_job(job_id=job_id, status="new")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        db_session.execute.return_value = result_mock

        data = JobDisqualifySchema(reason="Skills mismatch")

        await controller.disqualify.fn(
            controller,
            job_id=job_id,
            data=data,
            db_session=db_session,
            request=request,
        )

        assert job.disqualify_reason == "Skills mismatch"


# ---------------------------------------------------------------------------
# Tests: _job_to_schema helper
# ---------------------------------------------------------------------------


class TestJobToSchema:
    """Tests for the _job_to_schema helper function."""

    def test_converts_job_with_bids_correctly(self) -> None:
        """Should convert Job ORM instance with bids to JobResponseSchema."""
        bid1 = _create_mock_bid(bid_amount=Decimal("150"))
        bid2 = _create_mock_bid(bid_amount=Decimal("200"))
        job = _create_mock_job(bids=[bid1, bid2])

        schema = _job_to_schema(job)

        assert schema.id == job.id
        assert schema.platform == job.platform
        assert schema.title == job.title
        assert len(schema.bids) == 2
        assert schema.bids[0].bid_amount == Decimal("150")
        assert schema.bids[1].bid_amount == Decimal("200")

    def test_converts_job_with_no_bids(self) -> None:
        """Should convert Job ORM instance with no bids to JobResponseSchema with empty bids list."""
        job = _create_mock_job(bids=[])

        schema = _job_to_schema(job)

        assert schema.id == job.id
        assert len(schema.bids) == 0

    def test_all_fields_mapped_correctly(self) -> None:
        """Should map all Job ORM fields to schema correctly."""
        job_id = uuid.uuid4()
        discovered_at = datetime.now(UTC)
        job = _create_mock_job(job_id=job_id)
        job.discovered_at = discovered_at

        schema = _job_to_schema(job)

        assert schema.id == job_id
        assert schema.platform == "freelancer"
        assert schema.external_id == "ext-12345"
        assert schema.title == "Build a website"
        assert schema.description == "Need a landing page for my business"
        assert schema.budget_min == Decimal("100")
        assert schema.budget_max == Decimal("500")
        assert schema.budget_type == "fixed"
        assert schema.currency == "USD"
        assert schema.client_info == {"rating": 4.5, "reviews": 10}
        assert schema.skills_required == ["python", "react"]
        assert schema.deadline is None
        assert schema.status == "new"
        assert schema.score == Decimal("0.85")
        assert schema.disqualify_reason is None
        assert schema.discovered_at == discovered_at
        assert schema.url == "https://example.com/job/12345"
