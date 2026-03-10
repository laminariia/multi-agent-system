# P1.4: delivery_type в Planner + Packager

**Дата:** 2026-03-10
**Приоритет:** P1 (Ядро видения)
**Effort:** 3ч
**Зависимости:** P0 Dynamic Routing (DONE)

---

## Проблема

Сейчас Packager обрабатывает все проекты одинаково — собирает артефакты и генерирует
generic delivery message. Но разные типы проектов требуют разных deliverables:

- Лендинг → deploy (ссылка + настройки)
- Telegram бот → files + credentials (код + токены)
- Брендбук → files (дизайн-файлы)
- Аудит → instructions (документация)
- CRM под ключ → mixed (код + доступы + инструкция)

Поле `delivery_type` уже есть в state (default: "files"), Planner уже пробрасывает
его из JSON плана, но:
1. Planner prompt НЕ просит LLM указать delivery_type
2. Planner НЕ инферит delivery_type из requirements если LLM не вернул
3. Packager ПОЛНОСТЬЮ игнорирует delivery_type — одна логика для всех

---

## Архитектура решения

### 1. Валидация delivery_type (state.py)

Добавить константу `VALID_DELIVERY_TYPES` и валидирующую функцию.

```python
VALID_DELIVERY_TYPES: frozenset[str] = frozenset({
    "files", "credentials", "deploy", "instructions", "mixed",
})

def validate_delivery_type(value: str) -> str:
    """Return validated delivery_type or 'files' as fallback."""
    return value if value in VALID_DELIVERY_TYPES else "files"
```

### 2. Planner prompt (prompts/planner.py)

Добавить в output format:
- `delivery_type` field с описанием значений
- Правила выбора delivery_type на основе типа проекта

### 3. Planner agent (agents/planner.py)

Добавить `_infer_delivery_type()` — fallback если LLM не вернул delivery_type:
- Анализирует requirements по ключевым словам
- deploy: "лендинг", "сайт", "deploy", "хостинг", "домен"
- credentials: "бот", "telegram", "api", "сервис", "аккаунт", "token"
- instructions: "аудит", "консалтинг", "анализ", "рекомендации", "план"
- files: default (код, дизайн, контент)
- mixed: несколько типов одновременно

Валидация через `validate_delivery_type()` перед записью в state.

### 4. Packager prompt (prompts/packager.py)

Полная переработка prompt для поддержки delivery_type:
- Отдельные инструкции для каждого типа
- Разные folder structures
- Разные delivery messages
- Специфичные поля в output JSON

### 5. Packager agent (agents/packager.py)

Добавить delivery_type-aware логику:

```python
# Новые методы:
_build_delivery_context(delivery_type, collected) -> str  # контекст для LLM
_validate_delivery_completeness(delivery_type, delivery_info) -> list[str]  # missing items
_build_fallback_delivery(project_id, collected, delivery_type) -> dict  # type-aware fallback
```

Изменения в `_execute()`:
- Читает `delivery_type` из state
- Передаёт в LLM prompt как контекст
- Валидирует completeness по типу
- HITL entry включает delivery_type для оператора

Изменения в `_create_hitl_entry()`:
- delivery_type в title и description
- delivery_type в payload

### 6. Packager response parsing

Новые опциональные поля в parsed response:
- `delivery_type` (echo back)
- `credentials` (для credentials/mixed)
- `deploy_url` (для deploy/mixed)
- `setup_instructions` (для credentials/deploy/instructions/mixed)

---

## Файлы (изменения)

| Файл | Изменения |
|------|-----------|
| `src/core/state.py` | + `VALID_DELIVERY_TYPES`, `validate_delivery_type()` |
| `src/prompts/planner.py` | + delivery_type в output schema |
| `src/agents/planner.py` | + `_infer_delivery_type()`, валидация |
| `src/prompts/packager.py` | Полная переработка под delivery_type |
| `src/agents/packager.py` | + delivery_type-aware packaging |
| `tests/unit/test_delivery_type.py` | Новый файл: все тесты delivery_type |

---

## Edge Cases

1. LLM возвращает невалидный delivery_type (e.g. "pdf") → fallback to "files"
2. delivery_type="deploy" но нет dev артефактов → flag in missing_artifacts
3. delivery_type="credentials" но нет credentials data → flag + HITL
4. delivery_type="mixed" → валидация всех sub-components
5. Empty agent_sequence + delivery_type="instructions" → Packager handles consulting
6. Re-plan после Critic → delivery_type сохраняется (не сбрасывается)
7. HITL plan edit → operator может изменить delivery_type

---

## Тесты (TDD — пишутся ПЕРВЫМИ)

### State validation (5 tests)
- test_valid_delivery_types_constant
- test_validate_delivery_type_valid_values
- test_validate_delivery_type_invalid_fallback
- test_validate_delivery_type_empty_string
- test_create_initial_state_default_delivery_type

### Planner inference (8 tests)
- test_infer_delivery_type_deploy_keywords
- test_infer_delivery_type_credentials_keywords
- test_infer_delivery_type_instructions_keywords
- test_infer_delivery_type_files_default
- test_infer_delivery_type_mixed_multiple_signals
- test_infer_delivery_type_case_insensitive
- test_infer_delivery_type_russian_keywords
- test_planner_sets_delivery_type_from_plan

### Planner prompt (2 tests)
- test_planner_prompt_contains_delivery_type
- test_planner_prompt_lists_valid_values

### Packager delivery_type handling (12 tests)
- test_packager_files_delivery
- test_packager_credentials_delivery
- test_packager_deploy_delivery
- test_packager_instructions_delivery
- test_packager_mixed_delivery
- test_packager_unknown_delivery_type_fallback
- test_packager_delivery_context_includes_type
- test_packager_validates_completeness_files
- test_packager_validates_completeness_credentials_missing
- test_packager_validates_completeness_deploy_missing
- test_packager_hitl_entry_includes_delivery_type
- test_packager_fallback_delivery_type_aware

### Packager response parsing (5 tests)
- test_parse_response_with_credentials_field
- test_parse_response_with_deploy_url
- test_parse_response_with_setup_instructions
- test_parse_response_delivery_type_echo
- test_parse_response_missing_type_specific_fields_defaults

### Integration (3 tests)
- test_planner_to_packager_delivery_type_flow
- test_delivery_type_preserved_after_critic_revision
- test_consulting_project_instructions_delivery

**Total: 35 tests**
