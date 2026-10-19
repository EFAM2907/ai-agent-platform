"""acciones pendientes de aprobacion humana y delegacion en las trazas

Revision ID: d1c2b3a4e5f6
Revises: c0a3d6e9f2b4
Create Date: 2026-10-19 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1c2b3a4e5f6"
down_revision: Union[str, Sequence[str], None] = "c0a3d6e9f2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_pending_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"), nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"), nullable=False,
        ),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chat_pending_actions_user_status", "chat_pending_actions", ["user_id", "status"]
    )
    op.add_column(
        "chat_turn_traces",
        sa.Column("delegation", postgresql.JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("chat_turn_traces", "delegation")
    op.drop_index("ix_chat_pending_actions_user_status", table_name="chat_pending_actions")
    op.drop_table("chat_pending_actions")
