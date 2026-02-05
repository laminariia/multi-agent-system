# 🔧 Technical Implementation Guide: Multi-Agent System

**Цель документа:** Объяснить КАК технически работает система и КАК её реализовать.

---

## ⚠️ ВАЖНОЕ УТОЧНЕНИЕ: Claude Code ≠ Runtime

> **Claude Code** — это инструмент для РАЗРАБОТКИ кода, НЕ для его запуска 24/7.

```
┌─────────────────────────────────────────────────────────────────┐
│                    ЧТО ТАКОЕ CLAUDE CODE                        │
├─────────────────────────────────────────────────────────────────┤
│  Claude Code (Max x20) = IDE + ИИ-ассистент                    │
│                                                                 │
│  ✅ Помогает ПИСАТЬ код                                        │
│  ✅ Отлаживает ошибки                                          │
│  ✅ Генерирует файлы проекта                                   │
│  ✅ Объясняет архитектуру                                      │
│                                                                 │
│  ❌ НЕ запускает ваш сервис 24/7                               │
│  ❌ НЕ является сервером                                       │
│  ❌ НЕ хостит ваше приложение                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Правильная схема:

```mermaid
flowchart LR
    subgraph Development["🖥️ РАЗРАБОТКА"]
        CC[Claude Code]
        You[Вы]
    end
    
    subgraph Production["☁️ ПРОДАКШН 24/7"]
        Server[VPS/Cloud Server]
        MAS[Multi-Agent System]
        DB[(PostgreSQL)]
        Redis[(Valkey)]
    end
    
    You --> CC
    CC -->|пишет код| Code[Python код]
    Code -->|деплой| Server
    Server --> MAS
    MAS --> DB
    MAS --> Valkey
```

**Вывод:** Claude Code поможет вам НАПИСАТЬ всю систему, но запускать её нужно на отдельном сервере (VPS, AWS, DigitalOcean и т.д.).

---

## 🏗️ Техническая Архитектура

### Как работает система на сервере:

```mermaid
flowchart TB
    subgraph Server["☁️ VPS Server (Ubuntu)"]
        subgraph Docker["🐳 Docker Compose"]
            API[Litestar Server<br/>Port 8000]
            Worker1[LangGraph Worker 1]
            Worker2[LangGraph Worker 2]
            Scheduler[APScheduler<br/>Cron Jobs]
        end
        
        subgraph Storage["💾 Data Layer"]
            PG[(PostgreSQL<br/>State + Jobs)]
            RD[(Valkey<br/>Queues + Cache)]
        end
        
        subgraph External["🌐 External APIs"]
            LLM[Claude/Gemini API]
            E2B[E2B Sandbox]
            Platforms[Freelancer/Upwork]
        end
    end
    
    API --> Worker1
    API --> Worker2
    Worker1 --> PG
    Worker2 --> PG
    Worker1 --> RD
    Worker2 --> RD
    Worker1 --> LLM
    Worker2 --> LLM
    Worker1 --> E2B
    Scheduler --> RD
    
    User[👤 Вы через Dashboard] -->|WebSocket| API
