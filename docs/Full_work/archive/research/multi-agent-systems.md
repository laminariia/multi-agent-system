# Multi-Agent Systems (MAS): Глубокий анализ существующих систем

**Дата:** 31 января 2026  
**Тип:** Технический анализ мультиагентных AI систем

---

## EXECUTIVE SUMMARY

**2026 год — официально "Год Мультиагентных Систем"** [149][150][151]

Мультиагентные AI системы (MAS) — это архитектурный паттерн, где **несколько автономных AI-агентов взаимодействуют друг с другом** для решения задач, которые слишком сложны для одного агента.

**Ключевая статистика (январь 2026):**
- **80% enterprise apps** ожидается будут иметь встроенных агентов к концу 2026 года [156]
- **46%+ CAGR** рост рынка agentic AI [156]
- **37-63% failure rate** в production без правильной архитектуры [125][168]

**Главная проблема:** Большинство MAS проваливаются из-за **координационных провалов**, а не технических ограничений LLM.

---

## ЧТО ТАКОЕ MULTI-AGENT SYSTEM (MAS)?

### Определение

**Multi-Agent System (MAS)** — это фреймворк из **нескольких интеллектуальных агентов**, которые:

1. **Автономны** — принимают решения без человека
2. **Специализированы** — каждый имеет свою роль (как отдел в компании)
3. **Взаимодействуют** — общаются через протоколы коммуникации
4. **Координируются** — работают к общей цели или конкурируют

### Аналогия с человеческой командой

```
Компания = Multi-Agent System
├── CEO (Orchestrator Agent) → координирует всех
├── Sales Team (Scout Agent) → ищет клиентов
├── Marketing (Bid Agent) → готовит предложения
├── Engineering (Dev Agent) → строит продукт
├── QA (Critic Agent) → проверяет качество
└── PM (Packager Agent) → отправляет результат
```

**Разница с человеческой командой:**
- ✅ Агенты работают 24/7
- ❌ Агенты не "понимают" контекст как люди
- ❌ Агенты не задают уточняющих вопросов (если не запрограммировано)
- ❌ Ошибки агентов накапливаются (compounding errors)

---

## КЛЮЧЕВЫЕ КОМПОНЕНТЫ MAS

### 1. Agents (Агенты)

**Определение:** Автономные сущности с четкой ролью.

**Характеристики хорошего агента:**
```python
agent = {
    "role": "Research Assistant",
    "goal": "Find 10 recent papers on topic X",
    "backstory": "Expert in academic research with PhD",
    "tools": ["Google Scholar API", "arXiv scraper"],
    "constraints": {
        "max_iterations": 5,
        "timeout": 300,  # seconds
        "token_limit": 4000
    }
}
```

### 2. Communication Protocols (Протоколы общения)

**Проблема:** Агенты должны "понимать" друг друга.

**Стандартные протоколы:**
- **Message Passing** — агент A отправляет JSON агенту B
- **Shared Memory** — все агенты пишут в общую базу (Redis, PostgreSQL)
- **Event-Driven** — агенты слушают события (Kafka, RabbitMQ)

**Пример коммуникации:**
```json
{
  "from": "scout_agent",
  "to": "bid_agent",
  "type": "job_found",
  "payload": {
    "job_id": "12345",
    "title": "Build React Dashboard",
    "budget": "$500",
    "skills_match": 0.92
  },
  "timestamp": "2026-01-31T03:00:00Z"
}
```

### 3. Coordination Mechanisms (Механизмы координации)

**Типы координации:**

**A. Sequential (Последовательная)** [163]
```
Agent A → Agent B → Agent C → Done
```
- ✅ Простая логика
- ❌ Медленная (каждый ждет предыдущего)

**B. Parallel (Параллельная)**
```
        ┌→ Agent B →┐
Agent A ┼→ Agent C →┼→ Merge → Done
        └→ Agent D →┘
```
- ✅ Быстрее
- ❌ Сложнее синхронизировать

