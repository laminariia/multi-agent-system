"""Circuit Breaker pattern for platform adapter resilience.

Implements a three-state machine (CLOSED -> OPEN -> HALF_OPEN) to prevent
cascading failures when external platform APIs become unavailable.

Each platform gets its own CircuitBreaker instance for isolation.
Uses time.monotonic() for timing (immune to system clock changes).

Success-based reset: in HALF_OPEN state, requires ``success_threshold``
consecutive successful requests before transitioning back to CLOSED
(default 2).  A single failure in HALF_OPEN reopens immediately.
"""

from __future__ import annotations

import enum
import time
from types import TracebackType


class CircuitState(enum.Enum):
    """Circuit breaker states."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpen(Exception):
    """Raised when attempting to execute through an open circuit breaker."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN")


class CircuitBreaker:
    """Per-platform circuit breaker with configurable thresholds.

    Args:
        name: Identifier (typically platform name).
        failure_threshold: Consecutive failures before opening.
        recovery_timeout: Seconds to wait in OPEN before transitioning to HALF_OPEN.
        half_open_max: Max concurrent probe requests allowed in HALF_OPEN state.
        success_threshold: Consecutive successes in HALF_OPEN required to close
            the circuit.  Defaults to 2.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 300.0,
        half_open_max: int = 1,
        success_threshold: int = 2,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._success_threshold = success_threshold

        self._failure_count: int = 0
        self._state: CircuitState = CircuitState.CLOSED
        self._opened_at: float | None = None
        self._last_failure_at: float | None = None
        self._half_open_calls: int = 0
        self._half_open_successes: int = 0

    # ------------------------------------------------------------------
    # State property (computes HALF_OPEN dynamically from OPEN + timeout)
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._half_open_successes = 0
        return self._state

    # ------------------------------------------------------------------
    # Recording outcomes
    # ------------------------------------------------------------------

    def record_failure(self) -> None:
        """Record a failed request. May transition CLOSED->OPEN or HALF_OPEN->OPEN."""
        self._last_failure_at = time.monotonic()
        current = self.state

        if current == CircuitState.HALF_OPEN:
            # Probe failed -- reopen immediately, reset success counter
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._half_open_successes = 0
            return

        # CLOSED state -- increment and maybe open
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def record_success(self) -> None:
        """Record a successful request.

        In HALF_OPEN state, increments the success counter.  Once the
        counter reaches ``success_threshold``, the circuit transitions
        back to CLOSED.

        In CLOSED state, simply resets the failure counter.
        """
        current = self.state

        if current == CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self._success_threshold:
                # Enough consecutive successes -- close the circuit
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._opened_at = None
                self._half_open_calls = 0
                self._half_open_successes = 0
            return

        # CLOSED state -- reset failure count
        self._failure_count = 0

    # ------------------------------------------------------------------
    # Execution guards
    # ------------------------------------------------------------------

    def can_execute(self) -> bool:
        """Check if a request is allowed through the circuit breaker."""
        current = self.state

        if current == CircuitState.CLOSED:
            return True

        if current == CircuitState.HALF_OPEN:
            return self._half_open_calls < self._half_open_max

        # OPEN
        return False

    def ensure_closed(self) -> None:
        """Raise CircuitBreakerOpen if circuit is not allowing traffic."""
        if not self.can_execute():
            raise CircuitBreakerOpen(self._name)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, object]:
        """Operational statistics for monitoring/dashboards."""
        return {
            "name": self._name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "last_failure_at": self._last_failure_at,
            "opened_at": self._opened_at,
            "half_open_successes": self._half_open_successes,
        }

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> CircuitBreaker:
        self.ensure_closed()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()
        # Never suppress the exception
        return False
