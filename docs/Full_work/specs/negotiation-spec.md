# Negotiation Engine: Спецификация

> Автономный движок переговоров с клиентами на фриланс-платформах. Управляет диалогами от отправки бида до подтверждения заказа. State machine + platform polling + AI-генерация ответов + HITL при спорных моментах.

## Назначение

Negotiation Engine — подсистема Pipeline A между отправкой бида и запуском Dev Cycle. Отвечает за:

1. **Polling** новых сообщений с платформ (Freelancer API, FL.ru scraping, Kwork scraping, Telegram listener)
2. **Классификацию** входящих сообщений (вопрос, контрпредложение, scope change, подтверждение, отказ)
3. **Генерацию ответов** через LLM с RAG-контекстом успешных переговоров
4. **HITL-маршрутизацию** при спорных ситуациях (цена, scope, неуверенность AI)
5. **Follow-up** при молчании клиента (автоматическая серия напоминаний)
6. **WebSocket-чат** для оператора — возможность вмешаться в любой момент

### Off-Graph Architecture

Negotiation Engine работает **вне LangGraph** — это cron-based подсистема, а не нода графа.

```
LangGraph Pipeline A:
  Scout → Bid → [HITL bid] → bid_submission → [HITL dev_launch] → Planner → ...
                                                    ↓
                                                   END (граф завершён)

Negotiation Engine (отдельный процесс):
  APScheduler cron → MessagePoller → Classifier → ResponseGenerator → Platform Adapters
                                                      ↓
                                              (при acceptance)
                                                      ↓
                                         НОВЫЙ граф: create_initial_state() → Planner → Dev Cycle
```

**Ключевое правило:** После acceptance Negotiation Engine создаёт **НОВЫЙ** `thread_id` и запускает **НОВЫЙ** `graph.astream()` через `create_initial_state()`. Это НЕ resume старого thread — старый thread завершился на `hitl_dev_launch_node`.

**Почему:** LangGraph checkpoints привязаны к `thread_id`. State из фазы Scout→Bid не содержит информации, полученной в переговорах. Новый thread получает enriched `ProjectContext` с данными из переговоров:

```python
async def start_dev_cycle_from_negotiation(negotiation: Negotiation) -> str:
    """Создаёт новый pipeline для Dev Cycle после acceptance."""
    project_context = ProjectContext(
        project_id=str(uuid.uuid4()),
        job_id=str(negotiation.bid.job_id),
        platform=negotiation.bid.platform,
        client=negotiation.client_context,  # Обогащён из переговоров
        requirements=negotiation.final_requirements,  # Может отличаться от оригинальных
        budget=negotiation.agreed_amount,  # Согласованная сумма (не bid_amount!)
        deadline=negotiation.agreed_deadline,
    )

    state = create_initial_state(
        project=project_context,
        first_agent="planner",  # Начинаем с Planner, не Scout
        thread_id=None,  # Новый thread_id
    )

    thread_id = state["thread_id"]
    asyncio.create_task(run_pipeline(thread_id, state))
    return thread_id
```

---

## Архитектура

### Компоненты

```
┌─────────────────────────────────────────────────────────────────┐
│  NEGOTIATION ENGINE                                             │
│                                                                 │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐ │
│  │ Message      │───►│ Classifier       │───►│ Response      │ │
│  │ Poller       │    │ (LLM)            │    │ Generator     │ │
│  └──────┬───────┘    └────────┬─────────┘    └───────┬───────┘ │
│         │                     │                      │         │
│         ▼                     ▼                      ▼         │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐ │
│  │ Platform     │    │ State Machine    │    │ HITL Router   │ │
│  │ Adapters     │    │ Controller       │    │               │ │
│  └──────────────┘    └────────┬─────────┘    └───────┬───────┘ │
│                               │                      │         │
│                               ▼                      ▼         │
│                      ┌──────────────────┐    ┌───────────────┐ │
│                      │ Follow-up        │    │ WebSocket     │ │
│                      │ Scheduler        │    │ Chat Bridge   │ │
│                      └──────────────────┘    └───────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Platform (Freelancer/FL.ru/Kwork/Telegram)
    │
    ▼
Message Poller (cron: 5 мин)
    │
    ├── new message detected
    │       │
    │       ▼
    │   save to client_messages (direction='inbound')
    │       │
    │       ▼
    │   Classifier (LLM) → message_type
    │       │
    │       ├── question         → Response Generator → [HITL if technical/pricing] → send
    │       ├── counter_offer    → [ALWAYS HITL] → operator decides → send
    │       ├── scope_change     → [ALWAYS HITL] → operator decides → send
    │       ├── acceptance       → update negotiation → trigger dev_launch HITL
    │       ├── rejection        → archive bid → notify operator
    │       └── general          → Response Generator → send
    │
    └── no new messages
            │
            ▼
        Follow-up Scheduler checks → auto follow-up if overdue
```

---

## State Machine

### Состояния

```python
class NegotiationState(str, Enum):
    """Состояния переговорного процесса."""
    INITIAL = "initial"              # Бид отправлен, ждём первого ответа
    QUALIFYING = "qualifying"        # Клиент задаёт вопросы, мы отвечаем
    PROPOSING = "proposing"          # Мы предложили условия, ждём реакции
    NEGOTIATING = "negotiating"      # Идёт торг (цена, scope, сроки)
    CLOSING = "closing"              # Клиент согласен, финализация деталей
    ACCEPTED = "accepted"            # Подтверждён — готов к dev_launch HITL
    DECLINED = "declined"            # Клиент отказал
    STALE = "stale"                  # Нет ответа > 14 дней
    OPERATOR_OVERRIDE = "operator_override"  # Оператор взял управление
```

### Переходы

