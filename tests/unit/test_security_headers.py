"""Tests verifying security headers are set in nginx config.

Reads the nginx.conf file and asserts all required security headers
are present with correct values.
"""

from __future__ import annotations

import pathlib

import pytest

# Project root: tests/unit/test_security_headers.py -> ../../
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_NGINX_CONF = _PROJECT_ROOT / "docker" / "nginx" / "nginx.conf"


@pytest.fixture()
def nginx_content() -> str:
    """Read the nginx.conf file content."""
    return _NGINX_CONF.read_text(encoding="utf-8")


# ── CSP Header ────────────────────────────────────────────────────────────


class TestCSPHeader:
    """Content-Security-Policy header tests."""

    def test_csp_header_present(self, nginx_content: str) -> None:
        assert "Content-Security-Policy" in nginx_content

    def test_csp_default_src_self(self, nginx_content: str) -> None:
        assert "default-src 'self'" in nginx_content

    def test_csp_script_src_self(self, nginx_content: str) -> None:
        assert "script-src 'self'" in nginx_content

    def test_csp_style_src_unsafe_inline(self, nginx_content: str) -> None:
        assert "style-src 'self' 'unsafe-inline'" in nginx_content

    def test_csp_img_src_osm_tiles(self, nginx_content: str) -> None:
        assert "https://*.tile.openstreetmap.org" in nginx_content

    def test_csp_connect_src_wss(self, nginx_content: str) -> None:
        assert "connect-src 'self' wss:" in nginx_content

    def test_csp_font_src_self(self, nginx_content: str) -> None:
        assert "font-src 'self'" in nginx_content


# ── HSTS Header ───────────────────────────────────────────────────────────


class TestHSTSHeader:
    """Strict-Transport-Security header tests."""

    def test_hsts_header_present(self, nginx_content: str) -> None:
        assert "Strict-Transport-Security" in nginx_content

    def test_hsts_max_age(self, nginx_content: str) -> None:
        assert "max-age=31536000" in nginx_content

    def test_hsts_include_subdomains(self, nginx_content: str) -> None:
        assert "includeSubDomains" in nginx_content


# ── Other Security Headers ────────────────────────────────────────────────


class TestOtherSecurityHeaders:
    """X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy."""

    def test_x_frame_options_deny(self, nginx_content: str) -> None:
        assert "X-Frame-Options" in nginx_content
        assert '"DENY"' in nginx_content

    def test_x_content_type_options_nosniff(self, nginx_content: str) -> None:
        assert "X-Content-Type-Options" in nginx_content
        assert '"nosniff"' in nginx_content

    def test_referrer_policy(self, nginx_content: str) -> None:
        assert "Referrer-Policy" in nginx_content
        assert "strict-origin-when-cross-origin" in nginx_content

    def test_permissions_policy_camera(self, nginx_content: str) -> None:
        assert "Permissions-Policy" in nginx_content
        assert "camera=()" in nginx_content

    def test_permissions_policy_microphone(self, nginx_content: str) -> None:
        assert "microphone=()" in nginx_content

    def test_permissions_policy_geolocation(self, nginx_content: str) -> None:
        assert "geolocation=()" in nginx_content


# ── Proxy & WebSocket ─────────────────────────────────────────────────────


class TestProxyConfig:
    """Verify proxy and WebSocket configuration."""

    def test_websocket_upgrade_header(self, nginx_content: str) -> None:
        assert "proxy_set_header Upgrade" in nginx_content

    def test_websocket_connection_upgrade(self, nginx_content: str) -> None:
        assert '"upgrade"' in nginx_content

    def test_server_tokens_off(self, nginx_content: str) -> None:
        assert "server_tokens off" in nginx_content

    def test_proxy_pass_to_api_backend(self, nginx_content: str) -> None:
        assert "proxy_pass http://api_backend" in nginx_content

    def test_proxy_pass_to_dashboard(self, nginx_content: str) -> None:
        assert "proxy_pass http://dashboard_backend" in nginx_content

    def test_ws_location_block(self, nginx_content: str) -> None:
        assert "location /ws" in nginx_content

    def test_dashboard_location_block(self, nginx_content: str) -> None:
        assert "proxy_pass http://dashboard_backend" in nginx_content
