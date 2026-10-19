"""add full-text search column to kb_chunks (hybrid search)

Revision ID: d2e5f8a1b3c4
Revises: c9d4e7a1b2f3
Create Date: 2026-10-09 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d2e5f8a1b3c4"
down_revision: Union[str, Sequence[str], None] = "c9d4e7a1b2f3"
branch_labels = None
depends_on = None

# Debe coincidir con FTS_CONFIG en app.rag.models -- la KB esta en
# español, asi que 'spanish' hace stemming ("retrasos" ~ "retraso") y
# descarta stopwords. Los numeros (guias como 550201) se conservan tal cual.
FTS_CONFIG = "spanish"


def upgrade() -> None:
    # Columna GENERATED ... STORED: Postgres la calcula solo desde
    # `content`, asi que (1) las filas que ya existen se rellenan en
    # este mismo ALTER, y (2) ningun codigo de ingesta tiene que saber
    # que existe -- no hay forma de que quede desincronizada del texto.
    op.execute(
        "ALTER TABLE kb_chunks ADD COLUMN content_tsv tsvector "
        f"GENERATED ALWAYS AS (to_tsvector('{FTS_CONFIG}', content)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_kb_chunks_content_tsv ON kb_chunks USING gin (content_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_content_tsv")
    op.execute("ALTER TABLE kb_chunks DROP COLUMN IF EXISTS content_tsv")
