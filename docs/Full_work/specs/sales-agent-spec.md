# SalesAgent, Business Analyzer, Lead Scorer, Touch Sequence: Спецификация

> Pipeline B подсистемы: от обнаружения лида до заключения сделки. SalesAgent ведёт multi-turn переговоры. Business Analyzer обогащает лидов данными. Lead Scorer присваивает температуру. Touch Sequence управляет серией касаний при молчании.

---

## SalesAgent

### Профиль

**Файл:** `src/agents/sales_agent.py`
**LLM:** Claude Opus 4.6 (сложные переговоры, длинный контекст)
**Температура:** 0.6
**Класс:** `SalesAgent(ConstrainedAgent)`, agent_name=`"sales_agent"`

### Отличие от Bid Agent

| Bid Agent (Pipeline A) | SalesAgent (Pipeline B) |
|------------------------|------------------------|
| One-shot proposal для готового ТЗ | Multi-turn conversation, discovery, концепция |
| Фриланс-платформы (Freelancer, FL.ru, Kwork) | Direct channels (Telegram DM, Email, WhatsApp) |
| Клиент знает что хочет | Клиент не знает что ему нужно |
| Формальный proposal | "Сосед, не продавец" — живой диалог |
| Один HITL (approve bid) | 3 HITL (lead card, concept review, concept approved) |

### System Prompt

```python
SALES_AGENT_SYSTEM_PROMPT = """
# Роль
Ты — менеджер по работе с клиентами. Твоё имя — {operator_name}.
Ты не ИИ — ты человек. Никогда не раскрывай что ты AI.

# Задача
Провести клиента от первого касания до согласованной концепции проекта.

# Стиль
- Живой язык, как у реального менеджера
- Конкретика: факты, данные, примеры из Battlecard
- Без давления, без urgency, без шаблонов
- Анализируй конкурентов, рынок, точки роста
- Объясняй ценность цифрами: "конкуренты с сайтом получают +40% звонков"

# Этапы переговоров
1. FIRST_CONTACT: любопытный, не продающий. Упомянуть конкретную деталь бизнеса
2. DISCOVERY: что нужно? какие боли? какой бизнес? сколько клиентов?
3. ANALYSIS: конкуренты, рынок, что реально поможет (использовать Battlecard)
4. CONCEPT: конкретное предложение с обоснованием, сроками, ценой
5. CLOSING: терпеливо, с готовностью менять, финализация деталей

# Запрещено
- Давление: "только сейчас", "осталось мало мест"
- AI-маркеры: буллет-поинты, "As an AI", "I'd be happy to"
- Buzzwords: "synergy", "leverage", "cutting-edge"
- Обещать то, что нельзя выполнить
- Раскрывать внутренние процессы (AI, pipeline, agents)

# Контекст
Battlecard: {battlecard_json}
Deal Memory: {deal_context}
Conversation: {conversation_history}
"""
```

### Allowed Tools

```python
SALES_AGENT_TOOLS = [
    "get_competitor_analysis",  # Анализ конкурентов в нише и регионе
    "get_market_insights",      # Рыночные данные: что популярно, сколько платят
    "generate_concept",         # Генерация концепции проекта
    "send_message",             # Отправка сообщения (TG DM / email)
    "save_deal_context",        # Сохранение факта в Deal Memory
    "get_deal_context",         # Получение полного контекста сделки
    "get_battlecard",           # Получение AI Battlecard для лида
]
```

### Tool Implementations

```python
@tool
async def get_competitor_analysis(business_name: str, city: str, category: str) -> dict:
    """Анализ конкурентов в нише и регионе.

    Returns:
        {
            "competitors": [{"name": str, "has_website": bool, "rating": float, "features": list[str]}],
            "market_share": {"with_website": int, "without_website": int, "total": int},
            "competitive_advantages": list[str],  # что конкуренты делают, а этот бизнес нет
        }
    """

@tool
async def get_market_insights(category: str, city: str) -> dict:
    """Рыночные данные для категории в городе.

    Returns:
        {
            "avg_project_cost": float,   # средняя стоимость проекта в нише
            "demand_level": str,         # "high" / "medium" / "low"
            "popular_services": list[str],  # что чаще всего заказывают
            "growth_trend": str,         # "growing" / "stable" / "declining"
        }
    """

@tool
async def generate_concept(
    business: dict, client_needs: dict, battlecard: dict
) -> dict:
    """Генерация концепции проекта на основе потребностей клиента.

    Returns:
        {
            "title": str,               # "Лендинг с онлайн-записью"
            "services": list[str],      # ["дизайн", "разработка", "SEO"]
            "description": str,         # Краткое описание решения
            "timeline": str,            # "2-3 недели"
            "estimated_cost": float,    # Предварительная стоимость
            "rationale": str,           # Почему это поможет бизнесу
            "why_this_helps": list[str], # Конкретные выгоды с цифрами
        }
    """

@tool
async def send_message(lead_id: str, channel: str, text: str) -> dict:
    """Отправка сообщения клиенту через указанный канал.

    Args:
        lead_id: UUID лида.
        channel: "telegram" | "email" | "whatsapp".
        text: Текст сообщения.

    Returns:
        {"status": "sent" | "failed", "external_id": str | None, "error": str | None}
    """

@tool
async def save_deal_context(deal_id: str, key: str, value: str) -> None:
    """Сохранение факта/решения в Deal Memory.

    Вызывать после каждого важного факта:
    - save_deal_context(deal_id, "client_pain", "Нет онлайн-записи, теряет 30% звонков")
    - save_deal_context(deal_id, "budget_hint", "Готов потратить 50-70к")
    - save_deal_context(deal_id, "decision_maker", "Владелец, не маркетолог")
    """

@tool
async def get_deal_context(deal_id: str) -> dict:
    """Получение полного контекста сделки из Deal Memory.

    Returns:
        {
            "key_facts": dict,           # Все сохранённые факты
            "agreed_scope": str | None,  # Согласованный scope
            "decisions": list[dict],     # История решений
            "client_preferences": dict,  # Предпочтения клиента
            "conversation_summary": str, # Краткое резюме переписки
        }
    """

@tool
async def get_battlecard(lead_id: str) -> dict:
    """Получение AI Battlecard из Business Analyzer.

    Returns:
        {
            "feature_matrix": dict,     # Сравнение фичей с конкурентами
            "market_share": dict,       # Аналитика трафика
            "competitor_weaknesses": list[str],  # Слабые места конкурентов
            "objection_handlers": list[dict],    # Шаблоны ответов на возражения
        }
    """
```