**C. Hierarchical (Иерархическая)**
```
        Manager Agent
           ↓   ↓   ↓
        A   B   C
```
- ✅ Хорошо для масштаба
- ❌ Bottleneck в manager

**D. Group Chat (Групповой чат)** [157]
```
All agents в одном чате.
ChatManager решает, кто говорит следующим.
```
- ✅ Flexible
- ❌ Context window переполняется

### 4. State Management (Управление состоянием)

**Ключевой вопрос:** Где хранится "память" системы?

**Варианты:**
- **Stateless** (OpenAI Agents SDK) — нет памяти, каждый вызов независим
- **Stateful** (LangGraph) — граф сохраняет state между шагами
- **Distributed State** (Redis/PostgreSQL) — внешнее хранилище

---

## 7 ВЕДУЩИХ ФРЕЙМВОРКОВ MAS (ЯНВАРЬ 2026)

### 1. LangGraph (LangChain) ⭐ ВАШ ВЫБОР

**Источник:** [163][81][154]

**Философия:** Граф из узлов (agents) и ребер (transitions).

**Killer Features:**
- ✅ **Cyclic graphs** — агент может вернуться назад при ошибке
- ✅ **State persistence** — pause/resume через дни
- ✅ **Time travel** — rewind interaction для debugging
- ✅ **HITL checkpoints** — встроенная человеческая проверка

**Архитектура:**
```python
from langgraph.graph import StateGraph

workflow = StateGraph(state_schema)

workflow.add_node("scout", scout_agent)
workflow.add_node("bid", bid_agent)
workflow.add_node("human_approval", human_checkpoint)

# Conditional routing
workflow.add_conditional_edges(
    "bid",
    lambda state: "human_approval" if state["budget"] > 1000 else "dev"
)

workflow.add_edge("human_approval", "dev")
```

**Use Cases:**
- ✅ Code generation с iterative refinement (ваш Dev Agent)
- ✅ Long-running workflows (legal research, medical diagnosis)
- ✅ Complex branching logic

**Недостатки:**
- ❌ **Steep learning curve** (требует понимания графов)
- ❌ **Over-engineering** для простых задач (Scout → Bid = overkill)
- ❌ **Higher token costs** (stateful agents сохраняют больше контекста)

**Когда использовать:**
- Workflow с loops (Dev Agent генерирует код → тестирует → фиксит → повторяет)
- Production-critical системы (нужна observability)

---

### 2. CrewAI (CrewAI Inc)

**Источник:** [82][88][163]

**Философия:** Role-playing команда как в компании.

**Killer Features:**
- ✅ **Beginner-friendly** — самый простой syntax
- ✅ **Built-in memory** (short-term + long-term)
- ✅ **Task parallelization** — agents работают одновременно
- ✅ **High-level abstractions** — меньше boilerplate

**Архитектура:**
```python
from crewai import Crew, Agent, Task

researcher = Agent(
    role="Market Researcher",
    goal="Find 10 potential clients in logistics",
    backstory="20 years in B2B sales",
    tools=[google_search, linkedin_scraper]
)

writer = Agent(
    role="Copywriter",
    goal="Write cold email",
    backstory="Ex-McKinsey consultant"
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
    process="sequential"  # или "hierarchical"
)

result = crew.kickoff()
```

**Use Cases:**
- ✅ Content pipelines (research → write → edit)
- ✅ Linear workflows (A → B → C)
- ✅ Быстрое прототипирование MVP

**Недостатки:**
- ❌ **No cyclic workflows** — не может loop back при ошибке
- ❌ **Less flexible** чем LangGraph
- ❌ **Not production-grade** (больше для prototyping)

**Когда использовать:**
- Простые агенты (Scout, Bid, Content, Packager)
- MVP за 2-3 дня

---

### 3. Microsoft AutoGen → Agent Framework

**Источник:** [169][172][178][163]

**Важно:** AutoGen **больше не развивается**. Microsoft объединил его с Semantic Kernel в **Microsoft Agent Framework** (октябрь 2025).

