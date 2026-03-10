# Pipeline B Specification — Source of Truth

> Единая спека. Основана на: View 1 (код), View 2 (видение пользователя), View 3 (дизайн-документы).
> Дата: 2026-03-06

---

## Полная схема потока

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     PIPELINE B: ПОЛНЫЙ FLOW                              │
└──────────────────────────────────────────────────────────────────────────┘

ФАЗА 0: Выбор режима (Dashboard)
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  GeoScanner   │  │  Web Search   │  │   Telegram    │
│  (по городу)  │  │  (весь РФ)    │  │  (из каналов) │
└───────┬───────┘  └───────┬───────┘  └───────┬───────┘
        │                  │                   │
        ▼                  ▼                   ▼
ФАЗА 1: Обнаружение лидов
┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐
│  GeoScout Agent │  │  WebScout Agent │  │ TelegramProfileAggr. │
│  Nominatim→H3   │  │  multi-source   │  │  ≥3 msgs → LLM score │
│  Overpass query │  │  2GIS/VK/IG/... │  │  score ≥ 0.6 → Lead  │
└────────┬────────┘  └────────┬────────┘  └──────────┬───────────┘
         └──────────────────┬─┘                       │
                            ▼                         ▼
                    leads(status='new') ←─────────────┘

ФАЗА 2: Анализ бизнеса (Business Analyzer)
         │
         ▼
┌────────────────────────────────────────────────────┐
│  Быстрый (5с):  есть/нет сайт, соцсети, рейтинг   │
│  Средний (30с): Lighthouse, соцсети, базовый SEO   │
│  Глубокий (2-3м): технологии, трафик, конкуренты  │
└─────────────────────────┬──────────────────────────┘
                          ▼
ФАЗА 3: Lead Scorer → температура (hot/warm/cold)

ФАЗА 4: HITL #1 — Оператор видит богатую карточку лида
┌─────────────────────────────────────────────────────┐
│  Описание бизнеса + контакты + AI-анализ (что      │
│  предложить: лендинг / SEO / маркетплейс / другое)  │
│  Конкуренты в районе                                │
│  [Написать сам] [Отправить Менеджеру] [Пропустить]  │
└────────────────────┬────────────────────────────────┘
           approve → SalesAgent
           skip    → lead.status='skipped'
           manual  → оператор пишет сам → Touch Sequence