```
                ┌─────────────────────────────────┐
                │                                 │
                ▼                                 │
INITIAL ──► QUALIFYING ──► PROPOSING ──► CLOSING ──► ACCEPTED
  │              │              │            │
  │              │              ▼            │
  │              │         NEGOTIATING ──────┘
  │              │              │
  ▼              ▼              ▼
STALE        DECLINED       DECLINED
  │
  ▼
DECLINED (auto after 14 days)

Любое состояние → OPERATOR_OVERRIDE (оператор вмешался)
OPERATOR_OVERRIDE → любое состояние (оператор вернул управление)
```

### Матрица переходов

| Из | В | Триггер | HITL |
|----|---|---------|------|
| `initial` | `qualifying` | Клиент задал вопрос | Нет |
| `initial` | `negotiating` | Клиент прислал контрпредложение | Да |
| `initial` | `accepted` | Клиент нанял напрямую | Нет |
| `initial` | `declined` | Клиент отказал | Нет |
| `initial` | `stale` | Нет ответа 14 дней | Нет |
| `qualifying` | `proposing` | AI ответил на все вопросы | Нет |
| `qualifying` | `negotiating` | Вопрос перешёл в торг | Да |
| `qualifying` | `accepted` | Клиент нанял после Q&A | Нет |
| `qualifying` | `declined` | Клиент отказал | Нет |
| `proposing` | `closing` | Клиент согласен с условиями | Нет |
| `proposing` | `negotiating` | Клиент хочет изменить условия | Да |
| `proposing` | `declined` | Клиент отказал | Нет |
| `negotiating` | `closing` | Стороны договорились | Нет |
| `negotiating` | `declined` | Не удалось договориться | Нет |
| `closing` | `accepted` | Финальное подтверждение | Нет |
| `closing` | `negotiating` | Новые условия на финише | Да |
| `stale` | `qualifying` | Клиент ответил после паузы | Нет |
| `stale` | `declined` | Авто-архивация (14 дней) | Нет |
| `*` | `operator_override` | Оператор взял управление | — |
| `operator_override` | `*` | Оператор вернул управление | — |

### Реализация State Machine

```python
# src/negotiations/state_machine.py

from __future__ import annotations
from typing import Any

VALID_TRANSITIONS: dict[str, set[str]] = {
    "initial": {"qualifying", "negotiating", "accepted", "declined", "stale", "operator_override"},
    "qualifying": {"proposing", "negotiating", "accepted", "declined", "operator_override"},
    "proposing": {"closing", "negotiating", "declined", "operator_override"},
    "negotiating": {"closing", "declined", "operator_override"},
    "closing": {"accepted", "negotiating", "operator_override"},
    "stale": {"qualifying", "declined", "operator_override"},
    "operator_override": {"initial", "qualifying", "proposing", "negotiating", "closing", "accepted", "declined", "stale"},
}


class NegotiationStateMachine:
    """Контроллер переходов состояний переговоров."""

    def __init__(self, current_state: str = "initial") -> None:
        self.current_state = current_state
        self.history: list[dict[str, Any]] = []

    def can_transition(self, target: str) -> bool:
        return target in VALID_TRANSITIONS.get(self.current_state, set())

    def transition(self, target: str, reason: str, actor: str = "system") -> None:
        """Переход в новое состояние с записью в историю.

        Args:
            target: Целевое состояние.
            reason: Причина перехода (для аудита).
            actor: "system", "ai", "operator".

        Raises:
            InvalidTransitionError: Если переход невалиден.
        """
        if not self.can_transition(target):
            raise InvalidTransitionError(
                f"Cannot transition from {self.current_state!r} to {target!r}. "
                f"Valid targets: {VALID_TRANSITIONS.get(self.current_state, set())}"
            )
        self.history.append({
            "from": self.current_state,
            "to": target,
            "reason": reason,
            "actor": actor,
            "timestamp": datetime.now(UTC),
        })
        self.current_state = target


class InvalidTransitionError(Exception):
    pass
```

### Atomic State Transitions (Optimistic Locking)

In-memory state machine + DB создаёт double-write window. Решение: atomic SQL с optimistic locking.

```python
class NegotiationStateMachine:
    """State machine с atomic DB transitions."""

    async def transition(self, bid_id: UUID, new_state: str, reason: str = "") -> bool:
        """Atomic transition с optimistic locking.

        Returns:
            True если transition успешен, False если state изменился concurrent'но.
        """
        # 1. Валидация перехода (in-memory, быстро)
        allowed = self.TRANSITIONS.get(current_state_cache.get(bid_id))
        if new_state not in (allowed or []):
            raise InvalidTransitionError(f"Cannot transition to {new_state}")

        # 2. Atomic UPDATE с WHERE current_state check
        result = await db.execute(
            update(Negotiation)
            .where(
                Negotiation.bid_id == bid_id,
                Negotiation.state == current_state_cache[bid_id],  # Optimistic lock
            )
            .values(
                state=new_state,
                state_changed_at=utcnow(),
                state_reason=reason,
                state_version=Negotiation.state_version + 1,  # Version counter
            )
            .returning(Negotiation.state)
        )

        if result.rowcount == 0:
            # Concurrent modification — перечитать из DB
            actual = await db.get(Negotiation, bid_id)
            logger.warning("optimistic_lock_conflict",
                bid_id=str(bid_id),
                expected=current_state_cache[bid_id],
                actual=actual.state if actual else "deleted",
            )
            # Обновить кэш и вернуть failure
            if actual:
                current_state_cache[bid_id] = actual.state
            return False

        # 3. Обновить in-memory кэш
        current_state_cache[bid_id] = new_state

        # 4. WebSocket notification
        await safe_publish(channels, "negotiation:state_changed", {
            "bid_id": str(bid_id),
            "old_state": current_state_cache.get(bid_id),
            "new_state": new_state,
            "reason": reason,
        })

        return True
```

**DB schema addition** для optimistic locking:

