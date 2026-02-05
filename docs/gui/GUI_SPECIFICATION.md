# 🎨 GUI Design Specification: MAS Dashboard

**Технологии:** Remix + shadcn/ui + Tailwind CSS + WebSocket

---

## 🏠 Структура интерфейса

| Раздел | Иконка | Описание | Badge |
|--------|--------|----------|-------|
| Dashboard | 📊 | KPI + Activity Feed + Pipeline | — |
| HITL Pending | ⏳ | Очередь одобрений | 🔴 Count |
| Agents | 🤖 | Статус агентов + логи | 🟢/🔴 |
| Projects | 📋 | Kanban (Freelance / Цифровизация) | Count |
| Outreach | 🗺️ | Geo Scout + Email кампании | — |
| Analytics | 📈 | Revenue, Win Rate, Costs | — |
| Settings | ⚙️ | Конфигурация | — |

---

## 📊 Dashboard

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   🔴 HITL    │  │  🟢 Agents   │  │  💼 Active   │  │  💰 Revenue  │
│   Pending: 5 │  │    9/10      │  │   Projects   │  │   $4,250     │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘

┌────────────────────────────────┐  ┌────────────────────────────────┐
│      AGENT ACTIVITY FEED       │  │       UPCOMING ACTIONS         │
│ 🔍 Scout Found 3 jobs    2m    │  │ ⏰ Deadline: Project #42       │
│ 💼 Bid   Proposal ready  5m    │  │ 📤 Delivery: Project #38       │
│ 💻 Dev   Code generated  12m   │  │ 🔔 HITL: 2 bids expiring       │
└────────────────────────────────┘  └────────────────────────────────┘
```

---

## ⏳ HITL Pending

**Фильтры:** [All] [Bids] [Reviews] [Deliveries]

**Карточка HITL:**
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🔴 URGENT                                                  Expires: 28 min │
│ 💼 BID APPROVAL — "React Dashboard for Analytics"                          │
│ Client: TechStartup Inc. | Budget: $2,500 | Deadline: 2 weeks              │
│                                                                             │
│ ┌─────────────────────────── Proposal ────────────────────────────────────┐ │
│ │ Hi! I've built 15+ similar projects. My approach:                       │ │
│ │ 1. Figma mockups (3 days) 2. Implementation (7 days) 3. Testing (4d)   │ │
│ └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│ [✅ Approve & Send]  [✏️ Edit Proposal]  [❌ Skip]  [⏸️ Later]             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Типы HITL:**
- **Bid Approval** (🔵) — Одобрение предложений
- **Code Review** (🟠) — Ревью кода от Critic
- **Delivery Review** (🟢) — Отправка заказа клиенту
- **Alert** (🔴) — Критические уведомления

---

## 🤖 Agents Monitoring

**System Health:** 🟢 All agents healthy | Last heartbeat: 5 sec ago

### Pipeline A: Freelance
| Agent | Status | Current Task | Last Activity |
|-------|--------|--------------|---------------|
| 🔍 Scout | 🟢 IDLE | — | 2m ago |
| 💼 Bid | 🟡 WORKING | Drafting proposal #4521 | now |
| 📋 Planner | 🟢 IDLE | — | 15m ago |
| 💻 Dev | 🟡 WORKING | Generating code #38 | now |
| 🔬 Critic | 🟡 WORKING | Reviewing Dev output | now |
| 📦 Packager | 🟢 IDLE | — | 30m ago |

### Pipeline B: Outreach
| Agent | Status | Current Task | Last Activity |
|-------|--------|--------------|---------------|
| 🗺️ Geo Scout | 🟢 IDLE | — | 1h ago |
| 📧 Outreach | 🟡 WORKING | Enriching batch #12 | now |

---

## 📋 Projects (Kanban)

### Табы: [Freelance] [Цифровизация]

### Freelance Tab
**Колонки:** Backlog → In Progress → Review → Done

### Цифровизация Tab
**Колонки:** Идеи → В очереди → В разработке → Развёрнуто

**Особенность:** Кнопка **"Добавить в очередь"** на карточках в Идеях

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  [Freelance]  [Цифровизация ✓]                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │    ИДЕИ      │  │  В ОЧЕРЕДИ   │  │ В РАЗРАБОТКЕ │  │  РАЗВЁРНУТО  │   │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├──────────────┤   │
│  │ Кафе Ромашка │  │ #1 Магазин   │  │ Онлайн-школа │  │ Агентство    │   │
│  │ 📍 Москва    │  │    игрушек   │  │ ████░░ 75%   │  │ ✓ 15.10.24   │   │
│  │[+ В очередь] │  │ #2 Ресторан  │  │              │  │              │   │
│  │              │  │              │  │ Клиника      │  │ Стартап      │   │
│  │ Автосервис   │  │ #3 Фитнес    │  │ ██░░░░ 40%   │  │ ✓ 20.10.24   │   │
│  │ 📍 СПб       │  │              │  │              │  │              │   │
│  │[+ В очередь] │  │              │  │              │  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🗺️ Geo Outreach

**Табы:** [🗺️ Geo Scan] [📊 Leads] [📧 Campaigns] [📈 Stats]

```
┌────────────────────────────────────┐  ┌──────────────────────────────┐
│         INTERACTIVE MAP            │  │      NEW GEO SCAN            │
│    ┌─────────────────────────┐    │  │  City: [Москва         ▼]   │
│    │    🔷 🔷 🔷 (H3 grid)    │    │  │  Radius: [10 km         ]   │
│    │ 🔷 🟢 🔷 🔷 🔷           │    │  │  [✓] Рестораны  [✓] Кафе   │
│    │    🔷 🔷 🟢              │    │  │  [ ] Салоны красоты         │
│    └─────────────────────────┘    │  │  [✓] Без сайта              │
│  Scanned: 847 | Leads: 234        │  │  [🚀 Start Scan]             │
└────────────────────────────────────┘  └──────────────────────────────┘
```

---

## 📈 Analytics

```
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│  REVENUE          │  │  WIN RATE         │  │  RESPONSE TIME    │
│  $12,450          │  │  24%              │  │  avg 2.3h         │
│  📈 +18% vs prev  │  │  📈 +5% vs prev   │  │  📉 -15% faster   │
└───────────────────┘  └───────────────────┘  └───────────────────┘

