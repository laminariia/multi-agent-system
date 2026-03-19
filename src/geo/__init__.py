"""Geo-scanning utilities for Pipeline B (Outreach).

Provides H3 hexagonal tiling, OpenStreetMap Overpass queries,
Yandex Maps geocoding/search, and 2GIS business directory search
to discover local businesses without websites.
"""

from __future__ import annotations

from src.geo.dgis import DGisBusiness, DGisClient
from src.geo.h3_scanner import BoundingBox, generate_hexagons, geocode_city, hex_to_bbox
from src.geo.overpass import GeoLead, OverpassClient
from src.geo.yandex_maps import YandexBusiness, YandexMapsClient

__all__ = [
    "BoundingBox",
    "DGisBusiness",
    "DGisClient",
    "GeoLead",
    "OverpassClient",
    "YandexBusiness",
    "YandexMapsClient",
    "generate_hexagons",
    "geocode_city",
    "hex_to_bbox",
]
