# Research Summary: Ключевые паттерны для MAS

> Извлечённые релевантные паттерны из `research.md` для Multi-Agent Service

---

## 1. Tool Calling / Function Calling

**Применение**: Все агенты (Scout, Critic, Content, Bid) для работы с API.

```python
# Схема инструмента
tools = [{
    "type": "function",
    "function": {
        "name": "search_upwork_jobs",
        "description": "Поиск вакансий на Upwork по фильтрам",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "budget_min": {"type": "integer"},
                "category": {"type": "string"}
            },
            "required": ["query"]
        }
    }
}]
```

**Best Practice**: Описания инструментов должны быть детальными — LLM принимает решение на их основе.

---

## 2. ReAct Pattern (Reason + Act)

**Применение**: Planner Agent для декомпозиции задач.

```
Thought: Нужно найти проект → проанализировать → написать бид → отправить
Action: search_jobs(query="flutter developer")
Observation: Найдено 5 проектов
Thought: Нужно проанализировать каждый на соответствие
Action: analyze_job(job_id="123")
...
```

**Формат промпта**:
```
Ты — планировщик задач. Отвечай в формате:
Thought: [рассуждение]
Action: [название_инструмента(параметры)]
Observation: [результат]
... (повторяй до завершения)
Final Answer: [итоговый результат]
```

---

## 3. Memory Management

**Рекомендация для MAS**: `ConversationSummaryBufferMemory`

| Длина сессии | Тип памяти |
|--------------|-----------|
| <10 turns | ConversationBufferMemory |
| 10-50 turns | ConversationBufferWindowMemory (k=5-10) |
| 50-200 turns | **ConversationSummaryBufferMemory** ← для агентов |
| 200+ turns | VectorStoreRetrieverMemory |

**Код**:
```python
from langchain.memory import ConversationSummaryBufferMemory

memory = ConversationSummaryBufferMemory(
    llm=llm,
    max_token_limit=500  # Суммаризируем при превышении
)
```

---

## 4. Async Programming для API Calls

**Применение**: Параллельные вызовы к моделям (OpenRouter, Anthropic, Google).

```python
import asyncio

async def call_model(prompt, client, semaphore):
    async with semaphore:  # Rate limiting
        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

async def main():
    semaphore = asyncio.Semaphore(5)  # Max 5 concurrent
    tasks = [call_model(p, client, semaphore) for p in prompts]
    results = await asyncio.gather(*tasks)
```

**Результат**: 3 запроса по 2сек → 2сек вместо 6сек.

---

## 5. Tiered Model Architecture (Router Pattern)

**Уже реализовано в `model_selection.md`**. Подтверждение правильности подхода:

```python
def route_to_model(task_complexity):
    if complexity == "simple":
        return "gemini-3-flash"  # Дешёвый
    elif complexity == "medium":
        return "gemini-3-flash"     # Баланс
    else:
        return "gemini-3-pro"       # Качество
```

**Экономия**: 40-60% cost reduction при правильном роутинге.

---

## 6. Token Optimization

### Техники:

1. **Output Length Control**
   ```python
   response = client.chat.completions.create(
       model="gpt-4",
       messages=[...],
       max_tokens=200  # Жёсткий лимит
   )
   ```

2. **Prompt Compression**
   ```python
   # ❌ Verbose
   "I would like you to carefully read through the following..."
   
   # ✅ Compressed
   "Summarize key points clearly:"
   ```

3. **Response Caching** (Redis)
   ```python
   cache_key = hashlib.md5(f"{model}:{prompt}".encode()).hexdigest()
   cached = redis_client.get(cache_key)
   if cached:
       return json.loads(cached)
   ```

---

## 7. Prompt Versioning

**Структура**:
```
prompts/
├── planner/
│   ├── v1.0.0.txt
│   └── v1.1.0.txt
├── analyst/
│   └── v1.0.0.txt
└── metadata.json
```

**Semantic Versioning**:
- `X.0.0` — Breaking changes
- `0.Y.0` — New features
- `0.0.Z` — Bug fixes

---

## 8. Production Monitoring Metrics

| Метрика | Target | Alert |
|---------|--------|-------|
| Accuracy | ≥95% | <90% |
| Response time (p50) | <500ms | >1000ms |
| Error rate | <5% | >10% |
| Uptime | 99.9% | <99.5% |
| Cost per request | <$0.05 | >$0.10 |

---

## 9. Rate Limiting с Semaphore

**Применение**: Защита от 429 Too Many Requests.

```python
class RateLimiter:
    def __init__(self, max_concurrent=5):
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def call(self, func, *args):
        async with self.semaphore:
            return await func(*args)
```

---

## 10. Error Handling Pattern

```python
async def safe_llm_call(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await asyncio.wait_for(
                call_model(prompt),
                timeout=30
            )
        except asyncio.TimeoutError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

---

## Quick Reference

### Применимость к агентам MAS:

| Агент | Ключевые паттерны |
|-------|------------------|
| **Planner Agent** | ReAct, Memory, Prompt Versioning |
| **Scout** | Tool Calling, Async, Rate Limiting |
| **Critic** | ReAct, Token Optimization |
| **Content** | Output Length Control, Caching |
| **Bid** | Tool Calling, Error Handling |

---

*Источник: `research.md` (2679 строк) → сжато до ~150 строк*
