# Infrastructure & Resilience: Спецификация

> WebSocket events каталог, concurrency control, crash recovery, Scout Capability Report. Всё что нужно для надёжной работы системы в production.

---

## 1. WebSocket Events Каталог

### Архитектура

**Endpoint:** `ws://host/ws/events` (без auth на handshake, JWT после подключения)

**Backend:** Litestar ChannelsPlugin + Valkey PubSub

**Lifecycle:**
1. Client → `connect()` → server `accept()`
2. Client → `{"type": "auth", "token": "Bearer ..."}` → server validates JWT
3. Server → `{"type": "auth:ok", "channels": [...]}` → subscribed to default channels
4. Server ping каждые 30s (keepalive), client отвечает pong
5. Client может подписаться на дополнительные каналы: `subscribe:project`, `subscribe:agent`
6. Max auth failures: 5 → `close(4001)`

### Publish Guard Pattern

Все `publish_event()` обёрнуты в `try/except (OSError, ConnectionError)`. WebSocket disconnect не должен ломать API response или agent execution.

```python
async def safe_publish(channels: ChannelsPlugin, channel: str, data: dict) -> None:
    try:
        await channels.publish(data, [channel])
    except (OSError, ConnectionError):
        logger.warning("ws_publish_failed", channel=channel)
```

### Полный каталог событий

#### Agent Events

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `agent:heartbeat` | `agent_heartbeat` | `{agent: str, status: "alive"\|"starting"\|"stopping", last_seen: ISO, uptime_seconds: int}` | HeartbeatMonitor (каждые 90с) |
| `agent:log` | `agent_log` | `{agent: str, level: "info"\|"warning"\|"error", message: str, timestamp: ISO, details: dict\|null}` | ConstrainedAgent._log() |
| `agent:{name}` | `agent_started` | `{agent: str, thread_id: str, job_id: str\|null}` | ConstrainedAgent.invoke() start |
| `agent:{name}` | `agent_completed` | `{agent: str, thread_id: str, duration_seconds: float, next_agent: str\|null, artifacts_count: int}` | ConstrainedAgent.invoke() end |
| `agent:{name}` | `agent_failed` | `{agent: str, thread_id: str, error: str, retry_count: int, will_retry: bool}` | ConstrainedAgent.invoke() error |
| `agent:{name}` | `agent_paused` | `{agent: str, thread_id: str, reason: "hitl"\|"loop_detected"\|"manual", hitl_id: str\|null}` | HITL pause |

#### HITL Events

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `hitl:new` | `hitl_created` | `{id: UUID, type: str, priority: str, title: str, bid_id: UUID\|null, created_at: ISO}` | HITLQueue insert |
| `hitl:resolved` | `hitl_resolved` | `{id: UUID, type: str, resolution: str, resolved_by: UUID, resolved_at: ISO}` | POST /hitl/{id}/resolve |
| `hitl:expired` | `hitl_expired` | `{id: UUID, type: str, title: str, expired_at: ISO}` | Expiry cron |

#### Project Events

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `project:update` | `project_status_changed` | `{project_id: UUID, old_status: str, new_status: str, agent: str}` | Agent state update |
| `project:{uuid}` | `project_artifact_created` | `{project_id: UUID, artifact_type: str, agent: str, artifact_id: str}` | Dev/Content/Design agent |
| `project:{uuid}` | `project_revision_started` | `{project_id: UUID, revision_count: int, target_agent: str, severity: str}` | Critic → revision |
| `project:{uuid}` | `project_delivery_ready` | `{project_id: UUID, delivery_type: str, artifact_count: int}` | Packager complete |

#### Orchestrator Events

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `orch:status` | `orchestrator_status` | `{alive: bool, uptime_seconds: int, current_goal: str\|null, health_grade: str}` | Orchestrator heartbeat |
| `orch:goal` | `goal_created` | `{goal_id: str, title: str, priority: str, category: str}` | POST /orchestrator/goals |
| `orch:goal` | `goal_completed` | `{goal_id: str, title: str, duration_seconds: float}` | Goal execution complete |
| `orch:goal` | `goal_failed` | `{goal_id: str, title: str, error: str}` | Goal execution failed |
| `orch:log` | `orchestrator_log` | `{level: str, message: str, timestamp: ISO, phase: str}` | Orchestrator runner |

#### Negotiation Events (Pipeline A)

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `negotiation:new_message` | `negotiation_message` | `{bid_id: UUID, message_id: UUID, direction: str, sender: str, message_type: str, requires_hitl: bool, preview: str}` | Message Poller |
| `negotiation:state_changed` | `negotiation_state` | `{bid_id: UUID, old_state: str, new_state: str, reason: str}` | NegotiationStateMachine.transition() |
| `negotiation:hitl_required` | `negotiation_hitl` | `{bid_id: UUID, hitl_id: UUID, hitl_type: str, title: str}` | Classifier → HITL |
| `negotiation:followup_sent` | `negotiation_followup` | `{bid_id: UUID, template: str, days_silent: int}` | FollowUpScheduler |

