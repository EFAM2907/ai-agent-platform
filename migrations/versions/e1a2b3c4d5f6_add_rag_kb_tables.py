"""add rag kb tables (articles + chunks with pgvector embeddings)

Revision ID: e1a2b3c4d5f6
Revises: b7f2a9c1d4e8
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, Sequence[str], None] = "b7f2a9c1d4e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# text-embedding-004 / gemini-embedding-001 truncado a 768 dims via
# output_dimensionality -- fijo aqui porque pgvector necesita el tamano
# de columna definido de antemano; si el modelo de embeddings cambia,
# esto exige una migracion nueva (no un simple cambio de config).
EMBEDDING_DIM = 768


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "kb_articles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_articles_organization_id", "kb_articles", ["organization_id"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("article_id", sa.UUID(), nullable=False),
        # Denormalizado a proposito: retrieval filtra por organization_id
        # directo en esta tabla -- defensa en profundidad, un bug en el
        # join contra kb_articles nunca deberia ser lo unico que evita
        # un leak cross-tenant en una busqueda por similitud.
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["kb_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_chunks_organization_id", "kb_chunks", ["organization_id"])
    # ivfflat sobre coseno: correcto para el tamano de KB del eval, aunque
    # con pocos cientos de filas el brute-force hubiera sido igual de
    # rapido -- se deja el index con la forma correcta para que escale,
    # documentado que requiere ANALYZE tras cargar datos para ser efectivo.
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_cosine ON kb_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kb_chunks_embedding_cosine", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_organization_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_kb_articles_organization_id", table_name="kb_articles")
    op.drop_table("kb_articles")
    # No se elimina la extension "vector" en el downgrade -- otras tablas
    # (el dominio de chat, una vez agregado) pueden llegar a depender de
    # ella tambien.
