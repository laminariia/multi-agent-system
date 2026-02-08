# 📚 RAG Knowledge Base Architecture

**Version:** 1.0  
**Purpose:** Structure and manage knowledge for agent context retrieval

---

## 📋 Overview

The RAG (Retrieval-Augmented Generation) system provides agents with relevant context from:
- Successful proposals/bids
- Portfolio projects
- Client communications
- Technical documentation

---

## 🗄️ Knowledge Base Structure

```
knowledge/
├── proposals/                  # Successful bid examples
│   ├── web_development/
│   │   ├── react_dashboard.md
│   │   ├── ecommerce_frontend.md
│   │   └── landing_pages.md
│   ├── mobile/
│   │   ├── flutter_app.md
│   │   └── react_native.md
│   ├── backend/
│   │   ├── api_development.md
│   │   └── database_design.md
│   └── design/
│       ├── ui_ux.md
│       └── branding.md
│
├── portfolio/                  # Completed projects
│   ├── case_studies/
│   │   ├── project_001.json
│   │   ├── project_002.json
│   │   └── ...
│   └── screenshots/           # Visual assets
│
├── clients/                    # Client communication patterns
│   ├── negotiation_templates.md
│   ├── common_questions.md
│   └── objection_handling.md
│
├── technical/                  # Tech documentation
│   ├── stack_guides/
│   │   ├── react_next.md
│   │   ├── python_fastapi.md
│   │   └── flutter.md
│   └── best_practices/
│       ├── code_review.md
│       └── security.md
│
└── outreach/                   # Cold email templates
    ├── industries/
    │   ├── dental_clinics.md
    │   ├── real_estate.md
    │   └── restaurants.md
    └── email_templates/
        ├── initial_contact.md
        └── follow_up.md
```

---

## 🔢 Embedding Strategy

### Model Selection

| Provider | Model | Dimensions | Cost | Speed |
|----------|-------|------------|------|-------|
| Google | text-embedding-004 | 768 | $0.006/1M tokens | Fast |
| OpenAI | text-embedding-3-small | 1536 | $0.02/1M tokens | Fast |
| Cohere | embed-multilingual-v3 | 1024 | $0.10/1M tokens | Fast |
| Local | nomic-embed-text | 768 | Free | Variable |

### Recommendation
```
Primary: Google text-embedding-004
- Best quality/cost ratio for our use case
- Very cost-effective ($0.006/1M tokens)
- 768 dimensions — good balance of quality and performance
- Fast enough for real-time

Fallback: nomic-embed-text (local)
- For offline/development
- No API dependency
```

### Configuration

```python
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.embeddings import OllamaEmbeddings

EMBEDDING_CONFIG = {
    "primary": {
        "provider": "google",
        "model": "models/text-embedding-004",
        "dimensions": 768,
    },
    "fallback": {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": 768,
    },
    "chunk_size": 1000,
    "chunk_overlap": 200,
}

def get_embeddings():
    try:
        return GoogleGenerativeAIEmbeddings(
            model=EMBEDDING_CONFIG["primary"]["model"]
        )
    except Exception:
        return OllamaEmbeddings(
            model=EMBEDDING_CONFIG["fallback"]["model"]
        )
```

---

## 🗃️ Vector Store

### Technology: pgvector

Using PostgreSQL with pgvector extension for vector storage (already have Postgres).

```sql
-- Enable extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Knowledge embeddings table
CREATE TABLE knowledge_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Source identification
    source_type TEXT NOT NULL,  -- 'proposal', 'portfolio', 'template', 'tech'
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,  -- For change detection
    
    -- Content
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    
    -- Embedding
    embedding vector(768),                     -- Google text-embedding-004 (768 dim)
    
    -- Metadata for filtering
    metadata JSONB DEFAULT '{}',
    category TEXT,              -- 'web_dev', 'mobile', 'backend', 'design'
    platform TEXT,              -- 'freelancer', 'upwork', 'all'
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(source_path, chunk_index)
);

-- Indexes
CREATE INDEX idx_embeddings_category ON knowledge_embeddings(category);
CREATE INDEX idx_embeddings_platform ON knowledge_embeddings(platform);
CREATE INDEX idx_embeddings_source ON knowledge_embeddings(source_type);

-- DiskANN index for ultra-fast vector search (pgvectorscale)
-- 11x faster than HNSW at 99% recall on large datasets
CREATE INDEX idx_embeddings_vector ON knowledge_embeddings
USING diskann (embedding vector_cosine_ops);
```

### Query Examples

```python
from langchain_postgres import PGVector

vector_store = PGVector(
    collection_name="knowledge",
    connection=postgres_url,
    embeddings=get_embeddings(),
)

# Semantic search for proposals
async def find_similar_proposals(
    job_description: str,
    category: str = None,
    platform: str = None,
    k: int = 5
) -> list[Document]:
    """Find similar successful proposals."""
    
    filter_dict = {"source_type": "proposal"}
    if category:
        filter_dict["category"] = category
    if platform:
        filter_dict["platform"] = platform
    
    return await vector_store.asimilarity_search(
        job_description,
        k=k,
        filter=filter_dict
    )

# Find relevant portfolio cases
async def find_portfolio_cases(
    requirements: str,
    k: int = 3
) -> list[Document]:
    """Find relevant portfolio projects."""
    
    return await vector_store.asimilarity_search(
        requirements,
        k=k,
        filter={"source_type": "portfolio"}
    )
```

