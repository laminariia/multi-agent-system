# Multi-Agent Orchestration Patterns (Sisyphus Architecture)

> **Источник:** Oh My OpenCode / Sisyphus  
> **Адаптация для:** Multi-Agent Service (Python + LangGraph)  
> **Статус:** Документация для будущей реализации

> [!NOTE]
> **Valkey Compatibility:** Code samples use `redis`/`Redis` variable names. This is intentional — **Valkey 8.1** is fully Redis-protocol compatible. Production runs on Valkey (see `deployment.md`).

---

## 📋 Оглавление

1. [Архитектура оркестрации](#1-архитектура-оркестрации)
2. [Иерархия агентов](#2-иерархия-агентов)
3. [Категории задач](#3-категории-задач)
4. [Hook System (Lifecycle Events)](#4-hook-system)
5. [Background Execution](#5-background-execution)
6. [Swarm (Multi-Agent Teams)](#6-swarm-multi-agent-teams)
7. [Режимы работы](#7-режимы-работы)
8. [Рекомендуемые паттерны для MAS](#8-рекомендуемые-паттерны-для-mas)

---

## 1. Архитектура оркестрации

### Как работает Oh My OpenCode

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        USER PROMPT                                       │
│                  "ulw add authentication"                                │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     HOOK: UserPromptSubmit                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  keyword-detector → detects "ulw" → activates ultrawork mode    │    │
│  │  think-mode → checks for "ultrathink" → adjusts thinking budget │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    SISYPHUS (Main Orchestrator)                         │
│                    Model: Claude Opus 4.6                               │
│                    Thinking Budget: 32k tokens                          │
│                                                                         │
│  1. Анализирует задачу                                                  │
│  2. Определяет категорию (visual-engineering, ultrabrain, etc.)         │
│  3. Выбирает стратегию: сам или делегировать                            │
│  4. Отслеживает TODO-list до полного завершения                         │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Oracle    │     │  Librarian  │     │   Explore   │
│(Sonnet 4.5)  │     │ (GLM 4.7)   │     │ (Haiku 4.5) │
│ Read-Only   │     │ Read-Only   │     │ Read-Only   │
│             │     │             │     │             │
│ Debugging   │     │   Docs,     │     │   Fast      │
│ Architecture│     │ OSS Search  │     │   Grep      │
└─────────────┘     └─────────────┘     └─────────────┘
         │                   │                   │
         └───────────────────┼───────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   Sisyphus-Junior (Executor)                            │
│           Выполняет делегированные задачи по категориям                │
│                                                                         │
│  delegate_task(category="visual-engineering", prompt="...")             │
│           → Uses Gemini 3 Pro Preview                                   │
│                                                                         │
│  delegate_task(category="ultrabrain", prompt="...")                     │
│           → Uses Claude Sonnet 4.5 (xhigh reasoning)                     │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     HOOK: PostToolUse                                   │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  comment-checker → warns about excessive comments               │    │
│  │  todo-continuation-enforcer → forces completion if incomplete   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     HOOK: Stop                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  ralph-loop → if task incomplete, inject follow-up prompt       │    │
│  │  session-recovery → handle errors, resume from last state       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Иерархия агентов

### Оригинальная иерархия Oh My OpenCode

| Уровень | Агент | Модель | Роль | Ограничения |
|---------|-------|--------|------|-------------|
| **Orchestrator** | Sisyphus | Opus 4.5 | Главный оркестратор, планирование, делегирование | Полный доступ |
| **Planning** | Prometheus | Opus 4.5 | Интервью + создание плана | План only |
| **Planning** | Metis | Opus 4.5 | Анализ плана, поиск скрытых требований | Консультация |
| **Planning** | Momus | Claude Sonnet 4.5 | Валидация плана | Review only |
| **Consultant** | Oracle | Claude Sonnet 4.5 | Архитектура, дебаг | Read-only |
| **Research** | Librarian | GLM 4.7 | Документация, OSS код | Read-only |
| **Research** | Explore | Haiku 4.5 | Быстрый grep по коду | Read-only |
| **Multimodal** | Multimodal-Looker | Gemini Flash | PDF, изображения | Read-only |
| **Executor** | Sisyphus-Junior | По категории | Выполнение задач | По категории |

### Fallback Chains (критично!)

Каждый агент имеет цепочку fallback моделей:

```
Sisyphus:
  Primary: claude-opus-4-6
  Fallback: gemini-3-pro → anthropic/claude-sonnet-4.5 (alternative fallback examples: kimi-k2.5, glm-4.7)

Oracle:
  Primary: anthropic/claude-sonnet-4.5
  (no fallback - critical for reasoning quality)

Explore:
  Primary: claude-haiku-4-5
  Fallback: gpt-5-mini → gpt-5-nano
```

---

## 3. Категории задач

### Система категорий (Task Categories)

Вместо того чтобы каждый раз выбирать модель, Sisyphus использует **категории** — преднастроенные профили для разных типов задач:

| Категория | Модель по умолчанию | Когда использовать |
|-----------|--------------------|--------------------|
| **visual-engineering** | Gemini 3 Pro | Frontend, UI/UX, стили, анимации |
| **ultrabrain** | Claude Sonnet 4.5 (xhigh) | Сложная логика, архитектурные решения |
| **artistry** | Gemini 3 Pro (max) | Креативные задачи, уникальные идеи |
| **quick** | Haiku 4.5 | Тривиальные правки, typo fixes |
| **unspecified-low** | Sonnet 4.5 | Стандартные задачи, низкая сложность |
| **unspecified-high** | Opus 4.5 (max) | Сложные задачи без специфики |
| **writing** | Gemini Flash | Документация, тексты |

### Пример использования

```javascript
// Делегирование по категории
delegate_task(
  category="visual-engineering", 
  prompt="Create a responsive dashboard component"
)
// → Автоматически использует Gemini 3 Pro с настройками для UI

delegate_task(
  category="ultrabrain", 
  prompt="Design the payment processing flow"
)
// → Автоматически использует Claude Sonnet 4.5 с xhigh reasoning
```

---

## 4. Hook System

### Event Types (4 точки перехвата)

| Event | Когда срабатывает | Что можно делать |
|-------|-------------------|------------------|
| **PreToolUse** | Перед вызовом инструмента | Блокировать, модифицировать input |
| **PostToolUse** | После вызова инструмента | Добавить warnings, модифицировать output |
| **UserPromptSubmit** | При отправке промпта | Блокировать, трансформировать, inject |
| **Stop** | Когда сессия простаивает | Inject follow-up prompts |

### Категории hooks

```
Context & Injection:
├── directory-agents-injector  — Auto-inject AGENTS.md
├── directory-readme-injector  — Auto-inject README.md
├── rules-injector             — Inject conditional rules
└── compaction-context-injector — Preserve context during compaction

Productivity & Control:
├── keyword-detector           — Detect "ultrawork", "search", "analyze"
├── think-mode                 — Auto-detect deep thinking needs
├── ralph-loop                 — Self-referential loop continuation
└── auto-slash-command         — Execute slash commands from prompts

Quality & Safety:
├── comment-checker            — Warn about excessive comments
├── thinking-block-validator   — Validate thinking blocks
└── edit-error-recovery        — Recover from edit failures

Recovery & Stability:
├── session-recovery           — Recover from session errors
├── anthropic-context-window-limit-recovery
├── background-compaction      — Auto-compact at token limits
└── todo-continuation-enforcer — Force task completion
```

### Key Insight: todo-continuation-enforcer

Это **ключевой паттерн Sisyphus** — агент не может "сдаться":

```
Agent returns incomplete → Hook intercepts → Injects:
"You MUST complete this task. Do not return until ALL items are done."
→ Agent forced back into execution
→ Repeat until truly complete OR max_retries reached → HITL
```

---

## 5. Background Execution

### Параллельные агенты

Oh My OpenCode поддерживает запуск агентов в фоне:

```javascript
// Запуск фонового агента
delegate_task(
  agent="explore", 
  background=true, 
  prompt="Find auth implementations"
)
// → Returns task_id immediately
// → Agent runs in background

// Продолжаем работу параллельно...

// Получаем результаты когда нужно
background_output(task_id="bg_abc123")
```

### Concurrency Limits

```json
{
  "background_task": {
    "defaultConcurrency": 5,
    "staleTimeoutMs": 180000,
    "providerConcurrency": {
      "anthropic": 3,
      "openai": 5,
      "google": 10
    },
    "modelConcurrency": {
      "anthropic/claude-opus-4-6": 2,
      "google/gemini-3-flash": 10
    }
  }
}
```

**Priority:** `modelConcurrency` > `providerConcurrency` > `defaultConcurrency`

---

## 6. Swarm (Multi-Agent Teams)

### Концепция

Swarm — это система для организации **команд агентов**, работающих над одной задачей:

```json
{
  "sisyphus": {
    "swarm": {
      "enabled": true,
      "storage_path": ".sisyphus/teams",
      "ui_mode": "tmux"
    }
  }
}
```

### UI Modes

| Mode | Описание |
|------|----------|
| `toast` | Notifications при completion |
| `tmux` | Live panes для каждого агента |
| `both` | Оба режима |

---

## 7. Режимы работы

### Mode 1: Ultrawork (Автоматический)

```
User: "ulw add authentication"
           ↓
keyword-detector hook: activates ultrawork
           ↓
Sisyphus automatically:
  1. Explores codebase
  2. Researches via sub-agents
  3. Implements following patterns
  4. Verifies with diagnostics
  5. Continues until complete
```

**Когда использовать:** Быстрые задачи, когда доверяете агенту.

### Mode 2: Prometheus (Плановый)

```
User: [Tab] → Enters Prometheus mode
           ↓
Prometheus interviews user
  - Clarifying questions
  - Researches codebase
  - Identifies hidden requirements
           ↓
Generates work plan (.sisyphus/plans/*.md)
  - Tasks with acceptance criteria
  - Guardrails
  - Dependencies
           ↓
User: /start-work
           ↓
Atlas orchestrator:
  - Distributes tasks to sub-agents
  - Verifies each independently
  - Tracks progress across sessions
  - Accumulates learnings
```

**Когда использовать:** Сложные многодневные проекты, критические изменения.

---

## 8. Рекомендуемые паттерны для MAS

### Паттерн 1: Task Categories для вашего MAS

```python
# src/core/task_categories.py

TASK_CATEGORIES = {
    "freelance-bid": {
        "model": "gemini-3-flash",
        "temperature": 0.7,
        "description": "Proposal writing, client communication"
    },
    "code-generation": {
        "model": "claude-opus-4-6",
        "temperature": 0.3,
        "thinking_budget": 16000,
        "description": "Complex code generation"
    },
    "code-review": {
        "model": "anthropic/claude-sonnet-4.5",
        "temperature": 0.1,
        "description": "Code review, debugging, quality checks"
    },
    "content-writing": {
        "model": "gemini-3-flash",
        "temperature": 0.8,
        "description": "Copywriting, documentation"
    },
    "quick-fix": {
        "model": "claude-haiku-4-5",
        "temperature": 0.2,
        "description": "Simple fixes, typos"
    },
    "design-ui": {
        "model": "gemini-3-pro",
        "temperature": 0.6,
        "description": "UI/UX design tasks"
    },
    "research": {
        "model": "claude-sonnet-4-5",
        "temperature": 0.5,
        "description": "Codebase exploration, docs lookup"
    }
}
```

### Паттерн 2: Hook System для LangGraph

```python
# src/core/hooks.py

class HookEvent(Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    AGENT_START = "agent_start"
    AGENT_STOP = "agent_stop"

class HookRegistry:
    def __init__(self):
        self.hooks: dict[HookEvent, list[Callable]] = defaultdict(list)
    
    def register(self, event: HookEvent, hook: Callable):
        self.hooks[event].append(hook)
    
    async def trigger(self, event: HookEvent, context: dict) -> dict:
        for hook in self.hooks[event]:
            context = await hook(context)
            if context.get("blocked"):
                break
        return context

# Пример hooks
async def todo_continuation_enforcer(context: dict) -> dict:
    """Sisyphus pattern: force completion."""
    if context.get("task_incomplete"):
        context["inject_message"] = (
            "Task incomplete. Continue working until ALL items are done."
        )
        context["force_continue"] = True
    return context

async def comment_checker(context: dict) -> dict:
    """Sisyphus pattern: prevent excessive comments."""
    if context.get("tool") == "write_file":
        code = context.get("output", "")
        if _has_excessive_comments(code):
            context["warning"] = "Excessive comments detected."
    return context
```

### Паттерн 3: Background Agent Executor

```python
# src/core/background_executor.py

class BackgroundExecutor:
    """Run agents in parallel (Sisyphus pattern)."""
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.concurrency_limits = {
            "anthropic": 3,
            "google": 10,
            "openai": 5
        }
    
    async def spawn(
        self,
        agent_name: str,
        prompt: str,
        category: str = None
    ) -> str:
        """Spawn background agent, return task_id."""
        task_id = f"bg_{uuid4().hex[:8]}"
        
        async def run():
            model = self._get_model_for_category(category)
            result = await self._execute_with_model(model, prompt)
            await self.redis.hset("bg_results", task_id, json.dumps(result))
        
        asyncio.create_task(run())
        return task_id
```

### Паттерн 4: Prometheus-style Planning

```python
# src/agents/planner.py (расширение)

class PrometheusPlanner:
    """Interview-based planning (Sisyphus pattern)."""
    
    async def interview_and_plan(self, task_description: str) -> WorkPlan:
        # Step 1: Interview
        questions = await self._generate_clarifying_questions(task_description)
        
        # Step 2: Wait for HITL answers
        answers = await self._wait_for_hitl_answers(questions)
        
        # Step 3: Pre-planning analysis (Metis pattern)
        hidden_requirements = await self._identify_hidden_requirements(
            task_description, answers
        )
        
        # Step 4: Generate plan
        plan = await self._generate_work_plan(
            task=task_description,
            answers=answers,
            hidden_requirements=hidden_requirements
        )
        
        # Step 5: Plan review (Momus pattern)
        validation = await self._validate_plan(plan)
        
        return plan
```

---

## Резюме для реализации

| Паттерн | Приоритет | Где реализовать |
|---------|-----------|-----------------|
| Task Categories | 🔴 High | `src/core/task_categories.py` |
| Hook System | 🔴 High | `src/core/hooks.py` |
| Todo Enforcer | 🔴 High | Hook: `agent_stop` |
| Comment Checker | 🟡 Medium | Hook: `post_tool_use` |
| Background Executor | 🟡 Medium | `src/core/background_executor.py` |
| Prometheus Planning | 🟡 Medium | `src/agents/planner.py` |
| Fallback Chains | 🟢 Low | `src/core/llm_client.py` |
| Swarm Teams | 🟢 Low | Future phase |
