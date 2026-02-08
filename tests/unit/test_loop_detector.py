"""Unit tests for src.core.loop_detector.LoopDetector.

Covers iteration cap enforcement, identical-step detection, reset, and stats.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import LoopDetectedError
from src.core.loop_detector import LoopDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THREAD = "thread-ld-test"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_check_normal_iteration_passes():
    """Normal iterations below the cap should not raise."""
    detector = LoopDetector(max_iterations=5, max_identical_steps=3)
    for i in range(5):
        await detector.check(THREAD, f"step_{i}")
    # No exception means all 5 iterations passed.


async def test_check_max_iterations_exceeded_raises():
    """Exceeding max_iterations should raise LoopDetectedError."""
    detector = LoopDetector(max_iterations=3, max_identical_steps=10)

    await detector.check(THREAD, "step_a")
    await detector.check(THREAD, "step_b")
    await detector.check(THREAD, "step_c")  # iteration 3 == max, still ok

    with pytest.raises(LoopDetectedError) as exc_info:
        await detector.check(THREAD, "step_d")  # iteration 4 > 3

    assert THREAD in str(exc_info.value)
    assert exc_info.value.iteration_count == 4


async def test_check_identical_steps_detected_raises():
    """Repeating the same step more than max_identical_steps should raise."""
    detector = LoopDetector(max_iterations=100, max_identical_steps=2)

    await detector.check(THREAD, "scout")  # 1st occurrence -> count=1
    await detector.check(THREAD, "scout")  # 2nd consecutive -> count=2, equals max, ok

    with pytest.raises(LoopDetectedError) as exc_info:
        await detector.check(THREAD, "scout")  # 3rd consecutive -> count=3 > 2

    assert "repeated" in str(exc_info.value).lower() or "scout" in str(exc_info.value)
    assert exc_info.value.repeat_count == 3


async def test_check_different_steps_resets_consecutive():
    """Inserting a different step between identical ones should reset the consecutive counter."""
    detector = LoopDetector(max_iterations=100, max_identical_steps=2)

    await detector.check(THREAD, "scout")
    await detector.check(THREAD, "scout")   # 2nd consecutive -- at limit
    await detector.check(THREAD, "bid")     # different step -- resets consecutive
    await detector.check(THREAD, "scout")   # 1st consecutive again
    await detector.check(THREAD, "scout")   # 2nd consecutive -- at limit, still ok

    # The 3rd consecutive (without interruption) should raise.
    with pytest.raises(LoopDetectedError):
        await detector.check(THREAD, "scout")


async def test_reset_clears_history():
    """After reset, the thread should be treated as brand-new."""
    detector = LoopDetector(max_iterations=3, max_identical_steps=2)

    await detector.check(THREAD, "step_a")
    await detector.check(THREAD, "step_b")
    await detector.check(THREAD, "step_c")  # at max

    await detector.reset(THREAD)

    # Should work again without raising.
    await detector.check(THREAD, "step_a")
    stats = await detector.get_stats(THREAD)
    assert stats["iteration_count"] == 1


async def test_get_stats_returns_tracking_info():
    """get_stats should return iteration_count, unique_steps, and total_steps."""
    detector = LoopDetector(max_iterations=20, max_identical_steps=5)

    await detector.check(THREAD, "scout")
    await detector.check(THREAD, "bid")
    await detector.check(THREAD, "scout")

    stats = await detector.get_stats(THREAD)

    assert stats["tracked"] is True
    assert stats["thread_id"] == THREAD
    assert stats["iteration_count"] == 3
    assert stats["total_steps"] == 3
    assert stats["unique_steps"] == 2  # "scout" and "bid" hashes
    assert stats["max_iterations"] == 20
    assert stats["max_identical_steps"] == 5


async def test_get_stats_untracked_thread():
    """get_stats for a thread that was never checked should return tracked=False."""
    detector = LoopDetector()
    stats = await detector.get_stats("nonexistent-thread")

    assert stats["tracked"] is False
    assert stats["thread_id"] == "nonexistent-thread"
