"""Apollo.io people search API integration."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.enrichment.osint import EnrichmentResult

logger = structlog.get_logger(__name__)

APOLLO_API_BASE = "https://api.apollo.io/v1"


class ApolloEnricher:
    """Apollo.io people search -- finds decision makers and their contact info.

    Pricing: ~$0.05 per request (higher cost, higher quality).

    Args:
        api_key: Apollo.io API key.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, api_key: str, *, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def enrich(
        self,
        business_name: str,
        *,
        city: str | None = None,
    ) -> EnrichmentResult:
        """Search Apollo for people at the given business.

        Args:
            business_name: Name of the business to search.
            city: Optional city to narrow results.

        Returns:
            EnrichmentResult with decision maker's email.
        """
        if not self._api_key:
            logger.warning("apollo_no_api_key")
            return EnrichmentResult(source="apollo", confidence=0.0)

        try:
            client = await self._get_client()
            params: dict[str, Any] = {
                "q_organization_name": business_name,
                "page": 1,
                "per_page": 5,
            }
            if city:
                params["person_locations[]"] = city

            resp = await client.post(
                f"{APOLLO_API_BASE}/mixed_people/search",
                headers={
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                },
                json={"api_key": self._api_key, **params},
            )
            resp.raise_for_status()
            data = resp.json()

            people = data.get("people", [])
            if not people:
                return EnrichmentResult(source="apollo", confidence=0.0)

            # Prefer people with email, prioritize owners/managers
            best = None
            for person in people:
                if person.get("email"):
                    best = person
                    break

            if not best:
                return EnrichmentResult(source="apollo", confidence=0.0)

            phone_numbers = best.get("phone_numbers") or []
            phone = phone_numbers[0].get("raw_number") if phone_numbers else None

            return EnrichmentResult(
                email=best.get("email"),
                phone=phone,
                source="apollo",
                confidence=0.75,  # Apollo is generally high quality
                raw_data={
                    "name": best.get("name"),
                    "title": best.get("title"),
                    "organization": (best.get("organization") or {}).get("name"),
                },
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "apollo_api_error",
                status=exc.response.status_code,
                business=business_name,
            )
            return EnrichmentResult(source="apollo", confidence=0.0)
        except httpx.HTTPError as exc:
            logger.warning(
                "apollo_request_failed",
                business=business_name,
                error=str(exc),
            )
            return EnrichmentResult(source="apollo", confidence=0.0)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
