# Plan: SalesAgent + Deal Memory (Tasks #13 + #15)

## Scope
Full implementation per `docs/Full_work/specs/sales-agent-spec.md`.
Всё рабочее, остаются только API ключи и внешние подключения.

## Files to Create

### 1. `src/core/deal_memory.py` (Task #15)
- `DealMemory` class — key-value store для deal context
- Methods: save_fact, get_facts, get_full_context, delete_fact, get_conversation_summary
- In-memory store (Phase 2), готово к миграции на DB (client_context table)

### 2. `src/prompts/sales.py`
- `SALES_AGENT_SYSTEM_PROMPT` — полный system prompt из спеки
- `CONCEPT_TEMPLATE` — шаблон генерации концепции
- `STAGE_INSTRUCTIONS` — инструкции по этапам

### 3. `src/negotiations/sales_conversation.py`
- `SalesStage(StrEnum)` — 11 состояний
- `SALES_TRANSITIONS` — матрица переходов
- `SalesAction` dataclass (type, message, auto_send, hitl_type, new_stage)
- `ClientMessage` dataclass (text, channel, timestamp)
- `ConceptData` dataclass (title, services, description, timeline, cost, rationale)
- `SalesConversationEngine` class:
  - `process_client_reply(deal, message)` → SalesAction
  - `_determine_action(response, stage, deal)` → SalesAction
  - `_detect_stage_transition(response, stage)` → str | None
  - `_needs_hitl(response, stage)` → bool
  - `_format_conversation(messages)` → str
  - `generate_first_contact(deal, lead, battlecard)` → str
  - `generate_concept(business, needs, battlecard)` → ConceptData

### 4. `src/agents/sales_agent.py`
- `SalesAgent(ConstrainedAgent)` — agent_name="sales_agent"
- `SALES_ALLOWED_TOOLS` — 7 tools
- `_execute(state)` → AgentState
- Tool implementations as async functions:
  - `get_competitor_analysis(business_name, city, category)` → dict
  - `get_market_insights(category, city)` → dict
  - `generate_concept(business, needs, battlecard)` → dict
  - `send_message(lead_id, channel, text)` → dict (stub for actual sending)
  - `save_deal_context(deal_id, key, value)` → None
  - `get_deal_context(deal_id)` → dict
  - `get_battlecard(lead_id)` → dict
- HITL creation for concept_review + concept_approved
- `sales_agent_node(state)` — LangGraph entry point

### 5. `src/negotiations/__init__.py` — update exports

## Test Files

### `tests/unit/test_deal_memory.py` (~25 tests)
- Save/get/delete facts
- Full context retrieval
- Edge cases (missing deal, duplicate keys)

### `tests/unit/test_sales_conversation.py` (~35 tests)
- SalesStage enum (11 states)
- SALES_TRANSITIONS matrix validation
- ConversationEngine: stage detection, HITL needs, first contact generation
- SalesAction/ClientMessage/ConceptData models
- Full conversation flows

### `tests/unit/test_sales_agent.py` (~30 tests)
- Agent construction, _execute with mock LLM
- Tool dispatch
- HITL creation for concept_review / concept_approved
- State transitions through full pipeline
- Error handling, edge cases

## Dependencies
- Existing: ConstrainedAgent, LLMClient, NegotiationEngine, TouchSequenceManager, LeadScorer, BusinessAnalyzer
- LLM: Claude Opus 4.6 via OpenRouter (Tier 1)
- DB: Deal model (exists), client_context (Phase 3+ for DB persistence)

## Quality Protocol
Plan → Tests (RED) → Implementation (GREEN) → Lint → Regression → Commit
