#!/usr/bin/env python3
"""Portfolio dogfooding — run MAS pipeline on internal portfolio projects.

Uses ``build_planner_pipeline_graph()`` to run Planner → Dev → Content
→ Design → Critic → Packager on portfolio project descriptions, producing
HTML/CSS mockups for screenshot and platform upload.

Usage::

    # Run a single project (by index)
    python scripts/create_portfolio_mockups.py --project 01

    # Run all projects
    python scripts/create_portfolio_mockups.py --all

    # Dry-run: just print the project contexts
    python scripts/create_portfolio_mockups.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path for imports.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


PORTFOLIO_DIR = _ROOT / "docs" / "portfolio"
OUTPUT_DIR = _ROOT / "portfolio" / "projects"

# Instruction appended to each project's requirements so Dev Agent
# generates a visual HTML/CSS page.
_VISUAL_INSTRUCTION = """

=== ADDITIONAL INSTRUCTIONS ===
Create a single-page HTML/CSS visual mockup of this project.
Frontend only, no backend needed.  Use Tailwind CSS via CDN.
Fill the page with realistic placeholder data and content.
The result should look like a real, polished product screenshot.
If the project is backend-only (bot, API, CRM), create a marketing
landing page describing the product's features instead.
Output a single index.html file with all styles inline or via CDN.
"""


def discover_projects() -> list[Path]:
    """Find all *.md files in docs/portfolio/, sorted by name."""
    if not PORTFOLIO_DIR.exists():
        return []
    return sorted(PORTFOLIO_DIR.glob("*.md"))


def load_project_description(path: Path) -> str:
    """Read a portfolio project markdown file."""
    return path.read_text(encoding="utf-8")


def build_project_context(
    description: str,
    project_index: str,
) -> dict[str, Any]:
    """Build a ProjectContext dict for the pipeline."""
    return {
        "project_id": f"portfolio_{project_index}",
        "job_id": "",
        "platform": "internal",
        "client": {"name": "MAS Portfolio", "type": "internal"},
        "requirements": description + _VISUAL_INSTRUCTION,
        "budget": 0.0,
        "deadline": datetime.now(tz=UTC),
    }


async def run_project(
    description: str,
    project_index: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Run the Planner pipeline on a single portfolio project.

    Returns the final state dict, or None on dry-run.
    """
    from src.core.graph import build_planner_pipeline_graph  # noqa: PLC0415
    from src.core.state import create_initial_state  # noqa: PLC0415
    from src.core.tracing import build_langsmith_config  # noqa: PLC0415

    project_ctx = build_project_context(description, project_index)

    if dry_run:
        print(f"[DRY-RUN] Project {project_index}:")
        print(f"  Platform: {project_ctx['platform']}")
        print(f"  Requirements: {description[:100]}...")
        return None

    initial_state = create_initial_state(
        project=project_ctx,  # type: ignore[arg-type]
        first_agent="planner",
        thread_id=f"portfolio-{project_index}",
    )

    graph = build_planner_pipeline_graph()
    config = build_langsmith_config(
        pipeline_name="portfolio_dogfooding",
        thread_id=f"portfolio-{project_index}",
    )

    print(f"[RUNNING] Project {project_index} — starting pipeline...")

    # Use astream to get final state reliably (see debugging.md).
    final: dict[str, Any] = {}
    async for chunk in graph.astream(
        initial_state, config=config or None, stream_mode="values",
    ):
        final = chunk

    status = final.get("status", "unknown")
    requires_hitl = final.get("requires_hitl", False)
    print(f"[DONE] Project {project_index} — status={status}, hitl={requires_hitl}")

    # Save artifacts summary.
    out_dir = OUTPUT_DIR / f"{project_index}"
    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts = final.get("artifacts", {})
    dev_artifacts = artifacts.get("dev", {})
    if isinstance(dev_artifacts, dict):
        for filename, content in dev_artifacts.items():
            if isinstance(content, str):
                (out_dir / filename).write_text(content, encoding="utf-8")
                print(f"  Saved: {out_dir / filename}")

    return final


async def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio dogfooding via MAS pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", type=str, help="Project index (e.g. 01)")
    group.add_argument("--all", action="store_true", help="Run all portfolio projects")
    group.add_argument("--dry-run", action="store_true", help="Print project contexts only")
    args = parser.parse_args()

    projects = discover_projects()
    if not projects:
        print(f"No portfolio projects found in {PORTFOLIO_DIR}")
        print("Create markdown files in docs/portfolio/ first.")
        sys.exit(1)

    print(f"Found {len(projects)} portfolio projects")

    if args.dry_run:
        for i, p in enumerate(projects, 1):
            desc = load_project_description(p)
            await run_project(desc, f"{i:03d}", dry_run=True)
        return

    if args.project:
        idx = int(args.project)
        if idx < 1 or idx > len(projects):
            print(f"Project index {idx} out of range (1-{len(projects)})")
            sys.exit(1)
        desc = load_project_description(projects[idx - 1])
        await run_project(desc, f"{idx:03d}")
        return

    # --all
    for i, p in enumerate(projects, 1):
        desc = load_project_description(p)
        await run_project(desc, f"{i:03d}")


if __name__ == "__main__":
    asyncio.run(main())
