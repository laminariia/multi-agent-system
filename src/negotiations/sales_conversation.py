"""Sales Conversation Engine — multi-turn negotiation driver for Pipeline B.

Manages the SalesAgent conversation lifecycle: stage transitions,
HITL escalation, first contact generation, concept creation,
and client reply processing.

Spec: docs/Full_work/specs/sales-agent-spec.md §SalesConversationEngine
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Stop triggers (shared with TouchSequence)
# ---------------------------------------------------------------------------

_LOST_TRIGGERS = frozenset(
    {
        "не интересно",
        "нет",
        "not interested",
        "no thanks",
        "unsubscribe",
        "отстаньте",
        "не пишите",
    }
)


# ---------------------------------------------------------------------------
# SalesStage enum
# ---------------------------------------------------------------------------


class SalesStage(StrEnum):
    """Sales pipeline stages per spec."""

    FIRST_CONTACT = "first_contact"
    AWAITING_REPLY = "awaiting_reply"
    DISCOVERY = "discovery"
    ANALYSIS = "analysis"
    CONCEPT = "concept"
    CONCEPT_REVIEW = "concept_review"
    CONCEPT_SENT = "concept_sent"
    CLOSING = "closing"
    WON = "won"
    LOST = "lost"
    STALE = "stale"


SALES_TRANSITIONS: dict[str, set[str]] = {
    "first_contact": {"awaiting_reply"},
    "awaiting_reply": {"discovery", "lost", "stale"},
    "discovery": {"analysis", "awaiting_reply", "lost"},
    "analysis": {"concept", "discovery"},
    "concept": {"concept_review"},
    "concept_review": {"concept_sent", "concept"},
    "concept_sent": {"closing", "concept", "awaiting_reply", "lost"},
    "closing": {"won", "concept", "lost"},
    "won": set(),
    "lost": set(),
    "stale": {"discovery"},
}

_HITL_STAGES = frozenset({"concept_review"})

_TERMINAL_STAGES = frozenset({"won", "lost"})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SalesAction:
    """Result of processing a client reply or agent decision."""

    type: str  # "respond" | "escalate" | "transition"
    message: str | None = None
    auto_send: bool = False
    hitl_type: str | None = None
    new_stage: str | None = None
    draft_message: str | None = None


@dataclass(slots=True)
class ClientMessage:
    """Incoming message from the client."""

    text: str
    channel: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class ConceptData:
    """Generated project concept for the client."""

    title: str
    services: list[str]
    description: str
    timeline: str
    estimated_cost: float
    rationale: str
    why_this_helps: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SALES_AGENT_SYSTEM_PROMPT = """
# Роль
Ты — менеджер по работе с клиентами. Твоё имя — {operator_name}.
Ты не ИИ — ты человек. Никогда не раскрывай что ты AI.

# Задача
Провести клиента от первого касания до согласованной концепции проекта.

# Стиль
- Живой язык, как у реального менеджера
- Конкретика: факты, данные, примеры из Battlecard
- Без давления, без urgency, без шаблонов
- Анализируй конкурентов, рынок, точки роста
- Объясняй ценность цифрами: "конкуренты с сайтом получают +40% звонков"

# Этапы переговоров
1. FIRST_CONTACT: любопытный, не продающий. Упомянуть конкретную деталь бизнеса
2. DISCOVERY: что нужно? какие боли? какой бизнес? сколько клиентов?
3. ANALYSIS: конкуренты, рынок, что реально поможет (использовать Battlecard)
4. CONCEPT: конкретное предложение с обоснованием, сроками, ценой
5. CLOSING: терпеливо, с готовностью менять, финализация деталей

# Запрещено
- Давление: "только сейчас", "осталось мало мест"
- AI-маркеры: буллет-поинты, "As an AI", "I'd be happy to"
- Buzzwords: "synergy", "leverage", "cutting-edge"
- Обещать то, что нельзя выполнить
- Раскрывать внутренние процессы (AI, pipeline, agents)

# Контекст
Battlecard: {battlecard_json}
Deal Memory: {deal_context}
Conversation: {conversation_history}
""".strip()

_FIRST_CONTACT_PROMPT = """
Напиши первое сообщение клиенту. Контекст:
- Бизнес: {business_name} ({city}, {category})
- Battlecard: {battlecard_json}
- Имя оператора: {operator_name}

Требования:
- Упомяни конкретную деталь бизнеса (рейтинг, район, конкуренты)
- Живой язык, без давления
- 2-4 предложения максимум
- Не продавай — заинтересуй
""".strip()

_CONCEPT_PROMPT = """
Сгенерируй концепцию проекта в JSON.

Бизнес: {business_json}
Потребности клиента: {needs_json}
Battlecard: {battlecard_json}