#### Pipeline B Events

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `pipeline_b:scan_progress` | `scan_update` | `{scan_id: str, mode: str, progress: float, leads_found: int, hexagons_scanned: int}` | GeoScout/WebScout |
| `pipeline_b:lead_scored` | `lead_scored` | `{lead_id: UUID, score: int, temperature: str, analysis_tier: str}` | LeadScorer |
| `pipeline_b:deal_update` | `deal_status` | `{deal_id: UUID, old_status: str, new_status: str, sales_stage: str\|null}` | SalesAgent |
| `pipeline_b:touch_sent` | `touch_update` | `{lead_id: UUID, step: int, channel: str, template: str}` | TouchSequenceManager |

#### System Events

| Канал | Событие | Payload | Источник |
|-------|---------|---------|----------|
| `notification` | `system_alert` | `{severity: "info"\|"warning"\|"critical", title: str, message: str, source: str}` | Любой компонент |
| `notification` | `capacity_warning` | `{resource: str, current: int, limit: int, percent: float}` | Capacity monitor |

### Event Schema (TypeScript для Dashboard)

```typescript
interface WSEvent {
  type: string;          // "agent_heartbeat", "hitl_created", etc.
  channel: string;       // Канал откуда пришло
  timestamp: string;     // ISO 8601
  data: Record<string, unknown>;
}

// Подписка
interface WSSubscribe {
  type: "subscribe";
  channel: string;       // "project:{uuid}", "agent:{name}"
}

// Auth
interface WSAuth {
  type: "auth";
  token: string;         // "Bearer eyJ..."
}
```

### Type Sync Strategy (Python → TypeScript)

Для предотвращения drift между Python event types и TypeScript types на Dashboard:

**Source of Truth:** Python dataclasses в `src/api/ws_events.py`

```python
# src/api/ws_events.py — canonical event definitions

@dataclass(frozen=True)
class AgentHeartbeatEvent:
    agent: str
    status: Literal["alive", "starting", "stopping"]
    last_seen: str  # ISO 8601
    uptime_seconds: int

    EVENT_TYPE: ClassVar[str] = "agent_heartbeat"
    CHANNEL: ClassVar[str] = "agent:heartbeat"

@dataclass(frozen=True)
class HITLCreatedEvent:
    id: str  # UUID as string
    type: str
    priority: str
    title: str
    bid_id: str | None
    created_at: str  # ISO 8601

    EVENT_TYPE: ClassVar[str] = "hitl_created"
    CHANNEL: ClassVar[str] = "hitl:new"

# ... аналогично для всех 30+ событий
```

**Генерация TypeScript types:**

```bash
# CI step: генерирует dashboard/app/types/ws-events.ts из Python dataclasses
python scripts/generate_ws_types.py > dashboard/app/types/ws-events.ts
```

Скрипт `generate_ws_types.py`:
1. Импортирует все `*Event` классы из `ws_events.py`
2. Для каждого dataclass генерирует TypeScript interface
3. Генерирует union type `WSEventData` и discriminated union `WSEvent`
4. CI проверяет: `git diff --exit-code dashboard/app/types/ws-events.ts` (drift detection)

**Сгенерированный файл (пример):**

```typescript
// AUTO-GENERATED from src/api/ws_events.py — DO NOT EDIT
// Run: python scripts/generate_ws_types.py

export interface AgentHeartbeatEvent {
  agent: string;
  status: "alive" | "starting" | "stopping";
  last_seen: string;
  uptime_seconds: number;
}

export interface HITLCreatedEvent {
  id: string;
  type: string;
  priority: string;
  title: string;
  bid_id: string | null;
  created_at: string;
}

// ... 30+ interfaces

export type WSEventType = "agent_heartbeat" | "hitl_created" | ... ;

export type WSEvent =
  | { type: "agent_heartbeat"; channel: string; timestamp: string; data: AgentHeartbeatEvent }
  | { type: "hitl_created"; channel: string; timestamp: string; data: HITLCreatedEvent }
  | ... ;
```

**CI Integration:**

```yaml
# .github/workflows/ci.yml
- name: Check WS types sync
  run: |
    python scripts/generate_ws_types.py > /tmp/ws-events.ts
    diff /tmp/ws-events.ts dashboard/app/types/ws-events.ts || (echo "WS types out of sync! Run: python scripts/generate_ws_types.py" && exit 1)
```

---

## 2. Concurrency Control

### Проблема

Множество параллельных процессов могут конфликтовать:

1. **Два poll task на один bid** — duplicate messages
2. **HITL resolve + AI response** — race condition при operator override
3. **Параллельные агенты** на одном thread_id — state corruption
4. **Checkpoint save** concurrent — lost updates
5. **Follow-up + client reply** — отправка follow-up после того как клиент уже ответил

### Locking Strategy

#### 1. Bid-level Lock (Negotiations)

