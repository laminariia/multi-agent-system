"""Unit tests for pagination support in Kwork and Freelancer adapters.

Tests cover:
- Kwork: multi-page scraping with limit per page, max_pages, delay, empty-page stop
- Freelancer: offset-based pagination with limit=100, max_pages, delay, empty-page stop
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.adapters.freelancer import FreelancerClient
from src.adapters.kwork import KworkClient

# ============================================================================
# Kwork helpers
# ============================================================================


def _mock_element(inner_text: str = "", href: str = "") -> AsyncMock:
    """Create a mock Playwright element handle."""
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=inner_text)
    el.get_attribute = AsyncMock(return_value=href)
    return el


def _card_qs(data: dict[str, Any]):
    """Return a side_effect for card.query_selector."""

    async def _qs(selector: str) -> AsyncMock | None:
        if "title" in selector.lower() or "h3" in selector:
            return _mock_element(data.get("title", "Job"), data.get("href", "/projects/1"))
        if "link" in selector.lower():
            return _mock_element(data.get("title", "Job"), data.get("href", "/projects/1"))
        if "description" in selector.lower() or "breakwords" in selector:
            return _mock_element(data.get("description", "Desc"))
        if "price" in selector.lower() or "budget" in selector.lower():
            return _mock_element(data.get("budget", "1000 руб"))
        return None

    return _qs


def _make_kwork_cards(n: int, id_offset: int = 0) -> list[dict[str, Any]]:
    """Generate n card data dicts with sequential IDs."""
    return [
        {
            "title": f"Job {id_offset + i}",
            "href": f"/projects/{id_offset + i}",
            "description": f"Description {id_offset + i}",
            "budget": "5000 руб",
        }
        for i in range(n)
    ]


def _mock_page_for_cards(
    cards_data: list[dict[str, Any]],
    has_captcha: bool = False,
    has_cloudflare: bool = False,
    has_ban: bool = False,
) -> AsyncMock:
    """Create a mock StealthPage with configurable DOM responses."""
    underlying = AsyncMock()
    page = AsyncMock()
    page.page = underlying
    page.goto = AsyncMock()
    page.wait_random = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")

    async def query_selector(selector: str) -> AsyncMock | None:
        if "captcha" in selector.lower() or "challenge-running" in selector:
            return AsyncMock() if has_captcha else None
        if "challenge-stage" in selector or "cf-browser-verification" in selector:
            return AsyncMock() if has_cloudflare else None
        if "blocked-user" in selector or "account-blocked" in selector:
            return AsyncMock() if has_ban else None
        return None

    underlying.query_selector = AsyncMock(side_effect=query_selector)

    if cards_data:
        card_mocks = []
        for card_data in cards_data:
            card = AsyncMock()
            card.query_selector = AsyncMock(side_effect=_card_qs(card_data))
            card_mocks.append(card)
        underlying.query_selector_all = AsyncMock(return_value=card_mocks)
    else:
        underlying.query_selector_all = AsyncMock(return_value=[])

    return page


def _mock_pool_sequence(pages: list[AsyncMock]) -> AsyncMock:
    """Create a mock BrowserPool that returns pages in sequence."""
    pool = AsyncMock()
    pool.acquire = AsyncMock(side_effect=pages)
    pool.release = AsyncMock()
    return pool


# ============================================================================
# Freelancer helpers
# ============================================================================


def _freelancer_response(projects: list[dict[str, Any]]) -> MagicMock:
    """Create a mock httpx.Response with projects data."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {"result": {"projects": projects}}
    return response


def _make_freelancer_projects(n: int, id_offset: int = 0) -> list[dict[str, Any]]:
    """Generate n project dicts with sequential IDs."""
    return [
        {
            "id": id_offset + i,
            "title": f"Project {id_offset + i}",
            "description": f"Description {id_offset + i}",
            "budget": {"minimum": 100, "maximum": 500},
            "currency": {"code": "USD"},
            "jobs": [{"name": "Python"}],
            "owner": {
                "id": 1000 + i,
                "username": f"user{id_offset + i}",
                "employer_reputation": {"overall": 4.5},
                "location": {"country": {"name": "US"}},
            },
            "seo_url": f"project-{id_offset + i}",
        }
        for i in range(n)
    ]


# ============================================================================
# Kwork Pagination Tests
# ============================================================================


