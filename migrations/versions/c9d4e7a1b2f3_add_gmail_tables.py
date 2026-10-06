"""add gmail connection and processed messages tables

Revision ID: c9d4e7a1b2f3
Revises: f4b8c1a9d3e7
Create Date: 2026-09-21 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c9d4e7a1b2f3"
down_revision: Union[str, Sequence[str], None] = "f4b8c1a9d3e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "connected_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("google_email", sa.String(320), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "gmail_processed_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gmail_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("gmail_message_id", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("sender", sa.String(320), nullable=True),
        sa.Column("tracking_number", sa.String(200), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "connection_id", "gmail_message_id", name="uq_gmail_processed_connection_message"
        ),
    )
    op.create_index(
        "ix_gmail_processed_sender_recent",
        "gmail_processed_messages",
        ["connection_id", "sender", "processed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_gmail_processed_sender_recent", table_name="gmail_processed_messages")
    op.drop_table("gmail_processed_messages")
    op.drop_table("gmail_connections")
