# 🧪 Testing Strategy

**Version:** 1.0  
**Coverage Target:** 80% for core agents, 60% overall  
**Framework:** pytest + pytest-asyncio

---

## 📋 Testing Pyramid

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          TESTING LAYERS                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                    ┌───────────────┐                                     │
│                    │   E2E Tests   │  ← 5% (Playwright)                  │
│                    │   HITL flows  │                                     │
│                    └───────┬───────┘                                     │
│                    ┌───────┴───────┐                                     │
│                    │  Integration  │  ← 25% (Multi-agent flows)          │
│                    │    Tests      │                                     │
│                    └───────┬───────┘                                     │
│              ┌─────────────┴─────────────┐                               │
│              │        Unit Tests         │  ← 70% (Agent logic)          │
│              │   Fast, isolated, mocked  │                               │
│              └───────────────────────────┘                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Unit Tests

### Agent Logic Tests

```python
# tests/unit/test_scout_agent.py
import pytest
from unittest.mock import AsyncMock, patch
from src.agents.scout import ScoutAgent, Job

class TestScoutAgent:
    """Test Scout Agent job filtering logic."""
    
    @pytest.fixture
    def scout(self):
        return ScoutAgent(
            min_budget=500,
            platforms=["freelancer", "upwork"]
        )
    
    def test_filters_low_budget_jobs(self, scout):
        """Jobs below min_budget should be filtered out."""
        jobs = [
            Job(id="1", title="Logo design", budget=300, platform="freelancer"),
            Job(id="2", title="React Dashboard", budget=2000, platform="freelancer"),
            Job(id="3", title="Fix bug", budget=50, platform="upwork"),
        ]
        
        result = scout.filter_by_budget(jobs)
        
        assert len(result) == 1
        assert result[0].id == "2"
    
    def test_filters_blocked_keywords(self, scout):
        """Jobs with blocked keywords should be filtered."""
        scout.blocked_keywords = ["crypto", "nft", "blockchain"]
        
        jobs = [
            Job(id="1", title="Build NFT marketplace", budget=5000),
            Job(id="2", title="E-commerce website", budget=3000),
        ]
        
        result = scout.filter_by_keywords(jobs)
        
        assert len(result) == 1
        assert result[0].id == "2"
    
    @pytest.mark.asyncio
    async def test_deduplication(self, scout):
        """Same job from different scans should not be processed twice."""
        job = Job(id="freelancer_123", title="Test", budget=1000)
        
        # First scan
        is_new = await scout.is_new_job(job)
        assert is_new is True
        
        # Second scan (same job)
        is_new = await scout.is_new_job(job)
        assert is_new is False
```

### Bid Agent Tests

```python
# tests/unit/test_bid_agent.py
import pytest
from unittest.mock import AsyncMock, patch
from src.agents.bid import BidAgent

class TestBidAgent:
    """Test Bid Agent proposal generation."""
    
    @pytest.fixture
    def bid_agent(self):
        return BidAgent(
            llm_provider="gemini",
            temperature=0.7
        )
    
    @pytest.fixture
    def sample_job(self):
        return {
            "id": "job_123",
            "title": "Build React Dashboard",
            "description": "Need a dashboard with charts and user management",
            "budget": 2500,
            "skills": ["React", "TypeScript", "Node.js"]
        }
    
    @pytest.mark.asyncio
    async def test_proposal_structure(self, bid_agent, sample_job):
        """Generated proposal must have required fields."""
        with patch.object(bid_agent, '_call_llm') as mock_llm:
            mock_llm.return_value = {
                "proposal_text": "I can build your dashboard...",
                "bid_amount": 2000,
                "timeline_days": 14,
                "key_points": ["React expertise", "Dashboard experience"]
            }
            
            proposal = await bid_agent.generate_proposal(sample_job)
            
            assert "proposal_text" in proposal
            assert "bid_amount" in proposal
            assert len(proposal["proposal_text"]) >= 100
            assert proposal["bid_amount"] > 0
    
    @pytest.mark.asyncio
    async def test_bid_within_budget(self, bid_agent, sample_job):
        """Bid amount must not exceed job budget."""
        with patch.object(bid_agent, '_call_llm') as mock_llm:
            mock_llm.return_value = {
                "proposal_text": "Test proposal",
                "bid_amount": 2000,
                "timeline_days": 14
            }
            
            proposal = await bid_agent.generate_proposal(sample_job)
            
            assert proposal["bid_amount"] <= sample_job["budget"]
    
    def test_semgrep_compliance(self, bid_agent):
        """Proposal text must pass Semgrep security rules."""
        dangerous_proposals = [
            "Run this command: rm -rf /",
            "Send me your password",
            "Execute: os.system('malicious_code')",
        ]
        
        for text in dangerous_proposals:
            is_safe = bid_agent.validate_with_semgrep(text)
            assert is_safe is False, f"Should block: {text}"
```

