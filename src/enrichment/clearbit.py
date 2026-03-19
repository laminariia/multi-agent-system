"""Clearbit person and company enrichment API integration.

Provides person lookup (by email) and company lookup (by domain) using
the Clearbit Enrichment API.  Returns typed dicts with normalized fields,
or ``None`` on any failure (graceful degradation).

Pricing: ~$0.05-0.10 per request depending on plan.

Usage::

    client = ClearbitClient(api_key="sk_...")
    person = await client.person_lookup("ceo@example.com")
    company = await client.company_lookup("example.com")
    await client.close()
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

CLEARBIT_PERSON_API = "https://person.clearbit.com/v2/people/find"
CLEARBIT_COMPANY_API = "https://company.clearbit.com/v2/companies/find"


class ClearbitClient:
    """Clearbit enrichment client -- person and company lookup.

    Uses Bearer token authentication.  All public methods return ``None``
    on failure (network error, 404, rate limit, missing API key) so the
    caller never needs to handle exceptions.

    Args:
        api_key: Clearbit API key (``sk_...``).  Pass empty string or
            ``None`` to disable.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key or ""
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if the API key is set."""
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create (or re-create) the HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    # ------------------------------------------------------------------
    # Person lookup
    # ------------------------------------------------------------------

    async def person_lookup(self, email: str) -> dict[str, Any] | None:
        """Look up a person by email address.

        Args:
            email: The email address to look up.

        Returns:
            A dict with normalized person fields, or ``None`` if the
            lookup failed or the person was not found.
        """
        if not self._api_key:
            logger.warning("clearbit_no_api_key", method="person_lookup")
            return None

        try:
            client = await self._get_client()
            resp = await client.get(
                CLEARBIT_PERSON_API,
                params={"email": email},
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            name = data.get("name") or {}
            employment = data.get("employment") or {}
            linkedin = data.get("linkedin") or {}
            twitter = data.get("twitter") or {}

            return {
                "email": data.get("email"),
                "full_name": name.get("fullName"),
                "first_name": name.get("givenName"),
                "last_name": name.get("familyName"),
                "company": employment.get("name"),
                "title": employment.get("title"),
                "role": employment.get("role"),
                "seniority": employment.get("seniority"),
                "location": data.get("location"),
                "linkedin": linkedin.get("handle"),
                "twitter": twitter.get("handle"),
                "source": "clearbit",
            }

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "clearbit_person_api_error",
                status=exc.response.status_code,
                email=email,
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "clearbit_person_request_failed",
                email=email,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Company lookup
    # ------------------------------------------------------------------

    async def company_lookup(self, domain: str) -> dict[str, Any] | None:
        """Look up a company by domain.

        Args:
            domain: The company domain (e.g. ``"example.com"``).

        Returns:
            A dict with normalized company fields, or ``None`` if the
            lookup failed or the company was not found.
        """
        if not self._api_key:
            logger.warning("clearbit_no_api_key", method="company_lookup")
            return None

        try:
            client = await self._get_client()
            resp = await client.get(
                CLEARBIT_COMPANY_API,
                params={"domain": domain},
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            category = data.get("category") or {}
            metrics = data.get("metrics") or {}
            geo = data.get("geo") or {}
            linkedin = data.get("linkedin") or {}
            twitter = data.get("twitter") or {}

            return {
                "name": data.get("name"),
                "domain": data.get("domain"),
                "industry": category.get("industry"),
                "sector": category.get("sector"),
                "employees": metrics.get("employees"),
                "employees_range": metrics.get("employeesRange"),
                "annual_revenue": metrics.get("annualRevenue"),
                "city": geo.get("city"),
                "state": geo.get("state"),
                "country": geo.get("country"),
                "description": data.get("description"),
                "url": data.get("url"),
                "logo": data.get("logo"),
                "linkedin": linkedin.get("handle"),
                "twitter": twitter.get("handle"),
                "tech": data.get("tech"),
                "source": "clearbit",
            }

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "clearbit_company_api_error",
                status=exc.response.status_code,
                domain=domain,
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "clearbit_company_request_failed",
                domain=domain,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
