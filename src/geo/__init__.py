"""Geo-scanning utilities for Pipeline B (Outreach).

Provides H3 hexagonal tiling and OpenStreetMap Overpass queries
to discover local businesses without websites.
"""
from __future__ import annotations

from src.geo.h3_scanner import BoundingBox, generate_hexagons, geocode_city, hex_to_bbox
from src.geo.overpass import GeoLead, OverpassClient

__all__ = [
    "BoundingBox",
    "GeoLead",
    "OverpassClient",
    "generate_hexagons",
    "geocode_city",
    "hex_to_bbox",
]
