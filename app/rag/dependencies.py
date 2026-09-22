from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.organizations.dependencies import (
    get_current_organization,
    resolve_tenant_virtual_key,
)
from app.organizations.models import Organization
from app.rag.embeddings import EmbeddingClient
from app.rag.repository import KBRepository
from app.rag.service import RAGService


async def get_tenant_embedding_client(
    organization: Organization = Depends(get_current_organization),
) -> EmbeddingClient:
    """Misma virtual key (y misma logica de fallback a la de
    emergencia) que usa el chat -- ver
    app.organizations.dependencies.resolve_tenant_virtual_key, de
    donde se extrajo justamente para que esto no la duplique."""
    return EmbeddingClient(virtual_key=resolve_tenant_virtual_key(organization))


async def get_rag_service(
    session: AsyncSession = Depends(get_db),
    embedding_client: EmbeddingClient = Depends(get_tenant_embedding_client),
) -> RAGService:
    return RAGService(
        repository=KBRepository(session),
        embedding_client=embedding_client,
        session=session,
    )
