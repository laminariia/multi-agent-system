"""E2E tests for authentication flows: login, register, logout, protected routes."""

from __future__ import annotations

import pytest
from playwright.async_api import Page, expect

from tests.e2e.dashboard.conftest import (
    TEST_USER_EMAIL,
    TEST_USER_PASSWORD,
    make_login_response,
)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    """Login page E2E tests."""

    async def test_login_page_renders(self, page: Page):
        """Login page shows form fields and sign-in button."""
        await page.goto("/login")

        await expect(page.get_by_role("heading", name="Sign in to your account")).to_be_visible()
        await expect(page.locator("#email")).to_be_visible()
        await expect(page.locator("#password")).to_be_visible()
        await expect(page.get_by_role("button", name="Sign In")).to_be_visible()

    async def test_login_success_redirects_to_dashboard(self, page: Page, mock_api):
        """Successful login redirects to /dashboard."""
        # Mock the login API
        await mock_api(
            "/api/v1/auth/login",
            make_login_response(),
            method="POST",
        )
        # Mock the HITL pending count (dashboard layout fetches it)
        await mock_api("/api/v1/hitl/pending*", {"items": [], "total": 0, "pending_urgent": 0})
        await mock_api("/api/v1/users*", {"items": [], "total": 0})

        await page.goto("/login")

        await page.locator("#email").fill(TEST_USER_EMAIL)
        await page.locator("#password").fill(TEST_USER_PASSWORD)
        await page.get_by_role("button", name="Sign In").click()

        # Should navigate to dashboard
        await page.wait_for_url("**/dashboard", timeout=5000)
        assert "/dashboard" in page.url

    async def test_login_failure_shows_error(self, page: Page, mock_api):
        """Failed login shows error message."""
        await mock_api(
            "/api/v1/auth/login",
            "Invalid email or password",
            status=401,
            method="POST",
        )

        await page.goto("/login")

        await page.locator("#email").fill("wrong@example.com")
        await page.locator("#password").fill("wrongpassword")
        await page.get_by_role("button", name="Sign In").click()

        # Error message should appear
        error = page.locator(".text-destructive")
        await expect(error).to_be_visible(timeout=5000)

    async def test_login_button_shows_loading(self, page: Page, mock_api):
        """Sign-in button shows loading state during submission."""
        # Use a delayed response to catch the loading state
        async def slow_handler(route):
            import asyncio
            await asyncio.sleep(1)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body='{"access_token":"t","refresh_token":"r","token_type":"bearer","user":{"id":"1","email":"a@b.com","name":"A","role":"owner","status":"active"}}',
            )

        await page.route("**/api/v1/auth/login", slow_handler)
        await mock_api("/api/v1/hitl/pending*", {"items": [], "total": 0, "pending_urgent": 0})
        await mock_api("/api/v1/users*", {"items": [], "total": 0})

        await page.goto("/login")

        await page.locator("#email").fill(TEST_USER_EMAIL)
        await page.locator("#password").fill(TEST_USER_PASSWORD)
        await page.get_by_role("button", name="Sign In").click()

        # Button should show "Signing in..." text
        await expect(page.get_by_text("Signing in...")).to_be_visible(timeout=2000)

    async def test_login_has_register_link(self, page: Page):
        """Login page has a link to registration."""
        await page.goto("/login")

        register_link = page.get_by_role("link", name="Register")
        await expect(register_link).to_be_visible()
        await register_link.click()
        await page.wait_for_url("**/register")
        assert "/register" in page.url


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


