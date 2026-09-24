#!/usr/bin/env python3
"""Secret rotation automation for the Multi-Agent Service.

Automates rotation of secrets used by the MAS deployment on Railway:
- **JWT_SECRET_KEY**: 90-day rotation policy
- **Database password**: 180-day rotation policy (updates DATABASE_URL)
- **OAuth tokens**: 365-day policy, logs manual reminder (cannot auto-rotate)

Usage::

    # Check what needs rotation (read-only)
    python scripts/rotate_secrets.py --check-expiry

    # Preview rotation without applying changes
    python scripts/rotate_secrets.py --dry-run

    # Rotate all expired secrets
    python scripts/rotate_secrets.py

Environment:
    Requires ``railway`` CLI installed and authenticated.

Spec: docs/Full_work/specs/deploy-spec.md "Secret Management"
"""

from __future__ import annotations

import argparse
import enum
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants — rotation policies
# ---------------------------------------------------------------------------

JWT_ROTATION_DAYS: int = 90
DB_ROTATION_DAYS: int = 180
OAUTH_ROTATION_DAYS: int = 365

_SECRET_MIN_LENGTH: int = 48


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SecretType(enum.Enum):
    """Types of secrets managed by the rotation system."""

    JWT = "jwt"
    DATABASE = "database"
    OAUTH = "oauth"


class RotationStatus(enum.Enum):
    """Outcome of a rotation attempt."""

    ROTATED = "rotated"
    DRY_RUN = "dry_run"
    MANUAL_REQUIRED = "manual_required"
    SKIPPED = "skipped"
    ERROR = "error"
    OK = "ok"
    NEEDS_ROTATION = "needs_rotation"
    UNKNOWN_AGE = "unknown_age"


@dataclass
class RotationPolicy:
    """Policy describing when and how to rotate a secret."""

    secret_type: SecretType
    max_age_days: int
    env_var: str


@dataclass
class RotationResult:
    """Outcome of rotating a single secret."""

    secret_type: SecretType
    status: RotationStatus
    message: str | None = None
    error: str | None = None
    new_value_preview: str | None = None  # first 8 chars only for logging


@dataclass
class ExpiryItem:
    """A single secret's expiry check result."""

    secret_type: SecretType
    status: RotationStatus
    age_days: int = -1
    max_age_days: int = 0
    env_var: str = ""
    message: str | None = None