```python
# src/negotiations/locks.py

class NegotiationLock:
    """Distributed lock на уровне bid для предотвращения параллельного polling/processing."""

    def __init__(self, valkey: Valkey) -> None:
        self.valkey = valkey
        self.lock_ttl = 120  # 2 минуты максимум

    async def acquire(self, bid_id: str, owner: str = "poller") -> bool:
        """Попытка захватить lock.

        Args:
            bid_id: UUID бида.
            owner: Идентификатор процесса (для дебага).

        Returns:
            True если lock получен, False если занят.
        """
        key = f"lock:negotiation:{bid_id}"
        return await self.valkey.set(
            key,
            f"{owner}:{utcnow().isoformat()}",
            nx=True,  # SET IF NOT EXISTS
            ex=self.lock_ttl,
        )

    async def release(self, bid_id: str) -> None:
        key = f"lock:negotiation:{bid_id}"
        await self.valkey.delete(key)

    @asynccontextmanager
    async def lock(self, bid_id: str, owner: str = "poller"):
        """Context manager для lock/unlock."""
        acquired = await self.acquire(bid_id, owner)
        if not acquired:
            raise LockConflictError(f"Bid {bid_id} already locked")
        try:
            yield
        finally:
            await self.release(bid_id)
```

**Использование в Message Poller:**

```python
async def poll_platform(self, platform: str, bid: Bid) -> list[RawMessage]:
    try:
        async with self.lock.lock(str(bid.id), owner="poller"):
            # ... polling logic ...
            return new_messages
    except LockConflictError:
        logger.debug("bid_locked_skipping", bid_id=str(bid.id))
        return []
```

#### 2. Thread-level Lock (LangGraph)

```python
# src/core/graph.py

class ThreadLock:
    """Lock по thread_id для предотвращения параллельных invocations одного графа."""

    def __init__(self, valkey: Valkey) -> None:
        self.valkey = valkey
        self.lock_ttl = 600  # 10 минут (макс. время одного agent run)

    @asynccontextmanager
    async def lock(self, thread_id: str, agent: str):
        key = f"lock:thread:{thread_id}"
        acquired = await self.valkey.set(
            key,
            f"{agent}:{utcnow().isoformat()}",
            nx=True,
            ex=self.lock_ttl,
        )
        if not acquired:
            existing = await self.valkey.get(key)
            raise ThreadBusyError(
                f"Thread {thread_id} locked by {existing}. "
                f"Agent {agent} cannot proceed."
            )
        try:
            yield
        finally:
            await self.valkey.delete(key)
```

**Использование в graph invocation:**

```python
async def run_pipeline(thread_id: str, state: dict) -> dict:
    async with thread_lock.lock(thread_id, agent=state.get("current_agent", "unknown")):
        async for chunk in graph.astream(state, config, stream_mode="values"):
            final = chunk
    return final
```

#### 3. HITL Resolve Lock

```python
# src/api/routes/hitl.py

async def resolve_hitl(hitl_id: UUID, action: str, ...) -> dict:
    """Resolve HITL с optimistic locking."""

    # Atomic update: только pending → resolved
    result = await db.execute(
        update(HITLQueue)
        .where(HITLQueue.id == hitl_id, HITLQueue.status == "pending")
        .values(
            status="resolved",
            resolution=action,
            resolved_by=user.id,
            resolved_at=utcnow(),
        )
        .returning(HITLQueue.id)
    )

    if result.rowcount == 0:
        # Уже resolved (race condition) или expired
        existing = await db.get(HITLQueue, hitl_id)
        if existing and existing.status == "resolved":
            raise HTTPException(409, "Already resolved")
        raise HTTPException(404, "HITL item not found or expired")
```

#### 4. Operator Override vs AI Response

```python
# src/negotiations/processor.py

async def process_classified_message(bid: Bid, message: ClientMessage, ...) -> None:
    """Обработка с проверкой operator_override ПЕРЕД генерацией ответа."""

    # 1. Проверить state ПЕРЕД тем как тратить LLM tokens
    negotiation = await db.get_negotiation(bid_id=bid.id)
    if negotiation.state == "operator_override":
        # Только уведомить, не генерировать ответ
        await ws_publish("negotiation:new_message", {...})
        return

    # 2. Генерировать ответ (может занять 5-10с)
    response = await response_generator.generate(...)

    # 3. ПОВТОРНО проверить state ПОСЛЕ генерации (operator мог вмешаться)
    negotiation = await db.get_negotiation(bid_id=bid.id)
    if negotiation.state == "operator_override":
        logger.info("ai_response_cancelled_operator_override", bid_id=str(bid.id))
        return

    # 4. Отправить
    await _send_and_save(bid, response.text, ...)
```

#### 5. Follow-up Guard

```python
# src/negotiations/followup.py

async def _send_followup(self, negotiation: Negotiation, step: dict) -> None:
    """Отправка follow-up с final check на ответ клиента."""

    # Final check: клиент мог ответить между проверкой и отправкой
    client_replied = await self.db.has_inbound_after(
        bid_id=negotiation.bid_id,
        after=utcnow() - timedelta(minutes=5),  # последние 5 минут
    )
    if client_replied:
        logger.info("followup_cancelled_client_replied", bid_id=str(negotiation.bid_id))
        return

    # Atomic: increment followup_count + send
    async with self.lock.lock(str(negotiation.bid_id), owner="followup"):
        await self._send_and_record(negotiation, step)
```