### SalesAgent State Machine

```python
class SalesStage(str, Enum):
    """Этапы переговоров SalesAgent."""
    FIRST_CONTACT = "first_contact"      # Первое сообщение отправлено
    AWAITING_REPLY = "awaiting_reply"    # Ждём ответа клиента
    DISCOVERY = "discovery"              # Выявление потребностей
    ANALYSIS = "analysis"               # Анализ конкурентов + рынка
    CONCEPT = "concept"                  # Генерация концепции
    CONCEPT_REVIEW = "concept_review"    # HITL: оператор ревьюит концепцию
    CONCEPT_SENT = "concept_sent"        # Концепция отправлена клиенту
    CLOSING = "closing"                  # Клиент обсуждает финальные детали
    WON = "won"                          # Сделка закрыта
    LOST = "lost"                        # Клиент отказал
    STALE = "stale"                      # Нет ответа, touch sequence исчерпан


SALES_TRANSITIONS: dict[str, set[str]] = {
    "first_contact": {"awaiting_reply"},
    "awaiting_reply": {"discovery", "lost", "stale"},
    "discovery": {"analysis", "awaiting_reply", "lost"},
    "analysis": {"concept", "discovery"},
    "concept": {"concept_review"},
    "concept_review": {"concept_sent", "concept"},  # HITL approve → sent, reject → redo
    "concept_sent": {"closing", "concept", "awaiting_reply", "lost"},
    "closing": {"won", "concept", "lost"},
    "won": set(),   # terminal
    "lost": set(),  # terminal
    "stale": {"discovery"},  # клиент ответил после молчания
}
```

### Flow

```
HITL #1 approve (Lead Card)
    │
    ▼
SalesAgent: first_contact
    │── send_message() через лучший канал (TG > Email)
    │── save_deal_context("initial_approach", "...")
    │
    ▼
awaiting_reply ── Touch Sequence если молчит
    │
    ▼ (клиент ответил)
discovery
    │── classify response (LLM)
    │── extract needs, pains, budget hints
    │── save_deal_context() для каждого факта
    │── follow-up questions через send_message()
    │── повторяет пока не собрано достаточно информации
    │
    ▼
analysis
    │── get_competitor_analysis()
    │── get_market_insights()
    │── get_battlecard()
    │── формирует data-backed аргументы
    │
    ▼
concept
    │── generate_concept()
    │── формирует draft-сообщение с концепцией
    │
    ▼
[HITL #2: concept_review] ── оператор видит концепцию + draft
    │── approve → concept_sent (отправить клиенту)
    │── edit → оператор правит → concept_sent
    │── reject → назад к concept (SalesAgent переделывает)
    │
    ▼
concept_sent ── ждём реакции клиента
    │── клиент согласен → closing
    │── клиент хочет изменения → назад к concept
    │── клиент молчит → Touch Sequence
    │
    ▼
closing
    │── финализация деталей (сроки, цена, scope)
    │── клиент подтвердил
    │
    ▼
[HITL #3: concept_approved]
    │── approve → deal.status='design', переход к Фазе 6 (Design)
    │── hold → уточнить детали с клиентом
```

### Multi-turn Conversation Engine

