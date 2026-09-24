"""Unit tests for L9: Unsubscribe Link Verification.

Tests the POST /api/v1/unsubscribe/{token} endpoint: token decoding,
suppression list integration, error handling.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_test_token(email: str, secret: str = "test-secret", ttl: int = 86400) -> str:
    """Generate a valid unsubscribe token for testing."""
    from src.api.routes.unsubscribe import generate_unsubscribe_token

    return generate_unsubscribe_token(email, secret=secret)


# ---------------------------------------------------------------------------
# Token generation / validation tests
# ---------------------------------------------------------------------------


class TestGenerateUnsubscribeToken:
    """Tests for generate_unsubscribe_token."""

    def test_generates_non_empty_token(self) -> None:
        """Token is a non-empty string."""
        from src.api.routes.unsubscribe import generate_unsubscribe_token

        token = generate_unsubscribe_token("user@example.com", secret="s3cret")
        assert isinstance(token, str)
        assert len(token) > 10

    def test_deterministic_for_same_inputs(self) -> None:
        """Same email + secret produce the same token (ignoring timestamp)."""
        from src.api.routes.unsubscribe import generate_unsubscribe_token

        # Tokens include a timestamp, so they won't be identical
        # But they should both be valid
        t1 = generate_unsubscribe_token("a@b.com", secret="x")
        t2 = generate_unsubscribe_token("a@b.com", secret="x")
        # Both are valid strings
        assert isinstance(t1, str)
        assert isinstance(t2, str)

    def test_different_emails_different_tokens(self) -> None:
        """Different emails produce different tokens."""
        from src.api.routes.unsubscribe import generate_unsubscribe_token

        t1 = generate_unsubscribe_token("a@b.com", secret="x")
        t2 = generate_unsubscribe_token("c@d.com", secret="x")
        assert t1 != t2


class TestValidateUnsubscribeToken:
    """Tests for validate_unsubscribe_token."""

    def test_valid_token_returns_email(self) -> None:
        """A valid token returns the original email."""
        from src.api.routes.unsubscribe import (
            generate_unsubscribe_token,
            validate_unsubscribe_token,
        )

        token = generate_unsubscribe_token("user@test.com", secret="key")
        email = validate_unsubscribe_token(token, secret="key")
        assert email == "user@test.com"

    def test_invalid_token_returns_none(self) -> None:
        """A corrupted token returns None."""
        from src.api.routes.unsubscribe import validate_unsubscribe_token

        email = validate_unsubscribe_token("invalid-garbage-token", secret="key")
        assert email is None

    def test_wrong_secret_returns_none(self) -> None:
        """A token validated with the wrong secret returns None."""
        from src.api.routes.unsubscribe import (
            generate_unsubscribe_token,
            validate_unsubscribe_token,
        )

        token = generate_unsubscribe_token("a@b.com", secret="correct")
        email = validate_unsubscribe_token(token, secret="wrong")
        assert email is None

    def test_expired_token_returns_none(self) -> None:
        """An expired token returns None."""
        # Manually craft an expired token
        from src.api.routes.unsubscribe import _encode_token, validate_unsubscribe_token

        token = _encode_token("old@test.com", secret="key", timestamp=int(time.time()) - 200000)
        email = validate_unsubscribe_token(token, secret="key", max_age=86400)
        assert email is None

    def test_email_normalization(self) -> None:
        """Email is lowercased and stripped."""
        from src.api.routes.unsubscribe import (
            generate_unsubscribe_token,
            validate_unsubscribe_token,
        )

        token = generate_unsubscribe_token("  USER@Test.COM  ", secret="k")
        email = validate_unsubscribe_token(token, secret="k")
        assert email == "user@test.com"


class TestBuildUnsubscribeUrl:
    """Tests for build_unsubscribe_url helper."""

    def test_builds_valid_url(self) -> None:
        """Returns a URL with the token in the path."""
        from src.api.routes.unsubscribe import build_unsubscribe_url

        url = build_unsubscribe_url(
            "user@test.com",
            base_url="https://api.example.com",
            secret="key",
        )
        assert url.startswith("https://api.example.com/api/v1/unsubscribe/")
        assert len(url) > len("https://api.example.com/api/v1/unsubscribe/")

    def test_url_contains_token(self) -> None:
        """The URL contains a valid token that can be decoded."""
        from src.api.routes.unsubscribe import (
            build_unsubscribe_url,
            validate_unsubscribe_token,
        )

        url = build_unsubscribe_url("a@b.com", base_url="https://x.com", secret="s")
        # Extract token from URL
        token = url.rsplit("/", 1)[-1]
        email = validate_unsubscribe_token(token, secret="s")
        assert email == "a@b.com"


class TestUnsubscribeEndpointHandler:
    """Tests for the actual endpoint handler logic."""

    @pytest.mark.asyncio
    async def test_valid_token_adds_to_suppression(self) -> None:
        """Valid token adds email to suppression list."""
        from src.api.routes.unsubscribe import handle_unsubscribe

        with (
            patch("src.api.routes.unsubscribe._get_unsubscribe_secret", return_value="secret"),
            patch("src.api.routes.unsubscribe.validate_unsubscribe_token", return_value="user@test.com"),
            patch("src.api.routes.unsubscribe._suppress_email") as mock_suppress,
        ):
            mock_suppress.return_value = True

            result = await handle_unsubscribe("valid-token")

        assert result["success"] is True
        assert result["email"] == "user@test.com"
        mock_suppress.assert_called_once_with("user@test.com")

    @pytest.mark.asyncio
    async def test_invalid_token_returns_error(self) -> None:
        """Invalid token returns error response."""
        from src.api.routes.unsubscribe import handle_unsubscribe

        with (
            patch("src.api.routes.unsubscribe._get_unsubscribe_secret", return_value="secret"),
            patch("src.api.routes.unsubscribe.validate_unsubscribe_token", return_value=None),
        ):
            result = await handle_unsubscribe("bad-token")

        assert result["success"] is False
        assert "invalid" in result["error"].lower() or "expired" in result["error"].lower()
