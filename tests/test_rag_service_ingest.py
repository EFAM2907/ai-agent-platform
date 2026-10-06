"""Tests de RAGService.ingest_article con mocks (mismo patron que
tests/test_model_router.py) -- no necesitan base de datos: lo que se
prueba aca es que el parametro `chunks` (usado por ExcelLoader para
saltarse chunk_text) efectivamente reemplaza el chunking por defecto en
vez de sumarse a el."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.rag.service import RAGService


def _service(repository, embedding_client) -> RAGService:
    return RAGService(
        repository=repository, embedding_client=embedding_client, session=AsyncMock()
    )


@pytest.mark.asyncio
async def test_sin_chunks_explicitos_usa_chunk_text_por_defecto():
    repository = AsyncMock()
    embedding_client = AsyncMock()
    embedding_client.embed.return_value = [[0.1, 0.2]]

    await _service(repository, embedding_client).ingest_article(
        organization_id="org-1", title="Titulo", content="una dos tres"
    )

    embedding_client.embed.assert_awaited_once_with(["una dos tres"])


@pytest.mark.asyncio
async def test_con_chunks_explicitos_no_llama_chunk_text():
    repository = AsyncMock()
    embedding_client = AsyncMock()
    embedding_client.embed.return_value = [[0.1], [0.2]]

    custom_chunks = ["Servicio: Nacional; Precio: 25000", "Servicio: Urbano; Precio: 12000"]

    await _service(repository, embedding_client).ingest_article(
        organization_id="org-1",
        title="Tarifas",
        content="| Servicio | Precio |\n...",
        chunks=custom_chunks,
    )

    embedding_client.embed.assert_awaited_once_with(custom_chunks)


@pytest.mark.asyncio
async def test_chunks_vacios_no_llama_al_embedding_client():
    repository = AsyncMock()
    embedding_client = AsyncMock()

    await _service(repository, embedding_client).ingest_article(
        organization_id="org-1", title="Hoja vacia", content="", chunks=[]
    )

    embedding_client.embed.assert_not_awaited()
    repository.add_chunks.assert_not_awaited()