### Lock Summary

| Lock | Key Pattern | TTL | Защищает от |
|------|-------------|-----|-------------|
| Negotiation | `lock:negotiation:{bid_id}` | 120s | Parallel polling |
| Thread | `lock:thread:{thread_id}` | 600s | Parallel graph invocation |
| HITL | SQL `WHERE status='pending'` | — | Double resolve |
| Follow-up | `lock:negotiation:{bid_id}` (shared) | 120s | Follow-up after reply |

### Deadlock Prevention

- Все locks имеют TTL (auto-expire)
- Порядок захвата: thread lock → bid lock (если оба нужны)
- Нет вложенных locks одного типа
- `try/finally` гарантирует release

---

## 3. Crash Recovery

### Проблема

Агенты могут crash'нуться посреди выполнения:

1. **LLM timeout** — API не отвечает 60+ секунд
2. **OOM** — Docker container killed by OOM
3. **Platform ban** — adapter бросает необработанное исключение
4. **DB connection lost** — PostgreSQL перезапуск
5. **Deploy** — новая версия при работающем агенте
6. **Valkey restart** — потеря in-memory state

### LangGraph Checkpoint System

```python
# src/core/checkpoints.py

class HybridCheckpointSaver:
    """Двойное сохранение checkpoints: Valkey (быстрый доступ) + PostgreSQL (durability).

    Valkey — горячий кэш для быстрого resume.
    PostgreSQL — persistent store для recovery после полного restart.
    """

    def __init__(self, valkey: Valkey, db_engine: AsyncEngine) -> None:
        self.valkey = valkey
        self.db = db_engine
        self.valkey_ttl = 86400  # 24 часа

    async def put(self, config: dict, checkpoint: dict, metadata: dict) -> None:
        """Сохранить checkpoint.

        Args:
            config: {"configurable": {"thread_id": "...", "checkpoint_ns": "..."}}
            checkpoint: Полное состояние графа (AgentState dict)
            metadata: {"source": "loop", "step": 3, "writes": {...}}
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]

        serialized = json.dumps(checkpoint, default=str)

        # 1. Valkey (fast path)
        valkey_key = f"checkpoint:{thread_id}:{checkpoint_id}"
        await self.valkey.set(valkey_key, serialized, ex=self.valkey_ttl)

        # Latest pointer
        await self.valkey.set(f"checkpoint:latest:{thread_id}", checkpoint_id, ex=self.valkey_ttl)

        # 2. PostgreSQL (durable)
        await self._upsert_pg(thread_id, checkpoint_id, serialized, metadata)

    async def get(self, config: dict) -> dict | None:
        """Загрузить последний checkpoint.

        Стратегия: Valkey first → PostgreSQL fallback.
        """
        thread_id = config["configurable"]["thread_id"]

        # 1. Попытка Valkey
        latest_id = await self.valkey.get(f"checkpoint:latest:{thread_id}")
        if latest_id:
            data = await self.valkey.get(f"checkpoint:{thread_id}:{latest_id}")
            if data:
                return json.loads(data)

        # 2. Fallback: PostgreSQL
        row = await self._get_latest_pg(thread_id)
        if row:
            checkpoint = json.loads(row.data)
            # Restore to Valkey cache
            await self.valkey.set(
                f"checkpoint:{thread_id}:{row.checkpoint_id}",
                row.data,
                ex=self.valkey_ttl,
            )
            await self.valkey.set(
                f"checkpoint:latest:{thread_id}",
                row.checkpoint_id,
                ex=self.valkey_ttl,
            )
            return checkpoint

        return None

    async def list(self, config: dict, limit: int = 10) -> list[dict]:
        """Список checkpoint'ов для thread_id (из PostgreSQL)."""
        thread_id = config["configurable"]["thread_id"]
        rows = await self._list_pg(thread_id, limit)
        return [{"id": r.checkpoint_id, "metadata": r.metadata, "created_at": r.created_at} for r in rows]
```

### DB Table: checkpoints

```sql
CREATE TABLE checkpoints (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id       VARCHAR(255) NOT NULL,
    checkpoint_id   VARCHAR(255) NOT NULL,
    checkpoint_ns   VARCHAR(255) NOT NULL DEFAULT '',
    data            TEXT NOT NULL,          -- JSON serialized state
    metadata        JSONB DEFAULT '{}',     -- step, source, writes
    parent_id       VARCHAR(255),           -- Previous checkpoint
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(thread_id, checkpoint_id, checkpoint_ns)
);

CREATE INDEX idx_checkpoints_thread ON checkpoints(thread_id, created_at DESC);
CREATE INDEX idx_checkpoints_latest ON checkpoints(thread_id, checkpoint_ns, created_at DESC);
```

