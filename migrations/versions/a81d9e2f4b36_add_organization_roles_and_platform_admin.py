"""add organization roles and platform admin

Revision ID: a81d9e2f4b36
Revises: c050d40e077b
Create Date: 2026-08-22 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a81d9e2f4b36"
down_revision: Union[str, Sequence[str], None] = "c050d40e077b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires new enum values to be committed before they can be
    # referenced by an index predicate.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'OWNER'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'VIEWER'")

    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Existing organizations receive their oldest active member as OWNER.
    # The id tie-breaker makes the result deterministic when timestamps match.
    op.execute(
        """
        UPDATE users AS target
        SET role = 'OWNER'
        WHERE target.id IN (
            SELECT DISTINCT ON (organization_id) id
            FROM users
            WHERE deleted_at IS NULL
            ORDER BY organization_id, created_at ASC, id ASC
        )
        """
    )
    op.create_index(
        "ix_users_one_active_owner_per_organization",
        "users",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("role = 'OWNER' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_one_active_owner_per_organization", table_name="users")
    op.drop_column("users", "is_platform_admin")
    # PostgreSQL does not support removing individual enum values safely.
