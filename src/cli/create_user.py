"""CLI script for creating a user in the MAS database.

Usage::

    python -m src.cli.create_user \\
        --email admin@example.com \\
        --password "secure-password" \\
        --role owner \\
        --name "Admin"

The script connects to the database specified by ``DATABASE_URL``, hashes the
password with bcrypt, inserts a new row into the ``users`` table, and prints
a confirmation message.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from src.api.guards import hash_password
from src.core.database import async_session_factory, engine
from src.core.models import User


async def _create_user(
    email: str,
    password: str,
    role: str,
    name: str | None,
) -> None:
    """Insert a new user into the database.

    Raises:
        SystemExit: If a user with the given email already exists.
    """
    async with async_session_factory() as session:
        # Check for duplicate email
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            print(f"ERROR: A user with email '{email}' already exists.", file=sys.stderr)
            raise SystemExit(1)

        hashed = hash_password(password)

        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=hashed,
            name=name,
            role=role,
            created_at=datetime.now(UTC),
        )

        session.add(user)
        await session.commit()

        print("User created successfully:")
        print(f"  ID:    {user.id}")
        print(f"  Email: {user.email}")
        print(f"  Name:  {user.name or '(none)'}")
        print(f"  Role:  {user.role}")

    # Clean up the engine
    await engine.dispose()


def main() -> None:
    """Parse arguments and run the async user creation."""
    parser = argparse.ArgumentParser(
        description="Create a new user in the MAS database.",
        prog="python -m src.cli.create_user",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="User email address (must be unique)",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Plaintext password (will be bcrypt-hashed before storage)",
    )
    parser.add_argument(
        "--role",
        default="owner",
        choices=["owner", "viewer"],
        help="User role (default: owner)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Display name (optional)",
    )

    args = parser.parse_args()

    asyncio.run(
        _create_user(
            email=args.email,
            password=args.password,
            role=args.role,
            name=args.name,
        )
    )


if __name__ == "__main__":
    main()
