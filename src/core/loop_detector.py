"""Loop detection for multi-agent workflows (Feature 7 from architecture v4.2).

Prevents two failure modes identified in the MAST Taxonomy:
1. **Iteration overflow** -- an agent runs more steps than ``max_iterations``.
2. **Step repetition** -- an agent produces identical outputs on consecutive
   invocations, indicating it is stuck.

The detector is **thread-safe**: every public method acquires an ``asyncio.Lock``
so that concurrent coroutines sharing the same ``LoopDetector`` instance do not
corrupt internal state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from src.core.exceptions import LoopDetectedError


@dataclass
class _ThreadHistory:
    """Per-thread execution tracking state."""

    iteration_count: int = 0
    step_hashes: list[str] = field(default_factory=list)
    # Maps step_hash -> consecutive repeat count
    consecutive_counts: dict[str, int] = field(default_factory=dict)
    last_step_hash: str = ""


class LoopDetector:
    """Detect and prevent infinite loops in agent execution.

    Each thread (identified by ``thread_id``) maintains its own independent
    history so that parallel workflows do not interfere with each other.

    Args:
        max_iterations: Absolute cap on the number of steps a single thread
            may execute before raising ``LoopDetectedError``.
        max_identical_steps: Maximum number of times the same step output may
            appear consecutively.  When exceeded a ``LoopDetectedError`` is raised.
    """

    def __init__(
        self,
        *,
        max_iterations: int = 10,
        max_identical_steps: int = 2,
    ) -> None:
        self.max_iterations = max_iterations
        self.max_identical_steps = max_identical_steps
        self._histories: dict[str, _ThreadHistory] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check(
        self,
        thread_id: str,
        current_step: str,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Validate that the current step does not violate loop invariants.

        Args:
            thread_id: Unique workflow/thread identifier.
            current_step: Agent name or step label being executed.
            state: Optional snapshot of the agent state; when supplied it is
                hashed together with *current_step* for higher-fidelity
                duplicate detection.

        Raises:
            LoopDetectedError: When the iteration cap is exceeded or identical
                consecutive steps are detected.
        """
        step_hash = self._compute_hash(current_step, state)

        async with self._lock:
            history = self._histories.setdefault(thread_id, _ThreadHistory())
            history.iteration_count += 1

            # --- Check 1: absolute iteration cap ---
            if history.iteration_count > self.max_iterations:
                raise LoopDetectedError(
                    f"Thread {thread_id} exceeded max iterations ({self.max_iterations})",
                    agent_name=current_step,
                    thread_id=thread_id,
                    iteration_count=history.iteration_count,
                    step_hash=step_hash,
                )

            # --- Check 2: identical consecutive steps ---
            if step_hash == history.last_step_hash:
                history.consecutive_counts[step_hash] = history.consecutive_counts.get(step_hash, 1) + 1
                if history.consecutive_counts[step_hash] > self.max_identical_steps:
                    raise LoopDetectedError(
                        (
                            f"Step '{current_step}' repeated "
                            f"{history.consecutive_counts[step_hash]} consecutive times "
                            f"in thread {thread_id}"
                        ),
                        agent_name=current_step,
                        thread_id=thread_id,
                        iteration_count=history.iteration_count,
                        step_hash=step_hash,
                        repeat_count=history.consecutive_counts[step_hash],
                    )
            else:
                # Reset consecutive counter when the step changes
                history.consecutive_counts[step_hash] = 1

            history.last_step_hash = step_hash
            history.step_hashes.append(step_hash)

    async def reset(self, thread_id: str) -> None:
        """Clear all tracking state for a given thread.

        Typically called when a workflow completes or is explicitly restarted.
        """
        async with self._lock:
            self._histories.pop(thread_id, None)

    async def get_stats(self, thread_id: str) -> dict[str, Any]:
        """Return diagnostic info for a thread's execution history."""
        async with self._lock:
            history = self._histories.get(thread_id)
            if history is None:
                return {"thread_id": thread_id, "tracked": False}
            return {
                "thread_id": thread_id,
                "tracked": True,
                "iteration_count": history.iteration_count,
                "unique_steps": len(set(history.step_hashes)),
                "total_steps": len(history.step_hashes),
                "max_iterations": self.max_iterations,
                "max_identical_steps": self.max_identical_steps,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_hash(step_label: str, state: dict[str, Any] | None) -> str:
        """Deterministic hash combining the step label and optional state snapshot."""
        payload = step_label
        if state is not None:
            # Only include serialisable, semantically relevant keys
            relevant_keys = ("current_agent", "current_task", "next_agent", "artifacts")
            filtered = {k: state[k] for k in relevant_keys if k in state}
            payload += json.dumps(filtered, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]
