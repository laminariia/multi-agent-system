"""H3 hexagonal grid scanner for geo-based business discovery.

Provides city geocoding via Nominatim and H3 hex-grid generation
for systematic scanning of geographic areas. Used by GeoScout agent
to partition cities into manageable hexagonal cells for Overpass queries.

Key functions:
- ``geocode_city`` -- resolve city name to a bounding box.
- ``generate_hexagons`` -- tile a bounding box with H3 hexagons.
- ``hex_to_bbox`` -- convert a single hex to its bounding box.
"""
from __future__ import annotations

from dataclasses import dataclass

import h3
import httpx
import structlog

logger = structlog.get_logger(__name__)

# Nominatim requires a descriptive User-Agent per their usage policy.
_NOMINATIM_USER_AGENT = "MultiAgentService/4.2 (geo-scanner; contact@example.com)"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass
class BoundingBox:
    """Geographic bounding box (south-west to north-east)."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def center(self) -> tuple[float, float]:
        """Return (lat, lon) of the box center."""
        return (
            (self.min_lat + self.max_lat) / 2.0,
            (self.min_lon + self.max_lon) / 2.0,
        )


async def geocode_city(city: str, *, timeout: float = 10.0) -> BoundingBox:
    """Geocode a city name to a bounding box using Nominatim.

    Uses the OpenStreetMap Nominatim API (free, no key required).
    A descriptive ``User-Agent`` header is sent per Nominatim TOS.

    Parameters
    ----------
    city:
        Human-readable city name (e.g. ``"Berlin"``).
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    BoundingBox:
        The geographic bounding box for the city.

    Raises
    ------
    ValueError:
        If the city cannot be resolved.
    httpx.HTTPError:
        On network or HTTP errors.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            _NOMINATIM_URL,
            params={"q": city, "format": "json", "limit": 1},
            headers={"User-Agent": _NOMINATIM_USER_AGENT},
        )
        resp.raise_for_status()

    results = resp.json()
    if not results:
        msg = f"City not found: {city!r}"
        raise ValueError(msg)

    # Nominatim ``boundingbox`` order: [south, north, west, east]
    raw_bb = results[0]["boundingbox"]
    bbox = BoundingBox(
        min_lat=float(raw_bb[0]),
        max_lat=float(raw_bb[1]),
        min_lon=float(raw_bb[2]),
        max_lon=float(raw_bb[3]),
    )

    logger.info(
        "geocoded_city",
        city=city,
        min_lat=bbox.min_lat,
        max_lat=bbox.max_lat,
        min_lon=bbox.min_lon,
        max_lon=bbox.max_lon,
    )
    return bbox


def generate_hexagons(bbox: BoundingBox, resolution: int = 8) -> list[str]:
    """Generate H3 hexagon IDs covering the bounding box.

    Uses the h3 v4 ``polygon_to_cells`` API to fill the bounding box
    with hexagons at the requested resolution.

    Parameters
    ----------
    bbox:
        Geographic bounding box to tile.
    resolution:
        H3 resolution level.  Default ``8`` gives ~0.74 km per hex
        (edge length ~461 m), which is suitable for city-level scanning.

    Returns
    -------
    list[str]:
        Sorted list of H3 cell index strings covering the area.
    """
    # h3 v4 API: LatLngPoly expects a list of (lat, lng) tuples as the outer ring.
    # We define the polygon as the four corners of the bounding box.
    outer_ring = [
        (bbox.min_lat, bbox.min_lon),
        (bbox.min_lat, bbox.max_lon),
        (bbox.max_lat, bbox.max_lon),
        (bbox.max_lat, bbox.min_lon),
    ]

    polygon = h3.LatLngPoly(outer_ring)
    cells = h3.polygon_to_cells(polygon, resolution)

    # Sort for deterministic ordering.
    result = sorted(cells)

    logger.info(
        "generated_hexagons",
        resolution=resolution,
        hex_count=len(result),
        bbox_center=bbox.center(),
    )
    return result


def hex_to_bbox(hex_id: str) -> BoundingBox:
    """Convert an H3 hex cell to its bounding box.

    Useful for building Overpass API queries scoped to a single hex.

    Parameters
    ----------
    hex_id:
        H3 cell index string.

    Returns
    -------
    BoundingBox:
        Tight bounding box around the hexagonal cell boundary.
    """
    boundary = h3.cell_to_boundary(hex_id)  # list of (lat, lng) tuples
    lats = [p[0] for p in boundary]
    lngs = [p[1] for p in boundary]
    return BoundingBox(
        min_lat=min(lats),
        max_lat=max(lats),
        min_lon=min(lngs),
        max_lon=max(lngs),
    )
