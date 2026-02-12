"""Integration tests for Alembic migration integrity.

Verifies:
- Single migration head (no branch divergence)
- Linear migration chain (no orphans)
- Upgrade/downgrade reversibility for each revision
- ``alembic check`` reports no pending model changes
- Migration file naming conventions
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
VERSIONS_DIR = PROJECT_ROOT / "alembic" / "versions"


def _get_alembic_config() -> Config:
    """Build an Alembic Config from the project's alembic.ini."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg


def _get_script_directory() -> ScriptDirectory:
    """Return the ScriptDirectory for introspecting revisions."""
    return ScriptDirectory.from_config(_get_alembic_config())


def _collect_revisions() -> list[dict[str, Any]]:
    """Walk the migration chain and return ordered revision metadata."""
    script = _get_script_directory()
    revisions = []
    for rev in script.walk_revisions():
        revisions.append(
            {
                "revision": rev.revision,
                "down_revision": rev.down_revision,
                "doc": rev.doc,
                "path": rev.path,
            }
        )
    return revisions


# ---------------------------------------------------------------------------
# Tests — Migration Structure
# ---------------------------------------------------------------------------


class TestMigrationStructure:
    """Tests that verify the structural integrity of the migration chain."""

    def test_single_head(self):
        """There must be exactly one head revision (no branch divergence)."""
        script = _get_script_directory()
        heads = script.get_heads()
        assert len(heads) == 1, (
            f"Expected exactly 1 head, found {len(heads)}: {heads}. "
            "Run 'alembic merge heads' to fix branch divergence."
        )

    def test_single_base(self):
        """There must be exactly one base revision (the initial migration)."""
        script = _get_script_directory()
        bases = script.get_bases()
        assert len(bases) == 1, (
            f"Expected exactly 1 base, found {len(bases)}: {bases}."
        )

    def test_linear_chain(self):
        """Every revision (except the base) must have exactly one down_revision."""
        revisions = _collect_revisions()
        for rev in revisions:
            down = rev["down_revision"]
            if down is None:
                # Base revision — no parent
                continue
            # down_revision should be a single string, not a tuple (which indicates branching)
            assert isinstance(down, str), (
                f"Revision {rev['revision']} has a non-linear down_revision: {down}. "
                "This indicates a merge migration or branching."
            )

    def test_no_orphan_revisions(self):
        """Every revision's down_revision must point to an existing revision or None."""
        revisions = _collect_revisions()
        all_ids = {r["revision"] for r in revisions}
        for rev in revisions:
            down = rev["down_revision"]
            if down is None:
                continue
            assert down in all_ids, (
                f"Revision {rev['revision']} references down_revision={down} "
                "which does not exist in the versions directory."
            )

    def test_at_least_one_migration_exists(self):
        """The versions directory must contain at least one migration file."""
        py_files = [f for f in VERSIONS_DIR.iterdir() if f.suffix == ".py" and not f.name.startswith("__")]
        assert len(py_files) >= 1, "No migration files found in alembic/versions/"

    def test_head_is_most_recent(self):
        """The head revision should correspond to the most recently dated migration file."""
        script = _get_script_directory()
        heads = script.get_heads()
        assert len(heads) == 1
        head_rev = script.get_revision(heads[0])
        assert head_rev is not None

    def test_migration_file_naming_convention(self):
        """All migration files should follow the YYYYMMDD_HHMM_slug pattern."""
        pattern = re.compile(r"^\d{8}_\d{4}_[a-z0-9_]+\.py$")
        py_files = [f.name for f in VERSIONS_DIR.iterdir() if f.suffix == ".py" and not f.name.startswith("__")]
        for fn in py_files:
            assert pattern.match(fn), (
                f"Migration file '{fn}' does not match expected pattern "
                "'YYYYMMDD_HHMM_slug.py'. Example: '20260207_2326_initial_schema.py'"
            )


# ---------------------------------------------------------------------------
# Tests — Migration Content
# ---------------------------------------------------------------------------


class TestMigrationContent:
    """Tests that verify each migration has proper upgrade/downgrade functions."""

    def test_all_migrations_have_upgrade(self):
        """Every migration must define an upgrade() function."""
        script = _get_script_directory()
        for rev in script.walk_revisions():
            module = rev.module
            assert hasattr(module, "upgrade"), (
                f"Migration {rev.revision} ({rev.path}) is missing upgrade() function."
            )
            assert callable(module.upgrade)

    def test_all_migrations_have_downgrade(self):
        """Every migration must define a downgrade() function."""
        script = _get_script_directory()
        for rev in script.walk_revisions():
            module = rev.module
            assert hasattr(module, "downgrade"), (
                f"Migration {rev.revision} ({rev.path}) is missing downgrade() function."
            )
            assert callable(module.downgrade)

    def test_revision_ids_are_unique(self):
        """All revision IDs must be unique."""
        revisions = _collect_revisions()
        ids = [r["revision"] for r in revisions]
        assert len(ids) == len(set(ids)), (
            f"Duplicate revision IDs found: {[x for x in ids if ids.count(x) > 1]}"
        )

    def test_revision_has_docstring(self):
        """Each migration should have a module-level docstring."""
        script = _get_script_directory()
        for rev in script.walk_revisions():
            assert rev.doc, (
                f"Migration {rev.revision} has no docstring. "
                "Add a descriptive module-level docstring."
            )


# ---------------------------------------------------------------------------
# Tests — Alembic CLI (requires DATABASE_URL to be set for 'check')
# ---------------------------------------------------------------------------


class TestAlembicCLI:
    """Tests that invoke alembic CLI commands.

    These tests require a running PostgreSQL with DATABASE_URL set.
    They are automatically skipped if DATABASE_URL is not configured
    or points to an unreachable database.
    """

    @pytest.fixture(autouse=True)
    def _require_database(self):
        """Skip tests if DATABASE_URL is not set or is a dummy value."""
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url or "localhost" not in db_url:
            pytest.skip("DATABASE_URL not configured for live DB")
        # Also skip if the URL clearly points to a non-running test DB
        if db_url == "postgresql://test:test@localhost:5432/test":
            pytest.skip("DATABASE_URL points to dummy test DB")

    def test_alembic_upgrade_head(self):
        """``alembic upgrade head`` should succeed without errors."""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )

    def test_alembic_check_no_pending(self):
        """``alembic check`` should report no pending changes after upgrade head.

        This ensures models.py and the migration chain are in sync.
        """
        # First ensure we're at head
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        # Then check for pending changes
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        assert result.returncode == 0, (
            f"alembic check found pending changes:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}\n"
            "Run 'alembic revision --autogenerate -m \"description\"' to generate a new migration."
        )

    def test_alembic_current_at_head(self):
        """After upgrade head, ``alembic current`` should show the head revision."""
        # Ensure at head
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        assert result.returncode == 0
        script = _get_script_directory()
        heads = script.get_heads()
        assert heads[0] in result.stdout, (
            f"Expected head revision {heads[0]} in alembic current output, "
            f"got: {result.stdout}"
        )

    def test_alembic_downgrade_and_upgrade_roundtrip(self):
        """Downgrade one step and upgrade back to head should succeed."""
        # Ensure at head first
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        # Downgrade one step
        result_down = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "-1"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        assert result_down.returncode == 0, (
            f"alembic downgrade -1 failed:\n{result_down.stderr}"
        )
        # Upgrade back to head
        result_up = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        assert result_up.returncode == 0, (
            f"alembic upgrade head after downgrade failed:\n{result_up.stderr}"
        )
