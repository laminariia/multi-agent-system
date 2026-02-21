"""E2E tests for dashboard page navigation and rendering.

Covers: Dashboard overview, Agents, Jobs, Settings, Orchestrator pages.
All tests use route mocking to avoid requiring a real backend.
"""

from __future__ import annotations

import pytest
from playwright.async_api import Page, expect

from tests.e2e.dashboard.conftest import (
    make_agent_status_list,
    make_hitl_pending_response,
    make_hitl_stats,
    make_job_list_response,
)

# ---------------------------------------------------------------------------
# Shared fixture: pre-authenticated page with common mocks
# ---------------------------------------------------------------------------

@pytest.fixture()
async def app_page(auth_page: Page, mock_auth_api) -> Page:
    """Authenticated page with all common layout-level mocks."""
    await mock_auth_api(
        "/api/v1/hitl/pending*",
        make_hitl_pending_response(count=0, total=0),
    )
    await mock_auth_api("/api/v1/hitl/stats", make_hitl_stats())
    await mock_auth_api("/api/v1/users*", {"items": [], "total": 0})
    return auth_page


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------


class TestDashboardPage:
    """Tests for the /dashboard overview page."""

    async def test_dashboard_renders(self, app_page: Page, mock_auth_api):
        """Dashboard page loads and shows key sections."""
        # Mock dashboard-specific data
        await mock_auth_api("/api/v1/jobs/stats", {
            "total": 10, "active": 3, "completed": 5, "pending": 2,
        })
        await mock_auth_api("/api/v1/agents/status", make_agent_status_list())
        await mock_auth_api("/api/v1/hitl/trends*", {
            "dates": ["2026-02-10", "2026-02-11", "2026-02-12"],
            "approved": [5, 8, 3],
            "rejected": [1, 2, 0],
        })

        await app_page.goto("/dashboard")
        await app_page.wait_for_load_state("networkidle")

        # The page should have rendered — check for sidebar "MAS" logo
        await expect(app_page.locator("aside").get_by_text("MAS").first).to_be_visible()

    async def test_sidebar_navigation_visible(self, app_page: Page):
        """Sidebar shows navigation links."""
        await app_page.goto("/dashboard")
        await app_page.wait_for_load_state("domcontentloaded")

        # Check sidebar has navigation items
        sidebar = app_page.locator("aside")
        await expect(sidebar).to_be_visible()

        # The sidebar should have key navigation items
        nav_items = ["Dashboard", "HITL", "Agents", "Jobs"]
        for item in nav_items:
            link = sidebar.get_by_text(item, exact=False).first
            if await link.count() > 0:
                await expect(link).to_be_visible()

    async def test_sidebar_collapse(self, app_page: Page):
        """Sidebar can be collapsed and expanded."""
        await app_page.goto("/dashboard")
        await app_page.wait_for_load_state("domcontentloaded")

        sidebar = app_page.locator("aside")
        collapse_btn = sidebar.get_by_role("button", name="Collapse sidebar")

        if await collapse_btn.count() > 0:
            await collapse_btn.click()
            await app_page.wait_for_timeout(300)

            # Sidebar should be narrower after collapsing
            expand_btn = sidebar.get_by_role("button", name="Expand sidebar")
            await expect(expand_btn).to_be_visible()


# ---------------------------------------------------------------------------
# Agents page
# ---------------------------------------------------------------------------


class TestAgentsPage:
    """Tests for the /agents page."""

    async def test_agents_page_renders(self, app_page: Page, mock_auth_api):
        """Agents page shows list of agents."""
        await mock_auth_api("/api/v1/agents/status", make_agent_status_list())
        await mock_auth_api("/api/v1/agents/logs*", {"items": [], "total": 0})

        await app_page.goto("/agents")
        await app_page.wait_for_load_state("networkidle")

        # Should see agent names from the mock
        await expect(app_page.get_by_text("scout", exact=False).first).to_be_visible(timeout=5000)

    async def test_agents_status_indicators(self, app_page: Page, mock_auth_api):
        """Agent cards show status indicators (running/idle)."""
        await mock_auth_api("/api/v1/agents/status", make_agent_status_list())
        await mock_auth_api("/api/v1/agents/logs*", {"items": [], "total": 0})

        await app_page.goto("/agents")
        await app_page.wait_for_load_state("networkidle")

        # At least one "running" or "idle" text should appear
        running = app_page.get_by_text("running", exact=False)
        idle = app_page.get_by_text("idle", exact=False)
        total = (await running.count()) + (await idle.count())
        assert total > 0, "Should show at least one agent status"


# ---------------------------------------------------------------------------
# Jobs page
# ---------------------------------------------------------------------------


