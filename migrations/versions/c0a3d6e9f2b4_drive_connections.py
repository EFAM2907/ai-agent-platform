"""conexiones de Google Drive por organizacion

Revision ID: c0a3d6e9f2b4
Revises: b8e1f4a7c0d2
Create Date: 2026-10-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c0a3d6e9f2b4"
down_revision: Union[str, Sequence[str], None] = "b8e1f4a7c0d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drive_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"), nullable=False, unique=True,
        ),
        sa.Column(
            "connected_by_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"), nullable=False,
        ),
        sa.Column("google_email", sa.String(320), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=False),
        sa.Column("folder_id", sa.String(100), nullable=True),
        sa.Column("folder_name", sa.String(300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("drive_connections")
