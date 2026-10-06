"""add LiteLLM virtual key to organizations

Revision ID: b7f2a9c1d4e8
Revises: 88f90a0fb64e
Create Date: 2026-09-10 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7f2a9c1d4e8"
down_revision: Union[str, Sequence[str], None] = "88f90a0fb64e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("litellm_virtual_key", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "litellm_virtual_key")