### Resume Flow

```python
# src/api/services/orchestrator.py

async def resume_from_crash(thread_id: str) -> dict:
    """Возобновление pipeline после crash.

    1. Загрузить последний checkpoint
    2. Проверить состояние (paused/active/failed)
    3. Решить куда продолжить
    4. Запустить граф с восстановленным state
    """
    checkpoint_saver = get_checkpoint_saver()
    config = {"configurable": {"thread_id": thread_id}}

    # 1. Загрузить checkpoint
    checkpoint = await checkpoint_saver.get(config)
    if checkpoint is None:
        raise NoCheckpointError(f"No checkpoint for thread {thread_id}")

    state = checkpoint  # AgentState dict

    # 2. Проверить состояние
    status = state.get("status", "unknown")
    current_agent = state.get("current_agent")

    logger.info("resuming_from_crash",
        thread_id=thread_id,
        status=status,
        current_agent=current_agent,
        step=checkpoint.get("__metadata__", {}).get("step"),
    )

    # 3. Определить точку входа
    if status == "paused" and state.get("requires_hitl"):
        # HITL pending — не резюмить автоматически, ждать HITL resolve
        logger.info("crash_recovery_hitl_pending", thread_id=thread_id)
        return {"action": "wait_for_hitl", "hitl_id": state.get("hitl_request_id")}

    if status == "failed":
        # Проверить retry count
        retry_count = state.get("_crash_retry_count", 0)
        if retry_count >= MAX_CRASH_RETRIES:
            logger.error("crash_recovery_max_retries",
                thread_id=thread_id, retries=retry_count)
            await _create_crash_hitl(thread_id, state)
            return {"action": "escalate_to_hitl"}

        # Retry: reset status, increment retry count
        state["status"] = "active"
        state["_crash_retry_count"] = retry_count + 1

    if status == "active":
        # Agent was mid-execution — restart from current agent
        state["_crash_retry_count"] = state.get("_crash_retry_count", 0) + 1

    # 4. Запустить граф
    entry_node = _determine_entry_node(state)
    final_state = await run_pipeline(thread_id, state, entry_node=entry_node)

    return {"action": "resumed", "final_status": final_state.get("status")}


def _determine_entry_node(state: dict) -> str:
    """Определяет ноду графа для resume.

    Стратегия: перезапустить текущий агент (idempotent by design).
    """
    current = state.get("current_agent")
    if current:
        return f"{current}_node"

    # Fallback: использовать routing logic
    next_agent = state.get("next_agent")
    if next_agent:
        return f"{next_agent}_node"

    # Last resort: start from beginning
    return "scout_node"


MAX_CRASH_RETRIES = 3
```

### Crash Detection

```python
# src/core/heartbeat.py

class HeartbeatMonitor:
    """Мониторинг heartbeat'ов агентов. Детекция crash'ей."""

    HEARTBEAT_INTERVAL = 90      # секунд между heartbeat'ами
    DEAD_THRESHOLD = 180         # секунд без heartbeat → agent считается dead
    AUTO_RESTART_THRESHOLD = 180 # секунд → авто-restart

    async def check_all_agents(self) -> list[AgentHealth]:
        """Проверяет здоровье всех агентов.

        Вызывается cron-задачей каждые 60 секунд.
        """
        agents = await self._get_registered_agents()
        results = []

        for agent_name in agents:
            last_seen = await self.valkey.get(f"heartbeat:{agent_name}")

            if last_seen is None:
                status = "unknown"
                seconds_ago = None
            else:
                seconds_ago = (utcnow() - datetime.fromisoformat(last_seen)).total_seconds()
                if seconds_ago < self.HEARTBEAT_INTERVAL * 1.5:
                    status = "alive"
                elif seconds_ago < self.DEAD_THRESHOLD:
                    status = "stale"
                else:
                    status = "dead"

            if status == "dead":
                await self._handle_dead_agent(agent_name, seconds_ago)

            results.append(AgentHealth(
                agent=agent_name,
                status=status,
                last_seen_seconds_ago=seconds_ago,
            ))

        return results

    async def _handle_dead_agent(self, agent_name: str, seconds_ago: float) -> None:
        """Обработка dead агента."""

        # 1. Проверить есть ли активный thread
        active_thread = await self._get_active_thread(agent_name)

        if active_thread:
            # 2. Попытка resume
            logger.warning("agent_dead_attempting_resume",
                agent=agent_name, thread_id=active_thread, seconds_silent=seconds_ago)

            try:
                await resume_from_crash(active_thread)
            except Exception as e:
                logger.error("crash_recovery_failed",
                    agent=agent_name, thread_id=active_thread, error=str(e))
                # Создать HITL alert
                await _create_crash_hitl(active_thread, {"current_agent": agent_name, "error": str(e)})

        # 3. WebSocket notification
        await safe_publish(channels, "notification", {
            "type": "system_alert",
            "severity": "warning",
            "title": f"Agent {agent_name} is not responding",
            "message": f"No heartbeat for {int(seconds_ago)}s. Auto-recovery attempted.",
            "source": "heartbeat_monitor",
        })
```

