"""Comprehensive verification tests for AdaptiveRateLimiter scaling logic.

Verifies all four core adaptive behaviors:
1. Success adaptation: rate increases by x1.1 after 5 consecutive successes (capped at max)
2. Failure adaptation: rate halves (x0.5) on 429/timeout (floored at min)
3. Captcha/ban pause: drop to min_rpm + 30-min pause + Telegram alert
4. Valkey persistence: state saved/loaded across restarts with 24h TTL

Also tests cross-cutting scenarios: success-failure interleaving, multi-platform
isolation, the retry_on_transient decorator integration, and edge cases.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from src.adapters.rate_limiter import (
    _BACKOFF_FACTOR,
    _BAN_PAUSE_SECONDS,
    _DEFAULT_LIMITS,
    _SUCCESS_INCREASE,
    _VALKEY_PREFIX,
    AdaptiveRateLimiter,
    PlatformRateState,
    retry_on_transient,
)
from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
    PlatformRateLimitError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_limiter(
    *,
    valkey: AsyncMock | None = None,
    telegram_notify: AsyncMock | None = None,
) -> AdaptiveRateLimiter:
    """Create an AdaptiveRateLimiter with optional mocked dependencies."""
    return AdaptiveRateLimiter(valkey=valkey, telegram_notify=telegram_notify)


# ===================================================================
# 1. SUCCESS ADAPTATION  (x1.1 after 5 consecutive successes)
# ===================================================================


class TestSuccessAdaptation:
    """Verify rate increases by x1.1 after 5 consecutive successes."""

    @pytest.mark.asyncio
    async def test_rate_unchanged_after_1_success(self) -> None:
        """Rate must NOT change after a single success."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        original_rpm = state.current_rpm

        await limiter.record_success("freelancer")

        assert state.current_rpm == original_rpm
        assert state.consecutive_successes == 1

    @pytest.mark.asyncio
    async def test_rate_unchanged_after_4_successes(self) -> None:
        """Rate must NOT increase after only 4 successes."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        original_rpm = state.current_rpm

        for _ in range(4):
            await limiter.record_success("freelancer")

        assert state.current_rpm == original_rpm
        assert state.consecutive_successes == 4

    @pytest.mark.asyncio
    async def test_rate_increases_after_5_successes(self) -> None:
        """Rate must increase by exactly x1.1 after 5 consecutive successes."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        original_rpm = state.current_rpm

        for _ in range(5):
            await limiter.record_success("freelancer")

        expected = original_rpm * _SUCCESS_INCREASE
        assert state.current_rpm == pytest.approx(expected)
        assert state.consecutive_successes == 5

    @pytest.mark.asyncio
    async def test_rate_increases_again_after_10_successes(self) -> None:
        """Rate must increase a second time at 10 consecutive successes."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        original_rpm = state.current_rpm

        for _ in range(10):
            await limiter.record_success("freelancer")

        expected = original_rpm * _SUCCESS_INCREASE * _SUCCESS_INCREASE
        assert state.current_rpm == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_rate_capped_at_max(self) -> None:
        """Rate must not exceed platform max_rpm regardless of successes."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        state.current_rpm = state.max_rpm  # already at ceiling

        for _ in range(20):
            await limiter.record_success("freelancer")

        assert state.current_rpm == state.max_rpm

    @pytest.mark.asyncio
    async def test_rate_capped_when_increase_would_exceed_max(self) -> None:
        """When x1.1 would exceed max, rate must clamp to max, not overshoot."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        # Set current just below max so x1.1 would overshoot.
        state.current_rpm = state.max_rpm - 0.5

        for _ in range(5):
            await limiter.record_success("freelancer")

        assert state.current_rpm == state.max_rpm

    @pytest.mark.asyncio
    async def test_success_counter_resets_on_rate_limit_failure(self) -> None:
        """A rate-limit failure must reset the consecutive success counter."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")

        for _ in range(3):
            await limiter.record_success("freelancer")
        assert state.consecutive_successes == 3

        await limiter.record_rate_limit("freelancer")
        assert state.consecutive_successes == 0

    @pytest.mark.asyncio
    async def test_success_counter_resets_on_ban(self) -> None:
        """A ban event must reset the consecutive success counter."""
        limiter = _make_limiter()

        for _ in range(4):
            await limiter.record_success("freelancer")

        await limiter.record_ban("freelancer", reason="test")
        state = limiter._get_state("freelancer")
        assert state.consecutive_successes == 0

    @pytest.mark.asyncio
    async def test_no_increase_after_4_success_then_failure_then_4_success(self) -> None:
        """Interrupted streak: 4 success + failure + 4 success = no increase."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        original_rpm = state.current_rpm

        for _ in range(4):
            await limiter.record_success("freelancer")
        await limiter.record_rate_limit("freelancer")
        # Rate was halved, record the halved value.
        rpm_after_failure = state.current_rpm

        for _ in range(4):
            await limiter.record_success("freelancer")

        # Should still be at the halved rate (4 < 5 threshold).
        assert state.current_rpm == rpm_after_failure
        assert state.consecutive_successes == 4

    @pytest.mark.asyncio
    async def test_increase_factor_value(self) -> None:
        """The success increase constant is exactly 1.1."""
        assert _SUCCESS_INCREASE == 1.1


# ===================================================================
# 2. FAILURE ADAPTATION  (x0.5 on 429/timeout)
# ===================================================================


class TestFailureAdaptation:
    """Verify rate decreases by x0.5 on 429 errors and timeouts."""

    @pytest.mark.asyncio
    async def test_rate_halved_on_429(self) -> None:
        """Rate must halve on a single rate-limit (429) error."""
        limiter = _make_limiter()
        state = limiter._get_state("kwork")
        original_rpm = state.current_rpm

        await limiter.record_rate_limit("kwork")

        expected = original_rpm * _BACKOFF_FACTOR
        assert state.current_rpm == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_rate_halved_on_timeout(self) -> None:
        """Timeout must be treated identically to a 429 (delegates to record_rate_limit)."""
        limiter = _make_limiter()
        state = limiter._get_state("kwork")
        original_rpm = state.current_rpm

        await limiter.record_timeout("kwork")

        expected = original_rpm * _BACKOFF_FACTOR
        assert state.current_rpm == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_rate_floored_at_min(self) -> None:
        """Rate must not go below platform min_rpm even with repeated failures."""
        limiter = _make_limiter()
        state = limiter._get_state("kwork")
        min_rpm = state.min_rpm

        # Halve enough times to reach floor.
        for _ in range(20):
            await limiter.record_rate_limit("kwork")

        assert state.current_rpm == min_rpm

    @pytest.mark.asyncio
    async def test_consecutive_failures_increment(self) -> None:
        """Each failure must increment the consecutive_failures counter."""
        limiter = _make_limiter()

        await limiter.record_rate_limit("freelancer")
        await limiter.record_rate_limit("freelancer")
        await limiter.record_rate_limit("freelancer")

        state = limiter._get_state("freelancer")
        assert state.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_total_429s_counter_tracks_all_rate_limits(self) -> None:
        """total_429s must count all rate-limit events (not reset by success)."""
        limiter = _make_limiter()

        await limiter.record_rate_limit("freelancer")
        await limiter.record_success("freelancer")
        await limiter.record_rate_limit("freelancer")

        state = limiter._get_state("freelancer")
        assert state.total_429s == 2

    @pytest.mark.asyncio
    async def test_backoff_factor_value(self) -> None:
        """The backoff factor constant is exactly 0.5."""
        assert _BACKOFF_FACTOR == 0.5

    @pytest.mark.asyncio
    async def test_multiple_halves_are_cumulative(self) -> None:
        """Two consecutive 429s should quarter the rate (0.5 * 0.5 = 0.25)."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        original_rpm = state.current_rpm

        await limiter.record_rate_limit("freelancer")
        await limiter.record_rate_limit("freelancer")

        expected = max(original_rpm * _BACKOFF_FACTOR * _BACKOFF_FACTOR, state.min_rpm)
        assert state.current_rpm == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_timeout_increments_429_counter(self) -> None:
        """Timeouts should also increment total_429s since they delegate to record_rate_limit."""
        limiter = _make_limiter()

        await limiter.record_timeout("freelancer")

        state = limiter._get_state("freelancer")
        assert state.total_429s == 1


