"""CLI script for seeding an admin (owner) user in the MAS database.

Idempotent: skips creation if a user with the given email already exists.

Usage::

    python -m src.cli.seed_admin --email admin@example.com --password secret
    python -m src.cli.seed_admin  # uses ADMIN_EMAIL / ADMIN_PASSWORD env vars

Environment variable defaults:
    ADMIN_EMAIL     — default email when ``--email`` is not supplied
    ADMIN_PASSWORD  — default password when ``--password`` is not supplied
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from src.api.guards import hash_password
from src.core.database import async_session_factory, engine
from src.core.models import User


async def seed_admin_user(
    email: str | None = None,
    password: str | None = None,
) -> bool:
    """Create an admin user with ``role='owner'`` if one doesn't already exist.

    Args:
        email: Admin email. Falls back to ``ADMIN_EMAIL`` env var.
        password: Admin password. Falls back to ``ADMIN_PASSWORD`` env var.

    Returns:
        ``True`` if a new user was created, ``False`` if the user already exists.

    Raises:
        ValueError: If no email or password is provided (neither arg nor env).
    """
    # Resolve email and password from args or env
    resolved_email = email or os.environ.get("ADMIN_EMAIL", "")
    resolved_password = password or os.environ.get("ADMIN_PASSWORD", "")

    if not resolved_email:
        raise ValueError("No email provided. Use --email flag or set ADMIN_EMAIL env var.")
    if not resolved_password:
        raise ValueError("No password provided. Use --password flag or set ADMIN_PASSWORD env var.")

    resolved_email = resolved_email.strip().lower()

    async with async_session_factory() as session:
        # Check for existing user (idempotent)
        result = await session.execute(select(User).where(User.email == resolved_email))
        existing = result.scalar_one_or_none()

        if existing is not None:
            return False

        hashed = hash_password(resolved_password)

        user = User(
            id=uuid.uuid4(),
            email=resolved_email,
            password_hash=hashed,
            role="owner",
            status="active",
            created_at=datetime.now(UTC),
        )

        session.add(user)
        await session.commit()

    return True


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Seed an admin (owner) user in the MAS database. Idempotent.",
        prog="python -m src.cli.seed_admin",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Admin email address (default: ADMIN_EMAIL env var)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Admin password (default: ADMIN_PASSWORD env var)",
    )
    return parser


async def _main(email: str | None, password: str | None) -> None:
    """Async entry point."""
    try:
        created = await seed_admin_user(email=email, password=password)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if created:
        resolved = email or os.environ.get("ADMIN_EMAIL", "")
        print(f"Admin user created: {resolved}")
    else:
        resolved = email or os.environ.get("ADMIN_EMAIL", "")
        print(f"Admin user already exists: {resolved} (skipped)")

    await engine.dispose()


def main() -> None:
    """Parse arguments and run the async seed function."""
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_main(email=args.email, password=args.password))


if __name__ == "__main__":
    main()