ФАЗА 5: Продажи (SalesAgent — новый агент)
         │
         ▼
   Первый контакт → Discovery (боли, потребности)
         │
         ▼
   Анализ конкурентов + рынка
         │
         ▼
   Генерация концепции
         │
         ▼
   [HITL #2] Оператор ревью концепции
   approve → согласование с клиентом
   edit    → SalesAgent корректирует
         │
         ▼
   Многоходовые переговоры с клиентом (дни/недели)
         │
         ▼
   [HITL #3] Концепция утверждена клиентом
         │
         ▼

ФАЗА 6: Дизайн (→ design-workflow.md)
   Planner → Design Agent (Pencil.dev / Penpot)
         │
         ▼
   [HITL #4] Оператор ревью макета (макс. 3 раунда)
         │
         ▼
   [HITL #5] Клиент утверждает дизайн
         │
         ▼

ФАЗА 7: Разработка (→ Pipeline A flow)
   Dev Agent → Content Agent → Critic → [HITL #6: доставка] → Packager
         │
         ▼
   deal.status='completed'

ФАЗА 8: Touch Sequence (если нет ответа на фазах 4-5)
   День 1 → День 3 → День 5 → День 10: STOP
   Одно "нет" или "не интересно" = STOP FOREVER
```

---

## Фаза 0: Выбор режима

Dashboard-страница Pipeline B. Три режима — пользователь выбирает перед запуском.

| Режим | Описание | Статус |
|-------|----------|--------|
| GeoScanner | Сканирование по городу/региону | ✅ реализован (базово) |
| Web Search | Поиск по всей РФ через все источники | ❌ не реализован |
| Telegram | Профилирование пользователей из каналов | ❌ не реализован |

---

## Фаза 1: Обнаружение лидов

### 1A: GeoScanner (реализован, расширить)

**Текущий flow:**
```
Ввод города (Dashboard) → Nominatim geocode → BoundingBox
  → H3 hexagons (res=8, max 50 гексов)
  → Overpass query за hex → filter offline_businesses
  → dedup by osm_id → leads(status='new')
```

**Расширение (Vision):**
- Выбор категорий в Dashboard (checkboxes: автомобили / медицина / рестораны / ...)
- Яндекс.Карты API: рейтинг, фото, количество отзывов, соцсети
- Перекрёстная проверка: нет сайта в картах → Google search по названию
- Анализ соцсетей: VK/Instagram аккаунт? Когда последний пост?
- Ограничения: 50 гексов, 1 req/s Overpass, rate limit Яндекс API

**Данные в `leads`:** osm_id, name, address, category, phone, instagram_url, vk_url, has_website, google_rating, review_count, city, source='geo_scanner'

### 1B: Web Search (не реализован — P1)

**Источники (все разрешённые):**
```
2GIS API          — поиск по категории, без geo-ограничения, вся РФ
Яндекс.Бизнес    — Search API, фильтр по "нет сайта" или "сайт не указан"
VK Business       — поиск по группам-организациям, давно не постили
Instagram         — Business аккаунты без ссылки на сайт (scraping / API)
Google Search API — запросы: "[категория] [город] без сайта"
Отраслевые        — справочники, 2GIS расширенный
```

**Реализация:** `WebScoutAgent` (расширение GeoScoutAgent с `mode=web_search`).
Те же паттерны фильтрации (нет сайта / плохой сайт / мёртвые соцсети).

**Ограничения:** rate limits, anti-bot (нужен Playwright stealth для некоторых), Google Search API платный.

### 1C: Telegram Mining (дизайн — P2)

```
TelegramListener → 14 бизнес/нишевых каналов → парсинг сообщений
  → Valkey queue (LIST telegram_msgs_business)
  → TelegramProfileAggregator (cron каждые 5 мин):
    - RPOP батч из Valkey
    - группировка по user_id
    - ≥3 сообщений → LLM анализ профиля (боли, потребности, ниша)
    - score ≥ 0.6 → Lead(source='telegram', source_id=user_id)
    - username → контакт для TG DM
```

**Новые таблицы:** `telegram_user_profiles` (user_id, messages_json, score, analyzed_at, status)

**Backfill:** при первом подключении канала — iter_messages(30 дней), batch 100, delay 2s, max 3000/канал.

---

## Фаза 2: Business Analyzer (дизайн — P1)

Три тира — адаптивно, на основе первичного скана:

| Тир | Время | Что делает | Когда запускать |
|-----|-------|------------|-----------------|
| Быстрый | 5с | Есть/нет сайт, рейтинг, соцсети | Все лиды |
| Средний | 30с | + Lighthouse score, соцсети (дата поста), базовый SEO | Lead Scorer ≥ 3 |
| Глубокий | 2-3м | + SimilarWeb трафик, технологии (Wappalyzer), конкуренты в районе | Lead Scorer ≥ 5 |

**Инструменты:**
- `check_website_status(url)` → exists, ssl, speed_score, last_updated
- `run_lighthouse(url)` → performance, accessibility, seo
- `check_social_activity(url)` → platform, followers, last_post_days
- `get_competitors_nearby(lat, lon, category)` → list[{name, has_website}]
- `estimate_traffic(domain)` → SimilarWeb approx visit count

---

## OSINT-стратегия и Battlecards

### Вектор 2: Глобальный веб-поиск (Google Dorks + SEO-API)

Режим `Web Search` работает вне географических рамок — ищет клиентов по технологическим и маркетинговым маркерам неэффективности.

**Google Dorks для поиска уязвимых сайтов:**

Система автоматизирует продвинутые поисковые операторы:
- `intext:"powered by" [старая CMS]` / `inurl:wp-content/themes/[старая тема]` → бизнесы с устаревшими платформами, нуждающимися в редизайне или миграции.
- `-inurl:https [коммерческая ниша]` → сайты без SSL-сертификатов — критическая ошибка, напрямую влияющая на конверсию.
- `site:[домен] inurl:wp-admin` + `"Index of"` → открытые директории и уязвимые конфигурации.

**SEO-API интеграция:**

WebScoutAgent подключается к промышленным SEO-интерфейсам для поиска бизнесов с падающим трафиком:

| API | Что даёт |
|-----|----------|
| SE Ranking | Мониторинг позиций, исторический трафик |
| DataForSEO | Массовый парсинг доменов, ключевые слова |
| Semrush | Органический трафик, конкурентный анализ |

Агент выявляет домены с **стагнацией или падением органического трафика** за последние кварталы → формирует структурированную проблему: *"Компания тратит бюджет на рекламу, но имеет показатель отказов 85% из-за долгой загрузки сайта"* → отправляет в HITL обогащённую карточку.

**E2B Sandbox для OSINT-скрапинга:**

Любой код скрапинга нестандартных сайтов, который генерирует агент, **обязательно** выполняется внутри E2B или локального Docker-контейнера — без доступа к сети backend-сервера. Это защищает инфраструктуру при парсинге сложных сайтов или соцсетей.

---

### AI Battlecards (динамические боевые карты)

Для успешных переговоров SalesAgent использует доказательную базу — автоматически генерируемые Battlecards на основе данных из Business Analyzer и OSINT.

| Компонент Battlecard | Источник данных | Применение в переговорах |
|---------------------|-----------------|--------------------------|
| **Feature Matrix** (сравнение фичей) | BuiltWith, парсинг сайтов конкурентов | Парирование возражений "и так всё работает" — агент указывает на онлайн-запись или личные кабинеты у конкурентов |
| **Market Share** (аналитика трафика) | SimilarWeb, Semrush API | Демонстрация упущенной выгоды — какую долю локального рынка забирают цифровизированные конкуренты |
| **Слабые места конкурентов** | Сентимент-анализ отзывов Google/Яндекс/2GIS | Фокусировка на уникальных преимуществах, которые конкуренты не предоставляют (UI/UX, скорость, запись) |
| **Алгоритмы снятия возражений** | RAG-база успешных скриптов | Мгновенная реакция на вопросы цены, сроков или целесообразности инвестиций |

**Пример использования Battlecard SalesAgent'ом:**

*"Анализируя инфраструктуру мебельного ритейла в вашем регионе, мы обратили внимание, что ваш сайт загружается 8 секунд — это потеря до 60% мобильного трафика. У ваших конкурентов [Название] уже есть онлайн-каталог с фильтрами, и они получают в 2 раза больше обращений."*

**Генерация и хранение:**
- Battlecard формируется на этапе Business Analyzer (средний/глубокий скан).
- Сохраняется в `leads.battlecard_json` (JSONB).
- SalesAgent извлекает через `get_deal_context()` при подготовке к переговорам.

---

## Фаза 3: Lead Scorer

Балльная система → температура → глубина анализа:

| Критерий | Очки |
|----------|------|
| Нет сайта вообще | +3 |
| Сайт есть, нет мобильной версии | +2 |
| Много отзывов + высокий рейтинг (бизнес живой) | +2 |
| Мёртвые соцсети (>3 месяцев без поста) | +1 |
| Высокочековая категория (клиника, ресторан, авто) | +1 |
| Мало отзывов (<10) | -1 |
| Хороший сайт (Lighthouse > 80) | -2 |

| Score | Температура | Глубина анализа | Действие |
|-------|-------------|-----------------|----------|
| ≥ 5 | Горячий | Глубокий (2-3 мин) | HITL — приоритет |
| 3-4 | Тёплый | Средний (30 сек) | HITL — обычный |
| < 3 | Холодный | Только быстрый | Skip или generic касание |

---

## Фаза 4: HITL #1 — Lead Card

**Момент:** после Business Analyzer + Lead Scorer.
**Обязательный:** да (для всех горячих и тёплых лидов).

**Что видит оператор:**

```
┌──────────────────────────────────────────────────────────────┐
│ 🏪 [Название бизнеса], [Город]                              │
│ Категория: [авто / медицина / ресторан / ...]               │
│ Рейтинг: X.X ★ (N отзывов) | Сайт: нет / устарел           │
│ Instagram: мёртвый (N мес) | VK: нет аккаунта              │
│                                                              │
│ 📞 [телефон]  💬 [TG username]  📧 [email если найден]      │
│                                                              │
│ 🤖 Что предложить (AI-анализ):                             │
│ ✅ [Рекомендация 1 — приоритет]                            │
│ ✅ [Рекомендация 2]                                         │
│ ❓ [Рекомендация 3 — требует уточнения]                    │
│                                                              │
│ 📊 Конкуренты: N бизнесов в районе, M из них с сайтами     │
│                                                              │
│ [✍️ Написать сам]  [🚀 Менеджеру]  [❌ Пропустить]         │
└──────────────────────────────────────────────────────────────┘
```

**Действия:**
- `approve (→ Менеджеру)` — SalesAgent берёт лид, начинает продажу
- `manual` — оператор пишет сам (→ Touch Sequence без SalesAgent)
- `skip` — `lead.status='skipped'`, не беспокоить

**Рекомендации "что предложить"** (LLM + правила):
- Лендинг с онлайн-записью / бронированием
- Редизайн / ускорение сайта
- SEO (Яндекс + Google)
- Маркетплейс / интернет-магазин
- Реанимация соцсетей
- Другое (LLM предлагает на основе ниши)

**После approve → SalesAgent:**
- В карточке появляется live chat (WebSocket): переписка SalesAgent ↔ клиент в реальном времени
- Оператор видит и может вмешаться
- `deal.status='negotiating'`

---

## Фаза 5: SalesAgent (новый агент — P0 для Pipeline B)

> **Полная спецификация:** [specs/sales-agent-spec.md](specs/sales-agent-spec.md) — SalesAgent, Business Analyzer, Lead Scorer, Touch Sequence.
>
> **Архитектурное решение:** Отдельный агент от Bid Agent.
> Bid Agent (Pipeline A): one-shot proposal для готового ТЗ.
> SalesAgent (Pipeline B): multi-turn conversation, discovery, концепция.

### Профиль агента

```python
SALES_AGENT_PROMPT = """
# Роль
Ты — менеджер по работе с клиентами. Твоё имя — [имя оператора или "Алексей"].
Ты не ИИ — ты человек. Никогда не раскрывай что ты AI.

# Задача
Провести клиента от первого касания до согласованной концепции проекта.

# Стиль
- Живой язык, как у реального менеджера
- Конкретика: факты, данные, примеры
- Без давления, без шаблонов
- Анализируй конкурентов, рынок, точки роста
- Объясняй ценность цифрами

# Этапы
1. Первый контакт (тон: любопытный, не продающий)
2. Discovery (что нужно? какие боли? какой бизнес?)
3. Анализ (конкуренты, рынок, что реально поможет)
4. Концепция (конкретное предложение с обоснованием)
5. Согласование (терпеливо, с готовностью менять)
"""
```

### Инструменты SalesAgent

```python
@tool
def get_competitor_analysis(business: dict) -> dict:
    """Анализ конкурентов в нише и регионе."""

@tool
def get_market_insights(category: str, city: str) -> dict:
    """Рыночные данные: сколько платят, что популярно."""

@tool
def generate_concept(business: dict, client_needs: dict) -> dict:
    """
    Генерация концепции проекта.
    Returns: {title, description, services[], estimated_timeline, why_this_helps}
    """

@tool
def send_message(lead_id: str, channel: str, text: str) -> str:
    """Отправка сообщения клиенту (TG DM / email)."""

@tool
def save_deal_context(deal_id: str, key: str, value: str) -> None:
    """Сохранение факта в Deal Memory."""

@tool
def get_deal_context(deal_id: str) -> dict:
    """Получение полного контекста сделки."""
```

### HITL #2 — Концепция оператору

**Момент:** SalesAgent сформировал концепцию, до отправки клиенту.
**Payload:**
```json
{
  "action_type": "concept_review",
  "deal_id": "...",
  "concept": {
    "title": "Лендинг с онлайн-записью",
    "services": ["дизайн", "разработка", "SEO"],
    "timeline": "2-3 недели",
    "rationale": "Конкуренты с сайтом получают +40% звонков"
  },
  "draft_message": "Смотрел ваш сервис..."
}
```

**Действия:** approve / edit (оператор правит концепцию) / reject (SalesAgent переосмысляет)

### HITL #3 — Концепция утверждена клиентом

**Момент:** клиент согласовал концепцию в переписке.
**Payload:**
```json
{
  "action_type": "concept_approved",
  "deal_id": "...",
  "agreed_scope": "Лендинг + онлайн-запись + SEO",
  "client_requirements": "...",
  "next_step": "design"
}
```

**Действия:** approve (→ Фаза 6 Дизайн) / hold (уточнить детали)

---

## Фаза 6: Дизайн (→ `design-workflow.md`)

Полный flow описан в `docs/Full_work/archive/design-workflow.md`.
Ключевые HITL-точки для Pipeline B:

**HITL #4** — Оператор ревью макета Design Agent (Pencil.dev / Penpot).
Макс. 3 раунда правок перед эскалацией.

**HITL #5** — Клиент утверждает дизайн.
Approve → переход к разработке.

`deal.status='design'` во время, `'design_approved'` после HITL #5.

---

## Фаза 7: Разработка (→ Pipeline A flow)

После утверждения дизайна Pipeline B вливается в Pipeline A с фазы Planner:

```
Planner → Dev Agent → Content Agent → Critic → [HITL #6: доставка] → Packager
```

`deal.status='in_development'` → `'completed'`

---

## Фаза 8: Touch Sequence (P1)

Применяется если клиент не отвечает (после HITL #1 → manual или → approve но клиент молчит).

```
День 1:  Первое касание (лучший доступный канал: TG > WhatsApp > Email)
День 3:  Нет ответа → второй канал (мягкий follow-up)
День 5:  Нет ответа → последний канал (финальное, без давления)
День 10: STOP — lead.status='no_response'
```

**Стоп-правила:**
- Одно "нет" / "не интересно" → `lead.status='declined'`, НИКОГДА больше не писать
- Max 3 касания одному лиду
- Один "нет" на любом этапе = СТОП НАВСЕГДА

**Зависимости:** Scheduler (APScheduler), `lead.touch_count`, `lead.last_contacted_at`, `lead.channel_used`.

---

## Dashboard UI

### Страница Pipeline B

**Таб: Сканирование**
```
┌─────────────────────────────────────────────────────┐
│  Режим: [GeoScanner ▼]  [Web Search]  [Telegram]    │
│                                                      │
│  GeoScanner:                                        │
│  Город: [________________]                          │
│  Категории: [✓] Авто  [✓] Медицина  [ ] Мебель ... │
│  [🚀 Запустить сканирование]                        │
│                                                      │
│  Прогресс: ████████░░ 80% | Гексов: 40/50          │
│  Найдено лидов: 234 (горячих: 45, тёплых: 89)      │
└─────────────────────────────────────────────────────┘
```

**Таб: Лиды (HITL Queue)**
```
┌─────────────────────────────────────────────────────┐
│  [Все (23)] [Горячие (8)] [Тёплые (11)] [Новые (4)]│
│                                                      │
│  🔴 Стоматология Улыбка     Ростов    Нет сайта  →  │
│  🟠 Авто-сервис МоторМастер Москва    Сайт 2018  →  │
│  🟡 Кафе Лимон              СПб       Мёртв.IG   →  │
└─────────────────────────────────────────────────────┘
```

**Таб: Активные сделки (Kanban)**
```
| Новый лид | Переговоры | Концепция | Дизайн | Разработка | Готово |
```

**Таб: Статистика**
```
Лидов найдено: 1,234 | Отправлено: 89 | Ответили: 12 | В работе: 5
Конверсия воронки: найден → HITL approve 68% → ответил 13% → сделка 5.6%
```

### Live Chat (внутри карточки лида)

После approve → SalesAgent:
```
┌──────────────────────────────────────────┐
│  💬 Переписка с Улыбкой                 │
│  ─────────────────────────────────────  │
│  [Менеджер] Привет! Увидел что у вас... │
│  [Клиент]   Добрый день, да, хотим сайт │
│  [Менеджер] Отлично! Расскажите...      │
│                                          │
│  [Статус: переговоры — день 3]          │
└──────────────────────────────────────────┘
```

---

## Deal Memory (client_context)

Persistent хранилище контекста сделки между сессиями.

**Таблица `client_context`:**
| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID | PK |
| lead_id | UUID | FK leads |
| deal_id | UUID | FK deals (новая таблица) |
| key_facts | JSONB | Что клиент говорил: боли, детали бизнеса |
| agreed_scope | TEXT | Что согласовали |
| decisions | JSONB | История ключевых решений |
| design_versions | JSONB | Версии макетов, правки |
| client_preferences | JSONB | Предпочтения по стилю, функционалу |
| updated_at | TIMESTAMP | — |

SalesAgent вызывает `save_deal_context(key, value)` после каждого важного факта.
При возобновлении сессии — `get_deal_context(deal_id)` для восстановления.

---

## HITL-точки

| # | Момент | Обязательный? | Действия | Канал |
|---|--------|---------------|----------|-------|
| 1 | Lead Card: оценка лида | ДА (горячие/тёплые) | approve / manual / skip | Dashboard |
| 2 | Concept Review: концепция до клиента | ДА | approve / edit / reject | Dashboard + TG бот |
| 3 | Concept Approved: клиент согласовал | ДА | approve / hold | Dashboard |
| 4 | Design Review: макет оператору | ДА | approve / revise (макс 3) | Dashboard |
| 5 | Design Client: клиент утверждает | ДА | approve / changes | Dashboard |
| 6 | Delivery: сдача проекта | ДА (→ Pipeline A) | approve / reject | Dashboard |

---

## Каналы связи

| Канал | Реализован? | Лимит | Инфраструктура | Приоритет |
|-------|-------------|-------|----------------|-----------|
| Telegram DM | ✅ | 5/час, 720s между | Telethon + TelegramDMSender | 1 (высший) |
| Email | ✅ | 50/день, 30-60s stagger | SMTP + EmailSender | 2 |
| WhatsApp Business | ❌ | ~$0.05/msg | Business API | P2 |
| LinkedIn | ❌ | ~50 InMail/мес | Отложен | P4 |

---

## Таблицы БД

### `leads` (существует, расширить)
```
id, osm_id, business_name, address, city, category
phone, email, telegram_username, instagram_url, vk_url
has_website, website_url, website_score
google_rating, review_count
source (geo_scanner / web_search / telegram)
source_id
lead_score, temperature (hot/warm/cold)
status (new / hitl_pending / approved / skipped / declined / negotiating / won / lost / no_response)
touch_count, last_contacted_at, channel_used
created_at, updated_at
```

### `deals` (новая таблица)
```
id, lead_id, source (pipeline_a / pipeline_b)
status (new / negotiating / concept / design / development / completed / lost)
agreed_scope, concept_title
started_at, completed_at
```

### `client_context` (новая таблица)
```
id, lead_id, deal_id
key_facts JSONB, agreed_scope TEXT
decisions JSONB, design_versions JSONB
client_preferences JSONB
updated_at
```

### `telegram_user_profiles` (новая таблица — P2)
```
id, telegram_user_id, username
messages JSONB, channels TEXT[]
analyzed_at, score FLOAT
needs JSONB, status (pending / analyzed / promoted / rejected)
```

### `email_campaigns` (существует)
### `campaign_leads` (существует)

---

## Приоритеты реализации

### P0 — Критические баги (блокируют базовую работу)
- Идемпотентность dispatch (повторный запуск → дубли сообщений)
- Edit в HITL (action поддерживается, не реализован)
- Telegram session persistence (каждый раз новый коннект)
- FloodWait batch stop (сейчас продолжает слать при бане)

### P1 — Ядро видения (основные фичи из View 2)
- **SalesAgent** (новый агент, multi-turn conversation, deal memory)
- **Business Analyzer** (хотя бы quick tier — 5 секунд)
- **Lead Scorer** (балльная система → температура)
- **Rich HITL Lead Card** (AI-анализ + рекомендации + конкуренты)
- **Touch Sequence** (Day1→Day3→Day5→Stop, Scheduler + lead tracking)
- **`deals` + `client_context` таблицы** (миграция)
- **Live chat в HITL карточке** (WebSocket, SalesAgent ↔ клиент)
- **CRM/Kanban** в Dashboard (единый для A+B)

### P2 — Расширение
- **WebScoutAgent** (Web Search mode: 2GIS nationwide + Яндекс.Бизнес + VK)
- **TelegramProfileAggregator** (сбор профилей, LLM анализ, backfill 30 дней)
- **Business Analyzer** medium + deep тиры
- **WhatsApp Business API** (channel priority #1 после TG)
- Категорийные фильтры в GeoScanner (чекбоксы в UI)
- Retry soft bounces + unsubscribe tracking

### P3 — Будущее
- Instagram Business (web search source)
- LinkedIn (международный, Фаза 4)
- A/B тесты сообщений
- Pipeline C (Snapshot Report, Auto-Monitor, Upsell) — отдельный pipeline
- WhatsApp кастомные шаблоны (Business API verification)

---

## Ключевые файлы

### Существующие
| Файл | Роль |
|------|------|
| `src/agents/geo_scout.py` | GeoScout Agent (расширить под категории + Яндекс API) |
| `src/agents/outreach.py` | Outreach Agent (исходящий контакт, заменить SalesAgent) |
| `src/geo/h3_scanner.py` | H3 гексы |
| `src/geo/overpass.py` | Overpass API queries |
| `src/enrichment/waterfall.py` | OSINT → Hunter → Apollo |
| `src/enrichment/email_sender.py` | SMTP отправка |
| `src/enrichment/telegram_sender.py` | Telethon DM |
| `src/services/telegram_listener.py` | TelegramListener (22 канала) |
| `src/core/graph.py` | Pipeline B LangGraph nodes |
| `src/prompts/outreach.py` | LLM промпты (заменить на sales промпты) |

### Новые файлы (создать)
| Файл | Роль |
|------|------|
| `src/agents/sales_agent.py` | SalesAgent (multi-turn B2B продажи) |
| `src/agents/web_scout.py` | WebScoutAgent (multi-source web search) |
| `src/services/telegram_profile_aggregator.py` | TelegramProfileAggregator |
| `src/prompts/sales.py` | Sales prompts (discovery, concept, negotiation) |
| `src/core/deal_memory.py` | Deal Memory CRUD (client_context таблица) |
| `src/core/lead_scorer.py` | Lead Scorer (балльная система) |
| `src/core/business_analyzer.py` | Business Analyzer (3 тира) |
| `src/core/touch_sequence.py` | Touch Sequence scheduler |
| `alembic/versions/YYYYMMDD_deals.py` | Миграция: deals + client_context + telegram_user_profiles |

### Дизайн-документы (source of truth)
| Файл | Роль |
|------|------|
| `docs/Full_work/archive/personalized-outreach-design.md` | Тон, этика, Touch Sequence, Lead Scorer |
| `docs/Full_work/archive/telegram-strategy.md` | Dual pipeline, TelegramProfileAggregator, anti-ban |
| `docs/Full_work/archive/design-workflow.md` | Design workflow (HITL #4, #5) |
| `docs/Full_work/ideas/client-growth-upselling.md` | Pipeline C (будущее) |
| `docs/negotiation_flows.md` | Negotiation state machine (адаптировать под SalesAgent) |
