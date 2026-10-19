"""Sincroniza, en segundo plano, las carpetas de Drive de todas las
organizaciones que tienen una conectada (ver
scripts/run_kb_ingestion_scheduler.py).

Una sesion por organizacion: un fallo (Drive revocado, cuota de
embeddings, red) en una no frena ni contamina a las demas. Como es un
proceso de fondo, espera y reintenta ante el limite de cuota de
embeddings en vez de fallar (ver app.rag.sync).
"""

from __future__ import annotations

import logging
import uuid

from app.core.database import SessionLocal
from app.drive.errors import DriveError
from app.drive.repository import DriveRepository
from app.drive.service import DriveService
from app.organizations.dependencies import resolve_tenant_virtual_key
from app.organizations.repository import OrganizationRepository
from app.rag.embeddings import EmbeddingClient
from app.rag.repository import KBRepository
from app.rag.service import RAGService

logger = logging.getLogger("drive_sync")

_RATE_LIMIT_ATTEMPTS = 5


async def sync_all_drive_connections() -> None:
    async with SessionLocal() as session:
        organization_ids = [
            c.organization_id for c in await DriveRepository(session).list_syncable_connections()
        ]

    for organization_id in organization_ids:
        async with SessionLocal() as session:
            try:
                organization = await OrganizationRepository(session).get_by_id(organization_id)
                if organization is None or organization.deleted_at is not None:
                    continue
                rag_service = RAGService(
                    repository=KBRepository(session),
                    embedding_client=EmbeddingClient(
                        virtual_key=resolve_tenant_virtual_key(organization)
                    ),
                    session=session,
                )
                service = DriveService(DriveRepository(session), session)
                result = await service.sync(
                    organization_id, rag_service, rate_limit_attempts=_RATE_LIMIT_ATTEMPTS
                )
                _log_result(organization_id, result)
            except DriveError as exc:
                logger.warning("org=%s: Drive no se pudo sincronizar (%s)", organization_id, exc)
            except Exception:
                logger.exception("org=%s: fallo la sincronizacion de Drive", organization_id)


def _log_result(organization_id: uuid.UUID, result) -> None:
    if result.documents or result.pruned or result.failed:
        logger.info(
            "org=%s drive: %d indexados, %d sin cambios, %d retirados, %d con error",
            organization_id, result.documents, result.unchanged, result.pruned, len(result.failed),
        )