**Философия:** Conversational agents (как чат между экспертами).

**Killer Features:**
- ✅ **GroupChat** — все агенты в одном чате
- ✅ **Code execution** — Docker integration для безопасности
- ✅ **Enterprise-ready** — error handling, logging, failover
- ✅ **Multimodal** — text, images, files

**Архитектура:**
```python
from autogen import ConversableAgent, GroupChat, GroupChatManager

assistant = ConversableAgent(
    "assistant",
    llm_config={"model": "gpt-4"}
)

user_proxy = ConversableAgent(
    "user_proxy",
    human_input_mode="ALWAYS",  # HITL
    code_execution_config={"use_docker": True}
)

group_chat = GroupChat(
    agents=[assistant, user_proxy, coder, reviewer],
    messages=[],
    max_round=10
)

manager = GroupChatManager(groupchat=group_chat)
```

**Use Cases:**
- ✅ Code generation + testing (Dev Agent use case)
- ✅ Creative problem-solving (brainstorming)
- ✅ Enterprise deployments (reliability критична)

**Недостатки:**
- ❌ **Complex setup** (больше конфигурации чем CrewAI)
- ❌ **Migration to Agent Framework** — API changes в 2025-2026

**Когда использовать:**
- Microsoft ecosystem (Azure, .NET)
- Code execution workflows

---

### 4. OpenAI Agents SDK (ex-Swarm)

**Источник:** [163]

**Философия:** Lightweight handoffs (минималистичный подход).

**Killer Features:**
- ✅ **Stateless** — нет сложного state management
- ✅ **Simple handoffs** — агент передает задачу другому
- ✅ **Easy debugging** — каждый interaction независим
- ✅ **Fast prototyping**

**Архитектура:**
```python
from openai import agents

scout = agents.Agent(
    name="Scout",
    instructions="Find jobs",
    tools=[search_upwork]
)

bid = agents.Agent(
    name="Bid",
    instructions="Write proposal",
    tools=[generate_proposal]
)

# Handoff
scout.add_handoff(bid, when="job found")
```

**Use Cases:**
- ✅ Простая координация (A → B → C)
- ✅ Прототипы (MVP за 1 день)

**Недостатки:**
- ❌ **No memory** — не подходит для long-running tasks
- ❌ **Limited state** — сложные workflows невозможны
- ❌ **No persistence** — нельзя pause/resume

**Когда использовать:**
- Proof of concept
- Начальная стадия (потом мигрировать на LangGraph)

---

### 5. LlamaIndex Workflows

**Источник:** [163]

**Философия:** Event-driven architecture (события и реакции).

**Killer Features:**
- ✅ **RAG-first** — глубокая интеграция с vector DBs
- ✅ **Event-driven** — loose coupling между агентами
- ✅ **Document processing** — встроенные инструменты

**Use Cases:**
- ✅ Knowledge management systems
- ✅ Research assistants (научные статьи, документы)
- ✅ Document Q&A с мультиагентами

**Недостатки:**
- ❌ **Specialized** — не подходит для general-purpose MAS
- ❌ **RAG-focused** — если вам не нужен RAG, это overkill

---

### 6. LangFlow (Visual Builder)

**Источник:** [163]

**Философия:** No-code visual builder (drag-and-drop).

**Killer Features:**
- ✅ **Visual interface** — non-technical люди могут строить
- ✅ **Drag-and-drop** — быстро экспериментировать
- ✅ **Export to code** — можно потом вытащить код

**Недостатки:**
- ❌ **Limited for complex logic** — сложные workflows плохо в GUI
- ❌ **Developer friction** — advanced developers предпочитают код

---

### 7. Microsoft Semantic Kernel

**Источник:** [163]

**Философия:** Enterprise .NET/Python framework с Azure integration.

**Killer Features:**
- ✅ **Azure native** — встроенная интеграция с Azure services
- ✅ **Security/Compliance** — enterprise-grade
- ✅ **.NET + Python** support