class TestKworkPagination:
    """Test multi-page scraping for KworkClient.fetch_jobs."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_accepts_max_pages_param(self) -> None:
        """fetch_jobs accepts max_pages parameter without error."""
        page = _mock_page_for_cards(_make_kwork_cards(3))
        pool = _mock_pool_sequence([page])
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(max_pages=1)
        assert isinstance(jobs, list)

    @pytest.mark.asyncio
    async def test_single_page_returns_all_results(self) -> None:
        """Single page (max_pages=1) returns cards from that page."""
        cards = _make_kwork_cards(5)
        page = _mock_page_for_cards(cards)
        pool = _mock_pool_sequence([page])
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(max_pages=1)
        assert len(jobs) == 5

    @pytest.mark.asyncio
    async def test_multiple_pages_accumulates_results(self) -> None:
        """Multiple pages accumulate results across pages."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=0))
        page2 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(max_pages=2)
        assert len(jobs) == 6

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self) -> None:
        """Stops iterating when a page returns no cards."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards([])  # empty page -> stop
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(max_pages=5)
        assert len(jobs) == 3
        # Should only have acquired 2 pages, not 5
        assert pool.acquire.call_count == 2

    @pytest.mark.asyncio
    async def test_respects_max_pages_limit(self) -> None:
        """Does not exceed max_pages even if more results available."""
        pages = [_mock_page_for_cards(_make_kwork_cards(10, id_offset=i * 100)) for i in range(3)]
        pool = _mock_pool_sequence(pages)
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(max_pages=3)
        assert len(jobs) == 30
        assert pool.acquire.call_count == 3

    @pytest.mark.asyncio
    async def test_default_max_pages_is_five(self) -> None:
        """Default max_pages is 5."""
        pages = [_mock_page_for_cards(_make_kwork_cards(2, id_offset=i * 100)) for i in range(5)]
        # 6th page should never be requested
        pages.append(_mock_page_for_cards(_make_kwork_cards(2, id_offset=999)))
        pool = _mock_pool_sequence(pages)
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert len(jobs) == 10
        assert pool.acquire.call_count == 5

    @pytest.mark.asyncio
    async def test_max_results_caps_total(self) -> None:
        """max_results caps total results across all pages."""
        page1 = _mock_page_for_cards(_make_kwork_cards(80, id_offset=0))
        page2 = _mock_page_for_cards(_make_kwork_cards(80, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(max_results=100, max_pages=5)
        assert len(jobs) <= 100

    @pytest.mark.asyncio
    async def test_releases_page_after_each_iteration(self) -> None:
        """Each page is released after scraping."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        await client.fetch_jobs(max_pages=2)
        assert pool.release.call_count == 2

    @pytest.mark.asyncio
    async def test_delay_between_pages(self) -> None:
        """There is a delay between page fetches (rate limiting)."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        with patch("src.adapters.kwork.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await client.fetch_jobs(max_pages=2)
            # Should sleep between pages (at least once for 2 pages)
            assert mock_sleep.call_count >= 1
            # Delay should be positive
            for call in mock_sleep.call_args_list:
                assert call[0][0] > 0

    @pytest.mark.asyncio
    async def test_page_url_includes_page_number(self) -> None:
        """Page URL for subsequent pages includes page parameter."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        await client.fetch_jobs(max_pages=2)

        # Second page: should contain page=2
        second_goto_url = page2.goto.call_args[0][0]
        assert "page=2" in second_goto_url or "2" in second_goto_url

    @pytest.mark.asyncio
    async def test_pagination_with_category(self) -> None:
        """Pagination works with category parameter."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        await client.fetch_jobs(category="web", max_pages=2)

        first_url = page1.goto.call_args[0][0]
        assert "c=web" in first_url

    @pytest.mark.asyncio
    async def test_releases_page_on_error_mid_pagination(self) -> None:
        """Page is released even if an error occurs during pagination."""
        from src.core.exceptions import CaptchaDetectedError

        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards([], has_captcha=True)
        pool = _mock_pool_sequence([page1, page2])
        client = KworkClient(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs(max_pages=3)

        # Both pages should be released
        assert pool.release.call_count == 2

    @pytest.mark.asyncio
    async def test_rate_limiter_called_per_page(self) -> None:
        """Rate limiter is called once per page, not just once total."""
        page1 = _mock_page_for_cards(_make_kwork_cards(3))
        page2 = _mock_page_for_cards(_make_kwork_cards(3, id_offset=100))
        pool = _mock_pool_sequence([page1, page2])
        rate_limiter = AsyncMock()
        rate_limiter.acquire = AsyncMock()
        client = KworkClient(browser_pool=pool, rate_limiter=rate_limiter)

        await client.fetch_jobs(max_pages=2)
        assert rate_limiter.acquire.call_count == 2


# ============================================================================
# Freelancer Pagination Tests
# ============================================================================


class TestFreelancerPagination:
    """Test offset-based pagination for FreelancerClient.fetch_jobs."""

    @pytest.fixture
    def client(self) -> FreelancerClient:
        return FreelancerClient(
            client_id="test-id",  # noqa: S106
            client_secret="test-secret",  # noqa: S106
            access_token="test-token",  # noqa: S106
        )

    @pytest.fixture
    def mock_http(self) -> AsyncMock:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.is_closed = False
        http.headers = {}
        return http

    @pytest.mark.asyncio
    async def test_fetch_jobs_accepts_max_pages_param(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """fetch_jobs accepts max_pages parameter without error."""
        resp = _freelancer_response(_make_freelancer_projects(5))
        mock_http.get = AsyncMock(return_value=resp)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_pages=1)

        assert isinstance(jobs, list)

    @pytest.mark.asyncio
    async def test_single_page_returns_results(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Single page returns results normally."""
        projects = _make_freelancer_projects(10)
        resp = _freelancer_response(projects)
        mock_http.get = AsyncMock(return_value=resp)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_pages=1)

        assert len(jobs) == 10

    @pytest.mark.asyncio
    async def test_multiple_pages_accumulates_results(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Multiple pages accumulate results across API calls."""
        resp1 = _freelancer_response(_make_freelancer_projects(100, id_offset=0))
        resp2 = _freelancer_response(_make_freelancer_projects(50, id_offset=100))
        mock_http.get = AsyncMock(side_effect=[resp1, resp2])

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_pages=2)

        assert len(jobs) == 150

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Stops iterating when API returns empty projects list."""
        resp1 = _freelancer_response(_make_freelancer_projects(100, id_offset=0))
        resp2 = _freelancer_response([])  # empty -> stop
        mock_http.get = AsyncMock(side_effect=[resp1, resp2])

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_pages=5)

        assert len(jobs) == 100
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_respects_max_pages_limit(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Does not exceed max_pages even if more results available."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(3)]
        mock_http.get = AsyncMock(side_effect=responses)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_pages=3)

        assert len(jobs) == 300
        assert mock_http.get.call_count == 3

    @pytest.mark.asyncio
    async def test_default_max_pages_is_five(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Default max_pages is 5."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(6)]
        mock_http.get = AsyncMock(side_effect=responses)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100)

        assert len(jobs) == 500
        assert mock_http.get.call_count == 5

    @pytest.mark.asyncio
    async def test_offset_increases_per_page(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Offset parameter increases by 100 for each subsequent page."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(3)]
        mock_http.get = AsyncMock(side_effect=responses)

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.fetch_jobs("python", 100, max_pages=3)

        calls = mock_http.get.call_args_list
        # First page: offset=0 (or no offset)
        params0 = calls[0][1]["params"]
        assert params0.get("offset", 0) == 0
        # Second page: offset=100
        params1 = calls[1][1]["params"]
        assert params1["offset"] == 100
        # Third page: offset=200
        params2 = calls[2][1]["params"]
        assert params2["offset"] == 200

    @pytest.mark.asyncio
    async def test_limit_is_100_per_page(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Each page request uses limit=100."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(2)]
        mock_http.get = AsyncMock(side_effect=responses)

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.fetch_jobs("python", 100, max_pages=2)

        for call in mock_http.get.call_args_list:
            params = call[1]["params"]
            assert params["limit"] == 100

    @pytest.mark.asyncio
    async def test_delay_between_pages(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """There is a delay between page fetches."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(2)]
        mock_http.get = AsyncMock(side_effect=responses)

        with patch.object(client, "_get_http", return_value=mock_http):
            with patch("src.adapters.freelancer.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await client.fetch_jobs("python", 100, max_pages=2)
                # At least one sleep between pages
                assert mock_sleep.call_count >= 1
                for call in mock_sleep.call_args_list:
                    assert call[0][0] > 0

    @pytest.mark.asyncio
    async def test_stops_on_partial_page(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Stops when a page returns fewer than limit results (partial page)."""
        resp1 = _freelancer_response(_make_freelancer_projects(100, id_offset=0))
        resp2 = _freelancer_response(_make_freelancer_projects(30, id_offset=100))  # partial
        mock_http.get = AsyncMock(side_effect=[resp1, resp2])

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_pages=5)

        # Should stop after partial page
        assert len(jobs) == 130
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_max_results_caps_total(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """max_results caps total returned results across pages."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(3)]
        mock_http.get = AsyncMock(side_effect=responses)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_results=150, max_pages=5)

        assert len(jobs) <= 150

    @pytest.mark.asyncio
    async def test_rate_limiter_called_per_page(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Rate limiter is called once per page."""
        responses = [_freelancer_response(_make_freelancer_projects(100, id_offset=i * 100)) for i in range(3)]
        mock_http.get = AsyncMock(side_effect=responses)
        rate_limiter = AsyncMock()
        rate_limiter.acquire = AsyncMock()
        client._rate_limiter = rate_limiter  # noqa: SLF001

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.fetch_jobs("python", 100, max_pages=3)

        assert rate_limiter.acquire.call_count == 3

    @pytest.mark.asyncio
    async def test_backward_compatible_without_max_pages(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """fetch_jobs works without max_pages (backward compatibility)."""
        resp = _freelancer_response(_make_freelancer_projects(10))
        # After first partial page, should stop
        mock_http.get = AsyncMock(return_value=resp)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100, max_results=50)

        assert isinstance(jobs, list)
        assert len(jobs) > 0
