"""Enrichment waterfall -- tries multiple sources to find contact info.

Order: OSINT (free) -> Hunter.io ($0.01) -> Apollo.io ($0.05)
Stops at first successful email find to minimize cost.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from src.enrichment.apollo import ApolloEnricher
from src.enrichment.hunter import HunterEnricher
from src.enrichment.osint import EnrichmentResult, OsintEnricher

logger = structlog.get_logger(__name__)

# Cost per request for each source
_SOURCE_COSTS: dict[str, Decimal] = {
    "osint_duckduckgo": Decimal("0"),
    "hunter": Decimal("0.01"),
    "apollo": Decimal("0.05"),
}


class EnrichmentWaterfall:
    """Cascading enrichment pipeline that tries sources cheapest-first.

    Args:
        hunter_api_key: Hunter.io API key (optional -- skipped if empty).
        apollo_api_key: Apollo.io API key (optional -- skipped if empty).
    """

    def __init__(
        self,
        *,
        hunter_api_key: str | None = None,
        apollo_api_key: str | None = None,
    ) -> None:
        self._osint = OsintEnricher()
        self._hunter = HunterEnricher(hunter_api_key) if hunter_api_key else None
        self._apollo = ApolloEnricher(apollo_api_key) if apollo_api_key else None
        self._total_cost = Decimal("0")

    @property
    def total_cost(self) -> Decimal:
        """Total enrichment cost accumulated during this session."""
        return self._total_cost

    async def enrich(
        self,
        business_name: str,
        city: str,
        *,
        domain: str | None = None,
    ) -> EnrichmentResult:
        """Try each enrichment source in order until an email is found.

        Args:
            business_name: Name of the business.
            city: City where the business is located.
            domain: Optional domain for Hunter.io lookup.

        Returns:
            Best EnrichmentResult found, or empty result if all sources
            failed.
        """
        # 1. OSINT (free)
        logger.debug("enrichment_trying", source="osint", business=business_name)
        result = await self._osint.enrich(business_name, city)
        if result.email:
            logger.info("enrichment_found", source="osint", business=business_name)
            return result

        # 2. Hunter.io (if domain known and API key available)
        if domain and self._hunter:
            logger.debug("enrichment_trying", source="hunter", domain=domain)
            result = await self._hunter.enrich(domain)
            self._total_cost += _SOURCE_COSTS["hunter"]
            if result.email:
                result.raw_data = {
                    **(result.raw_data or {}),
                    "cost": float(_SOURCE_COSTS["hunter"]),
                }
                logger.info(
                    "enrichment_found",
                    source="hunter",
                    business=business_name,
                )
                return result

        # 3. Apollo.io (most expensive, highest quality)
        if self._apollo:
            logger.debug("enrichment_trying", source="apollo", business=business_name)
            result = await self._apollo.enrich(business_name, city=city)
            self._total_cost += _SOURCE_COSTS["apollo"]
            if result.email:
                result.raw_data = {
                    **(result.raw_data or {}),
                    "cost": float(_SOURCE_COSTS["apollo"]),
                }
                logger.info(
                    "enrichment_found",
                    source="apollo",
                    business=business_name,
                )
                return result

        logger.info("enrichment_not_found", business=business_name, city=city)
        return EnrichmentResult(source="none", confidence=0.0)

    async def close(self) -> None:
        """Close all HTTP clients."""
        await self._osint.close()
        if self._hunter:
            await self._hunter.close()
        if self._apollo:
            await self._apollo.close()
