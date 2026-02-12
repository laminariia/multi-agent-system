"""Hunter.io email finder API integration."""

from __future__ import annotations

import httpx
import structlog

from src.enrichment.osint import EnrichmentResult

logger = structlog.get_logger(__name__)

HUNTER_API_BASE = "https://api.hunter.io/v2"


class HunterEnricher:
    """Hunter.io email finder -- finds professional email addresses by domain.

    Pricing: ~$0.01 per request.

    Args:
        api_key: Hunter.io API key.
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

    async def enrich(self, domain: str) -> EnrichmentResult:
        """Find emails associated with a domain using Hunter.io domain-search.

        Args:
            domain: The company's domain name (e.g. ``"acme.com"``).

        Returns:
            EnrichmentResult with the first found email address.
        """
        if not self._api_key:
            logger.warning("hunter_no_api_key")
            return EnrichmentResult(source="hunter", confidence=0.0)

        try:
            client = await self._get_client()
            resp = await client.get(
                f"{HUNTER_API_BASE}/domain-search",
                params={"domain": domain, "api_key": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()

            emails_data = data.get("data", {}).get("emails", [])
            if not emails_data:
                return EnrichmentResult(source="hunter", confidence=0.0)

            # Take the highest-confidence email
            best = max(emails_data, key=lambda e: e.get("confidence", 0))
            confidence_val = best.get("confidence", 0) / 100  # Hunter returns 0-100

            return EnrichmentResult(
                email=best.get("value"),
                phone=None,  # Hunter doesn't provide phone numbers
                source="hunter",
                confidence=confidence_val,
                raw_data={
                    "domain": domain,
                    "total_found": len(emails_data),
                },
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "hunter_api_error",
                status=exc.response.status_code,
                domain=domain,
            )
            return EnrichmentResult(source="hunter", confidence=0.0)
        except httpx.HTTPError as exc:
            logger.warning(
                "hunter_request_failed",
                domain=domain,
                error=str(exc),
            )
            return EnrichmentResult(source="hunter", confidence=0.0)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