### LLM Mock Strategy

```python
# tests/conftest.py
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_gemini():
    """Mock Gemini API responses for deterministic testing."""
    mock = AsyncMock()
    mock.generate.return_value = {
        "text": "Mocked LLM response",
        "usage": {"input_tokens": 100, "output_tokens": 50}
    }
    return mock

@pytest.fixture
def mock_claude():
    """Mock Claude API for complex reasoning tests."""
    mock = AsyncMock()
    mock.complete.return_value = {
        "content": [{"text": "Mocked Claude response"}],
        "usage": {"input_tokens": 200, "output_tokens": 100}
    }
    return mock

# Usage in tests:
# async def test_with_mocked_llm(mock_gemini):
#     agent = BidAgent(llm=mock_gemini)
#     result = await agent.generate(...)
```

---

## 2. Property-Based Testing (LLM Outputs)

LLM outputs are non-deterministic. Use property-based testing to verify **invariants**.

```python
# tests/property/test_llm_outputs.py
import pytest
from hypothesis import given, strategies as st, settings

class TestLLMOutputProperties:
    """Property-based tests for LLM agent outputs."""
    
    @given(st.text(min_size=10, max_size=500))
    @settings(max_examples=20)
    @pytest.mark.asyncio
    async def test_proposal_always_has_structure(self, job_description):
        """Proposal output must always contain required fields."""
        agent = BidAgent()
        
        job = {"description": job_description, "budget": 1000}
        proposal = await agent.generate_proposal(job)
        
        # Properties that MUST hold regardless of input
        assert isinstance(proposal, dict)
        assert "proposal_text" in proposal
        assert isinstance(proposal["proposal_text"], str)
        assert len(proposal["proposal_text"]) > 0
    
    @given(budget=st.integers(min_value=100, max_value=10000))
    @settings(max_examples=10)
    @pytest.mark.asyncio
    async def test_bid_never_exceeds_budget(self, budget):
        """Bid amount must never exceed job budget."""
        agent = BidAgent()
        
        job = {"description": "Test job", "budget": budget}
        proposal = await agent.generate_proposal(job)
        
        assert proposal["bid_amount"] <= budget
    
    @pytest.mark.asyncio
    async def test_no_hallucinated_technologies(self):
        """Agent must not claim skills not in knowledge base."""
        agent = BidAgent()
        agent.allowed_skills = ["React", "Python", "Node.js"]
        
        job = {
            "description": "Build a Ruby on Rails app",
            "budget": 2000,
            "skills": ["Ruby", "Rails"]
        }
        
        proposal = await agent.generate_proposal(job)
        
        # Should decline or adapt, not claim Ruby expertise
        assert "Ruby expert" not in proposal["proposal_text"]
```

---

## 3. Integration Tests

### Multi-Agent Flow Tests