### Crash HITL Alert

```python
async def _create_crash_hitl(thread_id: str, state: dict) -> None:
    """Создать HITL alert при crash, который не удалось автоматически восстановить."""

    await create_hitl_entry(
        type="alert",
        priority="urgent",
        title=f"Agent crash: {state.get('current_agent', 'unknown')} (thread {thread_id[:8]}...)",
        description=(
            f"Agent {state.get('current_agent')} crashed after {state.get('_crash_retry_count', 0)} retries. "
            f"Manual intervention required."
        ),
        payload={
            "thread_id": thread_id,
            "current_agent": state.get("current_agent"),
            "status": state.get("status"),
            "retry_count": state.get("_crash_retry_count", 0),
            "last_error": state.get("_last_error"),
            "artifacts": list(state.get("artifacts", {}).keys()),
        },
        available_actions=["retry", "skip_agent", "abort_pipeline"],
    )
```

### Recovery Matrix

| Сценарий | Детекция | Recovery | Fallback |
|----------|----------|----------|----------|
| LLM timeout | `asyncio.TimeoutError` after 600s | Retry (max 3) в ConstrainedAgent | HITL alert |
| OOM kill | Heartbeat dead > 180s | Resume from checkpoint | HITL alert |
| Platform ban | `AccountSuspendedError` | Stop all bids on platform | CRITICAL HITL |
| DB connection lost | `ConnectionError` / `OperationalError` | Retry with backoff (5, 10, 30s) | Container restart |
| Deploy mid-execution | Graceful shutdown signal | Save checkpoint → resume after deploy | Auto-resume on startup |
| Valkey restart | `ConnectionError` on Valkey | Fallback to PostgreSQL checkpoints | Degraded mode (no cache) |

### Startup Recovery

```python
# src/api/main.py (lifespan)

async def on_startup() -> None:
    """При старте: проверить незавершённые pipelines и попытаться resume."""

    # Найти все threads с status='active' которые не имеют heartbeat
    stale_threads = await db.execute(
        select(PipelineRun)
        .where(
            PipelineRun.status.in_(["active", "paused"]),
            PipelineRun.updated_at < utcnow() - timedelta(minutes=5),
        )
    )

    for run in stale_threads.scalars():
        logger.info("startup_recovery_candidate",
            thread_id=run.thread_id, status=run.status, agent=run.current_agent)

        if run.status == "paused":
            # HITL pending — don't auto-resume
            continue

        # Attempt resume
        try:
            asyncio.create_task(resume_from_crash(run.thread_id))
        except Exception as e:
            logger.error("startup_recovery_failed", thread_id=run.thread_id, error=str(e))
```

---

## 4. Scout Capability Report

### Назначение

Scout Agent формирует Capability Report — структурированный отчёт о возможностях системы. Bid Agent использует его для честных обещаний в proposals. Planner использует для реалистичной декомпозиции.

### Capability Matrix