```sql
ALTER TABLE negotiations ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE negotiations ADD COLUMN state_reason TEXT;
```

**Retry pattern** при конфликте:

```python
async def safe_transition(bid_id: UUID, new_state: str, reason: str, max_retries: int = 3) -> bool:
    """Transition с retry при optimistic lock conflict."""
    for attempt in range(max_retries):
        success = await state_machine.transition(bid_id, new_state, reason)
        if success:
            return True
        # Перечитать актуальное состояние
        await asyncio.sleep(0.1 * (attempt + 1))

    logger.error("transition_failed_after_retries", bid_id=str(bid_id), target=new_state)
    return False
```

---

## Классификация сообщений

### Message Classifier

LLM-классификатор входящих сообщений. Определяет тип и необходимость HITL.

```python
# src/negotiations/classifier.py

QUESTION_TYPES: dict[str, dict[str, Any]] = {
    "clarification": {
        "hitl_required": False,
        "handler": "generate_clarification_response",
        "description": "Уточняющий вопрос о проекте, процессе, подходе",
        "patterns": ["can you", "how would", "what if", "tell me more"],
    },
    "technical": {
        "hitl_required": True,
        "handler": "generate_technical_response",
        "description": "Технический вопрос о стеке, архитектуре, инструментах",
        "patterns": ["framework", "api", "database", "architecture", "stack"],
    },
    "timeline": {
        "hitl_required": False,
        "handler": "generate_timeline_response",
        "description": "Вопрос о сроках, дедлайнах, этапах",
        "patterns": ["how long", "when", "deadline", "timeline", "delivery"],
    },
    "pricing": {
        "hitl_required": True,
        "handler": None,  # ВСЕГДА HITL, AI не отвечает
        "description": "Вопрос или предложение по цене",
        "patterns": ["price", "cost", "budget", "discount", "rate"],
    },
    "portfolio": {
        "hitl_required": False,
        "handler": "generate_portfolio_response",
        "description": "Запрос портфолио или примеров работ",
        "patterns": ["portfolio", "examples", "similar work", "show me"],
    },
    "acceptance": {
        "hitl_required": False,
        "handler": None,  # Переход в accepted
        "description": "Клиент нанимает / подтверждает заказ",
        "patterns": ["hired", "award", "let's start", "you're hired", "accept"],
    },
    "rejection": {
        "hitl_required": False,
        "handler": None,  # Переход в declined
        "description": "Клиент отказывает",
        "patterns": ["not interested", "went with someone", "no thanks", "declined"],
    },
    "counter_offer": {
        "hitl_required": True,
        "handler": "generate_counter_offer_options",
        "description": "Контрпредложение по цене",
        "patterns": ["budget is", "can you do it for", "lower", "discount"],
    },
    "scope_change": {
        "hitl_required": True,
        "handler": "generate_scope_change_options",
        "description": "Изменение или расширение требований",
        "patterns": ["also", "additionally", "one more thing", "can you add"],
    },
    "general": {
        "hitl_required": False,
        "handler": "generate_general_response",
        "description": "Общее сообщение, не попадающее в другие категории",
        "patterns": [],
    },
}
```

### LLM Classifier Prompt

```python
CLASSIFIER_SYSTEM_PROMPT = """You are a message classifier for freelance platform negotiations.

Given a client message and conversation context, classify it into ONE of these types:
- clarification: Client asking about project details, process, approach
- technical: Client asking about tech stack, architecture, tools
- timeline: Client asking about deadlines, delivery dates
- pricing: Client discussing price, budget, discounts (ALWAYS requires human review)
- portfolio: Client requesting examples, portfolio, past work
- acceptance: Client hiring / confirming the project
- rejection: Client declining / going with someone else
- counter_offer: Client proposing a different price
- scope_change: Client adding new requirements not in original brief
- general: Anything else

Also extract:
- sentiment: positive / neutral / negative
- urgency: high / medium / low
- contains_question: true / false
- extracted_amount: number if price mentioned, null otherwise

Respond in JSON only."""
```

### Classifier Output

```python
class ClassificationResult(TypedDict):
    message_type: str           # Один из QUESTION_TYPES ключей
    confidence: float           # 0.0 - 1.0
    sentiment: str              # "positive" / "neutral" / "negative"
    urgency: str                # "high" / "medium" / "low"
    contains_question: bool
    extracted_amount: float | None
    reasoning: str              # Краткое объяснение классификации
```

---

## Response Generator

### Генерация ответов

```python
# src/negotiations/response_generator.py

class ResponseGenerator:
    """Генерирует ответы клиентам с RAG-контекстом."""

    def __init__(self, llm_client: LLMClient, rag_store: ExperienceStore) -> None:
        self.llm = llm_client
        self.rag = rag_store

    async def generate(
        self,
        bid: Bid,
        conversation: list[ClientMessage],
        classification: ClassificationResult,
        handler_name: str,
    ) -> GeneratedResponse:
        """Генерирует ответ на основе контекста переговоров.

        Args:
            bid: Оригинальный бид (amount, proposal, job details).
            conversation: Вся история переписки.
            classification: Результат классификации последнего сообщения.
            handler_name: Имя обработчика из QUESTION_TYPES.

        Returns:
            GeneratedResponse с текстом ответа и метаданными.
        """
        # 1. RAG: найти похожие успешные переговоры
        rag_context = await self.rag.retrieve_similar(
            query=conversation[-1].content,
            filter_type="negotiation",
            top_k=3,
        )

        # 2. Собрать контекст
        context = {
            "job": bid.job_snapshot,
            "original_bid": {
                "amount": bid.bid_amount,
                "delivery_days": bid.delivery_days,
                "proposal_summary": bid.proposal_text[:500],
            },
            "conversation_history": [
                {"role": msg.direction, "content": msg.content, "type": msg.message_type}
                for msg in conversation
            ],
            "classification": classification,
            "similar_negotiations": rag_context,
        }

        # 3. Вызвать handler-specific промпт
        handler = RESPONSE_HANDLERS[handler_name]
        response_text = await self.llm.chat(
            messages=[
                {"role": "system", "content": handler.system_prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            temperature=0.4,
            max_tokens=800,
        )

        return GeneratedResponse(
            text=response_text,
            handler=handler_name,
            rag_sources=[r["id"] for r in rag_context],
            auto_send=not classification.get("hitl_required", True),
        )


class GeneratedResponse(TypedDict):
    text: str
    handler: str
    rag_sources: list[str]
    auto_send: bool  # True = отправить сразу, False = ждать HITL
```