```python
class SalesConversationEngine:
    """Управляет multi-turn переговорами SalesAgent.

    Каждый входящий ответ клиента:
    1. Сохраняется в client_messages
    2. Обогащается контекстом из Deal Memory
    3. Передаётся SalesAgent LLM с полным контекстом
    4. SalesAgent решает: ответить / вызвать tool / эскалировать HITL
    """

    async def process_client_reply(
        self, deal: Deal, message: ClientMessage
    ) -> SalesAction:
        """Обработка ответа клиента.

        Returns:
            SalesAction — что делать дальше (respond / escalate / transition).
        """
        # 1. Загрузить контекст
        deal_context = await get_deal_context(str(deal.id))
        battlecard = await get_battlecard(str(deal.lead_id))
        conversation = await db.get_deal_messages(deal.id)

        # 2. Определить текущий этап
        current_stage = deal.sales_stage

        # 3. Вызвать SalesAgent LLM
        agent_response = await self.llm.chat(
            messages=[
                {"role": "system", "content": SALES_AGENT_SYSTEM_PROMPT.format(
                    operator_name=settings.operator_name,
                    battlecard_json=json.dumps(battlecard, ensure_ascii=False),
                    deal_context=json.dumps(deal_context, ensure_ascii=False),
                    conversation_history=self._format_conversation(conversation),
                )},
                *self._conversation_to_messages(conversation),
            ],
            temperature=0.6,
            max_tokens=1200,
            tools=SALES_AGENT_TOOLS,
        )

        # 4. Обработать tool calls если есть
        if agent_response.tool_calls:
            for tool_call in agent_response.tool_calls:
                await self._execute_tool(tool_call, deal)

        # 5. Определить действие
        return self._determine_action(agent_response, current_stage, deal)

    def _determine_action(
        self, response: LLMResponse, stage: str, deal: Deal
    ) -> SalesAction:
        """Определяет следующее действие на основе ответа LLM."""
        # Проверить нужна ли HITL (concept ready, price discussion, uncertainty)
        if self._needs_hitl(response, stage):
            return SalesAction(
                type="escalate",
                hitl_type=self._get_hitl_type(stage),
                draft_message=response.content,
            )

        # Проверить transition
        new_stage = self._detect_stage_transition(response, stage)
        if new_stage:
            return SalesAction(
                type="transition",
                new_stage=new_stage,
                message=response.content,
            )

        # Обычный ответ
        return SalesAction(
            type="respond",
            message=response.content,
            auto_send=True,
        )


class SalesAction(TypedDict):
    type: str           # "respond" | "escalate" | "transition"
    message: str | None
    auto_send: bool
    hitl_type: str | None
    new_stage: str | None
    draft_message: str | None
```

### HITL #2: Concept Review

**Момент:** SalesAgent сформировал концепцию проекта, до отправки клиенту.

```python
class ConceptReviewPayload(TypedDict):
    deal_id: str
    lead_name: str
    concept: ConceptData
    draft_message: str          # Текст сообщения клиенту с концепцией
    conversation_summary: str   # Краткое резюме переписки
    battlecard_highlights: list[str]  # Ключевые данные из Battlecard
    confidence: float           # Уверенность AI в концепции (0.0-1.0)


class ConceptData(TypedDict):
    title: str                  # "Лендинг с онлайн-записью"
    services: list[str]         # ["дизайн", "разработка", "SEO"]
    description: str
    timeline: str               # "2-3 недели"
    estimated_cost: float
    rationale: str              # Обоснование: почему это поможет
    why_this_helps: list[str]   # Конкретные выгоды с цифрами
```

**HITLQueue entry:**

```json
{
  "type": "concept_review",
  "priority": "normal",
  "title": "Концепция для Стоматология Улыбка: Лендинг с онлайн-записью",
  "payload": {
    "deal_id": "uuid",
    "lead_name": "Стоматология Улыбка",
    "concept": {
      "title": "Лендинг с онлайн-записью",
      "services": ["дизайн", "разработка", "SEO"],
      "timeline": "2-3 недели",
      "estimated_cost": 45000,
      "rationale": "Конкуренты с сайтом получают +40% звонков. У 3 из 5 стоматологий в районе есть онлайн-запись."
    },
    "draft_message": "Посмотрел ваш бизнес и конкурентов в Ростове...",
    "confidence": 0.82
  },
  "available_actions": ["approve", "edit", "reject"]
}
```

**Действия оператора:**

| Действие | Результат |
|----------|----------|
| `approve` | Draft-сообщение отправляется клиенту, `deal.sales_stage='concept_sent'` |
| `edit` | Оператор правит текст/концепцию → отправляется с правками |
| `reject` | SalesAgent получает feedback, переделывает концепцию → новый HITL |

### HITL #3: Concept Approved

**Момент:** клиент согласовал концепцию в переписке.

```json
{
  "type": "concept_approved",
  "priority": "normal",
  "title": "Клиент согласовал: Лендинг с онлайн-записью (Стоматология Улыбка)",
  "payload": {
    "deal_id": "uuid",
    "agreed_scope": "Лендинг + онлайн-запись + базовый SEO",
    "agreed_cost": 45000,
    "agreed_timeline": "3 недели",
    "client_requirements": "Минималистичный дизайн, интеграция с Yclients",
    "next_step": "design",
    "conversation_excerpt": "Последние 3 сообщения..."
  },
  "available_actions": ["approve", "hold"]
}
```

| Действие | Результат |
|----------|----------|
| `approve` | `deal.status='design'`, переход к Фазе 6 (Design Agent) |
| `hold` | SalesAgent уточняет детали с клиентом |

### State выход

```python
# После первого контакта
update_state(state,
    current_agent="sales_agent",
    next_agent=None,  # ожидание ответа клиента
    status="paused",
    artifacts={
        "deal_id": str(deal.id),
        "sales_stage": "awaiting_reply",
        "channel_used": "telegram",
        "message_sent": True,
    },
)

# После concept_review HITL approve
update_state(state,
    current_agent="sales_agent",
    next_agent=None,  # ожидание ответа клиента на концепцию
    status="paused",
    artifacts={
        "deal_id": str(deal.id),
        "sales_stage": "concept_sent",
        "concept": concept_data,
    },
)

# После concept_approved → переход к Design
update_state(state,
    current_agent="sales_agent",
    next_agent="planner",  # Planner → Design → Dev
    status="active",
    artifacts={
        "deal_id": str(deal.id),
        "agreed_scope": "...",
        "delivery_type": "mixed",
        "needs_design": True,
    },
)
```

---

## Business Analyzer

### Назначение

