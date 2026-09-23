"""Modelo de datos de envios de Inter Rapidisimo: clientes, envios, y el
historial de eventos de cada envio.

El estado actual de un envio (Shipment.status) vive como columna propia
para poder filtrar/listar rapido sin agregacion -- pero el historial
completo (ShipmentEvent) es la fuente de verdad de "cuando fue el
ultimo movimiento". La politica de retrasos
(kb_content/inter_rapidisimo/02-politica-retrasos-investigacion.md) se
calcula sobre MAX(shipment_events.occurred_at) para el envio, nunca
sobre un campo denormalizado tipo last_event_at en shipments -- para un
dataset de este tamano (15-20 envios) ese join es trivial, y
denormalizarlo hubiera significado mantener dos fuentes de verdad
sincronizadas a mano en cada seed/actualizacion.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ShipmentStatus(str, enum.Enum):
    """Debe coincidir con los estados que describe
    kb_content/inter_rapidisimo/01-consulta-estado-envio.md -- tanto el
    estado actual de un envio (Shipment.status) como cada renglon de su
    historial (ShipmentEvent.event_type) usan este mismo enum."""

    ADMITIDO = "admitido"
    EN_BODEGA_ORIGEN = "en_bodega_origen"
    EN_TRANSITO = "en_transito"
    EN_BODEGA_DESTINO = "en_bodega_destino"
    EN_REPARTO = "en_reparto"
    ENTREGADO = "entregado"
    NOVEDAD = "novedad"


class ServiceType(str, enum.Enum):
    """Debe coincidir con
    kb_content/inter_rapidisimo/07-tarifas-tiempos-entrega.md y
    08-servicios.md."""

    ESTANDAR = "estandar"
    EXPRESS = "express"
    SAME_DAY = "same_day"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    full_name: Mapped[str] = mapped_column(String(200))
    document_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    shipments: Mapped[list["Shipment"]] = relationship(back_populates="customer")


class Shipment(Base):
    __tablename__ = "shipments"

    __table_args__ = (
        # Un tracking number es unico dentro de un tenant (no
        # globalmente) -- mismo criterio que ix_users_email_unique_active:
        # unico entre las filas activas, nunca contra las borradas.
        Index(
            "ix_shipments_tracking_number_unique_active",
            "organization_id",
            "tracking_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id")
    )
    tracking_number: Mapped[str] = mapped_column(String(50))
    status: Mapped[ShipmentStatus] = mapped_column(default=ShipmentStatus.ADMITIDO)
    service_type: Mapped[ServiceType] = mapped_column(default=ServiceType.ESTANDAR)
    origin_city: Mapped[str] = mapped_column(String(100))
    destination_city: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    customer: Mapped["Customer"] = relationship(back_populates="shipments")
    events: Mapped[list["ShipmentEvent"]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentEvent.occurred_at",
    )


class ShipmentEvent(Base):
    """Un renglon del historial de tracking de un envio -- la fuente de
    verdad de cual fue su ultimo movimiento y cuando ocurrio."""

    __tablename__ = "shipment_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE")
    )
    # Denormalizado igual que kb_chunks.organization_id / chat_messages.
    # organization_id -- mismo motivo: cualquier query de este dominio
    # filtra por organization_id directo, sin depender de un join hasta
    # shipments.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    event_type: Mapped[ShipmentStatus] = mapped_column()
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cuando ocurrio el movimiento de verdad -- distinto de created_at
    # (cuando se inserto la fila), que en el seed son iguales pero en
    # produccion no tendrian por que serlo (ej. una carga con retraso).
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shipment: Mapped["Shipment"] = relationship(back_populates="events")