### Tone of Voice

Все ответы генерируются в стиле **"сосед, не продавец"**:

- Конкретика вместо общих фраз
- Без давления, без urgency-маркеров ("limited time", "act now")
- Без AI-маркеров ("As an AI", "I'd be happy to")
- Прямой ответ на вопрос, потом контекст
- Человеческие обороты ("Let me check", "Good question", "Here's what I'd suggest")

---

## Counter-Offer Engine

### Стратегия контрпредложений

| Разница от bid_amount | Рекомендация | Типовой ответ |
|----------------------|-------------|---------------|
| ≤ 10% | `ACCEPT_POSSIBLE` | "I can work with that budget. Let's proceed!" |
| 11-25% | `NEGOTIATE` | "Would $X work? I can adjust the timeline slightly." |
| > 25% | `DECLINE_OR_REDUCE_SCOPE` | "That's below my minimum for this scope. I could offer a reduced version." |

### Counter-Offer HITL Payload

```python
class CounterOfferPayload(TypedDict):
    original_bid: float
    counter_amount: float
    difference_percent: float
    recommendation: str  # "ACCEPT_POSSIBLE" | "NEGOTIATE" | "DECLINE_OR_REDUCE_SCOPE"
    suggested_responses: list[SuggestedResponse]
    conversation_summary: str
    client_profile: dict  # rating, hire_rate, total_spent


class SuggestedResponse(TypedDict):
    action: str    # "accept" | "counter" | "decline" | "custom_response"
    text: str      # Готовый текст ответа
    amount: float | None  # Новая сумма (для counter)
```

### HITL-интерфейс

```json
{
  "type": "counter_offer",
  "title": "Client counter offer: $1,500 (originally $2,000)",
  "payload": {
    "original_bid": 2000,
    "counter_amount": 1500,
    "difference_percent": 25,
    "recommendation": "NEGOTIATE",
    "suggested_responses": [
      {"action": "accept", "text": "I can work with $1,500. Let's proceed!", "amount": 1500},
      {"action": "counter", "text": "Would $1,750 work? I can adjust the timeline slightly.", "amount": 1750},
      {"action": "decline", "text": "Unfortunately $1,500 is below my minimum for this scope. I could offer a reduced version focusing on core features.", "amount": null}
    ],
    "conversation_summary": "Client asked 2 clarifying questions, then proposed $1,500 instead of $2,000."
  },
  "available_actions": ["accept", "counter", "decline", "custom_response"]
}
```

**Действия оператора:**

| Действие | Результат |
|----------|----------|
| `accept` | Отправляет accept-текст, negotiation → `closing` |
| `counter` | Отправляет counter-текст с новой суммой, negotiation остаётся `negotiating` |
| `decline` | Отправляет decline-текст, negotiation → `declined` |
| `custom_response` | Оператор пишет свой текст, negotiation остаётся в текущем состоянии |

---

## Scope Change Handler

### Детекция scope change

LLM сравнивает новое сообщение с оригинальными требованиями job:

```python
SCOPE_CHANGE_PROMPT = """Compare the client's latest message with the original job requirements.

Original job: {job_description}
Original bid scope: {proposal_text}
Client's message: {message}

Determine:
1. Is this a scope change? (new requirement not in original brief)
2. If yes, estimate additional cost (USD) and time (days)
3. Generate 3 response options

Respond in JSON:
{
  "is_scope_change": true/false,
  "new_requirements": ["list of new items"],
  "estimated_additional_cost": 0,
  "estimated_additional_days": 0,
  "response_options": [
    {"strategy": "include", "text": "Happy to include that! It would add approximately $X and Y days."},
    {"strategy": "phase_2", "text": "Great addition! Let's discuss this as a Phase 2 after the initial delivery."},
    {"strategy": "revise", "text": "That's definitely possible. Want me to revise the proposal with this included?"}
  ]
}"""
```

### Scope Change HITL Payload

```python
class ScopeChangePayload(TypedDict):
    original_scope: str
    new_requirements: list[str]
    estimated_additional_cost: float
    estimated_additional_days: int
    response_options: list[ScopeChangeOption]
    current_bid_amount: float
    current_delivery_days: int


class ScopeChangeOption(TypedDict):
    strategy: str  # "include" | "phase_2" | "revise"
    text: str
    new_total_amount: float | None
    new_total_days: int | None
```

---

## Follow-up Scheduler

### Автоматические follow-up при молчании клиента

```python
FOLLOW_UP_SCHEDULE: list[FollowUpStep] = [
    {
        "days_without_response": 2,
        "template": "gentle_reminder",
        "auto_send": True,
        "text": "Hi! Just checking in on this. Let me know if you have any questions!",
    },
    {
        "days_without_response": 5,
        "template": "second_followup",
        "auto_send": True,
        "text": "Wanted to follow up on my proposal. I'm still available and interested in this project.",
    },
    {
        "days_without_response": 10,
        "template": "final_checkin",
        "auto_send": True,
        "text": "Final check-in — I'll assume you've moved in a different direction if I don't hear back. Best of luck with the project!",
    },
    {
        "days_without_response": 14,
        "template": "archive",
        "auto_send": True,
        "text": None,  # Не отправляем сообщение, только меняем статус
        "action": "archive_as_stale",
    },
]
```

