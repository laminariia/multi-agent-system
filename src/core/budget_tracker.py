"""LLM budget control with daily/monthly limits and alerting.

Tracks per-call costs in Valkey with atomic INCRBYFLOAT for concurrent safety.
Raises BudgetExceeded when daily ($50) or monthly ($500) limits are hit.
Sends a Telegram alert when spending reaches 80% of either limit.

Keys:
    budget:daily:{YYYY-MM-DD}  TTL 48h
    budget:monthly:{YYYY-MM}   TTL 35d
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from src.core.exceptions import MASException

logger = structlog.get_logger(__name__)

# TTLs for Valkey keys
_DAILY_TTL = 48 * 3600  # 48 hours
_MONTHLY_TTL = 35 * 86400  # 35 days

# Fallback cost for unknown models (conservative: assume expensive)
_FALLBACK_INPUT_PER_1K = 0.01
_FALLBACK_OUTPUT_PER_1K = 0.03


class BudgetExceeded(MASException):
    """Raised when LLM spending exceeds a configured budget limit."""


class BudgetTracker:
    """Enforces daily/monthly LLM spending limits via Valkey.

    Args:
        valkey_client: Async Valkey/Redis client instance.
        daily_limit: Max USD per day (default $50).
        monthly_limit: Max USD per month (default $500).
        alert_threshold: Fraction (0-1) at which to send alert (default 0.8).
    """

    def __init__(
        self,
        *,
        valkey_client: Any,
        daily_limit: float = 50.0,
        monthly_limit: float = 500.0,
        alert_threshold: float = 0.8,
    ) -> None:
        self._valkey = valkey_client
        self._daily_limit = daily_limit
        self._monthly_limit = monthly_limit
        self._alert_threshold = alert_threshold
        self._alert_sent_today: bool = False
        self._alert_date: str = ""

    # ------------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate USD cost for a given model and token counts."""
        from src.core.llm_client import MODELS

        spec = MODELS.get(model)
        if spec is not None:
            input_cost = (input_tokens / 1000) * spec.cost_input_per_1k
            output_cost = (output_tokens / 1000) * spec.cost_output_per_1k
        else:
            # Conservative fallback for unknown models
            input_cost = (input_tokens / 1000) * _FALLBACK_INPUT_PER_1K
            output_cost = (output_tokens / 1000) * _FALLBACK_OUTPUT_PER_1K

        return input_cost + output_cost

    # ------------------------------------------------------------------
    # Valkey key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _daily_key() -> str:
        return f"budget:daily:{date.today().isoformat()}"

    @staticmethod
    def _monthly_key() -> str:
        return f"budget:monthly:{date.today().strftime('%Y-%m')}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Record cost of an LLM call. Returns the computed cost in USD."""
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        if cost <= 0:
            return 0.0

        daily_key = self._daily_key()
        monthly_key = self._monthly_key()

        # Atomic increments
        daily_new = await self._valkey.incrbyfloat(daily_key, cost)
        await self._valkey.expire(daily_key, _DAILY_TTL)

        monthly_new = await self._valkey.incrbyfloat(monthly_key, cost)
        await self._valkey.expire(monthly_key, _MONTHLY_TTL)

        daily_spent = float(daily_new)
        monthly_spent = float(monthly_new)

        logger.debug(
            "budget.recorded",
            model=model,
            cost_usd=round(cost, 6),
            daily_spent=round(daily_spent, 2),
            monthly_spent=round(monthly_spent, 2),
        )

        await self._send_alert_if_needed(daily_spent, monthly_spent)

        return cost

    async def check_budget(self) -> None:
        """Raise BudgetExceeded if daily or monthly limit is reached."""
        daily_raw = await self._valkey.get(self._daily_key())
        monthly_raw = await self._valkey.get(self._monthly_key())

        daily_spent = float(daily_raw) if daily_raw else 0.0
        monthly_spent = float(monthly_raw) if monthly_raw else 0.0

        if daily_spent >= self._daily_limit:
            raise BudgetExceeded(f"LLM daily budget exceeded: ${daily_spent:.2f} >= ${self._daily_limit:.2f}")
        if monthly_spent >= self._monthly_limit:
            raise BudgetExceeded(f"LLM monthly budget exceeded: ${monthly_spent:.2f} >= ${self._monthly_limit:.2f}")

    async def get_usage(self) -> dict[str, float]:
        """Return current budget usage and remaining amounts."""
        daily_raw = await self._valkey.get(self._daily_key())
        monthly_raw = await self._valkey.get(self._monthly_key())

        daily_spent = float(daily_raw) if daily_raw else 0.0
        monthly_spent = float(monthly_raw) if monthly_raw else 0.0

        return {
            "daily_spent": daily_spent,
            "monthly_spent": monthly_spent,
            "daily_limit": self._daily_limit,
            "monthly_limit": self._monthly_limit,
            "daily_remaining": max(0.0, self._daily_limit - daily_spent),
            "monthly_remaining": max(0.0, self._monthly_limit - monthly_spent),
        }

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------

    async def _send_alert_if_needed(self, daily_spent: float, monthly_spent: float) -> None:
        """Send Telegram alert when spending reaches alert_threshold of any limit."""
        today = date.today().isoformat()
        if self._alert_date != today:
            self._alert_sent_today = False
            self._alert_date = today

        if self._alert_sent_today:
            return

        daily_pct = daily_spent / self._daily_limit if self._daily_limit > 0 else 0
        monthly_pct = monthly_spent / self._monthly_limit if self._monthly_limit > 0 else 0

        if daily_pct >= self._alert_threshold or monthly_pct >= self._alert_threshold:
            self._alert_sent_today = True
            await self._send_alert(daily_spent, monthly_spent)

    async def _send_alert(self, daily_spent: float, monthly_spent: float) -> None:
        """Send budget alert via Telegram. Override in tests."""
        logger.warning(
            "budget.alert",
            daily_spent=round(daily_spent, 2),
            daily_limit=self._daily_limit,
            monthly_spent=round(monthly_spent, 2),
            monthly_limit=self._monthly_limit,
        )
