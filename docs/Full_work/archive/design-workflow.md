# Design Workflow & Pencil.dev Integration

> Дата: 2026-02-25 | Статус: Дизайн | Автор: Brainstorm Session

## 1. Проблема

В текущем Pipeline A Design Agent работает **параллельно** с Dev Agent:

```
Bid → [HITL] → Planner → Design + Dev + Content (параллельно) → Critic → [HITL] → Packager
```

В реальном фрилансе это работает иначе:
- **Дизайн утверждается клиентом отдельно и ДО разработки**
- Dev может кодить по не утверждённому макету → клиент просит переделать → двойная работа
- Нет шага "клиент ревьюит дизайн" — HITL только в конце
- Revision loop для дизайна (1-3 раунда правок) — стандарт индустрии

## 2. Правильный Pipeline

```
Bid → [HITL: принять заказ] →
  Planner →
  Design Agent → [HITL: ревью + ревизии (1-3 раунда)] →
  [HITL: клиент утверждает дизайн] →
  Dev + Content (параллельно) →
  Critic → [HITL: финальная сдача] →
  Packager
```

### Ключевые изменения

| Аспект | Было | Стало |
|--------|------|-------|
| HITL-точки | 2 (bid + delivery) | 3-4 (bid + design review + design approval + delivery) |
| Design vs Dev | Параллельно | Design строго ДО Dev |
| Design revision | Нет | 1-3 раунда правок |
| Клиент видит дизайн | Только в финале | Отдельный этап утверждения |

### HITL-точки (детально)

1. **HITL #1 — Bid Approval**: как сейчас, без изменений
2. **HITL #2 — Design Review** (новый): оператор проверяет макет перед отправкой клиенту
   - Может отправить на ревизию Design Agent'у (до 3 раундов)
   - Может отредактировать вручную в Pencil.dev/Penpot
3. **HITL #3 — Client Design Approval** (новый): клиент утверждает дизайн
   - Approve → Dev начинает работу
   - Request changes → возврат к Design Agent с фидбэком клиента
   - Reject → пересмотр подхода (может вернуться к Planner)
4. **HITL #4 — Final Delivery**: как сейчас, без изменений

### Когда дизайн НЕ нужен

Не все проекты требуют дизайна. Planner определяет `needs_design: bool`:
- **API-only проекты** → `needs_design: false` → прямо к Dev
- **Backend services** → `needs_design: false`
- **Landing pages, UI features, full-stack** → `needs_design: true`
- **При сомнении** → HITL-решение оператора

## 3. Pencil.dev Integration

### Что это