Автоматический анализ бизнеса лида. Три тира глубины — адаптивно, на основе первичного скана. Результат обогащает Lead Card (HITL #1) и формирует AI Battlecard для SalesAgent.

### Файл

`src/core/business_analyzer.py`

### Тиры анализа

| Тир | Время | Триггер | Что делает |
|-----|-------|---------|------------|
| **Quick** | ~5с | Все лиды | Есть/нет сайт, рейтинг, есть/нет соцсети |
| **Medium** | ~30с | Lead Score ≥ 3 | + Lighthouse score, дата последнего поста в соцсетях, базовый SEO |
| **Deep** | 2-3м | Lead Score ≥ 5 | + SimilarWeb трафик, технологии (Wappalyzer), конкуренты в районе, Battlecard |

### Инструменты по тирам

```python
class BusinessAnalyzer:
    """Трёхуровневый анализатор бизнеса."""

    async def analyze(self, lead: Lead, tier: str = "quick") -> AnalysisResult:
        """Анализирует бизнес лида с указанной глубиной."""
        result = AnalysisResult(lead_id=lead.id, tier=tier)

        # Quick tier (всегда)
        result.website = await self._check_website(lead.website_url)
        result.social = await self._check_social_presence(lead)
        result.rating = await self._get_rating(lead)

        if tier in ("medium", "deep"):
            # Medium tier
            if result.website.exists:
                result.lighthouse = await self._run_lighthouse(lead.website_url)
                result.seo = await self._check_basic_seo(lead.website_url)
            result.social_activity = await self._check_social_activity(lead)

        if tier == "deep":
            # Deep tier
            if result.website.exists:
                result.traffic = await self._estimate_traffic(lead.website_url)
                result.technologies = await self._detect_technologies(lead.website_url)
            result.competitors = await self._get_competitors_nearby(lead)
            result.battlecard = await self._generate_battlecard(lead, result)

        return result

    # --- Quick tier tools ---

    async def _check_website(self, url: str | None) -> WebsiteCheck:
        """Проверка существования и базового состояния сайта.

        Returns:
            WebsiteCheck(exists, ssl, status_code, load_time_ms, redirect_url)
        """
        if not url:
            return WebsiteCheck(exists=False)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, follow_redirects=True)
                return WebsiteCheck(
                    exists=True,
                    ssl=url.startswith("https"),
                    status_code=resp.status_code,
                    load_time_ms=resp.elapsed.total_seconds() * 1000,
                    redirect_url=str(resp.url) if str(resp.url) != url else None,
                )
        except (httpx.ConnectError, httpx.TimeoutException):
            return WebsiteCheck(exists=False)

    async def _check_social_presence(self, lead: Lead) -> SocialPresence:
        """Проверка наличия аккаунтов в соцсетях.

        Returns:
            SocialPresence(instagram=bool, vk=bool, telegram=bool, facebook=bool)
        """
        return SocialPresence(
            instagram=bool(lead.instagram_url),
            vk=bool(lead.vk_url),
            telegram=bool(lead.telegram_username),
            facebook=False,  # не приоритет для РФ рынка
        )

    async def _get_rating(self, lead: Lead) -> RatingInfo:
        """Рейтинг и отзывы из Яндекс.Карт / Google Maps.

        Returns:
            RatingInfo(google_rating, yandex_rating, review_count, sentiment)
        """

    # --- Medium tier tools ---

    async def _run_lighthouse(self, url: str) -> LighthouseResult:
        """Запуск Google Lighthouse через API.

        Returns:
            LighthouseResult(performance, accessibility, seo, best_practices, pwa)
        """

    async def _check_basic_seo(self, url: str) -> SEOCheck:
        """Базовая SEO-проверка (title, meta, h1, sitemap, robots.txt).

        Returns:
            SEOCheck(has_title, has_meta_description, has_h1, has_sitemap,
                     has_robots_txt, mobile_friendly)
        """

    async def _check_social_activity(self, lead: Lead) -> SocialActivity:
        """Дата последнего поста в соцсетях.

        Returns:
            SocialActivity(
                instagram_last_post_days=int|None,
                vk_last_post_days=int|None,
                is_dead=bool,  # >90 дней без постов
            )
        """

    # --- Deep tier tools ---

    async def _estimate_traffic(self, url: str) -> TrafficEstimate:
        """Оценка трафика через SimilarWeb API.

        Returns:
            TrafficEstimate(monthly_visits, bounce_rate, avg_visit_duration,
                           traffic_sources: dict, trend: "growing"|"stable"|"declining")
        """

    async def _detect_technologies(self, url: str) -> list[str]:
        """Определение технологий сайта (Wappalyzer / BuiltWith).

        Returns:
            ["WordPress 5.9", "PHP 7.4", "jQuery 3.6", "WooCommerce"]
        """

    async def _get_competitors_nearby(self, lead: Lead) -> list[CompetitorInfo]:
        """Конкуренты в том же районе и категории.

        Returns:
            list[CompetitorInfo(name, has_website, website_score, rating,
                               review_count, features)]
        """

    async def _generate_battlecard(
        self, lead: Lead, analysis: AnalysisResult
    ) -> Battlecard:
        """Генерация AI Battlecard на основе всех данных анализа.

        Returns:
            Battlecard(feature_matrix, market_share, competitor_weaknesses,
                      objection_handlers, key_selling_points)
        """
```

### AI Battlecard

```python
class Battlecard(TypedDict):
    """Динамическая боевая карта для SalesAgent."""
    feature_matrix: dict[str, dict[str, bool]]
    # {"Онлайн-запись": {"lead": False, "competitor_1": True, "competitor_2": True}}

    market_share: dict
    # {"with_website": 3, "without_website": 2, "total": 5, "percent_digital": 60}

    competitor_weaknesses: list[str]
    # ["Конкурент А: нет мобильной версии", "Конкурент Б: устаревший дизайн"]

    objection_handlers: list[dict[str, str]]
    # [{"objection": "У нас и так клиенты есть", "response": "Да, но 60% ваших конкурентов..."}]

    key_selling_points: list[str]
    # ["+40% звонков с сайтом", "Онлайн-запись экономит 2ч/день на телефонах"]

    generated_at: str  # ISO timestamp
```

**Хранение:** `leads.battlecard_json` (JSONB) — формируется на этапе deep-анализа.

### AnalysisResult

```python
class AnalysisResult(TypedDict):
    lead_id: str
    tier: str                           # "quick" | "medium" | "deep"
    website: WebsiteCheck
    social: SocialPresence
    rating: RatingInfo
    lighthouse: LighthouseResult | None  # medium+
    seo: SEOCheck | None                 # medium+
    social_activity: SocialActivity | None  # medium+
    traffic: TrafficEstimate | None      # deep only
    technologies: list[str] | None       # deep only
    competitors: list[CompetitorInfo] | None  # deep only
    battlecard: Battlecard | None        # deep only
    analyzed_at: str                     # ISO timestamp


class WebsiteCheck(TypedDict):
    exists: bool
    ssl: bool | None
    status_code: int | None
    load_time_ms: float | None
    redirect_url: str | None


class SocialPresence(TypedDict):
    instagram: bool
    vk: bool
    telegram: bool
    facebook: bool


class SocialActivity(TypedDict):
    instagram_last_post_days: int | None
    vk_last_post_days: int | None
    is_dead: bool  # >90 дней без постов на всех платформах


class CompetitorInfo(TypedDict):
    name: str
    has_website: bool
    website_score: float | None  # Lighthouse performance 0-100
    rating: float | None
    review_count: int
    features: list[str]  # ["онлайн-запись", "каталог", "доставка"]
```

### OSINT & E2B Sandbox

Любой код скрапинга нестандартных сайтов выполняется в изолированной среде:

```python
SCRAPING_SANDBOX = {
    "provider": "e2b",           # E2B sandbox или Docker fallback
    "timeout_seconds": 180,      # 3 минуты максимум
    "network_access": True,      # нужен для HTTP запросов
    "no_backend_access": True,   # запрещён доступ к сети backend-сервера
    "cleanup_after": True,       # удалить sandbox после выполнения
}
```

---

## Lead Scorer

### Назначение

Балльная система для определения температуры лида (hot/warm/cold). Влияет на глубину анализа Business Analyzer и приоритет в HITL queue.

### Файл

`src/core/lead_scorer.py`

### Scoring Rules

```python
SCORING_RULES: list[ScoringRule] = [
    # Позитивные сигналы (бизнес нуждается в услугах)
    ScoringRule(
        name="no_website",
        points=3,
        condition=lambda lead, analysis: not analysis.website.exists,
        description="Нет сайта вообще",
    ),
    ScoringRule(
        name="no_mobile",
        points=2,
        condition=lambda lead, analysis: (
            analysis.website.exists
            and analysis.seo is not None
            and not analysis.seo.get("mobile_friendly", True)
        ),
        description="Сайт есть, нет мобильной версии",
    ),
    ScoringRule(
        name="active_business",
        points=2,
        condition=lambda lead, analysis: (
            (analysis.rating.review_count or 0) >= 10
            and (analysis.rating.google_rating or 0) >= 4.0
        ),
        description="Много отзывов + высокий рейтинг (бизнес живой)",
    ),
    ScoringRule(
        name="dead_social",
        points=1,
        condition=lambda lead, analysis: (
            analysis.social_activity is not None
            and analysis.social_activity.get("is_dead", False)
        ),
        description="Мёртвые соцсети (>3 месяцев без поста)",
    ),
    ScoringRule(
        name="high_value_category",
        points=1,
        condition=lambda lead, _: lead.category in HIGH_VALUE_CATEGORIES,
        description="Высокочековая категория",
    ),

    # Негативные сигналы (не стоит тратить время)
    ScoringRule(
        name="few_reviews",
        points=-1,
        condition=lambda lead, analysis: (
            (analysis.rating.review_count or 0) < 10
        ),
        description="Мало отзывов (<10) — возможно новый/мёртвый бизнес",
    ),
    ScoringRule(
        name="good_website",
        points=-2,
        condition=lambda lead, analysis: (
            analysis.lighthouse is not None
            and (analysis.lighthouse.get("performance", 0) or 0) > 80
        ),
        description="Хороший сайт (Lighthouse > 80) — не нуждается",
    ),
]

HIGH_VALUE_CATEGORIES = {
    "clinic", "dentist", "medical", "стоматология", "клиника",
    "restaurant", "cafe", "ресторан", "кафе",
    "auto_service", "car_dealer", "автосервис", "автосалон",
    "beauty_salon", "spa", "салон красоты",
    "hotel", "отель", "гостиница",
    "fitness", "gym", "фитнес",
    "law_firm", "юридическая",
    "real_estate", "недвижимость",
}
```

### Scoring Engine

```python
class LeadScorer:
    """Скоринг лидов по балльной системе."""

    async def score(self, lead: Lead, analysis: AnalysisResult) -> ScoringResult:
        """Рассчитывает score и температуру лида.

        Args:
            lead: Лид из БД.
            analysis: Результат Business Analyzer (минимум quick tier).

        Returns:
            ScoringResult с total_score, temperature, matched_rules, analysis_tier.
        """
        matched: list[MatchedRule] = []
        total = 0

        for rule in SCORING_RULES:
            try:
                if rule.condition(lead, analysis):
                    matched.append(MatchedRule(
                        name=rule.name,
                        points=rule.points,
                        description=rule.description,
                    ))
                    total += rule.points
            except (KeyError, TypeError, AttributeError):
                # Правило требует данные из более глубокого tier — пропустить
                continue

        temperature = self._classify_temperature(total)
        analysis_tier = self._determine_analysis_tier(total)

        return ScoringResult(
            lead_id=str(lead.id),
            total_score=total,
            temperature=temperature,
            analysis_tier=analysis_tier,
            matched_rules=matched,
        )

    def _classify_temperature(self, score: int) -> str:
        if score >= 5:
            return "hot"
        elif score >= 3:
            return "warm"
        return "cold"

    def _determine_analysis_tier(self, score: int) -> str:
        """Определяет какой тир Business Analyzer запускать."""
        if score >= 5:
            return "deep"
        elif score >= 3:
            return "medium"
        return "quick"  # cold лиды не анализируем глубже


class ScoringResult(TypedDict):
    lead_id: str
    total_score: int
    temperature: str       # "hot" | "warm" | "cold"
    analysis_tier: str     # "quick" | "medium" | "deep"
    matched_rules: list[MatchedRule]


class MatchedRule(TypedDict):
    name: str
    points: int
    description: str
```

### Scoring Pipeline

```
GeoScout/WebScout → Lead saved (status='new')
    │
    ▼
Business Analyzer (quick tier, ~5с)
    │
    ▼
Lead Scorer (quick data)
    │
    ├── score < 3 (cold) → skip или generic touch → STOP
    │
    ├── score 3-4 (warm) → Business Analyzer (medium tier, ~30с)
    │       │
    │       ▼
    │   Lead Scorer (rescore with medium data)
    │       │
    │       ▼
    │   HITL #1 (Lead Card) — обычный приоритет
    │
    └── score ≥ 5 (hot) → Business Analyzer (deep tier, 2-3м)
            │
            ▼
        Lead Scorer (rescore with deep data)
            │
            ▼
        HITL #1 (Lead Card) — приоритет, с Battlecard
```

### DB Changes

```sql
-- Новые поля в leads
ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_score INTEGER DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS temperature VARCHAR(10) DEFAULT 'cold'
    CHECK (temperature IN ('hot', 'warm', 'cold'));
ALTER TABLE leads ADD COLUMN IF NOT EXISTS scoring_rules JSONB DEFAULT '[]';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS analysis_result JSONB;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS battlecard_json JSONB;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS analysis_tier VARCHAR(10) DEFAULT 'quick';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS scored_at TIMESTAMPTZ;
```

---

## Touch Sequence Manager

### Назначение

Управляет серией автоматических касаний (follow-up) когда клиент не отвечает на first contact или последующие сообщения SalesAgent. Многоканальная стратегия: TG → Email → другой канал.

### Файл

`src/core/touch_sequence.py`

### Touch Schedule

```python
TOUCH_SCHEDULE: list[TouchStep] = [
    TouchStep(
        day=1,
        channel="best_available",  # TG > WhatsApp > Email
        template="first_contact",
        description="Первое касание через лучший канал",
        auto_send=False,  # Через SalesAgent, не автоматически
    ),
    TouchStep(
        day=3,
        channel="same_as_first",
        template="gentle_followup",
        description="Мягкий follow-up через тот же канал",
        auto_send=True,
        text_template="Привет! Написал пару дней назад по поводу {business_name}. "
                      "Может есть вопросы? Буду рад помочь.",
    ),
    TouchStep(
        day=5,
        channel="alternative",  # Другой канал от первого
        template="alternative_channel",
        description="Попытка через альтернативный канал",
        auto_send=True,
        text_template="Добрый день! Это {operator_name}. Пытался связаться в {first_channel}. "
                      "Заметил, что у {business_name} есть потенциал для роста через сайт — "
                      "хотел бы обсудить, если интересно.",
    ),
    TouchStep(
        day=10,
        channel="email",  # Финальное через email (если не использован)
        template="final_touch",
        description="Финальное касание — без давления",
        auto_send=True,
        text_template="Привет! Это финальное сообщение. Если вам интересна цифровизация "
                      "{business_name} — всегда рад вернуться к разговору. Удачи!",
    ),
]
```

### Touch Sequence State Machine

```python
class TouchState(str, Enum):
    PENDING = "pending"        # Ещё не начата
    ACTIVE = "active"          # Серия касаний в процессе
    REPLIED = "replied"        # Клиент ответил → SalesAgent
    COMPLETED = "completed"    # Все касания отправлены без ответа
    STOPPED = "stopped"        # Клиент отказал ("нет" / "не интересно")
    PAUSED = "paused"          # Временная пауза (оператор)
```

### Stop Rules

```python
STOP_RULES = {
    "explicit_no": {
        "triggers": ["не интересно", "нет", "not interested", "no thanks", "unsubscribe"],
        "action": "stop_forever",
        "lead_status": "declined",
        "description": "Клиент явно отказал — НИКОГДА больше не писать",
    },
    "max_touches": {
        "max_count": 3,  # Макс. 3 касания (не считая первый контакт)
        "action": "stop",
        "lead_status": "no_response",
        "description": "Исчерпаны все попытки",
    },
    "bounce": {
        "triggers": ["hard_bounce", "privacy_restricted", "user_not_found"],
        "action": "stop",
        "lead_status": "no_contact",
        "description": "Технически невозможно связаться",
    },
    "replied": {
        "action": "transition_to_sales",
        "description": "Клиент ответил → передать SalesAgent",
    },
}
```

### Touch Sequence Manager Implementation

```python
class TouchSequenceManager:
    """Управляет сериями касаний для лидов без ответа."""

    async def check_and_execute(self) -> list[TouchResult]:
        """Проверяет все активные touch sequences и выполняет нужные.

        Вызывается cron-задачей каждые 30 минут.
        """
        results: list[TouchResult] = []

        # Найти все лиды с активной touch sequence
        active_leads = await self.db.fetch_leads(
            touch_state=["active", "pending"],
        )

        for lead in active_leads:
            # Проверить стоп-правила
            stop = await self._check_stop_rules(lead)
            if stop:
                await self._apply_stop(lead, stop)
                results.append(TouchResult(lead_id=lead.id, action="stopped", reason=stop))
                continue

            # Проверить ответил ли клиент
            replied = await self._check_for_reply(lead)
            if replied:
                await self._transition_to_sales(lead)
                results.append(TouchResult(lead_id=lead.id, action="replied"))
                continue

            # Найти следующий touch step
            step = self._find_next_step(lead)
            if step is None:
                # Все касания исчерпаны
                await self._complete_sequence(lead)
                results.append(TouchResult(lead_id=lead.id, action="completed"))
                continue

            # Проверить что пришло время
            days_since_last = self._days_since_last_touch(lead)
            if days_since_last < step.day - (lead.touch_count or 0):
                continue  # Ещё рано

            # Определить канал
            channel = self._resolve_channel(step, lead)

            # Генерировать и отправить сообщение
            if step.auto_send and step.text_template:
                text = step.text_template.format(
                    business_name=lead.business_name,
                    operator_name=self.settings.operator_name,
                    first_channel=lead.channel_used or "Telegram",
                )
                send_result = await self._send_touch(lead, channel, text)

                if send_result["status"] == "sent":
                    await self._record_touch(lead, step, channel)
                    results.append(TouchResult(
                        lead_id=lead.id, action="sent", step=step.template, channel=channel,
                    ))
                else:
                    results.append(TouchResult(
                        lead_id=lead.id, action="failed", error=send_result.get("error"),
                    ))

        return results

    def _resolve_channel(self, step: TouchStep, lead: Lead) -> str:
        """Определяет канал для текущего шага."""
        match step.channel:
            case "best_available":
                if lead.telegram_username:
                    return "telegram"
                if lead.email:
                    return "email"
                return "email"  # fallback
            case "same_as_first":
                return lead.channel_used or "email"
            case "alternative":
                if lead.channel_used == "telegram" and lead.email:
                    return "email"
                if lead.channel_used == "email" and lead.telegram_username:
                    return "telegram"
                return lead.channel_used or "email"  # fallback to same
            case _:
                return step.channel

    def _find_next_step(self, lead: Lead) -> TouchStep | None:
        """Находит следующий шаг по touch_count."""
        touch_index = lead.touch_count or 0
        if touch_index >= len(TOUCH_SCHEDULE):
            return None
        return TOUCH_SCHEDULE[touch_index]

    async def _transition_to_sales(self, lead: Lead) -> None:
        """Клиент ответил — передать SalesAgent."""
        await self.db.update_lead(lead.id,
            touch_state="replied",
            status="negotiating",
        )
        # Создать Deal если ещё нет
        deal = await self.db.create_deal(
            lead_id=lead.id,
            source="pipeline_b",
            status="negotiating",
        )
        # Запустить SalesAgent conversation engine
        await self.sales_engine.start_conversation(deal, lead)
```

### DB Changes

```sql
-- Новые поля в leads для Touch Sequence
ALTER TABLE leads ADD COLUMN IF NOT EXISTS touch_count INTEGER DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS touch_state VARCHAR(20) DEFAULT 'pending'
    CHECK (touch_state IN ('pending', 'active', 'replied', 'completed', 'stopped', 'paused'));
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS channel_used VARCHAR(20);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS next_touch_at TIMESTAMPTZ;

-- Touch history
CREATE TABLE touch_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id     UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    step_index  INTEGER NOT NULL,
    template    VARCHAR(50) NOT NULL,
    channel     VARCHAR(20) NOT NULL,
    content     TEXT,
    status      VARCHAR(20) NOT NULL DEFAULT 'sent'
                CHECK (status IN ('sent', 'delivered', 'failed', 'bounced')),
    external_id VARCHAR(255),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_touch_history_lead ON touch_history(lead_id, created_at);
```

---

## Deal Memory

### Таблица `deals`

```sql
CREATE TABLE deals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    source          VARCHAR(20) NOT NULL CHECK (source IN ('pipeline_a', 'pipeline_b')),
    status          VARCHAR(30) NOT NULL DEFAULT 'new'
                    CHECK (status IN (
                        'new', 'negotiating', 'concept', 'concept_sent',
                        'design', 'design_approved', 'development',
                        'completed', 'lost', 'cancelled'
                    )),
    sales_stage     VARCHAR(30),  -- SalesAgent stage (first_contact, discovery, etc.)
    agreed_scope    TEXT,
    concept_title   VARCHAR(500),
    concept_data    JSONB,        -- ConceptData
    agreed_cost     DECIMAL(10,2),
    agreed_timeline VARCHAR(100),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deals_status ON deals(status) WHERE status NOT IN ('completed', 'lost', 'cancelled');
CREATE INDEX idx_deals_lead ON deals(lead_id);
```

### Таблица `client_context` (Deal Memory)

```sql
CREATE TABLE client_context (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    deal_id             UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    key_facts           JSONB NOT NULL DEFAULT '{}',
    agreed_scope        TEXT,
    decisions           JSONB NOT NULL DEFAULT '[]',
    design_versions     JSONB NOT NULL DEFAULT '[]',
    client_preferences  JSONB NOT NULL DEFAULT '{}',
    conversation_summary TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(deal_id)
);

CREATE INDEX idx_client_context_deal ON client_context(deal_id);
```

---

## API Endpoints

```python
# === Pipeline B Routes ===

# GET /api/v1/pipeline-b/leads
# Список лидов с фильтрацией
# Query: temperature, status, city, category, source, sort_by
# Response: list[LeadSummary]

# GET /api/v1/pipeline-b/leads/{id}
# Детали лида (Lead Card)
# Response: LeadDetail (analysis, scoring, battlecard, touch_history)

# POST /api/v1/pipeline-b/scan
# Запуск сканирования
# Body: {"mode": "geo"|"web"|"telegram", "city": str, "categories": list[str]}
# Response: {"scan_id": str, "status": "started"}

# GET /api/v1/pipeline-b/scan/{scan_id}/status
# Статус сканирования
# Response: {"status": "running"|"completed", "progress": 0.8, "leads_found": 234}

# GET /api/v1/pipeline-b/deals
# Список сделок (Kanban data)
# Query: status, sort_by
# Response: list[DealSummary]

# GET /api/v1/pipeline-b/deals/{id}
# Детали сделки (с Deal Memory)
# Response: DealDetail (context, messages, concept, stage)

# GET /api/v1/pipeline-b/deals/{id}/messages
# Переписка SalesAgent ↔ клиент
# Response: list[DealMessage]

# POST /api/v1/pipeline-b/deals/{id}/messages
# Оператор отправляет сообщение (override)
# Body: {"content": str, "channel": str}

# GET /api/v1/pipeline-b/analytics
# Аналитика Pipeline B
# Query: days (default 30)
# Response: PipelineBAnalytics

# POST /api/v1/pipeline-b/touch-sequence/{lead_id}/pause
# Приостановить touch sequence для лида

# POST /api/v1/pipeline-b/touch-sequence/{lead_id}/resume
# Возобновить touch sequence
```

---

## Конфигурация

### Environment Variables

```bash
# SalesAgent
SALES_AGENT_LLM_MODEL=anthropic/claude-opus-4-6
SALES_AGENT_TEMPERATURE=0.6
SALES_AGENT_MAX_TOKENS=1200
OPERATOR_NAME=Алексей

# Business Analyzer
BA_LIGHTHOUSE_API_KEY=              # Google PageSpeed Insights API
BA_SIMILARWEB_API_KEY=              # SimilarWeb API (optional, deep tier)
BA_WAPPALYZER_API_KEY=              # Wappalyzer API (optional, deep tier)
BA_QUICK_TIMEOUT_SECONDS=10
BA_MEDIUM_TIMEOUT_SECONDS=60
BA_DEEP_TIMEOUT_SECONDS=300

# Lead Scorer
MIN_SCORE_FOR_HITL=3               # Минимум для отправки в HITL
HOT_SCORE_THRESHOLD=5
WARM_SCORE_THRESHOLD=3

# Touch Sequence
TOUCH_CHECK_INTERVAL_SECONDS=1800   # 30 мин
MAX_TOUCHES_PER_LEAD=3
TOUCH_STOP_ON_EXPLICIT_NO=true

# Capacity
MAX_ACTIVE_DEALS=5                  # Phase 1 default
```

---

## Зависимости

| Компонент | Спека |
|-----------|-------|
| GeoScout Agent | `specs/geo-scout-spec.md` — обнаружение лидов |
| Outreach Agent | `specs/outreach-spec.md` — enrichment, email/TG sending |
| HITL System | `specs/hitl-spec.md` — Lead Card, concept review |
| Negotiation Engine | `specs/negotiation-spec.md` — state machine pattern (вдохновение) |
| Enrichment | `specs/enrichment-spec.md` — waterfall, email warmup |
| Platform Adapters | `specs/platform-adapters-spec.md` — Telegram sender |
| RAG Memory | `specs/rag-memory-spec.md` — RAG для Battlecard objection handlers |
| Pipeline A | `pipeline-a-spec.md` — после concept_approved, Dev Cycle flow |

---

## Ключевые файлы

| Компонент | Файл |
|-----------|------|
| SalesAgent | `src/agents/sales_agent.py` |
| Sales Prompts | `src/prompts/sales.py` |
| Business Analyzer | `src/core/business_analyzer.py` |
| Lead Scorer | `src/core/lead_scorer.py` |
| Touch Sequence | `src/core/touch_sequence.py` |
| Deal Memory | `src/core/deal_memory.py` |
| Pipeline B Routes | `src/api/routes/pipeline_b.py` |
| SalesAgent Conversation Engine | `src/negotiations/sales_conversation.py` |
| Deals Migration | `alembic/versions/YYYYMMDD_deals_and_context.py` |
| Touch History Migration | `alembic/versions/YYYYMMDD_touch_history.py` |
| Dashboard Pipeline B | `dashboard/app/routes/_app.pipeline-b.tsx` |
| Dashboard Lead Card | `dashboard/app/components/lead-card.tsx` |
| Dashboard Deal Chat | `dashboard/app/components/deal-chat.tsx` |
