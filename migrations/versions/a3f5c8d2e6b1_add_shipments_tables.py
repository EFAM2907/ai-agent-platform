"""add shipments, customers and shipment_events tables

Revision ID: a3f5c8d2e6b1
Revises: f7a8b9c0d1e2
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a3f5c8d2e6b1"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLAlchemy guarda un Python enum por el NOMBRE de su miembro, no por
# su .value (mismo comportamiento que UserRole/MessageRole en las
# migraciones anteriores) -- por eso estos tipos Postgres se crean con
# los nombres en mayuscula, no con los valores en minuscula que se ven
# en app.shipments.models.
_SHIPMENT_STATUS_VALUES = (
    "ADMITIDO",
    "EN_BODEGA_ORIGEN",
    "EN_TRANSITO",
    "EN_BODEGA_DESTINO",
    "EN_REPARTO",
    "ENTREGADO",
    "NOVEDAD",
)
_SERVICE_TYPE_VALUES = ("ESTANDAR", "EXPRESS", "SAME_DAY")


def upgrade() -> None:
    """Upgrade schema."""
    # postgresql.ENUM (no el sa.Enum generico) con create_type=False:
    # sa.Enum pierde ese flag cuando SQLAlchemy lo adapta al tipo nativo
    # del dialecto al asociarlo a una columna (_make_enum_kw no lo
    # reenvia), asi que create_table vuelve a intentar CREATE TYPE en
    # cada tabla que reutiliza el mismo Enum (shipments.status y
    # shipment_events.event_type comparten shipmentstatus) y falla con
    # DuplicateObject. El tipo ya-especifico-de-dialecto no pasa por esa
    # adaptacion, asi que el flag se conserva.
    shipment_status = postgresql.ENUM(*_SHIPMENT_STATUS_VALUES, name="shipmentstatus", create_type=False)
    service_type = postgresql.ENUM(*_SERVICE_TYPE_VALUES, name="servicetype", create_type=False)
    shipment_status.create(op.get_bind(), checkfirst=True)
    service_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "customers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("document_id", sa.String(length=50), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customers_organization_id", "customers", ["organization_id"])

    op.create_table(
        "shipments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("tracking_number", sa.String(length=50), nullable=False),
        sa.Column("status", shipment_status, nullable=False),
        sa.Column("service_type", service_type, nullable=False),
        sa.Column("origin_city", sa.String(length=100), nullable=False),
        sa.Column("destination_city", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipments_organization_id", "shipments", ["organization_id"])
    op.create_index("ix_shipments_customer_id", "shipments", ["customer_id"])
    # Un tracking number es unico dentro de un tenant, no globalmente --
    # y solo entre las filas activas (mismo criterio que
    # ix_users_email_unique_active), para poder re-emitir el mismo
    # numero si el envio original se llega a soft-delete algun dia.
    op.create_index(
        "ix_shipments_tracking_number_unique_active",
        "shipments",
        ["organization_id", "tracking_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "shipment_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shipment_id", sa.UUID(), nullable=False),
        # Denormalizado igual que kb_chunks.organization_id /
        # chat_messages.organization_id -- cualquier query de este
        # dominio filtra por organization_id directo, sin depender de
        # un join hasta shipments.
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("event_type", shipment_status, nullable=False),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipment_events_shipment_id", "shipment_events", ["shipment_id"])
    op.create_index("ix_shipment_events_organization_id", "shipment_events", ["organization_id"])
    # La politica de retrasos calcula MAX(occurred_at) por envio -- este
    # indice es lo que hace que ese calculo escale mas alla del dataset
    # de seed.
    op.create_index("ix_shipment_events_occurred_at", "shipment_events", ["occurred_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_shipment_events_occurred_at", table_name="shipment_events")
    op.drop_index("ix_shipment_events_organization_id", table_name="shipment_events")
    op.drop_index("ix_shipment_events_shipment_id", table_name="shipment_events")
    op.drop_table("shipment_events")

    op.drop_index("ix_shipments_tracking_number_unique_active", table_name="shipments")
    op.drop_index("ix_shipments_customer_id", table_name="shipments")
    op.drop_index("ix_shipments_organization_id", table_name="shipments")
    op.drop_table("shipments")

    op.drop_index("ix_customers_organization_id", table_name="customers")
    op.drop_table("customers")

    sa.Enum(name="servicetype").drop(op.get_bind())
    sa.Enum(name="shipmentstatus").drop(op.get_bind())
