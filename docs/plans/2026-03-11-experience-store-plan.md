# Experience Store — Implementation Plan

**Task**: Phase 2, Task #8 from gap-analysis.md
**Spec**: `docs/Full_work/specs/rag-memory-spec.md` §Experience Store
**Date**: 2026-03-11

## Problem

After HITL final approval, project experiences (successful bids, code patterns,
estimation accuracy) are lost. Agents can't learn from past outcomes.

## Design

Per MASTER-VISION §I.15: `experience_store` is a **subset of `knowledge_base`
with `type='experience'`**. No separate table needed.

### Module: `src/knowledge/experience_store.py`

**Constants:**
- `EXPERIENCE_TYPE = "experience"` — knowledge_base.type value
- `EXPERIENCE_CATEGORIES`: bid, code, negotiation, estimation, client_feedback

**Class: ExperienceStore**

Constructor:
- `embedding_service: EmbeddingService`
- `db_pool: asyncpg.Pool`

Methods:

1. `save_experience(category, title, content, metadata=None, success_score=None) -> uuid`
   - Embed content via EmbeddingService
   - INSERT into knowledge_base with type='experience'
   - Set category, success_rate from success_score
   - Returns entry UUID

2. `retrieve_similar(query, category=None, top_k=5, min_similarity=0.65) -> list[ExperienceResult]`
   - Embed query
   - Vector search with cosine similarity against type='experience'
   - Optional category filter
   - Rerank by weighted score: 0.7 * similarity + 0.3 * success_rate
   - Return top_k results

3. `record_outcome(experience_id, success: bool) -> None`
   - Update success_rate and usage_count for an experience entry
   - Incremental average: `new_rate = (old_rate * count + score) / (count + 1)`

4. `get_by_category(category, limit=10) -> list[ExperienceResult]`
   - Fetch experiences by category, ordered by success_rate desc

5. `delete_experience(experience_id) -> bool`
   - DELETE from knowledge_base WHERE id AND type='experience'

### Data Class

```python
@dataclass
class ExperienceResult:
    id: str
    category: str | None
    title: str
    content: str
    similarity: float | None
    success_rate: float | None
    usage_count: int
```

### Edge Cases

- Empty content → raise ValueError
- Unknown category → accept (no hard validation, extensible)
- No embedding service available → raise RuntimeError
- success_score out of [0,1] → clamp to range
- No results → return empty list
- experience_id not found → return False from delete

### Files

| File | Change |
|------|--------|
| `src/knowledge/experience_store.py` | NEW — main module |
| `tests/unit/test_experience_store.py` | NEW — test suite |
