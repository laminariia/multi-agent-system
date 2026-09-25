"""Overpass API client for querying OpenStreetMap businesses.

Discovers local businesses that lack a website -- prime targets for
the Outreach pipeline.  Queries are rate-limited (default 1 req/s)
and results are parsed into ``GeoLead`` dataclass instances with
H3 cell indexes pre-computed.

Usage::

    from src.geo.h3_scanner import BoundingBox
    from src.geo.overpass import OverpassClient

    client = OverpassClient()
    leads = await client.query_businesses(
        BoundingBox(52.49, 13.28, 52.55, 13.47),
        city="Berlin",
    )
    await client.close()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import h3
import httpx
import structlog

from src.geo.h3_scanner import BoundingBox

logger = structlog.get_logger(__name__)

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"

# Business types to search for (amenity/shop tags without website).
BUSINESS_AMENITIES: list[str] = [
    "restaurant",
    "cafe",
    "bar",
    "fast_food",
    "beauty",
    "hairdresser",
    "dentist",
    "doctors",
    "pharmacy",
    "veterinary",
]

BUSINESS_SHOPS: list[str] = [
    "convenience",
    "supermarket",
    "clothes",
    "shoes",
    "furniture",
    "electronics",
    "hardware",
    "bakery",
    "butcher",
    "florist",
    "optician",
]


@dataclass
class GeoLead:
    """A business lead discovered from OpenStreetMap.

    Fields map directly to the ``leads`` database table columns.
    """

    osm_id: int
    name: str
    category: str
    lat: float
    lon: float
    address: str | None = None
    city: str | None = None
    phone: str | None = None
    h3_index: str | None = None
    raw_tags: dict[str, str] = field(default_factory=dict)


class OverpassClient:
    """Rate-limited Overpass API client.

    Parameters
    ----------
    rate_limit:
        Minimum seconds between consecutive requests (default ``1.0``).
    timeout:
        HTTP request timeout in seconds (default ``30.0``).
    """

    def __init__(self, *, rate_limit: float = 1.0, timeout: float = 30.0) -> None:
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._last_request: float = 0.0
        self._http: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared ``httpx.AsyncClient``."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def _rate_wait(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query_businesses(
        self,
        bbox: BoundingBox,
        *,
        city: str | None = None,
    ) -> list[GeoLead]:
        """Query Overpass for businesses WITHOUT a website in *bbox*.

        The Overpass QL query fetches nodes tagged with common
        ``amenity`` or ``shop`` values that have a ``name`` tag.
        Results are then filtered in Python to exclude any node that
        carries a ``website`` or ``contact:website`` tag.

        Parameters
        ----------
        bbox:
            Geographic bounding box to search within.
        city:
            Optional city name attached to every returned lead
            (falls back to ``addr:city`` from OSM tags).

        Returns
        -------
        list[GeoLead]:
            Parsed leads with H3 indexes pre-computed.
        """
        await self._rate_wait()

        query = self._build_query(bbox)
        client = await self._get_client()

        logger.debug(
            "overpass_query",
            bbox_south=bbox.min_lat,
            bbox_west=bbox.min_lon,
            bbox_north=bbox.max_lat,
            bbox_east=bbox.max_lon,
        )

        resp = await client.post(
            OVERPASS_API_URL,
            data={"data": query},
            headers={"User-Agent": "MultiAgentService/4.2 (overpass-client)"},
        )
        resp.raise_for_status()

        data: dict[str, Any] = resp.json()
        elements: list[dict[str, Any]] = data.get("elements", [])

        leads: list[GeoLead] = []
        for element in elements:
            lead = self._parse_element(element, city=city)
            if lead is not None:
                leads.append(lead)

        logger.info(
            "overpass_results",
            raw_elements=len(elements),
            leads_found=len(leads),
            city=city,
        )
        return leads

    # ------------------------------------------------------------------
    # Query building
    # ------------------------------------------------------------------

    def _build_query(self, bbox: BoundingBox) -> str:
        """Build Overpass QL query for businesses in *bbox*.

        The query selects nodes with ``amenity`` or ``shop`` tags that
        also have a ``name`` tag. Filtering out nodes *with* a website
        is done in Python (``_parse_element``) because Overpass
        negative-regex filters can be unreliable across tag variants.
        """
        amenity_filter = "|".join(BUSINESS_AMENITIES)
        shop_filter = "|".join(BUSINESS_SHOPS)
        # Overpass bbox order: south, west, north, east
        bb = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        return (
            f"[out:json][timeout:25];\n"
            f"(\n"
            f'  node["amenity"~"{amenity_filter}"]["name"]({bb});\n'
            f'  node["shop"~"{shop_filter}"]["name"]({bb});\n'
            f");\n"
            f"out body;\n"
        )

    # ------------------------------------------------------------------
    # Element parsing
    # ------------------------------------------------------------------

    def _parse_element(
        self,
        element: dict[str, Any],
        *,
        city: str | None = None,
    ) -> GeoLead | None:
        """Parse an Overpass JSON element into a ``GeoLead``.

        Returns ``None`` if the element already has a website
        (we only want businesses WITHOUT websites) or lacks a name.
        """
        tags: dict[str, str] = element.get("tags", {})

        # Skip if business already has a website.
        if tags.get("website") or tags.get("contact:website"):
            return None

        name = tags.get("name", "")
        if not name:
            return None

        # Determine category from amenity or shop tag.
        category = tags.get("amenity") or tags.get("shop") or "unknown"

        # Build address from addr:* tags.
        addr_parts: list[str] = []
        for key in ("addr:street", "addr:housenumber"):
            if val := tags.get(key):
                addr_parts.append(val)
        address = ", ".join(addr_parts) if addr_parts else None

        lat: float = element.get("lat", 0.0)
        lon: float = element.get("lon", 0.0)

        h3_idx = h3.latlng_to_cell(lat, lon, 8)

        return GeoLead(
            osm_id=element["id"],
            name=name,
            category=category,
            lat=lat,
            lon=lon,
            address=address,
            city=city or tags.get("addr:city"),
            phone=tags.get("phone") or tags.get("contact:phone"),
            h3_index=h3_idx,
            raw_tags=tags,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
