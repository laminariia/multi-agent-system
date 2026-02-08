"""E2B Code Interpreter sandbox executor (fallback).

Uses the E2B cloud sandbox for short-lived code execution when Docker is
unavailable.  The ``e2b-code-interpreter`` package is an **optional**
dependency -- importing this module when the SDK is missing will work, but
calling :meth:`E2BExecutor.execute` will raise :class:`ImportError` with a
helpful message.
"""

from __future__ import annotations

import os
import time
from pathlib import PurePosixPath
from typing import Any

import structlog

from src.sandbox.base import ExecutionResult, SandboxExecutor

logger = structlog.get_logger(__name__)

# Attempt to import the E2B SDK.  If it is not installed the module stays
# importable but execution will raise at runtime.
_E2B_AVAILABLE = False
try:
    from e2b_code_interpreter import AsyncSandbox  # type: ignore[import-untyped]

    _E2B_AVAILABLE = True
except ImportError:
    AsyncSandbox = None  # type: ignore[assignment,misc]


class E2BExecutor(SandboxExecutor):
    """Execute code via the E2B Code Interpreter cloud sandbox.

    Parameters
    ----------
    api_key:
        E2B API key.  Falls back to the ``E2B_API_KEY`` environment variable
        when *None*.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("E2B_API_KEY", "")
        self._sandbox: Any | None = None
        self._log = logger.bind(executor="e2b")

        if not self._api_key:
            self._log.warning("e2b_no_api_key", hint="Set E2B_API_KEY or pass api_key explicitly")

    # ------------------------------------------------------------------
    # SandboxExecutor interface
    # ------------------------------------------------------------------

    async def execute(
        self,
        files: list[dict[str, str]],
        command: str | None = None,
        timeout_seconds: int = 60,
    ) -> ExecutionResult:
        """Upload *files* to an E2B sandbox and run *command*."""
        if not _E2B_AVAILABLE:
            raise ImportError(
                "The e2b-code-interpreter package is not installed.  "
                "Install it with: pip install 'e2b-code-interpreter>=0.0.8'"
            )

        if not self._api_key:
            return ExecutionResult(
                stderr="E2B API key is not configured (E2B_API_KEY)",
                exit_code=1,
            )

        if not files:
            return ExecutionResult(stderr="No files provided", exit_code=1)

        t0 = time.perf_counter()

        try:
            # 1. Create sandbox.
            self._sandbox = await AsyncSandbox.create(api_key=self._api_key, timeout=timeout_seconds)

            self._log.info(
                "e2b_execute_start",
                file_count=len(files),
                timeout=timeout_seconds,
            )

            # 2. Upload files (with path sanitisation).
            for file_spec in files:
                raw_path = file_spec.get("path", "main.py")
                content = file_spec.get("content", "")

                # Strip traversal segments and absolute prefixes.
                safe_parts = [
                    p for p in PurePosixPath(raw_path).parts
                    if p not in ("..", "/", "\\") and not p.endswith(":")
                ]
                safe_path = "/".join(safe_parts) if safe_parts else "main.py"

                await self._sandbox.filesystem.write(f"/home/user/{safe_path}", content)

            # 3. Determine command.
            if command is None:
                command = self._build_command(files)

            # 4. Execute.
            result = await self._sandbox.process.start_and_wait(
                cmd=command,
                cwd="/home/user",
                timeout=timeout_seconds,
            )

            elapsed_ms = (time.perf_counter() - t0) * 1000

            stdout = result.stdout if hasattr(result, "stdout") else ""
            stderr = result.stderr if hasattr(result, "stderr") else ""
            exit_code = result.exit_code if hasattr(result, "exit_code") else 0

            execution_result = ExecutionResult(
                stdout=stdout or "",
                stderr=stderr or "",
                exit_code=exit_code,
                timed_out=False,
                duration_ms=round(elapsed_ms, 2),
            )

            self._log.info(
                "e2b_execute_done",
                exit_code=exit_code,
                duration_ms=execution_result.duration_ms,
            )
            return execution_result

        except TimeoutError:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._log.warning("e2b_timeout", timeout=timeout_seconds)
            return ExecutionResult(
                stderr=f"E2B execution timed out after {timeout_seconds}s",
                exit_code=137,
                timed_out=True,
                duration_ms=round(elapsed_ms, 2),
            )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._log.error("e2b_execute_error", error=str(exc))
            return ExecutionResult(
                stderr=f"E2B execution error: {exc}",
                exit_code=1,
                duration_ms=round(elapsed_ms, 2),
            )

        finally:
            await self._close_sandbox()

    async def cleanup(self) -> None:
        """Close the E2B sandbox if one is still open."""
        await self._close_sandbox()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _close_sandbox(self) -> None:
        """Safely close the sandbox, swallowing errors."""
        if self._sandbox is not None:
            try:
                await self._sandbox.close()
            except Exception as exc:
                self._log.debug("e2b_close_error", error=str(exc))
            finally:
                self._sandbox = None

    @staticmethod
    def _build_command(files: list[dict[str, str]]) -> str:
        """Auto-detect the run command from the provided files."""
        paths = [f.get("path", "") for f in files]

        # Python files.
        py_files = [p for p in paths if p.endswith(".py")]
        if py_files:
            entry = "main.py" if "main.py" in py_files else py_files[0]
            return f"python {entry}"

        # JavaScript files.
        js_files = [p for p in paths if p.endswith(".js")]
        if js_files:
            entry = "index.js" if "index.js" in js_files else js_files[0]
            return f"node {entry}"

        # TypeScript files.
        ts_files = [p for p in paths if p.endswith(".ts")]
        if ts_files:
            entry = "index.ts" if "index.ts" in ts_files else ts_files[0]
            return f"npx ts-node {entry}"

        # Fallback.
        return f"python {paths[0]}" if paths else "python main.py"
