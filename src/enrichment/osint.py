"""Free OSINT enrichment -- scrape publicly available contact information."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EnrichmentResult:
    """Result from an enrichment source."""

    email: str | None = None
    phone: str | None = None
    website: str | None = None
    source: str = ""
    confidence: float = 0.0
    raw_data: dict[str, Any] | None = field(default=None)


class OsintEnricher:
    """Free OSINT-based enrichment using public web data.

    Searches for business contact information using DuckDuckGo (no API key
    needed).  This is the cheapest enrichment source (free) but lowest
    confidence.
    """

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                },
            )
        return self._http

    async def enrich(self, business_name: str, city: str) -> EnrichmentResult:
        """Search for contact info using DuckDuckGo HTML search.

        Searches for ``"{business_name} {city} email contact"`` and parses
        results for email patterns and phone numbers.
        """
        query = f"{business_name} {city} email contact"
        try:
            client = await self._get_client()
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            resp.raise_for_status()
            text = resp.text

            # Extract emails using regex
            emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
            # Filter out common false positives
            emails = [
                e
                for e in emails
                if not e.endswith((".png", ".jpg", ".gif", ".svg"))
                and "@" in e
                and "example.com" not in e
                and "duckduckgo" not in e
            ]

            # Extract phone numbers (international format)
            phones = re.findall(r"\+?\d[\d\s\-()]{8,}\d", text)

            email = emails[0] if emails else None
            phone = phones[0].strip() if phones else None

            confidence = 0.0
            if email:
                confidence = 0.4  # Low confidence -- found via search
            if phone and email:
                confidence = 0.5

            return EnrichmentResult(
                email=email,
                phone=phone,
                source="osint_duckduckgo",
                confidence=confidence,
                raw_data={
                    "query": query,
                    "emails_found": len(emails),
                    "phones_found": len(phones),
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("osint_search_failed", query=query, error=str(exc))
            return EnrichmentResult(source="osint_duckduckgo", confidence=0.0)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
