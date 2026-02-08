"""Sandbox manager -- smart routing between Docker and E2B executors.

The manager tries E2B first (default, for Railway compatibility) and falls
back to Docker when E2B is unavailable.  Set ``prefer_docker=True`` to
reverse the order for local dev or VPS deploys.  If both are unavailable the
caller receives an :class:`ExecutionResult` with a descriptive error.
"""

from __future__ import annotations

import os

import structlog

from src.sandbox.base import ExecutionResult, SandboxExecutor
from src.sandbox.docker_executor import DockerExecutor
from src.sandbox.e2b_executor import E2BExecutor

logger = structlog.get_logger(__name__)


class SandboxManager:
    """Route code execution to the best available sandbox backend.

    Parameters
    ----------
    prefer_docker:
        When ``True`` Docker is tried first.  Defaults to ``False`` so that
        E2B is preferred (required on Railway where Docker daemon is unavailable).
    e2b_api_key:
        Explicit E2B API key.  Falls back to ``E2B_API_KEY`` env var.
    docker_image:
        Docker image name.  Defaults to ``mas-sandbox:latest``.
    """

    def __init__(
        self,
        prefer_docker: bool = False,
        e2b_api_key: str | None = None,
        docker_image: str = "mas-sandbox:latest",
    ) -> None:
        self._prefer_docker = prefer_docker
        self._e2b_api_key = e2b_api_key or os.environ.get("E2B_API_KEY", "")
        self._docker_image = docker_image
        self._log = logger.bind(component="sandbox_manager")

        self._docker: SandboxExecutor | None = None
        self._e2b: SandboxExecutor | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_code(
        self,
        files: list[dict[str, str]],
        command: str | None = None,
        timeout_seconds: int = 60,
    ) -> ExecutionResult:
        """Execute *files* in the most appropriate sandbox.

        Routing logic:
        1. If ``prefer_docker`` is ``True``, try Docker first.
        2. On Docker failure (e.g. daemon not running), fall back to E2B if
           an API key is available.
        3. If neither backend succeeds, return an error result.
        """
        if not files:
            return ExecutionResult(stderr="No files provided for execution", exit_code=1)

        primary, fallback = self._choose_order()

        # Try primary executor.
        result = await self._try_executor(
            label=primary,
            files=files,
            command=command,
            timeout_seconds=timeout_seconds,
        )
        if result is not None:
            return result

        # Primary failed to initialise -- try fallback.
        if fallback is not None:
            self._log.info("sandbox_fallback", primary=primary, fallback=fallback)
            result = await self._try_executor(
                label=fallback,
                files=files,
                command=command,
                timeout_seconds=timeout_seconds,
            )
            if result is not None:
                return result

        # Both unavailable.
        self._log.error("sandbox_all_unavailable")
        return ExecutionResult(
            stderr="No sandbox backend available (Docker not running and E2B API key not set)",
            exit_code=1,
        )

    async def cleanup(self) -> None:
        """Release resources held by both executors."""
        if self._docker is not None:
            await self._docker.cleanup()
        if self._e2b is not None:
            await self._e2b.cleanup()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _choose_order(self) -> tuple[str, str | None]:
        """Return ``(primary, fallback)`` executor labels."""
        if self._prefer_docker:
            fallback = "e2b" if self._e2b_api_key else None
            return "docker", fallback
        else:
            fallback = "docker"
            return "e2b", fallback

    async def _try_executor(
        self,
        *,
        label: str,
        files: list[dict[str, str]],
        command: str | None,
        timeout_seconds: int,
    ) -> ExecutionResult | None:
        """Attempt execution with the named backend.

        Returns ``None`` if the backend cannot be initialised (e.g. Docker
        daemon not running, E2B SDK missing).  Returns an
        :class:`ExecutionResult` on success *or* on a legitimate execution
        failure (non-zero exit code).
        """
        try:
            executor = self._get_executor(label)
            if executor is None:
                return None

            self._log.info(
                "sandbox_execute",
                backend=label,
                file_count=len(files),
                timeout=timeout_seconds,
            )

            result = await executor.execute(
                files=files,
                command=command,
                timeout_seconds=timeout_seconds,
            )

            self._log.info(
                "sandbox_result",
                backend=label,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
            )
            return result

        except Exception as exc:
            self._log.warning(
                "sandbox_backend_error",
                backend=label,
                error=str(exc),
            )
            return None

    def _get_executor(self, label: str) -> SandboxExecutor | None:
        """Lazily create and return the named executor, or ``None`` on failure."""
        if label == "docker":
            return self._get_docker()
        if label == "e2b":
            return self._get_e2b()
        return None

    def _get_docker(self) -> SandboxExecutor | None:
        """Return a :class:`DockerExecutor`, verifying the daemon is reachable."""
        if self._docker is not None:
            return self._docker

        try:
            # Verify Docker daemon connectivity at construction time.
            self._check_docker_available()
            self._docker = DockerExecutor(image=self._docker_image)
            return self._docker
        except Exception as exc:
            self._log.warning("docker_unavailable", error=str(exc))
            return None

    def _get_e2b(self) -> SandboxExecutor | None:
        """Return an :class:`E2BExecutor` if the SDK and API key are available."""
        if self._e2b is not None:
            return self._e2b

        if not self._e2b_api_key:
            self._log.debug("e2b_no_api_key")
            return None

        try:
            self._e2b = E2BExecutor(api_key=self._e2b_api_key)
            return self._e2b
        except Exception as exc:
            self._log.warning("e2b_init_failed", error=str(exc))
            return None

    @staticmethod
    def _check_docker_available() -> None:
        """Raise if the Docker daemon is not reachable.

        Imports ``docker`` lazily so the module remains importable even when
        the SDK is not installed.
        """
        import docker  # noqa: PLC0415

        client = docker.from_env()
        try:
            client.ping()
        finally:
            client.close()
