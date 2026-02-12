"""Playwright E2E test fixtures for the MAS Dashboard.

Provides browser, authenticated page, and API mock fixtures.
Designed to run against a real or mock API backend.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.async_api import BrowserContext, Page, async_playwright


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:3000"
API_URL = "http://localhost:8000"

# Test user credentials — seeded via fixtures or the API
TEST_USER_EMAIL = "e2e-test@example.com"
TEST_USER_PASSWORD = "e2e-test-password-123"
TEST_USER_NAME = "E2E Test User"


# ---------------------------------------------------------------------------
# Fake auth state injected into localStorage to bypass real login flow
# ---------------------------------------------------------------------------

def _build_auth_storage(
    *,
    email: str = TEST_USER_EMAIL,
    name: str = TEST_USER_NAME,
    role: str = "owner",
    user_id: str = "e2e-user-001",
    access_token: str = "e2e-fake-access-token",
    refresh_token: str = "e2e-fake-refresh-token",
) -> dict[str, Any]:
    """Build the auth-storage object matching Zustand persist shape."""
    return {
        "state": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "user": {
                "id": user_id,
                "email": email,
                "name": name,
                "role": role,
                "status": "active",
            },
            "isAuthenticated": True,
        },
        "version": 0,
    }


# ---------------------------------------------------------------------------
# Browser / context fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
async def browser():
    """Launch a single Chromium browser for all tests in the session."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.fixture()
async def context(browser) -> BrowserContext:
    """Create a fresh browser context per test (clean cookies/storage)."""
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        base_url=BASE_URL,
    )
    ctx.set_default_timeout(10_000)
    ctx.set_default_navigation_timeout(15_000)
    yield ctx
    await ctx.close()


@pytest.fixture()
async def page(context: BrowserContext) -> Page:
    """A blank page — no authentication. Use for login/register tests."""
    pg = await context.new_page()
    yield pg
    await pg.close()


@pytest.fixture()
async def auth_page(context: BrowserContext) -> Page:
    """A page pre-authenticated via localStorage injection.

    Skips the login flow — goes directly to the dashboard.
    API calls will still need a mock or a real backend with
    matching JWT validation disabled / test token accepted.
    """
    pg = await context.new_page()

    # Navigate to the app first so localStorage is on the correct origin
    await pg.goto("/login")
    await pg.wait_for_load_state("domcontentloaded")

    # Inject auth state into localStorage
    auth_data = _build_auth_storage()
    await pg.evaluate(
        "data => localStorage.setItem('auth-storage', JSON.stringify(data))",
        auth_data,
    )

    yield pg
    await pg.close()


# ---------------------------------------------------------------------------
# API route mocking helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_api(page: Page):
    """Returns a helper to mock API responses via Playwright route interception.

    Usage:
        await mock_api("/api/v1/auth/login", {"access_token": "...", ...})
    """
    async def _mock(
        path: str,
        body: Any,
        status: int = 200,
        method: str | None = None,
    ):
        async def handler(route):
            if method and route.request.method.upper() != method.upper():
                await route.fallback()
                return
            await route.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps(body) if not isinstance(body, str) else body,
            )

        # Match both relative and absolute URL patterns
        await page.route(f"**{path}", handler)

    return _mock


@pytest.fixture()
def mock_auth_api(auth_page: Page):
    """Same as mock_api but bound to auth_page."""
    async def _mock(
        path: str,
        body: Any,
        status: int = 200,
        method: str | None = None,
    ):
        async def handler(route):
            if method and route.request.method.upper() != method.upper():
                await route.fallback()
                return
            await route.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps(body) if not isinstance(body, str) else body,
            )

        await auth_page.route(f"**{path}", handler)

    return _mock


# ---------------------------------------------------------------------------
# Common mock data factories
# ---------------------------------------------------------------------------

def make_login_response(
    email: str = TEST_USER_EMAIL,
    name: str = TEST_USER_NAME,
    role: str = "owner",
) -> dict[str, Any]:
    return {
        "access_token": "e2e-jwt-access-token",
        "refresh_token": "e2e-jwt-refresh-token",
        "token_type": "bearer",
        "user": {
            "id": "e2e-user-001",
            "email": email,
            "name": name,
            "role": role,
            "status": "active",
        },
    }


def make_hitl_pending_response(
    count: int = 3,
    total: int = 3,
) -> dict[str, Any]:
    items = []
    for i in range(count):
        items.append({
            "id": f"hitl-{i+1:03d}",
            "type": ["bid_approval", "code_review", "delivery"][i % 3],
            "title": f"Test HITL Item {i+1}",
            "description": f"Description for item {i+1}",
            "status": "pending",
            "priority": "normal",
            "created_at": "2026-02-12T10:00:00Z",
            "agent": "bid",
            "project_id": f"proj-{i+1:03d}",
        })
    return {
        "items": items,
        "total": total,
        "pending_urgent": 0,
    }


def make_hitl_resolve_response() -> dict[str, Any]:
    return {
        "id": "hitl-001",
        "status": "resolved",
        "next_action": "resume_pipeline",
    }


def make_agent_status_list() -> list[dict[str, Any]]:
    agents = ["scout", "bid", "planner", "dev", "content", "design", "critic", "packager", "geo_scout", "outreach"]
    return [
        {
            "name": name,
            "status": "running" if i < 5 else "idle",
            "last_heartbeat": "2026-02-12T12:00:00Z",
            "current_task": f"Processing job {i}" if i < 5 else None,
            "uptime_seconds": 3600 + i * 100,
        }
        for i, name in enumerate(agents)
    ]


def make_job_list_response(count: int = 5) -> dict[str, Any]:
    items = []
    for i in range(count):
        items.append({
            "id": f"job-{i+1:03d}",
            "title": f"Test Job {i+1}",
            "platform": ["freelancer", "upwork", "fl_ru"][i % 3],
            "status": ["active", "completed", "pending"][i % 3],
            "budget": 500 + i * 200,
            "created_at": "2026-02-12T08:00:00Z",
        })
    return {"items": items, "total": count}


def make_hitl_stats() -> dict[str, Any]:
    return {
        "total": 25,
        "pending": 3,
        "approved": 15,
        "rejected": 5,
        "modified": 2,
        "avg_resolution_time_seconds": 120,
    }
