# Функциональный и логический анализ MAS — 2026-03-03

---

## 1. Pipeline A: Путь заказа от обнаружения до сдачи

### Поток данных

```
Scout (5 платформ, каждые 5 мин)
  → LLM-скоринг батчами по 10
  → score ≥0.7 → Bid (автоматически)
  → score 0.5-0.7 → HITL (пользователь решает)
  → score <0.5 → отсеяно

Bid (RAG + LLM)
  → генерирует предложение (5 частей: hook, credibility, solution, timeline, CTA)
  → HITL: approve / edit / skip / later  ← ОБЯЗАТЕЛЬНЫЙ

[при approve] → Planner (Claude Opus)
  → декомпозиция на задачи ≤4ч
  → если >20ч → HITL plan review

→ Dev → Content → Design → Critic
  → score ≥0.85 → Packager
  → score 0.60-0.84 minor → обратно к Dev (макс 3 раза)
  → score 0.60-0.84 major → обратно к Planner
  → scope_creep или 3+ ревизий → HITL эскалация

Packager → HITL: approve / request_changes / reject  ← ОБЯЗАТЕЛЬНЫЙ
```

### Платформы Scout

| Платформа | Интеграция | Приоритет |
|-----------|-----------|-----------|
| Freelancer.com | REST API | Primary |
| FL.ru | RSS feed | Primary |
| Kwork | Playwright scraper (stealth + proxy) | Secondary |
| Upwork | GraphQL мониторинг (read-only) | Optional |
| Telegram | Valkey queue (из listener) | Дополнительный |

### Фильтры Scout

**Берём:** $20-$5000, web dev, landing pages, HTML/CSS, React/Next.js, WordPress, Shopify, Webflow. Чёткие требования, активный клиент (последние 7 дней).

**Отсеиваем:** mobile apps, AI/ML, blockchain, enterprise ERP, native iOS/Android, game dev. Клиенты с <3 отзывов, <50% hire rate, история споров. Конкурсы без гарантии.

### Скоринг Bid

- Стратегия: **-10-15% от рынка** для первых проектов (reputation building)
- После 10+ положительных отзывов → по рыночной цене
- RAG: 3 похожих выигранных бида через KnowledgeRetriever (vector similarity)
- Размеры: Micro ($20-100), Small ($100-500), Medium ($500-2000), Large ($2000-5000)
- Структура: Hook → Credibility → Solution → Timeline → CTA
- Язык = язык заказа (русский для FL.ru/Kwork)

### HITL-точки Pipeline A

| # | Точка | Когда | Обязательна |
|---|-------|-------|-------------|
| 1 | Job review | score 0.5-0.7 | Условно |
| 2 | **Bid approval** | Всегда | **ДА** |
| 3 | Plan review | >20ч или major revision | Условно |
| 4 | Critic escalation | score <0.60, scope_creep, 3+ ревизий | Условно |
| 5 | **Final review** | Всегда | **ДА** |

### Что проверяет Critic

- **Автоматически:** Semgrep scan (fail-closed). CRITICAL = -0.3, WARNING = -0.1
- **LLM (Claude Sonnet 4.5):** код (тесты, безопасность, структура), контент (грамматика, SEO), дизайн (responsive, dark/light)
- **Пороги:** ≥0.85 → Packager, 0.60-0.84 → revision, <0.60 → HITL

### Safety

| Слой | Механизм |
|------|----------|
| Код | Semgrep scan (fail-closed) |
| Выполнение | Docker sandbox |
| Бид | requires_hitl=True (invariant) |
| Доставка | requires_hitl=True (invariant) |
| Heartbeat | 90 сек пинг, 180 сек timeout → auto-restart (макс 3) |
| Loop | LoopDetector для runaway prevention |

---

## 2. Pipeline B: Холодный outreach

### Поток данных

```
GeoScout (Overpass/2GIS/Google Places)
  → H3-гексы по городу
  → фильтр: нет сайта / устарел / не мобильный
  → ниши: стоматологии, автосервисы, рестораны, салоны...

→ Enrichment Waterfall:
  Tier 1 (FREE): 2GIS, Instagram, web scraping
  Tier 2 ($0.01): Hunter.io (средние лиды)
  Tier 3 ($0.05): Apollo.io (только premium ниши)

→ Outreach Agent
  → _select_channel: TG username → Telegram, иначе → Email
  → тон "сосед, не продавец", уникальное сообщение, 2-3 предложения
  → HITL: approve / edit / reject  ← ОБЯЗАТЕЛЬНЫЙ

→ Email/Telegram отправка
  → Email: 50/день, 30-60 сек между письмами, unsubscribe header
  → Telegram: 5 DM/час, 12 мин между DM, FloodWait handling
```