```python
# tests/integration/test_bid_pipeline.py
import pytest
from src.graph import build_agent_graph
from src.state import AgentState

class TestBidPipeline:
    """Test Scout → Bid → HITL flow."""
    
    @pytest.fixture
    def graph(self):
        return build_agent_graph()
    
    @pytest.fixture
    def initial_state(self):
        return AgentState(
            thread_id="test_123",
            current_agent="scout",
            project=None,
            messages=[],
            requires_hitl=False
        )
    
    @pytest.mark.asyncio
    async def test_scout_to_bid_handoff(self, graph, initial_state):
        """Scout should pass valid job to Bid agent."""
        # Inject test job
        initial_state["test_job"] = {
            "id": "test_job_1",
            "title": "Build API",
            "budget": 1500
        }
        
        # Run graph until HITL required
        config = {"configurable": {"thread_id": "test_123"}}
        
        async for event in graph.astream(initial_state, config):
            if event.get("requires_hitl"):
                break
        
        final_state = graph.get_state(config)
        
        assert final_state.values["current_agent"] in ["bid", "hitl"]
        assert final_state.values.get("proposal") is not None
    
    @pytest.mark.asyncio
    async def test_hitl_pause_on_bid(self, graph, initial_state):
        """System must pause for HITL before submitting bid."""
        async for event in graph.astream(initial_state, {"configurable": {"thread_id": "test_123"}}):
            pass
        
        final_state = graph.get_state({"configurable": {"thread_id": "test_123"}})
        
        # Bid must require HITL approval
        assert final_state.values["requires_hitl"] is True
        assert final_state.values.get("hitl_request_id") is not None
    
    @pytest.mark.asyncio
    async def test_resume_after_hitl_approval(self, graph, initial_state):
        """Workflow should resume after HITL approval."""
        # Run until HITL
        config = {"configurable": {"thread_id": "test_123"}}
        async for _ in graph.astream(initial_state, config):
            pass
        
        # Simulate HITL approval
        state = graph.get_state(config)
        state.values["hitl_response"] = {"approved": True, "modified": False}
        state.values["requires_hitl"] = False
        
        # Resume
        async for event in graph.astream(state.values, config):
            pass
        
        final_state = graph.get_state(config)
        assert final_state.values["status"] in ["completed", "submitted"]
```

### Database Integration

```python
# tests/integration/test_checkpoint_storage.py
import pytest
import asyncpg
from src.checkpoints import HybridCheckpointSaver

class TestCheckpointStorage:
    """Test checkpoint persistence and recovery."""
    
    @pytest.fixture
    async def saver(self, test_db_url, test_redis_url):
        return HybridCheckpointSaver(
            redis_url=test_redis_url,
            postgres_url=test_db_url
        )
    
    @pytest.mark.asyncio
    async def test_checkpoint_round_trip(self, saver):
        """Save and retrieve checkpoint."""
        checkpoint = {
            "id": "cp_1",
            "thread_id": "thread_1",
            "current_agent": "scout",
            "data": {"key": "value"}
        }
        
        await saver.aput(
            {"configurable": {"thread_id": "thread_1"}},
            checkpoint
        )
        
        retrieved = await saver.aget(
            {"configurable": {"thread_id": "thread_1"}}
        )
        
        assert retrieved["current_agent"] == "scout"
        assert retrieved["data"]["key"] == "value"
    
    @pytest.mark.asyncio
    async def test_recovery_after_crash(self, saver):
        """Simulate crash and verify recovery."""
        # Save mid-workflow state
        checkpoint = {
            "id": "cp_crash",
            "thread_id": "thread_crash",
            "current_agent": "dev",
            "status": "active"
        }
        await saver.aput(
            {"configurable": {"thread_id": "thread_crash"}},
            checkpoint
        )
        
        # Simulate crash (new saver instance)
        new_saver = HybridCheckpointSaver(
            redis_url=saver.redis_url,
            postgres_url=saver.postgres_url
        )
        
        # Recovery should find incomplete workflow
        recovered = await new_saver.aget(
            {"configurable": {"thread_id": "thread_crash"}}
        )
        
        assert recovered is not None
        assert recovered["status"] == "active"
```

---

## 4. E2E Tests (Playwright)