**Недостатки:**
- ❌ **Microsoft ecosystem lock-in**
- ❌ **Steeper learning curve**

---

## COMPARISON MATRIX

| Framework | Learning Curve | Best For | State Mgmt | HITL | Cost | Production |
|-----------|----------------|----------|------------|------|------|------------|
| **LangGraph** | ⭐⭐⭐⭐⭐ | Complex workflows | ✅✅✅ | ✅✅✅ | High | ✅ Ready |
| **CrewAI** | ⭐⭐ | Prototyping | ✅✅ | ✅✅ | Medium | ⚠️ Limited |
| **AutoGen** | ⭐⭐⭐⭐ | Code gen, Chat | ✅✅ | ✅✅ | Medium | ✅ Ready |
| **OpenAI SDK** | ⭐ | Simple handoffs | ❌ | ⚠️ | Low | ⚠️ MVP |
| **LlamaIndex** | ⭐⭐⭐ | RAG/Knowledge | ✅✅ | ✅ | Medium | ✅ Ready |
| **LangFlow** | ⭐ | No-code | ✅ | ✅ | Low | ⚠️ Limited |
| **Semantic Kernel** | ⭐⭐⭐⭐ | .NET/Azure | ✅✅ | ✅✅ | Medium | ✅ Ready |

---

## РЕАЛЬНЫЕ ПРИМЕРЫ MAS В PRODUCTION (2026)

### 1. Medical Decision Support [161]

**Use Case:** Virtual Tumor Board (онкология)

**Архитектура:**
```
Agent 1: Diagnostic Specialist → анализирует снимки (CT, MRI)
Agent 2: Vital Signs Monitor → следит за показателями пациента
Agent 3: Patient History → ищет похожие случаи в базе
Agent 4: Treatment Planner → предлагает план лечения
```

**Результат:** Синхронизация рекомендаций в real-time, помощь клиницистам.

### 2. Autonomous Traffic Management [153]

**Use Case:** Умные светофоры

**Архитектура:**
```
Каждая машина = Agent
- Принимает решения (acceleration, braking, lane change)
- Координируется с соседними авто
- Адаптируется к traffic flow
```

**Результат:** Снижение заторов на 30% в pilot cities.

### 3. Supply Chain Optimization [158]

**Use Case:** Логистика от поставщика до клиента

**Архитектура:**
```
Agent 1: Supplier → оптимизирует закупку
Agent 2: Manufacturer → планирует производство
Agent 3: Distributor → маршруты доставки
Agent 4: Retailer → управление складом
```

**Результат:** Снижение costs на 15-20%.

### 4. Autonomous Swarm Robotics [158]

**Use Case:** Спасательные операции

**Архитектура:**
```
Swarm из 50 роботов
- Каждый работает semi-independently
- Координируются для покрытия большой площади
- Share sensory data
```

**Результат:** Эффективное исследование сложных территорий (завалы, планеты).

---

## ПОЧЕМУ MAS ПРОВАЛИВАЮТСЯ: MAST TAXONOMY

### MAST (Multi-Agent System Failure Taxonomy)

**Источник:** UC Berkeley + institutions, 2025 [165][166][168][171]

**Статистика провалов:**
- **41.8%** — Specification & System Design Issues
- **36.9%** — Inter-Agent Misalignment
- **21.3%** — Task Verification & Termination

---

### CATEGORY 1: Specification & System Design Issues (41.8%)

#### 1.1 Role and Task Ambiguity [166][168]

**Проблема:** Агенты не понимают свою роль или "нарушают" её.

**Пример:**
```
System: "Agent A — subordinate, Agent B — manager"
Reality: Agent A принимает executive decisions без спроса у B
```

**Почему происходит:**
- Prompts слишком general ("You are a helpful assistant")
- Нет четких границ ответственности

**Решение:**
```python
scout_agent = Agent(
    role="Job Scout — ONLY search, DO NOT bid",
    constraints=[
        "Never submit proposals",
        "Never contact clients",
        "Only pass jobs to Bid Agent"
    ]
)
```

