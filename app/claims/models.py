"""Reclamos (la "investigacion" de la politica de envios demorados).

La base de conocimiento habla de un numero de caso ("Claim #") que el
cliente usa para seguir su reclamo y de un SLA de investigacion; hasta
ahora el agente no tenia como abrir ninguno. Un reclamo se asocia a UN
envio y recorre un ciclo de vida corto con historial (ClaimEvent), de
modo que siempre se puede reconstruir quien hizo que y cuando.

type / status son strings validados en codigo (no enums nativos de
Postgres): sumar un tipo o un estado no exige un ALTER TYPE.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ClaimType(str, enum.Enum):
    """Mismos tipos que la KB (05-reclamaciones.md y el procedimiento
    interno de reclamos) con su codigo REC."""

    DANO = "dano"  # REC-01
    PERDIDA = "perdida"  # REC-02
    RETRASO = "retraso"  # REC-03
    COBRO_INCORRECTO = "cobro_incorrecto"  # REC-04
    ENTREGA_NO_AUTORIZADA = "entrega_no_autorizada"  # REC-05


class ClaimStatus(str, enum.Enum):
    ABIERTO = "abierto"
    EN_INVESTIGACION = "en_investigacion"
    RESUELTO = "resuelto"
    CERRADO = "cerrado"


# SLA de investigacion en horas por tipo (tabla de tiempos del
# procedimiento interno de reclamos de la KB).
SLA_HOURS: dict[ClaimType, int] = {
    ClaimType.DANO: 72,
    ClaimType.PERDIDA: 120,
    ClaimType.RETRASO: 96,
    ClaimType.COBRO_INCORRECTO: 48,
    ClaimType.ENTREGA_NO_AUTORIZADA: 120,
}

REC_CODES: dict[ClaimType, str] = {
    ClaimType.DANO: "REC-01",
    ClaimType.PERDIDA: "REC-02",
    ClaimType.RETRASO: "REC-03",
    ClaimType.COBRO_INCORRECTO: "REC-04",
    ClaimType.ENTREGA_NO_AUTORIZADA: "REC-05",
}

# Transiciones validas. CERRADO es terminal; un RESUELTO puede reabrirse
# (volver a EN_INVESTIGACION) si el cliente no esta conforme.
TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.ABIERTO: frozenset({ClaimStatus.EN_INVESTIGACION, ClaimStatus.CERRADO}),
    ClaimStatus.EN_INVESTIGACION: frozenset({ClaimStatus.RESUELTO, ClaimStatus.CERRADO}),
    ClaimStatus.RESUELTO: frozenset({ClaimStatus.CERRADO, ClaimStatus.EN_INVESTIGACION}),
    ClaimStatus.CERRADO: frozenset(),
}

# Estados en los que el reclamo sigue vivo (no permite abrir un duplicado).
ACTIVE_STATUSES = (ClaimStatus.ABIERTO, ClaimStatus.EN_INVESTIGACION)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("organization_id", "claim_number", name="uq_claims_org_number"),
        Index("ix_claims_org_status", "organization_id", "status"),
        Index("ix_claims_shipment", "shipment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shipments.id"))
    claim_number: Mapped[str] = mapped_column(String(30))
    claim_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default=ClaimStatus.ABIERTO.value)
    description: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    opened_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list["ClaimEvent"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="ClaimEvent.created_at"
    )


class ClaimEvent(Base):
    """Historial del reclamo: apertura y cada cambio de estado, con quien
    lo hizo y la nota que dejo."""

    __tablename__ = "claim_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    claim: Mapped["Claim"] = relationship(back_populates="events")