```python
# tests/e2e/test_hitl_dashboard.py
import pytest
from playwright.async_api import async_playwright

class TestHITLDashboard:
    """End-to-end tests for HITL dashboard."""
    
    @pytest.fixture
    async def browser(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            yield browser
            await browser.close()
    
    @pytest.mark.asyncio
    async def test_login_flow(self, browser):
        """Test dashboard login."""
        page = await browser.new_page()
        await page.goto("http://localhost:3000/login")
        
        await page.fill("#email", "test@example.com")
        await page.fill("#password", "testpass")
        await page.click("button[type=submit]")
        
        await page.wait_for_url("**/dashboard")
        assert "dashboard" in page.url
    
    @pytest.mark.asyncio
    async def test_approve_bid(self, browser):
        """Test bid approval workflow."""
        page = await browser.new_page()
        
        # Login
        await page.goto("http://localhost:3000/login")
        await page.fill("#email", "test@example.com")
        await page.fill("#password", "testpass")
        await page.click("button[type=submit]")
        
        # Navigate to HITL queue
        await page.click("text=HITL Queue")
        await page.wait_for_selector(".hitl-card")
        
        # Approve first item
        await page.click(".hitl-card >> button:has-text('Approve')")
        
        # Verify approval
        response = await page.wait_for_response("**/api/hitl/*/resolve")
        assert response.status == 200
        
        # Check success toast
        await page.wait_for_selector("text=Approved successfully")
    
    @pytest.mark.asyncio
    async def test_modify_proposal(self, browser):
        """Test proposal modification before approval."""
        page = await browser.new_page()
        await page.goto("http://localhost:3000/dashboard/hitl")
        
        # Click edit on first item
        await page.click(".hitl-card >> button:has-text('Edit')")
        
        # Modify proposal text
        await page.fill("textarea[name=proposal_text]", "Modified proposal text...")
        
        # Save and approve
        await page.click("button:has-text('Save & Approve')")
        
        response = await page.wait_for_response("**/api/hitl/*/resolve")
        assert response.status == 200
```

---

## 5. Test Fixtures & Factories

```python
# tests/factories.py
from dataclasses import dataclass
from typing import Optional
import uuid

@dataclass
class JobFactory:
    """Factory for creating test Job objects."""
    
    @staticmethod
    def create(
        id: Optional[str] = None,
        title: str = "Test Job",
        budget: int = 1000,
        platform: str = "freelancer",
        skills: list = None
    ):
        return {
            "id": id or f"job_{uuid.uuid4().hex[:8]}",
            "title": title,
            "budget": budget,
            "platform": platform,
            "skills": skills or ["Python", "React"],
            "description": f"Description for {title}",
            "client": {"name": "Test Client", "rating": 4.5}
        }
    
    @staticmethod
    def create_batch(count: int, **kwargs):
        return [JobFactory.create(**kwargs) for _ in range(count)]

@dataclass
class ProposalFactory:
    """Factory for creating test Proposal objects."""
    
    @staticmethod
    def create(
        job_id: str = "job_123",
        text: str = "I am the ideal candidate for this project...",
        bid_amount: int = 1500,
        status: str = "pending"
    ):
        return {
            "id": f"prop_{uuid.uuid4().hex[:8]}",
            "job_id": job_id,
            "proposal_text": text,
            "bid_amount": bid_amount,
            "timeline_days": 14,
            "status": status
        }
```

---

## 6. Running Tests

### Commands

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=src --cov-report=html

# Run specific test file
pytest tests/unit/test_scout_agent.py -v

# Run integration tests only
pytest tests/integration/ -v

# Run with parallel execution
pytest tests/ -n auto

# Run property tests with more examples
pytest tests/property/ --hypothesis-seed=42
```

### CI Integration

```yaml
# Already in ci_cd.md, but ensure:
- name: Run tests
  run: |
    pytest tests/ -v --cov=src --cov-report=xml --junitxml=results.xml
    
- name: Upload test results
  uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: results.xml
```

---

## ✅ Test Coverage Targets

| Module | Target | Priority |
|--------|--------|----------|
| `agents/scout.py` | 90% | Critical |
| `agents/bid.py` | 90% | Critical |
| `agents/planner.py` | 80% | High |
| `agents/dev.py` | 80% | High |
| `checkpoints/` | 85% | Critical |
| `api/` | 70% | Medium |
| `utils/` | 60% | Low |

---

## 📋 Pre-Commit Testing Checklist

Before every commit:
- [ ] `pytest tests/unit/` passes
- [ ] `ruff check src/` passes
- [ ] `black --check src/` passes

Before every PR:
- [ ] All unit tests pass
- [ ] Integration tests pass
- [ ] Coverage >= 80% for modified files