### Follow-up Logic

```python
# src/negotiations/followup.py

class FollowUpScheduler:
    """Планировщик follow-up сообщений при молчании клиента."""

    async def check_and_send(self) -> list[FollowUpResult]:
        """Проверяет все активные переговоры и отправляет follow-up где нужно.

        Вызывается cron-задачей каждые 30 минут.
        """
        results: list[FollowUpResult] = []

        # Найти все переговоры в состояниях, допускающих follow-up
        active = await self.db.fetch_negotiations(
            states=["initial", "qualifying", "proposing"],
        )

        for negotiation in active:
            last_message = await self.db.get_last_message(
                bid_id=negotiation.bid_id,
                direction="outbound",
            )
            if last_message is None:
                continue

            days_silent = (utcnow() - last_message.created_at).days

            # Найти подходящий follow-up шаг
            step = self._find_step(days_silent, negotiation.followup_count)
            if step is None:
                continue

            # Проверить что клиент не ответил между последним follow-up и сейчас
            client_replied = await self.db.has_inbound_after(
                bid_id=negotiation.bid_id,
                after=last_message.created_at,
            )
            if client_replied:
                continue

            if step.get("action") == "archive_as_stale":
                await self._archive(negotiation)
            elif step["auto_send"]:
                await self._send_followup(negotiation, step)

            results.append(FollowUpResult(
                bid_id=negotiation.bid_id,
                step=step["template"],
                days_silent=days_silent,
            ))

        return results

    def _find_step(self, days_silent: int, followup_count: int) -> dict | None:
        """Находит шаг follow-up по количеству дней молчания.

        Каждый шаг отправляется только один раз (followup_count трекает).
        """
        for i, step in enumerate(FOLLOW_UP_SCHEDULE):
            if days_silent >= step["days_without_response"] and followup_count == i:
                return step
        return None
```

### Отмена follow-up

Follow-up автоматически отменяется когда:

- Клиент ответил (inbound message detected)
- Оператор взял управление (`operator_override`)
- Negotiation перешла в терминальное состояние (`accepted`, `declined`)
- Оператор явно отменил (через Dashboard)

---

## Message Poller

### Platform Polling

```python
# src/negotiations/poller.py

POLLING_INTERVALS: dict[str, int] = {
    "freelancer": 300,    # 5 мин (API)
    "fl_ru": 600,         # 10 мин (scraping, щадящий режим)
    "kwork": 600,         # 10 мин (scraping)
    "telegram": 0,        # Real-time (Telethon listener)
}


class MessagePoller:
    """Поллинг новых сообщений с платформ."""

    async def poll_platform(self, platform: str, bid: Bid) -> list[RawMessage]:
        """Получает новые сообщения для конкретного бида.

        Args:
            platform: Имя платформы.
            bid: Бид с platform_bid_id и thread_id.

        Returns:
            Список новых сообщений (ещё не в client_messages).
        """
        adapter = self.adapters[platform]

        match platform:
            case "freelancer":
                raw = await adapter.get_thread_messages(
                    thread_id=bid.platform_thread_id,
                    since=bid.last_polled_at,
                )
            case "fl_ru":
                raw = await adapter.scrape_inbox_thread(
                    project_url=bid.platform_url,
                    since=bid.last_polled_at,
                )
            case "kwork":
                raw = await adapter.scrape_chat(
                    order_id=bid.platform_bid_id,
                    since=bid.last_polled_at,
                )
            case "telegram":
                # Telegram использует push через Telethon listener
                # Этот метод — fallback для пропущенных сообщений
                raw = await adapter.get_direct_messages(
                    user_id=bid.client_telegram_id,
                    since=bid.last_polled_at,
                )
            case _:
                return []

        # Фильтр уже сохранённых
        existing_ids = await self.db.get_external_ids(bid_id=bid.id)
        new_messages = [m for m in raw if m.external_id not in existing_ids]

        # Обновить last_polled_at
        await self.db.update_bid(bid.id, last_polled_at=utcnow())

        return new_messages
```

### Poll Orchestration (cron)

```python
async def poll_all_active_negotiations() -> None:
    """Главный cron: поллинг всех активных переговоров.

    Запускается каждые 5 минут через APScheduler.
    """
    active_bids = await db.fetch_bids_with_active_negotiations()

    for bid in active_bids:
        platform = bid.platform
        interval = POLLING_INTERVALS[platform]

        # Проверить что прошло достаточно времени с последнего poll
        if bid.last_polled_at and (utcnow() - bid.last_polled_at).total_seconds() < interval:
            continue

        try:
            new_messages = await poller.poll_platform(platform, bid)

            for raw_msg in new_messages:
                # 1. Сохранить в БД
                saved = await db.save_client_message(
                    bid_id=bid.id,
                    direction="inbound",
                    content=raw_msg.content,
                    platform=platform,
                    external_id=raw_msg.external_id,
                )

                # 2. Классифицировать
                classification = await classifier.classify(
                    message=raw_msg.content,
                    conversation=await db.get_conversation(bid.id),
                    job=bid.job_snapshot,
                )

                # 3. Обработать по типу
                await process_classified_message(bid, saved, classification)

                # 4. WebSocket уведомление
                await ws_publish("negotiation:new_message", {
                    "bid_id": str(bid.id),
                    "message_id": str(saved.id),
                    "message_type": classification["message_type"],
                    "requires_hitl": QUESTION_TYPES[classification["message_type"]]["hitl_required"],
                })

        except (OSError, ConnectionError) as e:
            logger.warning("Platform polling failed", platform=platform, bid_id=str(bid.id), error=str(e))
```

---

## Operator Override (WebSocket Chat)

### Архитектура чата

Оператор может в любой момент зайти в карточку бида и:

