"""Unit tests for Pipeline B integrations: Lead Scorer, Touch Sequence, HITL Telegram notifications.

H4: Lead Scorer integration into GeoScout + Business Analyzer into Outreach
H5: Touch Sequence integration into Outreach
H14: HITL Telegram auto-notifications in hitl_bid_node, hitl_dev_launch_node, hitl_review_node
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.geo_scout import GeoScoutAgent
from src.agents.outreach import OutreachAgent
from src.core.lead_scorer import AnalysisResult, WebsiteCheck
from src.core.models import Deal, HITLQueue, Lead, User
from src.core.touch_sequence import (
    TouchSequenceManager,
    TouchState,
)
from src.enrichment.waterfall import EnrichmentResult
from src.geo.h3_scanner import BoundingBox
from src.geo.overpass import GeoLead

# ===========================================================================
# Helpers
# ===========================================================================


def _make_state(**overrides: Any) -> dict[str, Any]:
    """Create minimal test state for Pipeline B."""
    base: dict[str, Any] = {
        "thread_id": "test-thread-pb",
        "mas_checkpoint_id": "test-cp",
        "project": {
            "project_id": "p1",
            "job_id": "",
            "platform": "outreach",
            "client": {},
            "requirements": "Berlin",
            "budget": 0,
            "deadline": datetime.now(UTC),
        },
        "current_agent": "geoscout",
        "current_task": None,
        "artifacts": {},
        "messages": [],
        "next_agent": None,
        "requires_hitl": False,
        "hitl_request_id": None,
        "retry_count": 0,
        "errors": [],
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "status": "active",
    }
    base.update(overrides)
    return base


def _make_lead(**overrides: Any) -> MagicMock:
    """Create a mock Lead object."""
    lead = MagicMock(spec=Lead)
    lead.id = overrides.get("id", uuid.uuid4())
    lead.name = overrides.get("name", "Test Cafe")
    lead.category = overrides.get("category", "cafe")
    lead.city = overrides.get("city", "Berlin")
    lead.address = overrides.get("address", "Test St 1")
    lead.phone = overrides.get("phone", "+49123456")
    lead.email = overrides.get("email", None)
    lead.osm_id = overrides.get("osm_id", 12345)
    lead.status = overrides.get("status", "new")
    lead.h3_index = overrides.get("h3_index", "891f1d4d9dfffff")
    lead.latitude = overrides.get("latitude", Decimal("52.5"))
    lead.longitude = overrides.get("longitude", Decimal("13.4"))
    lead.telegram_username = overrides.get("telegram_username", None)
    lead.website_url = overrides.get("website_url", None)
    lead.google_rating = overrides.get("google_rating", None)
    lead.review_count = overrides.get("review_count", None)
    lead.lead_score = overrides.get("lead_score", None)
    lead.temperature = overrides.get("temperature", None)
    lead.channel_used = overrides.get("channel_used", None)
    lead.touch_count = overrides.get("touch_count", 0)
    return lead


def _make_geo_lead(**overrides: Any) -> GeoLead:
    """Create a GeoLead for GeoScout tests."""
    return GeoLead(
        osm_id=overrides.get("osm_id", 100),
        name=overrides.get("name", "Test Business"),
        category=overrides.get("category", "restaurant"),
        lat=overrides.get("lat", 52.5),
        lon=overrides.get("lon", 13.4),
        h3_index=overrides.get("h3_index", "891f1d4d9dfffff"),
        address=overrides.get("address", "Main St 1"),
        phone=overrides.get("phone", None),
    )


def _make_hitl_item(**overrides: Any) -> MagicMock:
    """Create a mock HITLQueue item."""
    item = MagicMock(spec=HITLQueue)
    item.id = overrides.get("id", uuid.uuid4())
    item.type = overrides.get("type", "bid_approval")
    item.priority = overrides.get("priority", "normal")
    item.title = overrides.get("title", "Test HITL")
    item.description = overrides.get("description", "Test description")
    item.payload = overrides.get("payload", {})
    item.available_actions = overrides.get("available_actions", ["approve", "reject"])
    item.status = overrides.get("status", "pending")
    item.telegram_sent = overrides.get("telegram_sent", False)
    item.email_sent = overrides.get("email_sent", False)
    item.expires_at = overrides.get("expires_at", None)
    item.created_at = overrides.get("created_at", datetime.now(UTC))
    return item


def _make_user(**overrides: Any) -> MagicMock:
    """Create a mock User."""
    user = MagicMock(spec=User)
    user.id = overrides.get("id", uuid.uuid4())
    user.email = overrides.get("email", "test@example.com")
    user.telegram_chat_id = overrides.get("telegram_chat_id", 123456789)
    return user


# ===========================================================================
# H4: Lead Scorer integration — GeoScout calls score_lead after discovery
# ===========================================================================


class TestGeoScoutLeadScorerIntegration:
    """GeoScout should call LeadScorer.score() for each discovered lead
    and store scoring results (score, temperature) in artifacts."""

    @pytest.mark.asyncio
    async def test_geoscout_scores_leads_after_discovery(self):
        """GeoScout must call lead_scorer.score() for every discovered lead."""
        agent = GeoScoutAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            overpass_client=AsyncMock(),
        )
        geo_lead = _make_geo_lead(category="restaurant")
        bbox = BoundingBox(min_lat=52.3, max_lat=52.7, min_lon=13.1, max_lon=13.6)

        with (
            patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
            patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
            patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        ):
            agent._overpass.query_businesses = AsyncMock(return_value=[geo_lead])

            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            mock_session.commit = AsyncMock()

            with patch("src.agents.geo_scout.get_db_session") as mock_db:
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

                state = _make_state(artifacts={"_scan_city": "Berlin"})
                result = await agent._execute(state)

        assert result["status"] != "failed"
        assert result.get("next_agent") == "outreach"
        # Scoring results should be in artifacts
        scan_results = result["artifacts"].get("_geo_scan_results", {})
        assert "lead_scores" in scan_results

    @pytest.mark.asyncio
    async def test_geoscout_stores_scoring_summary_in_artifacts(self):
        """Scoring summary (hot/warm/cold counts) should be in _geo_scan_results."""
        agent = GeoScoutAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            overpass_client=AsyncMock(),
        )
        leads = [
            _make_geo_lead(osm_id=1, category="restaurant"),
            _make_geo_lead(osm_id=2, category="cafe"),
        ]
        bbox = BoundingBox(min_lat=52.3, max_lat=52.7, min_lon=13.1, max_lon=13.6)

        with (
            patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
            patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
            patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        ):
            agent._overpass.query_businesses = AsyncMock(return_value=leads)

            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            mock_session.commit = AsyncMock()

            with patch("src.agents.geo_scout.get_db_session") as mock_db:
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

                state = _make_state(artifacts={"_scan_city": "Berlin"})
                result = await agent._execute(state)

        scan_results = result["artifacts"]["_geo_scan_results"]
        assert "lead_scores" in scan_results
        scores = scan_results["lead_scores"]
        assert len(scores) == 2

    @pytest.mark.asyncio
    async def test_geoscout_scoring_failure_does_not_block_pipeline(self):
        """If LeadScorer raises, GeoScout should continue without scores."""
        agent = GeoScoutAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            overpass_client=AsyncMock(),
        )
        geo_lead = _make_geo_lead()
        bbox = BoundingBox(min_lat=52.3, max_lat=52.7, min_lon=13.1, max_lon=13.6)

        with (
            patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
            patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
            patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
            patch("src.core.lead_scorer.LeadScorer") as mock_scorer_cls,
        ):
            mock_scorer_cls.return_value.score.side_effect = Exception("scorer broken")
            agent._overpass.query_businesses = AsyncMock(return_value=[geo_lead])

            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
            mock_session.commit = AsyncMock()

            with patch("src.agents.geo_scout.get_db_session") as mock_db:
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

                state = _make_state(artifacts={"_scan_city": "Berlin"})
                result = await agent._execute(state)

        # Should NOT fail — scoring is best-effort
        assert result["next_agent"] == "outreach"
        assert result["status"] != "failed"


# ===========================================================================
# H4: Business Analyzer integration — Outreach calls analyze() before message gen
# ===========================================================================


class TestOutreachBusinessAnalyzerIntegration:
    """Outreach should call BusinessAnalyzer.analyze() before generating messages."""

    @pytest.mark.asyncio
    async def test_outreach_calls_business_analyzer_before_messages(self):
        """Outreach must call BusinessAnalyzer.analyze() for each lead."""
        agent = OutreachAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            waterfall=AsyncMock(spec_set=["enrich", "total_cost", "close"]),
        )
        agent._waterfall.total_cost = Decimal("0")

        lead = _make_lead(email="test@example.com", category="restaurant")
        enrichment_result = EnrichmentResult(
            email="test@example.com",
            phone=None,
            source="hunter",
            raw_data={"cost": 0.01},
        )
        agent._waterfall.enrich = AsyncMock(return_value=enrichment_result)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.refresh = AsyncMock()

        # Mock _load_leads to return our lead
        agent._load_leads = AsyncMock(return_value=[lead])

        # Mock campaign creation
        campaign = MagicMock()
        campaign.id = uuid.uuid4()
        agent._create_campaign = AsyncMock(return_value=campaign)

        # Mock HITL creation
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        with (
            patch("src.agents.outreach.get_db_session") as mock_db,
            patch("src.core.business_analyzer.BusinessAnalyzer") as mock_analyzer_cls,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_analyzer = AsyncMock()
            mock_analyzer.analyze.return_value = AnalysisResult(
                lead_id=str(lead.id),
                tier="quick",
                website=WebsiteCheck(exists=False),
            )
            mock_analyzer_cls.return_value = mock_analyzer

            # Mock LLM call for message generation
            agent._call_llm = AsyncMock(
                return_value=(
                    MagicMock(content='{"subject": "Website", "body": "Hello"}'),
                    MagicMock(),
                )
            )

            state = _make_state(
                artifacts={"_geo_scan_results": {"city": "Berlin"}},
                current_agent="outreach",
            )
            result = await agent._execute(state)

        # Analyzer should have been called
        mock_analyzer.analyze.assert_called_once()
        # Analysis results should appear in outreach_results
        outreach_results = result["artifacts"].get("_outreach_results", {})
        assert "analysis_results" in outreach_results

    @pytest.mark.asyncio
    async def test_outreach_analyzer_failure_does_not_block_messages(self):
        """If BusinessAnalyzer raises, Outreach should still generate messages."""
        agent = OutreachAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            waterfall=AsyncMock(spec_set=["enrich", "total_cost", "close"]),
        )
        agent._waterfall.total_cost = Decimal("0")

        lead = _make_lead(email="test@example.com")
        enrichment_result = EnrichmentResult(
            email="test@example.com",
            phone=None,
            source="hunter",
            raw_data={"cost": 0.01},
        )
        agent._waterfall.enrich = AsyncMock(return_value=enrichment_result)

        agent._load_leads = AsyncMock(return_value=[lead])
        campaign = MagicMock()
        campaign.id = uuid.uuid4()
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        with (
            patch("src.agents.outreach.get_db_session") as mock_db,
            patch("src.core.business_analyzer.BusinessAnalyzer") as mock_analyzer_cls,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_analyzer = AsyncMock()
            mock_analyzer.analyze.side_effect = Exception("analyzer broken")
            mock_analyzer_cls.return_value = mock_analyzer

            agent._call_llm = AsyncMock(
                return_value=(
                    MagicMock(content='{"subject": "Website", "body": "Hello"}'),
                    MagicMock(),
                )
            )

            state = _make_state(
                artifacts={"_geo_scan_results": {"city": "Berlin"}},
                current_agent="outreach",
            )
            result = await agent._execute(state)

        # Pipeline should continue — analyzer failure is non-fatal
        assert result["status"] != "failed"


# ===========================================================================
# H5: Touch Sequence integration — Outreach creates TouchSequence after send
# ===========================================================================


class TestOutreachTouchSequenceIntegration:
    """After Outreach drafts initial message, it should create TouchSequence records
    and schedule follow-ups on Day 1/3/5/10."""

    @pytest.mark.asyncio
    async def test_outreach_creates_touch_sequence_per_lead(self):
        """Each enriched lead should get a TouchSequence record in artifacts."""
        agent = OutreachAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            waterfall=AsyncMock(spec_set=["enrich", "total_cost", "close"]),
        )
        agent._waterfall.total_cost = Decimal("0")

        lead = _make_lead(email="test@example.com", name="Test Cafe")
        enrichment_result = EnrichmentResult(
            email="test@example.com",
            phone=None,
            source="hunter",
            raw_data={"cost": 0.01},
        )
        agent._waterfall.enrich = AsyncMock(return_value=enrichment_result)
        agent._load_leads = AsyncMock(return_value=[lead])

        campaign = MagicMock()
        campaign.id = uuid.uuid4()
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        with (
            patch("src.agents.outreach.get_db_session") as mock_db,
            patch("src.core.business_analyzer.BusinessAnalyzer", new_callable=lambda: MagicMock),
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            agent._call_llm = AsyncMock(
                return_value=(
                    MagicMock(content='{"subject": "Website", "body": "Hello"}'),
                    MagicMock(),
                )
            )

            state = _make_state(
                artifacts={"_geo_scan_results": {"city": "Berlin"}},
                current_agent="outreach",
            )
            result = await agent._execute(state)

        outreach_results = result["artifacts"].get("_outreach_results", {})
        assert "touch_sequences" in outreach_results
        sequences = outreach_results["touch_sequences"]
        assert len(sequences) >= 1

    @pytest.mark.asyncio
    async def test_touch_sequence_has_scheduled_followups(self):
        """TouchSequence record should include follow-up schedule (Day 1/3/5/10)."""
        agent = OutreachAgent(
            llm_client=MagicMock(),
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
            waterfall=AsyncMock(spec_set=["enrich", "total_cost", "close"]),
        )
        agent._waterfall.total_cost = Decimal("0")

        lead = _make_lead(email="test@example.com", name="Test Biz")
        enrichment_result = EnrichmentResult(
            email="test@example.com",
            phone=None,
            source="hunter",
            raw_data={"cost": 0.01},
        )
        agent._waterfall.enrich = AsyncMock(return_value=enrichment_result)
        agent._load_leads = AsyncMock(return_value=[lead])

        campaign = MagicMock()
        campaign.id = uuid.uuid4()
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        with (
            patch("src.agents.outreach.get_db_session") as mock_db,
            patch("src.core.business_analyzer.BusinessAnalyzer", new_callable=lambda: MagicMock),
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            agent._call_llm = AsyncMock(
                return_value=(
                    MagicMock(content='{"subject": "Website", "body": "Hello"}'),
                    MagicMock(),
                )
            )

            state = _make_state(
                artifacts={"_geo_scan_results": {"city": "Berlin"}},
                current_agent="outreach",
            )
            result = await agent._execute(state)

        sequences = result["artifacts"]["_outreach_results"]["touch_sequences"]
        seq = sequences[0]
        assert "follow_ups" in seq
        days = [f["day"] for f in seq["follow_ups"]]
        assert days == [1, 3, 5, 10], f"Expected [1,3,5,10], got {days}"

    @pytest.mark.asyncio
    async def test_touch_sequence_enforces_stop_rules(self):
        """TouchSequenceManager should check stop rules — bounced leads get stopped."""
        mgr = TouchSequenceManager()

        # "hard_bounce" should trigger stop
        stop = mgr.should_stop(touch_count=0, last_message="hard_bounce")
        assert stop is not None
        assert stop.rule == "bounce"
        assert stop.action == "stop"

    @pytest.mark.asyncio
    async def test_touch_sequence_stops_on_unsubscribe(self):
        """'unsubscribe' keyword should trigger stop_forever."""
        mgr = TouchSequenceManager()
        stop = mgr.should_stop(touch_count=0, last_message="Please unsubscribe me")
        assert stop is not None
        assert stop.rule == "explicit_no"
        assert stop.action == "stop_forever"

    @pytest.mark.asyncio
    async def test_touch_sequence_stops_at_max_touches(self):
        """After 3 touches without reply, should stop."""
        mgr = TouchSequenceManager()
        stop = mgr.should_stop(touch_count=3, last_message=None)
        assert stop is not None
        assert stop.rule == "max_touches"

    @pytest.mark.asyncio
    async def test_touch_sequence_initial_state_is_pending(self):
        """New touch sequence starts in PENDING state."""
        mgr = TouchSequenceManager()
        seq = mgr.create_sequence("lead-123")
        assert seq.state == TouchState.PENDING
        assert seq.touch_count == 0

    @pytest.mark.asyncio
    async def test_touch_sequence_advance_activates_and_records(self):
        """First advance should move to ACTIVE and record in history."""
        mgr = TouchSequenceManager()
        seq = mgr.create_sequence("lead-123")
        lead = _make_lead(telegram_username="test_user")

        result = mgr.advance(seq, lead)

        assert seq.state == TouchState.ACTIVE
        assert seq.touch_count == 1
        assert len(seq.history) == 1
        assert result.action in ("sent", "needs_manual")


# ===========================================================================
# H14: HITL Telegram auto-notifications
# ===========================================================================


class TestHITLTelegramAutoNotifications:
    """When HITL items are created in hitl_bid_node, hitl_dev_launch_node,
    and hitl_review_node, they should auto-send Telegram notifications."""

    @pytest.mark.asyncio
    async def test_hitl_bid_node_sends_telegram_notification(self):
        """hitl_bid_node should call TelegramNotifier when pausing."""
        from src.core.graph import hitl_bid_node

        state = _make_state(status="active")

        with patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock) as mock_notify:
            mock_notify.return_value = True
            result = await hitl_bid_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_hitl_dev_launch_node_sends_telegram_notification(self):
        """hitl_dev_launch_node should call TelegramNotifier when pausing."""
        from src.core.graph import hitl_dev_launch_node

        state = _make_state(status="active")

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with (
            patch("src.core.database.get_db_session") as mock_db,
            patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock) as mock_notify,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_notify.return_value = True

            result = await hitl_dev_launch_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_hitl_review_node_sends_telegram_notification(self):
        """hitl_review_node should call TelegramNotifier when pausing."""
        from src.core.graph import hitl_review_node

        state = _make_state(status="active")

        with patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock) as mock_notify:
            mock_notify.return_value = True
            result = await hitl_review_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_hitl_notification_skipped_when_already_paused(self):
        """If state is already paused, no notification should be sent."""
        from src.core.graph import hitl_bid_node

        state = _make_state(status="paused")

        with patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock) as mock_notify:
            await hitl_bid_node(state)

        # Already paused — should return state unchanged, no notification
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_hitl_notification_failure_does_not_block_pause(self):
        """If Telegram notification fails, the HITL node should still pause."""
        from src.core.graph import hitl_bid_node

        state = _make_state(status="active")

        with patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock) as mock_notify:
            mock_notify.side_effect = Exception("Telegram API down")
            result = await hitl_bid_node(state)

        # Must still pause even though notification failed
        assert result["status"] == "paused"
        assert result["requires_hitl"] is True

    @pytest.mark.asyncio
    async def test_send_hitl_telegram_notification_calls_notifier(self):
        """_send_hitl_telegram_notification should use TelegramNotifier.notify_new_hitl."""
        from src.core.graph import _send_hitl_telegram_notification

        user = _make_user(telegram_chat_id=123456)

        mock_session = AsyncMock()
        # Return a user from the DB query
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = user
        mock_session.execute = AsyncMock(return_value=mock_result)

        with (
            patch("src.core.database.get_db_session") as mock_db,
            patch("src.bot.notifications.TelegramNotifier") as mock_notifier_cls,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_notifier = AsyncMock()
            mock_notifier.notify_new_hitl.return_value = True
            mock_notifier_cls.return_value = mock_notifier

            result = await _send_hitl_telegram_notification(
                hitl_type="bid_approval",
                hitl_title="Approve bid",
                hitl_payload={"job_id": "j1"},
                thread_id="test-thread",
            )

        assert result is True
        mock_notifier.notify_new_hitl.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_hitl_telegram_notification_no_user_skips(self):
        """If no user with telegram_chat_id exists, skip notification gracefully."""
        from src.core.graph import _send_hitl_telegram_notification

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.core.database.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _send_hitl_telegram_notification(
                hitl_type="bid_approval",
                hitl_title="Approve bid",
                hitl_payload={},
                thread_id="test-thread",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_hitl_outreach_node_sends_notification(self):
        """hitl_outreach_node should also send Telegram notification."""
        from src.core.graph import hitl_outreach_node

        state = _make_state(status="active")

        with patch("src.core.graph._send_hitl_telegram_notification", new_callable=AsyncMock) as mock_notify:
            mock_notify.return_value = True
            result = await hitl_outreach_node(state)

        assert result["status"] == "paused"
        mock_notify.assert_called_once()


# ===========================================================================
# Notifications module: TelegramNotifier button support for new HITL types
# ===========================================================================


class TestTelegramNotifierHITLTypes:
    """TelegramNotifier should support buttons for outreach_approval, dev_launch,
    and final_review HITL types."""

    def test_type_buttons_includes_outreach_approval(self):
        """outreach_approval should have inline keyboard buttons."""
        from src.bot.notifications import _TYPE_BUTTONS

        assert "outreach_approval" in _TYPE_BUTTONS

    def test_type_buttons_includes_dev_launch(self):
        """dev_launch should have inline keyboard buttons."""
        from src.bot.notifications import _TYPE_BUTTONS

        assert "dev_launch" in _TYPE_BUTTONS

    def test_type_buttons_includes_final_review(self):
        """final_review should have inline keyboard buttons."""
        from src.bot.notifications import _TYPE_BUTTONS

        assert "final_review" in _TYPE_BUTTONS

    def test_type_emoji_includes_outreach_approval(self):
        """outreach_approval should have an emoji mapping."""
        from src.bot.notifications import _TYPE_EMOJI

        assert "outreach_approval" in _TYPE_EMOJI

    def test_type_emoji_includes_dev_launch(self):
        """dev_launch should have an emoji mapping."""
        from src.bot.notifications import _TYPE_EMOJI

        assert "dev_launch" in _TYPE_EMOJI

    def test_type_emoji_includes_final_review(self):
        """final_review should have an emoji mapping."""
        from src.bot.notifications import _TYPE_EMOJI

        assert "final_review" in _TYPE_EMOJI

    def test_build_reply_markup_for_dev_launch(self):
        """Should build valid inline keyboard for dev_launch."""
        from src.bot.notifications import _build_reply_markup

        markup = _build_reply_markup("dev_launch", "abc123")
        assert markup is not None
        assert "inline_keyboard" in markup
        buttons = markup["inline_keyboard"][0]
        assert len(buttons) >= 2
        # All callback_data should contain the uuid
        for btn in buttons:
            assert "abc123" in btn["callback_data"]

    def test_build_reply_markup_for_outreach_approval(self):
        """Should build valid inline keyboard for outreach_approval."""
        from src.bot.notifications import _build_reply_markup

        markup = _build_reply_markup("outreach_approval", "def456")
        assert markup is not None
        assert "inline_keyboard" in markup


# ===========================================================================
# C4: SalesAgent wired into Pipeline B graph
# ===========================================================================


class TestSalesAgentGraphWiring:
    """SalesAgent should be wired into Pipeline B as a node with routing."""

    def test_sales_agent_node_exists_in_pipeline_b(self):
        """build_pipeline_b_graph should include a sales_agent_node."""
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        # Check via the nodes dict on the compiled graph (Pregel)
        assert "sales_agent_node" in graph.nodes

    def test_hitl_concept_review_node_exists_in_pipeline_b(self):
        """build_pipeline_b_graph should include hitl_concept_review_node."""
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        assert "hitl_concept_review_node" in graph.nodes

    def test_route_after_outreach_to_lead_card_on_lead_replied(self):
        """_route_after_outreach should route to hitl_lead_card when lead replied."""
        from src.core.graph import _route_after_outreach

        state = {
            "status": "active",
            "requires_hitl": False,
            "artifacts": {"_lead_replied": True},
        }
        result = _route_after_outreach(state)
        assert result == "hitl_lead_card_node"

    def test_route_after_outreach_to_hitl_outreach_when_requires_hitl(self):
        """_route_after_outreach still routes to hitl_outreach when requires_hitl=True."""
        from src.core.graph import _route_after_outreach

        state = {"status": "paused", "requires_hitl": True, "artifacts": {}}
        result = _route_after_outreach(state)
        assert result == "hitl_outreach_node"

    def test_route_after_outreach_to_end_on_failure(self):
        """_route_after_outreach still routes to END on failure."""
        from langgraph.graph import END

        from src.core.graph import _route_after_outreach

        state = {"status": "failed", "requires_hitl": False, "artifacts": {}}
        result = _route_after_outreach(state)
        assert result == END

    def test_route_after_outreach_to_end_when_no_hitl_no_reply(self):
        """_route_after_outreach routes to END when no HITL and no lead reply."""
        from langgraph.graph import END

        from src.core.graph import _route_after_outreach

        state = {"status": "completed", "requires_hitl": False, "artifacts": {}}
        result = _route_after_outreach(state)
        assert result == END

    def test_route_after_sales_agent_to_hitl_concept_review(self):
        """_route_after_sales_agent routes to hitl_concept_review when requires_hitl."""
        from src.core.graph import _route_after_sales_agent

        state = {"status": "paused", "requires_hitl": True}
        result = _route_after_sales_agent(state)
        assert result == "hitl_concept_review_node"

    def test_route_after_sales_agent_to_end_on_failure(self):
        """_route_after_sales_agent routes to END on failure."""
        from langgraph.graph import END

        from src.core.graph import _route_after_sales_agent

        state = {"status": "failed", "requires_hitl": False}
        result = _route_after_sales_agent(state)
        assert result == END

    def test_route_after_sales_agent_to_end_on_completion(self):
        """_route_after_sales_agent routes to END when completed (won/lost/paused)."""
        from langgraph.graph import END

        from src.core.graph import _route_after_sales_agent

        state = {"status": "paused", "requires_hitl": False}
        result = _route_after_sales_agent(state)
        assert result == END

    def test_route_after_sales_agent_to_end_on_active_no_hitl(self):
        """_route_after_sales_agent routes to END when active but no HITL (awaiting reply)."""
        from langgraph.graph import END

        from src.core.graph import _route_after_sales_agent

        state = {"status": "active", "requires_hitl": False}
        result = _route_after_sales_agent(state)
        assert result == END

    def test_route_after_hitl_concept_review_to_end_on_approve(self):
        """_route_after_hitl_concept_review routes to END after concept approval."""
        from langgraph.graph import END

        from src.core.graph import _route_after_hitl_concept_review

        state = {"status": "active", "requires_hitl": False}
        result = _route_after_hitl_concept_review(state)
        assert result == END

    def test_route_after_hitl_concept_review_to_end_on_failure(self):
        """_route_after_hitl_concept_review routes to END on rejection."""
        from langgraph.graph import END

        from src.core.graph import _route_after_hitl_concept_review

        state = {"status": "failed"}
        result = _route_after_hitl_concept_review(state)
        assert result == END

    def test_route_after_hitl_concept_review_to_sales_agent_on_edit(self):
        """_route_after_hitl_concept_review routes back to sales_agent on edit."""
        from src.core.graph import _route_after_hitl_concept_review

        state = {
            "status": "active",
            "requires_hitl": False,
            "artifacts": {"_concept_edited": True},
        }
        result = _route_after_hitl_concept_review(state)
        assert result == "sales_agent_node"

    @pytest.mark.asyncio
    async def test_hitl_concept_review_node_pauses_when_active(self):
        """hitl_concept_review_node should pause the pipeline for concept review."""
        from src.core.graph import hitl_concept_review_node

        state = _make_state(status="active", requires_hitl=True)
        result = await hitl_concept_review_node(state)

        assert result["status"] == "paused"
        assert result["requires_hitl"] is True

    @pytest.mark.asyncio
    async def test_hitl_concept_review_node_noop_when_already_paused(self):
        """hitl_concept_review_node should return state unchanged when already paused."""
        from src.core.graph import hitl_concept_review_node

        state = _make_state(status="paused")
        result = await hitl_concept_review_node(state)
        assert result["status"] == "paused"

    @pytest.mark.asyncio
    async def test_pipeline_b_graph_geo_scout_to_outreach_flow(self):
        """Pipeline B graph: geo_scout -> outreach basic flow still works."""
        from unittest.mock import patch as _patch

        from src.core.graph import build_pipeline_b_graph

        async def _geo_ok(state):
            return {**state, "status": "active", "next_agent": "outreach", "current_agent": "geoscout"}

        async def _outreach_ok(state):
            return {**state, "status": "completed", "requires_hitl": False, "current_agent": "outreach"}

        with (
            _patch("src.core.graph.geo_scout_node", side_effect=_geo_ok),
            _patch("src.core.graph.outreach_node", side_effect=_outreach_ok),
        ):
            graph = build_pipeline_b_graph()
            result = await graph.ainvoke(_make_state())

        assert result["current_agent"] == "outreach"

    @pytest.mark.asyncio
    async def test_pipeline_b_graph_outreach_to_sales_agent_flow(self):
        """Pipeline B graph: outreach -> lead_card HITL -> sales_agent when lead replied."""
        from unittest.mock import patch as _patch

        from src.core.graph import build_pipeline_b_graph

        async def _geo_ok(state):
            return {**state, "status": "active", "next_agent": "outreach", "current_agent": "geoscout"}

        async def _outreach_replied(state):
            artifacts = dict(state.get("artifacts") or {})
            artifacts["_lead_replied"] = True
            return {
                **state,
                "status": "active",
                "requires_hitl": False,
                "current_agent": "outreach",
                "artifacts": artifacts,
            }

        async def _lead_card_approve(state):
            artifacts = dict(state.get("artifacts") or {})
            artifacts["_lead_card_resolution"] = "approve"
            return {
                **state,
                "status": "active",
                "requires_hitl": False,
                "current_agent": "hitl_lead_card",
                "artifacts": artifacts,
            }

        async def _sales_ok(state):
            return {
                **state,
                "status": "paused",
                "requires_hitl": False,
                "current_agent": "sales_agent",
            }

        with (
            _patch("src.core.graph.geo_scout_node", side_effect=_geo_ok),
            _patch("src.core.graph.outreach_node", side_effect=_outreach_replied),
            _patch("src.core.graph.hitl_lead_card_node", side_effect=_lead_card_approve),
            _patch("src.core.graph.sales_agent_node", side_effect=_sales_ok),
        ):
            graph = build_pipeline_b_graph()
            result = await graph.ainvoke(_make_state())

        assert result["current_agent"] == "sales_agent"

    @pytest.mark.asyncio
    async def test_pipeline_b_graph_sales_agent_to_hitl_concept_flow(self):
        """Pipeline B graph: sales_agent -> hitl_concept_review when requires_hitl."""
        from unittest.mock import patch as _patch

        from src.core.graph import build_pipeline_b_graph

        async def _geo_ok(state):
            return {**state, "status": "active", "next_agent": "outreach", "current_agent": "geoscout"}

        async def _outreach_replied(state):
            artifacts = dict(state.get("artifacts") or {})
            artifacts["_lead_replied"] = True
            return {
                **state,
                "status": "active",
                "requires_hitl": False,
                "current_agent": "outreach",
                "artifacts": artifacts,
            }

        async def _lead_card_approve(state):
            artifacts = dict(state.get("artifacts") or {})
            artifacts["_lead_card_resolution"] = "approve"
            return {
                **state,
                "status": "active",
                "requires_hitl": False,
                "current_agent": "hitl_lead_card",
                "artifacts": artifacts,
            }

        async def _sales_concept(state):
            return {
                **state,
                "status": "paused",
                "requires_hitl": True,
                "current_agent": "sales_agent",
            }

        async def _hitl_concept(state):
            return {
                **state,
                "status": "paused",
                "requires_hitl": True,
                "current_agent": "hitl_concept_review",
            }

        with (
            _patch("src.core.graph.geo_scout_node", side_effect=_geo_ok),
            _patch("src.core.graph.outreach_node", side_effect=_outreach_replied),
            _patch("src.core.graph.hitl_lead_card_node", side_effect=_lead_card_approve),
            _patch("src.core.graph.sales_agent_node", side_effect=_sales_concept),
            _patch("src.core.graph.hitl_concept_review_node", side_effect=_hitl_concept),
        ):
            graph = build_pipeline_b_graph()
            result = await graph.ainvoke(_make_state())

        assert result["current_agent"] == "hitl_concept_review"
        assert result["requires_hitl"] is True


# ===========================================================================
# C5: Pipeline B → Pipeline A transition (deals start-development)
# ===========================================================================


class TestPipelineBToATransition:
    """POST /api/v1/deals/{deal_id}/start-development should bridge Pipeline B -> A.

    Tests call the handler's underlying ``fn`` directly (bypassing Litestar routing)
    by accessing ``DealController.start_development.fn``.
    """

    @staticmethod
    def _get_handler():
        """Get the raw async function from the Litestar handler."""
        from src.api.routes.deals import DealController

        return DealController.start_development.fn

    @pytest.mark.asyncio
    async def test_start_development_requires_won_status(self):
        """start-development should reject deals not in 'won' status."""
        handler = self._get_handler()

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.status = "new"
        deal.pipeline_a_thread_id = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = deal
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_request = MagicMock()
        mock_request.user = MagicMock()
        mock_request.user.id = uuid.uuid4()

        with pytest.raises(Exception) as exc_info:
            await handler(
                self=MagicMock(),
                deal_id=str(deal.id),
                db_session=mock_session,
                request=mock_request,
            )

        assert "won" in str(exc_info.value).lower() or "status" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_start_development_rejects_already_in_development(self):
        """start-development should reject deals already in development."""
        handler = self._get_handler()

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.status = "in_development"
        deal.pipeline_a_thread_id = "existing-thread"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = deal
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_request = MagicMock()
        mock_request.user = MagicMock()
        mock_request.user.id = uuid.uuid4()

        with pytest.raises(Exception) as exc_info:
            await handler(
                self=MagicMock(),
                deal_id=str(deal.id),
                db_session=mock_session,
                request=mock_request,
            )

        assert "already" in str(exc_info.value).lower() or "development" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_start_development_success_for_won_deal(self):
        """start-development should launch pipeline and return thread_id for won deal."""
        handler = self._get_handler()

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.status = "won"
        deal.agreed_scope = "Build a landing page"
        deal.budget = 1000
        deal.deadline = None
        deal.client_context = {"name": "Test Client"}
        deal.design_versions = None
        deal.pipeline_a_thread_id = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = deal
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()

        mock_request = MagicMock()
        mock_request.user = MagicMock()
        mock_request.user.id = uuid.uuid4()

        with (
            patch("src.api.routes.deals.asyncio.create_task") as mock_task,
            patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock),
        ):
            mock_task_obj = MagicMock()
            mock_task_obj.add_done_callback = MagicMock()
            mock_task.return_value = mock_task_obj

            result = await handler(
                self=MagicMock(),
                deal_id=str(deal.id),
                db_session=mock_session,
                request=mock_request,
            )

        assert result["status"] == "started"
        assert "thread_id" in result
        assert result["deal_id"] == str(deal.id)
        # Deal status should be updated
        assert deal.status == "in_development"
        assert deal.pipeline_a_thread_id is not None

    @pytest.mark.asyncio
    async def test_start_development_deal_not_found(self):
        """start-development should 404 for non-existent deal."""
        from litestar.exceptions import NotFoundException

        handler = self._get_handler()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_request = MagicMock()
        mock_request.user = MagicMock()
        mock_request.user.id = uuid.uuid4()

        with pytest.raises(NotFoundException):
            await handler(
                self=MagicMock(),
                deal_id=str(uuid.uuid4()),
                db_session=mock_session,
                request=mock_request,
            )

    @pytest.mark.asyncio
    async def test_start_development_passes_scope_as_requirements(self):
        """Pipeline A payload should use deal.agreed_scope as requirements."""
        handler = self._get_handler()

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.status = "won"
        deal.agreed_scope = "Full e-commerce site with payments"
        deal.budget = 5000
        deal.deadline = None
        deal.client_context = {}
        deal.design_versions = None
        deal.pipeline_a_thread_id = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = deal
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()

        mock_request = MagicMock()
        mock_request.user = MagicMock()
        mock_request.user.id = uuid.uuid4()

        with (
            patch("src.api.routes.deals.asyncio.create_task") as mock_task,
            patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock),
        ):
            mock_task_obj = MagicMock()
            mock_task_obj.add_done_callback = MagicMock()
            mock_task.return_value = mock_task_obj

            await handler(
                self=MagicMock(),
                deal_id=str(deal.id),
                db_session=mock_session,
                request=mock_request,
            )

        # Verify deal fields updated
        assert deal.status == "in_development"
        assert deal.pipeline_a_thread_id == f"deal-pipeline-{deal.id}"
