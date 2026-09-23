from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.gmail.models import (
    MAX_ATTEMPTS,
    OUTCOME_FAILED,
    OUTCOME_PROCESSING,
    OUTCOME_REPLIED,
    OUTCOME_SKIPPED,
    GmailConnection,
    GmailProcessedMessage,
)


class GmailRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- conexiones ---------------------------------------------------

    async def get_connection(self, organization_id: uuid.UUID) -> GmailConnection | None:
        stmt = select(GmailConnection).where(GmailConnection.organization_id == organization_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active_connections(self) -> list[GmailConnection]:
        stmt = select(GmailConnection).where(GmailConnection.revoked_at.is_(None))
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert_connection(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        google_email: str,
        encrypted_refresh_token: str,
    ) -> GmailConnection:
        """Crea la conexion o reactiva la existente. Si la cuenta de
        Google cambio, el historial de correos atendidos se descarta:
        los ids de mensaje son por buzon, no comparables entre cuentas."""
        connection = await self.get_connection(organization_id)
        if connection is None:
            connection = GmailConnection(
                organization_id=organization_id,
                connected_by_user_id=user_id,
                google_email=google_email,
                refresh_token=encrypted_refresh_token,
            )
            self.session.add(connection)
        else:
            if connection.google_email != google_email:
                await self.session.execute(
                    delete(GmailProcessedMessage).where(
                        GmailProcessedMessage.connection_id == connection.id
                    )
                )
            connection.connected_by_user_id = user_id
            connection.google_email = google_email
            connection.refresh_token = encrypted_refresh_token
            connection.revoked_at = None
        await self.session.flush()
        return connection

    async def mark_revoked(self, connection: GmailConnection) -> None:
        connection.revoked_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def touch_polled(self, connection: GmailConnection) -> None:
        connection.last_polled_at = datetime.now(timezone.utc)
        await self.session.flush()

    # -- correos procesados --------------------------------------------

    async def is_finished(self, connection_id: uuid.UUID, message_id: str) -> bool:
        """True si el correo ya no debe tocarse: cualquier estado salvo
        `failed` con reintentos disponibles. Barato -- se consulta antes
        de descargar el mensaje completo."""
        row = await self._get_processed(connection_id, message_id)
        if row is None:
            return False
        return not (row.outcome == OUTCOME_FAILED and row.attempts < MAX_ATTEMPTS)

    async def claim_message(
        self,
        connection: GmailConnection,
        *,
        message_id: str,
        thread_id: str | None,
        sender: str | None,
    ) -> GmailProcessedMessage | None:
        """Reclama un correo ANTES de trabajar con el, y hace commit.

        Devuelve None si otro proceso (u otra corrida) ya lo tiene o ya
        lo termino. El unique (connection_id, gmail_message_id) es lo que
        hace atomica la reclamacion. Reclamar antes de enviar -- y no
        registrar despues -- implica que un crash entre el envio y el
        registro no puede provocar una segunda respuesta."""
        existing = await self._get_processed(connection.id, message_id)
        if existing is not None:
            if existing.outcome == OUTCOME_FAILED and existing.attempts < MAX_ATTEMPTS:
                existing.outcome = OUTCOME_PROCESSING
                existing.attempts += 1
                existing.detail = None
                await self.session.commit()
                return existing
            return None

        row = GmailProcessedMessage(
            connection_id=connection.id,
            organization_id=connection.organization_id,
            gmail_message_id=message_id,
            thread_id=thread_id,
            sender=sender,
            outcome=OUTCOME_PROCESSING,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return None  # otro proceso lo reclamo entre el SELECT y el INSERT
        await self.session.commit()
        return row

    async def finish_message(
        self,
        row: GmailProcessedMessage,
        outcome: str,
        *,
        detail: str | None = None,
        tracking_number: str | None = None,
    ) -> None:
        row.outcome = outcome
        row.detail = detail[:500] if detail else None
        row.tracking_number = tracking_number
        row.processed_at = datetime.now(timezone.utc)
        await self.session.commit()

    async def count_recent_replies_to(
        self,
        connection_id: uuid.UUID,
        sender: str,
        *,
        since_hours: int = 24,
        detail: str | None = None,
    ) -> int:
        """Respuestas enviadas a `sender` en las ultimas `since_hours`;
        con `detail`, solo las de ese tipo (ver ReplyKind)."""
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        stmt = select(func.count()).where(
            GmailProcessedMessage.connection_id == connection_id,
            GmailProcessedMessage.sender == sender,
            GmailProcessedMessage.outcome == OUTCOME_REPLIED,
            GmailProcessedMessage.processed_at >= since,
        )
        if detail is not None:
            stmt = stmt.where(GmailProcessedMessage.detail == detail)
        return (await self.session.execute(stmt)).scalar_one()

    async def list_unresolved(
        self, connection_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> tuple[list[GmailProcessedMessage], int]:
        """Correos que llegaron y no tuvieron una respuesta util: SKIPPED
        (no encajaron en ningun caso que el pipeline sabe resolver -- ver
        GmailProcessedMessage.detail, ej. "no_tracking_number") o FAILED
        (fallo el envio, incluso agotados los reintentos). REPLIED queda
        afuera a proposito: esos ya tuvieron respuesta, automatica o no.
        Mas recientes primero, para revisar primero lo mas reciente."""
        condition = (
            GmailProcessedMessage.connection_id == connection_id,
            GmailProcessedMessage.outcome.in_((OUTCOME_SKIPPED, OUTCOME_FAILED)),
        )
        total = (
            await self.session.execute(select(func.count()).where(*condition))
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(GmailProcessedMessage)
                .where(*condition)
                .order_by(GmailProcessedMessage.processed_at.desc())
                .offset(skip)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), total

    async def _get_processed(
        self, connection_id: uuid.UUID, message_id: str
    ) -> GmailProcessedMessage | None:
        stmt = select(GmailProcessedMessage).where(
            GmailProcessedMessage.connection_id == connection_id,
            GmailProcessedMessage.gmail_message_id == message_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
