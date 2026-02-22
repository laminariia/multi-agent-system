"""Geo Scout Agent -- discovers offline businesses without websites.

Uses H3 hexagonal scanning + Overpass API to find businesses in a given city
that don't have a website. Results are stored as Leads in PostgreSQL.

Pipeline B flow: GeoScout -> Enrichment -> Outreach -> [HITL] -> Send
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.heartbeat import HeartbeatMonitor
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import Lead
from src.core.state import AgentState, update_state
from src.geo.h3_scanner import generate_hexagons, geocode_city, hex_to_bbox
from src.geo.overpass import GeoLead, OverpassClient

logger = structlog.get_logger(__name__)

# Tools that the GeoScout Agent is allowed to invoke.
GEOSCOUT_ALLOWED_TOOLS: list[str] = [
    "geocode_city",
    "scan_hexagons",
    "query_overpass",
]

# Maximum hexagons to scan per invocation (rate-limit protection).
_MAX_HEXAGONS_PER_SCAN = 50


class GeoScoutAgent(ConstrainedAgent):
    """Discovers offline businesses via H3 geo-scanning.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance.
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    overpass_client:
        :class:`OverpassClient` instance for OSM queries.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
        overpass_client: OverpassClient | None = None,
    ) -> None:
        super().__init__(
            agent_name="geoscout",
            allowed_tools=GEOSCOUT_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )
        self._overpass = overpass_client or OverpassClient()

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Scan a city for businesses without websites.

        Expects city name in ``state["artifacts"]["_scan_city"]`` or
        ``state["project"]["requirements"]``.
        """
        # Extract city from state
        city = (state.get("artifacts") or {}).get("_scan_city", "")
        if not city:
            city = (state.get("project") or {}).get("requirements", "")

        if not city:
            return update_state(
                state,
                status="failed",
                errors=[*state["errors"], "No city specified for geo scan"],
                next_agent=None,
            )

        self._log.info("geoscout_starting", city=city)

        try:
            # 1. Geocode city to bounding box.
            bbox = await geocode_city(city)
            self._log.info(
                "geoscout_geocoded",
                city=city,
                bbox=f"{bbox.min_lat},{bbox.min_lon} - {bbox.max_lat},{bbox.max_lon}",
            )

            # 2. Generate H3 hexagons covering the city.
            hexagons = generate_hexagons(bbox)
            total_hexagons = len(hexagons)

            # Limit hexagons per scan to avoid Overpass rate-limit issues.
            if len(hexagons) > _MAX_HEXAGONS_PER_SCAN:
                self._log.warning(
                    "geoscout_truncating_hexagons",
                    total=total_hexagons,
                    limit=_MAX_HEXAGONS_PER_SCAN,
                )
                hexagons = hexagons[:_MAX_HEXAGONS_PER_SCAN]

            self._log.info(
                "geoscout_scanning",
                hexagons=len(hexagons),
                total=total_hexagons,
            )

            # 3. Query Overpass for each hexagon.
            all_leads: list[GeoLead] = []
            seen_osm_ids: set[int] = set()

            for i, hex_id in enumerate(hexagons):
                hex_bbox = hex_to_bbox(hex_id)
                leads = await self._overpass.query_businesses(hex_bbox, city=city)

                # Deduplicate by OSM ID.
                for lead in leads:
                    if lead.osm_id not in seen_osm_ids:
                        seen_osm_ids.add(lead.osm_id)
                        all_leads.append(lead)

                if (i + 1) % 10 == 0:
                    self._log.info(
                        "geoscout_progress",
                        scanned=i + 1,
                        total=len(hexagons),
                        leads=len(all_leads),
                    )

            self._log.info(
                "geoscout_scan_complete",
                leads_found=len(all_leads),
                hexagons_scanned=len(hexagons),
            )

            # 4. Store leads in PostgreSQL.
            stored_count = await self._store_leads(all_leads, city)

            # 5. Update state and hand off to outreach.
            artifacts = dict(state.get("artifacts") or {})
            artifacts["_geo_scan_results"] = {
                "city": city,
                "hexagons_total": total_hexagons,
                "hexagons_scanned": len(hexagons),
                "leads_found": len(all_leads),
                "leads_stored": stored_count,
            }
            # Store lead OSM IDs for the outreach agent to process.
            artifacts["_lead_osm_ids"] = [lead.osm_id for lead in all_leads]

            return update_state(
                state,
                artifacts=artifacts,
                next_agent="outreach",
                current_agent="geoscout",
            )

        except ValueError as exc:
            return update_state(
                state,
                status="failed",
                errors=[*state["errors"], f"Geocoding failed: {exc}"],
                next_agent=None,
            )
        except Exception as exc:  # noqa: BLE001 — intentional: agent must not crash
            self._log.exception("geoscout_error", city=city, error=str(exc))
            return update_state(
                state,
                errors=[*state["errors"], f"Geo scan error: {exc}"],
                next_agent=None,
                status="failed",
            )

    # ------------------------------------------------------------------
    # Database persistence
    # ------------------------------------------------------------------

    async def _store_leads(self, leads: list[GeoLead], city: str) -> int:
        """Store discovered leads in PostgreSQL, skipping duplicates by osm_id."""
        stored = 0
        async with get_db_session() as session:
            for geo_lead in leads:
                # Check for existing lead by osm_id.
                existing = await session.execute(select(Lead).where(Lead.osm_id == geo_lead.osm_id))
                if existing.scalar_one_or_none():
                    continue

                lead = Lead(
                    name=geo_lead.name,
                    category=geo_lead.category,
                    address=geo_lead.address,
                    city=city,
                    country=None,  # Could be derived from city geocoding
                    latitude=Decimal(str(geo_lead.lat)),
                    longitude=Decimal(str(geo_lead.lon)),
                    h3_index=geo_lead.h3_index,
                    phone=geo_lead.phone,
                    osm_id=geo_lead.osm_id,
                    status="new",
                )
                session.add(lead)
                stored += 1

            await session.commit()

        self._log.info("geoscout_stored_leads", count=stored, city=city)
        return stored


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def geo_scout_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node wrapper for the GeoScout agent.

    Creates a :class:`GeoScoutAgent` instance with shared dependencies
    from the DI container and runs it.  Uses ``dict[str, Any]`` signature
    to avoid LangGraph state reconstruction issues (see MEMORY.md).
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = GeoScoutAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    try:
        result = await agent.invoke(state)
    finally:
        # Clean up Overpass HTTP client.
        try:
            await agent._overpass.close()  # noqa: SLF001
        except (OSError, ConnectionError):
            logger.debug("geo_scout_overpass_close_error", exc_info=True)

    return result
