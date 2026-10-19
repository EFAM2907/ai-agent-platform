"""API REST de la base de conocimiento.

- Administrar documentos (listar, subir, borrar) y ver estadisticas:
  solo ADMIN/OWNER.
- Buscar (POST /kb/search): cualquier usuario autenticado, pero solo ve
  articulos cuyo access_level su rol puede leer. Es el mismo
  RAGService.retrieve que usa el chat, expuesto como endpoint para la
  tool de MCP `search_knowledge_base` (RAG agentico: el modelo decide
  cuando buscar de nuevo y con que palabras) y para probar la KB desde
  la interfaz sin gastar una llamada de chat.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from openai import APIError as OpenAIAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_admin, require_password_changed
from app.core.rate_limit import rate_limit
from app.organizations.dependencies import get_current_organization
from app.organizations.models import Organization
from app.rag.access import normalize_access_level
from app.rag.dependencies import get_rag_service
from app.rag.ingestion import DocumentLoadError, UnsupportedSourceError, load_documents
from app.rag.ingestion.registry import supported_extensions
from app.rag.models import SOURCE_UPLOAD
from app.rag.repository import KBRepository
from app.rag.schemas import (
    KBDocumentListResponse,
    KBDocumentResponse,
    KBSearchHit,
    KBSearchRequest,
    KBSearchResponse,
    KBStatsResponse,
    KBUploadResponse,
    KBUploadResult,
)
from app.rag.service import RAGService
from app.users.models import User

router = APIRouter(prefix="/kb", tags=["knowledge-base"], dependencies=[Depends(rate_limit)])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
_SNIPPET_CHARS = 400


def _to_response(article, chunk_count: int) -> KBDocumentResponse:
    return KBDocumentResponse(
        id=article.id,
        title=article.title,
        source_type=article.source_type,
        source_ref=article.source_ref,
        mime_type=article.mime_type,
        access_level=article.access_level,
        chunk_count=chunk_count,
        indexed_at=article.indexed_at,
        source_modified_at=article.source_modified_at,
        updated_at=article.updated_at,
    )


@router.get("/documents", response_model=KBDocumentListResponse)
async def list_documents(
    skip: int = 0,
    limit: int = 50,
    source_type: str | None = None,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> KBDocumentListResponse:
    limit = max(1, min(limit, 100))
    repository = KBRepository(session)
    rows = await repository.list_articles(
        admin.organization_id, skip=skip, limit=limit, source_type=source_type
    )
    total = await repository.count_articles(admin.organization_id, source_type)
    return KBDocumentListResponse(
        total=total,
        returned=len(rows),
        documents=[_to_response(article, count) for article, count in rows],
    )


@router.get("/stats", response_model=KBStatsResponse)
async def kb_stats(
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)
) -> KBStatsResponse:
    repository = KBRepository(session)
    by_source: dict[str, int] = {}
    for source in ("file", "upload", "drive", "manual"):
        count = await repository.count_articles(admin.organization_id, source)
        if count:
            by_source[source] = count
    return KBStatsResponse(
        articles=await repository.count_articles(admin.organization_id),
        chunks=await repository.count_chunks(admin.organization_id),
        by_source=by_source,
        supported_formats=supported_extensions(),
    )


@router.post("/documents", response_model=KBUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    access_level: str = Form("viewer"),
    admin: User = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
) -> KBUploadResponse:
    """Sube e indexa un documento (md, pdf, xlsx, docx). Un archivo puede
    producir varios documentos (un Excel con varias hojas). Volver a
    subir el mismo archivo no duplica nada: se actualiza por titulo, y si
    el contenido no cambio no se vuelve a llamar al proveedor de
    embeddings."""
    filename = Path(file.filename or "").name  # nunca una ruta del cliente
    suffix = Path(filename).suffix.lower()
    if suffix not in supported_extensions():
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato '{suffix or 'sin extension'}' no soportado "
            f"(soportados: {', '.join(supported_extensions())})",
        )
    try:
        level = normalize_access_level(access_level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"El archivo supera el maximo de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not data:
        raise HTTPException(status_code=400, detail="El archivo esta vacio")

    # Los loaders trabajan sobre rutas de archivo y derivan el titulo del
    # nombre: se escribe a un directorio temporal conservando el nombre.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        path.write_bytes(data)
        try:
            documents = load_documents(path)
        except UnsupportedSourceError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except DocumentLoadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    results: list[KBUploadResult] = []
    try:
        for document in documents:
            existing = await rag_service.repository.get_article_by_title(
                admin.organization_id, document.title
            )
            before = existing.indexed_at if existing else None
            article = await rag_service.ingest_article(
                admin.organization_id,
                document.title,
                document.content,
                chunks=document.chunks,
                source_type=SOURCE_UPLOAD,
                source_ref=filename,
                mime_type=file.content_type,
                access_level=level,
            )
            if existing is None:
                outcome = "created"
            elif article.indexed_at != before:
                outcome = "updated"
            else:
                outcome = "unchanged"
            chunk_count = await rag_service.repository.count_chunks_for_article(article.id)
            results.append(
                KBUploadResult(
                    id=article.id, title=article.title, chunk_count=chunk_count, status=outcome
                )
            )
    except OpenAIAPIError as exc:
        # Cuota o caida del gateway de embeddings: lo ya indexado queda
        # (cada articulo se confirma por separado); el resto, a reintentar.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de embeddings no esta disponible (¿cuota?). Intenta de nuevo en un minuto.",
        ) from exc

    return KBUploadResponse(filename=filename, documents=results)


class AccessLevelUpdate(BaseModel):
    access_level: str


@router.patch("/documents/{article_id}", response_model=KBDocumentResponse)
async def update_document_access(
    article_id: uuid.UUID,
    payload: AccessLevelUpdate,
    admin: User = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
) -> KBDocumentResponse:
    """Cambia el nivel de acceso de un documento (quien puede leerlo)."""
    try:
        article = await rag_service.set_access_level(
            admin.organization_id, article_id, payload.access_level
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if article is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    count = await rag_service.repository.count_chunks_for_article(article.id)
    return _to_response(article, count)


@router.delete("/documents/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    article_id: uuid.UUID,
    admin: User = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
) -> None:
    if not await rag_service.delete_article(admin.organization_id, article_id):
        raise HTTPException(status_code=404, detail="Documento no encontrado")


@router.post("/search", response_model=KBSearchResponse)
async def search_kb(
    payload: KBSearchRequest,
    current_user: User = Depends(require_password_changed),
    organization: Organization = Depends(get_current_organization),
    rag_service: RAGService = Depends(get_rag_service),
) -> KBSearchResponse:
    try:
        rows = await rag_service.retrieve(
            organization.id,
            payload.query,
            top_k=payload.top_k,
            viewer_role=current_user.role,
        )
    except OpenAIAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de embeddings no esta disponible. Intenta de nuevo en un minuto.",
        ) from exc
    return KBSearchResponse(
        query=payload.query,
        results=[
            KBSearchHit(
                article_id=chunk.article_id,
                title=title,
                chunk_index=chunk.chunk_index,
                score=round(score, 5),
                snippet=chunk.content[:_SNIPPET_CHARS],
            )
            for chunk, title, score in rows
        ],
    )
