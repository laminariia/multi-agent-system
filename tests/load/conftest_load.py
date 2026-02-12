"""Shared configuration and utilities for Locust load tests.

Provides base URL configuration, test user credentials, common auth
helpers, and reusable validation functions.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("LOCUST_HOST", "http://localhost:8000")

# Test user credentials (seeded in load-test environment)
TEST_EMAIL = os.getenv("LOADTEST_EMAIL", "loadtest@example.com")
TEST_PASSWORD = os.getenv("LOADTEST_PASSWORD", "loadtest-password-123")

# ---------------------------------------------------------------------------
# Performance thresholds
# ---------------------------------------------------------------------------

P50_THRESHOLD_MS = 200
P95_THRESHOLD_MS = 500
P99_THRESHOLD_MS = 2000
MAX_ERROR_RATE_PCT = 1.0
MAX_5XX_COUNT = 0
MIN_RPS = 50

# Endpoints with stricter latency requirements
FAST_ENDPOINTS = {
    "/api/v1/health",
}

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def auth_headers(token: str) -> dict[str, str]:
    """Return Authorization header dict for the given JWT token."""
    return {"Authorization": f"Bearer {token}"}


def login_and_get_token(client, *, email: str = TEST_EMAIL, password: str = TEST_PASSWORD) -> str:
    """Perform login via the client and return the access token (or empty string)."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        name="/api/v1/auth/login [startup]",
    )
    if resp.status_code == 200:
        data = resp.json()
        return data.get("access_token", "")
    return ""
