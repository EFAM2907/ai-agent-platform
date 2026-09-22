"""Endpoints de la integracion con Gmail.

Todos requieren ADMIN/OWNER salvo /callback: lo invoca el navegador
redirigido por Google, sin Bearer token -- su unica credencial es el
`state` firmado que /connect emitio para un admin autenticado.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_admin, require_password_changed
from app.core.rate_limit import rate_limit
from app.gmail.errors import (
    GmailAPIError,
    GmailAuthorizationError,
    GmailNotConfiguredError,
    GmailNotConnectedError,
)
from app.gmail.factory import build_inbox_processor
from app.gmail.repository import GmailRepository
from app.gmail.schemas import (
    GmailAuthorizationOut,
    GmailPollOut,
    GmailStatusOut,
    GmailUnresolvedListOut,
    GmailUnresolvedMessageOut,
)
from app.gmail.service import GmailConnectionService, GmailPollService
from app.llm.client import LLMClient
from app.organizations.dependencies import get_tenant_llm_client
from app.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gmail", tags=["gmail"])


def get_connection_service(session: AsyncSession = Depends(get_db)) -> GmailConnectionService:
    return GmailConnectionService(GmailRepository(session), session)


def get_poll_service(session: AsyncSession = Depends(get_db)) -> GmailPollService:
    return GmailPollService(GmailRepository(session), session)


def get_gmail_repository(session: AsyncSession = Depends(get_db)) -> GmailRepository:
    return GmailRepository(session)


def _page(title: str, message: str, status_code: int) -> HTMLResponse:
    # Solo se interpolan textos propios (nunca parametros del request).
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{title}</title>"
        f"<body style='font-family:sans-serif;max-width:32rem;margin:4rem auto'>"
        f"<h2>{title}</h2><p>{message}</p></body>",
        status_code=status_code,
    )


@router.get(
    "/connect",
    response_model=GmailAuthorizationOut,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def connect_gmail(
    admin: User = Depends(require_admin),
    service: GmailConnectionService = Depends(get_connection_service),
) -> GmailAuthorizationOut:
    """Devuelve la URL de consentimiento de Google; el frontend debe
    redirigir al navegador ahi. Google vuelve a /gmail/callback."""
    try:
        url = service.start_authorization(admin.id, admin.organization_id)
    except GmailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return GmailAuthorizationOut(authorization_url=url)


@router.get("/callback", include_in_schema=False)
async def gmail_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    service: GmailConnectionService = Depends(get_connection_service),
) -> HTMLResponse:
    if error or not code or not state:
        return _page(
            "No se conectó Gmail",
            "La autorización fue cancelada o está incompleta. Vuelve a intentarlo.",
            400,
        )
    try:
        connection = await service.complete_authorization(code, state)
    except GmailAuthorizationError as exc:
        return _page("No se conectó Gmail", str(exc), 400)
    except GmailNotConfiguredError:
        return _page("Gmail no está configurado", "Faltan credenciales de Google.", 503)
    except GmailAPIError:
        logger.exception("Fallo el callback de OAuth de Gmail")
        return _page("No se conectó Gmail", "Google no respondió. Inténtalo de nuevo.", 502)
    return _page(
        "Gmail conectado",
        f"La cuenta {connection.google_email} quedó conectada. Puedes cerrar esta pestaña.",
        200,
    )


@router.get("/status", response_model=GmailStatusOut, dependencies=[Depends(rate_limit)])
async def gmail_status(
    admin: User = Depends(require_admin),
    service: GmailConnectionService = Depends(get_connection_service),
) -> GmailStatusOut:
    connection = await service.get_active_connection(admin.organization_id)
    if connection is None:
        return GmailStatusOut(connected=False)
    return GmailStatusOut(
        connected=True,
        google_email=connection.google_email,
        connected_at=connection.created_at,
        last_polled_at=connection.last_polled_at,
    )


@router.post(
    "/poll",
    response_model=GmailPollOut,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def poll_gmail(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
    connection_service: GmailConnectionService = Depends(get_connection_service),
    poll_service: GmailPollService = Depends(get_poll_service),
    llm_client: LLMClient = Depends(get_tenant_llm_client),
) -> GmailPollOut:
    """Procesa el buzon ahora mismo (el poller en segundo plano hace lo
    mismo cada gmail_poll_interval_seconds)."""
    connection = await connection_service.get_active_connection(admin.organization_id)
    if connection is None:
        raise HTTPException(status_code=409, detail="Gmail no está conectado para esta organización")
    processor = build_inbox_processor(session, llm_client, admin.organization_id)
    try:
        summary = await poll_service.poll(connection, processor)
    except GmailNotConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except GmailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GmailAPIError:
        logger.exception("Fallo la Gmail API durante /gmail/poll")
        raise HTTPException(status_code=502, detail="No se pudo consultar Gmail")
    return GmailPollOut(
        examined=summary.examined,
        replied=summary.replied,
        skipped=summary.skipped,
        failed=summary.failed,
        skip_reasons=summary.skip_reasons,
    )


@router.get(
    "/unresolved",
    response_model=GmailUnresolvedListOut,
    dependencies=[Depends(rate_limit)],
)
async def gmail_unresolved(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    admin: User = Depends(require_admin),
    repository: GmailRepository = Depends(get_gmail_repository),
) -> GmailUnresolvedListOut:
    """Correos que llegaron y no tuvieron una respuesta util para quien
    los escribio -- para que un humano los revise en vez de que se
    pierdan en silencio (ver GmailRepository.list_unresolved). No exige
    que Gmail siga conectado: revisar el historial sigue siendo util
    despues de desconectar la cuenta."""
    connection = await repository.get_connection(admin.organization_id)
    if connection is None:
        return GmailUnresolvedListOut(total=0, returned=0, messages=[])

    rows, total = await repository.list_unresolved(connection.id, skip, limit)
    messages = [
        GmailUnresolvedMessageOut(
            id=row.id,
            sender=row.sender,
            tracking_number=row.tracking_number,
            outcome=row.outcome,
            detail=row.detail,
            attempts=row.attempts,
            processed_at=row.processed_at,
            # Abre el correo real en el buzon conectado -- solo quien
            # tiene acceso a esa cuenta de Google puede verlo.
            gmail_link=(
                f"https://mail.google.com/mail/?authuser={connection.google_email}"
                f"#all/{row.thread_id}"
                if row.thread_id
                else None
            ),
        )
        for row in rows
    ]
    return GmailUnresolvedListOut(total=total, returned=len(messages), messages=messages)


@router.delete(
    "/connection",
    status_code=204,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def disconnect_gmail(
    admin: User = Depends(require_admin),
    service: GmailConnectionService = Depends(get_connection_service),
) -> Response:
    if not await service.disconnect(admin.organization_id):
        raise HTTPException(status_code=404, detail="Gmail no está conectado")
    return Response(status_code=204)
