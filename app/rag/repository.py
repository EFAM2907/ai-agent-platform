from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models import KBArticle, KBChunk


class KBRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_article_by_title(
        self, organization_id: uuid.UUID, title: str
    ) -> KBArticle | None:
        """Usado por RAGService.ingest_article para decidir si un
        documento ya existe (upsert) o hay que crearlo -- el titulo es
        la unica identidad estable que un loader conoce entre corridas
        (un MarkdownLoader/PDFLoader siempre produce el mismo titulo
        para el mismo archivo; ver app.rag.ingestion). Excluye
        soft-deleted, mismo criterio que el resto del proyecto (ej.
        UserRepository.get_by_email)."""
        stmt = select(KBArticle).where(
            KBArticle.organization_id == organization_id,
            KBArticle.title == title,
            KBArticle.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_article(
        self, organization_id: uuid.UUID, title: str, content: str
    ) -> KBArticle:
        article = KBArticle(
            organization_id=organization_id, title=title, content=content
        )
        self.session.add(article)
        await self.session.flush()
        return article

    async def update_article_content(self, article: KBArticle, content: str) -> None:
        article.content = content
        await self.session.flush()

    async def delete_chunks_for_article(self, article_id: uuid.UUID) -> None:
        """DELETE a nivel SQL, no session.delete() sobre los objetos ORM
        -- mismo motivo que ChatRepository.delete_session: iterar
        article.chunks fuera de un await explicito revienta con
        MissingGreenlet en SQLAlchemy async. Se usa para reemplazar los
        chunks de un articulo que ya existia (ver RAGService.ingest_article),
        nunca para borrar el articulo en si."""
        await self.session.execute(delete(KBChunk).where(KBChunk.article_id == article_id))

    async def add_chunks(
        self,
        article: KBArticle,
        organization_id: uuid.UUID,
        chunks: list[tuple[str, list[float]]],
    ) -> list[KBChunk]:
        """chunks: lista de (texto, embedding) ya en orden -- el
        chunk_index se deriva de la posicion en la lista, no se
        recibe aparte, para que no puedan desincronizarse."""
        rows = [
            KBChunk(
                article_id=article.id,
                organization_id=organization_id,
                chunk_index=index,
                content=text,
                embedding=embedding,
            )
            for index, (text, embedding) in enumerate(chunks)
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def search_similar_chunks(
        self,
        organization_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[tuple[KBChunk, str, float]]:
        """Devuelve (chunk, titulo_del_articulo, distancia_coseno)
        ordenado por distancia ascendente (mas parecido primero,
        distancia 0 = identico).

        El titulo se trae con un join en la misma query, no accediendo
        a chunk.article.title despues -- SQLAlchemy async no permite
        lazy-load de relationships fuera de un await explicito
        (MissingGreenlet), y un join es ademas mas barato que N
        queries lazy por chunk.

        El filtro por organization_id va en el WHERE de esta query,
        nunca aplicado despues en Python sobre el resultado -- es la
        pieza concreta que evita que el contexto de un tenant se
        cuele en el prompt de otro. Mismo principio que
        verify_same_organization en el resto del proyecto, aplicado
        aca a nivel de busqueda vectorial."""
        distance = KBChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(KBChunk, KBArticle.title, distance.label("distance"))
            .join(KBArticle, KBChunk.article_id == KBArticle.id)
            .where(KBChunk.organization_id == organization_id)
            .order_by(distance)
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        return [(row.KBChunk, row.title, row.distance) for row in result]
