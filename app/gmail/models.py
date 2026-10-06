"""Modelos de la integracion con Gmail: la conexion OAuth de cada
organizacion y el registro de correos ya atendidos.

gmail_processed_messages es lo que garantiza que un correo nunca se
responde dos veces (ver GmailRepository.claim_message): la ventana de
busqueda del poller se solapa entre corridas a proposito, asi que la
idempotencia no puede depender de marcar el correo como leido en Gmail
(ademas, ni siquiera pedimos el scope para modificarlo).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Resultados de un correo procesado. PROCESSING es un estado transitorio
# (correo "reclamado" antes de enviar): si el proceso muere a mitad de
# camino queda ahi y NO se reintenta -- preferimos, a proposito, no
# responder que responder dos veces.
OUTCOME_PROCESSING = "processing"
OUTCOME_REPLIED = "replied"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

MAX_ATTEMPTS = 3


class GmailConnection(Base):
    """Una fila por organizacion (unique): reconectar reutiliza la fila,
    asi el historial de correos atendidos sobrevive a un desconectar /
    reconectar y no se responde dos veces lo que cae en la ventana."""

    __tablename__ = "gmail_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), unique=True
    )
    connected_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    google_email: Mapped[str] = mapped_column(String(320))
    # Cifrado con app.core.crypto -- un refresh token equivale a acceso
    # permanente al buzon, nunca en texto plano.
    refresh_token: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # No nulo = desconectada (por el admin o porque Google revoco el
    # acceso); el poller la ignora.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class GmailProcessedMessage(Base):
    __tablename__ = "gmail_processed_messages"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "gmail_message_id", name="uq_gmail_processed_connection_message"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gmail_connections.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    gmail_message_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(320), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default=OUTCOME_PROCESSING)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
