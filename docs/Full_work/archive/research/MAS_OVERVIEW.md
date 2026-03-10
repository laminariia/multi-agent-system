# MAS — Multi-Agent Service

> Автономная мульти-агентная система: 10 ИИ-агентов, которые находят заказы, пишут код, общаются с клиентами и доставляют проекты. Ты только одобряешь ключевые решения.

---

## Что это?

MAS — система для фриланса "под ключ". Она делает всё, что обычно делаешь руками:

1. **Ищет заказы** на 5 платформах (Freelancer, FL.ru, Kwork, Upwork, Telegram-каналы)
2. **Пишет предложения** клиентам (анализирует заказ, генерит персонализированный bid)
3. **Планирует проект** (разбивает на задачи ≤4 часов)
4. **Пишет код** (в Docker-песочнице, сканирует на уязвимости Semgrep)
5. **Генерит контент** (тексты, README, документацию)
6. **Проверяет качество** (ревью кода, тестов, дизайна)
7. **Собирает deliverable** (архив + инструкция для клиента)
8. **Находит локальные бизнесы** без сайтов (рестораны, клиники, автосервисы)
9. **Пишет им персонализированные сообщения** (email + Telegram DM)
10. **Отправляет после твоего одобрения**

**Принцип: автономность + контроль.** Система работает сама, но НИКОГДА не отправит bid или сообщение клиенту без твоего "OK".

---

## Два пайплайна

### Pipeline A — Заказы с бирж

```
Scout → Bid → [ТЫ ОДОБРЯЕШЬ] → Planner → Dev → Content → Design → Critic → [ТЫ ОДОБРЯЕШЬ] → Packager
```

| Шаг | Кто | Что делает |
|-----|-----|-----------|
| Scout | ИИ-агент | Сканирует 5 платформ каждые 5 минут, оценивает заказы по score (0–1). >0.7 = квалифицирован, 0.5–0.7 = спросит тебя |
| Bid | ИИ-агент | Пишет предложение из 5 частей: intro, разбор требований, подход, timeline, цена. Использует RAG по прошлым выигранным бидам |
| **HITL** | **Ты** | Читаешь бид, жмёшь Approve / Edit / Reject в Dashboard или Telegram-боте |
| Planner | ИИ-агент | Декомпозирует проект на задачи ≤4ч. Если >20ч — спрашивает тебя |
| Dev | ИИ-агент | Пишет код (Python, JS, HTML/CSS). Запускает в Docker-песочнице. Semgrep сканирует на уязвимости |
| Content | ИИ-агент | Тексты, копирайтинг, README, UI-тексты |
| Design | ИИ-агент | Спеки дизайна (цвета, шрифты, layout, responsive) |
| Critic | ИИ-агент | Ревьюит всё. ≥0.85 → Packager, 0.60–0.84 → обратно к Dev (макс 3 раза), <0.60 → HITL |
| **HITL** | **Ты** | Финальный ревью перед отправкой клиенту |
| Packager | ИИ-агент | Собирает архив: код + тесты + README + инструкция |

### Pipeline B — Поиск клиентов

```
GeoScout → Outreach → [ТЫ ОДОБРЯЕШЬ] → Message Dispatch → END
```

| Шаг | Кто | Что делает |
|-----|-----|-----------|
| GeoScout | ИИ-агент | Сканирует город через H3-гексы + OpenStreetMap. Находит бизнесы без сайтов (рестораны, клиники, салоны) |
| Outreach | ИИ-агент | Обогащает контакты (OSINT → Hunter → Apollo), выбирает канал (Telegram / Email), генерит персонализированное сообщение |
| **HITL** | **Ты** | Видишь все сообщения, одобряешь пакетом или правишь каждое |
| Message Dispatch | Система | Отправляет email через SMTP и Telegram DM через Telethon (5 DM/час, 12 мин между сообщениями) |

---

## 10 ИИ-агентов