1. Читать всю историю переписки (AI + клиент + оператор)
2. Написать ответ клиенту своими словами
3. Взять управление диалогом (state → `operator_override`)
4. Вернуть управление AI

### WebSocket Events

```python
# Клиент → Сервер
WS_EVENTS_CLIENT = {
    "negotiation:subscribe": {
        "bid_id": "uuid",
        "description": "Подписка на обновления конкретного бида",
    },
    "negotiation:send_message": {
        "bid_id": "uuid",
        "content": "str",
        "description": "Оператор отправляет сообщение клиенту",
    },
    "negotiation:take_over": {
        "bid_id": "uuid",
        "description": "Оператор берёт управление",
    },
    "negotiation:release": {
        "bid_id": "uuid",
        "description": "Оператор возвращает управление AI",
    },
}

# Сервер → Клиент
WS_EVENTS_SERVER = {
    "negotiation:new_message": {
        "bid_id": "uuid",
        "message": "ClientMessage",
        "classification": "ClassificationResult | null",
        "description": "Новое сообщение (входящее или исходящее)",
    },
    "negotiation:state_changed": {
        "bid_id": "uuid",
        "old_state": "str",
        "new_state": "str",
        "reason": "str",
        "description": "Изменение состояния переговоров",
    },
    "negotiation:hitl_required": {
        "bid_id": "uuid",
        "hitl_id": "uuid",
        "hitl_type": "str",
        "description": "Требуется решение оператора",
    },
    "negotiation:followup_sent": {
        "bid_id": "uuid",
        "template": "str",
        "description": "Автоматический follow-up отправлен",
    },
}
```

### API Endpoints

```python
# src/api/routes/negotiations.py

# GET /api/v1/negotiations
# Список активных переговоров с фильтрацией
# Query: state, platform, sort_by (created_at, last_message_at)
# Response: list[NegotiationSummary]

# GET /api/v1/negotiations/{bid_id}
# Детали переговорного процесса
# Response: NegotiationDetail (state, history, metrics)

# GET /api/v1/negotiations/{bid_id}/messages
# Вся переписка по биду
# Query: limit, offset, direction (inbound/outbound/all)
# Response: list[ClientMessage]

# POST /api/v1/negotiations/{bid_id}/messages
# Оператор отправляет сообщение
# Body: {"content": "str"}
# Side effects: save to client_messages, send via platform adapter, ws publish
# Response: ClientMessage

# POST /api/v1/negotiations/{bid_id}/take-over
# Оператор берёт управление
# Side effects: state → operator_override, ws publish
# Response: {"state": "operator_override"}

# POST /api/v1/negotiations/{bid_id}/release
# Оператор возвращает управление AI
# Body: {"target_state": "qualifying"}  # опционально
# Side effects: state → target_state или предыдущее, ws publish
# Response: {"state": "qualifying"}

# GET /api/v1/negotiations/analytics
# Метрики переговоров за период
# Query: days (default 30)
# Response: NegotiationAnalytics
```

---

## Таблицы БД

### client_messages

```sql
CREATE TABLE client_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bid_id          UUID NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    project_id      UUID REFERENCES projects(id) ON DELETE SET NULL,
    direction       VARCHAR(10) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    sender          VARCHAR(20) NOT NULL CHECK (sender IN ('client', 'ai', 'operator')),
    message_type    VARCHAR(30),  -- 'question', 'counter_offer', 'scope_change', 'general', 'followup', etc.
    content         TEXT NOT NULL,
    platform        VARCHAR(50) NOT NULL,
    external_id     VARCHAR(255),  -- platform-specific message ID для дедупликации
    auto_generated  BOOLEAN NOT NULL DEFAULT FALSE,
    hitl_reviewed   BOOLEAN NOT NULL DEFAULT FALSE,
    hitl_id         UUID REFERENCES hitl_queue(id),  -- ссылка на HITL запись если было ревью
    metadata        JSONB DEFAULT '{}',  -- classification result, RAG sources, etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_client_messages_bid_id ON client_messages(bid_id, created_at);
CREATE INDEX idx_client_messages_external_id ON client_messages(platform, external_id);
CREATE INDEX idx_client_messages_direction ON client_messages(bid_id, direction, created_at);
```

### negotiations

```sql
CREATE TABLE negotiations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bid_id          UUID NOT NULL UNIQUE REFERENCES bids(id) ON DELETE CASCADE,
    state           VARCHAR(30) NOT NULL DEFAULT 'initial'
                    CHECK (state IN (
                        'initial', 'qualifying', 'proposing', 'negotiating',
                        'closing', 'accepted', 'declined', 'stale', 'operator_override'
                    )),
    previous_state  VARCHAR(30),  -- для возврата из operator_override
    original_amount DECIMAL(10,2) NOT NULL,
    current_amount  DECIMAL(10,2),  -- текущая обсуждаемая сумма
    final_amount    DECIMAL(10,2),  -- финальная согласованная сумма
    rounds          INTEGER NOT NULL DEFAULT 0,  -- количество раундов торга
    followup_count  INTEGER NOT NULL DEFAULT 0,  -- количество отправленных follow-up
    last_followup_at TIMESTAMPTZ,
    outcome         VARCHAR(20) CHECK (outcome IN ('accepted', 'declined', 'scope_adjusted', 'stale')),
    history         JSONB NOT NULL DEFAULT '[]',  -- state machine transition log
    metadata        JSONB DEFAULT '{}',  -- analytics, AI confidence scores, etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_negotiations_state ON negotiations(state) WHERE state NOT IN ('accepted', 'declined', 'stale');
CREATE INDEX idx_negotiations_bid_id ON negotiations(bid_id);
```

### Расширение таблицы bids

```sql
-- Новые поля в таблице bids для поддержки переговоров
ALTER TABLE bids ADD COLUMN IF NOT EXISTS platform_thread_id VARCHAR(255);
ALTER TABLE bids ADD COLUMN IF NOT EXISTS platform_bid_id VARCHAR(255);
ALTER TABLE bids ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMPTZ;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS client_telegram_id BIGINT;
```

