# Аудит документации MAS — 2026-03-03

## Общий статус

| Область | Оценка | Проблемы |
|---------|--------|----------|
| Docs-First Gate | **0/12** | Оба файла — пустые шаблоны |
| Точность существующих docs | **~65%** | 6 противоречий, устаревшие данные |
| Полнота | **warn** | 3 отсутствующих файла, 4 недокументированных API |
| Дизайн-документы | **9/9 актуальны** | 2 MVP, 5 ожидают реализации |

---

## Критические противоречия (6)

| # | Что | Где противоречие |
|---|-----|-----------------|
| 1 | **Количество тестов** | CLAUDE.md: "2087+", MEMORY.md: "2330", факт: **2483** |
| 2 | **LLM-модели агентов** | Agent specs: "Gemini 3 Flash", TECH_STACK.md (авторитет): **"DeepSeek V3.2"** |
| 3 | **Деплой** | deployment.md: "Hetzner/DigitalOcean VPS", факт: **Railway** |
| 4 | **API campaigns** | api_specification.md: "не реализовано", факт: **CampaignController работает** |
| 5 | **Таблицы БД** | database_schema.md: "18 таблиц" (документирует 17), факт: **21 модель в models.py** |
| 6 | **Stealth API** | TECH_STACK.md: `stealth_async(page)`, факт: `Stealth().apply_stealth_async(page)` |

---

## Отсутствующее в документации

### Недокументированные API-роуты (4)

| Route file | Path | Документирован? |
|------------|------|----------------|
| `src/api/routes/settings.py` | `/api/v1/settings` | НЕТ |
| `src/api/routes/orchestrator.py` | `/api/v1/orchestrator` | НЕТ |
| `src/api/routes/campaigns.py` | `/api/v1/campaigns` | НЕТ |
| `src/api/routes/telegram_channels.py` | `/api/v1/telegram-channels` | НЕТ |

### Недокументированные колонки БД (5)

| Колонка | Таблица | Файл |
|---------|---------|------|
| `status` | `users` | `src/core/models.py:78` |
| `platform_bid_id` | `bids` | `src/core/models.py:197` |
| `channel_type` | `campaign_leads` | `src/core/models.py:583` |
| `telegram_username` | `leads` | `src/core/models.py:499` |
| `enrichment_source` | `leads` | `src/core/models.py` |

### Полностью отсутствующие таблицы в schema docs (3)

- `orchestrator_goals` (table 18)
- `telegram_channels` (table 19)
- `langgraph_checkpoint_history` (table 14b)

### Отсутствующие файлы

- **`docs/email_warmup.md`** — ссылка в CLAUDE.md, файл не существует
- **`docs/features/`** — директория пуста (нет feature specs)
- **Railway deployment guide** — только разрозненные заметки в `.claude/rules/debugging.md`

### Фантомная платформа

- **Fiverr** — в UI настроек (`settings.py:59`), но нет адаптера, нет документации, нет поддержки в Scout

---

## Рекомендации (по приоритету)

| # | Действие | Импакт | Время |
|---|----------|--------|-------|
| 1 | Fix LLM model assignments в agent spec файлах (Gemini → DeepSeek V3.2) | High | 5 мин |
| 2 | Update test count в CLAUDE.md (2087 → 2483+) и MEMORY.md | High | 2 мин |
| 3 | Sync database_schema.md с models.py (+5 колонок, +3 таблицы) | High | 30 мин |
| 4 | Документировать 4 отсутствующих API-роута | High | 1 час |
| 5 | Создать `docs/email_warmup.md` или убрать ссылку | Medium | 15 мин |
| 6 | Fix TECH_STACK.md stealth API sample | Medium | 5 мин |
| 7 | Добавить Fiverr в docs или убрать из settings.py | Medium | 10 мин |
| 8 | Написать Railway deployment doc | Medium | 30 мин |
| 9 | Заполнить docs-first gate файлы (Overview.md, TechSpec.md) | Medium | 1 час |
| 10 | Консолидировать 3 файла agent specs | Low | 15 мин |
| 11 | Обновить deployment.md target (VPS → Railway) | Low | 5 мин |
| 12 | Обновить testing_strategy.md (black → ruff, примеры тестов) | Low | 20 мин |
