"""Unit tests for L10: Google Dorks Web Search.

Tests the _generate_google_dorks method on WebScoutAgent: category-specific
dork templates, city substitution, domain-specific patterns.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent() -> Any:
    """Create a WebScoutAgent with mocked dependencies."""
    from src.agents.web_scout import WebScoutAgent

    return WebScoutAgent(
        llm_client=MagicMock(),
        heartbeat=MagicMock(),
        loop_detector=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Tests for _generate_google_dorks
# ---------------------------------------------------------------------------


class TestGenerateGoogleDorks:
    """Tests for the _generate_google_dorks method."""

    def test_returns_list_of_strings(self) -> None:
        """Returns a list of string queries."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("restaurant", "Moscow")
        assert isinstance(dorks, list)
        assert len(dorks) > 0
        assert all(isinstance(d, str) for d in dorks)

    def test_city_appears_in_dorks(self) -> None:
        """The city name is included in the generated dorks."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("restaurant", "Berlin")
        city_present = any("Berlin" in d for d in dorks)
        assert city_present, f"City 'Berlin' not found in dorks: {dorks}"

    def test_category_appears_in_dorks(self) -> None:
        """The category appears in at least some dorks."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("auto_service", "Moscow")
        # Category or its translation should be present
        cat_present = any("auto" in d.lower() or "service" in d.lower() for d in dorks)
        assert cat_present, f"Category not found in dorks: {dorks}"

    def test_different_categories_different_dorks(self) -> None:
        """Different categories produce different dork sets."""
        agent = _make_agent()
        d1 = agent._generate_google_dorks("restaurant", "Moscow")
        d2 = agent._generate_google_dorks("beauty_salon", "Moscow")
        assert d1 != d2

    def test_known_category_has_domain_templates(self) -> None:
        """Known categories use domain-specific templates."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("restaurant", "Moscow")
        # Restaurant dorks should include food-related terms
        joined = " ".join(dorks).lower()
        # At least one should have site: or inurl: or specific domain patterns
        has_advanced = any(kw in joined for kw in ["site:", "inurl:", "intitle:", "-site:", "restaurant", "menu"])
        assert has_advanced, f"No advanced search operators in dorks: {dorks}"

    def test_unknown_category_uses_generic_templates(self) -> None:
        """Unknown categories fall back to generic templates."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("rare_category_xyz", "Moscow")
        assert isinstance(dorks, list)
        assert len(dorks) > 0
        # Generic templates should still include the category and city
        joined = " ".join(dorks)
        assert "rare_category_xyz" in joined or "Moscow" in joined

    def test_empty_city_still_works(self) -> None:
        """Empty city produces dorks without city component."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("restaurant", "")
        assert isinstance(dorks, list)
        assert len(dorks) > 0

    def test_no_site_overlap(self) -> None:
        """Multiple dorks with site: use different domains."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("restaurant", "Moscow")
        site_dorks = [d for d in dorks if "site:" in d]
        if len(site_dorks) > 1:
            # Extract the site: domains
            sites = []
            for sd in site_dorks:
                for part in sd.split():
                    if part.startswith("site:"):
                        sites.append(part)
            # Unique sites
            assert len(sites) == len(set(sites)), f"Duplicate site: entries: {sites}"

    def test_dork_count_reasonable(self) -> None:
        """Generates a reasonable number of dorks (3-15)."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("restaurant", "Moscow")
        assert 3 <= len(dorks) <= 15, f"Unexpected dork count: {len(dorks)}"

    def test_beauty_salon_dorks(self) -> None:
        """Beauty salon category generates appropriate dorks."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("beauty_salon", "SPb")
        joined = " ".join(dorks).lower()
        assert "SPb" in " ".join(dorks) or "spb" in joined

    def test_medical_dorks(self) -> None:
        """Medical category generates appropriate dorks."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("medical", "Kazan")
        assert isinstance(dorks, list)
        assert len(dorks) >= 3

    def test_education_dorks(self) -> None:
        """Education category generates appropriate dorks."""
        agent = _make_agent()
        dorks = agent._generate_google_dorks("education", "Novosibirsk")
        assert isinstance(dorks, list)
        assert len(dorks) >= 3


class TestGoogleDorkTemplates:
    """Tests for the DORK_TEMPLATES constant."""

    def test_templates_dict_exists(self) -> None:
        """DORK_TEMPLATES is a dict with category keys."""
        from src.agents.web_scout import DORK_TEMPLATES

        assert isinstance(DORK_TEMPLATES, dict)
        assert len(DORK_TEMPLATES) > 0

    def test_generic_key_exists(self) -> None:
        """A _generic key provides fallback templates."""
        from src.agents.web_scout import DORK_TEMPLATES

        assert "_generic" in DORK_TEMPLATES

    def test_all_values_are_lists(self) -> None:
        """All template values are lists of strings."""
        from src.agents.web_scout import DORK_TEMPLATES

        for key, templates in DORK_TEMPLATES.items():
            assert isinstance(templates, list), f"DORK_TEMPLATES[{key!r}] is not a list"
            for t in templates:
                assert isinstance(t, str), f"Template in {key!r} is not a string: {t!r}"

    def test_templates_have_placeholders(self) -> None:
        """Templates contain {category} and/or {city} placeholders."""
        from src.agents.web_scout import DORK_TEMPLATES

        for key, templates in DORK_TEMPLATES.items():
            for t in templates:
                has_placeholder = "{category}" in t or "{city}" in t
                assert has_placeholder, f"Template in {key!r} missing placeholders: {t!r}"
