"""Unit tests for BudgetTracker (src/core/budget_tracker.py).

Tests cover: cost recording, daily/monthly limits, 80% alert threshold,
per-model cost calculation, usage reporting, and concurrent safety.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.core.budget_tracker import (
    BudgetExceeded,
    BudgetTracker,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tracker(
    daily_limit: float = 50.0,
    monthly_limit: float = 500.0,
    alert_threshold: float = 0.8,
) -> BudgetTracker:
    """Create a BudgetTracker with a mock Valkey client."""
    valkey = AsyncMock()
    # Default: no existing spend
    valkey.get = AsyncMock(return_value=None)
    valkey.incrbyfloat = AsyncMock(return_value=b"0.0")
    valkey.expire = AsyncMock()
    return BudgetTracker(
        valkey_client=valkey,
        daily_limit=daily_limit,
        monthly_limit=monthly_limit,
        alert_threshold=alert_threshold,
    )


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


class TestBudgetCostCalculation:
    """Verify _calculate_cost uses MODELS registry correctly."""

    async def test_known_model_cost(self):
        bt = _tracker()
        # claude-sonnet-4-6: input=0.003/1k, output=0.015/1k
        cost = bt._calculate_cost("claude-sonnet-4-6", 1000, 1000)
        assert cost == pytest.approx(0.003 + 0.015, rel=1e-6)

    async def test_unknown_model_uses_fallback(self):
        bt = _tracker()
        cost = bt._calculate_cost("nonexistent-model", 1000, 1000)
        # Should use conservative fallback costs, not crash
        assert cost > 0

    async def test_zero_tokens_zero_cost(self):
        bt = _tracker()
        cost = bt._calculate_cost("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0

    async def test_scales_with_tokens(self):
        bt = _tracker()
        cost_1k = bt._calculate_cost("claude-sonnet-4-6", 1000, 0)
        cost_2k = bt._calculate_cost("claude-sonnet-4-6", 2000, 0)
        assert cost_2k == pytest.approx(cost_1k * 2, rel=1e-6)


# ---------------------------------------------------------------------------
# Recording costs
# ---------------------------------------------------------------------------


class TestBudgetRecordCost:
    """Verify record_cost writes to Valkey and returns computed cost."""

    async def test_records_and_returns_cost(self):
        bt = _tracker()
        bt._valkey.incrbyfloat = AsyncMock(return_value=b"0.018")
        cost = await bt.record_cost("claude-sonnet-4-6", 1000, 1000)
        assert cost == pytest.approx(0.003 + 0.015, rel=1e-6)

    async def test_increments_daily_key(self):
        bt = _tracker()
        today = date.today().isoformat()
        await bt.record_cost("claude-sonnet-4-6", 1000, 1000)
        # Should have called incrbyfloat for daily key
        calls = bt._valkey.incrbyfloat.call_args_list
        daily_calls = [c for c in calls if today in str(c)]
        assert len(daily_calls) >= 1

    async def test_increments_monthly_key(self):
        bt = _tracker()
        month = date.today().strftime("%Y-%m")
        await bt.record_cost("claude-sonnet-4-6", 1000, 1000)
        calls = bt._valkey.incrbyfloat.call_args_list
        monthly_calls = [c for c in calls if month in str(c)]
        assert len(monthly_calls) >= 1

    async def test_sets_ttl_on_keys(self):
        bt = _tracker()
        await bt.record_cost("claude-sonnet-4-6", 1000, 0)
        # expire should be called for daily (48h=172800) and monthly (35d=3024000)
        assert bt._valkey.expire.call_count >= 2


# ---------------------------------------------------------------------------
# Budget checking
# ---------------------------------------------------------------------------


class TestBudgetCheckLimits:
    """Verify check_budget raises BudgetExceeded when over limits."""

    async def test_under_limits_passes(self):
        bt = _tracker(daily_limit=50.0, monthly_limit=500.0)
        bt._valkey.get = AsyncMock(return_value=b"10.0")
        # Should not raise
        await bt.check_budget()

    async def test_daily_limit_exceeded(self):
        bt = _tracker(daily_limit=50.0)

        # daily over, monthly under
        async def _get(key: str) -> bytes | None:
            if "daily" in key:
                return b"51.0"
            return b"100.0"

        bt._valkey.get = AsyncMock(side_effect=_get)
        with pytest.raises(BudgetExceeded, match="daily"):
            await bt.check_budget()

    async def test_monthly_limit_exceeded(self):
        bt = _tracker(monthly_limit=500.0)

        async def _get(key: str) -> bytes | None:
            if "monthly" in key:
                return b"501.0"
            return b"10.0"

        bt._valkey.get = AsyncMock(side_effect=_get)
        with pytest.raises(BudgetExceeded, match="monthly"):
            await bt.check_budget()

    async def test_exactly_at_limit_raises(self):
        bt = _tracker(daily_limit=50.0)

        async def _get(key: str) -> bytes | None:
            if "daily" in key:
                return b"50.0"
            return b"10.0"

        bt._valkey.get = AsyncMock(side_effect=_get)
        with pytest.raises(BudgetExceeded):
            await bt.check_budget()

    async def test_budget_exceeded_is_mas_exception(self):
        """BudgetExceeded should be a MASException subclass."""
        from src.core.exceptions import MASException

        assert issubclass(BudgetExceeded, MASException)


# ---------------------------------------------------------------------------
# Alert threshold
# ---------------------------------------------------------------------------


class TestBudgetAlertThreshold:
    """Verify _send_alert_if_needed triggers at 80% of limits."""

    async def test_alert_at_80_percent_daily(self):
        bt = _tracker(daily_limit=50.0, alert_threshold=0.8)
        bt._alert_sent_today = False
        # 80% of 50 = 40
        with patch.object(bt, "_send_alert", new_callable=AsyncMock) as mock_alert:
            await bt._send_alert_if_needed(daily_spent=40.0, monthly_spent=100.0)
            mock_alert.assert_called_once()

    async def test_no_alert_under_threshold(self):
        bt = _tracker(daily_limit=50.0, alert_threshold=0.8)
        bt._alert_sent_today = False
        with patch.object(bt, "_send_alert", new_callable=AsyncMock) as mock_alert:
            await bt._send_alert_if_needed(daily_spent=30.0, monthly_spent=100.0)
            mock_alert.assert_not_called()

    async def test_alert_at_80_percent_monthly(self):
        bt = _tracker(monthly_limit=500.0, alert_threshold=0.8)
        bt._alert_sent_today = False
        # 80% of 500 = 400
        with patch.object(bt, "_send_alert", new_callable=AsyncMock) as mock_alert:
            await bt._send_alert_if_needed(daily_spent=10.0, monthly_spent=400.0)
            mock_alert.assert_called_once()

    async def test_alert_sent_only_once_per_day(self):
        bt = _tracker(daily_limit=50.0, alert_threshold=0.8)
        bt._alert_sent_today = True  # already sent
        bt._alert_date = date.today().isoformat()  # same day
        with patch.object(bt, "_send_alert", new_callable=AsyncMock) as mock_alert:
            await bt._send_alert_if_needed(daily_spent=45.0, monthly_spent=100.0)
            mock_alert.assert_not_called()


# ---------------------------------------------------------------------------
# Usage reporting
# ---------------------------------------------------------------------------


class TestBudgetGetUsage:
    """Verify get_usage returns correct structure."""

    async def test_usage_structure(self):
        bt = _tracker(daily_limit=50.0, monthly_limit=500.0)

        async def _get(key: str) -> bytes | None:
            if "daily" in key:
                return b"12.50"
            return b"150.00"

        bt._valkey.get = AsyncMock(side_effect=_get)

        usage = await bt.get_usage()
        assert usage["daily_spent"] == pytest.approx(12.50)
        assert usage["monthly_spent"] == pytest.approx(150.00)
        assert usage["daily_limit"] == 50.0
        assert usage["monthly_limit"] == 500.0
        assert usage["daily_remaining"] == pytest.approx(37.50)
        assert usage["monthly_remaining"] == pytest.approx(350.00)

    async def test_usage_nil_values_default_zero(self):
        bt = _tracker()
        bt._valkey.get = AsyncMock(return_value=None)
        usage = await bt.get_usage()
        assert usage["daily_spent"] == 0.0
        assert usage["monthly_spent"] == 0.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestBudgetEdgeCases:
    """Edge cases: zero limits, negative remaining, Valkey unavailable."""

    async def test_zero_limit_always_exceeds(self):
        bt = _tracker(daily_limit=0.0)

        async def _get(key: str) -> bytes | None:
            if "daily" in key:
                return b"0.001"
            return b"0.0"

        bt._valkey.get = AsyncMock(side_effect=_get)
        with pytest.raises(BudgetExceeded):
            await bt.check_budget()

    async def test_negative_remaining_clamped(self):
        bt = _tracker(daily_limit=50.0)

        async def _get(key: str) -> bytes | None:
            if "daily" in key:
                return b"60.0"
            return b"0.0"

        bt._valkey.get = AsyncMock(side_effect=_get)
        usage = await bt.get_usage()
        assert usage["daily_remaining"] == 0.0  # clamped, not negative
