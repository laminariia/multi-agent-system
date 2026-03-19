"""Freelancer.com REST API adapter.

Handles OAuth2-authenticated requests for job discovery and bid submission.
Rate limits: 100 req/hour for API, 30 req/min for search, 10 bids/hour.

Reference: https://developers.freelancer.com/docs
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from src.adapters.rate_limiter import AdaptiveRateLimiter
from src.core.exceptions import (
    PlatformAPIError,
    PlatformBannedError,
    PlatformRateLimitError,
)

logger = structlog.get_logger(__name__)

# Default timeout for all Freelancer API calls (seconds).
_DEFAULT_TIMEOUT = 30.0

# Timeout for individual HTTP requests (seconds).
_REQUEST_TIMEOUT = 30.0

# Pagination defaults.
_DEFAULT_MAX_PAGES = 5
_PAGE_SIZE = 100
_PAGE_DELAY_SECONDS = 1.0


class FreelancerClient:
    """Async client for the Freelancer.com REST API.

    Parameters
    ----------
    client_id:
        OAuth2 application client ID.
    client_secret:
        OAuth2 application client secret.
    access_token:
        Pre-obtained OAuth2 bearer token.  If provided, the client skips
        the token-exchange flow and uses this token directly.
    base_url:
        API root (override for sandbox/testing).
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        access_token: str = "",
        base_url: str = "https://www.freelancer.com/api",
        rate_limiter: AdaptiveRateLimiter | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token = access_token
        self.base_url = base_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None
        self._log = logger.bind(platform="freelancer")
        self._rate_limiter = rate_limiter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_http(self) -> httpx.AsyncClient:
        """Return (and lazily create) the shared httpx client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._auth_headers(),
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
            )
        return self._http

    async def close(self) -> None:
        """Shut down the underlying HTTP client gracefully."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._access_token:
            headers["Freelancer-OAuth-V1"] = self._access_token
        return headers

    def set_access_token(self, token: str) -> None:
        """Update the bearer token (e.g. after a refresh)."""
        self._access_token = token
        # Force recreation of the http client so the new header takes effect.
        if self._http is not None and not self._http.is_closed:
            self._http.headers["Freelancer-OAuth-V1"] = token

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    def _handle_response(self, response: httpx.Response, *, operation: str) -> dict[str, Any]:
        """Inspect *response* and raise appropriate MAS exceptions on error."""
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            retry_seconds = float(retry_after) if retry_after else None
            self._log.warning("rate_limited", operation=operation, retry_after=retry_seconds)
            raise PlatformRateLimitError(
                platform="freelancer",
                operation=operation,
                retry_after_seconds=retry_seconds,
            )

        if response.status_code == 403:
            raise PlatformBannedError(
                message="Freelancer API returned 403 Forbidden — possible account issue",
                platform="freelancer",
                operation=operation,
            )

        if response.status_code >= 400:
            body_text = response.text[:500]
            self._log.error(
                "api_error",
                operation=operation,
                status=response.status_code,
                body=body_text,
            )
            raise PlatformAPIError(
                f"Freelancer API error {response.status_code}: {body_text}",
                platform="freelancer",
                operation=operation,
                details={"status_code": response.status_code, "body": body_text},
            )

        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            raise PlatformAPIError(
                f"Failed to decode Freelancer JSON response: {exc}",
                platform="freelancer",
                operation=operation,
            ) from exc

        # Freelancer wraps errors inside {"status": "error", "message": "..."}
        if data.get("status") == "error":
            error_msg = data.get("message", "Unknown error")
            raise PlatformAPIError(
                f"Freelancer API logical error: {error_msg}",
                platform="freelancer",
                operation=operation,
                details={"api_error": error_msg},
            )

        return data

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def fetch_jobs(
        self,
        category: str,
        min_budget: int,
        max_results: int | None = None,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> list[dict[str, Any]]:
        """Fetch active projects matching *category* and *min_budget* with pagination.

        Calls ``GET /projects/0.1/projects/active/`` repeatedly with increasing
        ``offset`` until *max_pages* is reached, an empty/partial page is returned,
        or *max_results* total jobs have been collected.

        Parameters
        ----------
        category:
            Freelancer job category slug (e.g. ``"python"``).
        min_budget:
            Minimum average price filter.
        max_results:
            Maximum total number of jobs to return across all pages.
        max_pages:
            Maximum number of API pages to fetch (default 5 = 500 results max).

        Returns a normalised list of job dicts.
        """
        http = await self._get_http()
        all_jobs: list[dict[str, Any]] = []
        start = time.monotonic()

        self._log.info(
            "fetch_jobs",
            category=category,
            min_budget=min_budget,
            max_results=max_results,
            max_pages=max_pages,
        )

        for page_num in range(max_pages):
            if self._rate_limiter:
                await self._rate_limiter.acquire("freelancer")

            offset = page_num * _PAGE_SIZE
            if max_results is not None:
                remaining = max_results - len(all_jobs)
                page_limit = min(remaining, _PAGE_SIZE)
            else:
                page_limit = _PAGE_SIZE
            params: dict[str, Any] = {
                "jobs[]": category,
                "min_avg_price": min_budget,
                "limit": page_limit,
                "offset": offset,
                "full_description": True,
                "job_details": True,
                "user_details": True,
                "compact": False,
            }

            response = await asyncio.wait_for(
                http.get("/projects/0.1/projects/active/", params=params),
                timeout=_REQUEST_TIMEOUT,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._log.debug(
                "fetch_jobs_page_response",
                page=page_num + 1,
                status=response.status_code,
                latency_ms=elapsed_ms,
            )

            data = self._handle_response(response, operation="fetch_jobs")
            raw_projects: list[dict[str, Any]] = data.get("result", {}).get("projects", [])

            page_jobs = self._normalise_projects(raw_projects)
            all_jobs.extend(page_jobs)

            self._log.info(
                "fetch_jobs_page_done",
                page=page_num + 1,
                page_count=len(page_jobs),
                total=len(all_jobs),
            )

            # Stop on empty page, partial page (< requested limit), or max_results reached.
            if not page_jobs or len(page_jobs) < page_limit:
                break
            if max_results is not None and len(all_jobs) >= max_results:
                break

            # Delay between pages for rate limiting.
            if page_num < max_pages - 1:
                await asyncio.sleep(_PAGE_DELAY_SECONDS)

        self._log.info("fetch_jobs_done", count=len(all_jobs))
        return all_jobs[:max_results]

    @staticmethod
    def _normalise_projects(raw_projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalise a list of raw Freelancer project dicts into standard format."""
        normalised: list[dict[str, Any]] = []
        for proj in raw_projects:
            budget_info = proj.get("budget", {})
            owner = proj.get("owner", {})
            normalised.append(
                {
                    "external_id": str(proj.get("id", "")),
                    "platform": "freelancer",
                    "title": proj.get("title", ""),
                    "description": proj.get("description", proj.get("preview_description", "")),
                    "budget_min": budget_info.get("minimum"),
                    "budget_max": budget_info.get("maximum"),
                    "currency": proj.get("currency", {}).get("code", "USD"),
                    "skills_required": [j.get("name", "") for j in proj.get("jobs", [])],
                    "client_info": {
                        "user_id": owner.get("id"),
                        "username": owner.get("username"),
                        "rating": owner.get("employer_reputation", {}).get("overall"),
                        "country": owner.get("location", {}).get("country", {}).get("name"),
                    },
                    "url": f"https://www.freelancer.com/projects/{proj.get('seo_url', '')}",
                    "raw_data": proj,
                }
            )
        return normalised

    async def submit_bid(
        self,
        project_id: int,
        description: str,
        amount: float,
        period: int,
        milestone_percentage: int = 100,
    ) -> dict[str, Any]:
        """Submit a bid to a Freelancer project.

        Calls ``POST /projects/0.1/bids/``.

        Parameters
        ----------
        project_id:
            Numeric Freelancer project ID.
        description:
            Proposal text.
        amount:
            Bid amount in the project's currency.
        period:
            Delivery period in days.
        milestone_percentage:
            Percentage of the bid to request as an initial milestone (0–100).

        Returns the API response dict on success.
        """
        http = await self._get_http()
        payload: dict[str, Any] = {
            "project_id": project_id,
            "description": description,
            "amount": amount,
            "period": period,
            "milestone_percentage": milestone_percentage,
        }

        if self._rate_limiter:
            await self._rate_limiter.acquire("freelancer")

        self._log.info("submit_bid", project_id=project_id, amount=amount, period=period)
        start = time.monotonic()
        response = await asyncio.wait_for(
            http.post("/projects/0.1/bids/", json=payload),
            timeout=_REQUEST_TIMEOUT,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log.debug("submit_bid_response", status=response.status_code, latency_ms=elapsed_ms)

        data = self._handle_response(response, operation="submit_bid")
        self._log.info("submit_bid_done", project_id=project_id, bid_id=data.get("result", {}).get("id"))
        return data

    async def get_client_profile(self, user_id: int) -> dict[str, Any]:
        """Fetch a Freelancer user's public profile.

        Calls ``GET /users/0.1/users/{user_id}/``.

        Returns a dict with normalised fields:
        ``user_id``, ``username``, ``rating``, ``hire_rate``,
        ``total_spent``, ``reviews_count``, ``country``, ``raw_data``.
        """
        http = await self._get_http()
        url = f"/users/0.1/users/{user_id}/"
        params: dict[str, Any] = {
            "reputation": True,
            "employer_reputation": True,
            "jobs": True,
            "portfolio": True,
        }

        if self._rate_limiter:
            await self._rate_limiter.acquire("freelancer")

        self._log.info("get_client_profile", user_id=user_id)
        start = time.monotonic()
        response = await asyncio.wait_for(
            http.get(url, params=params),
            timeout=_REQUEST_TIMEOUT,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log.debug("get_client_profile_response", status=response.status_code, latency_ms=elapsed_ms)

        data = self._handle_response(response, operation="get_client_profile")
        user_data: dict[str, Any] = data.get("result", {})

        employer_rep = user_data.get("employer_reputation", {})
        reputation = user_data.get("reputation", {})

        profile: dict[str, Any] = {
            "user_id": user_data.get("id"),
            "username": user_data.get("username"),
            "display_name": user_data.get("display_name", ""),
            "rating": employer_rep.get("overall", reputation.get("overall")),
            "hire_rate": employer_rep.get("completion_rate"),
            "total_reviews": employer_rep.get("reviews", 0) + reputation.get("reviews", 0),
            "total_spent": employer_rep.get("earnings"),
            "country": user_data.get("location", {}).get("country", {}).get("name"),
            "registration_date": user_data.get("registration_date"),
            "raw_data": user_data,
        }

        self._log.info("get_client_profile_done", user_id=user_id, rating=profile.get("rating"))
        return profile