```python
# src/agents/capability_report.py

CAPABILITY_MATRIX: dict[str, Capability] = {
    # === Frontend ===
    "react_nextjs": Capability(
        name="React / Next.js",
        category="frontend",
        confidence=0.95,
        tools=["create-next-app", "vercel-cli"],
        examples=["SPA dashboard", "SSR landing page", "E-commerce storefront"],
        limitations=["No React Native (mobile)", "No Electron (desktop)"],
    ),
    "html_css_tailwind": Capability(
        name="HTML/CSS/Tailwind",
        category="frontend",
        confidence=0.98,
        tools=["tailwindcss", "postcss"],
        examples=["Landing pages", "Email templates", "Static sites"],
        limitations=[],
    ),
    "vue_svelte": Capability(
        name="Vue.js / Svelte",
        category="frontend",
        confidence=0.85,
        tools=["vue-cli", "sveltekit"],
        examples=["Interactive dashboards", "Admin panels"],
        limitations=["Less experience than React"],
    ),
    "wordpress": Capability(
        name="WordPress",
        category="cms",
        confidence=0.90,
        tools=["wp-cli", "elementor"],
        examples=["Business sites", "Blogs", "WooCommerce stores"],
        limitations=["Custom plugin development may need review"],
    ),
    "shopify_webflow": Capability(
        name="Shopify / Webflow",
        category="cms",
        confidence=0.80,
        tools=["shopify-cli", "webflow-api"],
        examples=["E-commerce", "Portfolio sites"],
        limitations=["Limited custom backend logic"],
    ),

    # === Backend ===
    "python_litestar": Capability(
        name="Python / Litestar / FastAPI",
        category="backend",
        confidence=0.95,
        tools=["poetry", "alembic", "pytest"],
        examples=["REST APIs", "WebSocket servers", "Background workers"],
        limitations=[],
    ),
    "nodejs_express": Capability(
        name="Node.js / Express",
        category="backend",
        confidence=0.90,
        tools=["npm", "prisma"],
        examples=["REST APIs", "GraphQL servers", "Microservices"],
        limitations=[],
    ),
    "postgresql": Capability(
        name="PostgreSQL",
        category="database",
        confidence=0.95,
        tools=["psql", "pgvector", "alembic"],
        examples=["Schema design", "Migrations", "Vector search"],
        limitations=[],
    ),

    # === DevOps ===
    "docker_deploy": Capability(
        name="Docker / Deploy",
        category="devops",
        confidence=0.90,
        tools=["docker-compose", "railway-cli", "vercel-cli", "netlify-cli"],
        examples=["Containerized apps", "CI/CD pipelines", "Railway deploy"],
        limitations=["No Kubernetes", "No AWS/GCP/Azure native services"],
    ),

    # === Content ===
    "copywriting": Capability(
        name="Copywriting / Content",
        category="content",
        confidence=0.90,
        tools=[],
        examples=["Landing page copy", "Blog posts", "Email sequences", "UI text"],
        limitations=["No video production", "No podcast editing"],
    ),

    # === Design ===
    "ui_ux_design": Capability(
        name="UI/UX Design",
        category="design",
        confidence=0.85,
        tools=["pencil.dev", "figma (read-only)"],
        examples=["Wireframes", "Mockups", "Design systems", "Responsive layouts"],
        limitations=["No 3D modeling", "No animation (complex)", "No print design"],
    ),
}

# Категории, которые ВСЕГДА reject
REJECT_CATEGORIES = {
    "mobile_app": "Mobile app development (React Native, Flutter, Swift, Kotlin)",
    "ai_ml": "AI/ML model training and deployment",
    "blockchain": "Blockchain / Web3 / Smart contracts",
    "erp": "Enterprise ERP systems (SAP, 1C)",
    "game_dev": "Game development (Unity, Unreal)",
    "embedded": "Embedded systems / IoT firmware",
    "desktop_app": "Desktop applications (Electron exceptions possible)",
}
```

### Dynamic Capability Registry (будущее)

Текущая `CAPABILITY_MATRIX` — статический dict. В будущем планируется **Dynamic Capability Registry**, который обновляется автоматически:

1. **Experience Feedback Loop:** После каждого завершённого проекта Packager записывает `(capability_id, success: bool, quality_score)` в таблицу `capability_history`
2. **Confidence Recalculation:** Cron (ежедневно) пересчитывает `confidence` на основе success_rate последних 30 проектов:
   ```python
   new_confidence = 0.7 * historical_confidence + 0.3 * recent_success_rate
   ```
3. **New Capability Detection:** Если Dev Agent успешно выполнил задачу с requirement, не matching ни одной capability → предлагает оператору добавить новую capability (HITL)
4. **Deprecation:** Если capability не использовалась 90 дней → `confidence *= 0.9` (decay)

**Таблица `capability_history` (планируется):**

```sql
CREATE TABLE capability_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability_id VARCHAR(50) NOT NULL,
    project_id UUID REFERENCES projects(id),
    success BOOLEAN NOT NULL,
    quality_score FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**IMPORTANT:** До реализации Dynamic Registry, обновление CAPABILITY_MATRIX — ручной процесс. При добавлении нового стека (например, Flutter support) — добавить запись в dict и обновить specs.

### Capability Report Generation

```python
class CapabilityReporter:
    """Генерирует Capability Report для конкретного job/lead."""

    def generate(self, job_requirements: list[str], job_category: str) -> CapabilityReport:
        """Создаёт отчёт о соответствии возможностей требованиям job.

        Args:
            job_requirements: Список требований из описания вакансии.
            job_category: Категория (web_dev, ui_ux, copywriting, etc.).

        Returns:
            CapabilityReport с matched/unmatched capabilities и confidence.
        """
        matched: list[CapabilityMatch] = []
        gaps: list[str] = []
        rejected = False
        reject_reason = None

        # Проверить reject categories
        for reject_key, reject_desc in REJECT_CATEGORIES.items():
            if self._matches_category(job_requirements, job_category, reject_key):
                rejected = True
                reject_reason = reject_desc
                break

        if not rejected:
            for req in job_requirements:
                best_match = self._find_best_capability(req)
                if best_match:
                    matched.append(best_match)
                else:
                    gaps.append(req)

        overall_confidence = self._calculate_confidence(matched, gaps)

        return CapabilityReport(
            matched_capabilities=matched,
            gaps=gaps,
            rejected=rejected,
            reject_reason=reject_reason,
            overall_confidence=overall_confidence,
            recommended_agents=self._recommend_agents(matched),
            estimated_complexity=self._estimate_complexity(matched, gaps),
        )

    def _find_best_capability(self, requirement: str) -> CapabilityMatch | None:
        """Находит лучшее совпадение capability для требования."""
        best = None
        best_score = 0.0

        for cap_id, cap in CAPABILITY_MATRIX.items():
            score = self._match_score(requirement, cap)
            if score > best_score and score > 0.5:
                best = CapabilityMatch(
                    capability_id=cap_id,
                    capability_name=cap["name"],
                    match_score=score,
                    confidence=cap["confidence"],
                )
                best_score = score

        return best

    def _calculate_confidence(
        self, matched: list[CapabilityMatch], gaps: list[str]
    ) -> float:
        """Общая уверенность = средний confidence matched * coverage ratio."""
        if not matched:
            return 0.0
        avg_confidence = sum(m["confidence"] for m in matched) / len(matched)
        coverage = len(matched) / (len(matched) + len(gaps))
        return round(avg_confidence * coverage, 2)