#### 1.2 Step Repetition [166][168]

**Проблема:** Системы застревают в loops, повторяя одно и то же.

**Пример:**
```
Agent: "I will search for jobs"
Agent: "I will search for jobs"  # Повтор
Agent: "I will search for jobs"  # Бесконечно
```

**Почему происходит:**
- Нет bounded iterations
- Agent теряет историю (context window overflow)

**Решение:**
```python
max_iterations = 5  # Hard limit
iteration_history = set()  # Track completed steps

if current_step in iteration_history:
    raise LoopDetected()
```

#### 1.3 Loss of History (Conversation Resets) [166][168]

**Проблема:** Агенты "забывают" предыдущий контекст.

**Пример:**
```
Turn 1: Agent finds API credentials
Turn 2: Agent says "I don't have credentials" — FORGOT!
```

**Почему происходит:**
- Context window full (4K, 8K tokens)
- No memory persistence

**Решение:**
- Semantic Cache (ваш проект уже имеет)
- External memory (Redis, PostgreSQL)
- Summarization агенты

---

### CATEGORY 2: Inter-Agent Misalignment (36.9%)

#### 2.1 Communication Breakdown [166][168]

**Проблема:** Агенты не задают уточняющих вопросов, делают false assumptions.

**Пример:**
```
Agent A: "Budget is tight"
Agent B: Assumes "tight" = $100 (на самом деле $500)
```

**Почему происходит:**
- Humans ask clarifying questions, LLMs — no
- Ambiguous natural language

**Решение:**
- Structured message formats (JSON schema validation)
- Verification agents ("Did you mean X or Y?")

#### 2.2 Information Withholding [166][168]

**Проблема:** Агент находит критическую информацию, но не делится.

**Пример:**
```
Agent A: Finds correct API key
Agent B: Пытается использовать старый key → fails
(Agent A не передал новый key)
```

**Решение:**
- Shared state (все агенты пишут в одну базу)
- Explicit handoff protocols

#### 2.3 Reasoning-Action Mismatch [166][171]

**Проблема:** Reasoning агента не соответствует его действию.

**Пример:**
```
Agent reasoning: "This job requires Python, I know Django"
Agent action: Submits proposal saying "I'm a React expert"
```

**Решение:**
- Chain-of-Thought prompting
- Critic Agent проверяет consistency

---

### CATEGORY 3: Task Verification & Termination (21.3%)

#### 3.1 Superficial Verification [166][168]

**Проблема:** Verifier проверяет low-level (syntax), но не high-level (logic).

**Пример:**
```
Critic Agent: "Code compiles ✅"
Reality: Code has logical bug (wrong calculation)
```

**Решение:**
- Multi-level verification (syntax → logic → business rules)
- Test-driven agents (write tests first)

#### 3.2 Premature Termination [166][168][171]

**Проблема:** Система останавливается до завершения задачи.

**Пример:**
```
Goal: "Write 5 blog posts"
Agent: Writes 3 posts → stops → "Task complete"
```

**Решение:**
- Explicit success criteria в промпте
- Verification agent counts outputs

#### 3.3 No Termination (Infinite Loops) [166][171]

**Проблема:** Система не понимает, что задача выполнена.

**Пример:**
```
Goal: "Find 10 jobs"
Agent: Finds 15 jobs → continues searching → 20, 30, 40...
```

**Решение:**
```python
if len(jobs_found) >= goal:
    return TERMINATE
```

---

## ДОПОЛНИТЕЛЬНЫЕ ПРОБЛЕМЫ MAS

### Compounding Errors (Ошибки накапливаются) [166][168]

**Проблема:** Маленькая ошибка в Agent A становится фактом для Agent B.

**Пример:**
```
Research Agent: "Company revenue is $10M" (hallucination, на самом деле $1M)
Execution Agent: Builds pitch на основе $10M → клиент отказывает
```

**Решение:**
- Cross-verification (2+ agents проверяют факты)
- HITL для критических данных

