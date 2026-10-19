from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.drive.models import DriveConnection


class DriveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_connection(self, organization_id: uuid.UUID) -> DriveConnection | None:
        stmt = select(DriveConnection).where(DriveConnection.organization_id == organization_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_syncable_connections(self) -> list[DriveConnection]:
        """Conexiones activas con carpeta elegida: las que el proceso
        periodico debe sincronizar."""
        stmt = select(DriveConnection).where(
            DriveConnection.revoked_at.is_(None), DriveConnection.folder_id.is_not(None)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert_connection(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        google_email: str,
        encrypted_refresh_token: str,
    ) -> DriveConnection:
        """Crea la conexion o reactiva la existente. Si la cuenta de Google
        cambia, la carpeta elegida se descarta: un id de carpeta solo
        significa algo para la cuenta que la ve."""
        connection = await self.get_connection(organization_id)
        if connection is None:
            connection = DriveConnection(
                organization_id=organization_id,
                connected_by_user_id=user_id,
                google_email=google_email,
                refresh_token=encrypted_refresh_token,
            )
            self.session.add(connection)
        else:
            if connection.google_email != google_email:
                connection.folder_id = None
                connection.folder_name = None
                connection.last_synced_at = None
                connection.last_sync = None
            connection.connected_by_user_id = user_id
            connection.google_email = google_email
            connection.refresh_token = encrypted_refresh_token
            connection.revoked_at = None
        await self.session.flush()
        return connection

    async def set_folder(
        self, connection: DriveConnection, folder_id: str, folder_name: str
    ) -> None:
        if connection.folder_id != folder_id:
            connection.last_synced_at = None
            connection.last_sync = None
        connection.folder_id = folder_id
        connection.folder_name = folder_name
        await self.session.flush()

    async def mark_revoked(self, connection: DriveConnection) -> None:
        connection.revoked_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def record_sync(self, connection: DriveConnection, summary: dict[str, Any]) -> None:
        connection.last_synced_at = datetime.now(timezone.utc)
        connection.last_sync = summary
        await self.session.flush()
