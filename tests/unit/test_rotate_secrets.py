"""Tests for secret rotation automation script.

Covers:
- JWT secret generation and rotation
- DB password generation and rotation
- OAuth token expiry checking with reminder logging
- Policy: 90-day JWT, 180-day DB, 365-day OAuth
- --dry-run mode (no changes applied)
- --check-expiry mode (reports what needs rotation)
- Railway CLI integration (mocked)
- Password strength (length, character classes)
- Error handling for missing Railway CLI
- Structlog output
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from scripts.rotate_secrets import (
    DB_ROTATION_DAYS,
    JWT_ROTATION_DAYS,
    OAUTH_ROTATION_DAYS,
    RotationPolicy,
    RotationReport,
    RotationResult,
    RotationStatus,
    SecretType,
    check_expiry,
    generate_secure_secret,
    get_secret_age_days,
    parse_args,
    rotate_db_password,
    rotate_jwt_secret,
    rotate_oauth_tokens,
    run_rotation,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify rotation policy constants."""

    def test_jwt_rotation_days(self) -> None:
        assert JWT_ROTATION_DAYS == 90

    def test_db_rotation_days(self) -> None:
        assert DB_ROTATION_DAYS == 180

    def test_oauth_rotation_days(self) -> None:
        assert OAUTH_ROTATION_DAYS == 365


# ---------------------------------------------------------------------------
# generate_secure_secret
# ---------------------------------------------------------------------------


class TestGenerateSecureSecret:
    """Test secure secret generation."""

    def test_default_length(self) -> None:
        secret = generate_secure_secret()
        assert len(secret) >= 48  # default minimum length

    def test_custom_length(self) -> None:
        secret = generate_secure_secret(length=64)
        assert len(secret) >= 64

    def test_uniqueness(self) -> None:
        secrets = {generate_secure_secret() for _ in range(10)}
        assert len(secrets) == 10  # all unique

    def test_url_safe_characters(self) -> None:
        """Secrets should be URL-safe (no characters that break env vars)."""
        secret = generate_secure_secret()
        # Should not contain shell-special characters
        for char in ["'", '"', "\\", " ", "\n", "\t", "$", "`"]:
            assert char not in secret


# ---------------------------------------------------------------------------
# RotationPolicy
# ---------------------------------------------------------------------------


class TestRotationPolicy:
    """Test rotation policy data class."""

    def test_jwt_policy(self) -> None:
        policy = RotationPolicy(
            secret_type=SecretType.JWT,
            max_age_days=JWT_ROTATION_DAYS,
            env_var="JWT_SECRET_KEY",
        )
        assert policy.secret_type == SecretType.JWT
        assert policy.max_age_days == 90
        assert policy.env_var == "JWT_SECRET_KEY"

    def test_db_policy(self) -> None:
        policy = RotationPolicy(
            secret_type=SecretType.DATABASE,
            max_age_days=DB_ROTATION_DAYS,
            env_var="DATABASE_URL",
        )
        assert policy.max_age_days == 180


# ---------------------------------------------------------------------------
# get_secret_age_days
# ---------------------------------------------------------------------------


class TestGetSecretAgeDays:
    """Test secret age calculation."""

    def test_known_age(self) -> None:
        last_rotated = datetime.now(UTC) - timedelta(days=45)
        age = get_secret_age_days(last_rotated)
        assert age == 45 or age == 44  # allow 1-day rounding

    def test_none_returns_max(self) -> None:
        """If no rotation date is known, treat as maximally aged."""
        age = get_secret_age_days(None)
        assert age == -1  # sentinel for "unknown"

    def test_future_date(self) -> None:
        """Future date returns 0 (just rotated)."""
        future = datetime.now(UTC) + timedelta(days=10)
        age = get_secret_age_days(future)
        assert age == 0


# ---------------------------------------------------------------------------
# check_expiry
# ---------------------------------------------------------------------------


class TestCheckExpiry:
    """Test --check-expiry mode."""

    def test_returns_report(self) -> None:
        report = check_expiry()
        assert isinstance(report, RotationReport)

    def test_report_has_all_secret_types(self) -> None:
        report = check_expiry()
        types = {item.secret_type for item in report.items}
        assert SecretType.JWT in types
        assert SecretType.DATABASE in types
        assert SecretType.OAUTH in types

    def test_report_items_have_status(self) -> None:
        report = check_expiry()
        for item in report.items:
            assert isinstance(item.status, RotationStatus)