### Context Degradation [166][168]

**Проблема:** Чем больше агентов, тем больше "шума" в context window.

**Пример:**
```
Context window (8K tokens):
- Agent A message: 500 tokens
- Agent B message: 600 tokens
- Agent C message: 700 tokens
...
- Agent H: Context full → теряет начало разговора
```

**Решение:**
- Hierarchical memory (краткосрочная + долгосрочная)
- Summarization после каждых N turns

### Autonomy vs. Reliability Trade-off [166][168]

**Цитата:**
> "Many teams chase full autonomy too early. Research indicates that simpler, well-tuned single-agent prompts or human-in-the-loop workflows often outperform complex multi-agent architectures."

**Вывод:** HITL-first approach (ваш проект) — правильное решение.

---

## ЛУЧШИЕ ПРАКТИКИ MAS (2026)

### 1. Communication Protocols [166][168]

✅ **Standardized APIs**
```python
# Плохо
agent_a.send("budget is $500")

# Хорошо
agent_a.send({
    "type": "budget_update",
    "amount": 500,
    "currency": "USD",
    "confidence": 0.95
})
```

✅ **Shared Ontology** — все агенты понимают одинаково
```python
ONTOLOGY = {
    "customer_churn": "User inactive 90+ days",
    "high_priority": "Response needed < 24 hours"
}
```

✅ **Event-Driven Communication** (не polling)
```python
# Плохо (polling)
while True:
    jobs = check_new_jobs()  # Каждую секунду

# Хорошо (event-driven)
@event_listener("new_job")
def handle_new_job(job):
    bid_agent.process(job)
```

### 2. Aligned Objectives [166][168]

✅ **Global Reward Function**
```python
# Плохо (локальные цели конфликтуют)
scout_goal = "Max jobs found"  # → Находит 1000 low-quality
bid_goal = "Max proposals sent"  # → Спам

# Хорошо (глобальная цель)
system_goal = "Max high-quality projects won"
scout_reward = 0.3 * quality_score
bid_reward = 0.7 * quality_score
```

✅ **Central Coordinator** (арбитр)
```python
if scout_agent.disagrees_with(bid_agent):
    decision = coordinator_agent.arbitrate()
```

### 3. Scalability [166][168][176]

✅ **Modular Design**
```python
# Каждый agent = независимый модуль
agents/
├── scout/
│   ├── graph.py
│   └── tools.py
├── bid/
│   ├── graph.py
│   └── tools.py
```

✅ **Hierarchical Architecture**
```python
Manager Agent
├── Pipeline A Manager
│   ├── Scout
│   └── Bid
└── Pipeline B Manager
    ├── Enrichment
    └── Outreach
```

✅ **Observability**
- Логи каждого agent interaction
- Distributed tracing (Jaeger, OpenTelemetry)
- Metrics (latency, token usage, error rate)

### 4. Ethical AI [166][168]

✅ **Bias Auditing** — проверяйте training data
✅ **HITL** — человек для критических решений
✅ **Explainability** — system должна объяснить свое решение

---

## КОГДА ИСПОЛЬЗОВАТЬ MAS VS SINGLE AGENT

### ✅ Используйте MAS когда:

1. **Задача слишком сложная** для одного агента
   - Пример: Medical diagnosis (нужно 5+ экспертиз)

2. **Нужна специализация**
   - Scout expert в поиске, Bid expert в копирайтинге

3. **Параллелизация** дает выигрыш
   - 5 agents ищут jobs одновременно = быстрее

4. **Разделение ответственности** (audit trail)
   - Кто принял решение? → Agent X

### ❌ НЕ используйте MAS когда:

1. **Задача линейная и простая**
   - "Напиши email" → overkill для MAS

2. **Latency критична**
   - MAS = больше handoffs = slower

3. **Budget ограничен**
   - MAS = больше LLM calls = дороже

4. **Команда маленькая**
   - Debugging MAS требует времени