[Pencil.dev](https://pencil.dev) — AI design tool с MCP-интеграцией:
- Генерирует визуальные макеты из текстовых промптов
- Экспортирует в HTML/CSS/React код
- `.pen` файлы живут в git (версионируемые)
- MCP Server позволяет Claude Code напрямую создавать/редактировать дизайны

### Workflow с Pencil.dev

```
Design Agent формирует JSON-спеку дизайна
  ↓
Pencil.dev MCP генерирует визуальный макет (.pen файл)
  ↓
Экспорт: скриншот (PNG) + код (HTML/CSS/React)
  ↓
[HITL #2]: оператор ревьюит в Pencil.dev UI
  ↓ (правки если нужны)
[HITL #3]: клиенту отправляется скриншот + preview link
  ↓ (approve)
Dev Agent получает экспортированный код как базу
```

### Design Agent → Pencil.dev JSON

Design Agent генерирует структурированную спеку:

```json
{
  "project_type": "landing_page",
  "style": {
    "theme": "modern_minimal",
    "colors": ["#1a1a2e", "#16213e", "#0f3460", "#e94560"],
    "typography": "Inter / system-ui"
  },
  "sections": [
    {
      "type": "hero",
      "content": "AI-powered analytics dashboard",
      "elements": ["headline", "subtext", "cta_button", "hero_image"]
    },
    {
      "type": "features",
      "items": 3,
      "layout": "grid"
    }
  ],
  "responsive": true,
  "export_format": "react"
}
```

### .pen Files в Git

```
project/
├── design/
│   ├── v1.pen          # первая версия
│   ├── v2.pen          # после ревизии
│   └── approved.pen    # утверждённая версия
├── design-export/
│   ├── screenshot.png  # для клиента
│   ├── index.html      # экспорт
│   └── components/     # React компоненты
```

## 4. Penpot (альтернатива)

### Что это

[Penpot](https://penpot.app) — open-source design tool:
- Self-hosted (Docker)
- [MCP Server](https://github.com/penpot/penpot-mcp-server) для программатического создания компонентов
- SVG-based — чистый экспорт
- Бесплатный, без ограничений

### Pencil.dev vs Penpot

| Критерий | Pencil.dev | Penpot |
|----------|-----------|--------|
| AI generation | Нативный (промпт → дизайн) | Через MCP (программатический) |
| Code export | HTML/CSS/React | SVG + CSS |
| Self-hosted | Нет (SaaS) | Да (Docker) |
| Git integration | .pen файлы | API-based |
| Стоимость | Freemium | Бесплатный |
| Кривая обучения | Низкая (AI делает) | Средняя (нужна структура) |
| Клиент может ревьюить | Да (share link) | Да (share link) |

### Рекомендация

**MVP: Pencil.dev** — быстрее запустить, AI-native генерация.
**Позже: Penpot** — для self-hosted контроля и сложных проектов.
**Гибрид возможен** — Design Agent выбирает инструмент по типу проекта.

## 5. Изменения в коде (при реализации)

### State

Новые поля в `AgentState`:

```python
# Design workflow
"needs_design": bool,           # Planner решает
"design_spec": dict | None,     # JSON-спека от Design Agent
"design_artifacts": dict | None, # {pen_file, screenshot, code_export}
"design_approved": bool,        # Клиент утвердил
"design_revision": int,         # Счётчик ревизий (0-3)
"design_feedback": str | None,  # Фидбэк от оператора/клиента
```

### Graph routing

```python
def _route_after_planner(state: dict[str, Any]) -> str:
    if state.get("needs_design"):
        return "design"          # → Design Agent
    return "dev_content_fork"    # → Dev + Content параллельно

def _route_after_design(state: dict[str, Any]) -> str:
    if state.get("requires_hitl"):
        return "__end__"         # → HITL: design review
    return "design_approval"     # → автоматом к клиенту (если skip review)

def _route_design_approval(state: dict[str, Any]) -> str:
    if state.get("design_approved"):
        return "dev_content_fork"  # → Dev + Content
    if state.get("design_revision", 0) >= 3:
        return "__end__"           # → эскалация к оператору
    return "design"                # → ещё раунд ревизии
```

### HITL entries

```python
# Design review (оператор)
HITLQueue(
    action_type="design_review",
    payload={
        "design_spec": state["design_spec"],
        "screenshot_url": state["design_artifacts"]["screenshot"],
        "pen_file": state["design_artifacts"]["pen_file"],
        "revision": state["design_revision"],
    },
    status="pending",
)

# Client approval
HITLQueue(
    action_type="design_client_approval",
    payload={
        "screenshot_url": state["design_artifacts"]["screenshot"],
        "preview_link": state["design_artifacts"]["preview_link"],
        "project_brief": state["project_context"]["description"],
    },
    status="pending",
)
```

### Telegram Bot UI

Новые inline-кнопки для design HITL:
- `design_approve` — отправить клиенту
- `design_revise` — вернуть Design Agent'у с комментарием
- `design_edit_manual` — открыть в Pencil.dev для ручного редактирования
- `client_approved` — клиент утвердил
- `client_changes` — клиент просит правки (+ текст фидбэка)
- `client_rejected` — клиент отклонил подход

## 6. Этапы реализации

### Фаза 1: Pipeline Restructure (без AI design tools)
- Добавить `needs_design` в Planner
- Переключить Design → sequential (до Dev)
- Добавить HITL #2 (design review) и #3 (client approval)
- Design Agent генерирует текстовую спеку + wireframe description
- Оператор создаёт дизайн вручную, загружает скриншот

### Фаза 2: Pencil.dev Integration
- Подключить Pencil.dev MCP Server
- Design Agent генерирует JSON → Pencil.dev → визуальный макет
- Автоматический экспорт скриншотов и кода
- Preview links для клиента

### Фаза 3: Penpot + Advanced
- Penpot MCP для сложных проектов
- Design Agent выбирает инструмент (Pencil.dev vs Penpot)
- Component library (повторные элементы)
- Design system enforcement

### Фаза 4: Client Portal
- Клиент ревьюит дизайн через веб-интерфейс (не через оператора)
- Inline-комментарии на макете
- Автоматический сбор фидбэка → Design Agent

## 7. Figma (контекст)

Ранее обсуждалась Figma-интеграция (см. backlog). Pencil.dev/Penpot НЕ заменяют Figma полностью:

| Сценарий | Инструмент |
|----------|-----------|
| Быстрый макет для фрилансера | Pencil.dev (AI-генерация) |
| Сложный UI с design system | Penpot (self-hosted) |
| Клиент дал макет в Figma | Figma API (read-only) |
| Клиент хочет Figma-формат | Экспорт из Penpot → импорт в Figma |

Figma-интеграция остаётся в backlog как отдельная фича для чтения клиентских макетов.
