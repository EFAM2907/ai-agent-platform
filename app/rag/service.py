from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.chunking import chunk_text
from app.rag.embeddings import EmbeddingClient
from app.rag.models import KBArticle, KBChunk
from app.rag.repository import KBRepository


class RAGService:
    def __init__(
        self,
        repository: KBRepository,
        embedding_client: EmbeddingClient,
        session: AsyncSession,
    ) -> None:
        self.repository = repository
        self.embedding_client = embedding_client
        self.session = session

    async def ingest_article(
        self, organization_id: uuid.UUID, title: str, content: str
    ) -> KBArticle:
        """Crea el articulo, lo parte en chunks, genera un embedding
        por chunk y guarda todo en una sola transaccion -- un articulo
        nunca queda a medio indexar (con fila en kb_articles pero sin
        sus chunks) si algo falla a mitad de camino."""
        article = await self.repository.create_article(
            organization_id, title, content
        )

        pieces = chunk_text(content)
        if pieces:
            vectors = await self.embedding_client.embed(pieces)
            await self.repository.add_chunks(
                article, organization_id, list(zip(pieces, vectors))
            )

        await self.session.commit()
        return article

    async def retrieve(
        self, organization_id: uuid.UUID, query: str, top_k: int = 5
    ) -> list[tuple[KBChunk, str, float]]:
        """Embeddea la query del usuario y devuelve los top_k chunks
        mas parecidos de la KB de esa organizacion, cada uno con el
        titulo de su articulo y su distancia coseno (0 = identico).
        No filtra por un umbral de distancia aca a proposito -- esa
        decision (que tan relevante es 'suficientemente relevante') es
        del caller, que sabe para que va a usar los chunks (inyectarlos
        en el prompt, mostrar fuentes, etc.). Calibrar un umbral
        numerico necesitaria datos de eval que todavia no existen para
        RAG -- ver eval_harness/model_routing_eval.py para el mismo
        principio aplicado a routing."""
        query_vector = await self.embedding_client.embed_one(query)
        return await self.repository.search_similar_chunks(
            organization_id, query_vector, top_k=top_k
        )
