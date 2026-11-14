"""extension unaccent para busquedas sin tildes

Revision ID: b8e1f4a7c0d2
Revises: a7d0e3f6b9c1
Create Date: 2026-10-15 00:00:00.000000

Quien busca "julian" espera encontrar a "Julián". unaccent viene en los
contrib de PostgreSQL (incluidos en la imagen pgvector/pgvector).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8e1f4a7c0d2"
down_revision: Union[str, Sequence[str], None] = "a7d0e3f6b9c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    # No se elimina la extension: otras consultas pueden depender de ella.
    pass