---

## 📝 Document Processing

### Chunking Strategy

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    separators=["\n\n", "\n", ". ", " ", ""],
    length_function=len,
)

def process_document(file_path: str, source_type: str) -> list[Document]:
    """Process document into chunks with metadata."""
    
    with open(file_path, "r") as f:
        content = f.read()
    
    chunks = splitter.split_text(content)
    
    documents = []
    for i, chunk in enumerate(chunks):
        doc = Document(
            page_content=chunk,
            metadata={
                "source_path": file_path,
                "source_type": source_type,
                "chunk_index": i,
                "category": extract_category(file_path),
                "platform": extract_platform(content),
            }
        )
        documents.append(doc)
    
    return documents
```

### Indexing Pipeline

```python
import hashlib
from pathlib import Path

async def index_knowledge_base():
    """Index or re-index the entire knowledge base."""
    
    knowledge_dir = Path("knowledge")
    
    for source_type in ["proposals", "portfolio", "clients", "technical", "outreach"]:
        type_dir = knowledge_dir / source_type
        
        for file_path in type_dir.rglob("*.md"):
            await index_file(file_path, source_type)
            
        for file_path in type_dir.rglob("*.json"):
            await index_file(file_path, source_type)

async def index_file(file_path: Path, source_type: str):
    """Index a single file, skip if unchanged."""
    
    current_hash = hash_file(file_path)
    
    # Check if already indexed with same hash
    existing = await db.fetchrow(
        "SELECT source_hash FROM knowledge_embeddings WHERE source_path = $1 LIMIT 1",
        str(file_path)
    )
    
    if existing and existing["source_hash"] == current_hash:
        logger.debug(f"Skipping unchanged: {file_path}")
        return
    
    # Delete old embeddings
    await db.execute(
        "DELETE FROM knowledge_embeddings WHERE source_path = $1",
        str(file_path)
    )
    
    # Process and index
    documents = process_document(str(file_path), source_type)
    
    for doc in documents:
        embedding = await embeddings.aembed_query(doc.page_content)
        
        await db.execute("""
            INSERT INTO knowledge_embeddings 
            (source_type, source_path, source_hash, content, chunk_index, 
             embedding, metadata, category, platform)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
            source_type,
            str(file_path),
            current_hash,
            doc.page_content,
            doc.metadata["chunk_index"],
            embedding,
            doc.metadata,
            doc.metadata.get("category"),
            doc.metadata.get("platform")
        )
    
    logger.info(f"Indexed {len(documents)} chunks from {file_path}")

def hash_file(path: Path) -> str:
    """Calculate file hash for change detection."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
```

---

## 🔄 Update Strategy

### Automatic Indexing

```python
# On startup
@app.on_event("startup")
async def startup_indexing():
    await index_knowledge_base()

# Scheduled reindex (daily)
@scheduler.scheduled_job("cron", hour=3)
async def nightly_reindex():
    await index_knowledge_base()
```

### Manual Updates

```python
# After adding new proposal
async def add_successful_proposal(
    proposal_text: str,
    category: str,
    platform: str,
    outcome: dict  # win rate, client feedback
):
    """Add a new successful proposal to knowledge base."""
    
    # Save to file
    filename = f"proposals/{category}/{uuid.uuid4()}.md"
    content = format_proposal_doc(proposal_text, outcome)
    
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    Path(filename).write_text(content)
    
    # Index immediately
    await index_file(Path(filename), "proposal")
```

---

## 📊 Metrics & Monitoring

### Retrieval Quality

```python
async def log_retrieval_usage(
    query: str,
    documents: list[Document],
    was_helpful: bool = None
):
    """Log retrieval for quality monitoring."""
    
    await db.execute("""
        INSERT INTO retrieval_logs 
        (query, num_results, doc_ids, was_helpful)
        VALUES ($1, $2, $3, $4)
    """,
        query,
        len(documents),
        [d.metadata["source_path"] for d in documents],
        was_helpful
    )
```

### Dashboard Metrics

| Metric | Query |
|--------|-------|
| Total documents | `SELECT COUNT(*) FROM knowledge_embeddings` |
| By category | `SELECT category, COUNT(*) GROUP BY category` |
| Retrieval accuracy | `SELECT AVG(was_helpful::int) FROM retrieval_logs` |
| Index freshness | `SELECT MAX(updated_at) FROM knowledge_embeddings` |

---

## ⚙️ Configuration

```python
KNOWLEDGE_BASE_CONFIG = {
    "embedding": {
        "model": "models/text-embedding-004",  # Google text-embedding-004
        "dimensions": 768,
    },
    "chunking": {
        "chunk_size": 1000,
        "chunk_overlap": 200,
    },
    "retrieval": {
        "default_k": 5,
        "score_threshold": 0.7,
    },
    "indexing": {
        "auto_reindex": True,
        "reindex_schedule": "0 3 * * *",  # 3 AM daily
    }
}
```