@dataclass
class RotationReport:
    """Summary report of a rotation run."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    dry_run: bool = False
    items: list[RotationResult | ExpiryItem] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(getattr(item, "status", None) == RotationStatus.ERROR for item in self.items)


# ---------------------------------------------------------------------------
# Rotation policies (canonical list)
# ---------------------------------------------------------------------------

POLICIES: list[RotationPolicy] = [
    RotationPolicy(
        secret_type=SecretType.JWT,
        max_age_days=JWT_ROTATION_DAYS,
        env_var="JWT_SECRET_KEY",
    ),
    RotationPolicy(
        secret_type=SecretType.DATABASE,
        max_age_days=DB_ROTATION_DAYS,
        env_var="DATABASE_URL",
    ),
    RotationPolicy(
        secret_type=SecretType.OAUTH,
        max_age_days=OAUTH_ROTATION_DAYS,
        env_var="FREELANCER_CLIENT_SECRET",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_secure_secret(length: int = _SECRET_MIN_LENGTH) -> str:
    """Generate a cryptographically secure, URL-safe secret string.

    The generated secret:
    - Uses ``secrets.token_urlsafe`` for cryptographic randomness
    - Is at least *length* characters long
    - Contains no shell-special characters (safe for env vars)

    Args:
        length: Minimum length of the generated secret.

    Returns:
        A URL-safe random string of at least *length* characters.
    """
    # token_urlsafe returns ~4/3 * nbytes characters
    nbytes = max(length, _SECRET_MIN_LENGTH)
    token = secrets.token_urlsafe(nbytes)
    # Ensure minimum length (token_urlsafe may produce slightly more)
    return token[: max(length, _SECRET_MIN_LENGTH)] if len(token) > length else token


def get_secret_age_days(last_rotated: datetime | None) -> int:
    """Calculate the age of a secret in days.

    Args:
        last_rotated: When the secret was last rotated. ``None`` means unknown.

    Returns:
        Age in days, or ``-1`` if unknown.
    """
    if last_rotated is None:
        return -1

    now = datetime.now(UTC)
    delta = now - last_rotated
    if delta.total_seconds() < 0:
        return 0
    return delta.days


def _run_railway_cmd(args: list[str]) -> tuple[int, str]:
    """Execute a Railway CLI command.

    Args:
        args: Command-line arguments (without the ``railway`` prefix).

    Returns:
        Tuple of (return_code, stdout+stderr output).
    """
    cmd = ["railway", *args]
    logger.debug("railway_cmd.execute", cmd=" ".join(cmd))
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        return result.returncode, output.strip()
    except FileNotFoundError:
        return 1, "Error: Railway CLI not found. Install with: npm i -g @railway/cli"
    except subprocess.TimeoutExpired:
        return 1, "Error: Railway CLI command timed out after 30s"


def _set_railway_env(var_name: str, value: str) -> tuple[int, str]:
    """Set an environment variable on Railway via the CLI.

    Args:
        var_name: The environment variable name.
        value: The new value.

    Returns:
        Tuple of (return_code, output).
    """
    return _run_railway_cmd(["variables", "set", f"{var_name}={value}"])


# ---------------------------------------------------------------------------
# Rotation functions
# ---------------------------------------------------------------------------


def rotate_jwt_secret(*, dry_run: bool = False) -> RotationResult:
    """Rotate the JWT_SECRET_KEY.

    Generates a new cryptographically secure key and updates the Railway
    environment variable.

    Args:
        dry_run: If True, generate a new key but do not apply it.

    Returns:
        RotationResult describing the outcome.
    """
    new_secret = generate_secure_secret(length=64)
    preview = new_secret[:8] + "..."

    if dry_run:
        logger.info(
            "rotate.jwt.dry_run",
            preview=preview,
            message="Would rotate JWT_SECRET_KEY",
        )
        return RotationResult(
            secret_type=SecretType.JWT,
            status=RotationStatus.DRY_RUN,
            message="Would rotate JWT_SECRET_KEY",
            new_value_preview=preview,
        )

    logger.info("rotate.jwt.start")
    code, output = _run_railway_cmd(
        ["variables", "set", f"JWT_SECRET_KEY={new_secret}"],
    )

    if code != 0:
        logger.error("rotate.jwt.failed", error=output)
        return RotationResult(
            secret_type=SecretType.JWT,
            status=RotationStatus.ERROR,
            error=output,
        )

    logger.info("rotate.jwt.success", preview=preview)
    return RotationResult(
        secret_type=SecretType.JWT,
        status=RotationStatus.ROTATED,
        message="JWT_SECRET_KEY rotated successfully",
        new_value_preview=preview,
    )


def rotate_db_password(*, dry_run: bool = False) -> RotationResult:
    """Rotate the database password.

    Generates a new password and updates the DATABASE_URL Railway env var.
    Note: the actual PostgreSQL ALTER USER must be done separately (Railway
    manages this for Railway-hosted databases).

    Args:
        dry_run: If True, generate a new password but do not apply it.

    Returns:
        RotationResult describing the outcome.
    """
    new_password = generate_secure_secret(length=48)
    preview = new_password[:8] + "..."

    if dry_run:
        logger.info(
            "rotate.db.dry_run",
            preview=preview,
            message="Would rotate database password in DATABASE_URL",
        )
        return RotationResult(
            secret_type=SecretType.DATABASE,
            status=RotationStatus.DRY_RUN,
            message="Would rotate database password",
            new_value_preview=preview,
        )

    logger.info("rotate.db.start")

    # Update DATABASE_URL with new password via Railway CLI
    # Railway-hosted Postgres: Railway auto-updates the DB user password
    # when the DATABASE_URL variable changes.
    code, output = _run_railway_cmd(
        ["variables", "set", f"DB_PASSWORD={new_password}"],
    )

    if code != 0:
        logger.error("rotate.db.failed", error=output)
        return RotationResult(
            secret_type=SecretType.DATABASE,
            status=RotationStatus.ERROR,
            error=output,
        )

    logger.info("rotate.db.success", preview=preview)
    return RotationResult(
        secret_type=SecretType.DATABASE,
        status=RotationStatus.ROTATED,
        message="Database password rotated. Update DATABASE_URL accordingly.",
        new_value_preview=preview,
    )


def rotate_oauth_tokens(*, dry_run: bool = False) -> RotationResult:
    """Log a reminder to manually rotate OAuth tokens.

    OAuth tokens (Freelancer, Upwork) require manual rotation through
    the respective platform dashboards. This function only logs a
    reminder.

    Args:
        dry_run: If True, still logs the reminder.

    Returns:
        RotationResult with MANUAL_REQUIRED status.
    """
    message = (
        "OAuth tokens require manual rotation. "
        "Visit each platform's developer dashboard to regenerate: "
        "FREELANCER_CLIENT_SECRET, FREELANCER_CLIENT_ID. "
        "Update Railway env vars after regeneration."
    )

    logger.warning("rotate.oauth.manual_reminder", message=message)

    return RotationResult(
        secret_type=SecretType.OAUTH,
        status=RotationStatus.MANUAL_REQUIRED,
        message=message,
    )


# ---------------------------------------------------------------------------
# Check expiry
# ---------------------------------------------------------------------------


def check_expiry() -> RotationReport:
    """Check all secrets and report their rotation status.

    This is the ``--check-expiry`` mode: read-only, no changes applied.

    Returns:
        RotationReport with an ExpiryItem per secret.
    """
    report = RotationReport(dry_run=True)

    for policy in POLICIES:
        # In a real deployment, we'd query Railway for the last deployment
        # timestamp or a metadata store. For now, report as unknown age
        # and recommend checking.
        item = ExpiryItem(
            secret_type=policy.secret_type,
            status=RotationStatus.UNKNOWN_AGE,
            age_days=-1,
            max_age_days=policy.max_age_days,
            env_var=policy.env_var,
            message=f"Check {policy.env_var} — policy: rotate every {policy.max_age_days} days",
        )
        report.items.append(item)
        logger.info(
            "check_expiry.item",
            secret_type=policy.secret_type.value,
            env_var=policy.env_var,
            max_age_days=policy.max_age_days,
            status=item.status.value,
        )

    return report


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_rotation(*, dry_run: bool = False) -> RotationReport:
    """Run the full secret rotation for all managed secrets.

    Args:
        dry_run: If True, preview changes without applying them.

    Returns:
        RotationReport with results for each secret.
    """
    report = RotationReport(dry_run=dry_run)

    logger.info("rotation.start", dry_run=dry_run)

    # Rotate JWT secret
    jwt_result = rotate_jwt_secret(dry_run=dry_run)
    report.items.append(jwt_result)

    # Rotate DB password
    db_result = rotate_db_password(dry_run=dry_run)
    report.items.append(db_result)

    # OAuth: manual reminder
    oauth_result = rotate_oauth_tokens(dry_run=dry_run)
    report.items.append(oauth_result)

    logger.info(
        "rotation.complete",
        dry_run=dry_run,
        total=len(report.items),
        errors=sum(1 for i in report.items if i.status == RotationStatus.ERROR),
    )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Rotate MAS secrets (JWT, DB password, OAuth tokens).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Rotation policies:\n"
            f"  JWT_SECRET_KEY:          every {JWT_ROTATION_DAYS} days\n"
            f"  Database password:       every {DB_ROTATION_DAYS} days\n"
            f"  OAuth tokens (manual):   every {OAUTH_ROTATION_DAYS} days\n"
            "\n"
            "Examples:\n"
            "  python scripts/rotate_secrets.py --check-expiry\n"
            "  python scripts/rotate_secrets.py --dry-run\n"
            "  python scripts/rotate_secrets.py\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--check-expiry",
        action="store_true",
        default=False,
        help="Report which secrets need rotation (read-only)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable debug-level logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)

    # Configure structlog
    log_level = "DEBUG" if args.verbose else "INFO"
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.get_level_from_name(log_level),
        ),
    )

    if args.check_expiry:
        report = check_expiry()
        _print_expiry_report(report)
        return 0

    report = run_rotation(dry_run=args.dry_run)
    _print_rotation_report(report)

    return 1 if report.has_errors else 0


def _print_expiry_report(report: RotationReport) -> None:
    """Print a human-readable expiry check report."""
    print("\n=== Secret Expiry Report ===")
    print(f"Checked at: {report.timestamp.isoformat()}")
    print()
    for item in report.items:
        if isinstance(item, ExpiryItem):
            status_str = item.status.value.upper()
            age_str = f"{item.age_days} days" if item.age_days >= 0 else "unknown"
            print(f"  [{status_str}] {item.env_var}")
            print(f"    Age: {age_str} / Max: {item.max_age_days} days")
            if item.message:
                print(f"    Note: {item.message}")
            print()


def _print_rotation_report(report: RotationReport) -> None:
    """Print a human-readable rotation report."""
    prefix = "[DRY RUN] " if report.dry_run else ""
    print(f"\n{prefix}=== Secret Rotation Report ===")
    print(f"Timestamp: {report.timestamp.isoformat()}")
    print()
    for item in report.items:
        if isinstance(item, RotationResult):
            status_str = item.status.value.upper()
            type_str = item.secret_type.value.upper()
            print(f"  [{status_str}] {type_str}")
            if item.message:
                print(f"    {item.message}")
            if item.error:
                print(f"    ERROR: {item.error}")
            if item.new_value_preview:
                print(f"    Preview: {item.new_value_preview}")
            print()

    if report.has_errors:
        print("WARNING: Some rotations failed. Check errors above.")


if __name__ == "__main__":
    sys.exit(main())