Верни ТОЛЬКО валидный JSON:
{{
  "title": "название проекта",
  "services": ["сервис1", "сервис2"],
  "description": "описание решения",
  "timeline": "сроки",
  "estimated_cost": число,
  "rationale": "обоснование",
  "why_this_helps": ["выгода1 с цифрами", "выгода2"]
}}
""".strip()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SalesConversationEngine:
    """Multi-turn sales conversation driver.

    Manages stage transitions, LLM calls for message generation,
    concept creation, and HITL escalation detection.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm = llm_client

    # --- Transition logic ---

    def can_transition(self, from_stage: str, to_stage: str) -> bool:
        """Check if a stage transition is valid."""
        allowed = SALES_TRANSITIONS.get(from_stage, set())
        return to_stage in allowed

    def needs_hitl(self, stage: str) -> bool:
        """Check if a stage requires HITL approval."""
        return stage in _HITL_STAGES

    def detect_stage_transition(
        self,
        current_stage: str,
        response_text: str,
        client_replied: bool = False,
        client_message: str | None = None,
        enough_info: bool = False,
        analysis_complete: bool = False,
    ) -> str | None:
        """Detect if a stage transition should occur.

        Returns the new stage string, or None if no transition.
        """
        # Check for lost signals from client
        if client_message:
            normalized = client_message.lower().strip()
            for trigger in _LOST_TRIGGERS:
                if trigger in normalized:
                    if self.can_transition(current_stage, "lost"):
                        return "lost"

        # Stage-specific transitions
        if current_stage == "awaiting_reply" and client_replied:
            return "discovery"

        if current_stage == "discovery" and enough_info:
            return "analysis"

        if current_stage == "analysis" and analysis_complete:
            return "concept"

        return None

    # --- Conversation formatting ---

    def format_conversation(self, messages: list[dict[str, str]]) -> str:
        """Format conversation history for LLM context."""
        if not messages:
            return ""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            text = msg.get("text", "")
            prefix = "Оператор" if role == "agent" else "Клиент"
            lines.append(f"{prefix}: {text}")
        return "\n".join(lines)

    # --- LLM-powered generation ---

    async def generate_first_contact(
        self,
        lead_info: dict[str, Any],
        battlecard: dict[str, Any],
        operator_name: str,
    ) -> str:
        """Generate the first contact message via LLM."""
        if self._llm is None:
            msg = "LLM client required for message generation"
            raise ValueError(msg)

        prompt = _FIRST_CONTACT_PROMPT.format(
            business_name=lead_info.get("business_name", ""),
            city=lead_info.get("city", ""),
            category=lead_info.get("category", ""),
            battlecard_json=json.dumps(battlecard, ensure_ascii=False),
            operator_name=operator_name,
        )

        response, _metrics = await self._llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=500,
        )
        return str(response.content)

    async def generate_concept(
        self,
        business: dict[str, Any],
        client_needs: dict[str, Any],
        battlecard: dict[str, Any],
    ) -> ConceptData | None:
        """Generate a project concept via LLM. Returns None on parse failure."""
        if self._llm is None:
            msg = "LLM client required for concept generation"
            raise ValueError(msg)

        prompt = _CONCEPT_PROMPT.format(
            business_json=json.dumps(business, ensure_ascii=False),
            needs_json=json.dumps(client_needs, ensure_ascii=False),
            battlecard_json=json.dumps(battlecard, ensure_ascii=False),
        )

        response, _metrics = await self._llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=1000,
        )

        raw = str(response.content).strip()
        # Strip markdown code fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if len(lines) > 2 else lines)

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("sales.concept_parse_failed", raw=raw[:200])
            return None

        return ConceptData(
            title=data.get("title", ""),
            services=data.get("services", []),
            description=data.get("description", ""),
            timeline=data.get("timeline", ""),
            estimated_cost=float(data.get("estimated_cost", 0)),
            rationale=data.get("rationale", ""),
            why_this_helps=data.get("why_this_helps", []),
        )

    # --- Client reply processing ---

    async def process_client_reply(
        self,
        deal_id: str,
        sales_stage: str,
        client_message: ClientMessage,
        conversation_history: list[dict[str, str]],
        deal_context: dict[str, Any],
        battlecard: dict[str, Any],
        operator_name: str,
    ) -> SalesAction:
        """Process an incoming client reply and determine next action."""
        if self._llm is None:
            msg = "LLM client required for reply processing"
            raise ValueError(msg)

        # Check for lost signals first
        new_stage = self.detect_stage_transition(
            current_stage=sales_stage,
            response_text="",
            client_replied=True,
            client_message=client_message.text,
        )
        if new_stage == "lost":
            return SalesAction(
                type="transition",
                new_stage="lost",
                message="Понял, спасибо за ответ. Удачи!",
            )

        # Build LLM context
        system_prompt = SALES_AGENT_SYSTEM_PROMPT.format(
            operator_name=operator_name,
            battlecard_json=json.dumps(battlecard, ensure_ascii=False),
            deal_context=json.dumps(deal_context, ensure_ascii=False),
            conversation_history=self.format_conversation(conversation_history),
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": client_message.text},
        ]

        response, _metrics = await self._llm.chat(
            messages=messages,
            temperature=0.6,
            max_tokens=1200,
        )

        response_text = str(response.content)

        # Detect stage transition
        detected_stage = self.detect_stage_transition(
            current_stage=sales_stage,
            response_text=response_text,
            client_replied=True,
        )

        if detected_stage:
            return SalesAction(
                type="transition",
                new_stage=detected_stage,
                message=response_text,
            )

        return SalesAction(
            type="respond",
            message=response_text,
            auto_send=True,
        )