| # | Агент | LLM | Для чего |
|---|-------|-----|----------|
| 1 | **Scout** | DeepSeek V3.2 | Ищет заказы на 5 платформах, оценивает релевантность |
| 2 | **Bid** | DeepSeek V3.2 | Пишет предложения клиентам (proposal) |
| 3 | **Planner** | Claude Opus 4.6 | Разбивает проект на задачи |
| 4 | **Dev** | Claude Opus 4.6 | Пишет код в песочнице |
| 5 | **Content** | Claude Haiku 4.5 | Тексты, документация, копирайтинг |
| 6 | **Design** | NanoBanana Pro | Спеки дизайна (цвета, шрифты, layout) |
| 7 | **Critic** | Claude Sonnet 4.5 | Ревью кода и контента |
| 8 | **Packager** | Claude Haiku 4.5 | Сборка deliverable |
| 9 | **GeoScout** | DeepSeek V3.2 | Сканирует города, ищет бизнесы без сайтов |
| 10 | **Outreach** | DeepSeek V3.2 | Обогащает лиды, пишет cold messages |

Все LLM-вызовы идут через **OpenRouter** (один API-ключ на все модели).

---

## Dashboard (веб-интерфейс)

Remix + TypeScript + shadcn/ui. 15+ страниц:

| Страница | Что показывает |
|----------|---------------|
| **Dashboard** | Обзор системы: метрики, статус агентов, активность, графики (заказы по платформам, HITL по типам) |
| **Jobs** | Все найденные заказы с фильтрами (платформа, статус, score). Кнопка "Scan Now", CSV-экспорт |
| **Job Detail** | Детали заказа: биды, timeline, артефакты |
| **Agents** | Список агентов с heartbeat-статусом (dead/error/healthy). Кнопки restart/pause/resume |
| **Agent Detail** | Логи, история heartbeat, управление |
| **HITL Queue** | Очередь на одобрение: bid approval, code review, delivery. Bulk resolve |
| **Orchestrator** | Автономный runner: старт/стоп, цели, здоровье системы, milestones |
| **Leads** | Все лиды из Pipeline B с enrichment-статусом |
| **Lead Detail** | Профиль бизнеса: контакты, enrichment-данные, история outreach |
| **Geo Scanner** | Карта с H3-гексами, запуск сканирования по городу |
| **Outreach** | Email/Telegram кампании: черновики, одобрение, статистика отправки |
| **Telegram Channels** | Мониторинг каналов: добавить/удалить, backfill-статус |
| **Settings** | API-ключи (зашифрованы), платформенные аккаунты, тест credentials |
| **Users** | Управление пользователями: одобрение, роли, suspend |

**Реал-тайм**: WebSocket обновляет данные на лету (новый лог агента, resolved HITL, статус сканирования).

---

## Telegram-бот

Вместо Dashboard можно управлять через Telegram:

```
/orch         → панель управления оркестратором
/run          → запустить автономную сессию
/stop         → остановить сессию
/goals        → посмотреть цели
/add_goal     → добавить новую цель
/health       → здоровье системы
/pending      → очередь HITL (кнопки: Approve / Skip)
/scan Berlin  → запустить geo-scan города
/status       → статус агентов
/stats        → статистика за сегодня
```

HITL-карточки приходят прямо в чат с кнопками Approve / Reject.

---

## Технологии

| Слой | Технология |
|------|-----------|
| Оркестрация | LangGraph 1.0 (граф состояний, checkpoints) |
| Бэкенд | Litestar + Python 3.12 + asyncpg |
| База данных | PostgreSQL 16 + pgvector + DiskANN |
| Кэш | Valkey 8.1 (Redis-совместимый) + RediSearch |
| Фронтенд | Remix + TypeScript + shadcn/ui |
| LLM | OpenRouter → DeepSeek V3.2, Claude Opus/Sonnet, Gemini |
| Embeddings | OpenAI text-embedding-3-large (3072 dim) |
| Браузер | Playwright + Stealth (anti-detection) |
| Деплой | Railway (Docker, API + Dashboard как отдельные сервисы) |
| Мониторинг | Prometheus + Grafana + Sentry + LangSmith |

---

## Безопасность

