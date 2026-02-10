"""Docker-based sandbox executor.

Runs generated code inside a disposable Docker container with strict
resource limits, no network access, and a non-root user.  Each invocation
creates a fresh container, captures output, and tears down cleanly --
even on timeout.

All Docker SDK calls are synchronous and are therefore wrapped with
:func:`asyncio.to_thread` to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from src.sandbox.base import ExecutionResult, SandboxExecutor

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Language -> Docker image mapping
# ---------------------------------------------------------------------------

_LANGUAGE_IMAGE_MAP: dict[str, str] = {
    "python": "mas-sandbox:latest",
    "javascript": "mas-sandbox:latest",
    "typescript": "mas-sandbox:latest",
}

_DEFAULT_IMAGE = "mas-sandbox:latest"

# ---------------------------------------------------------------------------
# File extension helpers
# ---------------------------------------------------------------------------

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

_LANGUAGE_COMMANDS: dict[str, dict[str, str]] = {
    "python": {"default_entry": "main.py", "runner": "python"},
    "javascript": {"default_entry": "index.js", "runner": "node"},
    "typescript": {"default_entry": "index.ts", "runner": "npx ts-node"},
}


class DockerExecutor(SandboxExecutor):
    """Execute code in an isolated Docker container.

    Parameters
    ----------
    image:
        Docker image to use.  Defaults to ``mas-sandbox:latest``.
    """

    def __init__(self, image: str = _DEFAULT_IMAGE) -> None:
        self._image = image
        self._log = logger.bind(executor="docker", image=image)

    # ------------------------------------------------------------------
    # SandboxExecutor interface
    # ------------------------------------------------------------------

    async def execute(
        self,
        files: list[dict[str, str]],
        command: str | None = None,
        timeout_seconds: int = 60,
    ) -> ExecutionResult:
        """Write *files* to a temp directory and run them inside a Docker container."""
        if not files:
            return ExecutionResult(stderr="No files provided", exit_code=1)

        tmp_dir = tempfile.mkdtemp(prefix="mas_sandbox_")
        container: Any | None = None
        t0 = time.perf_counter()

        try:
            # 1. Write files to the temp directory.
            self._write_files(tmp_dir, files)

            # 2. Determine language and build the command.
            language = self._detect_language(files)
            if command is None:
                command = self._build_command(files, language)

            image = _LANGUAGE_IMAGE_MAP.get(language, self._image)

            self._log.info(
                "docker_execute_start",
                language=language,
                command=command,
                file_count=len(files),
                timeout=timeout_seconds,
            )

            # 3. Run the container (sync SDK, wrapped in to_thread).
            container, timed_out = await self._run_container(
                tmp_dir=tmp_dir,
                command=command,
                image=image,
                timeout_seconds=timeout_seconds,
            )

            # 4. Capture output.
            stdout, stderr = await self._capture_logs(container)

            # 5. Determine exit code.
            if timed_out:
                exit_code = 137  # SIGKILL convention
            else:
                exit_code = await self._get_exit_code(container)

            # 6. List any files created in /workspace during execution.
            files_created = self._list_created_files(tmp_dir, files)

            elapsed_ms = (time.perf_counter() - t0) * 1000

            result = ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=timed_out,
                duration_ms=round(elapsed_ms, 2),
                files_created=files_created,
            )

            self._log.info(
                "docker_execute_done",
                exit_code=exit_code,
                timed_out=timed_out,
                duration_ms=result.duration_ms,
            )
            return result

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._log.error("docker_execute_error", error=str(exc), duration_ms=round(elapsed_ms, 2))
            return ExecutionResult(
                stderr=f"Docker execution error: {exc}",
                exit_code=1,
                duration_ms=round(elapsed_ms, 2),
            )

        finally:
            # Always clean up.
            await self._cleanup_container(container)
            self._cleanup_tmp_dir(tmp_dir)

    async def cleanup(self) -> None:
        """No-op -- each execution cleans up after itself."""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_files(tmp_dir: str, files: list[dict[str, str]]) -> None:
        """Write file dicts to the temp directory, sanitising paths."""
        base = Path(tmp_dir)
        for file_spec in files:
            rel_path = file_spec.get("path", "main.py")
            content = file_spec.get("content", "")

            # Sanitise: remove traversal segments and absolute prefixes.
            safe_parts = [
                p for p in Path(rel_path).parts
                if p not in ("..", "/", "\\") and not p.endswith(":")
            ]
            safe_path = Path(*safe_parts) if safe_parts else Path("main.py")
            target = base / safe_path

            # Final guard: resolved path must stay within tmp_dir.
            if not target.resolve().is_relative_to(base.resolve()):
                target = base / Path(rel_path).name

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _detect_language(files: list[dict[str, str]]) -> str:
        """Infer the dominant language from file extensions."""
        counts: dict[str, int] = {}
        for f in files:
            path = f.get("path", "")
            ext = os.path.splitext(path)[1].lower()
            lang = _EXT_TO_LANGUAGE.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1

        if not counts:
            return "python"  # default

        return max(counts, key=counts.get)  # type: ignore[arg-type]

    @staticmethod
    def _build_command(files: list[dict[str, str]], language: str) -> str:
        """Auto-detect the shell command from files and language."""
        lang_info = _LANGUAGE_COMMANDS.get(language, _LANGUAGE_COMMANDS["python"])
        runner = lang_info["runner"]
        default_entry = lang_info["default_entry"]

        # Check if the default entry file is among the provided files.
        paths = [f.get("path", "") for f in files]
        for p in paths:
            basename = os.path.basename(p)
            if basename == default_entry:
                return f"{runner} {p}"

        # Fall back to the first file whose extension matches the language.
        ext_map = {"python": ".py", "javascript": ".js", "typescript": ".ts"}
        target_ext = ext_map.get(language, ".py")

        for p in paths:
            if p.endswith(target_ext):
                return f"{runner} {p}"

        # Last resort: run the first file with the runner.
        return f"{runner} {paths[0]}" if paths else f"{runner} {default_entry}"

    async def _run_container(
        self,
        *,
        tmp_dir: str,
        command: str,
        image: str,
        timeout_seconds: int,
    ) -> tuple[Any, bool]:
        """Start a Docker container and wait for completion or timeout.

        Returns ``(container, timed_out)`` tuple.
        """
        import docker  # noqa: PLC0415 -- lazy import to keep module importable without docker SDK

        def _sync_run() -> tuple[Any, bool]:
            client = docker.from_env()
            try:
                container = client.containers.run(
                    image=image,
                    command=f"sh -c {shlex.quote(command)}",
                    volumes={tmp_dir: {"bind": "/workspace", "mode": "rw"}},
                    working_dir="/workspace",
                    mem_limit="512m",
                    nano_cpus=1_000_000_000,  # 1 CPU
                    network_mode="none",
                    user="1000:1000",
                    read_only=False,
                    detach=True,
                    stderr=True,
                    stdout=True,
                )

                timed_out = False
                try:
                    container.wait(timeout=timeout_seconds)
                except Exception:
                    # Timeout or other error -- force-stop the container.
                    timed_out = True
                    try:
                        container.stop(timeout=5)
                    except Exception as stop_exc:  # noqa: BLE001
                        logger.debug("container_stop_failed", error=str(stop_exc))

                return container, timed_out
            finally:
                client.close()

        return await asyncio.to_thread(_sync_run)

    @staticmethod
    async def _capture_logs(container: Any) -> tuple[str, str]:
        """Read stdout and stderr from the container."""

        def _sync_logs() -> tuple[str, str]:
            try:
                stdout_bytes = container.logs(stdout=True, stderr=False)
                stderr_bytes = container.logs(stdout=False, stderr=True)
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
                return stdout, stderr
            except Exception as exc:
                return "", f"Failed to read container logs: {exc}"

        return await asyncio.to_thread(_sync_logs)

    @staticmethod
    async def _get_exit_code(container: Any) -> int:
        """Retrieve the container exit code."""

        def _sync_exit_code() -> int:
            try:
                container.reload()
                return int(container.attrs.get("State", {}).get("ExitCode", 1))
            except Exception:
                return 1

        return await asyncio.to_thread(_sync_exit_code)

    @staticmethod
    async def _cleanup_container(container: Any | None) -> None:
        """Remove the container if it exists."""
        if container is None:
            return

        def _sync_remove() -> None:
            try:
                container.remove(force=True)
            except Exception as rm_exc:  # noqa: BLE001
                logger.debug("container_remove_failed", error=str(rm_exc))

        await asyncio.to_thread(_sync_remove)

    @staticmethod
    def _cleanup_tmp_dir(tmp_dir: str) -> None:
        """Remove the temporary directory tree."""
        shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _list_created_files(tmp_dir: str, original_files: list[dict[str, str]]) -> list[str]:
        """Return paths of files in *tmp_dir* that were NOT in the original file list."""
        original_basenames = {os.path.basename(f.get("path", "")) for f in original_files}
        created: list[str] = []

        for root, _dirs, filenames in os.walk(tmp_dir):
            for fn in filenames:
                if fn not in original_basenames:
                    rel = os.path.relpath(os.path.join(root, fn), tmp_dir)
                    created.append(rel)

        return created


