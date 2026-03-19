"""WebScout Agent -- discovers businesses without websites via nationwide web search.

Searches multiple data sources across Russia (DuckDuckGo, 2GIS, Yandex Business,
Google Maps, VK Business, Instagram) to find businesses that lack websites or have
poor digital presence. Results are normalized, deduplicated, qualified via LLM, and
stored as Leads in PostgreSQL.

DuckDuckGo HTML search is the primary live source (no API key required).
Other sources (2GIS, Yandex Business, Google Maps) are stub-ready for future
API integration.

Pipeline B flow: WebScout -> Outreach -> [HITL] -> SalesAgent
Spec: docs/Full_work/pipeline-b-spec.md (Phase 1B: Web Search)
LLM: Gemini 2.5 Flash (Tier 5: Extraction).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import and_, select

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.exceptions import LLMException
from src.core.heartbeat import HeartbeatMonitor
from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import Lead
from src.core.state import AgentState, update_state
from src.prompts.web_scout import QUALIFICATION_TEMPLATE, WEB_SCOUT_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# DuckDuckGo is live; other sources remain stubs pending API integration.
logger.info(
    "webscout_sources_status",
    msg="DuckDuckGo HTML search is live. "
    "Other sources (2gis, yandex, google_maps, vk, instagram) are stubs "
    "pending API integration.",
)

# Tools that the WebScout Agent is allowed to invoke.
WEBSCOUT_ALLOWED_TOOLS: list[str] = [
    "search_duckduckgo",
    "search_2gis",
    "search_yandex",
    "search_vk",
    "search_google",
    "search_google_maps",
    "search_instagram",
]

# Maximum leads to evaluate in one LLM call to avoid context-window overflow.
_MAX_LEADS_PER_BATCH = 10

# Maximum name length for storage.
_MAX_NAME_LENGTH = 255

# DuckDuckGo HTML search configuration.
_DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
_DUCKDUCKGO_TIMEOUT_SECONDS = 15
_DUCKDUCKGO_MAX_RESULTS = 20

# Regex patterns for parsing DuckDuckGo HTML results.
_DDG_RESULT_RE_PATTERN = r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
_DDG_SNIPPET_RE_PATTERN = r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)'

# Phone pattern for extracting phone numbers from snippets.
_PHONE_RE_PATTERN = r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"

# ---------------------------------------------------------------------------
# Google Dork templates (L10)
# ---------------------------------------------------------------------------

DORK_TEMPLATES: dict[str, list[str]] = {
    "restaurant": [
        'intitle:"{category}" "{city}" -site:tripadvisor.com -site:yelp.com',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "menu" "delivery" -site:uber.com',
        '"{category}" "{city}" "reservations" "phone"',
        'inurl:cafe "{city}" -site:booking.com',
    ],
    "beauty_salon": [
        'intitle:"{category}" "{city}" "appointments"',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "manicure" OR "haircut" OR "spa"',
        '"{category}" "{city}" "price list" "phone"',
        'inurl:salon "{city}" -site:yelp.com',
    ],
    "auto_service": [
        'intitle:"{category}" "{city}" "repair"',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "oil change" OR "tire" OR "diagnostic"',
        '"{category}" "{city}" "service center" "phone"',
        'inurl:auto "{city}" "service"',
    ],
    "medical": [
        'intitle:"{category}" "{city}" "clinic" OR "doctor"',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "appointment" "consultation"',
        '"{category}" "{city}" "dentist" OR "therapist" OR "pediatrician"',
        'inurl:med "{city}" "clinic"',
    ],
    "education": [
        'intitle:"{category}" "{city}" "courses" OR "training"',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "tutoring" OR "school" OR "lessons"',
        '"{category}" "{city}" "enrollment" "phone"',
        'inurl:edu "{city}" "courses"',
    ],
    "fitness": [
        'intitle:"{category}" "{city}" "gym" OR "fitness"',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "membership" "trainer"',
        '"{category}" "{city}" "yoga" OR "crossfit" OR "pool"',
    ],
    "_generic": [
        'intitle:"{category}" "{city}"',
        'site:2gis.ru "{category}" "{city}"',
        'site:yandex.ru/maps "{category}" "{city}"',
        '"{category}" "{city}" "phone" "address"',
        '"{category}" "{city}" -site:wikipedia.org -site:facebook.com',
        'inurl:"{category}" "{city}"',
    ],
}


class WebScoutAgent(ConstrainedAgent):
    """Discovers businesses without websites via multi-source web search.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Gemini 2.5 Flash).
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
    ) -> None:
        super().__init__(
            agent_name="webscout",
            allowed_tools=WEBSCOUT_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Run the full web scout pipeline: fetch -> normalize -> dedup -> qualify -> store."""
        self._log.info("webscout_execute_start", thread_id=state["thread_id"])

        # 1. Extract scan parameters from state.
        #    Supports both _web_scout_params (full config) and _scan_city (simple city+category).
        artifacts = state.get("artifacts") or {}
        params = artifacts.get("_web_scout_params")

        # Fallback: build params from _scan_city + project requirements.
        if not params:
            scan_city = artifacts.get("_scan_city")
            if scan_city:
                # Derive category from project requirements if available.
                project = state.get("project") or {}
                requirements = project.get("requirements", "")
                category = self._extract_category_from_requirements(requirements)
                params = {
                    "categories": [category] if category else ["_generic"],
                    "region": scan_city,
                    "max_results_per_source": 20,
                }
                self._log.info(
                    "webscout_params_from_scan_city",
                    city=scan_city,
                    category=category,
                )

        if not params:
            self._log.error("webscout_no_params")
            return update_state(
                state,
                status="failed",
                errors=[*state["errors"], "No scan parameters specified for web scout"],
                current_agent="webscout",
                next_agent=None,
            )

        categories = params.get("categories", [])
        if not categories:
            self._log.error("webscout_no_categories")
            return update_state(
                state,
                status="failed",
                errors=[*state["errors"], "No categories specified for web scout"],
                current_agent="webscout",
                next_agent=None,
            )

        region = params.get("region", "Россия")
        max_results = params.get("max_results_per_source", 50)

        try:
            # 2. Fetch from all sources in parallel.
            raw_leads = await self._fetch_all_sources(categories, region, max_results)
            self._log.info("webscout_fetched", total=len(raw_leads))

            if not raw_leads:
                self._log.info("webscout_no_leads_found")
                return update_state(
                    state,
                    current_agent="webscout",
                    next_agent=None,
                    status="active",
                )

            # 3. Deduplicate across sources.
            deduped = self._deduplicate_leads(raw_leads)
            self._log.info(
                "webscout_dedup",
                before=len(raw_leads),
                after=len(deduped),
            )

            # 4. LLM qualification.
            qualified = await self._qualify_leads(deduped)
            qualified_leads = [q for q in qualified if q.get("qualified")]
            self._log.info(
                "webscout_qualified",
                total=len(qualified),
                qualified=len(qualified_leads),
            )

            # 5. Store qualified leads in DB.
            stored_count = 0
            if qualified_leads:
                stored_count = await self._store_leads(qualified_leads)

            # 6. Build updated state.
            artifacts = dict(state.get("artifacts") or {})
            scan_summary = {
                "total_fetched": len(raw_leads),
                "deduped_count": len(deduped),
                "qualified_count": len(qualified_leads),
                "stored_count": stored_count,
                "categories": categories,
                "region": region,
            }
            artifacts["web_scout_leads"] = scan_summary
            # Also store under _web_scan_results for downstream consumers.
            artifacts["_web_scan_results"] = [
                {
                    "business_name": lead.get("business_name"),
                    "city": lead.get("city"),
                    "category": lead.get("category"),
                    "phone": lead.get("phone"),
                    "has_website": lead.get("has_website", False),
                    "qualification_score": lead.get("qualification_score", 0),
                    "suggested_service": lead.get("suggested_service"),
                }
                for lead in qualified_leads
            ]

            next_agent = "outreach" if stored_count > 0 else None
            return update_state(
                state,
                current_agent="webscout",
                next_agent=next_agent,
                artifacts=artifacts,
                status="active",
            )

        except Exception as exc:  # noqa: BLE001 -- agent must not crash
            self._log.exception("webscout_error", error=str(exc))
            return update_state(
                state,
                status="failed",
                errors=[*state["errors"], f"Web scout error: {exc}"],
                current_agent="webscout",
                next_agent=None,
            )

    # ------------------------------------------------------------------
    # Source fetching (all sources in parallel)
    # ------------------------------------------------------------------

    async def _fetch_all_sources(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Fetch leads from all data sources in parallel, normalizing each.

        DuckDuckGo is the primary live source. Other sources are stubs
        pending API integration.
        """
        tasks = [
            asyncio.create_task(
                self._safe_fetch("duckduckgo", self._search_duckduckgo, categories, region, max_results)
            ),
            asyncio.create_task(self._safe_fetch("2gis", self._search_2gis, categories, region, max_results)),
            asyncio.create_task(
                self._safe_fetch("yandex", self._search_yandex_business, categories, region, max_results)
            ),
            asyncio.create_task(
                self._safe_fetch("google_maps", self._search_google_maps, categories, region, max_results)
            ),
            asyncio.create_task(self._safe_fetch("vk", self._fetch_vk, categories, region, max_results)),
            asyncio.create_task(self._safe_fetch("instagram", self._fetch_instagram, categories, region, max_results)),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_leads: list[dict[str, Any]] = []
        source_names = ["duckduckgo", "2gis", "yandex", "google_maps", "vk", "instagram"]
        for idx, result in enumerate(results):
            source = source_names[idx] if idx < len(source_names) else "unknown"
            if isinstance(result, Exception):
                self._log.error("source_fetch_failed", source=source, error=str(result))
                continue
            if isinstance(result, list):
                # Normalize each raw lead from this source.
                for raw_lead in result:
                    normalized = self._normalize_lead(raw_lead, source=source)
                    all_leads.append(normalized)

        return all_leads

    async def _safe_fetch(
        self,
        source: str,
        fetch_fn: Any,
        *args: Any,
    ) -> list[dict[str, Any]]:
        """Call a source fetch function, catching exceptions."""
        try:
            return await fetch_fn(*args)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("source_error", source=source, error=str(exc))
            return []

    # ------------------------------------------------------------------
    # DuckDuckGo HTML search (live source -- no API key required)
    # ------------------------------------------------------------------

    async def _search_duckduckgo(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Search DuckDuckGo HTML for businesses matching categories in a region.

        Builds queries like ``"restaurant Москва без сайта"`` and parses the
        HTML results page.  Extracts business name, URL, snippet, city, and
        phone (when visible in snippet).

        Args:
            categories: Business categories to search for.
            region: City or region name for geo-targeting.
            max_results: Maximum results per category query.

        Returns:
            List of raw result dicts with keys: ``name``, ``city``,
            ``category``, ``link``, ``snippet``, ``phone``, ``website``,
            ``has_website``.
        """

        all_results: list[dict[str, Any]] = []
        effective_max = min(max_results, _DUCKDUCKGO_MAX_RESULTS)

        for category in categories:
            query = f"{category} {region} без сайта"
            self._log.info(
                "duckduckgo_search",
                query=query,
                max_results=effective_max,
            )

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(_DUCKDUCKGO_TIMEOUT_SECONDS),
                    follow_redirects=True,
                ) as client:
                    resp = await client.post(
                        _DUCKDUCKGO_URL,
                        data={"q": query, "b": ""},
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/131.0.0.0 Safari/537.36"
                            ),
                            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
                        },
                    )
                    resp.raise_for_status()
                    html = resp.text

            except httpx.TimeoutException:
                self._log.warning("duckduckgo_timeout", query=query)
                continue
            except httpx.HTTPStatusError as exc:
                self._log.warning(
                    "duckduckgo_http_error",
                    query=query,
                    status=exc.response.status_code,
                )
                continue
            except httpx.HTTPError as exc:
                self._log.warning("duckduckgo_error", query=query, error=str(exc))
                continue

            # Parse HTML results.
            results = self._parse_duckduckgo_html(html, category, region)
            all_results.extend(results[:effective_max])

        return all_results

    def _parse_duckduckgo_html(
        self,
        html: str,
        category: str,
        city: str,
    ) -> list[dict[str, Any]]:
        """Parse DuckDuckGo HTML response into structured business results.

        Extracts links, titles, and snippets from the search results page.
        Attempts to detect phone numbers and website presence from snippets.

        Args:
            html: Raw HTML from DuckDuckGo.
            category: The category used in the search query.
            city: The city/region used in the search query.

        Returns:
            List of parsed result dicts.
        """

        results: list[dict[str, Any]] = []

        # Extract result links and titles.
        link_matches = re.findall(_DDG_RESULT_RE_PATTERN, html, re.DOTALL)

        # Extract snippets.
        snippet_matches = re.findall(_DDG_SNIPPET_RE_PATTERN, html, re.DOTALL)

        for idx, (url, raw_title) in enumerate(link_matches):
            # Clean HTML tags from title.
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            if not title:
                continue

            # Get corresponding snippet if available.
            snippet = ""
            if idx < len(snippet_matches):
                snippet = re.sub(r"<[^>]+>", "", snippet_matches[idx]).strip()

            # Try to extract phone from snippet.
            phone = None
            phone_match = re.search(_PHONE_RE_PATTERN, snippet)
            if phone_match:
                phone = phone_match.group(0)

            # Determine if result looks like a business website.
            has_website = bool(url) and not any(
                domain in url for domain in ["2gis.ru", "yandex.ru/maps", "google.com/maps", "vk.com", "instagram.com"]
            )

            results.append(
                {
                    "name": title,
                    "city": city,
                    "category": category,
                    "link": url,
                    "snippet": snippet,
                    "phone": phone,
                    "website": url if has_website else None,
                    "has_website": has_website,
                }
            )

        return results

    # ------------------------------------------------------------------
    # Stub sources (interface ready for future API integration)
    # ------------------------------------------------------------------

    async def _search_2gis(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Search 2GIS for business listings.

        Stub -- returns empty list. 2GIS API integration is a separate task
        (Phase 3M). Interface accepts categories and region for future use.
        """
        self._log.info("search_2gis_stub", categories=categories, region=region)
        return []

    async def _search_yandex_business(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Search Yandex Business directory for listings.

        Stub -- returns empty list. Yandex Business API not configured.
        Interface accepts categories and region for future use.
        """
        self._log.info("search_yandex_business_stub", categories=categories, region=region)
        return []

    async def _search_google_maps(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Search Google Maps / Places API for businesses.

        Stub -- returns empty list. Google Maps API not configured.
        Interface accepts categories and region for future use.
        """
        self._log.info("search_google_maps_stub", categories=categories, region=region)
        return []

    async def _fetch_vk(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Fetch business communities from VK API.

        Stub -- returns empty list. VK API integration is a separate task.
        """
        self._log.info("fetch_vk_stub", categories=categories, region=region)
        return []

    async def _fetch_instagram(
        self,
        categories: list[str],
        region: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Fetch business accounts from Instagram.

        Stub -- returns empty list. Requires Playwright stealth integration.
        """
        self._log.info("fetch_instagram_stub", categories=categories, region=region)
        return []

    # ------------------------------------------------------------------
    # Category extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_category_from_requirements(requirements: str) -> str | None:
        """Extract a business category from free-text project requirements.

        Performs simple keyword matching against known high-value categories.
        Returns the first match or ``None`` if no category is detected.

        Args:
            requirements: Free-text project requirements string.

        Returns:
            Category string (e.g. ``"restaurant"``) or ``None``.
        """
        if not requirements:
            return None

        text = requirements.lower()
        # Map of keywords to category identifiers.
        category_keywords: dict[str, list[str]] = {
            "restaurant": ["ресторан", "restaurant", "кафе", "cafe", "кофейн"],
            "beauty_salon": ["салон красоты", "beauty", "парикмахер", "маникюр", "spa", "спа"],
            "auto_service": ["автосервис", "auto service", "автомастерск", "шиномонтаж", "сто"],
            "medical": ["клиника", "clinic", "стоматолог", "dentist", "врач", "doctor", "медицинск"],
            "education": ["школа", "курсы", "courses", "обучение", "training", "репетитор"],
            "fitness": ["фитнес", "fitness", "тренажёрн", "gym", "спортзал", "йога", "yoga"],
        }
        for category, keywords in category_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    return category
        return None

    # ------------------------------------------------------------------
    # Lead normalization
    # ------------------------------------------------------------------

    def _normalize_lead(
        self,
        raw: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Normalize a raw lead from any source into a standard schema.

        Standard schema:
            source, source_id, business_name, city, category, address,
            phone, email, website, has_website, rating, review_count,
            lat, lon, vk_url, instagram_url, extra
        """
        # Extract common fields with fallbacks.
        name = raw.get("name") or raw.get("full_name") or raw.get("title") or "Unknown"
        name = str(name).strip()[:_MAX_NAME_LENGTH]

        source_id = str(raw.get("id") or raw.get("source_id") or raw.get("username") or "")

        website = raw.get("website") or raw.get("site") or None
        has_website = bool(website)

        phone = raw.get("phone") or None
        city = raw.get("city") or None
        category = raw.get("category") or None
        address = raw.get("address") or None
        email = raw.get("email") or None
        rating = raw.get("rating") or raw.get("google_rating") or None
        review_count = raw.get("review_count") or None
        lat = raw.get("lat") or raw.get("latitude") or None
        lon = raw.get("lon") or raw.get("longitude") or None

        # Source-specific social URLs.
        vk_url = None
        instagram_url = None

        if source == "vk":
            screen_name = raw.get("screen_name")
            if screen_name:
                vk_url = f"https://vk.com/{screen_name}"

        if source == "instagram":
            username = raw.get("username")
            if username:
                instagram_url = f"https://instagram.com/{username}"

        # Collect extra source-specific data.
        extra: dict[str, Any] = {}
        if raw.get("last_post_days") is not None:
            extra["last_post_days"] = raw["last_post_days"]
        if raw.get("members_count") is not None:
            extra["members_count"] = raw["members_count"]
        if raw.get("followers") is not None:
            extra["followers"] = raw["followers"]
        if raw.get("snippet"):
            extra["snippet"] = raw["snippet"]
        if raw.get("link"):
            extra["link"] = raw["link"]

        return {
            "source": source,
            "source_id": source_id,
            "business_name": name,
            "city": city,
            "category": category,
            "address": address,
            "phone": phone,
            "email": email,
            "website": website,
            "has_website": has_website,
            "rating": rating,
            "review_count": review_count,
            "lat": lat,
            "lon": lon,
            "vk_url": vk_url,
            "instagram_url": instagram_url,
            "extra": extra,
        }

    # ------------------------------------------------------------------
    # Deduplication across sources
    # ------------------------------------------------------------------

    def _deduplicate_leads(
        self,
        leads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Deduplicate leads using multiple keys: source+source_id, phone, name+city.

        First occurrence wins. Returns a new list without duplicates.
        """
        if not leads:
            return []

        seen_source_ids: set[str] = set()
        seen_phones: set[str] = set()
        seen_name_city: set[str] = set()
        deduped: list[dict[str, Any]] = []

        for lead in leads:
            # Key 1: source + source_id
            sid_key = f"{lead.get('source', '')}:{lead.get('source_id', '')}"
            if lead.get("source_id") and sid_key in seen_source_ids:
                continue

            # Key 2: phone (if present)
            phone = lead.get("phone")
            if phone and phone in seen_phones:
                continue

            # Key 3: name + city
            name = (lead.get("business_name") or "").strip().lower()
            city = (lead.get("city") or "").strip().lower()
            nc_key = f"{name}|{city}" if name else ""
            if nc_key and nc_key in seen_name_city:
                continue

            # Mark as seen.
            if lead.get("source_id"):
                seen_source_ids.add(sid_key)
            if phone:
                seen_phones.add(phone)
            if nc_key:
                seen_name_city.add(nc_key)

            deduped.append(lead)

        return deduped

    # ------------------------------------------------------------------
    # LLM qualification
    # ------------------------------------------------------------------

    async def _qualify_leads(
        self,
        leads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Score leads via LLM in batches, returning enriched lead dicts.

        Each lead gets: qualified (bool), qualification_score (float),
        reasoning (str), suggested_service (str).
        """
        if not leads:
            return []

        all_results: list[dict[str, Any]] = []

        for batch_start in range(0, len(leads), _MAX_LEADS_PER_BATCH):
            batch = leads[batch_start : batch_start + _MAX_LEADS_PER_BATCH]
            try:
                scored = await self._score_batch(batch)
                all_results.extend(scored)
            except (LLMException, KeyError, ValueError):
                self._log.exception(
                    "qualification_batch_failed",
                    batch_size=len(batch),
                )
                # Fallback: return leads with default qualification.
                for lead in batch:
                    lead["qualified"] = False
                    lead["qualification_score"] = 0.5
                    lead["reasoning"] = "qualification_failed"
                    lead["suggested_service"] = None
                all_results.extend(batch)

        return all_results

    async def _score_batch(
        self,
        batch: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Send a batch of leads to the LLM for qualification scoring."""
        leads_text = json.dumps(batch, indent=2, default=str, ensure_ascii=False)

        prompt = QUALIFICATION_TEMPLATE.format(
            count=len(batch),
            leads_json=leads_text,
        )

        messages = [
            SystemMessage(content=WEB_SCOUT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.2)
        raw_text = str(response_msg.content)

        return self._parse_qualification_response(raw_text, batch)

    def _parse_qualification_response(
        self,
        raw: str,
        original_leads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Parse LLM qualification response and merge with original lead data."""
        try:
            parsed = extract_json(raw, expected_type=list)
        except ValueError:
            try:
                parsed = extract_json(raw, expected_type=dict)
                parsed = [parsed]
            except ValueError:
                self._log.error("qualification_json_parse_error", raw_preview=raw[:300])
                # Return originals with fallback scores.
                for lead in original_leads:
                    lead["qualified"] = False
                    lead["qualification_score"] = 0.5
                    lead["reasoning"] = "parse_failed"
                    lead["suggested_service"] = None
                return original_leads

        if not isinstance(parsed, list):
            parsed = [parsed]

        # Build lookup by source_id for merging.
        scored_by_id: dict[str, dict[str, Any]] = {}
        for item in parsed:
            if isinstance(item, dict):
                sid = item.get("source_id", "")
                if sid:
                    scored_by_id[sid] = item

        # Merge qualification results back into original leads.
        results: list[dict[str, Any]] = []
        for lead in original_leads:
            sid = lead.get("source_id", "")
            scored = scored_by_id.get(sid, {})

            merged = {**lead}

            # Extract and validate qualification fields.
            try:
                merged["qualification_score"] = float(scored.get("qualification_score", 0.5))
            except (TypeError, ValueError):
                merged["qualification_score"] = 0.5

            merged["qualification_score"] = max(0.0, min(1.0, merged["qualification_score"]))
            merged["qualified"] = scored.get("qualified", merged["qualification_score"] >= 0.5)
            merged["reasoning"] = scored.get("reasoning", "")
            merged["suggested_service"] = scored.get("suggested_service")

            results.append(merged)

        return results

    # ------------------------------------------------------------------
    # Google Dorks generation (L10)
    # ------------------------------------------------------------------

    def _generate_google_dorks(
        self,
        category: str,
        city: str,
    ) -> list[str]:
        """Generate Google search dork queries for a category and city.

        Uses domain-specific templates for known categories and falls
        back to generic templates for unknown ones.  Each template
        contains ``{category}`` and/or ``{city}`` placeholders that
        are substituted with the provided values.

        Args:
            category: Business category (e.g. ``"restaurant"``, ``"auto_service"``).
            city: City name for geo-targeting (can be empty).

        Returns:
            A list of Google dork query strings ready for search API use.
        """
        # Pick category-specific or generic templates
        templates = DORK_TEMPLATES.get(category, DORK_TEMPLATES["_generic"])

        dorks: list[str] = []
        for template in templates:
            query = template.format(category=category, city=city)
            dorks.append(query)

        return dorks

    # ------------------------------------------------------------------
    # Database persistence
    # ------------------------------------------------------------------

    async def _store_leads(self, leads: list[dict[str, Any]]) -> int:
        """Store qualified leads in PostgreSQL, skipping duplicates.

        Deduplicates by (source, source_id) against existing leads.
        Returns the count of newly stored leads.
        """
        if not leads:
            return 0

        from decimal import Decimal  # noqa: PLC0415

        stored = 0
        async with get_db_session() as session:
            for lead_data in leads:
                source = lead_data.get("source", "web_search")
                source_id = lead_data.get("source_id", "")

                # Check for existing lead by source + source_id.
                existing = await session.execute(
                    select(Lead).where(
                        and_(
                            Lead.source == source,
                            Lead.source_id == source_id,
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                lead = Lead(
                    name=lead_data.get("business_name", "Unknown")[:_MAX_NAME_LENGTH],
                    category=lead_data.get("category"),
                    address=lead_data.get("address"),
                    city=lead_data.get("city"),
                    phone=lead_data.get("phone"),
                    email=lead_data.get("email"),
                    website=lead_data.get("website"),
                    source=lead_data.get("source", "web_search"),
                    source_id=source_id,
                    status="new",
                    social_links={
                        k: v
                        for k, v in {
                            "vk_url": lead_data.get("vk_url"),
                            "instagram_url": lead_data.get("instagram_url"),
                        }.items()
                        if v
                    }
                    or None,
                    enrichment_data={
                        "qualification_score": lead_data.get("qualification_score"),
                        "suggested_service": lead_data.get("suggested_service"),
                        "reasoning": lead_data.get("reasoning"),
                        "original_source": source,
                        "extra": lead_data.get("extra"),
                    },
                )

                # Set rating/review fields if available.
                if lead_data.get("rating") is not None:
                    try:
                        lead.google_rating = Decimal(str(lead_data["rating"]))
                    except (ValueError, TypeError):
                        pass
                if lead_data.get("review_count") is not None:
                    try:
                        lead.review_count = int(lead_data["review_count"])
                    except (ValueError, TypeError):
                        pass

                # Set coordinates if available.
                if lead_data.get("lat") is not None and lead_data.get("lon") is not None:
                    try:
                        lead.latitude = Decimal(str(lead_data["lat"]))
                        lead.longitude = Decimal(str(lead_data["lon"]))
                    except (ValueError, TypeError):
                        pass

                session.add(lead)
                stored += 1

            await session.commit()

        self._log.info("webscout_stored_leads", count=stored)
        return stored


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def web_scout_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node wrapper for the WebScout agent.

    Creates a :class:`WebScoutAgent` instance with shared dependencies
    from the DI container and runs it.  Uses ``dict[str, Any]`` signature
    to avoid LangGraph state reconstruction issues (see debugging.md).
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = WebScoutAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