**Цитата из Reddit:** [152]
> "Single agents are less complex, easier to debug, more reliable. The hype around multi-agent is mostly from people selling courses."

---

## ВАШ ПРОЕКТ: РЕКОМЕНДАЦИИ

### Какие агенты должны быть MAS?

**✅ Используйте MAS для:**

1. **Dev Agent + Critic Agent** (сложный loop)
   ```
   Dev → Code → Critic → Feedback → Dev (repeat)
   ```
   - LangGraph идеален (cyclic graph)

2. **Pipeline B (H3 Enrichment)**
   ```
   H3 → Enrichment (parallel 5 APIs) → Merge
   ```
   - CrewAI или LangGraph

**⚠️ Возможно single agent:**

3. **Scout Agent** — просто поиск jobs
4. **Bid Agent** — generate proposal (без сложной логики)
5. **Packager Agent** — zip files

**Почему single agent проще:**
- Меньше coordination overhead
- Faster response
- Easier debugging

### Hybrid подход (рекомендую)

```python
# Simple agents = single
scout = SingleAgent("Scout", model="gemini-flash")
bid = SingleAgent("Bid", model="claude-sonnet")

# Complex agents = multi-agent
dev_critic_system = LangGraphMAS(
    agents=[dev_agent, critic_agent],
    graph=cyclic_refinement_graph
)
```

---

## ФИНАЛЬНЫЕ ВЫВОДЫ

### ✅ Плюсы MAS

1. **Specialization** — каждый агент expert в своей области
2. **Scalability** — можно добавить агентов без переписывания
3. **Fault tolerance** — если один agent падает, другие работают
4. **Parallel processing** — быстрее для некоторых задач
5. **Audit trail** — видно, кто что сделал

### ❌ Минусы MAS

1. **Coordination overhead** — агенты тратят время на общение
2. **Compounding errors** — ошибки накапливаются
3. **Context degradation** — context window переполняется
4. **Debugging nightmare** — найти root cause сложнее
5. **Higher costs** — больше LLM calls
6. **Longer latency** — больше handoffs

### 🎯 Правило большого пальца

**Если задача решается одним агентом за <30 секунд → используйте single agent.**

**Если нужна координация 3+ экспертиз или loops → используйте MAS.**

---

## РЕКОМЕНДАЦИИ ДЛЯ ВАШЕГО ПРОЕКТА

### Phase 1: MVP (Month 1-2)

**Стратегия:** Start simple, scale up.

```python
# Simple single agents
scout = SingleAgent(model="gemini-flash")
bid = SingleAgent(model="claude-sonnet")
packager = SingleAgent(model="gemini-flash")

# Orchestrator координирует их
orchestrator = SimpleOrchestrator([scout, bid, packager])
```

**Почему:**
- Быстрее build
- Easier debug
- Дешевле test

### Phase 2: Optimize (Month 3-4)

**Стратегия:** Add MAS где bottleneck.

```python
# Dev + Critic = MAS (iterative refinement нужен)
dev_critic = LangGraphMAS([dev, critic])

# Pipeline B Enrichment = MAS (5 APIs parallel)
enrichment = CrewAI([apollo, hunter, clearbit, linkedin, google])
```

### Phase 3: Scale (Month 5-6)

**Стратегия:** Full production MAS с monitoring.

```python
# Observability
from opentelemetry import trace
from prometheus_client import Counter

agent_calls = Counter("agent_calls_total", "Agent invocations", ["agent_name"])

@trace_span("scout_agent")
def scout():
    agent_calls.labels(agent_name="scout").inc()
    # ...
```

---

**Итоговый совет:** MAS — мощный инструмент, но **не silver bullet**. Используйте где нужна координация, но не over-engineer. Ваш HITL-first подход — правильное решение, которое защитит от большинства MAS провалов.

---

**Автор:** Deep Research AI Agent  
**Дата:** 31 января 2026  
**Источники:** 90+ источников (фреймворки, failure analysis, production examples)  
**Confidence Level:** 95%