```

### Ключевые компоненты:

| Компонент | Технология | Зачем нужен |
|-----------|------------|-------------|
| **API Server** | Litestar + Uvicorn | Принимает запросы, WebSocket для UI |
| **Workers** | LangGraph Python | Выполняют логику агентов |
| **Scheduler** | APScheduler | Запускает Scout каждые 5 минут |
| **Database** | PostgreSQL | Хранит состояния, историю, задачи |
| **Cache** | Redis | Очереди задач, кэш, Pub/Sub |
| **LLM API** | Claude/Gemini | "Мозг" агентов |
| **Sandbox** | E2B | Безопасное выполнение кода |

---

## 📊 Диаграммы взаимодействия агентов

### Pipeline A: Фриланс (полный цикл)

```mermaid
sequenceDiagram
    autonumber
    participant Scheduler as ⏰ Scheduler
    participant Scout as 🔍 Scout Agent
    participant Redis as 📮 Redis Queue
    participant Bid as 💼 Bid Agent
    participant Human as 👤 HITL (Вы)
    participant Planner as 📋 Planner Agent
    participant Dev as 💻 Dev Agent
    participant Content as ✍️ Content Agent
    participant Critic as 🔬 Critic Agent
    participant Packager as 📦 Packager Agent
    
    Scheduler->>Scout: Trigger every 5 min
    Scout->>Scout: Fetch RSS/API
    Scout->>Scout: Filter by criteria
    Scout->>Redis: Push qualified jobs
    
    Redis->>Bid: Pop job from queue
    Bid->>Bid: Analyze client history
    Bid->>Bid: Generate proposal draft
    Bid->>Human: 🛑 HITL: Approve proposal?
    
    alt Approved
        Human->>Bid: ✅ Approved
        Bid->>Bid: Submit via Playwright
        
        Note over Planner: After client accepts
        Bid->>Planner: Project requirements
        Planner->>Planner: Decompose into tasks
        
        par Parallel Execution
            Planner->>Dev: Backend tasks
            Planner->>Dev: Frontend tasks
            Planner->>Content: Copy tasks
        end
        
        Dev->>Critic: Code for review
        Critic->>Critic: Semgrep scan + Quality check
        
        alt Quality OK
            Critic->>Packager: Approved artifacts
        else Needs revision
            Critic->>Dev: Feedback + retry
        end
        
        Packager->>Packager: Bundle deliverables
        Packager->>Human: 🛑 HITL: Final review
        Human->>Packager: ✅ Ship it
        Packager->>Bid: Deliver to client
    else Rejected
        Human->>Bid: ❌ Skip this job
    end
```

### Pipeline B: Cold Outreach

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 Вы
    participant GeoScout as 🗺️ Geo Scout
    participant H3 as 🔷 H3 Index
    participant Overpass as 🌍 Overpass API
    participant Outreach as 📧 Outreach Agent
    participant Waterfall as 💧 Enrichment Waterfall
    participant Human as 👤 HITL
    participant Email as ✉️ Smartlead
    
    User->>GeoScout: Scan: "Москва, рестораны"
    GeoScout->>H3: Get hexagons for area
    H3->>GeoScout: 847 hexagons
    
    loop For each hexagon
        GeoScout->>Overpass: Query businesses
        Overpass->>GeoScout: Businesses without websites
    end
    
    GeoScout->>Outreach: 234 leads found
    
    loop For each lead
        Outreach->>Waterfall: Enrich contact
        
        alt Tier 1: OSINT (Free)
            Waterfall->>Waterfall: Google + Social Media
        else Tier 2: Hunter ($0.01)
            Waterfall->>Waterfall: Hunter.io lookup
        else Tier 3: Apollo ($0.05)
            Waterfall->>Waterfall: Apollo.io search
        end
        
        Waterfall->>Outreach: email found
        Outreach->>Outreach: Generate personalized email
    end
    
    Outreach->>Human: 🛑 HITL: Review campaign?
    Human->>Outreach: ✅ Send
    Outreach->>Email: Schedule campaign
```

### LangGraph State Machine

```mermaid
stateDiagram-v2
    [*] --> Idle
    
    Idle --> ScoutActive: New scan trigger
    ScoutActive --> JobFound: Qualified job
    ScoutActive --> Idle: No jobs
    
    JobFound --> BidDraft: Generate proposal
    BidDraft --> AwaitingHITL: Need approval
    
    AwaitingHITL --> BidSubmitted: Human approved
    AwaitingHITL --> Idle: Human rejected
    
    BidSubmitted --> Planning: Client accepted
    BidSubmitted --> Idle: Client rejected
    
    Planning --> Executing: Tasks assigned
    
    state Executing {
        [*] --> DevWorking
        [*] --> ContentWorking
        DevWorking --> CriticReview
        ContentWorking --> CriticReview
        CriticReview --> DevWorking: Revise
        CriticReview --> Done: Approved
    }
    
    Executing --> Packaging: All tasks done
    Packaging --> FinalReview: Bundle ready
    FinalReview --> Delivered: Human approved
    FinalReview --> Executing: Human requested changes
    Delivered --> [*]
```

