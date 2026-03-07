# Portfolio Agent — Дизайн-документ

> Автоматическое пополнение портфолио завершёнными проектами. 11-й агент MAS.
> Дата: 2026-02-25

## 1. Проблема

- Портфолио заполняется вручную на 6 платформах отдельно
- Нет единого source of truth — тексты расходятся между платформами
- Завершённые проекты не попадают в портфолио автоматически
- Существующие 10 проектов в `docs/portfolio/*.md` — хорошая база (case study + RU/EN тексты), но формат плоский, нет структуры для автоматизации

## 2. Архитектура: Git-папка как Source of Truth

Ключевая идея: **`portfolio/` в репо — единственный source of truth.** Тексты, скриншоты, метаданные хранятся в git, оттуда публикуются на фриланс-площадки и в Telegram.

```
portfolio/                          # В репо, версионируется
├── projects/
│   ├── 001-restaurant-landing/
│   │   ├── meta.json              # ID, дата, стек, категории, платформы
│   │   ├── description_ru.md      # Описание (RU)
│   │   ├── description_en.md      # Описание (EN)
│   │   ├── screenshots/           # Скриншоты (auto + manual)
│   │   │   ├── desktop.png
│   │   │   ├── mobile.png
│   │   │   └── mockup.png
│   │   └── platform_versions/     # Адаптации под платформы
│   │       ├── ru/                # Русскоязычные платформы
│   │       │   ├── kwork.md
│   │       │   ├── fl_ru.md
│   │       │   └── youdo.md
│   │       └── en/                # Международные
│   │           ├── fiverr.md
│   │           └── freelancer.md
│   └── 002-beauty-salon-bot/
│       └── ...
├── templates/                     # Шаблоны "О себе" для платформ
│   ├── about_ru.md
│   └── about_en.md
└── config.json                    # Настройки: платформы, форматы, лимиты
```

## 3. Pipeline: Packager → Portfolio Agent

```
Packager → HITL(final_review) → [approve] →
  Portfolio Agent:
    1. Классификация проекта (простой/сложный)
    2. IF простой: Playwright автоскриншот deployed URL
       IF сложный: уведомление оператору "загрузи скриншоты"
    3. Генерация описаний (RU + EN)
    4. Адаптация под каждую платформу (тон, длина, формат)
    5. Сохранение в portfolio/ папку (git commit)
  → [HITL: оператор ревьюит материалы]
  → Публикация на платформы (API где есть, ручная инструкция где нет)
```

## 4. Входные данные (из Packager state)

```python
# Доступно после Packager approve:
state["project"]                        # контекст проекта (platform, client, requirements, budget)
state["artifacts"]["packager"][0]        # JSON delivery_info:
#   delivery_id, files_count, includes, delivery_message,
#   readme_content, quality_notes
state["artifacts"]["dev"]               # код
state["artifacts"]["content"]           # тексты
state["artifacts"]["design"]            # дизайн-файлы
```

## 5. Классификация проекта (простой vs сложный)

Portfolio Agent определяет `complexity`:

| Тип | Критерии | Скриншоты |
|-----|----------|-----------|
| **Простой** (`auto_screenshot: true`) | Лендинги, статические сайты, простые UI | Playwright автоскриншот |
| **Сложный** (`auto_screenshot: false`) | CRM, дашборды с auth, боты, CLI | Уведомление оператору |

Критерии определения:
- `includes` из delivery_info (frontend → простой, backend-only → сложный)
- Тип проекта из `project.requirements`
- Наличие deployed URL

## 6. Скриншоты

### Автоматические (Playwright)

- Открыть deployed URL (из `state["project"]`)
- Desktop viewport (1440x900) + Mobile (390x844)
- Ключевые страницы: главная + 1-2 внутренние
- Сохранить в `portfolio/projects/{id}/screenshots/`
- Оператор может заменить на свои через HITL

### Ручные (оператор)

- Telegram-уведомление: "Проект {name} завершён. Загрузи скриншоты для портфолио"
- Оператор загружает через Dashboard или Telegram
- Agent оформляет в мокапы (Smartmockups API или шаблоны)