### Pydantic Models

```python
# src/core/models.py

class ClientMessageCreate(BaseModel):
    bid_id: UUID
    direction: Literal["inbound", "outbound"]
    sender: Literal["client", "ai", "operator"]
    content: str
    platform: str
    external_id: str | None = None
    message_type: str | None = None
    auto_generated: bool = False
    metadata: dict[str, Any] = {}


class ClientMessageResponse(BaseModel):
    id: UUID
    bid_id: UUID
    direction: str
    sender: str
    message_type: str | None
    content: str
    platform: str
    auto_generated: bool
    hitl_reviewed: bool
    created_at: datetime


class NegotiationResponse(BaseModel):
    id: UUID
    bid_id: UUID
    state: str
    original_amount: Decimal
    current_amount: Decimal | None
    final_amount: Decimal | None
    rounds: int
    followup_count: int
    outcome: str | None
    created_at: datetime
    updated_at: datetime


class NegotiationSummary(BaseModel):
    """Краткая информация для списка переговоров."""
    bid_id: UUID
    job_title: str
    client_name: str
    platform: str
    state: str
    current_amount: Decimal | None
    last_message_preview: str | None
    last_message_at: datetime | None
    unread_count: int
```

---

## Error Handling

### Platform Account Issues

| Проблема | Детекция | Действие | HITL |
|----------|----------|----------|------|
| Account suspended | Login fails + ban message | Приостановить все переговоры на платформе | CRITICAL |
| Rate limited | HTTP 429 | Пауза 1 час, увеличить интервал polling | Уведомление |
| Session expired | Redirect to login | Авто re-login через adapter | Нет |
| 2FA required | 2FA prompt detected | Приостановить polling | HITL для ввода кода |
| Platform down | Connection timeout / 5xx | Retry с exponential backoff (max 3) | Нет |

### Message Processing Errors

| Проблема | Детекция | Действие |
|----------|----------|----------|
| LLM classification failed | Exception / timeout | Fallback: `message_type="general"`, HITL для ревью |
| Response generation failed | Exception / timeout | Не отправлять, создать HITL alert |
| Platform send failed | API error / timeout | Retry 3x, затем HITL alert |
| Duplicate message | `external_id` уже существует | Skip (idempotent) |
| Encoding issues | Unicode decode error | Store raw bytes, flag for review |

### Concurrency

- **Один poll task на bid** — `asyncio.Lock` по `bid_id` предотвращает параллельный polling
- **Message ordering** — `created_at` + `external_id` sequence, platform-specific ordering
- **Race condition при HITL** — operator может ответить пока AI генерирует → AI-ответ отменяется

```python
async def process_classified_message(bid: Bid, message: ClientMessage, classification: ClassificationResult) -> None:
    """Обработка классифицированного сообщения с проверкой на operator override."""

    negotiation = await db.get_negotiation(bid_id=bid.id)

    # Если оператор управляет — только уведомить, не генерировать ответ
    if negotiation.state == "operator_override":
        await ws_publish("negotiation:new_message", {...})
        return

    handler_config = QUESTION_TYPES.get(classification["message_type"], QUESTION_TYPES["general"])

    # Обновить состояние state machine
    sm = NegotiationStateMachine(negotiation.state)
    new_state = _determine_new_state(classification, sm.current_state)
    if new_state and sm.can_transition(new_state):
        sm.transition(new_state, reason=f"message_type={classification['message_type']}")
        await db.update_negotiation(negotiation.id, state=new_state, history=sm.history)

    # Acceptance → trigger dev_launch HITL
    if classification["message_type"] == "acceptance":
        await _create_dev_launch_hitl(bid)
        return

    # Rejection → archive
    if classification["message_type"] == "rejection":
        await db.update_negotiation(negotiation.id, state="declined", outcome="declined", resolved_at=utcnow())
        return

    # HITL required → генерировать draft + создать HITL
    if handler_config["hitl_required"]:
        if handler_config["handler"]:
            draft = await response_generator.generate(bid, conversation, classification, handler_config["handler"])
        else:
            draft = None

        hitl_payload = _build_hitl_payload(classification, bid, draft)
        await create_hitl_entry(
            type=f"negotiation_{classification['message_type']}",
            bid_id=bid.id,
            payload=hitl_payload,
            available_actions=_get_actions_for_type(classification["message_type"]),
        )
        return

    # Auto-response
    if handler_config["handler"]:
        response = await response_generator.generate(bid, conversation, classification, handler_config["handler"])
        await _send_and_save(bid, response.text, platform=bid.platform)
```

---

## Communication Analytics

| Метрика | Описание | Целевое значение | SQL |
|---------|----------|-----------------|-----|
| Response Time | Среднее время ответа клиенту | < 2 часов | `AVG(outbound.created_at - inbound.created_at)` |
| Conversion Rate | Бид → Accepted | > 60% | `COUNT(accepted) / COUNT(total)` |
| Counter Accept Rate | Контрпредложения приняты | < 30% | `COUNT(counter_accepted) / COUNT(counters)` |
| Follow-up Effectiveness | Ответы после follow-up | > 15% | `COUNT(reply_after_followup) / COUNT(followups_sent)` |
| Scope Creep Rate | Проекты с scope change | Tracking only | `COUNT(scope_changes) / COUNT(negotiations)` |
| AI Accuracy | AI-ответы без HITL-правок | > 80% | `COUNT(auto_sent AND NOT edited) / COUNT(auto_sent)` |
| Avg Rounds | Среднее кол-во раундов торга | < 3 | `AVG(rounds)` |
| Time to Close | От первого контакта до accepted | < 5 дней | `AVG(resolved_at - created_at)` |