- **Semgrep** сканирует весь сгенерированный код на уязвимости перед исполнением
- **Docker-песочница** — Dev Agent никогда не запускает код на хосте
- **Fernet-шифрование** — API-ключи хранятся зашифрованными в БД
- **JWT + RBAC** — роли (owner, co_owner, moderator, viewer)
- **Rate limiting** — 300 req/min глобально, 60/min на auth
- **Stealth-браузер** — без CDP, ротация proxy, человекоподобные задержки
- **HITL mandatory** — bid submission и delivery ВСЕГДА требуют одобрения человека
- **Fail-closed HITL** — неизвестный action → отклонение, а не одобрение

---

## Семантический кэш

Двухслойный кэш LLM-ответов:

1. **Valkey** (горячий) — RediSearch HNSW-индекс, быстрый поиск по cosine similarity
2. **PostgreSQL** (холодный) — pgvector + DiskANN, долгосрочное хранение

Если новый запрос похож на предыдущий (cosine >0.92), возвращается кэшированный ответ. TTL по типам: proposals = 24ч, code = 1ч, content = 12ч.

---

## Мониторинг

- **Heartbeat**: каждый агент пингует каждые 90 сек. Timeout >180с → авто-рестарт (макс 3). После 3 рестартов → HITL-алерт
- **Prometheus**: метрики (uptime агентов, количество заказов, размер HITL-очереди, стоимость LLM)
- **Grafana**: дашборд с графиками
- **Sentry**: трекинг ошибок
- **LangSmith**: трассировка LLM-вызовов

---

## База данных (18 таблиц)

Ключевые:

| Таблица | Для чего |
|---------|----------|
| `users` | Аккаунты с ролями |
| `jobs` | Заказы с бирж (платформа, score, статус) |
| `bids` | Предложения для заказов |
| `projects` | Принятые проекты (timeline, artifacts) |
| `tasks` | Подзадачи от Planner |
| `artifacts` | Сгенерированные файлы (код, тексты, дизайн) |
| `hitl_queue` | Очередь на одобрение человеком |
| `leads` | Бизнесы из Pipeline B (город, контакты, enrichment) |
| `email_campaigns` | Outreach-кампании |
| `campaign_leads` | Привязка лидов к кампаниям (канал: email/telegram) |
| `agent_logs` | Логи решений агентов |
| `agent_heartbeats` | Пинги живости |
| `telegram_channels` | Мониторимые TG-каналы |

---

# Статус фич

## ✅ Реализовано и работает

| Фича | Описание |
|------|----------|
| **Pipeline A** | Полный цикл: поиск заказов → bid → разработка → delivery |
| **Pipeline B** | Полный цикл: гео-скан → enrichment → outreach → отправка |
| **Multi-Channel Outreach** | Email + Telegram DM с автовыбором канала на лида |
| **Telegram DM Sender** | Telethon MTProto, rate limiting (5/час), FloodWait retry |
| **Outreach промпты** | Два промпта (email formal + telegram casual), тон "сосед, не продавец" |
| **10 агентов** | Scout, Bid, Planner, Dev, Content, Design, Critic, Packager, GeoScout, Outreach |
| **Dashboard** | 15+ страниц, WebSocket real-time, CRUD для всех сущностей |
| **Telegram-бот** | HITL-одобрение, управление оркестратором, запуск сканирования |
| **Stealth-браузер** | Playwright + anti-detection для парсинга платформ |
| **Семантический кэш** | Valkey + pgvector, cosine 0.92, экономия на LLM-вызовах |
| **Heartbeat мониторинг** | 90с пинг, авто-рестарт, HITL-алерт после 3 падений |
| **Semgrep security** | Сканирование сгенерированного кода на уязвимости |
| **Docker sandbox** | Изолированное выполнение кода от Dev Agent |
| **Railway deploy** | API + Dashboard как отдельные сервисы |
| **Portfolio описания** | 10 проектов с детальным RU-текстом (ресторан, клиника, e-commerce...) |
| **Portfolio dogfooding скрипт** | MAS генерит HTML/CSS визуалы для своего портфолио |
| **Screenshot автоматизация** | Playwright: desktop + mobile + device mockup (MacBook + iPhone frame) |
| **Fail-closed HITL** | Неизвестные actions отклоняются, а не одобряются |
| **2330 тестов** | 0 failed, ruff 0 errors |