## 7. RU vs Международные платформы

Два рынка — два подхода. Agent генерирует **отдельные версии** для каждой группы.

### Русскоязычные (Kwork, FL.ru, YouDo, Telegram RU)

- Описания на русском
- Цены в рублях
- Стек: упоминать знакомые RU-рынку термины (1С, Битрикс, ЮKassa)
- Тон: профессиональный, но доступный
- Акценты: сроки, цена, конкретный результат
- Категории: привязаны к категориям FL.ru/Kwork

### Международные (Fiverr, Freelancer.com, Telegram EN)

- Описания на английском
- Цены в USD
- Стек: международные стандарты (Stripe, AWS, Vercel)
- Тон: professional, service-oriented
- Акценты: quality, methodology, deliverables, response time
- Keywords: SEO-оптимизированы под платформенный поиск

## 8. Генерация описаний (LLM)

**Вход:** `delivery_message` + `readme_content` + `quality_notes` + `project.requirements`

**Два этапа:**
1. **Базовые описания:** `description_ru.md` + `description_en.md` (полные case study)
2. **Платформенные адаптации:** из базовых описаний → сжатие/адаптация под каждую платформу

**Выход для каждой платформы:**

| Платформа | Язык | Стиль | Лимит |
|-----------|------|-------|-------|
| Kwork | RU | Короткий, продающий, фокус на результат | ≤500 символов |
| FL.ru | RU | Профессиональный, с категориями | ≤1000 символов |
| YouDo | RU | Простой, без жаргона, решение проблем | ≤800 символов |
| Fiverr | EN | Дружелюбный, акцент на сервис и пакеты | ≤1200 символов |
| Freelancer.com | EN | Формальный, метрики и methodology | ≤1000 символов |
| Telegram | RU+EN | Свободный формат для постов в канал | без лимита |

## 9. HITL

Всегда HITL перед публикацией:

- Оператор видит: скриншоты + все описания + целевые платформы
- Действия: `approve_all`, `approve_selected`, `edit`, `regenerate`, `reject`
- Может отредактировать любое описание перед публикацией
- Может заменить скриншоты

## 10. Публикация на платформы

| Платформа | Метод | Автоматизация |
|-----------|-------|---------------|
| Freelancer.com | Portfolio API | Полная |
| Fiverr | Seller API | Частичная (исследовать) |
| Kwork | Нет API | Инструкция оператору + clipboard |
| FL.ru | Нет API | Инструкция оператору + clipboard |
| YouDo | Нет API | Инструкция оператору + clipboard |
| Telegram | Bot API | Полная (пост в канал) |
| GitHub | Git push | Полная (portfolio/ папка) |

Для платформ без API: agent генерирует пошаговую инструкцию + копирует тексты в clipboard-ready формат.

## 11. Аудит актуальности портфолио

Периодическая проверка: какие проекты актуальны, какие устарели, что добавить.

### Триггеры аудита

- **По расписанию:** раз в месяц (cron/APScheduler)
- **По событию:** каждые 5 завершённых проектов
- **Вручную:** оператор запускает через Telegram `/portfolio_audit`

### Что анализирует Agent

1. **Свежесть**: проекты старше 6 месяцев → кандидаты на замену
2. **Разнообразие**: покрытие ниш (не 5 лендингов подряд, а микс)
3. **Стек-баланс**: все ключевые технологии представлены
4. **Конверсия** (если трекаем): какие проекты приводят клиентов
5. **Платформенное покрытие**: все ли платформы обновлены

### Выход аудита (→ HITL)

```
Рекомендации:
- ЗАМЕНИТЬ: "Лендинг ресторана" (6 мес, устарел стек) → на новый проект X
- ДОБАВИТЬ: нет AI-проектов в портфолио, добавить "AI-чатбот"
- ОБНОВИТЬ: "CRM автосервиса" — обновить скриншоты (UI изменился)
- УДАЛИТЬ: "Email-бот" — низкая конверсия, не актуален
- ПЛАТФОРМЫ: YouDo не обновлялся 2 мес, синхронизировать
```