### Целевые ниши

- **HIGH VALUE** (стоят Apollo enrichment): стоматологии, юрфирмы, медклиники, риэлторы, автосервисы
- **MEDIUM VALUE**: рестораны/кафе, салоны красоты, фитнес, локальный ритейл

### Email warm-up (6 недель)

| Неделя | Лимит | Описание |
|--------|-------|----------|
| 1-2 | 5-10/день | Внутренняя warm-up сеть |
| 3 | 20/день | Наращивание |
| 4 | 30/день | Наращивание |
| 5-6 | 50/день | Production ready |

**Зачем:** без warm-up deliverability ~10-20%, с warm-up — 80-95%.

**Реализация:** rate limit 50/день статичный, progressive schedule НЕ автоматизирован.

### Telegram интеграция

- **Listener:** Telethon MTProto, 22 канала (8 freelance, 10 business, 2 niche), refresh из БД каждые 5 мин
- **DM Sender:** 5 DM/час, 12 мин между DM, FloodWait handling
- **Статус:** session string готов, end-to-end НЕ тестирован, backfill и dual pipeline НЕ реализованы

---

## 3. Стратегическое видение

### Три пайплайна

| Pipeline | Суть | Статус |
|----------|------|--------|
| **A** (Freelance) | Реактивный — ищет заказы на биржах | Полностью реализован |
| **B** (Outreach) | Проактивный — сам находит бизнесы | MVP (email+TG), ответы НЕ обработаны |
| **C** (Growth) | Удержание — мониторинг + upsell | Только видение, 0 кода |

### Связь пайплайнов

- **A** и **B** используют одну команду агентов (Planner, Dev, Content, Design, Critic, Packager)
- Общая HITL-инфраструктура, семантический кэш, LLM, heartbeat
- **Telegram как мост:** freelance-каналы → Pipeline A (заказы), business-каналы → Pipeline B (лиды)
- **C** получает клиентов из обоих: Auto-Monitor → Growth Report → Upsell → [HITL] → новый проект

### Бизнес-модель

- **Одноразовые проекты (A+B):** 30-80k руб ($300-800), AI делает 80%+
- **Managed Services (C, будущее):** 25-50k руб/мес на клиента
- **Target:** 20 клиентов = ~1M руб + 300k руб/мес MRR (50% конверсия)
- **Маржа:** 60-90% (vs 30-50% у обычных агентств)
- **Юрформа:** самозанятый → ИП → ООО

### Портфолио — холодный старт

- 10 описаний проектов в `docs/portfolio/`
- Верстка для скриншотов (НЕ деплой)
- -20-30% от рынка на первые 5 проектов ради отзывов
- Цель: 15+ отзывов за 3 мес, 40+ за 6 мес, рейтинг 4.8+
- Portfolio Agent (11-й) — дизайн готов, код НЕ написан

### Масштабирование

- Фаза 2: WhatsApp Business API ($0.05/msg, 98% open rate)
- Фаза 3: Touch Sequence Manager (День 1 → День 3 → День 5 → Стоп)
- Фаза 4: LinkedIn + международный рынок
- Throughput: 5-10 проектов/нед → 15-25 → 30-50

---

## 4. Dashboard и пользовательский опыт

### Страницы (15+)

| Страница | Назначение |
|----------|-----------|
| Dashboard | Метрики, статус агентов, графики |
| Jobs | Заказы, фильтры, Scan Now, CSV-экспорт |
| Job Detail | Биды, timeline, артефакты |
| Agents | Heartbeat, restart/pause/resume |
| HITL Queue | Очередь одобрения, bulk resolve |
| Orchestrator | Старт/стоп, milestones |
| Leads | Лиды Pipeline B, enrichment |
| Lead Detail | Профиль, контакты, история |
| Geo Scanner | Карта с H3-гексами |
| Outreach | Кампании, черновики, статистика |
| Telegram Channels | Мониторинг, CRUD |
| Settings | API-ключи (зашифрованы), тест |
| Users | Роли, suspend |

### Типичный день оператора

**Утро:** Dashboard → HITL Queue (одобрить биды/доставки) → Jobs (новые заказы)

**Днём:** Telegram бот (быстрые approve/reject) → Outreach (ревью сообщений)

**Периодически:** Orchestrator → Geo Scanner → Settings

