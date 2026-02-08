"""Seed the database with test data for development and demo purposes.

Usage::

    python -m src.cli.seed_data          # Create seed data
    python -m src.cli.seed_data --force  # Drop existing seed data first
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from src.api.guards import hash_password
from src.core.database import async_session_factory, engine
from src.core.models import AgentHeartbeat, HITLQueue, Job, User

# Deterministic UUIDs for idempotent seeding
_NS = uuid.NAMESPACE_DNS


def _sid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)


_NOW = datetime.now(UTC)

# ── Test user ────────────────────────────────────────────────────────────────

SEED_USER = User(
    id=_sid("seed-user-admin"),
    email="admin@test.com",
    password_hash=hash_password("admin123"),
    name="Admin",
    role="owner",
    settings={
        "telegram_notifications": {
            "notify_bid_approval": True,
            "notify_code_review": True,
            "notify_delivery": True,
        },
        "quiet_hours": {"enabled": False, "start": 23, "end": 8},
        "timezone": "Europe/Moscow",
    },
    created_at=_NOW,
)

# ── Jobs ─────────────────────────────────────────────────────────────────────

SEED_JOBS = [
    Job(
        id=_sid("seed-job-1"),
        platform="freelancer",
        external_id="FL-10001",
        title="React Dashboard for Analytics Startup",
        description="Build a real-time analytics dashboard with React, TypeScript, and Recharts.",
        budget_min=Decimal("2000"),
        budget_max=Decimal("5000"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "TechCorp", "rating": 4.8, "reviews": 42, "country": "US"},
        skills_required=["react", "typescript", "tailwindcss", "recharts"],
        status="qualified",
        score=Decimal("0.92"),
        discovered_at=_NOW - timedelta(hours=3),
        url="https://freelancer.com/projects/10001",
    ),
    Job(
        id=_sid("seed-job-2"),
        platform="freelancer",
        external_id="FL-10002",
        title="WordPress E-Commerce Site Migration",
        description="Migrate WooCommerce store from shared hosting to AWS with performance optimizations.",
        budget_min=Decimal("800"),
        budget_max=Decimal("1500"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "ShopOwner", "rating": 4.5, "reviews": 15, "country": "UK"},
        skills_required=["wordpress", "woocommerce", "aws", "mysql"],
        status="qualified",
        score=Decimal("0.78"),
        discovered_at=_NOW - timedelta(hours=2),
        url="https://freelancer.com/projects/10002",
    ),
    Job(
        id=_sid("seed-job-3"),
        platform="freelancer",
        external_id="FL-10003",
        title="Mobile App UI/UX Design",
        description="Design screens for a fitness tracking mobile app. Figma deliverables.",
        budget_min=Decimal("500"),
        budget_max=Decimal("1000"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "FitApp Inc", "rating": 4.2, "reviews": 8, "country": "DE"},
        skills_required=["figma", "ui-design", "mobile"],
        status="new",
        score=Decimal("0.65"),
        discovered_at=_NOW - timedelta(hours=1),
        url="https://freelancer.com/projects/10003",
    ),
    Job(
        id=_sid("seed-job-4"),
        platform="freelancer",
        external_id="FL-10004",
        title="Python Backend API with FastAPI",
        description="RESTful API for inventory management system. PostgreSQL, Docker, CI/CD.",
        budget_min=Decimal("3000"),
        budget_max=Decimal("5000"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "InventoryPro", "rating": 4.9, "reviews": 67, "country": "US"},
        skills_required=["python", "fastapi", "postgresql", "docker"],
        status="bid_sent",
        score=Decimal("0.95"),
        discovered_at=_NOW - timedelta(hours=5),
        url="https://freelancer.com/projects/10004",
    ),
    Job(
        id=_sid("seed-job-5"),
        platform="fl_ru",
        external_id="FLRU-20001",
        title="Landing page for law firm",
        description="Single-page site for a Moscow law firm. Responsive, fast loading.",
        budget_min=Decimal("200"),
        budget_max=Decimal("400"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "Legal Partners", "country": "RU"},
        skills_required=["html", "css", "javascript"],
        status="qualified",
        score=Decimal("0.81"),
        discovered_at=_NOW - timedelta(hours=4),
        url="https://fl.ru/projects/20001",
    ),
    Job(
        id=_sid("seed-job-6"),
        platform="fl_ru",
        external_id="FLRU-20002",
        title="Telegram bot for restaurant orders",
        description="Bot for accepting food orders via Telegram. Python + aiogram.",
        budget_min=Decimal("300"),
        budget_max=Decimal("600"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "Cafe Mocha", "country": "RU"},
        skills_required=["python", "aiogram", "postgresql"],
        status="new",
        score=Decimal("0.72"),
        discovered_at=_NOW - timedelta(hours=2),
        url="https://fl.ru/projects/20002",
    ),
    Job(
        id=_sid("seed-job-7"),
        platform="fl_ru",
        external_id="FLRU-20003",
        title="Fix CSS bugs on corporate site",
        description="Several layout issues on Safari and mobile browsers.",
        budget_min=Decimal("50"),
        budget_max=Decimal("100"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "CorpSite LLC", "country": "RU"},
        skills_required=["css", "html", "responsive"],
        status="disqualified",
        score=Decimal("0.30"),
        disqualify_reason="Budget too low for scope",
        discovered_at=_NOW - timedelta(hours=6),
        url="https://fl.ru/projects/20003",
    ),
    Job(
        id=_sid("seed-job-8"),
        platform="kwork",
        external_id="KW-30001",
        title="Set up VPS + Docker deployment",
        description="Configure Ubuntu VPS with Docker, Nginx, SSL, and CI/CD via GitHub Actions.",
        budget_min=Decimal("150"),
        budget_max=Decimal("150"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "StartupDev"},
        skills_required=["linux", "docker", "nginx", "github-actions"],
        status="new",
        score=Decimal("0.68"),
        discovered_at=_NOW - timedelta(hours=1),
        url="https://kwork.ru/projects/30001",
    ),
    Job(
        id=_sid("seed-job-9"),
        platform="kwork",
        external_id="KW-30002",
        title="Scrape product data from 5 websites",
        description="Extract product names, prices, images from competitor sites. Python + Playwright.",
        budget_min=Decimal("100"),
        budget_max=Decimal("200"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "DataMiner"},
        skills_required=["python", "playwright", "scraping"],
        status="qualified",
        score=Decimal("0.75"),
        discovered_at=_NOW - timedelta(hours=3),
        url="https://kwork.ru/projects/30002",
    ),
    Job(
        id=_sid("seed-job-10"),
        platform="upwork",
        external_id="UPW-40001",
        title="Full-stack Next.js SaaS Application",
        description="Build a multi-tenant SaaS app with Next.js, Prisma, Stripe billing.",
        budget_min=Decimal("4000"),
        budget_max=Decimal("8000"),
        budget_type="fixed",
        currency="USD",
        client_info={"name": "SaaS Ventures", "rating": 4.7, "total_spent": 125000, "country": "US"},
        skills_required=["nextjs", "react", "prisma", "stripe", "typescript"],
        status="new",
        score=Decimal("0.88"),
        discovered_at=_NOW - timedelta(minutes=30),
        url="https://upwork.com/jobs/40001",
    ),
]

# ── HITL items ───────────────────────────────────────────────────────────────

SEED_HITL = [
    HITLQueue(
        id=_sid("seed-hitl-1"),
        type="bid_approval",
        priority="urgent",
        title="Bid: React Dashboard for Analytics Startup",
        description="Proposal ready for Freelancer.com job FL-10001. Budget: $3,500.",
        payload={
            "job_title": "React Dashboard for Analytics Startup",
            "platform": "freelancer",
            "bid_amount": 3500.00,
            "estimated_days": 14,
            "client_rating": 4.8,
            "proposal_text": (
                "Hi! I specialize in building React dashboards with real-time data visualization. "
                "I've delivered 15+ similar projects using React, TypeScript, and Recharts. "
                "I can start immediately and deliver within 2 weeks. Let's discuss the details!"
            ),
        },
        available_actions=["approve", "edit", "skip", "later"],
        status="pending",
        created_at=_NOW - timedelta(minutes=45),
        expires_at=_NOW + timedelta(hours=2),
    ),
    HITLQueue(
        id=_sid("seed-hitl-2"),
        type="bid_approval",
        priority="normal",
        title="Bid: WordPress E-Commerce Migration",
        description="Proposal ready for Freelancer.com job FL-10002. Budget: $1,200.",
        payload={
            "job_title": "WordPress E-Commerce Site Migration",
            "platform": "freelancer",
            "bid_amount": 1200.00,
            "estimated_days": 7,
            "client_rating": 4.5,
            "proposal_text": (
                "I have extensive experience migrating WooCommerce stores to AWS. "
                "I'll handle the full migration including database, media files, SSL setup, "
                "and performance optimization. Zero downtime guaranteed."
            ),
        },
        available_actions=["approve", "edit", "skip", "later"],
        status="pending",
        created_at=_NOW - timedelta(minutes=20),
        expires_at=_NOW + timedelta(hours=24),
    ),
    HITLQueue(
        id=_sid("seed-hitl-3"),
        type="code_review",
        priority="normal",
        title="Code Review: Python API — InventoryPro",
        description="Dev Agent completed 45 files. Critic found 2 minor security issues.",
        payload={
            "project_name": "InventoryPro Backend API",
            "files_count": 45,
            "lines_of_code": 3200,
            "quality_score": 0.92,
            "security_issues": 2,
            "security_details": [
                {"severity": "low", "rule": "S105", "file": "config.py", "message": "Possible hardcoded password"},
                {
                    "severity": "low", "rule": "B108", "file": "utils.py",
                    "message": "Probable insecure usage of temp file",
                },
            ],
            "test_coverage": 0.84,
        },
        available_actions=["approve", "reject", "edit", "skip"],
        status="pending",
        created_at=_NOW - timedelta(hours=1),
    ),
    HITLQueue(
        id=_sid("seed-hitl-4"),
        type="delivery",
        priority="urgent",
        title="Delivery: Landing Page — Legal Partners",
        description="Project complete. Packager has prepared the delivery archive.",
        payload={
            "project_name": "Landing Page for Legal Partners",
            "client_name": "Legal Partners",
            "platform": "fl_ru",
            "deadline": (_NOW + timedelta(hours=6)).isoformat(),
            "deliverables": ["index.html", "styles.css", "script.js", "assets/"],
            "archive_size_mb": 2.4,
        },
        available_actions=["approve", "reject", "edit", "later"],
        status="pending",
        created_at=_NOW - timedelta(minutes=10),
        expires_at=_NOW + timedelta(hours=4),
    ),
    HITLQueue(
        id=_sid("seed-hitl-5"),
        type="alert",
        priority="normal",
        title="Budget Alert: Daily LLM spend at 82%",
        description="Today's LLM API costs have reached $18.40 of $22.50 daily budget.",
        payload={
            "alert_type": "budget",
            "current_spend_usd": 18.40,
            "daily_budget_usd": 22.50,
            "percentage": 82,
            "top_consumers": [
                {"agent": "dev", "cost": 8.50},
                {"agent": "planner", "cost": 4.20},
                {"agent": "scout", "cost": 3.10},
            ],
        },
        available_actions=["approve", "skip"],
        status="pending",
        created_at=_NOW - timedelta(minutes=5),
    ),
]

# ── Agent heartbeats ─────────────────────────────────────────────────────────

_AGENTS = [
    ("scout", "idle", None, _NOW - timedelta(seconds=30)),
    ("bid", "working", "Drafting proposal for FL-10002", _NOW - timedelta(seconds=10)),
    ("planner", "idle", None, _NOW - timedelta(minutes=2)),
    ("dev", "idle", None, _NOW - timedelta(minutes=5)),
    ("content", "idle", None, None),
    ("design", "idle", None, None),
    ("critic", "idle", None, _NOW - timedelta(minutes=8)),
    ("packager", "idle", None, _NOW - timedelta(minutes=15)),
    ("geo_scout", "idle", None, None),
    ("outreach", "idle", None, None),
]

SEED_HEARTBEATS = [
    AgentHeartbeat(
        agent_name=name,
        status=status,
        current_task=task,
        last_heartbeat=hb,
        restart_count=0,
    )
    for name, status, task, hb in _AGENTS
]


# ── Main seed function ───────────────────────────────────────────────────────


async def _seed(force: bool = False) -> None:
    async with async_session_factory() as session:
        # Check if seed user already exists
        existing = await session.execute(
            select(User).where(User.email == "admin@test.com")
        )
        if existing.scalar_one_or_none() is not None:
            if not force:
                print("Seed data already exists (admin@test.com found).")
                print("Use --force to drop and re-create.")
                return
            # Force: delete existing seed data
            print("Dropping existing seed data...")
            await session.execute(delete(AgentHeartbeat))
            await session.execute(delete(HITLQueue))
            await session.execute(delete(Job))
            await session.execute(delete(User).where(User.email == "admin@test.com"))
            await session.commit()
            print("Existing seed data dropped.")

        # Insert all seed data
        session.add(SEED_USER)
        session.add_all(SEED_JOBS)
        session.add_all(SEED_HITL)

        # Heartbeats use merge (upsert) since PK is agent_name
        for hb in SEED_HEARTBEATS:
            await session.merge(hb)

        await session.commit()

        print("Seed data created successfully:")
        print("  Users:            1  (admin@test.com / admin123)")
        print(f"  Jobs:            {len(SEED_JOBS):2d}")
        print(f"  HITL items:       {len(SEED_HITL)}")
        print(f"  Agent heartbeats: {len(SEED_HEARTBEATS)}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the MAS database with test data.",
        prog="python -m src.cli.seed_data",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop existing seed data before re-creating",
    )
    args = parser.parse_args()
    asyncio.run(_seed(force=args.force))


if __name__ == "__main__":
    main()