### Analytics API Response

```python
class NegotiationAnalytics(BaseModel):
    period_days: int
    total_negotiations: int
    active: int
    accepted: int
    declined: int
    stale: int
    avg_response_time_hours: float
    conversion_rate: float
    counter_accept_rate: float
    followup_effectiveness: float
    avg_rounds: float
    avg_time_to_close_days: float
    ai_accuracy: float
    by_platform: dict[str, PlatformStats]


class PlatformStats(BaseModel):
    total: int
    accepted: int
    declined: int
    conversion_rate: float
    avg_response_time_hours: float
```

---

## Capacity Management

**Лимит параллельных активных переговоров (не проектов):**

| Фаза | Лимит переговоров | Лимит проектов |
|------|-------------------|----------------|
| Phase 1 (MVP) | 10 | 5 |
| Phase 2 | 20 | 10 |
| Phase 3+ | 30-50 | 15-20 |

При достижении лимита переговоров Scout приостанавливает отправку новых бидов.

**Минимальный порог качества:** `MIN_QUALITY_THRESHOLD = 0.85`. Если средний quality score за 30 дней ниже порога — новые проекты блокируются до восстановления.

---

## Dashboard UI

### Bid Kanban (обзор)

| Backlog | Отправлен | Активный диалог | Подтверждён | Отказ |
|---------|-----------|-----------------|-------------|-------|
| Ждёт HITL | Ждём клиента | AI ведёт переговоры | Готов к Dev | — |

### Карточка бида → чат-окно

Клик по карточке открывает чат-окно:

```
┌─────────────────────────────────────────────┐
│ Job: Build React Dashboard    [Freelancer]  │
│ Bid: $2,000 / 14 days                      │
│ State: qualifying  ●                        │
├─────────────────────────────────────────────┤
│                                             │
│ [Client] Can you show me examples?          │
│           12:30 PM                          │
│                                             │
│ [AI] Sure! Here are some relevant...    ✓   │
│       12:31 PM  (auto)                      │
│                                             │
│ [Client] What framework would you use?      │
│           2:15 PM                           │
│                                             │
│ [AI] I'd recommend Next.js for this...  ⏳  │
│       2:16 PM  (draft, pending HITL)        │
│                                             │
├─────────────────────────────────────────────┤
│ [Type message...]              [Send] [AI]  │
│                                             │
│ [Take Over] [View HITL] [Follow-up: 2d]    │
└─────────────────────────────────────────────┘
```

Элементы:

- **Метки отправителя:** `[Client]`, `[AI]`, `[Operator]` — цветовая кодировка
- **Статус сообщения:** ✓ (sent), ⏳ (pending HITL), ✗ (failed)
- **Take Over** — кнопка перехода в operator_override
- **View HITL** — переход к pending HITL если есть
- **Follow-up timer** — показывает когда будет следующий auto follow-up

---

## Конфигурация

### Environment Variables

```bash
# Polling
NEGOTIATION_POLL_INTERVAL_SECONDS=300      # default: 5 min
NEGOTIATION_FOLLOWUP_CHECK_INTERVAL=1800   # default: 30 min

# Limits
MAX_PARALLEL_NEGOTIATIONS=10               # Phase 1 default
MAX_PARALLEL_PROJECTS=5
MIN_QUALITY_THRESHOLD=0.85

# Follow-up
FOLLOWUP_DAY_2_ENABLED=true
FOLLOWUP_DAY_5_ENABLED=true
FOLLOWUP_DAY_10_ENABLED=true
FOLLOWUP_AUTO_ARCHIVE_DAYS=14

# LLM
NEGOTIATION_LLM_MODEL=deepseek/deepseek-chat  # DeepSeek V3.2 via OpenRouter
NEGOTIATION_LLM_TEMPERATURE=0.4
NEGOTIATION_LLM_MAX_TOKENS=800
```

### Settings (Litestar)

```python
class NegotiationSettings(BaseModel):
    poll_interval_seconds: int = 300
    followup_check_interval: int = 1800
    max_parallel_negotiations: int = 10
    max_parallel_projects: int = 5
    min_quality_threshold: float = 0.85
    followup_auto_archive_days: int = 14
    llm_model: str = "deepseek/deepseek-chat"
    llm_temperature: float = 0.4
    llm_max_tokens: int = 800
```

---

## Зависимости

| Компонент | Спека |
|-----------|-------|
| Platform Adapters | `specs/platform-adapters-spec.md` — polling, message send/receive |
| HITL System | `specs/hitl-spec.md` — HITL entries, resolve flow, resume |
| RAG Memory | `specs/rag-memory-spec.md` — retrieve_similar для negotiation context |
| Agents | `specs/agents-spec.md` — Bid Agent state, AgentState fields |
| API | `specs/api-spec.md` — REST endpoints, WebSocket |
| Database | `specs/database-spec.md` — client_messages, negotiations tables |
| Telegram Bot | `specs/telegram-bot-spec.md` — HITL notifications |

---

## Ключевые файлы

| Компонент | Файл |
|-----------|------|
| State Machine | `src/negotiations/state_machine.py` |
| Classifier | `src/negotiations/classifier.py` |
| Response Generator | `src/negotiations/response_generator.py` |
| Counter-Offer Engine | `src/negotiations/counter_offer.py` |
| Scope Change Handler | `src/negotiations/scope_change.py` |
| Follow-up Scheduler | `src/negotiations/followup.py` |
| Message Poller | `src/negotiations/poller.py` |
| API Routes | `src/api/routes/negotiations.py` |
| Pydantic Models | `src/core/models.py` (расширение) |
| Alembic Migration | `alembic/versions/YYYYMMDD_negotiation_tables.py` |
| Dashboard Chat | `dashboard/app/components/negotiation-chat.tsx` |
| Dashboard Kanban | `dashboard/app/routes/_app.bids.tsx` |