### Telegram бот

```
/orch       — панель оркестратора
/run        — запустить автономную сессию
/stop       — остановить
/goals      — цели
/health     — здоровье системы
/pending    — HITL очередь (inline-кнопки)
/scan Berlin — geo-scan
/status     — статус агентов
/stats      — статистика за сегодня
```

---

## 5. Логические пробелы и риски

### Pipeline A

| Пробел | Описание | Критичность |
|--------|----------|-------------|
| Scout → пусто | Pipeline тихо завершается, нет уведомления | Medium |
| Все биды отклонены | Заказы навсегда `qualified`, нет retry | Medium |
| HITL timeout | 24ч только для бидов, для final_review — ∞ | High |
| Revision loop | Content+Design пересоздаются при code-only fix | Medium |
| Capacity check | MAX_CONCURRENT=5 описан, не enforced | High |
| Многодневная работа | Серия HITL-пауз, нет daily progress | Low |
| Заказ удалён | Бид для несуществующего заказа | Medium |

### Pipeline B

| Пробел | Описание | Критичность |
|--------|----------|-------------|
| **Нет обработки ответов** | Pipeline заканчивается отправкой | **CRITICAL** |
| Bounce monitoring | Нет автостопа при >2% bounce rate | High |
| Email footer | Нет company name + address (GDPR) | High |
| Progressive warm-up | Лимит статичный, не нарастает | Medium |
| TG escalation | FloodWait >300с = стоп не в коде | Medium |
| Cross-pipeline dedup | Один бизнес — два сообщения | Medium |
| Data retention | Удаление лидов через 90/365 дней не автоматизировано | Medium |

### Системные

| Риск | Описание | Критичность |
|------|----------|-------------|
| 152-ФЗ | Данные РФ на Railway (не в России) | High |
| LLM cost | Нет budget cap на pipeline run | Medium |
| Нет e2e теста | Scout→...→Packager не тестирован end-to-end | High |
| TG backup | Бан аккаунта = потеря всех кампаний | Medium |
| Design после Dev | Двойная работа, workflow не перестроен | Medium |

---

## 6. Нереализованные фичи (задокументированы)

| Фича | Документ | Статус |
|------|----------|--------|
| Обработка ответов на outreach | — | Не описана, не реализована |
| Business Analyzer | personalized-outreach-design.md | Описан, не реализован |
| Lead Scorer | personalized-outreach-design.md | Описан, не реализован |
| Touch Sequence Manager | personalized-outreach-design.md | Описан, не реализован |
| WhatsApp канал | personalized-outreach-design.md | Описан, не реализован |
| Portfolio Agent (11-й) | portfolio-agent-design.md | Описан, не реализован |
| Pipeline C (Growth) | client-growth-upselling-vision.md | Видение, не реализовано |
| Telegram Dual Pipeline | telegram-strategy.md §7 | Описан, не реализован |
| Telegram Backfill | telegram-strategy.md | Описан, не реализован |
| Design перед Dev | design-workflow.md | Описан, не реализован |
| Progressive warm-up | agent_specifications_support.md | Описан, частично |
| Data retention cron | legal_compliance.md | Описан, не реализован |
| Negotiation tables | negotiation_flows.md | Описаны, не реализованы |
| TG профилирование | telegram-strategy.md | Описан, не реализован |
| Дедупликация TG-постов | telegram-strategy.md | Описана, не реализована |
| Scout Extended Coverage | scout-extended-coverage.md | Описан, не реализован |
| Self-Improvement Agent | self-improvement-agent.md | Описан, не реализован |

---

## 7. Open Questions

- [ ] HITL timeout для final_review — намеренно отсутствует или пропуск?
- [ ] Revision loop при code-only — оптимизировать маршрут Critic → Dev → Critic?
- [ ] Целевая максимальная стоимость LLM за pipeline run?
- [ ] Fiverr — какой статус и как включить в Scout?
- [ ] Capacity check — enforced или допустимо сверх лимита?
- [ ] Заказ удалён с платформы между Scout и Bid — проверять?
- [ ] Как оператор узнаёт что бизнес ответил на outreach?
- [ ] Bounce rate threshold для автостопа кампании?
- [ ] Railway + 152-ФЗ — как решать?
- [ ] Backup TG-аккаунт нужен?
- [ ] Capacity между Pipeline A и Pipeline B — как распределять?
- [ ] "Ответ" на outreach — любое сообщение или только позитивное?
- [ ] Когда запускать Telegram warm-up?
