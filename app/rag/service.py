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
        self,
        organization_id: uuid.UUID,
        title: str,
        content: str,
        *,
        chunks: list[str] | None = None,
    ) -> KBArticle:
        """Upsert por (organization_id, title): si ya existe un articulo
        con ese titulo para esa organizacion, se actualiza su contenido
        y se regeneran sus chunks desde cero; si no, se crea. Esto es lo
        que hace idempotente a scripts/ingest_kb_articles.py -- correrlo
        dos veces sobre la misma carpeta ya no duplica articulos, y
        editar un archivo fuente y volver a correrlo si actualiza el
        contenido indexado (antes de este cambio, ninguna de las dos
        cosas era cierta: cada corrida insertaba articulos nuevos sin
        mirar si el titulo ya existia).

        El titulo es la unica identidad estable que un loader garantiza
        entre corridas (ver app.rag.ingestion) -- dos fuentes distintas
        que por accidente produzcan el mismo titulo pisan el mismo
        articulo, "el ultimo que se ingiere gana"; con los loaders
        actuales (titulo = encabezado H1 o nombre de archivo/hoja) eso
        requeriria un choque de nombres deliberado, no algo que pase por
        azar.

        Reparte el articulo en chunks, genera un embedding por chunk y
        guarda todo en una sola transaccion -- un articulo nunca queda
        a medio indexar (con fila en kb_articles pero sin sus chunks, o
        con chunks viejos y contenido nuevo) si algo falla a mitad de
        camino.

        chunks: override opcional del chunking por defecto. None (el
        caso normal, markdown/PDF) usa chunk_text(content) -- ventanas
        de palabras pensadas para prosa. Una fuente que ya sabe como
        partirse a si misma lo pasa explicito -- ver
        app.rag.ingestion.excel_loader.ExcelLoader, que arma un chunk
        por fila en vez de dejar que chunk_text() trate una tabla como
        si fuera un parrafo."""
        article = await self.repository.get_article_by_title(organization_id, title)
        if article is None:
            article = await self.repository.create_article(organization_id, title, content)
        else:
            await self.repository.update_article_content(article, content)
            await self.repository.delete_chunks_for_article(article.id)

        pieces = chunks if chunks is not None else chunk_text(content)
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