---

## 🧠 Как "обучить" агентов?

> **Важно:** Агенты НЕ обучаются как нейросети. Они КОНФИГУРИРУЮТСЯ через промпты, инструменты и базы знаний.

### 3 способа специализации агентов:

```mermaid
flowchart TB
    subgraph Specialization["Специализация агента"]
        Agent[🤖 Agent]
        
        subgraph Method1["1️⃣ System Prompt"]
            SP[Роль + Правила + Ограничения]
        end
        
        subgraph Method2["2️⃣ Tools"]
            T1[API клиенты]
            T2[Браузер автоматизация]
            T3[Песочница E2B]
        end
        
        subgraph Method3["3️⃣ RAG Knowledge"]
            KB[База знаний]
            Examples[Примеры работ]
            Templates[Шаблоны]
        end
        
        SP --> Agent
        T1 --> Agent
        T2 --> Agent
        T3 --> Agent
        KB --> Agent
        Examples --> Agent
        Templates --> Agent
    end
```

### Пример: System Prompt для Bid Agent

```python
BID_AGENT_PROMPT = """
# Роль
Ты — опытный фриланс-менеджер с 10-летним стажем на Upwork.
Твоя задача: писать убедительные предложения, которые выигрывают проекты.

# Правила
1. ВСЕГДА начинай с персонализации (упомяни что-то из описания клиента)
2. НИКОГДА не используй шаблонные фразы типа "Dear Sir/Madam"
3. Ограничение: максимум 300 слов на предложение
4. Обязательно включай: конкретный план работ + сроки + кейс из портфолио

# Ограничения
- НЕ обещай нереальные сроки
- НЕ занижай цену ниже $500 для веб-проектов
- НЕ соглашайся на работу без ТЗ

# Формат ответа
Верни JSON:
{
  "proposal_text": "...",
  "bid_amount": 1500,
  "estimated_days": 7,
  "relevant_case": "portfolio_item_id"
}
"""
```

### Пример: RAG Knowledge Base

```python
# knowledge/bid_agent/successful_proposals.json
{
  "proposals": [
    {
      "job_type": "landing_page",
      "client_industry": "restaurant",
      "winning_proposal": "Привет! Увидел, что вам нужен лендинг для ресторана...",
      "bid_amount": 800,
      "win_rate": 0.45
    },
    {
      "job_type": "ecommerce",
      "client_industry": "fashion",
      "winning_proposal": "Заметил, что вы ищете Shopify-разработчика...",
      "bid_amount": 2500,
      "win_rate": 0.32
    }
  ]
}
```

### Пример: Tools для Dev Agent

```python
from langchain.tools import tool

@tool
def execute_code_in_sandbox(code: str, language: str) -> dict:
    """
    Выполняет код в изолированной E2B песочнице.
    Используй для тестирования сгенерированного кода.
    
    Args:
        code: Код для выполнения
        language: python или javascript
    
    Returns:
        {"stdout": "...", "stderr": "...", "exit_code": 0}
    """
    from e2b_code_interpreter import Sandbox
    
    with Sandbox() as sandbox:
        result = sandbox.run_code(code)
        return {
            "stdout": result.logs.stdout,
            "stderr": result.logs.stderr,
            "exit_code": 0 if not result.error else 1
        }

@tool  
def search_stackoverflow(query: str) -> list:
    """
    Ищет решения на StackOverflow.
    Используй когда сталкиваешься с ошибками.
    """
    # Implementation...
    pass

DEV_AGENT_TOOLS = [execute_code_in_sandbox, search_stackoverflow]
```

---

## 🚀 Как реализовать: Пошаговый план

### Этап 1: Разработка (с Claude Code)

```bash
# Claude Code помогает создать:
multi-agent-service/
├── src/
│   ├── agents/           # Логика агентов
│   ├── core/             # LangGraph, state
│   ├── prompts/          # System prompts
│   └── api/              # FastAPI endpoints
├── knowledge/            # RAG базы знаний
├── docker-compose.yml    # Контейнеризация
└── requirements.txt
```

### Этап 2: Локальное тестирование