# ===================================================================
# 3. CAPTCHA / BAN PAUSE  (drop to min + 30-min pause + alert)
# ===================================================================


class TestCaptchaBanPause:
    """Verify 30-minute pause on captcha/ban detection."""

    @pytest.mark.asyncio
    async def test_ban_drops_to_min_rate(self) -> None:
        """Rate must drop to min_rpm on ban."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")
        assert state.current_rpm > state.min_rpm  # precondition

        await limiter.record_ban("freelancer", reason="IP blocked")

        assert state.current_rpm == state.min_rpm

    @pytest.mark.asyncio
    async def test_ban_triggers_30min_pause(self) -> None:
        """Platform must be paused for approximately 30 minutes."""
        limiter = _make_limiter()

        before = time.monotonic()
        await limiter.record_ban("freelancer", reason="captcha")
        after = time.monotonic()

        state = limiter._get_state("freelancer")
        # paused_until should be ~30min from now.
        pause_duration = state.paused_until - before
        assert pause_duration >= _BAN_PAUSE_SECONDS - 1  # allow 1s tolerance
        assert pause_duration <= _BAN_PAUSE_SECONDS + (after - before) + 1

    @pytest.mark.asyncio
    async def test_ban_pause_constant_is_30_minutes(self) -> None:
        """The ban pause constant must be exactly 1800 seconds (30 minutes)."""
        assert _BAN_PAUSE_SECONDS == 30 * 60
        assert _BAN_PAUSE_SECONDS == 1800

    @pytest.mark.asyncio
    async def test_ban_triggers_telegram_alert(self) -> None:
        """Telegram notification must be sent on ban with platform and reason."""
        notify = AsyncMock()
        limiter = _make_limiter(telegram_notify=notify)

        await limiter.record_ban("kwork", reason="CAPTCHA challenge")

        notify.assert_awaited_once()
        msg = notify.call_args[0][0]
        assert "kwork" in msg
        assert "CAPTCHA challenge" in msg
        assert "30" in msg  # 30 minutes

    @pytest.mark.asyncio
    async def test_ban_telegram_alert_includes_ban_count(self) -> None:
        """Telegram alert must include total ban count."""
        notify = AsyncMock()
        limiter = _make_limiter(telegram_notify=notify)

        await limiter.record_ban("freelancer", reason="test1")
        await limiter.record_ban("freelancer", reason="test2")

        # Second call should show total_bans=2.
        second_msg = notify.call_args_list[1][0][0]
        assert "2" in second_msg

    @pytest.mark.asyncio
    async def test_requests_blocked_during_pause(self) -> None:
        """acquire() must raise PlatformBannedError while paused."""
        limiter = _make_limiter()

        await limiter.record_ban("freelancer", reason="banned")

        with pytest.raises(PlatformBannedError, match="paused"):
            await limiter.acquire("freelancer")

    @pytest.mark.asyncio
    async def test_requests_allowed_after_pause_expires(self) -> None:
        """acquire() must succeed once the pause window has elapsed."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")

        # Simulate a ban that already expired.
        state.paused_until = time.monotonic() - 1
        state.current_rpm = state.min_rpm

        await limiter.acquire("freelancer")
        assert state.total_requests == 1

    @pytest.mark.asyncio
    async def test_ban_without_telegram_notify_does_not_raise(self) -> None:
        """When no telegram_notify callback is set, ban must still succeed (log-only)."""
        limiter = _make_limiter(telegram_notify=None)

        await limiter.record_ban("freelancer", reason="test")

        state = limiter._get_state("freelancer")
        assert state.total_bans == 1

    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_break_ban_recording(self) -> None:
        """If Telegram notification fails, ban state must still be recorded."""
        notify = AsyncMock(side_effect=ConnectionError("telegram down"))
        limiter = _make_limiter(telegram_notify=notify)

        await limiter.record_ban("freelancer", reason="banned")

        state = limiter._get_state("freelancer")
        assert state.total_bans == 1
        assert state.current_rpm == state.min_rpm
        assert state.paused_until > time.monotonic()

    @pytest.mark.asyncio
    async def test_ban_increments_consecutive_failures(self) -> None:
        """Ban must increment consecutive_failures counter."""
        limiter = _make_limiter()

        await limiter.record_ban("freelancer", reason="test")

        state = limiter._get_state("freelancer")
        assert state.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_ban_reason_defaults_to_unknown(self) -> None:
        """When reason is empty, Telegram alert must show 'unknown'."""
        notify = AsyncMock()
        limiter = _make_limiter(telegram_notify=notify)

        await limiter.record_ban("freelancer", reason="")

        msg = notify.call_args[0][0]
        assert "unknown" in msg


