"""Conexion OAuth de Gmail por organizacion, y ejecucion de un poll.

Dos servicios separados a proposito: conectar/desconectar/consultar
estado no necesita LLM ni envios (y el callback de OAuth ni siquiera
tiene un usuario autenticado), mientras que procesar el buzon si.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.gmail.client import GmailClient
from app.gmail.errors import (
    GmailAccessRevokedError,
    GmailAuthorizationError,
    GmailNotConnectedError,
)
from app.gmail.models import GmailConnection
from app.gmail.oauth import GoogleOAuth, create_state, decode_state
from app.gmail.outbound import build_plain_email_raw
from app.gmail.processor import InboxProcessor, PollSummary
from app.gmail.repository import GmailRepository

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], GmailClient]


class GmailConnectionService:
    def __init__(
        self,
        repository: GmailRepository,
        session: AsyncSession,
        oauth: GoogleOAuth | None = None,
        client_factory: ClientFactory = GmailClient,
    ) -> None:
        self.repository = repository
        self.session = session
        self.oauth = oauth or GoogleOAuth()
        self.client_factory = client_factory

    def start_authorization(self, user_id: uuid.UUID, organization_id: uuid.UUID) -> str:
        return self.oauth.build_authorization_url(create_state(user_id, organization_id))

    async def complete_authorization(self, code: str, state: str) -> GmailConnection:
        oauth_state = decode_state(state)
        tokens = await self.oauth.exchange_code(code)
        if not tokens.refresh_token:
            # Pasa si la cuenta ya habia autorizado la app y Google no
            # reemite el token; prompt=consent normalmente lo evita.
            raise GmailAuthorizationError(
                "Google no entrego un refresh token. Revoca el acceso de la app en "
                "https://myaccount.google.com/permissions y vuelve a conectar"
            )
        google_email = await self.client_factory(tokens.access_token).get_profile_email()

        connection = await self.repository.upsert_connection(
            organization_id=oauth_state.organization_id,
            user_id=oauth_state.user_id,
            google_email=google_email,
            encrypted_refresh_token=encrypt_secret(tokens.refresh_token),
        )
        await self.session.commit()
        logger.info(
            "Gmail conectado para la organizacion %s (%s)",
            oauth_state.organization_id,
            google_email,
        )
        return connection

    async def get_active_connection(self, organization_id: uuid.UUID) -> GmailConnection | None:
        connection = await self.repository.get_connection(organization_id)
        if connection is None or connection.revoked_at is not None:
            return None
        return connection

    async def disconnect(self, organization_id: uuid.UUID) -> bool:
        """Revoca el token en Google (mejor esfuerzo) y marca la
        conexion como revocada. La fila se conserva: ver GmailConnection."""
        connection = await self.get_active_connection(organization_id)
        if connection is None:
            return False
        await self.oauth.revoke(decrypt_secret(connection.refresh_token))
        await self.repository.mark_revoked(connection)
        await self.session.commit()
        return True

    async def send_email(
        self, organization_id: uuid.UUID, *, to_email: str, subject: str, body: str
    ) -> None:
        """Envia un correo suelto (no una respuesta de tracking) desde
        el buzon conectado de la organizacion -- hoy, solo la
        invitacion de create_user con la contraseña temporal (ver
        app.users.tools.create_user). Requiere una conexion activa;
        GmailNotConnectedError sube tal cual si no la hay o si Google la
        revoca a mitad de camino (y, en ese caso, la marca revocada
        antes de propagar), para que quien llama decida el fallback --
        create_user, por ejemplo, cae a mostrar la contraseña en
        pantalla en vez de fallar la creacion del usuario."""
        connection = await self.get_active_connection(organization_id)
        if connection is None:
            raise GmailNotConnectedError("Gmail no esta conectado para esta organizacion")
        try:
            access_token = await self.oauth.refresh_access_token(
                decrypt_secret(connection.refresh_token)
            )
            raw = build_plain_email_raw(
                to_email=to_email,
                from_email=connection.google_email,
                subject=subject,
                body=body,
            )
            await self.client_factory(access_token).send_message(raw, thread_id=None)
        except GmailAccessRevokedError as exc:
            await self.repository.mark_revoked(connection)
            await self.session.commit()
            raise GmailNotConnectedError(
                "Google revoco el acceso a Gmail; hay que volver a conectar la cuenta"
            ) from exc


class GmailPollService:
    def __init__(
        self,
        repository: GmailRepository,
        session: AsyncSession,
        oauth: GoogleOAuth | None = None,
        client_factory: ClientFactory = GmailClient,
    ) -> None:
        self.repository = repository
        self.session = session
        self.oauth = oauth or GoogleOAuth()
        self.client_factory = client_factory

    async def poll(self, connection: GmailConnection, processor: InboxProcessor) -> PollSummary:
        if connection.revoked_at is not None:
            raise GmailNotConnectedError("La conexion de Gmail esta revocada")
        try:
            access_token = await self.oauth.refresh_access_token(
                decrypt_secret(connection.refresh_token)
            )
            summary = await processor.process(connection, self.client_factory(access_token))
        except GmailAccessRevokedError as exc:
            await self.repository.mark_revoked(connection)
            await self.session.commit()
            logger.warning(
                "Google revoco el acceso de Gmail de la organizacion %s; hay que reconectar",
                connection.organization_id,
            )
            raise GmailNotConnectedError(
                "Google revoco el acceso a Gmail; hay que volver a conectar la cuenta"
            ) from exc

        await self.repository.touch_polled(connection)
        await self.session.commit()
        return summary