```bash
# Запуск локально для отладки
docker-compose up -d postgres redis
python -m src.api.main  # FastAPI на localhost:8000
```

### Этап 3: Деплой на сервер

```mermaid
flowchart LR
    subgraph Local["💻 Локально"]
        Code[Код проекта]
        Git[Git репозиторий]
    end
    
    subgraph CI["⚙️ GitHub Actions"]
        Build[Build Docker Image]
        Push[Push to Registry]
    end
    
    subgraph Server["☁️ VPS"]
        Pull[Pull Image]
        Run[Docker Compose Up]
        Live[🟢 Система работает 24/7]
    end
    
    Code --> Git
    Git --> Build
    Build --> Push
    Push --> Pull
    Pull --> Run
    Run --> Live
```

### Минимальные требования к серверу:

| Ресурс | Минимум | Рекомендуется |
|--------|---------|---------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| SSD | 40 GB | 80 GB |
| OS | Ubuntu 22.04 | Ubuntu 22.04 |
| Цена | ~$20/мес | ~$40/мес |

**Рекомендуемые провайдеры:**
- Hetzner (Европа) — дёшево и надёжно
- DigitalOcean — простой UI
- AWS Lightsail — если нужна экосистема AWS

---

## 📝 Что делать в Claude Code?

### Шаг за шагом:

1. **Создать структуру проекта**
   ```
   Попросить: "Создай структуру папок для multi-agent-service"
   ```

2. **Написать базовые агенты**
   ```
   Попросить: "Напиши Scout Agent с LangGraph"
   ```

3. **Добавить промпты**
   ```
   Попросить: "Создай system prompt для Bid Agent"
   ```

4. **Настроить Docker**
   ```
   Попросить: "Создай docker-compose.yml с Postgres и Redis"
   ```

5. **Написать API**
   ```
   Попросить: "Создай FastAPI endpoints для HITL"
   ```

6. **Тестирование**
   ```
   Попросить: "Запусти тесты для Scout Agent"
   ```

---

## 🔄 Как это работает 24/7?

```mermaid
flowchart TB
    subgraph Always["🔁 Работает постоянно"]
        Cron[APScheduler<br/>каждые 5 мин] -->|trigger| Scout
        Scout -->|новые заказы| Queue[(Redis Queue)]
        Worker[LangGraph Worker] -->|берёт из очереди| Queue
    end
    
    subgraph OnDemand["📲 По требованию"]
        You[Вы] -->|открываете dashboard| API
        API -->|WebSocket| Dashboard[React Dashboard]
        Dashboard -->|показывает| Status[Статус агентов<br/>Pending задачи<br/>HITL запросы]
    end
    
    subgraph HITL["🛑 Требует вашего участия"]
        Worker -->|proposal ready| Notification[Telegram/Email]
        Notification --> You
        You -->|approve/reject| API
        API --> Worker
    end
```

### Cron-расписание (APScheduler):

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# Scout проверяет новые заказы каждые 5 минут
@scheduler.scheduled_job('interval', minutes=5)
async def run_scout():
    await scout_agent.scan_platforms()

# Geo Scout сканирует новый район каждый час
@scheduler.scheduled_job('interval', hours=1)
async def run_geo_scout():
    await geo_scout_agent.scan_next_region()

# Heartbeat check каждые 30 секунд
@scheduler.scheduled_job('interval', seconds=30)
async def check_heartbeats():
    await heartbeat_monitor.check_all_agents()

scheduler.start()
```

---

## ✅ Резюме

| Вопрос | Ответ |
|--------|-------|
| Claude Code запускает систему 24/7? | ❌ Нет, Claude Code только ПИШЕТ код |
| Где будет работать система? | ☁️ На VPS сервере (Hetzner, DO, AWS) |
| Как "обучить" агентов? | 📝 System prompts + 🔧 Tools + 📚 RAG |
| Что делает Claude Code? | 💻 Помогает написать весь код проекта |
| Сколько стоит сервер? | $20-40/мес за VPS |

---

**Следующий шаг:** Хотите, чтобы я начал создавать код проекта прямо сейчас?
