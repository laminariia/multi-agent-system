"""add_knowledge_vector_index

Revision ID: a7f3e1b92d4c
Revises: c661f6b62e0c
Create Date: 2026-02-08 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f3e1b92d4c"
down_revision: Union[str, None] = "c661f6b62e0c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # HNSW index on knowledge_base.embedding for cosine distance search.
    # pgvector's HNSW is the recommended index type for ANN search.
    # m=16 and ef_construction=64 are good defaults for <100K rows.
    op.execute(
        "CREATE INDEX idx_knowledge_embedding_hnsw ON knowledge_base "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_knowledge_embedding_hnsw")