class CapabilityReport(TypedDict):
    matched_capabilities: list[CapabilityMatch]
    gaps: list[str]                    # Требования без matching capability
    rejected: bool                     # True если job в reject category
    reject_reason: str | None
    overall_confidence: float          # 0.0 - 1.0
    recommended_agents: list[str]      # ["dev", "design", "content"]
    estimated_complexity: str          # "simple" | "standard" | "complex"


class CapabilityMatch(TypedDict):
    capability_id: str
    capability_name: str
    match_score: float     # Насколько хорошо capability подходит к requirement
    confidence: float      # Уверенность в выполнении
```

### Использование в Scout

```python
# src/agents/scout.py (внутри _score_jobs)

async def _score_job(self, job: dict) -> float:
    """LLM-скоринг с Capability Report."""

    # 1. Сгенерировать capability report
    report = self.capability_reporter.generate(
        job_requirements=job.get("skills", []),
        job_category=job.get("category", ""),
    )

    # 2. Auto-reject если в reject category
    if report["rejected"]:
        return 0.0

    # 3. Включить report в LLM prompt
    scoring_context = {
        "job": job,
        "capability_report": {
            "matched": [m["capability_name"] for m in report["matched_capabilities"]],
            "gaps": report["gaps"],
            "confidence": report["overall_confidence"],
            "complexity": report["estimated_complexity"],
        },
    }

    # 4. LLM scoring с capability awareness
    score = await self._call_llm(scoring_context)
    return score
```

### Использование в Bid Agent

```python
# src/agents/bid.py (внутри _generate_proposal)

async def _generate_proposal(self, state: dict) -> str:
    """Генерация proposal с честными capability claims."""

    capability_report = state.get("artifacts", {}).get("capability_report")

    prompt_context = {
        "job": state["project"],
        "capability_report": capability_report,
        "rules": [
            "ONLY claim capabilities listed in matched_capabilities",
            "If gaps exist, acknowledge them honestly or propose alternatives",
            "Never claim experience with reject_categories",
            f"Overall confidence: {capability_report['overall_confidence']:.0%}",
        ],
    }

    proposal = await self._call_llm(prompt_context)
    return proposal
```

### Capability Report в State

```python
# Scout записывает в artifacts
update_state(state,
    artifacts={
        **artifacts,
        "scout": [qualified_job_ids],
        "capability_report": report,  # CapabilityReport dict
    },
)
```

---

## Конфигурация

### Environment Variables

```bash
# Concurrency
LOCK_TTL_NEGOTIATION_SECONDS=120
LOCK_TTL_THREAD_SECONDS=600

# Crash Recovery
MAX_CRASH_RETRIES=3
HEARTBEAT_INTERVAL_SECONDS=90
DEAD_AGENT_THRESHOLD_SECONDS=180
STARTUP_RECOVERY_ENABLED=true

# Checkpoints
CHECKPOINT_VALKEY_TTL_SECONDS=86400
CHECKPOINT_CLEANUP_DAYS=30

# WebSocket
WS_PING_INTERVAL_SECONDS=30
WS_MAX_AUTH_FAILURES=5
WS_AUTH_TIMEOUT_SECONDS=10
```

---

## Зависимости

| Компонент | Спека |
|-----------|-------|
| API & WebSocket | `specs/api-spec.md` — channels, publish_event |
| Agents | `specs/agents-spec.md` — ConstrainedAgent, heartbeat |
| HITL System | `specs/hitl-spec.md` — crash alert HITL |
| Orchestrator | `specs/orchestrator-spec.md` — Sisyphus runner |
| Deploy | `specs/deploy-spec.md` — Docker, Railway restart |
| Negotiation | `specs/negotiation-spec.md` — poller locks |
| Database | `specs/database-spec.md` — checkpoints table |

---

## Ключевые файлы

| Компонент | Файл |
|-----------|------|
| WebSocket Handler | `src/api/websocket.py` |
| Negotiation Lock | `src/negotiations/locks.py` |
| Thread Lock | `src/core/locks.py` |
| Checkpoint Saver | `src/core/checkpoints.py` |
| Crash Recovery | `src/api/services/orchestrator.py` |
| Heartbeat Monitor | `src/core/heartbeat.py` |
| Capability Report | `src/agents/capability_report.py` |
| Capability Matrix | `src/agents/capability_report.py` (CAPABILITY_MATRIX) |
