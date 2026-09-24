"""Analytics routes.

Provides aggregated statistics across jobs, bids, leads, deals, and
pipeline performance for the dashboard Analytics page.

Mounted at ``/api/v1/analytics``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from litestar import Controller, get
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import Bid, Deal, Job, Lead, Project

logger = structlog.get_logger(__name__)


class AnalyticsController(Controller):
    """Analytics and aggregate statistics."""

    path = "/api/v1/analytics"
    tags = ["analytics"]

    # -----------------------------------------------------------------
    # GET /api/v1/analytics  (root — dashboard calls this with ?days=N)
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # GET /api/v1/analytics/overview
    # -----------------------------------------------------------------

    @get(
        "/overview",
        summary="Dashboard overview KPIs",
        description="Top-level KPIs: active jobs, bids sent, win rate, revenue, leads.",
    )
    async def overview(
        self,
        db_session: AsyncSession,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return high-level KPIs for the dashboard overview cards."""
        # Jobs
        total_jobs = (await db_session.execute(select(func.count()).select_from(Job))).scalar_one()
        active_jobs = (
            await db_session.execute(select(func.count()).select_from(Job).where(Job.status.in_(("new", "qualified"))))
        ).scalar_one()

        # Bids
        total_bids = (await db_session.execute(select(func.count()).select_from(Bid))).scalar_one()
        won_bids = (
            await db_session.execute(select(func.count()).select_from(Bid).where(Bid.status == "won"))
        ).scalar_one()
        sent_bids = (
            await db_session.execute(
                select(func.count()).select_from(Bid).where(Bid.status.in_(("sent", "won", "lost", "rejected")))
            )
        ).scalar_one()
        win_rate = round(won_bids / sent_bids * 100, 1) if sent_bids > 0 else 0.0

        # Revenue (won bids + completed projects)
        won_revenue_row = (
            await db_session.execute(select(func.sum(Bid.bid_amount)).where(Bid.status == "won"))
        ).scalar_one()
        won_revenue = float(won_revenue_row or Decimal("0"))

        # Leads (Pipeline B)
        total_leads = (await db_session.execute(select(func.count()).select_from(Lead))).scalar_one()
        hot_leads = (
            await db_session.execute(select(func.count()).select_from(Lead).where(Lead.temperature == "hot"))
        ).scalar_one()

        # Deals
        total_deals = (await db_session.execute(select(func.count()).select_from(Deal))).scalar_one()
        active_deals = (
            await db_session.execute(
                select(func.count()).select_from(Deal).where(Deal.status.in_(("new", "negotiating", "proposal_sent")))
            )
        ).scalar_one()

        # Projects
        active_projects = (
            await db_session.execute(
                select(func.count())
                .select_from(Project)
                .where(Project.status.in_(("planning", "in_progress", "review")))
            )
        ).scalar_one()

        return {
            "jobs": {
                "total": total_jobs,
                "active": active_jobs,
            },
            "bids": {
                "total": total_bids,
                "sent": sent_bids,
                "won": won_bids,
                "win_rate_pct": win_rate,
                "won_revenue_usd": won_revenue,
            },
            "leads": {
                "total": total_leads,
                "hot": hot_leads,
            },
            "deals": {
                "total": total_deals,
                "active": active_deals,
            },
            "projects": {
                "active": active_projects,
            },
        }

    # -----------------------------------------------------------------
    # GET /api/v1/analytics/pipeline-a
    # -----------------------------------------------------------------

    @get(
        "/pipeline-a",
        summary="Pipeline A funnel statistics",
        description="Jobs → qualified → bid → won funnel with conversion rates.",
    )
    async def pipeline_a(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=30, ge=1, le=365, description="Lookback window in days"),
    ) -> dict[str, Any]:
        """Return Pipeline A funnel data for the given lookback window."""
        since = datetime.now(UTC) - timedelta(days=days)

        # Jobs discovered in window
        jobs_total = (
            await db_session.execute(select(func.count()).select_from(Job).where(Job.discovered_at >= since))
        ).scalar_one()

        # Jobs qualified
        jobs_qualified = (
            await db_session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.discovered_at >= since, Job.status.in_(("qualified", "bid_sent", "won", "lost")))
            )
        ).scalar_one()

        # Bids sent from those jobs (join)
        bids_sent = (
            await db_session.execute(
                select(func.count())
                .select_from(Bid)
                .join(Bid.job)
                .where(Job.discovered_at >= since, Bid.status.in_(("sent", "viewed", "shortlisted", "won", "lost")))
            )
        ).scalar_one()

        # Won bids
        bids_won = (
            await db_session.execute(
                select(func.count())
                .select_from(Bid)
                .join(Bid.job)
                .where(Job.discovered_at >= since, Bid.status == "won")
            )
        ).scalar_one()

        # Bids by platform
        platform_stmt = (
            select(Job.platform, func.count(Bid.id))
            .join(Bid.job)
            .where(Job.discovered_at >= since)
            .group_by(Job.platform)
        )
        platform_rows = (await db_session.execute(platform_stmt)).all()
        by_platform = [{"platform": row[0], "bids": row[1]} for row in platform_rows]

        # Average bid amount
        avg_bid_row = (
            await db_session.execute(
                select(func.avg(Bid.bid_amount)).join(Bid.job).where(Job.discovered_at >= since, Bid.status != "draft")
            )
        ).scalar_one()
        avg_bid = float(avg_bid_row or 0)

        return {
            "window_days": days,
            "funnel": {
                "discovered": jobs_total,
                "qualified": jobs_qualified,
                "bids_sent": bids_sent,
                "won": bids_won,
            },
            "conversion": {
                "qualify_rate_pct": round(jobs_qualified / jobs_total * 100, 1) if jobs_total > 0 else 0.0,
                "bid_rate_pct": round(bids_sent / jobs_qualified * 100, 1) if jobs_qualified > 0 else 0.0,
                "win_rate_pct": round(bids_won / bids_sent * 100, 1) if bids_sent > 0 else 0.0,
            },
            "avg_bid_amount_usd": avg_bid,
            "by_platform": by_platform,
        }

    # -----------------------------------------------------------------
    # GET /api/v1/analytics/pipeline-b
    # -----------------------------------------------------------------

    @get(
        "/pipeline-b",
        summary="Pipeline B funnel statistics",
        description="Leads → contacted → replied → deal → won funnel.",
    )
    async def pipeline_b(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=30, ge=1, le=365, description="Lookback window in days"),
    ) -> dict[str, Any]:
        """Return Pipeline B funnel data for the given lookback window."""
        since = datetime.now(UTC) - timedelta(days=days)

        # Leads discovered
        leads_total = (
            await db_session.execute(select(func.count()).select_from(Lead).where(Lead.discovered_at >= since))
        ).scalar_one()

        # Leads by temperature
        temp_stmt = select(Lead.temperature, func.count()).where(Lead.discovered_at >= since).group_by(Lead.temperature)
        temp_rows = (await db_session.execute(temp_stmt)).all()
        by_temperature = {row[0] or "unknown": row[1] for row in temp_rows}

        # Leads contacted (touch_count > 0)
        leads_contacted = (
            await db_session.execute(
                select(func.count()).select_from(Lead).where(Lead.discovered_at >= since, Lead.touch_count > 0)
            )
        ).scalar_one()

        # Deals created in window
        deals_total = (
            await db_session.execute(select(func.count()).select_from(Deal).where(Deal.created_at >= since))
        ).scalar_one()

        # Deals won
        deals_won = (
            await db_session.execute(
                select(func.count()).select_from(Deal).where(Deal.created_at >= since, Deal.status == "won")
            )
        ).scalar_one()

        # Deals by status
        deal_status_stmt = select(Deal.status, func.count()).where(Deal.created_at >= since).group_by(Deal.status)
        deal_status_rows = (await db_session.execute(deal_status_stmt)).all()
        deals_by_status = {row[0]: row[1] for row in deal_status_rows}

        # Avg lead score
        avg_score_row = (
            await db_session.execute(
                select(func.avg(Lead.lead_score)).where(Lead.discovered_at >= since, Lead.lead_score.is_not(None))
            )
        ).scalar_one()
        avg_score = float(avg_score_row or 0)

        return {
            "window_days": days,
            "funnel": {
                "discovered": leads_total,
                "contacted": leads_contacted,
                "deals_created": deals_total,
                "deals_won": deals_won,
            },
            "conversion": {
                "contact_rate_pct": round(leads_contacted / leads_total * 100, 1) if leads_total > 0 else 0.0,
                "lead_to_deal_pct": round(deals_total / leads_contacted * 100, 1) if leads_contacted > 0 else 0.0,
                "deal_win_rate_pct": round(deals_won / deals_total * 100, 1) if deals_total > 0 else 0.0,
            },
            "by_temperature": by_temperature,
            "deals_by_status": deals_by_status,
            "avg_lead_score": round(avg_score, 1),
        }

    # -----------------------------------------------------------------
    # GET /api/v1/analytics/revenue
    # -----------------------------------------------------------------

    @get(
        "/revenue",
        summary="Revenue over time",
        description="Daily/weekly revenue aggregation from won bids.",
    )
    async def revenue(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=90, ge=7, le=365, description="Lookback window in days"),
    ) -> dict[str, Any]:
        """Return revenue aggregated by week for the given lookback window."""
        since = datetime.now(UTC) - timedelta(days=days)

        # Won bids with amounts, grouped by week
        week_stmt = (
            select(
                func.date_trunc("week", Bid.sent_at).label("week"),
                func.sum(Bid.bid_amount).label("revenue"),
                func.count(Bid.id).label("count"),
            )
            .where(Bid.status == "won", Bid.sent_at >= since, Bid.sent_at.is_not(None))
            .group_by(func.date_trunc("week", Bid.sent_at))
            .order_by(func.date_trunc("week", Bid.sent_at))
        )
        week_rows = (await db_session.execute(week_stmt)).all()

        weekly = [
            {
                "week": row.week.isoformat() if row.week else None,
                "revenue_usd": float(row.revenue or 0),
                "won_count": row.count,
            }
            for row in week_rows
        ]

        total_revenue = sum(w["revenue_usd"] for w in weekly)

        return {
            "window_days": days,
            "total_revenue_usd": total_revenue,
            "weekly": weekly,
        }

    # -----------------------------------------------------------------
    # GET /api/v1/analytics/agents
    # -----------------------------------------------------------------

    @get(
        "/agents",
        summary="Agent performance statistics",
        description="LLM cost, token usage, and task counts per agent.",
    )
    async def agents(
        self,
        db_session: AsyncSession,
        days: int = Parameter(default=7, ge=1, le=90, description="Lookback window in days"),
    ) -> dict[str, Any]:
        """Return per-agent cost, token usage, and task counts."""
        from src.core.models import AgentLog  # noqa: PLC0415

        since = datetime.now(UTC) - timedelta(days=days)

        agent_stmt = (
            select(
                AgentLog.agent_name,
                func.count(AgentLog.id).label("log_count"),
                func.sum(AgentLog.tokens_input).label("tokens_in"),
                func.sum(AgentLog.tokens_output).label("tokens_out"),
                func.sum(AgentLog.cost_usd).label("cost_usd"),
                func.avg(AgentLog.latency_ms).label("avg_latency_ms"),
            )
            .where(AgentLog.created_at >= since)
            .group_by(AgentLog.agent_name)
            .order_by(func.sum(AgentLog.cost_usd).desc().nulls_last())
        )
        rows = (await db_session.execute(agent_stmt)).all()

        agents_data = [
            {
                "agent": row.agent_name,
                "log_count": row.log_count,
                "tokens_input": row.tokens_in or 0,
                "tokens_output": row.tokens_out or 0,
                "cost_usd": float(row.cost_usd or 0),
                "avg_latency_ms": float(row.avg_latency_ms or 0),
            }
            for row in rows
        ]

        total_cost = sum(a["cost_usd"] for a in agents_data)

        return {
            "window_days": days,
            "total_cost_usd": round(total_cost, 4),
            "agents": agents_data,
        }
