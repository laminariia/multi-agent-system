"""One-time migration: copy goals from YAML to PostgreSQL.

Usage::

    python -m src.cli.migrate_goals           # Dry run (print only)
    python -m src.cli.migrate_goals --apply   # Actually insert into DB
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from sqlalchemy import select

from src.core.database import async_session_factory, engine
from src.core.models import OrchestratorGoal

_HOME = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
GOALS_FILE = _HOME / ".claude" / "orchestrator" / "goals.yaml"


def _parse_goals_yaml(path: Path) -> list[dict]:
    """Minimal YAML-like parser for goals.yaml (regex-based)."""
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8", errors="replace")
    goals: list[dict] = []
    current: dict | None = None

    for line in content.splitlines():
        stripped = line.rstrip()
        if re.match(r"^\s+-\s+id:\s+", stripped):
            if current:
                goals.append(current)
            m = re.match(r"^\s+-\s+id:\s+(\S+)", stripped)
            current = {"id": m.group(1)} if m else {"id": "unknown"}
        elif current is not None:
            if m := re.match(r'^\s+title:\s+"(.+)"', stripped):
                current["title"] = m.group(1)
            elif m := re.match(r"^\s+title:\s+(.+)", stripped):
                current["title"] = m.group(1).strip().strip('"')
            elif m := re.match(r"^\s+priority:\s+(\S+)", stripped):
                current["priority"] = m.group(1)
            elif m := re.match(r"^\s+category:\s+(\S+)", stripped):
                current["category"] = m.group(1)
            elif m := re.match(r"^\s+status:\s+(\S+)", stripped):
                current["status"] = m.group(1)
            elif m := re.match(r'^\s+result:\s+"(.+)"', stripped):
                current["result"] = m.group(1)
            elif m := re.match(r"^\s+result:\s+(.+)", stripped):
                current["result"] = m.group(1).strip().strip('"')
            elif m := re.match(r'^\s+context:\s+"(.+)"', stripped):
                current["context"] = m.group(1)

    if current:
        goals.append(current)
    return goals


async def migrate(apply: bool = False) -> None:
    """Read goals from YAML and insert into PostgreSQL."""
    if not GOALS_FILE.exists():
        print(f"Goals file not found: {GOALS_FILE}")
        return

    goals = _parse_goals_yaml(GOALS_FILE)
    if not goals:
        print("No goals found in YAML file.")
        return

    print(f"Found {len(goals)} goals in {GOALS_FILE}")
    for g in goals:
        print(f"  {g.get('id', '?'):>6}  [{g.get('status', '?'):>10}]  {g.get('title', '?')}")

    if not apply:
        print("\nDry run — pass --apply to insert into database.")
        return

    async with async_session_factory() as session:
        inserted = 0
        skipped = 0
        for g in goals:
            goal_id = g.get("id", "")
            # Check if already exists
            existing = await session.execute(
                select(OrchestratorGoal).where(OrchestratorGoal.goal_id == goal_id)
            )
            if existing.scalar_one_or_none():
                print(f"  SKIP {goal_id} (already exists)")
                skipped += 1
                continue

            obj = OrchestratorGoal(
                goal_id=goal_id,
                title=g.get("title", "Untitled"),
                priority=g.get("priority", "medium"),
                category=g.get("category", "feature"),
                status=g.get("status", "pending"),
                result=g.get("result"),
                context=g.get("context"),
            )
            session.add(obj)
            inserted += 1
            print(f"  ADD  {goal_id}")

        await session.commit()
        print(f"\nDone: {inserted} inserted, {skipped} skipped.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate goals from YAML to PostgreSQL")
    parser.add_argument("--apply", action="store_true", help="Actually insert into DB")
    args = parser.parse_args()
    asyncio.run(migrate(apply=args.apply))


if __name__ == "__main__":
    main()
