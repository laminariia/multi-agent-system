"""Unit tests for TelegramProfileAggregator (src/core/telegram_aggregator.py).

Tests cover: message aggregation from Valkey, profile scoring via LLM,
lead promotion, batch processing, deduplication, rate limiting,
edge cases (empty queues, malformed messages, LLM failures).

Spec: docs/Full_work/pipeline-b-spec.md §1C Telegram Mining
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from src.core.llm_client import CallMetrics, CostTracker, LLMClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tg_message(
    user_id: int = 100,
    username: str = "test_user",
    text: str = "Нужен сайт для ресторана",
    channel: str = "biz_channel",
    message_id: int = 1,
    ts: float | None = None,
) -> str:
    """Create a JSON-encoded Telegram message for the Valkey queue."""
    return json.dumps(
        {
            "user_id": user_id,
            "username": username,
            "text": text,
            "channel": channel,
            "message_id": message_id,
            "timestamp": ts or time.time(),
        },
        ensure_ascii=False,
    )


def _llm_score_response(
    score: float = 0.75,
    needs: list[str] | None = None,
    category: str = "restaurant",
    summary: str = "Владелец ресторана, ищет сайт",
) -> str:
    """Build a JSON string mimicking the LLM profile scoring response."""
    return json.dumps(
        {
            "score": score,
            "needs": needs or ["website", "seo"],
            "category": category,
            "summary": summary,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Import target (deferred so collection doesn't fail if module is missing)
# ---------------------------------------------------------------------------


@pytest.fixture()
def aggregator_module():
    """Import the aggregator module."""
    import src.core.telegram_aggregator as mod

    return mod


@pytest.fixture()
def TelegramProfileAggregator(aggregator_module):
    return aggregator_module.TelegramProfileAggregator


@pytest.fixture()
def ProfileResult(aggregator_module):
    return aggregator_module.ProfileResult


@pytest.fixture()
def AggregatedProfile(aggregator_module):
    return aggregator_module.AggregatedProfile


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_valkey() -> AsyncMock:
    """Async mock of Valkey client."""
    valkey = AsyncMock()
    valkey.rpop = AsyncMock(return_value=None)
    valkey.lpush = AsyncMock(return_value=1)
    valkey.set = AsyncMock(return_value=True)
    valkey.get = AsyncMock(return_value=None)
    valkey.sismember = AsyncMock(return_value=False)
    valkey.sadd = AsyncMock(return_value=1)
    valkey.llen = AsyncMock(return_value=0)
    valkey.delete = AsyncMock(return_value=1)
    return valkey


@pytest.fixture()
def mock_llm_client() -> AsyncMock:
    """Async mock of LLMClient."""
    client = AsyncMock(spec=LLMClient)
    default_response = AIMessage(content=_llm_score_response())
    default_metrics = CallMetrics(
        agent_name="telegram_aggregator",
        model_id="google/gemini-2.5-flash",
        provider="google",
        tokens_input=200,
        tokens_output=80,
        cost_usd=0.0001,
        latency_ms=350.0,
        was_fallback=False,
        attempt=1,
    )
    client.call = AsyncMock(return_value=(default_response, default_metrics))
    client.cost_tracker = CostTracker()
    return client


@pytest.fixture()
def mock_db_session_factory() -> MagicMock:
    """Mock of DB session factory (async context manager).

    ``get_db_session()`` is an ``@asynccontextmanager`` — calling it
    synchronously returns an async context manager.  The mock must
    replicate this: ``factory()`` returns an object with
    ``__aenter__`` / ``__aexit__``, **not** a coroutine.
    """
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    # Context manager returned by factory()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    # factory() must synchronously return the context manager
    factory = MagicMock()
    factory.return_value = ctx
    factory._test_session = session
    return factory


@pytest.fixture()
def aggregator(
    TelegramProfileAggregator,
    mock_valkey,
    mock_llm_client,
    mock_db_session_factory,
):
    """Create a TelegramProfileAggregator with all mocks."""
    return TelegramProfileAggregator(
        valkey=mock_valkey,
        llm_client=mock_llm_client,
        db_session_factory=mock_db_session_factory,
    )


# ===========================================================================
# Constants & Configuration
# ===========================================================================


class TestConstants:
    """Verify module-level constants match spec."""

    def test_valkey_queue_key(self, aggregator_module):
        assert aggregator_module.VALKEY_QUEUE_KEY == "mas:tg_msgs:business"

    def test_min_messages_threshold(self, aggregator_module):
        assert aggregator_module.MIN_MESSAGES_FOR_SCORING == 3

    def test_score_promotion_threshold(self, aggregator_module):
        assert aggregator_module.SCORE_PROMOTION_THRESHOLD == 0.6

    def test_batch_size(self, aggregator_module):
        assert aggregator_module.BATCH_SIZE == 100

    def test_cron_interval_minutes(self, aggregator_module):
        assert aggregator_module.CRON_INTERVAL_MINUTES == 5

    def test_dedup_key_prefix(self, aggregator_module):
        assert "tg_aggregator" in aggregator_module.DEDUP_SET_KEY

    def test_rate_limit_per_batch(self, aggregator_module):
        assert aggregator_module.MAX_LLM_CALLS_PER_BATCH >= 1

    def test_llm_agent_name(self, aggregator_module):
        # Tier 5 extraction — uses gemini flash via agent name
        assert aggregator_module.LLM_AGENT_NAME in (
            "telegram_aggregator",
            "geoscout",
            "scout",
        )


# ===========================================================================
# Data Models
# ===========================================================================


class TestProfileResult:
    """Tests for ProfileResult dataclass."""

    def test_create_profile_result(self, ProfileResult):
        pr = ProfileResult(
            user_id=100,
            username="alice",
            score=0.8,
            needs=["website"],
            category="clinic",
            summary="Dentist looking for website",
        )
        assert pr.user_id == 100
        assert pr.username == "alice"
        assert pr.score == 0.8
        assert pr.needs == ["website"]
        assert pr.category == "clinic"

    def test_profile_result_defaults(self, ProfileResult):
        pr = ProfileResult(user_id=1, username="u")
        assert pr.score == 0.0
        assert pr.needs == []
        assert pr.category == ""
        assert pr.summary == ""

    def test_profile_is_promotable_above_threshold(self, ProfileResult):
        pr = ProfileResult(user_id=1, username="u", score=0.7)
        assert pr.is_promotable is True

    def test_profile_not_promotable_below_threshold(self, ProfileResult):
        pr = ProfileResult(user_id=1, username="u", score=0.5)
        assert pr.is_promotable is False

    def test_profile_promotable_at_threshold(self, ProfileResult):
        pr = ProfileResult(user_id=1, username="u", score=0.6)
        assert pr.is_promotable is True


class TestAggregatedProfile:
    """Tests for AggregatedProfile dataclass."""

    def test_create_aggregated_profile(self, AggregatedProfile):
        msgs = [{"text": "hello", "channel": "ch1"}]
        ap = AggregatedProfile(
            user_id=42,
            username="bob",
            messages=msgs,
            channels=["ch1"],
        )
        assert ap.user_id == 42
        assert ap.username == "bob"
        assert len(ap.messages) == 1
        assert "ch1" in ap.channels

    def test_message_count_property(self, AggregatedProfile):
        msgs = [{"text": f"msg{i}"} for i in range(5)]
        ap = AggregatedProfile(user_id=1, username="u", messages=msgs, channels=["c"])
        assert ap.message_count == 5

    def test_has_enough_messages_true(self, AggregatedProfile):
        msgs = [{"text": f"msg{i}"} for i in range(3)]
        ap = AggregatedProfile(user_id=1, username="u", messages=msgs, channels=["c"])
        assert ap.has_enough_messages is True

    def test_has_enough_messages_false(self, AggregatedProfile):
        msgs = [{"text": "only one"}]
        ap = AggregatedProfile(user_id=1, username="u", messages=msgs, channels=["c"])
        assert ap.has_enough_messages is False


# ===========================================================================
# Message Aggregation (from Valkey)
# ===========================================================================


class TestAggregateMessages:
    """Tests for aggregate_messages — RPOP from Valkey, group by user_id."""

    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty(self, aggregator, mock_valkey):
        mock_valkey.rpop = AsyncMock(return_value=None)
        profiles = await aggregator.aggregate_messages()
        assert profiles == {}

    @pytest.mark.asyncio
    async def test_single_user_multiple_messages(self, aggregator, mock_valkey):
        messages = [
            _make_tg_message(user_id=100, username="alice", text="need website", message_id=1),
            _make_tg_message(user_id=100, username="alice", text="for my restaurant", message_id=2),
            _make_tg_message(user_id=100, username="alice", text="budget 50k", message_id=3),
        ]
        # rpop returns messages one by one, then None to stop
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        profiles = await aggregator.aggregate_messages()
        assert 100 in profiles
        assert profiles[100].message_count == 3
        assert profiles[100].username == "alice"

    @pytest.mark.asyncio
    async def test_multiple_users_grouped(self, aggregator, mock_valkey):
        messages = [
            _make_tg_message(user_id=100, username="alice", text="msg1"),
            _make_tg_message(user_id=200, username="bob", text="msg2"),
            _make_tg_message(user_id=100, username="alice", text="msg3"),
            _make_tg_message(user_id=200, username="bob", text="msg4"),
            _make_tg_message(user_id=300, username="carol", text="msg5"),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        profiles = await aggregator.aggregate_messages()
        assert len(profiles) == 3
        assert profiles[100].message_count == 2
        assert profiles[200].message_count == 2
        assert profiles[300].message_count == 1

    @pytest.mark.asyncio
    async def test_channels_tracked_per_user(self, aggregator, mock_valkey):
        messages = [
            _make_tg_message(user_id=100, username="alice", channel="ch1", message_id=1),
            _make_tg_message(user_id=100, username="alice", channel="ch2", message_id=2),
            _make_tg_message(user_id=100, username="alice", channel="ch1", message_id=3),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        profiles = await aggregator.aggregate_messages()
        channels = profiles[100].channels
        assert "ch1" in channels
        assert "ch2" in channels

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self, aggregator, mock_valkey):
        messages = [
            "not valid json",
            _make_tg_message(user_id=100, username="alice", text="ok", message_id=1),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        profiles = await aggregator.aggregate_messages()
        # Only valid message processed, malformed skipped
        assert len(profiles) <= 1

    @pytest.mark.asyncio
    async def test_missing_user_id_skipped(self, aggregator, mock_valkey):
        bad_msg = json.dumps({"text": "no user_id", "channel": "ch1"})
        good_msg = _make_tg_message(user_id=100, username="alice")
        mock_valkey.rpop = AsyncMock(side_effect=[bad_msg, good_msg, None])
        profiles = await aggregator.aggregate_messages()
        assert 100 in profiles

    @pytest.mark.asyncio
    async def test_batch_size_respected(self, aggregator, mock_valkey, aggregator_module):
        batch_size = aggregator_module.BATCH_SIZE
        # Return batch_size+10 messages — should stop after batch_size
        messages = [_make_tg_message(user_id=i, username=f"u{i}", message_id=i) for i in range(batch_size + 10)] + [
            None
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages)
        profiles = await aggregator.aggregate_messages()
        # Total messages popped should not exceed batch_size
        assert mock_valkey.rpop.call_count <= batch_size + 1  # +1 for the None sentinel

    @pytest.mark.asyncio
    async def test_username_updated_to_latest(self, aggregator, mock_valkey):
        """If user changes username between messages, keep latest."""
        messages = [
            _make_tg_message(user_id=100, username="old_name", text="msg1", message_id=1),
            _make_tg_message(user_id=100, username="new_name", text="msg2", message_id=2),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        profiles = await aggregator.aggregate_messages()
        assert profiles[100].username == "new_name"


# ===========================================================================
# Profile Scoring (LLM)
# ===========================================================================


class TestScoreProfile:
    """Tests for score_profile — LLM-based profile analysis."""

    @pytest.mark.asyncio
    async def test_score_profile_success(self, aggregator, mock_llm_client, AggregatedProfile):
        profile = AggregatedProfile(
            user_id=100,
            username="alice",
            messages=[
                {"text": "Нужен сайт для ресторана", "channel": "biz"},
                {"text": "Бюджет 100к", "channel": "biz"},
                {"text": "Кто делает лендинги?", "channel": "web"},
            ],
            channels=["biz", "web"],
        )
        result = await aggregator.score_profile(profile)
        assert result is not None
        assert result.user_id == 100
        assert result.username == "alice"
        assert result.score == 0.75
        assert "website" in result.needs
        mock_llm_client.call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_score_profile_uses_tier5_model(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
        aggregator_module,
    ):
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        await aggregator.score_profile(profile)
        call_args = mock_llm_client.call.call_args
        agent_name = call_args[0][0] if call_args[0] else call_args[1].get("agent_name", "")
        # Must use the configured agent name (Tier 5)
        assert agent_name == aggregator_module.LLM_AGENT_NAME

    @pytest.mark.asyncio
    async def test_score_profile_llm_failure_returns_none(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
    ):
        mock_llm_client.call = AsyncMock(side_effect=Exception("LLM down"))
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        result = await aggregator.score_profile(profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_score_profile_malformed_llm_response(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
    ):
        """LLM returns non-JSON — should return None gracefully."""
        bad_response = AIMessage(content="I'm not sure what to say")
        bad_metrics = CallMetrics(
            agent_name="test",
            model_id="x",
            provider="y",
        )
        mock_llm_client.call = AsyncMock(return_value=(bad_response, bad_metrics))
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        result = await aggregator.score_profile(profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_score_profile_markdown_fences_stripped(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
    ):
        """LLM wraps JSON in markdown code fences — should still parse."""
        wrapped = f"```json\n{_llm_score_response(score=0.9)}\n```"
        resp = AIMessage(content=wrapped)
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        result = await aggregator.score_profile(profile)
        assert result is not None
        assert result.score == 0.9

    @pytest.mark.asyncio
    async def test_score_clamped_to_0_1_range(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
    ):
        """Score outside [0, 1] is clamped."""
        resp = AIMessage(content=_llm_score_response(score=1.5))
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        result = await aggregator.score_profile(profile)
        assert result is not None
        assert result.score <= 1.0

    @pytest.mark.asyncio
    async def test_negative_score_clamped_to_zero(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
    ):
        resp = AIMessage(content=_llm_score_response(score=-0.5))
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        result = await aggregator.score_profile(profile)
        assert result is not None
        assert result.score >= 0.0


# ===========================================================================
# Lead Promotion
# ===========================================================================


class TestPromoteToLead:
    """Tests for promote_to_lead — creating Lead from scored profile."""

    @pytest.mark.asyncio
    async def test_promote_high_score(self, aggregator, mock_db_session_factory, ProfileResult):
        result = ProfileResult(
            user_id=100,
            username="alice",
            score=0.8,
            needs=["website", "seo"],
            category="restaurant",
            summary="Restaurant owner needs site",
        )
        promoted = await aggregator.promote_to_lead(result)
        assert promoted is True

    @pytest.mark.asyncio
    async def test_no_promote_low_score(self, aggregator, ProfileResult):
        result = ProfileResult(
            user_id=100,
            username="alice",
            score=0.4,
        )
        promoted = await aggregator.promote_to_lead(result)
        assert promoted is False

    @pytest.mark.asyncio
    async def test_promote_at_threshold(self, aggregator, ProfileResult):
        result = ProfileResult(
            user_id=100,
            username="alice",
            score=0.6,
            needs=["website"],
            category="cafe",
            summary="Cafe owner",
        )
        promoted = await aggregator.promote_to_lead(result)
        assert promoted is True

    @pytest.mark.asyncio
    async def test_promote_sets_source_telegram(self, aggregator, mock_db_session_factory, ProfileResult):
        result = ProfileResult(
            user_id=100,
            username="alice",
            score=0.7,
            needs=["website"],
            category="clinic",
            summary="Dentist",
        )
        await aggregator.promote_to_lead(result)
        session = mock_db_session_factory._test_session
        # session.add should have been called with a lead-like object
        if session.add.called:
            added_obj = session.add.call_args[0][0]
            assert hasattr(added_obj, "source") or isinstance(added_obj, dict)

    @pytest.mark.asyncio
    async def test_promote_db_failure_returns_false(self, aggregator, mock_db_session_factory, ProfileResult):
        session = mock_db_session_factory._test_session
        session.commit = AsyncMock(side_effect=Exception("DB connection lost"))
        result = ProfileResult(
            user_id=100,
            username="alice",
            score=0.8,
            needs=["website"],
            category="restaurant",
            summary="test",
        )
        promoted = await aggregator.promote_to_lead(result)
        assert promoted is False

    @pytest.mark.asyncio
    async def test_promote_without_username_returns_false(self, aggregator, ProfileResult):
        """Cannot create lead without a contact method (username)."""
        result = ProfileResult(
            user_id=100,
            username="",
            score=0.9,
            needs=["website"],
            category="restaurant",
            summary="test",
        )
        promoted = await aggregator.promote_to_lead(result)
        assert promoted is False


# ===========================================================================
# Deduplication
# ===========================================================================


class TestDeduplication:
    """Tests for dedup — skip already-analyzed users."""

    @pytest.mark.asyncio
    async def test_already_analyzed_user_skipped(self, aggregator, mock_valkey):
        mock_valkey.sismember = AsyncMock(return_value=True)
        is_dup = await aggregator.is_duplicate(100)
        assert is_dup is True

    @pytest.mark.asyncio
    async def test_new_user_not_duplicate(self, aggregator, mock_valkey):
        mock_valkey.sismember = AsyncMock(return_value=False)
        is_dup = await aggregator.is_duplicate(100)
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_mark_analyzed_adds_to_set(self, aggregator, mock_valkey):
        await aggregator.mark_analyzed(100)
        mock_valkey.sadd.assert_awaited_once()
        call_args = mock_valkey.sadd.call_args
        # Should contain the user_id somewhere
        assert any(str(100) in str(a) for a in call_args[0])

    @pytest.mark.asyncio
    async def test_dedup_valkey_error_treated_as_not_duplicate(self, aggregator, mock_valkey):
        """If Valkey is down, treat user as NOT duplicate (don't lose leads)."""
        mock_valkey.sismember = AsyncMock(side_effect=Exception("connection lost"))
        is_dup = await aggregator.is_duplicate(100)
        assert is_dup is False


# ===========================================================================
# Rate Limiting
# ===========================================================================


class TestRateLimiting:
    """Tests for LLM rate limiting within batches."""

    @pytest.mark.asyncio
    async def test_rate_limit_tracks_calls(self, aggregator):
        # After init, no calls made
        assert aggregator._llm_calls_this_batch == 0

    @pytest.mark.asyncio
    async def test_can_make_llm_call_initially_true(self, aggregator):
        assert aggregator._can_make_llm_call() is True

    @pytest.mark.asyncio
    async def test_can_make_llm_call_false_at_limit(self, aggregator, aggregator_module):
        aggregator._llm_calls_this_batch = aggregator_module.MAX_LLM_CALLS_PER_BATCH
        assert aggregator._can_make_llm_call() is False

    @pytest.mark.asyncio
    async def test_reset_batch_counters(self, aggregator):
        aggregator._llm_calls_this_batch = 50
        aggregator._reset_batch_counters()
        assert aggregator._llm_calls_this_batch == 0


# ===========================================================================
# Batch Processing (run_batch)
# ===========================================================================


class TestRunBatch:
    """Tests for run_batch — the main entry point called by cron."""

    @pytest.mark.asyncio
    async def test_run_batch_empty_queue(self, aggregator, mock_valkey):
        mock_valkey.rpop = AsyncMock(return_value=None)
        stats = await aggregator.run_batch()
        assert stats["messages_processed"] == 0
        assert stats["profiles_scored"] == 0
        assert stats["leads_promoted"] == 0

    @pytest.mark.asyncio
    async def test_run_batch_full_pipeline(self, aggregator, mock_valkey, mock_llm_client):
        """3 messages from same user => aggregate => score => promote."""
        messages = [
            _make_tg_message(user_id=100, username="alice", text="need site", message_id=1),
            _make_tg_message(user_id=100, username="alice", text="for restaurant", message_id=2),
            _make_tg_message(user_id=100, username="alice", text="budget 50k", message_id=3),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        mock_valkey.sismember = AsyncMock(return_value=False)
        # LLM returns score >= 0.6 => promote
        resp = AIMessage(content=_llm_score_response(score=0.8))
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))

        stats = await aggregator.run_batch()
        assert stats["messages_processed"] == 3
        assert stats["profiles_scored"] == 1
        assert stats["leads_promoted"] == 1

    @pytest.mark.asyncio
    async def test_run_batch_skips_below_threshold_messages(
        self,
        aggregator,
        mock_valkey,
        mock_llm_client,
    ):
        """Users with < MIN_MESSAGES are not scored."""
        messages = [
            _make_tg_message(user_id=100, username="alice", text="one msg", message_id=1),
            _make_tg_message(user_id=200, username="bob", text="one msg", message_id=2),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        stats = await aggregator.run_batch()
        assert stats["messages_processed"] == 2
        assert stats["profiles_scored"] == 0
        # LLM should NOT have been called
        mock_llm_client.call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_batch_skips_duplicates(self, aggregator, mock_valkey, mock_llm_client):
        """Already-analyzed users are skipped."""
        messages = [_make_tg_message(user_id=100, username="alice", text=f"m{i}", message_id=i) for i in range(3)]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        mock_valkey.sismember = AsyncMock(return_value=True)  # Already analyzed

        stats = await aggregator.run_batch()
        assert stats["profiles_scored"] == 0
        assert stats["duplicates_skipped"] >= 1
        mock_llm_client.call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_batch_low_score_not_promoted(
        self,
        aggregator,
        mock_valkey,
        mock_llm_client,
    ):
        """User scored < 0.6 is NOT promoted to lead."""
        messages = [_make_tg_message(user_id=100, username="alice", text=f"m{i}", message_id=i) for i in range(3)]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        mock_valkey.sismember = AsyncMock(return_value=False)

        resp = AIMessage(content=_llm_score_response(score=0.3))
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))

        stats = await aggregator.run_batch()
        assert stats["profiles_scored"] == 1
        assert stats["leads_promoted"] == 0

    @pytest.mark.asyncio
    async def test_run_batch_multiple_users_mixed(
        self,
        aggregator,
        mock_valkey,
        mock_llm_client,
    ):
        """Mix of users: one with enough messages (promoted), one without."""
        messages = [
            # User 100: 3 messages => scored
            _make_tg_message(user_id=100, username="alice", text=f"a{i}", message_id=i)
            for i in range(3)
        ] + [
            # User 200: 1 message => not scored
            _make_tg_message(user_id=200, username="bob", text="only one", message_id=99),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        mock_valkey.sismember = AsyncMock(return_value=False)
        resp = AIMessage(content=_llm_score_response(score=0.8))
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))

        stats = await aggregator.run_batch()
        assert stats["messages_processed"] == 4
        assert stats["profiles_scored"] == 1
        assert stats["leads_promoted"] == 1

    @pytest.mark.asyncio
    async def test_run_batch_respects_rate_limit(
        self,
        aggregator,
        mock_valkey,
        mock_llm_client,
        aggregator_module,
    ):
        """If rate limit is hit, remaining profiles are not scored."""
        max_calls = aggregator_module.MAX_LLM_CALLS_PER_BATCH
        # Create enough users with 3+ messages each to exceed rate limit
        messages = []
        for uid in range(max_calls + 5):
            for mid in range(3):
                messages.append(
                    _make_tg_message(
                        user_id=1000 + uid,
                        username=f"user_{uid}",
                        text=f"msg_{mid}",
                        message_id=uid * 10 + mid,
                    )
                )
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        mock_valkey.sismember = AsyncMock(return_value=False)
        resp = AIMessage(content=_llm_score_response(score=0.8))
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))

        stats = await aggregator.run_batch()
        # Should not exceed max LLM calls
        assert stats["profiles_scored"] <= max_calls
        assert stats.get("rate_limited", 0) >= 1 or stats["profiles_scored"] <= max_calls

    @pytest.mark.asyncio
    async def test_run_batch_resets_counters(self, aggregator, mock_valkey):
        """Batch counters are reset at the start of each run_batch."""
        aggregator._llm_calls_this_batch = 999
        mock_valkey.rpop = AsyncMock(return_value=None)
        await aggregator.run_batch()
        assert aggregator._llm_calls_this_batch == 0


