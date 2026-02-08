"""Semgrep security gate -- static analysis before code execution.

Wraps the Semgrep CLI to scan generated code for dangerous patterns before
it is executed in any sandbox (Docker / E2B).  The gate is invoked by the
Critic Agent and may also be called directly by the Dev Agent's
``analyze_with_semgrep`` tool.

Usage::

    gate = SemgrepGate()
    result = await gate.scan_code("import os; os.system('rm -rf /')")
    if result.blocked:
        raise SemgrepBlockedError(rule_ids=[f.rule_id for f in result.findings])
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Default rules directory -- sibling ``rules/`` folder next to this file.
_DEFAULT_RULES_DIR = str(Path(__file__).resolve().parent / "rules")

# Language -> file extension mapping for temp files.
_LANG_EXT_MAP: dict[str, str] = {
    "python": ".py",
    "javascript": ".js",
    "typescript": ".ts",
    "java": ".java",
    "go": ".go",
    "ruby": ".rb",
    "php": ".php",
    "rust": ".rs",
    "c": ".c",
    "cpp": ".cpp",
    "csharp": ".cs",
    "bash": ".sh",
    "shell": ".sh",
}

# Semgrep timeout in seconds.
_SEMGREP_TIMEOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemgrepFinding:
    """A single finding reported by Semgrep."""

    rule_id: str
    severity: str
    message: str
    path: str
    line: int
    code_snippet: str


@dataclass
class ScanResult:
    """Aggregated result of a Semgrep scan."""

    findings: list[SemgrepFinding] = field(default_factory=list)
    critical_count: int = 0
    warning_count: int = 0
    blocked: bool = False


# ---------------------------------------------------------------------------
# SemgrepGate
# ---------------------------------------------------------------------------

class SemgrepGate:
    """Wrapper over the Semgrep CLI for static code analysis.

    Parameters
    ----------
    rules_dir:
        Path to the directory containing ``.yaml`` rule files.  Defaults to
        the ``rules/`` subdirectory next to this module.
    """

    def __init__(self, rules_dir: str | None = None) -> None:
        self._rules_dir = Path(rules_dir) if rules_dir else Path(_DEFAULT_RULES_DIR)
        self._log = logger.bind(component="semgrep_gate")

        if not self._rules_dir.is_dir():
            self._log.warning(
                "rules_dir_not_found",
                rules_dir=str(self._rules_dir),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_code(self, code: str, language: str = "python") -> ScanResult:
        """Scan a single code snippet for dangerous patterns.

        Writes *code* to a temporary file with the appropriate extension,
        runs Semgrep, parses the JSON output, and cleans up.

        Parameters
        ----------
        code:
            Source code string to analyse.
        language:
            Programming language (used to pick the temp file extension).

        Returns
        -------
        ScanResult
            Aggregated findings with blocked flag.
        """
        ext = _LANG_EXT_MAP.get(language.lower(), ".py")
        tmp_dir = Path(tempfile.mkdtemp(prefix="mas_semgrep_"))

        try:
            tmp_file = tmp_dir / f"scan_target{ext}"
            tmp_file.write_text(code, encoding="utf-8")

            self._log.debug(
                "scan_code_start",
                language=language,
                code_length=len(code),
                tmp_path=str(tmp_file),
            )

            return await self._run_semgrep(tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def scan_files(self, files: list[dict[str, str]]) -> ScanResult:
        """Scan multiple files for dangerous patterns.

        Accepts a list of dicts with ``path`` and ``content`` keys, matching
        the output format of :meth:`DevAgent._parse_code_response`.

        Parameters
        ----------
        files:
            List of ``{"path": "relative/path.py", "content": "..."}`` dicts.

        Returns
        -------
        ScanResult
            Aggregated findings across all files.
        """
        if not files:
            self._log.debug("scan_files_empty")
            return ScanResult()

        tmp_dir = Path(tempfile.mkdtemp(prefix="mas_semgrep_"))

        try:
            for file_spec in files:
                rel_path = file_spec.get("path", "unknown.py")
                content = file_spec.get("content", "")

                # Sanitise: strip traversal segments and absolute prefixes.
                safe_parts = [p for p in Path(rel_path).parts if p not in ("..", "/", "\\") and not p.endswith(":")]
                safe_path = Path(*safe_parts) if safe_parts else Path("unknown.py")
                target = tmp_dir / safe_path
                # Final check: resolved path must stay within tmp_dir.
                if not target.resolve().is_relative_to(tmp_dir.resolve()):
                    self._log.warning("path_traversal_blocked", original=rel_path)
                    target = tmp_dir / Path(rel_path).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            self._log.debug(
                "scan_files_start",
                file_count=len(files),
                tmp_dir=str(tmp_dir),
            )

            return await self._run_semgrep(tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def is_blocked(result: ScanResult) -> bool:
        """Return ``True`` if the scan result should block code execution."""
        return result.blocked

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_semgrep(self, target_dir: Path) -> ScanResult:
        """Execute Semgrep CLI and parse its JSON output.

        Uses ``asyncio.create_subprocess_exec`` (never ``shell=True``).
        """
        cmd: list[str] = [
            "semgrep",
            "--config",
            str(self._rules_dir),
            "--json",
            "--no-git-ignore",
            str(target_dir),
        ]

        self._log.debug("semgrep_exec", cmd=cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SEMGREP_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            self._log.error("semgrep_not_installed")
            # If Semgrep is not installed, return a blocked result as a
            # safety precaution -- fail closed.
            return ScanResult(
                findings=[
                    SemgrepFinding(
                        rule_id="mas-semgrep-unavailable",
                        severity="ERROR",
                        message="Semgrep binary not found -- cannot verify code safety",
                        path="",
                        line=0,
                        code_snippet="",
                    ),
                ],
                critical_count=1,
                warning_count=0,
                blocked=True,
            )
        except TimeoutError:
            self._log.error("semgrep_timeout", timeout=_SEMGREP_TIMEOUT_SECONDS)
            return ScanResult(
                findings=[
                    SemgrepFinding(
                        rule_id="mas-semgrep-timeout",
                        severity="ERROR",
                        message=f"Semgrep scan timed out after {_SEMGREP_TIMEOUT_SECONDS}s",
                        path="",
                        line=0,
                        code_snippet="",
                    ),
                ],
                critical_count=1,
                warning_count=0,
                blocked=True,
            )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if stderr_text:
            self._log.debug("semgrep_stderr", stderr=stderr_text[:500])

        return self._parse_output(stdout_text)

    def _parse_output(self, raw_json: str) -> ScanResult:
        """Parse Semgrep JSON output into a :class:`ScanResult`."""
        if not raw_json.strip():
            self._log.debug("semgrep_empty_output")
            return ScanResult()

        try:
            data: dict[str, Any] = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            self._log.error("semgrep_json_parse_error", error=str(exc), raw=raw_json[:300])
            # Fail closed -- treat unparseable output as blocked.
            return ScanResult(
                findings=[
                    SemgrepFinding(
                        rule_id="mas-semgrep-parse-error",
                        severity="ERROR",
                        message=f"Failed to parse Semgrep output: {exc}",
                        path="",
                        line=0,
                        code_snippet="",
                    ),
                ],
                critical_count=1,
                warning_count=0,
                blocked=True,
            )

        results_list: list[dict[str, Any]] = data.get("results", [])
        findings: list[SemgrepFinding] = []
        critical_count = 0
        warning_count = 0

        for item in results_list:
            check_id = item.get("check_id", "unknown")
            extra = item.get("extra", {})
            severity = extra.get("severity", "WARNING").upper()
            message = extra.get("message", "")

            start = item.get("start", {})
            path = item.get("path", "")
            line = start.get("line", 0)

            # Extract the matching code snippet from extra.lines or build
            # from start/end.
            code_snippet = extra.get("lines", "").strip()

            finding = SemgrepFinding(
                rule_id=check_id,
                severity=severity,
                message=message,
                path=path,
                line=line,
                code_snippet=code_snippet,
            )
            findings.append(finding)

            if severity == "ERROR":
                critical_count += 1
            else:
                warning_count += 1

        blocked = critical_count > 0

        self._log.info(
            "semgrep_scan_complete",
            findings_count=len(findings),
            critical=critical_count,
            warnings=warning_count,
            blocked=blocked,
        )

        return ScanResult(
            findings=findings,
            critical_count=critical_count,
            warning_count=warning_count,
            blocked=blocked,
        )