# ===================================================================
# 4. VALKEY PERSISTENCE  (state survives restarts)
# ===================================================================


class TestValkeyPersistence:
    """Verify rate state persists across restarts via Valkey."""

    @pytest.mark.asyncio
    async def test_state_saved_to_valkey_on_success(self) -> None:
        """record_success must persist state to Valkey."""
        valkey = AsyncMock()
        limiter = _make_limiter(valkey=valkey)

        await limiter.record_success("freelancer")

        valkey.hset.assert_awaited_once()
        call_kwargs = valkey.hset.call_args
        key = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("name", "")
        assert key == f"{_VALKEY_PREFIX}:freelancer"

    @pytest.mark.asyncio
    async def test_state_saved_to_valkey_on_rate_limit(self) -> None:
        """record_rate_limit must persist state to Valkey."""
        valkey = AsyncMock()
        limiter = _make_limiter(valkey=valkey)

        await limiter.record_rate_limit("freelancer")

        valkey.hset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_state_saved_to_valkey_on_ban(self) -> None:
        """record_ban must persist state to Valkey."""
        valkey = AsyncMock()
        limiter = _make_limiter(valkey=valkey)

        await limiter.record_ban("freelancer", reason="test")

        valkey.hset.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_state_saved_with_24h_ttl(self) -> None:
        """Valkey key must be set with 24-hour (86400s) TTL."""
        valkey = AsyncMock()
        limiter = _make_limiter(valkey=valkey)

        await limiter.record_success("freelancer")

        valkey.expire.assert_awaited_once()
        ttl_arg = valkey.expire.call_args[0][1]
        assert ttl_arg == 86400

    @pytest.mark.asyncio
    async def test_state_restored_from_valkey(self) -> None:
        """Rates must be restored from Valkey on _load_state."""
        valkey = AsyncMock()
        valkey.hgetall.return_value = {
            "current_rpm": "7.5",
            "consecutive_successes": "4",
            "consecutive_failures": "2",
            "paused_until": "0.0",
            "total_requests": "250",
            "total_429s": "10",
            "total_bans": "3",
        }
        limiter = _make_limiter(valkey=valkey)

        await limiter._load_state("freelancer")

        state = limiter._get_state("freelancer")
        assert state.current_rpm == 7.5
        assert state.consecutive_successes == 4
        assert state.consecutive_failures == 2
        assert state.total_requests == 250
        assert state.total_429s == 10
        assert state.total_bans == 3

    @pytest.mark.asyncio
    async def test_expired_state_uses_defaults(self) -> None:
        """If Valkey returns empty (expired key), defaults must be used."""
        valkey = AsyncMock()
        valkey.hgetall.return_value = {}
        limiter = _make_limiter(valkey=valkey)

        await limiter._load_state("freelancer")

        state = limiter._get_state("freelancer")
        assert state.current_rpm == _DEFAULT_LIMITS["freelancer"]["initial_rpm"]
        assert state.consecutive_successes == 0

    @pytest.mark.asyncio
    async def test_load_clamps_rpm_above_max(self) -> None:
        """Valkey-loaded rpm above max must be clamped to max_rpm."""
        valkey = AsyncMock()
        valkey.hgetall.return_value = {
            "current_rpm": "9999.0",
            "consecutive_successes": "0",
            "consecutive_failures": "0",
            "paused_until": "0",
            "total_requests": "0",
            "total_429s": "0",
            "total_bans": "0",
        }
        limiter = _make_limiter(valkey=valkey)

        await limiter._load_state("freelancer")

        state = limiter._get_state("freelancer")
        assert state.current_rpm == state.max_rpm

    @pytest.mark.asyncio
    async def test_load_clamps_rpm_below_min(self) -> None:
        """Valkey-loaded rpm below min must be clamped to min_rpm."""
        valkey = AsyncMock()
        valkey.hgetall.return_value = {
            "current_rpm": "0.001",
            "consecutive_successes": "0",
            "consecutive_failures": "0",
            "paused_until": "0",
            "total_requests": "0",
            "total_429s": "0",
            "total_bans": "0",
        }
        limiter = _make_limiter(valkey=valkey)

        await limiter._load_state("freelancer")

        state = limiter._get_state("freelancer")
        assert state.current_rpm == state.min_rpm

    @pytest.mark.asyncio
    async def test_load_handles_bytes_keys(self) -> None:
        """Valkey with decode_responses=False returns bytes -- must be handled."""
        valkey = AsyncMock()
        valkey.hgetall.return_value = {
            b"current_rpm": b"12.0",
            b"consecutive_successes": b"2",
            b"consecutive_failures": b"0",
            b"paused_until": b"0",
            b"total_requests": b"50",
            b"total_429s": b"1",
            b"total_bans": b"0",
        }
        limiter = _make_limiter(valkey=valkey)

        await limiter._load_state("freelancer")

        state = limiter._get_state("freelancer")
        assert state.current_rpm == 12.0
        assert state.consecutive_successes == 2
        assert state.total_requests == 50

    @pytest.mark.asyncio
    async def test_save_skipped_without_valkey(self) -> None:
        """When no Valkey client is provided, _save_state must be a no-op."""
        limiter = _make_limiter(valkey=None)
        limiter._get_state("freelancer")

        # Must not raise.
        await limiter._save_state("freelancer")

    @pytest.mark.asyncio
    async def test_load_skipped_without_valkey(self) -> None:
        """When no Valkey client is provided, _load_state must be a no-op."""
        limiter = _make_limiter(valkey=None)

        await limiter._load_state("freelancer")

        # State should be at defaults.
        state = limiter._get_state("freelancer")
        assert state.current_rpm == _DEFAULT_LIMITS["freelancer"]["initial_rpm"]

    @pytest.mark.asyncio
    async def test_valkey_connection_error_on_save_is_swallowed(self) -> None:
        """Connection error during save must be swallowed (logged, not raised)."""
        valkey = AsyncMock()
        valkey.hset.side_effect = OSError("connection reset")
        limiter = _make_limiter(valkey=valkey)

        limiter._get_state("freelancer")
        await limiter._save_state("freelancer")  # must not raise

    @pytest.mark.asyncio
    async def test_valkey_connection_error_on_load_is_swallowed(self) -> None:
        """Connection error during load must be swallowed (logged, not raised)."""
        valkey = AsyncMock()
        valkey.hgetall.side_effect = OSError("connection refused")
        limiter = _make_limiter(valkey=valkey)

        await limiter._load_state("freelancer")  # must not raise

    @pytest.mark.asyncio
    async def test_save_state_serializes_all_fields(self) -> None:
        """All state fields must be serialized to the Valkey hash."""
        valkey = AsyncMock()
        limiter = _make_limiter(valkey=valkey)

        state = limiter._get_state("freelancer")
        state.current_rpm = 12.5
        state.consecutive_successes = 3
        state.consecutive_failures = 1
        state.paused_until = 99999.0
        state.total_requests = 42
        state.total_429s = 7
        state.total_bans = 2

        await limiter._save_state("freelancer")

        call_kwargs = valkey.hset.call_args
        mapping = (
            call_kwargs[1].get("mapping") if call_kwargs[1] else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
        )
        # Handle both positional and keyword-only calling patterns.
        if not mapping and call_kwargs[1]:
            mapping = call_kwargs[1].get("mapping", {})

        assert mapping["current_rpm"] == "12.5"
        assert mapping["consecutive_successes"] == "3"
        assert mapping["consecutive_failures"] == "1"
        assert mapping["total_requests"] == "42"
        assert mapping["total_429s"] == "7"
        assert mapping["total_bans"] == "2"


