"""Abstract base class and result dataclass for sandbox executors.

All concrete executors (Docker, E2B) implement :class:`SandboxExecutor` so that
the :class:`SandboxManager` can route between them transparently.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class ExecutionResult:
    """Result of code execution in a sandboxed environment."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    duration_ms: float = 0.0
    files_created: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return ``True`` when the command exited cleanly and within the time limit."""
        return self.exit_code == 0 and not self.timed_out


class SandboxExecutor(abc.ABC):
    """Abstract base for sandbox executors.

    Subclasses must implement :meth:`execute` and :meth:`cleanup`.
    """

    @abc.abstractmethod
    async def execute(
        self,
        files: list[dict[str, str]],
        command: str | None = None,
        timeout_seconds: int = 60,
    ) -> ExecutionResult:
        """Execute code files in the sandbox.

        Args:
            files: List of ``{"path": "...", "content": "..."}`` dicts describing
                the files to write into the sandbox working directory.
            command: Optional shell command to run.  When *None* the executor
                auto-detects the entry-point from the file extensions.
            timeout_seconds: Maximum wall-clock execution time in seconds.

        Returns:
            An :class:`ExecutionResult` with captured stdout/stderr, exit code,
            timing information, and a list of files created during execution.
        """
        ...

    @abc.abstractmethod
    async def cleanup(self) -> None:
        """Release any resources held by this executor."""
        ...
