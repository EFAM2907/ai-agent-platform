"""Conexion de Google Drive por organizacion.

Una fila por organizacion (unique), igual que GmailConnection: reconectar
reutiliza la fila. Un refresh token equivale a acceso permanente de LECTURA
a los archivos de la cuenta, asi que va cifrado (app.core.crypto), nunca en
texto plano. El scope es drive.readonly: la app nunca escribe en Drive.

La carpeta elegida (folder_id) es lo que se sincroniza con la base de
conocimiento; los documentos que salen de ahi viven en kb_articles con
source_type="drive" y source_ref="drive:<id del archivo>".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy import func

from app.core.database import Base


class DriveConnection(Base):
    __tablename__ = "drive_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), unique=True
    )
    connected_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    google_email: Mapped[str] = mapped_column(String(320))
    refresh_token: Mapped[str] = mapped_column(String)
    folder_id: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    folder_name: Mapped[str | None] = mapped_column(String(300), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # No nulo = desconectada (por el admin o porque Google revoco el acceso).
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Resumen de la ultima sincronizacion (documentos, retirados, fallos).
    last_sync: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
