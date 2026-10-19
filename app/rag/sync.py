"""Sincronizar una FUENTE de documentos con la base de conocimiento.

Un conector (carpeta local, Google Drive, una subida...) solo tiene que
producir una lista de IngestedDocument con su source_ref; este modulo
hace lo comun: indexar lo nuevo o cambiado, saltar lo que no cambio y
-- lo que la ingesta sola nunca hacia -- retirar lo que desaparecio de
la fuente. Asi un conector nuevo es "leer y normalizar", sin volver a
resolver idempotencia, cuota de embeddings ni borrado.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from openai import RateLimitError

from app.rag.ingestion.base import IngestedDocument
from app.rag.service import RAGService
from app.users.models import UserRole

logger = logging.getLogger(__name__)

# El tier gratuito de embeddings de Gemini limita las requests POR MINUTO
# (cada texto de un batch cuenta como una): una carga inicial grande la
# supera. Un proceso de fondo espera a que se renueve la ventana y
# reintenta el MISMO documento -- seguro porque ingest_article calcula
# los embeddings antes de tocar la base (un fallo no deja nada a medias).
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 65


@dataclass
class SyncReport:
    documents: int = 0
    pruned: int = 0
    failed: list[str] = field(default_factory=list)


async def sync_documents(
    rag_service: RAGService,
    organization_id: uuid.UUID,
    documents: list[IngestedDocument],
    *,
    source_type: str,
    ref_prefix: str | None = None,
    prune: bool = True,
    keep_refs: set[str] | None = None,
    force: bool = False,
    access_level: str | UserRole | None = None,
    rate_limit_attempts: int = 1,
    rate_limit_wait_seconds: float = DEFAULT_RATE_LIMIT_WAIT_SECONDS,
) -> SyncReport:
    """Indexa `documents` y, si prune, retira los articulos de la misma
    fuente (source_type + ref_prefix) que ya no estan en la lista.

    keep_refs: referencias que existen en la fuente pero NO vienen en
    `documents` porque no cambiaron (un conector incremental, como Drive,
    no vuelve a descargar lo que ya tiene). Cuentan como "presentes" al
    podar.

    prune se salta si no hay ningun documento NI ninguna referencia que
    conservar: una carpeta mal montada o una cuenta de Drive sin acceso no
    debe interpretarse como "se borraron todos los documentos" y vaciar la
    base de conocimiento.

    rate_limit_attempts > 1 espera y reintenta ante el limite de cuota
    de embeddings (procesos de fondo); un endpoint HTTP lo deja en 1 y
    devuelve el error a quien llamo."""
    report = SyncReport()
    for document in documents:
        for attempt in range(1, max(rate_limit_attempts, 1) + 1):
            try:
                await rag_service.ingest_article(
                    organization_id,
                    document.title,
                    document.content,
                    chunks=document.chunks,
                    force=force,
                    source_type=source_type,
                    source_ref=document.source_ref,
                    mime_type=document.mime_type,
                    access_level=access_level,
                    source_modified_at=document.source_modified_at,
                )
                report.documents += 1
                break
            except RateLimitError:
                if attempt >= rate_limit_attempts:
                    raise
                logger.warning(
                    "Cuota de embeddings agotada en '%s' (intento %d/%d); esperando %ss",
                    document.title, attempt, rate_limit_attempts, rate_limit_wait_seconds,
                )
                await asyncio.sleep(rate_limit_wait_seconds)

    if prune and (documents or keep_refs):
        keep = {d.source_ref for d in documents if d.source_ref} | (keep_refs or set())
        report.pruned = await rag_service.prune_source(
            organization_id, source_type, keep, ref_prefix=ref_prefix
        )
    elif prune:
        logger.warning(
            "Sincronizacion de '%s' sin documentos: no se retira nada (¿fuente vacia o inaccesible?)",
            source_type,
        )
    return report