# ===================================================================
# 5. CROSS-CUTTING / INTEGRATION SCENARIOS
# ===================================================================


class TestCrossCuttingBehavior:
    """Integration-style tests verifying interactions between behaviors."""

    @pytest.mark.asyncio
    async def test_multi_platform_isolation(self) -> None:
        """Rate changes on one platform must not affect another."""
        limiter = _make_limiter()

        # Hammer kwork with failures.
        for _ in range(5):
            await limiter.record_rate_limit("kwork")

        # Freelancer should remain untouched.
        fl_state = limiter._get_state("freelancer")
        assert fl_state.current_rpm == _DEFAULT_LIMITS["freelancer"]["initial_rpm"]
        assert fl_state.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_recovery_after_rate_limit_via_successes(self) -> None:
        """After being halved, 5 consecutive successes must start recovery."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")

        await limiter.record_rate_limit("freelancer")
        rpm_after_failure = state.current_rpm

        for _ in range(5):
            await limiter.record_success("freelancer")

        expected = rpm_after_failure * _SUCCESS_INCREASE
        assert state.current_rpm == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_recovery_after_ban_via_successes(self) -> None:
        """After a ban expires, successes must gradually increase rate from min."""
        limiter = _make_limiter()
        state = limiter._get_state("freelancer")

        await limiter.record_ban("freelancer", reason="test")
        assert state.current_rpm == state.min_rpm

        # Simulate pause expiry.
        state.paused_until = time.monotonic() - 1

        for _ in range(5):
            await limiter.record_success("freelancer")

        expected = state.min_rpm * _SUCCESS_INCREASE
        assert state.current_rpm == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_full_lifecycle_success_failure_ban_recovery(self) -> None:
        """Full lifecycle: successes -> failure -> ban -> expire -> recovery."""
        limiter = _make_limiter()
        state = limiter._get_state("fl_ru")
        initial_rpm = state.current_rpm  # 5.0

        # Phase 1: 5 successes -> rate up.
        for _ in range(5):
            await limiter.record_success("fl_ru")
        assert state.current_rpm == pytest.approx(initial_rpm * _SUCCESS_INCREASE)

        # Phase 2: rate limit -> halve.
        rpm_before_429 = state.current_rpm
        await limiter.record_rate_limit("fl_ru")
        assert state.current_rpm == pytest.approx(rpm_before_429 * _BACKOFF_FACTOR)

        # Phase 3: ban -> drop to min + pause.
        await limiter.record_ban("fl_ru", reason="blocked")
        assert state.current_rpm == state.min_rpm
        assert state.paused_until > time.monotonic()

        # Phase 4: expire pause, start recovery.
        state.paused_until = time.monotonic() - 1
        for _ in range(5):
            await limiter.record_success("fl_ru")
        assert state.current_rpm == pytest.approx(state.min_rpm * _SUCCESS_INCREASE)

    @pytest.mark.asyncio
    async def test_get_stats_reflects_paused_state(self) -> None:
        """get_stats must reflect is_paused=True while platform is paused."""
        limiter = _make_limiter()

        await limiter.record_ban("freelancer", reason="test")

        stats = limiter.get_stats("freelancer")
        assert stats["is_paused"] is True
        assert stats["paused_remaining_s"] > 0
        assert stats["total_bans"] == 1
        assert stats["current_rpm"] == _DEFAULT_LIMITS["freelancer"]["min_rpm"]

    @pytest.mark.asyncio
    async def test_get_stats_reflects_active_state(self) -> None:
        """get_stats must show is_paused=False for active platforms."""
        limiter = _make_limiter()
        limiter._get_state("freelancer")

        stats = limiter.get_stats("freelancer")
        assert stats["is_paused"] is False
        assert stats["paused_remaining_s"] == 0

    @pytest.mark.asyncio
    async def test_acquire_increments_total_requests(self) -> None:
        """Each acquire call must increment total_requests."""
        limiter = _make_limiter()

        await limiter.acquire("freelancer")
        await limiter.acquire("freelancer")

        state = limiter._get_state("freelancer")
        assert state.total_requests == 2

    @pytest.mark.asyncio
    async def test_unknown_platform_uses_safe_defaults(self) -> None:
        """Unknown platforms must get safe fallback limits (10/30/2 rpm)."""
        limiter = _make_limiter()
        state = limiter._get_state("totally_new_platform")

        assert state.current_rpm == 10.0
        assert state.max_rpm == 30.0
        assert state.min_rpm == 2.0


# ===================================================================
# 6. RETRY DECORATOR INTEGRATION with RATE LIMITER
# ===================================================================


class TestRetryDecoratorWithRateLimiter:
    """Verify retry_on_transient decorator correctly feeds the limiter."""

    @pytest.mark.asyncio
    async def test_decorator_records_success(self) -> None:
        """Successful call must record_success on the limiter."""
        limiter = _make_limiter()

        @retry_on_transient(max_retries=1, rate_limiter=limiter, platform="freelancer")
        async def good_func() -> str:
            return "ok"

        await good_func()
        state = limiter._get_state("freelancer")
        assert state.consecutive_successes == 1

    @pytest.mark.asyncio
    async def test_decorator_records_rate_limit_then_success(self) -> None:
        """Retry after 429 must record both the failure and the eventual success."""
        limiter = _make_limiter()
        calls = 0

        @retry_on_transient(
            max_retries=2,
            initial_delay=0.01,
            rate_limiter=limiter,
            platform="kwork",
        )
        async def flaky_func() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise PlatformRateLimitError(platform="kwork", operation="test")
            return "ok"

        await flaky_func()
        state = limiter._get_state("kwork")
        assert state.total_429s == 1
        assert state.consecutive_successes == 1  # success after retry

    @pytest.mark.asyncio
    async def test_decorator_records_ban_and_does_not_retry(self) -> None:
        """Ban exceptions must record_ban and NOT retry."""
        limiter = _make_limiter()
        calls = 0

        @retry_on_transient(max_retries=3, rate_limiter=limiter, platform="freelancer")
        async def banned_func() -> str:
            nonlocal calls
            calls += 1
            raise PlatformBannedError(platform="freelancer", operation="test")

        with pytest.raises(PlatformBannedError):
            await banned_func()

        assert calls == 1
        state = limiter._get_state("freelancer")
        assert state.total_bans == 1
        assert state.current_rpm == state.min_rpm

    @pytest.mark.asyncio
    async def test_decorator_records_ban_on_captcha(self) -> None:
        """CaptchaDetectedError must trigger record_ban."""
        limiter = _make_limiter()

        @retry_on_transient(max_retries=3, rate_limiter=limiter, platform="kwork")
        async def captcha_func() -> str:
            raise CaptchaDetectedError(platform="kwork", operation="scrape")

        with pytest.raises(CaptchaDetectedError):
            await captcha_func()

        state = limiter._get_state("kwork")
        assert state.total_bans == 1

    @pytest.mark.asyncio
    async def test_decorator_records_ban_on_cloudflare(self) -> None:
        """CloudflareBlockError must trigger record_ban."""
        limiter = _make_limiter()

        @retry_on_transient(max_retries=3, rate_limiter=limiter, platform="fl_ru")
        async def cf_func() -> str:
            raise CloudflareBlockError(platform="fl_ru", operation="fetch")

        with pytest.raises(CloudflareBlockError):
            await cf_func()

        state = limiter._get_state("fl_ru")
        assert state.total_bans == 1

    @pytest.mark.asyncio
    async def test_decorator_exponential_backoff(self) -> None:
        """Retry delays must increase exponentially (backoff_factor=2.0 default)."""
        limiter = _make_limiter()
        sleep_durations: list[float] = []
        original_sleep = __import__("asyncio").sleep

        async def mock_sleep(duration: float) -> None:
            sleep_durations.append(duration)
            # Skip actual sleeping.

        @retry_on_transient(
            max_retries=3,
            initial_delay=1.0,
            backoff_factor=2.0,
            rate_limiter=limiter,
            platform="freelancer",
        )
        async def always_rate_limited() -> str:
            raise PlatformRateLimitError(platform="freelancer", operation="test")

        with patch("src.adapters.rate_limiter.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(PlatformRateLimitError):
                await always_rate_limited()

        # Should have slept 3 times (max_retries=3, attempts 0,1,2 fail -> sleep after 0,1,2).
        assert len(sleep_durations) == 3
        # Delays: 1.0, 2.0, 4.0.
        assert sleep_durations[0] == pytest.approx(1.0)
        assert sleep_durations[1] == pytest.approx(2.0)
        assert sleep_durations[2] == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_decorator_without_limiter_still_works(self) -> None:
        """When no rate_limiter is passed, decorator must still function."""
        calls = 0

        @retry_on_transient(max_retries=2, initial_delay=0.01)
        async def flaky() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise PlatformRateLimitError(platform="test", operation="test")
            return "done"

        result = await flaky()
        assert result == "done"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_decorator_uses_retry_after_from_exception(self) -> None:
        """When PlatformRateLimitError has retry_after_seconds, it must be preferred."""
        sleep_durations: list[float] = []

        async def mock_sleep(duration: float) -> None:
            sleep_durations.append(duration)

        calls = 0

        @retry_on_transient(max_retries=2, initial_delay=999.0)
        async def with_retry_after() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise PlatformRateLimitError(
                    platform="test",
                    operation="test",
                    retry_after_seconds=0.05,
                )
            return "ok"

        with patch("src.adapters.rate_limiter.asyncio.sleep", side_effect=mock_sleep):
            result = await with_retry_after()

        assert result == "ok"
        # Should use 0.05 from exception, not 999.0 from initial_delay.
        assert sleep_durations[0] == pytest.approx(0.05)


# ===================================================================
# 7. DEFAULT LIMITS CONFIGURATION
# ===================================================================


class TestDefaultLimitsConfiguration:
    """Verify platform defaults are correctly defined."""

    def test_all_known_platforms_have_limits(self) -> None:
        """All expected platforms must have default limits configured."""
        expected_platforms = {"freelancer", "fl_ru", "kwork", "upwork", "telegram"}
        assert set(_DEFAULT_LIMITS.keys()) == expected_platforms

    @pytest.mark.parametrize("platform", list(_DEFAULT_LIMITS.keys()))
    def test_each_platform_has_required_fields(self, platform: str) -> None:
        """Each platform config must have max_rpm, min_rpm, initial_rpm."""
        config = _DEFAULT_LIMITS[platform]
        assert "max_rpm" in config
        assert "min_rpm" in config
        assert "initial_rpm" in config

    @pytest.mark.parametrize("platform", list(_DEFAULT_LIMITS.keys()))
    def test_min_below_initial_below_max(self, platform: str) -> None:
        """min_rpm < initial_rpm < max_rpm must hold for every platform."""
        config = _DEFAULT_LIMITS[platform]
        assert config["min_rpm"] < config["initial_rpm"]
        assert config["initial_rpm"] <= config["max_rpm"]

    def test_platform_rate_state_dataclass_defaults(self) -> None:
        """PlatformRateState defaults must be sane."""
        state = PlatformRateState()
        assert state.current_rpm == 10.0
        assert state.consecutive_successes == 0
        assert state.consecutive_failures == 0
        assert state.paused_until == 0.0
        assert state.total_requests == 0
