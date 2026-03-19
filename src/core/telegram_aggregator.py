"""TelegramProfileAggregator — background service for Pipeline B lead discovery.

Aggregates Telegram messages per user from a Valkey queue, scores profiles
via LLM (Gemini Flash, Tier 5), and promotes high-scoring users to leads.
Runs as a cron job every 5 minutes via APScheduler.

Flow:
    TelegramListener → Valkey LIST (mas:tg_msgs:business)
      → TelegramProfileAggregator (cron every 5 min):
        - RPOP batch from Valkey
        - Group by user_id
        - >= 3 messages → LLM profile analysis (needs, category, score)
        - score >= 0.6 → Lead(source='telegram', source_id=user_id)
        - username → contact for TG DM

Planned:
    ``backfill_channel()`` — replay historical channel messages into the
    aggregation pipeline.  Tracked for a future release.

Spec: docs/Full_work/pipeline-b-spec.md §1C Telegram Mining
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (spec-aligned)
# ---------------------------------------------------------------------------

VALKEY_QUEUE_KEY = "mas:tg_msgs:business"
"""Valkey LIST key where TelegramListener pushes business channel messages."""

MIN_MESSAGES_FOR_SCORING = 3
"""Minimum messages from a single user before LLM scoring is triggered."""

SCORE_PROMOTION_THRESHOLD = 0.6
"""Minimum LLM score to promote a profile to a Lead."""

BATCH_SIZE = 100
"""Maximum messages to RPOP from Valkey per batch run."""

CRON_INTERVAL_MINUTES = 5
"""APScheduler interval for run_batch execution."""

DEDUP_SET_KEY = "mas:tg_aggregator:analyzed"
"""Valkey SET key tracking already-analyzed user IDs."""

MAX_LLM_CALLS_PER_BATCH = 20
"""Maximum LLM scoring calls per single batch to control costs."""

LLM_AGENT_NAME = "geoscout"
"""Agent name for LLMClient model selection (Tier 5: Extraction — Gemini Flash)."""

_MAX_TEXT_PER_MESSAGE = 500
"""Truncate individual messages to this length in the LLM prompt."""

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AggregatedProfile:
    """Messages collected from a single Telegram user across channels."""

    user_id: int
    username: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def has_enough_messages(self) -> bool:
        return self.message_count >= MIN_MESSAGES_FOR_SCORING


@dataclass(slots=True)
class ProfileResult:
    """LLM scoring result for a Telegram user profile."""

    user_id: int
    username: str
    score: float = 0.0
    needs: list[str] = field(default_factory=list)
    category: str = ""
    summary: str = ""

    @property
    def is_promotable(self) -> bool:
        return self.score >= SCORE_PROMOTION_THRESHOLD


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class TelegramProfileAggregator:
    """Background service: aggregate Telegram messages, score profiles, promote leads.

    Parameters
    ----------
    valkey:
        Async Valkey (redis-py) client.
    llm_client:
        Shared :class:`~src.core.llm_client.LLMClient` instance.
    db_session_factory:
        Async callable returning an ``AsyncSession`` context manager
        (e.g. ``get_db_session``).
    """

    def __init__(
        self,
        *,
        valkey: Any,
        llm_client: Any,
        db_session_factory: Any,
    ) -> None:
        self._valkey = valkey
        self._llm_client = llm_client
        self._db_session_factory = db_session_factory
        self._log = logger.bind(component="telegram_aggregator")

        # Batch-level counters (reset each run_batch)
        self._llm_calls_this_batch: int = 0
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_batch(self) -> dict[str, Any]:
        """Main entry point — called by APScheduler every CRON_INTERVAL_MINUTES.

        Returns a stats dict summarising the batch outcome.
        """
        if self._running:
            self._log.warning("batch_already_running")
            return {
                "messages_processed": 0,
                "profiles_scored": 0,
                "leads_promoted": 0,
                "duplicates_skipped": 0,
                "errors": 0,
                "skipped_reason": "already_running",
            }

        self._running = True
        self._reset_batch_counters()

        stats: dict[str, Any] = {
            "messages_processed": 0,
            "profiles_scored": 0,
            "leads_promoted": 0,
            "duplicates_skipped": 0,
            "errors": 0,
            "rate_limited": 0,
        }

        try:
            # Step 1: Aggregate messages from Valkey
            profiles = await self.aggregate_messages()
            stats["messages_processed"] = sum(p.message_count for p in profiles.values())

            if not profiles:
                self._log.debug("batch_empty_queue")
                return stats

            # Step 2: Score & promote each qualifying profile
            for user_id, profile in profiles.items():
                if not profile.has_enough_messages:
                    continue

                # Dedup check
                if await self.is_duplicate(user_id):
                    stats["duplicates_skipped"] += 1
                    continue

                # Rate limit guard
                if not self._can_make_llm_call():
                    stats["rate_limited"] += 1
                    continue

                # Score via LLM
                try:
                    result = await self.score_profile(profile)
                except Exception:  # noqa: BLE001
                    self._log.exception("score_profile_error", user_id=user_id)
                    stats["errors"] += 1
                    continue

                if result is None:
                    stats["errors"] += 1
                    continue

                stats["profiles_scored"] += 1

                # Mark as analyzed (dedup)
                await self.mark_analyzed(user_id)

                # Promote if score meets threshold
                if result.is_promotable:
                    try:
                        promoted = await self.promote_to_lead(result)
                        if promoted:
                            stats["leads_promoted"] += 1
                    except Exception:  # noqa: BLE001
                        self._log.exception("promote_error", user_id=user_id)
                        stats["errors"] += 1

            self._log.info(
                "batch_complete",
                messages=stats["messages_processed"],
                scored=stats["profiles_scored"],
                promoted=stats["leads_promoted"],
                duplicates=stats["duplicates_skipped"],
                errors=stats["errors"],
            )
        finally:
            self._running = False

        return stats

    async def aggregate_messages(self) -> dict[int, AggregatedProfile]:
        """RPOP up to BATCH_SIZE messages from Valkey, group by user_id.

        Returns a mapping of user_id -> AggregatedProfile.
        """
        profiles: dict[int, AggregatedProfile] = {}
        popped = 0

        while popped < BATCH_SIZE:
            raw = await self._valkey.rpop(VALKEY_QUEUE_KEY)
            if raw is None:
                break
            popped += 1

            # Parse message JSON
            try:
                msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                self._log.warning("malformed_message", raw=str(raw)[:200])
                continue

            user_id = msg.get("user_id")
            if user_id is None:
                self._log.warning("missing_user_id", msg_keys=list(msg.keys()))
                continue

            user_id = int(user_id)
            username = str(msg.get("username", ""))
            text = str(msg.get("text", ""))
            channel = str(msg.get("channel", ""))

            if user_id not in profiles:
                profiles[user_id] = AggregatedProfile(
                    user_id=user_id,
                    username=username,
                    messages=[],
                    channels=[],
                )

            profile = profiles[user_id]
            # Update username to latest (may change between messages)
            if username:
                profile.username = username

            profile.messages.append(
                {
                    "text": text,
                    "channel": channel,
                    "message_id": msg.get("message_id"),
                    "timestamp": msg.get("timestamp"),
                }
            )

            if channel and channel not in profile.channels:
                profile.channels.append(channel)

        self._log.debug(
            "messages_aggregated",
            total_popped=popped,
            unique_users=len(profiles),
        )
        return profiles

    async def score_profile(self, profile: AggregatedProfile) -> ProfileResult | None:
        """Score a user profile via LLM.

        Returns ProfileResult on success, None on any failure.
        """
        messages = self._build_scoring_prompt(profile)

        try:
            response, _metrics = await self._llm_client.call(
                LLM_AGENT_NAME,
                messages,
                temperature=0.3,
            )
            self._llm_calls_this_batch += 1
        except Exception:  # noqa: BLE001
            self._log.exception("llm_call_failed", user_id=profile.user_id)
            return None

        # Parse LLM response
        content = str(response.content).strip()
        parsed = self._parse_llm_response(content)
        if parsed is None:
            self._log.warning("llm_response_unparseable", user_id=profile.user_id, content=content[:200])
            return None

        # Clamp score to [0, 1]
        score = max(0.0, min(1.0, float(parsed.get("score", 0.0))))

        result = ProfileResult(
            user_id=profile.user_id,
            username=profile.username,
            score=score,
            needs=parsed.get("needs", []) or [],
            category=str(parsed.get("category", "")),
            summary=str(parsed.get("summary", "")),
        )

        self._log.info(
            "profile_scored",
            user_id=result.user_id,
            username=result.username,
            score=result.score,
            promotable=result.is_promotable,
            needs=result.needs,
        )

        return result

    async def promote_to_lead(self, result: ProfileResult) -> bool:
        """Create a Lead record from a scored profile.

        Returns True if lead was created, False otherwise.
        """
        if not result.is_promotable:
            return False

        if not result.username:
            self._log.warning("no_username_for_lead", user_id=result.user_id)
            return False

        try:
            async with self._db_session_factory() as session:
                from src.core.models import Lead  # noqa: PLC0415

                lead = Lead(
                    name=result.summary[:255] if result.summary else f"TG user @{result.username}",
                    telegram_username=result.username,
                    source="telegram",
                    source_id=str(result.user_id),
                    lead_score=int(result.score * 10),
                    temperature=self._score_to_temperature(result.score),
                    category=result.category or None,
                    status="new",
                )
                session.add(lead)
                await session.commit()

                self._log.info(
                    "lead_promoted",
                    user_id=result.user_id,
                    username=result.username,
                    score=result.score,
                    category=result.category,
                )
                return True
        except Exception:  # noqa: BLE001
            self._log.exception("lead_promotion_failed", user_id=result.user_id)
            return False

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    async def is_duplicate(self, user_id: int) -> bool:
        """Check if a user has already been analyzed."""
        try:
            return bool(await self._valkey.sismember(DEDUP_SET_KEY, str(user_id)))
        except Exception:  # noqa: BLE001
            self._log.debug("dedup_check_failed", user_id=user_id, exc_info=True)
            return False  # Fail-open: don't lose leads

    async def mark_analyzed(self, user_id: int) -> None:
        """Mark a user as analyzed in the dedup set."""
        try:
            await self._valkey.sadd(DEDUP_SET_KEY, str(user_id))
        except Exception:  # noqa: BLE001
            self._log.debug("dedup_mark_failed", user_id=user_id, exc_info=True)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _can_make_llm_call(self) -> bool:
        """Check if we can make another LLM call within this batch."""
        return self._llm_calls_this_batch < MAX_LLM_CALLS_PER_BATCH

    def _reset_batch_counters(self) -> None:
        """Reset per-batch counters."""
        self._llm_calls_this_batch = 0

    # ------------------------------------------------------------------
    # LLM Prompt
    # ------------------------------------------------------------------

    def _build_scoring_prompt(self, profile: AggregatedProfile) -> list:
        """Build the LLM prompt for profile scoring.

        Returns a list of LangChain message objects.
        """
        system_prompt = (
            "Ты — аналитик лидов. Тебе даны сообщения пользователя Telegram "
            "из бизнес-каналов. Оцени, насколько этот пользователь является "
            "потенциальным клиентом для веб-разработки/маркетинга.\n\n"
            "Ответь СТРОГО в формате JSON (без markdown, без комментариев):\n"
            "{\n"
            '  "score": <float 0.0-1.0>,\n'
            '  "needs": [<список потребностей: "website", "seo", "design", '
            '"social", "marketing", "app", "other">],\n'
            '  "category": "<категория бизнеса на английском>",\n'
            '  "summary": "<краткое описание профиля на русском, 1-2 предложения>"\n'
            "}\n\n"
            "Критерии оценки score:\n"
            "- 0.8-1.0: явно ищет исполнителя, указывает бюджет, конкретные потребности\n"
            "- 0.6-0.8: обсуждает бизнес-проблемы, связанные с веб/маркетингом\n"
            "- 0.4-0.6: упоминает бизнес, но неясно нужна ли помощь\n"
            "- 0.0-0.4: не похож на потенциального клиента\n"
        )

        # Build user message with all messages
        user_parts: list[str] = [
            f"Пользователь: @{profile.username} (ID: {profile.user_id})",
            f"Каналы: {', '.join(profile.channels)}",
            f"Всего сообщений: {profile.message_count}",
            "",
            "Сообщения:",
        ]

        for msg in profile.messages:
            text = str(msg.get("text", ""))
            # Truncate long messages
            if len(text) > _MAX_TEXT_PER_MESSAGE:
                text = text[:_MAX_TEXT_PER_MESSAGE] + "..."
            channel = msg.get("channel", "")
            user_parts.append(f"[{channel}] {text}")

        user_content = "\n".join(user_parts)

        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_llm_response(content: str) -> dict[str, Any] | None:
        """Parse LLM JSON response using the shared extract_json utility.

        Delegates to :func:`src.core.json_repair.extract_json` which handles
        markdown fence stripping, trailing commas, single quotes, and other
        common LLM output quirks.
        """
        from src.core.json_repair import extract_json as _extract_json  # noqa: PLC0415

        try:
            parsed = _extract_json(content, expected_type=dict)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_temperature(score: float) -> str:
        """Map LLM score to lead temperature."""
        if score >= 0.8:
            return "hot"
        if score >= 0.6:
            return "warm"
        return "cold"

    # ------------------------------------------------------------------
    # Scheduler config
    # ------------------------------------------------------------------

    def get_scheduler_config(self) -> dict[str, Any]:
        """Return APScheduler job configuration for this aggregator.

        Usage::

            scheduler.add_job(**aggregator.get_scheduler_config())
        """
        return {
            "func": self.run_batch,
            "trigger": "interval",
            "minutes": CRON_INTERVAL_MINUTES,
            "id": "telegram_profile_aggregator",
            "name": "Telegram Profile Aggregator",
            "replace_existing": True,
        }