class TestRegister:
    """Registration page E2E tests."""

    async def test_register_page_renders(self, page: Page):
        """Register page shows all form fields."""
        await page.goto("/register")

        await expect(page.get_by_role("heading", name="Create your account")).to_be_visible()
        await expect(page.locator("#name")).to_be_visible()
        await expect(page.locator("#email")).to_be_visible()
        await expect(page.locator("#password")).to_be_visible()
        await expect(page.locator("#confirmPassword")).to_be_visible()
        await expect(page.get_by_role("button", name="Create Account")).to_be_visible()

    async def test_register_password_mismatch(self, page: Page):
        """Mismatched passwords show client-side error."""
        await page.goto("/register")

        await page.locator("#name").fill("Test")
        await page.locator("#email").fill("new@example.com")
        await page.locator("#password").fill("password123")
        await page.locator("#confirmPassword").fill("different123")
        await page.get_by_role("button", name="Create Account").click()

        error = page.locator(".text-destructive")
        await expect(error).to_be_visible()
        await expect(error).to_contain_text("Passwords do not match")

    async def test_register_short_password(self, page: Page):
        """Short password shows client-side error."""
        await page.goto("/register")

        await page.locator("#name").fill("Test")
        await page.locator("#email").fill("new@example.com")
        await page.locator("#password").fill("123")
        await page.locator("#confirmPassword").fill("123")
        await page.get_by_role("button", name="Create Account").click()

        error = page.locator(".text-destructive")
        await expect(error).to_be_visible()
        await expect(error).to_contain_text("at least 6 characters")

    async def test_register_success_auto_login(self, page: Page, mock_api):
        """First user registration returns tokens and auto-redirects to dashboard."""
        await mock_api(
            "/api/v1/auth/register",
            make_login_response(email="new@example.com", name="New User"),
            method="POST",
        )
        await mock_api("/api/v1/hitl/pending*", {"items": [], "total": 0, "pending_urgent": 0})
        await mock_api("/api/v1/users*", {"items": [], "total": 0})

        await page.goto("/register")

        await page.locator("#name").fill("New User")
        await page.locator("#email").fill("new@example.com")
        await page.locator("#password").fill("securepassword123")
        await page.locator("#confirmPassword").fill("securepassword123")
        await page.get_by_role("button", name="Create Account").click()

        await page.wait_for_url("**/dashboard", timeout=5000)
        assert "/dashboard" in page.url

    async def test_register_pending_approval(self, page: Page, mock_api):
        """Non-first registration returns pending approval notice."""
        await mock_api(
            "/api/v1/auth/register",
            {"status": "pending_approval", "message": "Account pending admin approval"},
            status=202,
            method="POST",
        )

        await page.goto("/register")

        await page.locator("#name").fill("Pending User")
        await page.locator("#email").fill("pending@example.com")
        await page.locator("#password").fill("securepassword123")
        await page.locator("#confirmPassword").fill("securepassword123")
        await page.get_by_role("button", name="Create Account").click()

        # Should show pending approval state
        await expect(page.get_by_text("Registration Submitted")).to_be_visible(timeout=5000)
        await expect(page.get_by_text("awaiting administrator approval")).to_be_visible()

    async def test_register_has_login_link(self, page: Page):
        """Register page has a link back to login."""
        await page.goto("/register")

        login_link = page.get_by_role("link", name="Sign in")
        await expect(login_link).to_be_visible()
        await login_link.click()
        await page.wait_for_url("**/login")
        assert "/login" in page.url


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    """Logout and session expiry tests."""

    async def test_logout_redirects_to_login(self, auth_page: Page, mock_auth_api):
        """Clicking sign-out clears session and redirects to login."""
        # Mock API calls that dashboard layout makes
        await mock_auth_api("/api/v1/hitl/pending*", {"items": [], "total": 0, "pending_urgent": 0})
        await mock_auth_api("/api/v1/users*", {"items": [], "total": 0})

        await auth_page.goto("/dashboard")
        await auth_page.wait_for_load_state("networkidle")

        # Open user dropdown and click sign out
        # The user avatar/dropdown trigger is at the bottom of the sidebar
        user_button = auth_page.locator("aside button").last
        await user_button.click()

        sign_out = auth_page.get_by_text("Sign out")
        await expect(sign_out).to_be_visible()
        await sign_out.click()

        await auth_page.wait_for_url("**/login", timeout=5000)
        assert "/login" in auth_page.url


# ---------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------


class TestProtectedRoutes:
    """Unauthenticated access should redirect to login."""

    @pytest.mark.parametrize(
        "path",
        [
            "/dashboard",
            "/hitl",
            "/agents",
            "/jobs",
            "/settings",
            "/orchestrator",
        ],
    )
    async def test_unauthenticated_redirect(self, page: Page, path: str):
        """Protected routes redirect to /login when not authenticated."""
        await page.goto(path)
        # The app layout checks isAuthenticated and navigates to /login
        await page.wait_for_url("**/login", timeout=5000)
        assert "/login" in page.url