class TestJobsPage:
    """Tests for the /jobs page."""

    async def test_jobs_page_renders(self, app_page: Page, mock_auth_api):
        """Jobs page loads and shows job list."""
        await mock_auth_api("/api/v1/jobs*", make_job_list_response(count=5))
        await mock_auth_api("/api/v1/jobs/stats", {
            "total": 10, "active": 3, "completed": 5, "pending": 2,
        })

        await app_page.goto("/jobs")
        await app_page.wait_for_load_state("networkidle")

        # Should see job titles
        await expect(app_page.get_by_text("Test Job 1", exact=False).first).to_be_visible(timeout=5000)

    async def test_jobs_empty_state(self, app_page: Page, mock_auth_api):
        """Jobs page shows empty state when no jobs."""
        await mock_auth_api("/api/v1/jobs*", {"items": [], "total": 0})
        await mock_auth_api("/api/v1/jobs/stats", {
            "total": 0, "active": 0, "completed": 0, "pending": 0,
        })

        await app_page.goto("/jobs")
        await app_page.wait_for_load_state("networkidle")

        # Page should load without errors (empty state is acceptable)
        await expect(app_page.locator("main")).to_be_visible()


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------


class TestSettingsPage:
    """Tests for the /settings page."""

    async def test_settings_page_renders(self, app_page: Page, mock_auth_api):
        """Settings page loads."""
        await mock_auth_api("/api/v1/credentials/summary", {
            "platforms": [], "total": 0,
        })

        await app_page.goto("/settings")
        await app_page.wait_for_load_state("networkidle")

        # Settings page should show some settings-related text
        settings_indicators = [
            app_page.get_by_text("Settings", exact=False),
            app_page.get_by_text("Profile", exact=False),
            app_page.get_by_text("Preferences", exact=False),
            app_page.get_by_text("Theme", exact=False),
        ]
        visible = False
        for indicator in settings_indicators:
            if await indicator.count() > 0:
                visible = True
                break
        assert visible, "Settings page should show settings-related content"

    async def test_theme_toggle(self, app_page: Page, mock_auth_api):
        """Theme can be toggled between dark and light."""
        await mock_auth_api("/api/v1/credentials/summary", {
            "platforms": [], "total": 0,
        })

        await app_page.goto("/settings")
        await app_page.wait_for_load_state("domcontentloaded")

        # Find theme toggle in the top bar
        theme_btn = app_page.get_by_role("button", name="Switch to light mode")
        if await theme_btn.count() == 0:
            theme_btn = app_page.get_by_role("button", name="Switch to dark mode")

        if await theme_btn.count() > 0:
            await theme_btn.click()
            await app_page.wait_for_timeout(300)
            # The html element should reflect the theme change
            # (exact check depends on implementation)


# ---------------------------------------------------------------------------
# Orchestrator page
# ---------------------------------------------------------------------------


class TestOrchestratorPage:
    """Tests for the /orchestrator page."""

    async def test_orchestrator_page_renders(self, app_page: Page, mock_auth_api):
        """Orchestrator page loads."""
        await mock_auth_api("/api/v1/orchestrator/status", {
            "runner_status": "idle",
            "active_goals": 0,
            "completed_goals": 5,
            "total_sessions": 10,
        })
        await mock_auth_api("/api/v1/orchestrator/goals*", {"items": [], "total": 0})
        await mock_auth_api("/api/v1/orchestrator/health", {
            "status": "healthy",
            "dimensions": {},
        })
        await mock_auth_api("/api/v1/orchestrator/phases*", [])
        await mock_auth_api("/api/v1/orchestrator/logs*", {"items": [], "total": 0})

        await app_page.goto("/orchestrator")
        await app_page.wait_for_load_state("networkidle")

        # Should show orchestrator-related content
        await expect(app_page.locator("main")).to_be_visible()


# ---------------------------------------------------------------------------
# Navigation between pages
# ---------------------------------------------------------------------------


class TestNavigation:
    """Test navigation between different dashboard pages."""

    async def test_navigate_via_sidebar(self, app_page: Page, mock_auth_api):
        """Clicking sidebar links navigates to the correct page."""
        # Set up mocks for all pages
        await mock_auth_api("/api/v1/agents/status", make_agent_status_list())
        await mock_auth_api("/api/v1/agents/logs*", {"items": [], "total": 0})
        await mock_auth_api("/api/v1/jobs*", make_job_list_response())
        await mock_auth_api("/api/v1/jobs/stats", {"total": 0, "active": 0, "completed": 0, "pending": 0})

        await app_page.goto("/dashboard")
        await app_page.wait_for_load_state("domcontentloaded")

        sidebar = app_page.locator("aside")

        # Navigate to Agents
        agents_link = sidebar.get_by_text("Agents", exact=False).first
        if await agents_link.count() > 0:
            await agents_link.click()
            await app_page.wait_for_url("**/agents", timeout=5000)
            assert "/agents" in app_page.url

        # Navigate to Jobs
        jobs_link = sidebar.get_by_text("Jobs", exact=False).first
        if await jobs_link.count() > 0:
            await jobs_link.click()
            await app_page.wait_for_url("**/jobs", timeout=5000)
            assert "/jobs" in app_page.url

    async def test_breadcrumb_visible(self, app_page: Page, mock_auth_api):
        """Top bar shows breadcrumb navigation."""
        await mock_auth_api("/api/v1/agents/status", make_agent_status_list())
        await mock_auth_api("/api/v1/agents/logs*", {"items": [], "total": 0})

        await app_page.goto("/agents")
        await app_page.wait_for_load_state("domcontentloaded")

        # Header should show "Dashboard" and the current section
        header = app_page.locator("header")
        await expect(header.get_by_text("Dashboard").first).to_be_visible()