HITL обязателен: оператор утверждает каждое изменение. Agent НЕ удаляет/заменяет автоматически.

### meta.json дополнения для аудита

```json
{
  "added_at": "2026-03-15",
  "last_audit": "2026-04-15",
  "conversion_count": 3,
  "status": "active",
  "audit_notes": "Good performer, keep"
}
```

## 12. meta.json структура

```json
{
  "id": "001",
  "delivery_id": "del-xxx",
  "project_id": "proj-xxx",
  "title": "Лендинг ресторана с бронированием",
  "title_en": "Restaurant Landing Page with Booking",
  "date": "2026-03-15",
  "client": "anonymous",
  "stack": ["HTML", "CSS", "JavaScript"],
  "categories": ["landing", "restaurant", "booking"],
  "market": "ru",
  "platforms_published": {
    "ru": ["kwork", "fl_ru", "youdo"],
    "en": ["freelancer"]
  },
  "screenshots": ["desktop.png", "mobile.png"],
  "deployed_url": null,
  "complexity": "simple",
  "auto_screenshot": true,
  "added_at": "2026-03-15",
  "last_audit": null,
  "conversion_count": 0,
  "status": "active",
  "audit_notes": null
}
```

## 13. Agent спецификация

- **Имя:** `portfolio` (11-й агент)
- **Класс:** `PortfolioAgent(ConstrainedAgent)`
- **LLM:** DeepSeek V3.2 (генерация описаний)
- **Файл:** `src/agents/portfolio.py`
- **Вход:** state после Packager approve
- **Выход:** `artifacts["portfolio"]` с meta.json + описания + пути скриншотов
- **HITL:** обязателен перед публикацией

### Методы

```python
class PortfolioAgent(ConstrainedAgent):
    async def _execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Основной flow: classify → screenshot → generate → adapt → save"""

    async def _classify_project(self, delivery_info: dict, project: dict) -> str:
        """Определяет complexity: 'simple' | 'complex'"""

    async def _take_screenshots(self, url: str, project_dir: Path) -> list[str]:
        """Playwright скриншоты для простых проектов"""

    async def _generate_descriptions(self, state: dict) -> dict:
        """LLM генерация базовых описаний RU + EN"""

    async def _adapt_for_platforms(self, descriptions: dict) -> dict:
        """Адаптация под каждую платформу из config"""

    async def _save_to_portfolio(self, project_data: dict) -> Path:
        """Сохранение в portfolio/ папку"""
```

### Node function

```python
async def portfolio_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node для Portfolio Agent"""
```

## 14. Этапы реализации

### Фаза 1: Git-папка + миграция существующих 10 проектов

- Создать структуру `portfolio/` с `config.json`
- Мигрировать 10 проектов из `docs/portfolio/*.md` в новый формат:
  - Каждый `.md` → папка с `meta.json` + `description_ru.md` + `description_en.md`
  - Вытащить из каждого: клиент, задача, решение, результат, стек, сроки → `meta.json`
  - "Текст для платформы (RU)" → `platform_versions/ru/` (нарезать под Kwork/FL.ru/YouDo)
  - "Platform Text (EN)" → `platform_versions/en/` (нарезать под Fiverr/Freelancer)
- Существующие тексты — основа, не переписываем, только структурируем
- `docs/portfolio/` → оставить как архив или удалить (решение оператора)

### Фаза 2: Portfolio Agent (генерация)

- Агент: классификация, генерация описаний RU+EN, адаптация под платформы
- Playwright автоскриншоты для простых проектов
- HITL через Telegram Bot

### Фаза 3: Публикация

- Freelancer.com API интеграция
- Telegram Bot API (посты в канал)
- Clipboard-ready инструкции для платформ без API

### Фаза 4: Автоматический триггер

- Packager approve → автоматический запуск Portfolio Agent
- Интеграция в Pipeline A граф

### Фаза 5: Аудит актуальности

- Месячный cron-аудит портфолио
- Анализ свежести, разнообразия, конверсии
- Рекомендации → HITL → обновление
