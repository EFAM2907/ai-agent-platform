"""Endpoints de la integracion con Google Drive.

Todos requieren ADMIN/OWNER salvo /callback: lo invoca el navegador
redirigido por Google, sin Bearer token -- su unica credencial es el
`state` firmado que /connect emitio para un admin autenticado.

Flujo: /connect (consentimiento de solo lectura) -> PUT /folder (elegir la
carpeta) -> GET /preview (ver que se sincronizaria) -> POST /sync.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from openai import APIError as OpenAIAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_admin, require_password_changed
from app.core.rate_limit import rate_limit
from app.drive.errors import (
    DriveAPIError,
    DriveError,
    DriveFolderError,
    DriveFolderNotSetError,
    DriveNotConnectedError,
)
from app.drive.repository import DriveRepository
from app.drive.schemas import (
    DriveAuthorizationOut,
    DriveFileOutcomeOut,
    DriveFolderIn,
    DrivePreviewOut,
    DriveStatusOut,
    DriveSyncSummaryOut,
)
from app.drive.service import DriveService, SyncResult
from app.gmail.errors import (
    GmailAPIError,
    GmailAuthorizationError,
    GmailNotConfiguredError,
)
from app.rag.dependencies import get_rag_service
from app.rag.service import RAGService
from app.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drive", tags=["drive"])


def get_drive_service(session: AsyncSession = Depends(get_db)) -> DriveService:
    return DriveService(DriveRepository(session), session)


def _page(title: str, message: str, status_code: int) -> HTMLResponse:
    # Solo se interpolan textos propios (nunca parametros del request).
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{title}</title>"
        f"<body style='font-family:sans-serif;max-width:32rem;margin:4rem auto'>"
        f"<h2>{title}</h2><p>{message}</p></body>",
        status_code=status_code,
    )


def _http_error(exc: DriveError) -> HTTPException:
    if isinstance(exc, DriveNotConnectedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DriveFolderNotSetError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DriveFolderError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Fallo la Drive API")
    return HTTPException(status_code=502, detail="No se pudo consultar Google Drive")


def _outcomes(items) -> list[DriveFileOutcomeOut]:
    return [DriveFileOutcomeOut(**item) if isinstance(item, dict) else DriveFileOutcomeOut(
        name=item.name, path=item.path, status=item.status, reason=item.reason
    ) for item in items]


@router.get(
    "/connect",
    response_model=DriveAuthorizationOut,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def connect_drive(
    admin: User = Depends(require_admin),
    service: DriveService = Depends(get_drive_service),
) -> DriveAuthorizationOut:
    """URL de consentimiento de Google (solo lectura de Drive); el
    frontend redirige al navegador ahi. Google vuelve a /drive/callback."""
    try:
        url = service.start_authorization(admin.id, admin.organization_id)
    except GmailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return DriveAuthorizationOut(authorization_url=url)


@router.get("/callback", include_in_schema=False)
async def drive_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    service: DriveService = Depends(get_drive_service),
) -> HTMLResponse:
    if error or not code or not state:
        return _page(
            "No se conectó Drive",
            "La autorización fue cancelada o está incompleta. Vuelve a intentarlo.",
            400,
        )
    try:
        connection = await service.complete_authorization(code, state)
    except GmailAuthorizationError as exc:
        return _page("No se conectó Drive", str(exc), 400)
    except GmailNotConfiguredError:
        return _page("Drive no está configurado", "Faltan credenciales de Google.", 503)
    except (GmailAPIError, DriveError):
        logger.exception("Fallo el callback de OAuth de Drive")
        return _page("No se conectó Drive", "Google no respondió. Inténtalo de nuevo.", 502)
    return _page(
        "Drive conectado",
        f"La cuenta {connection.google_email} quedó conectada. Vuelve a la aplicación y elige la carpeta.",
        200,
    )


@router.get("/status", response_model=DriveStatusOut, dependencies=[Depends(rate_limit)])
async def drive_status(
    admin: User = Depends(require_admin),
    service: DriveService = Depends(get_drive_service),
) -> DriveStatusOut:
    connection = await service.get_active_connection(admin.organization_id)
    if connection is None:
        return DriveStatusOut(connected=False)
    last_sync = None
    if connection.last_sync:
        raw = connection.last_sync
        last_sync = DriveSyncSummaryOut(
            documents=raw.get("documents", 0),
            unchanged=raw.get("unchanged", 0),
            pruned=raw.get("pruned", 0),
            truncated=raw.get("truncated", False),
            unsupported=_outcomes(raw.get("unsupported", [])),
            failed=_outcomes(raw.get("failed", [])),
            at=raw.get("at"),
        )
    return DriveStatusOut(
        connected=True,
        google_email=connection.google_email,
        connected_at=connection.created_at,
        folder_id=connection.folder_id,
        folder_name=connection.folder_name,
        last_synced_at=connection.last_synced_at,
        last_sync=last_sync,
    )


@router.put(
    "/folder",
    response_model=DriveStatusOut,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def set_drive_folder(
    payload: DriveFolderIn,
    admin: User = Depends(require_admin),
    service: DriveService = Depends(get_drive_service),
) -> DriveStatusOut:
    """Elige la carpeta (enlace o id) que se sincroniza con la base de
    conocimiento. Se valida contra Drive: debe ser una carpeta que la
    cuenta conectada pueda ver."""
    try:
        await service.set_folder(admin.organization_id, payload.folder)
    except GmailAPIError as exc:
        raise HTTPException(status_code=502, detail="No se pudo consultar Google Drive") from exc
    except DriveError as exc:
        raise _http_error(exc)
    return await drive_status(admin, service)


@router.get(
    "/preview",
    response_model=DrivePreviewOut,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def preview_drive_sync(
    admin: User = Depends(require_admin),
    service: DriveService = Depends(get_drive_service),
    rag_service: RAGService = Depends(get_rag_service),
) -> DrivePreviewOut:
    """Que haria una sincronizacion ahora (nuevos, modificados, iguales,
    no soportados), sin descargar ni indexar nada."""
    try:
        result = await service.preview(admin.organization_id, rag_service)
    except DriveError as exc:
        raise _http_error(exc)
    counts = {"new": 0, "changed": 0, "unchanged": 0, "unsupported": 0}
    for item in result.files:
        if item.status in counts:
            counts[item.status] += 1
    return DrivePreviewOut(
        truncated=result.truncated,
        files=_outcomes(result.files),
        **counts,
    )


@router.post(
    "/sync",
    response_model=DriveSyncSummaryOut,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def sync_drive(
    admin: User = Depends(require_admin),
    service: DriveService = Depends(get_drive_service),
    rag_service: RAGService = Depends(get_rag_service),
) -> DriveSyncSummaryOut:
    """Sincroniza ahora: indexa lo nuevo o modificado y retira lo que ya
    no esta en la carpeta. Cada documento se confirma por separado, asi
    que si se agota la cuota de embeddings a mitad de camino (503) lo ya
    indexado se conserva y reintentar continua donde quedo."""
    try:
        result: SyncResult = await service.sync(admin.organization_id, rag_service)
    except OpenAIAPIError as exc:
        raise HTTPException(
            status_code=503,
            detail="El servicio de embeddings no esta disponible (¿cuota?). Lo ya indexado se conserva; "
            "intenta de nuevo en un minuto.",
        ) from exc
    except DriveError as exc:
        raise _http_error(exc)
    summary = result.summary()
    return DriveSyncSummaryOut(
        documents=summary["documents"],
        unchanged=summary["unchanged"],
        pruned=summary["pruned"],
        truncated=summary["truncated"],
        unsupported=_outcomes(summary["unsupported"]),
        failed=_outcomes(summary["failed"]),
        at=summary["at"],
    )


@router.delete(
    "/connection",
    status_code=204,
    dependencies=[Depends(rate_limit), Depends(require_password_changed)],
)
async def disconnect_drive(
    remove_documents: bool = Query(default=False),
    admin: User = Depends(require_admin),
    service: DriveService = Depends(get_drive_service),
    rag_service: RAGService = Depends(get_rag_service),
) -> Response:
    """Desconecta Drive. Los documentos ya indexados se conservan salvo
    remove_documents=true."""
    if not await service.disconnect(
        admin.organization_id, rag_service=rag_service, remove_documents=remove_documents
    ):
        raise HTTPException(status_code=404, detail="Drive no está conectado")
    return Response(status_code=204)