## 📐 Спроектировано (дизайн готов, код НЕ написан)

| Фича | Документ | Суть |
|------|----------|------|
| **Portfolio Agent** (11-й) | `docs/plans/2026-02-25-portfolio-agent-design.md` | Автопубликация портфолио после Packager approve. Git-папка как source of truth. Адаптация под 6 платформ (Kwork, FL.ru, Fiverr, Freelancer, YouDo, Telegram) |
| **Design Workflow** | `docs/plans/2026-02-25-design-workflow.md` | Design Agent ДО Dev (не параллельно). 3-4 HITL-точки. Pencil.dev → Penpot → Figma |
| **Client Growth (Pipeline C)** | `docs/plans/2026-02-24-client-growth-upselling-vision.md` | Auto-мониторинг клиентских сайтов → Snapshot Report → Upsell → новый проект. 4 уровня: улучшения → интеграции → кастомный софт → полная цифровизация |
| **Telegram Dual Pipeline** | `docs/plans/2026-02-24-telegram-strategy.md` (секция 7) | Pipeline 1: freelance-каналы → заказы. Pipeline 2: business-каналы → профилирование пользователей → лиды → Outreach |
| **Touch Sequence Manager** | `docs/plans/2026-02-24-personalized-outreach-design.md` | Каскад касаний: день 1 (первое сообщение) → день 4 (follow-up) → день 8 (value add) → день 10 (last chance). Авто-стоп при ответе |

## 💡 Идеи (бэклог, НЕ для немедленной реализации)

| Идея | Описание |
|------|----------|
| **Pencil.dev MCP** | Интеграция Design Agent с Pencil.dev через MCP protocol. JSON-спека → визуальный макет + код. Оценка: 2-3 дня |
| **v0.dev API** | REST API для генерации React/HTML из текста ($20/мес). Альтернатива Pencil.dev с более простой интеграцией |
| **WhatsApp канал** | Business API + каскад: WhatsApp > Telegram > Email. Требует Business аккаунт |
| **LinkedIn outreach** | Для международных клиентов. Отдельный подход, другая стратегия |
| **Managed Services** | AI делает 80%+ работы, ты продаёшь как managed service. 300k руб/мес MRR при 20 клиентах |

---

## Как запустить

```bash
# 1. Инфраструктура
docker compose up -d          # PostgreSQL + Valkey

# 2. API
litestar --app src.api.main:app run --reload

# 3. Dashboard
cd dashboard && npm run dev

# 4. Telegram бот
python -m src.bot.main

# 5. Worker (фоновые задачи: Scout каждые 5 мин, Pipeline B каждые 24ч)
python -m src.worker.main
```

Все ключи — в `.env` (шаблон: `.env.example`).

---

## Структура проекта

```
src/
├── agents/          10 ИИ-агентов
├── api/             Litestar API (40+ endpoints)
├── bot/             Telegram-бот (HITL + управление)
├── browser/         Playwright Stealth (anti-detection)
├── core/            Ядро: граф, состояние, heartbeat, кэш, модели, LLM-клиент
├── enrichment/      Waterfall: OSINT → Hunter → Apollo + Email/TG sender
├── geo/             H3-гексы + Overpass API
├── knowledge/       RAG: ingestion + retrieval + embeddings
├── monitoring/      Prometheus + Sentry
├── orchestrator/    Автономный runner
├── prompts/         Системные промпты (bid, outreach email/telegram)
├── sandbox/         Docker + E2B песочницы
├── security/        Semgrep + шифрование
├── services/        Telegram listener
├── worker/          APScheduler + task queue
dashboard/           Remix + shadcn/ui (15+ страниц)
scripts/             Утилиты (portfolio, screenshots, backup)
tests/               2330 тестов (unit + integration)
docs/                Архитектура, спеки, планы
portfolio/           Описания проектов + шаблоны скриншотов
alembic/             Миграции БД
docker/              Docker Compose + monitoring
```
