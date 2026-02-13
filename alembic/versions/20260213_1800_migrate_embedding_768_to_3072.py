"""migrate_embedding_768_to_3072

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-13 18:00:00.000000

Migrate embedding columns from Google text-embedding-004 (768 dim) to
OpenAI text-embedding-3-large (3072 dim).  Existing embeddings are
incompatible with the new dimension and must be re-generated.

Affected tables:
  - knowledge_base.embedding: Vector(768) → Vector(3072)
  - semantic_cache.embedding: Vector(768) → Vector(3072)
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the existing HNSW vector index on knowledge_base
    op.execute("DROP INDEX IF EXISTS idx_knowledge_embedding_hnsw")

    # 2. Nullify existing 768-dim embeddings (incompatible with 3072-dim)
    op.execute("UPDATE knowledge_base SET embedding = NULL WHERE embedding IS NOT NULL")
    op.execute("UPDATE semantic_cache SET embedding = NULL WHERE embedding IS NOT NULL")

    # 3. ALTER COLUMN type from vector(768) to vector(3072)
    op.execute("ALTER TABLE knowledge_base ALTER COLUMN embedding TYPE vector(3072)")
    op.execute("ALTER TABLE semantic_cache ALTER COLUMN embedding TYPE vector(3072)")

    # 4. Recreate vector index (HNSW — compatible with pgvector 0.7+)
    #    DiskANN requires pgvectorscale extension; use HNSW as safe default.
    op.execute(
        "CREATE INDEX idx_knowledge_embedding_hnsw ON knowledge_base "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    # 1. Drop index
    op.execute("DROP INDEX IF EXISTS idx_knowledge_embedding_hnsw")

    # 2. Nullify 3072-dim embeddings (incompatible with 768-dim)
    op.execute("UPDATE knowledge_base SET embedding = NULL WHERE embedding IS NOT NULL")
    op.execute("UPDATE semantic_cache SET embedding = NULL WHERE embedding IS NOT NULL")

    # 3. Revert to vector(768)
    op.execute("ALTER TABLE knowledge_base ALTER COLUMN embedding TYPE vector(768)")
    op.execute("ALTER TABLE semantic_cache ALTER COLUMN embedding TYPE vector(768)")

    # 4. Recreate index with original dimension
    op.execute(
        "CREATE INDEX idx_knowledge_embedding_hnsw ON knowledge_base "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
