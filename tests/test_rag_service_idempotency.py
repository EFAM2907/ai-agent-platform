"""RAGService.ingest_article contra Postgres real (a diferencia de
tests/test_rag_service_ingest.py, que solo verifica con mocks que el
parametro `chunks` reemplaza a chunk_text()) -- lo que se prueba aca es
el comportamiento de upsert en si: que correr la ingesta dos veces
sobre el mismo (organization_id, titulo) actualice en vez de duplicar,
que los chunks viejos de verdad desaparezcan de la tabla (no solo que
el conteo final cuadre por casualidad), y que dos organizaciones nunca
se pisen entre si aunque usen el mismo titulo. Esto es lo que hace
idempotente a scripts/ingest_kb_articles.py -- correrlo dos veces sobre
la misma carpeta de kb_content/ ya no duplica articulos."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.models import KBArticle, KBChunk
from app.rag.repository import KBRepository
from app.rag.service import RAGService
from tests.factories import create_organization


class FakeEmbeddingClient:
    """Un vector deterministico por texto -- ningun test de este
    archivo le importa el CONTENIDO del embedding, solo que exista uno
    por chunk y que ingest_article no llame a LiteLLM de verdad."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def _service(db_session) -> RAGService:
    return RAGService(
        repository=KBRepository(db_session),
        embedding_client=FakeEmbeddingClient(),
        session=db_session,
    )


async def _articles(db_session, organization_id):
    return (
        (
            await db_session.execute(
                select(KBArticle).where(KBArticle.organization_id == organization_id)
            )
        )
        .scalars()
        .all()
    )


async def _chunks_of(db_session, article_id):
    return (
        (await db_session.execute(select(KBChunk).where(KBChunk.article_id == article_id)))
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_ingesting_the_same_title_twice_does_not_duplicate_the_article(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)

    first = await service.ingest_article(org.id, "Politica de retrasos", "contenido original")
    second = await service.ingest_article(org.id, "Politica de retrasos", "contenido original")

    assert first.id == second.id
    assert len(await _articles(db_session, org.id)) == 1


@pytest.mark.asyncio
async def test_re_ingesting_with_new_content_replaces_content_and_chunks(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)

    article = await service.ingest_article(
        org.id, "Tarifas", "uno dos tres cuatro cinco seis siete ocho"
    )
    [old_chunk] = await _chunks_of(db_session, article.id)

    updated = await service.ingest_article(org.id, "Tarifas", "contenido completamente nuevo")

    assert updated.id == article.id  # mismo articulo, no uno nuevo al lado
    assert updated.content == "contenido completamente nuevo"
    [new_chunk] = await _chunks_of(db_session, article.id)
    assert new_chunk.content == "contenido completamente nuevo"
    assert new_chunk.id != old_chunk.id  # el chunk viejo de verdad se borro, no quedo huerfano


@pytest.mark.asyncio
async def test_re_ingesting_with_explicit_chunks_replaces_them_instead_of_accumulating(
    db_session,
):
    # Mismo escenario que ExcelLoader: una hoja que antes tenia 2 filas
    # y ahora tiene 3 -- la segunda corrida no debe dejar los 2 chunks
    # viejos sueltos al lado de los 3 nuevos.
    org = await create_organization(db_session)
    service = _service(db_session)

    article = await service.ingest_article(
        org.id, "Tarifas.xlsx - Hoja1", "tabla v1", chunks=["fila 1", "fila 2"]
    )
    await service.ingest_article(
        org.id, "Tarifas.xlsx - Hoja1", "tabla v2", chunks=["fila 1", "fila 2", "fila 3"]
    )

    chunks = await _chunks_of(db_session, article.id)
    assert sorted(c.content for c in chunks) == ["fila 1", "fila 2", "fila 3"]


@pytest.mark.asyncio
async def test_two_organizations_with_the_same_title_never_collide(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    service = _service(db_session)

    article_a = await service.ingest_article(org_a.id, "Servicios", "contenido de A")
    article_b = await service.ingest_article(org_b.id, "Servicios", "contenido de B")

    assert article_a.id != article_b.id
    assert len(await _articles(db_session, org_a.id)) == 1
    assert len(await _articles(db_session, org_b.id)) == 1
    refreshed_a = (await _articles(db_session, org_a.id))[0]
    assert refreshed_a.content == "contenido de A"