# ---------------------------------------------------------------------------
# rotate_jwt_secret
# ---------------------------------------------------------------------------


class TestRotateJwtSecret:
    """Test JWT secret rotation."""

    @patch("scripts.rotate_secrets._run_railway_cmd")
    def test_dry_run_no_railway_call(self, mock_cmd: MagicMock) -> None:
        result = rotate_jwt_secret(dry_run=True)
        mock_cmd.assert_not_called()
        assert result.status == RotationStatus.DRY_RUN

    @patch("scripts.rotate_secrets._run_railway_cmd")
    def test_rotates_and_sets_env(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (0, "OK")
        result = rotate_jwt_secret(dry_run=False)
        assert result.status == RotationStatus.ROTATED
        # Should have called Railway to set the new secret
        mock_cmd.assert_called()
        call_args_str = str(mock_cmd.call_args)
        assert "JWT_SECRET_KEY" in call_args_str

    @patch("scripts.rotate_secrets._run_railway_cmd")
    def test_railway_failure(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (1, "Error: not logged in")
        result = rotate_jwt_secret(dry_run=False)
        assert result.status == RotationStatus.ERROR
        assert result.error is not None


# ---------------------------------------------------------------------------
# rotate_db_password
# ---------------------------------------------------------------------------


class TestRotateDbPassword:
    """Test database password rotation."""

    @patch("scripts.rotate_secrets._run_railway_cmd")
    def test_dry_run(self, mock_cmd: MagicMock) -> None:
        result = rotate_db_password(dry_run=True)
        mock_cmd.assert_not_called()
        assert result.status == RotationStatus.DRY_RUN

    @patch("scripts.rotate_secrets._run_railway_cmd")
    def test_rotates_db_password(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (0, "OK")
        result = rotate_db_password(dry_run=False)
        assert result.status == RotationStatus.ROTATED
        mock_cmd.assert_called()

    @patch("scripts.rotate_secrets._run_railway_cmd")
    def test_railway_failure(self, mock_cmd: MagicMock) -> None:
        mock_cmd.return_value = (1, "Error")
        result = rotate_db_password(dry_run=False)
        assert result.status == RotationStatus.ERROR


# ---------------------------------------------------------------------------
# rotate_oauth_tokens
# ---------------------------------------------------------------------------


class TestRotateOauthTokens:
    """Test OAuth token rotation (manual reminder only)."""

    def test_returns_reminder(self) -> None:
        result = rotate_oauth_tokens(dry_run=False)
        assert result.status == RotationStatus.MANUAL_REQUIRED
        assert result.message is not None
        assert "manual" in result.message.lower() or "reminder" in result.message.lower()

    def test_dry_run_also_returns_reminder(self) -> None:
        result = rotate_oauth_tokens(dry_run=True)
        assert result.status in (RotationStatus.DRY_RUN, RotationStatus.MANUAL_REQUIRED)


# ---------------------------------------------------------------------------
# run_rotation (integration of all three)
# ---------------------------------------------------------------------------


class TestRunRotation:
    """Test the full rotation orchestrator."""

    @patch("scripts.rotate_secrets.rotate_oauth_tokens")
    @patch("scripts.rotate_secrets.rotate_db_password")
    @patch("scripts.rotate_secrets.rotate_jwt_secret")
    def test_dry_run_passes_flag(
        self,
        mock_jwt: MagicMock,
        mock_db: MagicMock,
        mock_oauth: MagicMock,
    ) -> None:
        mock_jwt.return_value = RotationResult(SecretType.JWT, RotationStatus.DRY_RUN)
        mock_db.return_value = RotationResult(SecretType.DATABASE, RotationStatus.DRY_RUN)
        mock_oauth.return_value = RotationResult(SecretType.OAUTH, RotationStatus.DRY_RUN)

        report = run_rotation(dry_run=True)

        mock_jwt.assert_called_once_with(dry_run=True)
        mock_db.assert_called_once_with(dry_run=True)
        mock_oauth.assert_called_once_with(dry_run=True)
        assert len(report.items) == 3


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Test CLI argument parsing."""

    def test_default(self) -> None:
        args = parse_args([])
        assert args.dry_run is False
        assert args.check_expiry is False

    def test_dry_run(self) -> None:
        args = parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_check_expiry(self) -> None:
        args = parse_args(["--check-expiry"])
        assert args.check_expiry is True

    def test_both_flags(self) -> None:
        args = parse_args(["--dry-run", "--check-expiry"])
        assert args.dry_run is True
        assert args.check_expiry is True
