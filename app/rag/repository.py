from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Text, cast, delete, func, literal_column, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models import FTS_CONFIG, KBArticle, KBChunk


class KBRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Articulos
    # ------------------------------------------------------------------
    async def get_article_by_title(
        self, organization_id: uuid.UUID, title: str
    ) -> KBArticle | None:
        """Usado por RAGService.ingest_article para decidir si un
        documento ya existe (upsert) o hay que crearlo -- el titulo es
        la unica identidad estable que un loader conoce entre corridas
        (un MarkdownLoader/PDFLoader siempre produce el mismo titulo
        para el mismo archivo; ver app.rag.ingestion). Excluye
        soft-deleted, mismo criterio que el resto del proyecto (ej.
        UserRepository.get_by_email); el indice parcial
        ix_kb_articles_title_unique_active garantiza que haya a lo sumo
        uno."""
        stmt = select(KBArticle).where(
            KBArticle.organization_id == organization_id,
            KBArticle.title == title,
            KBArticle.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_article(
        self, organization_id: uuid.UUID, article_id: uuid.UUID
    ) -> KBArticle | None:
        stmt = select(KBArticle).where(
            KBArticle.id == article_id,
            KBArticle.organization_id == organization_id,
            KBArticle.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_articles(
        self,
        organization_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
        source_type: str | None = None,
    ) -> list[tuple[KBArticle, int]]:
        """(articulo, cantidad de chunks) mas recientes primero."""
        chunk_count = (
            select(func.count(KBChunk.id))
            .where(KBChunk.article_id == KBArticle.id)
            .correlate(KBArticle)
            .scalar_subquery()
        )
        stmt = (
            select(KBArticle, chunk_count)
            .where(KBArticle.organization_id == organization_id, KBArticle.deleted_at.is_(None))
            .order_by(KBArticle.updated_at.desc(), KBArticle.title)
            .offset(skip)
            .limit(limit)
        )
        if source_type:
            stmt = stmt.where(KBArticle.source_type == source_type)
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]

    async def count_articles(
        self, organization_id: uuid.UUID, source_type: str | None = None
    ) -> int:
        stmt = select(func.count(KBArticle.id)).where(
            KBArticle.organization_id == organization_id, KBArticle.deleted_at.is_(None)
        )
        if source_type:
            stmt = stmt.where(KBArticle.source_type == source_type)
        return (await self.session.execute(stmt)).scalar_one()

    async def count_chunks(self, organization_id: uuid.UUID) -> int:
        stmt = (
            select(func.count(KBChunk.id))
            .join(KBArticle, KBChunk.article_id == KBArticle.id)
            .where(KBChunk.organization_id == organization_id, KBArticle.deleted_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def count_chunks_for_article(self, article_id: uuid.UUID) -> int:
        stmt = select(func.count(KBChunk.id)).where(KBChunk.article_id == article_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def list_active_by_source(
        self, organization_id: uuid.UUID, source_type: str
    ) -> list[KBArticle]:
        stmt = select(KBArticle).where(
            KBArticle.organization_id == organization_id,
            KBArticle.source_type == source_type,
            KBArticle.deleted_at.is_(None),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_article(
        self, organization_id: uuid.UUID, title: str, content: str, **fields: Any
    ) -> KBArticle:
        article = KBArticle(
            organization_id=organization_id, title=title, content=content, **fields
        )
        self.session.add(article)
        await self.session.flush()
        return article

    async def update_article(
        self, article: KBArticle, *, content: str | None = None, **fields: Any
    ) -> None:
        if content is not None:
            article.content = content
        for name, value in fields.items():
            setattr(article, name, value)
        await self.session.flush()

    async def soft_delete_article(self, article: KBArticle) -> None:
        """Marca el articulo como borrado Y elimina sus chunks: sin
        chunks no hay forma de que vuelva a aparecer en una busqueda, y
        el indice vectorial no acumula basura de documentos retirados."""
        article.deleted_at = datetime.now(timezone.utc)
        await self.session.execute(delete(KBChunk).where(KBChunk.article_id == article.id))
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
                content=content,
                embedding=embedding,
            )
            for index, (content, embedding) in enumerate(chunks)
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    # ------------------------------------------------------------------
    # Busqueda
    # ------------------------------------------------------------------
    @staticmethod
    def _visibility(organization_id: uuid.UUID, allowed_levels: list[str] | None) -> list:
        """Condiciones comunes a TODA busqueda: la organizacion en el
        WHERE de esta misma query (nunca filtrada despues en Python --
        es la pieza que evita que el contexto de un tenant se cuele en
        el de otro), articulos no borrados y, si se conoce el rol de
        quien pregunta, solo los niveles de acceso que ese rol puede
        leer."""
        conditions = [
            KBChunk.organization_id == organization_id,
            KBArticle.deleted_at.is_(None),
        ]
        if allowed_levels is not None:
            conditions.append(KBArticle.access_level.in_(allowed_levels))
        return conditions

    async def search_similar_chunks(
        self,
        organization_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int = 5,
        allowed_levels: list[str] | None = None,
    ) -> list[tuple[KBChunk, str, float]]:
        """Devuelve (chunk, titulo_del_articulo, distancia_coseno)
        ordenado por distancia ascendente (mas parecido primero,
        distancia 0 = identico).

        El titulo se trae con un join en la misma query, no accediendo
        a chunk.article.title despues -- SQLAlchemy async no permite
        lazy-load de relationships fuera de un await explicito
        (MissingGreenlet), y un join es ademas mas barato que N
        queries lazy por chunk.

        hnsw.iterative_scan: HNSW recupera ef_search candidatos y
        DESPUES se aplican los filtros (organizacion, no borrado,
        nivel de acceso); con muchos tenants, un tenant chico podria
        quedarse con menos de top_k resultados aunque existan. El scan
        iterativo (pgvector >= 0.8) sigue buscando hasta completar,
        manteniendo el orden exacto. SET LOCAL: solo dura esta
        transaccion."""
        await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        distance = KBChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(KBChunk, KBArticle.title, distance.label("distance"))
            .join(KBArticle, KBChunk.article_id == KBArticle.id)
            .where(*self._visibility(organization_id, allowed_levels))
            .order_by(distance)
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        return [(row.KBChunk, row.title, row.distance) for row in result]

    @staticmethod
    def _tsquery_and(query: str):
        regconfig = literal_column(f"'{FTS_CONFIG}'::regconfig")
        return func.plainto_tsquery(regconfig, query)

    async def has_strict_keyword_match(
        self,
        organization_id: uuid.UUID,
        query: str,
        allowed_levels: list[str] | None = None,
    ) -> bool:
        """True si algun chunk visible contiene TODOS los terminos
        significativos de la query (AND, no el OR de
        search_keyword_chunks). Es la senal de relevancia por palabras
        clave del filtro de RAGService.retrieve: un codigo exacto
        ("OFC-MED-003", "N-07") puede quedar lejos de su chunk en el
        espacio vectorial y aun asi coincidir por completo aca. Una
        pregunta solo de stopwords produce un tsquery vacio y devuelve
        False."""
        stmt = (
            select(KBChunk.id)
            .join(KBArticle, KBChunk.article_id == KBArticle.id)
            .where(
                *self._visibility(organization_id, allowed_levels),
                KBChunk.content_tsv.op("@@")(self._tsquery_and(query)),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def search_keyword_chunks(
        self,
        organization_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        allowed_levels: list[str] | None = None,
    ) -> list[tuple[KBChunk, str, float]]:
        """Busqueda por palabras clave (full-text de Postgres) sobre
        kb_chunks.content_tsv. Devuelve (chunk, titulo, ts_rank) ordenado
        de mayor a menor rank -- a diferencia de search_similar_chunks,
        aca MAYOR es mejor.

        Complementa a la busqueda vectorial en lo que los embeddings
        representan peor: numeros de guia, siglas y terminos exactos
        ("550201", "novedad", "contra entrega").

        plainto_tsquery une los terminos con AND, y una pregunta en
        lenguaje natural casi nunca tiene TODAS sus palabras en un solo
        chunk -- se cambia a OR para que un chunk que contenga la
        mayoria de los terminos relevantes igual califique (ts_rank ya
        pone mas arriba a quien contiene mas). plainto_tsquery
        neutraliza los operadores que escriba el usuario, asi que
        reemplazar ' & ' solo toca los que pone el propio Postgres.

        Una pregunta compuesta solo por stopwords produce un tsquery
        vacio y no devuelve filas (no es un error)."""
        or_query = cast(
            func.replace(cast(self._tsquery_and(query), Text), " & ", " | "), TSQUERY
        )
        rank = func.ts_rank(KBChunk.content_tsv, or_query)
        stmt = (
            select(KBChunk, KBArticle.title, rank.label("rank"))
            .join(KBArticle, KBChunk.article_id == KBArticle.id)
            .where(
                *self._visibility(organization_id, allowed_levels),
                KBChunk.content_tsv.op("@@")(or_query),
            )
            .order_by(rank.desc(), KBChunk.id)
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        return [(row.KBChunk, row.title, row.rank) for row in result]