BIDS FUNNEL:
Jobs Scanned ████████████████████████████  486
Qualified    █████████████████             287 (59%)
Bids Sent    ████████████                  156 (54%)
Won          ███                            38 (25%)

COSTS: LLM $340 | E2B $120 | Proxies $80 | Total: $585/mo | ROI: 2,027%
```

---

## 📱 Telegram Bot (Mobile HITL)

```
┌─────────────────────────────────────────┐
│  🤖 MAS Bot                             │
├─────────────────────────────────────────┤
│  🔔 New HITL Request                    │
│  💼 BID APPROVAL                        │
│  Client: TechStartup Inc.               │
│  Budget: $2,500                         │
│                                         │
│  [✅ Approve] [❌ Skip] [⏸️ Later]       │
│  ⏰ Expires in 28 minutes               │
└─────────────────────────────────────────┘
```

---

## 🎨 Design System

### Colors (Dark Theme)
| Token | Hex | Usage |
|-------|-----|-------|
| `--background` | `#0F172A` | Main background |
| `--card` | `#1E293B` | Card backgrounds |
| `--primary` | `#3B82F6` | Buttons, links |
| `--success` | `#22C55E` | Approve, OK |
| `--warning` | `#F59E0B` | Working, attention |
| `--destructive` | `#EF4444` | Error, urgent |
| `--foreground` | `#F1F5F9` | Text |

### shadcn/ui Components
- `Card` — информационные блоки
- `Badge` — статусы (IDLE, WORKING, URGENT)
- `Button` — действия
- `Tabs` — переключатели разделов
- `Table` — списки агентов, лидов
- `Dialog` — модальные формы
- `Toast` — уведомления

---

## 🖼️ Mockup Images

Визуальные мокапы сохранены в: `docs/gui/mockups/`

1. `dashboard_main.png` — главный экран
2. `hitl_pending.png` — очередь одобрений
3. `agents_monitoring.png` — мониторинг агентов
4. `outreach_geo_map.png` — Geo Scout с картой
5. `projects_kanban.png` — Kanban Freelance
6. `projects_digitalization.png` — Kanban Цифровизация
7. `analytics_dashboard.png` — аналитика
8. `telegram_bot_mobile.png` — Telegram бот

---

## 🚀 Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Remix (Server-first) |
| UI | shadcn/ui + Tailwind CSS |
| Real-time | Socket.io |
| State | Zustand |
| Data Fetching | React Query |
| Charts | Recharts |
| Maps | React-Map-GL + Mapbox |