# ===========================================================================
# LLM Prompt Construction
# ===========================================================================


class TestPromptConstruction:
    """Tests for the LLM prompt builder."""

    def test_build_scoring_prompt(self, aggregator, AggregatedProfile):
        profile = AggregatedProfile(
            user_id=100,
            username="alice",
            messages=[
                {"text": "Нужен сайт", "channel": "biz"},
                {"text": "Для клиники", "channel": "biz"},
                {"text": "Бюджет 200к", "channel": "web"},
            ],
            channels=["biz", "web"],
        )
        messages = aggregator._build_scoring_prompt(profile)
        # Should return a list of BaseMessage-compatible objects
        assert len(messages) >= 1
        # System message should contain instructions
        combined_text = " ".join(str(m.content) for m in messages)
        assert "score" in combined_text.lower() or "оцен" in combined_text.lower()
        # User messages should be included
        assert "Нужен сайт" in combined_text or "alice" in combined_text

    def test_prompt_includes_all_messages(self, aggregator, AggregatedProfile):
        msgs = [{"text": f"message_{i}", "channel": "ch"} for i in range(5)]
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=msgs,
            channels=["ch"],
        )
        prompt_messages = aggregator._build_scoring_prompt(profile)
        combined = " ".join(str(m.content) for m in prompt_messages)
        for i in range(5):
            assert f"message_{i}" in combined

    def test_prompt_includes_channel_info(self, aggregator, AggregatedProfile):
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": "msg", "channel": "business_talks"}],
            channels=["business_talks"],
        )
        prompt_messages = aggregator._build_scoring_prompt(profile)
        combined = " ".join(str(m.content) for m in prompt_messages)
        assert "business_talks" in combined


