"""Knowledge Base module -- RAG for proposal generation and project context."""

from src.knowledge.embedding_service import EmbeddingService
from src.knowledge.ingestion import KnowledgeIngestionPipeline
from src.knowledge.retrieval import KnowledgeResult, KnowledgeRetriever

__all__ = [
    "EmbeddingService",
    "KnowledgeIngestionPipeline",
    "KnowledgeResult",
    "KnowledgeRetriever",
]
