"""kb: origen/version/acceso por articulo, titulo unico y indice HNSW

Revision ID: e3f6a9b2c5d7
Revises: d2e5f8a1b3c4
Create Date: 2026-10-15 00:00:00.000000

Prepara la base de conocimiento para crecer sin reescribirla:

- kb_articles guarda DE DONDE salio cada documento (source_type /
  source_ref: carpeta local, subida por la API, Google Drive), un hash
  de su contenido (saltar lo que no cambio sin comparar textos
  enteros), con que modelo se embebio (si cambia el modelo se detecta
  que hay que reindexar), el nivel de acceso minimo para leerlo y
  cuando se modifico en la fuente.
- (organization_id, title) pasa a ser UNICO entre los articulos activos:
  RAGService.ingest_article ya asumia esa unicidad para el upsert, pero
  nada la garantizaba.
- El indice vectorial pasa de IVFFlat a HNSW. IVFFlat se construyo con la
  tabla vacia (centroides sin sentido) y filtra por organizacion DESPUES
  de usar el indice; HNSW no necesita entrenamiento y, junto con
  hnsw.iterative_scan (pgvector >= 0.8, ver KBRepository), mantiene el
  recall al filtrar por tenant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3f6a9b2c5d7"
down_revision: Union[str, Sequence[str], None] = "d2e5f8a1b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kb_articles",
        sa.Column("source_type", sa.String(20), nullable=False, server_default="manual"),
    )
    op.add_column("kb_articles", sa.Column("source_ref", sa.String(500), nullable=True))
    op.add_column("kb_articles", sa.Column("mime_type", sa.String(100), nullable=True))
    op.add_column(
        "kb_articles",
        sa.Column("access_level", sa.String(10), nullable=False, server_default="viewer"),
    )
    op.add_column("kb_articles", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("kb_articles", sa.Column("embedding_model", sa.String(100), nullable=True))
    op.add_column(
        "kb_articles", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "kb_articles",
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "kb_articles",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Lo que ya existe se ingesto desde kb_content/ con el modelo actual.
    # El hash es SHA-256 hex del contenido en UTF-8: identico a
    # hashlib.sha256(content.encode()).hexdigest() de RAGService.
    op.execute(
        "UPDATE kb_articles SET source_type = 'file', source_ref = title, "
        "content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex'), "
        "embedding_model = 'gemini-embedding', indexed_at = created_at"
    )

    op.create_index(
        "ix_kb_articles_title_unique_active",
        "kb_articles",
        ["organization_id", "title"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_kb_articles_source", "kb_articles", ["organization_id", "source_type", "source_ref"]
    )

    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding_cosine")
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding_hnsw")
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_cosine ON kb_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.drop_index("ix_kb_articles_source", table_name="kb_articles")
    op.drop_index("ix_kb_articles_title_unique_active", table_name="kb_articles")
    for column in (
        "updated_at",
        "source_modified_at",
        "indexed_at",
        "embedding_model",
        "content_hash",
        "access_level",
        "mime_type",
        "source_ref",
        "source_type",
    ):
        op.drop_column("kb_articles", column)