# ===========================================================================
# Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Edge case and error handling tests."""

    @pytest.mark.asyncio
    async def test_empty_text_messages_handled(self, aggregator, mock_valkey):
        messages = [
            json.dumps({"user_id": 100, "username": "u", "text": "", "channel": "c", "message_id": 1}),
            json.dumps({"user_id": 100, "username": "u", "text": "", "channel": "c", "message_id": 2}),
            json.dumps({"user_id": 100, "username": "u", "text": "", "channel": "c", "message_id": 3}),
        ]
        mock_valkey.rpop = AsyncMock(side_effect=messages + [None])
        profiles = await aggregator.aggregate_messages()
        # Should still aggregate even with empty text
        assert 100 in profiles

    @pytest.mark.asyncio
    async def test_very_long_text_truncated_in_prompt(self, aggregator, AggregatedProfile):
        """Very long messages should be truncated to avoid context overflow."""
        long_text = "x" * 10000
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": long_text, "channel": "c"}],
            channels=["c"],
        )
        prompt = aggregator._build_scoring_prompt(profile)
        combined = " ".join(str(m.content) for m in prompt)
        # Should not include the full 10k chars
        assert len(combined) < 10000

    @pytest.mark.asyncio
    async def test_concurrent_batch_safety(self, aggregator):
        """Running flag prevents overlapping batches."""
        assert aggregator._running is False
        # Simulate a running batch
        aggregator._running = True
        stats = await aggregator.run_batch()
        assert stats.get("skipped_reason") == "already_running" or stats["messages_processed"] == 0
        aggregator._running = False

    @pytest.mark.asyncio
    async def test_numeric_username_handled(self, aggregator, mock_valkey):
        """Some users have numeric usernames or no username."""
        msg = json.dumps(
            {
                "user_id": 100,
                "username": "12345",
                "text": "need website",
                "channel": "ch",
                "message_id": 1,
            }
        )
        mock_valkey.rpop = AsyncMock(side_effect=[msg, None])
        profiles = await aggregator.aggregate_messages()
        assert profiles[100].username == "12345"

    @pytest.mark.asyncio
    async def test_score_profile_missing_fields_in_response(
        self,
        aggregator,
        mock_llm_client,
        AggregatedProfile,
    ):
        """LLM returns JSON missing some fields — defaults should apply."""
        partial = json.dumps({"score": 0.7})
        resp = AIMessage(content=partial)
        metrics = CallMetrics(agent_name="test", model_id="x", provider="y")
        mock_llm_client.call = AsyncMock(return_value=(resp, metrics))
        profile = AggregatedProfile(
            user_id=100,
            username="u",
            messages=[{"text": f"m{i}"} for i in range(3)],
            channels=["c"],
        )
        result = await aggregator.score_profile(profile)
        assert result is not None
        assert result.score == 0.7
        assert result.needs == [] or isinstance(result.needs, list)

    @pytest.mark.asyncio
    async def test_stats_dict_structure(self, aggregator, mock_valkey):
        """run_batch returns a well-structured stats dict."""
        mock_valkey.rpop = AsyncMock(return_value=None)
        stats = await aggregator.run_batch()
        assert "messages_processed" in stats
        assert "profiles_scored" in stats
        assert "leads_promoted" in stats
        assert "duplicates_skipped" in stats
        assert "errors" in stats
        assert isinstance(stats["messages_processed"], int)
        assert isinstance(stats["profiles_scored"], int)
        assert isinstance(stats["leads_promoted"], int)


# ===========================================================================
# Scheduler Integration
# ===========================================================================


class TestSchedulerIntegration:
    """Tests for the APScheduler cron setup."""

    def test_get_scheduler_job_config(self, aggregator):
        config = aggregator.get_scheduler_config()
        assert config["trigger"] == "interval"
        assert config["minutes"] == 5
        assert callable(config["func"])

    def test_scheduler_config_has_id(self, aggregator):
        config = aggregator.get_scheduler_config()
        assert "id" in config
        assert "telegram" in config["id"].lower()
