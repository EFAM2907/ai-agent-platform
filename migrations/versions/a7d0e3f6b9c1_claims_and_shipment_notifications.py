"""reclamos y notificaciones de envios

Revision ID: a7d0e3f6b9c1
Revises: f5b8c1d4e7a9
Create Date: 2026-10-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d0e3f6b9c1"
down_revision: Union[str, Sequence[str], None] = "f5b8c1d4e7a9"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("organization_id", _UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("shipment_id", _UUID, sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("claim_number", sa.String(30), nullable=False),
        sa.Column("claim_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("opened_by_user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("organization_id", "claim_number", name="uq_claims_org_number"),
    )
    op.create_index("ix_claims_org_status", "claims", ["organization_id", "status"])
    op.create_index("ix_claims_shipment", "claims", ["shipment_id"])

    op.create_table(
        "claim_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("claim_id", _UUID, sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", _UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index("ix_claim_events_claim", "claim_events", ["claim_id"])

    op.create_table(
        "shipment_notifications",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("organization_id", _UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "shipment_id", _UUID, sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sent_by_user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index(
        "ix_shipment_notifications_shipment", "shipment_notifications", ["shipment_id", "created_at"]
    )

    # Busquedas de clientes por nombre/correo/documento (list_customers?search=).
    op.create_index("ix_customers_org_email", "customers", ["organization_id", "email"])


def downgrade() -> None:
    op.drop_index("ix_customers_org_email", table_name="customers")
    op.drop_index("ix_shipment_notifications_shipment", table_name="shipment_notifications")
    op.drop_table("shipment_notifications")
    op.drop_index("ix_claim_events_claim", table_name="claim_events")
    op.drop_table("claim_events")
    op.drop_index("ix_claims_shipment", table_name="claims")
    op.drop_index("ix_claims_org_status", table_name="claims")
    op.drop_table("claims")
