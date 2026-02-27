# Self-Improvement Agent — Идея

> Статус: ИДЕЯ (backlog, не для немедленной реализации)
> Дата: 2026-02-27

## Концепция

Агент, который расширяет возможности MAS автоматически: отслеживает, что система умеет, находит недостающие инструменты и устанавливает их через HITL.

**Ключевое ограничение:** агент НЕ редактирует код MAS. Работает только с конфигурацией и БД.

## Архитектура

### 1. Capability Registry (БД)

```sql
CREATE TABLE capabilities (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,           -- 'figma_export', 'stripe_integration'
    category TEXT NOT NULL,       -- 'mcp_server', 'api_key', 'skill', 'tool'
    provider TEXT,                -- 'figma', 'stripe', 'puppeteer'
    status TEXT DEFAULT 'available', -- 'available', 'installing', 'failed'
    confidence FLOAT DEFAULT 0.5,   -- 0.0-1.0, растёт с опытом
    config JSONB,                -- конфигурация (API keys, endpoints)
    installed_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    projects_completed INT DEFAULT 0
);
```

### 2. Capability Check (перед Bid)

Новый шаг в Pipeline A между Scout и Bid:

```
Scout → Capability Check → Bid
```

- Анализ требований проекта (технологии, инструменты, интеграции)
- Сопоставление с Capability Registry
- Результат: `can_do: bool`, `missing_capabilities: list`, `confidence: float`
- Если `confidence < 0.3` → skip проект
- Если `confidence 0.3-0.7` → пометить в Bid как "частичное покрытие"
- Если `confidence > 0.7` → полный Bid

### 3. MCP Registry API

Использование публичного реестра MCP серверов для поиска инструментов:

```
GET https://registry.modelcontextprotocol.io/v0/servers?search=figma
```

- Поиск по ключевым словам из требований проекта
- Проверка совместимости (Python, Node.js)
- Оценка quality (stars, downloads, last update)

### 4. Self-Install через HITL

Workflow при обнаружении недостающей capability:

```
1. Capability Check → missing: "figma_export"
2. MCP Registry → найден: "@anthropic/mcp-server-figma"
3. → HITL запрос: "Для проекта X нужен Figma export.
   Найден MCP сервер: @anthropic/mcp-server-figma (★4.5, 10k downloads).
   Установить? [Да/Нет/Позже]"
4. Одобрение → npx install → обновить .mcp.json → обновить Registry
5. Отклонение → пометить capability как 'skipped', снизить confidence для подобных проектов
```

### 5. Experience Memory

После завершения проекта (Packager approve):

- Обновить `confidence` для использованных capabilities (+0.1 при успехе, -0.1 при проблемах)
- Записать `projects_completed++`
- Обновить `last_used_at`
- Если использованы новые инструменты, не из Registry → добавить как capability

## Workflow

```
[Проект завершён]
     ↓
[Experience Update]
     ↓
confidence++, projects_completed++
     ↓
[Новый проект из Scout]
     ↓
[Capability Check]
     ├── Всё есть → Bid (full confidence)
     ├── Частично → Bid (with caveats) + MCP Search
     └── Мало → Skip или HITL "стоит ли браться?"
          ↓
     [MCP Registry Search]
          ↓
     [HITL: установить инструмент?]
          ├── Да → Install → Registry Update → Re-check
          └── Нет → Skip / Lower confidence
```

## Что НЕ делает агент

- Не редактирует исходный код MAS
- Не устанавливает без HITL-одобрения
- Не принимает решения о pricing (это Bid Agent)
- Не взаимодействует с клиентами

## Оценка

| Компонент | Усилия | Зависимости |
|-----------|--------|-------------|
| Capability Registry (БД + CRUD) | 3-4 часа | Alembic миграция |
| Capability Check node | 4-6 часов | Registry, LLM |
| MCP Registry интеграция | 2-3 часа | HTTP client |
| Self-Install workflow | 6-8 часов | HITL, npm/npx |
| Experience Memory | 3-4 часа | Packager hook |
| **Итого** | **~3 дня** | — |

## Риски

- MCP Registry API может измениться (v0 = beta)
- Не все MCP серверы качественные — нужен whitelist/blacklist
- Автоустановка npm пакетов = security concern → HITL обязателен
- Confidence scoring нужно калибровать на реальных проектах
