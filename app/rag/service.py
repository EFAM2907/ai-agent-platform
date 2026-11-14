from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.rag.access import normalize_access_level, readable_levels
from app.rag.chunking import chunk_text
from app.rag.embeddings import EMBEDDING_MODEL, EmbeddingClient
from app.rag.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion
from app.rag.models import SOURCE_MANUAL, KBArticle, KBChunk
from app.rag.repository import KBRepository
from app.users.models import UserRole

# Candidatos que trae cada ranker antes de fusionar: top_k * 4, con
# piso de 20, para que la fusion tenga de donde elegir aunque top_k sea
# chico.
_CANDIDATES_PER_TOP_K = 4
_MIN_CANDIDATES = 20


def content_hash(content: str) -> str:
    """SHA-256 hex del contenido en UTF-8. La migracion e3f6a9b2c5d7
    calcula lo mismo en SQL para los articulos que ya existian."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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
        force: bool = False,
        source_type: str = SOURCE_MANUAL,
        source_ref: str | None = None,
        mime_type: str | None = None,
        access_level: str | UserRole | None = None,
        source_modified_at: datetime | None = None,
    ) -> KBArticle:
        """Upsert por (organization_id, title): si ya existe un articulo
        con ese titulo para esa organizacion, se actualiza su contenido
        y se regeneran sus chunks desde cero; si no, se crea. Esto es lo
        que hace idempotente a scripts/ingest_kb_articles.py -- correrlo
        dos veces sobre la misma carpeta no duplica articulos, y
        editar un archivo fuente y volver a correrlo si actualiza el
        contenido indexado.

        El titulo es la unica identidad estable que un loader garantiza
        entre corridas (ver app.rag.ingestion) -- dos fuentes distintas
        que por accidente produzcan el mismo titulo pisan el mismo
        articulo, "el ultimo que se ingiere gana".

        Reparte el articulo en chunks, genera un embedding por chunk y
        guarda todo en una sola transaccion -- un articulo nunca queda
        a medio indexar si algo falla a mitad de camino. Los embeddings
        se calculan ANTES de tocar la base: si el proveedor falla
        (cuota, red), no queda nada a medias en la sesion.

        chunks: override opcional del chunking por defecto. None (el
        caso normal, markdown/PDF/Word) usa chunk_text(content) --
        ventanas de palabras pensadas para prosa. Una fuente que ya sabe
        como partirse a si misma lo pasa explicito -- ver
        app.rag.ingestion.excel_loader.ExcelLoader, que arma un chunk
        por fila.

        No se vuelve a chunkear ni a embeber si el contenido (hash
        SHA-256) y el modelo de embeddings son los mismos que la vez
        anterior: cada embedding es una llamada al proveedor, con cuota.
        Si lo unico que cambio son los metadatos (origen, nivel de
        acceso, fecha en la fuente) se actualizan sin tocar los
        embeddings. force=True reindexa igual -- necesario cuando cambia
        algo que NO esta en el contenido, como el algoritmo de chunking.
        Si en cambio cambia EMBEDDING_MODEL, los articulos viejos se
        reindexan solos en su proxima ingesta: se detecta por
        KBArticle.embedding_model."""
        digest = content_hash(content)
        metadata = {
            "source_type": source_type,
            "source_ref": source_ref,
            "mime_type": mime_type,
            "source_modified_at": source_modified_at,
        }
        # access_level=None = "no lo cambies": un articulo nuevo queda en
        # 'viewer' (default de la columna) y uno existente conserva el que
        # un admin le haya puesto -- sincronizar una carpeta o Drive nunca
        # debe volver a abrir un documento que se restringio a mano.
        if access_level is not None:
            metadata["access_level"] = normalize_access_level(access_level)

        article = await self.repository.get_article_by_title(organization_id, title)
        if (
            article is not None
            and not force
            and article.content_hash == digest
            and article.embedding_model == EMBEDDING_MODEL
        ):
            changed = {
                name: value
                for name, value in metadata.items()
                if getattr(article, name) != value
                # Un reingreso que no trae fecha de la fuente no debe borrar la que ya habia.
                and not (name in ("source_modified_at", "mime_type") and value is None)
            }
            if changed:
                await self.repository.update_article(article, **changed)
                await self.session.commit()
            return article

        pieces = chunks if chunks is not None else chunk_text(content)
        vectors = await self.embedding_client.embed(pieces) if pieces else []

        index_fields = {
            "content_hash": digest,
            "embedding_model": EMBEDDING_MODEL,
            "indexed_at": datetime.now(timezone.utc),
            **metadata,
        }
        if article is None:
            article = await self.repository.create_article(
                organization_id, title, content, **index_fields
            )
        else:
            await self.repository.update_article(article, content=content, **index_fields)
            await self.repository.delete_chunks_for_article(article.id)

        if pieces:
            await self.repository.add_chunks(
                article, organization_id, list(zip(pieces, vectors))
            )

        await self.session.commit()
        return article

    async def set_access_level(
        self, organization_id: uuid.UUID, article_id: uuid.UUID, access_level: str | UserRole
    ) -> KBArticle | None:
        """Cambia quien puede leer un documento, sin reindexar nada."""
        article = await self.repository.get_article(organization_id, article_id)
        if article is None:
            return None
        await self.repository.update_article(
            article, access_level=normalize_access_level(access_level)
        )
        await self.session.commit()
        return article

    async def delete_article(self, organization_id: uuid.UUID, article_id: uuid.UUID) -> bool:
        """Borrado logico + sus chunks. False si no existe en esta organizacion."""
        article = await self.repository.get_article(organization_id, article_id)
        if article is None:
            return False
        await self.repository.soft_delete_article(article)
        await self.session.commit()
        return True

    async def prune_source(
        self,
        organization_id: uuid.UUID,
        source_type: str,
        keep_refs: set[str],
        *,
        ref_prefix: str | None = None,
    ) -> int:
        """Retira los articulos de una fuente que YA NO estan en ella:
        la ingesta nunca borra por si sola, asi que sin esto un archivo
        eliminado de la carpeta (o de Drive) seguiria respondiendo
        preguntas para siempre. ref_prefix acota la poda a una parte de
        la fuente (una carpeta concreta), para que sincronizar una no
        retire lo de otra. Devuelve cuantos articulos retiro."""
        removed = 0
        for article in await self.repository.list_active_by_source(organization_id, source_type):
            if ref_prefix is not None and not (article.source_ref or "").startswith(ref_prefix):
                continue
            if article.source_ref in keep_refs:
                continue
            await self.repository.soft_delete_article(article)
            removed += 1
        if removed:
            await self.session.commit()
        return removed

    async def retrieve(
        self,
        organization_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        *,
        hybrid: bool | None = None,
        relevance_gate: bool = True,
        viewer_role: str | UserRole | None = None,
    ) -> list[tuple[KBChunk, str, float]]:
        """Devuelve los top_k chunks mas relevantes de la KB de esa
        organizacion, cada uno con el titulo de su articulo y un score
        de relevancia donde MAYOR es mejor.

        hybrid=True (default, ver settings.rag_hybrid_search_enabled):
        corre la busqueda vectorial y la de palabras clave, y fusiona
        los dos rankings con Reciprocal Rank Fusion (ver app.rag.fusion).
        El score es el RRF -- sirve para ordenar, no es una probabilidad
        ni una similitud, y no es comparable con el de otra query.
        hybrid=False: solo vectorial, el score es 1 - distancia coseno
        (1 = identico). Existe para poder medir una contra la otra en
        eval_harness/rag_eval.py y como interruptor de emergencia.

        Filtro de relevancia (relevance_gate, ver
        settings.rag_max_cosine_distance): si el chunk mas cercano esta
        mas lejos que el umbral Y ningun chunk contiene todos los
        terminos de la pregunta, devuelve [] -- "la KB no tiene nada
        sobre esto". Es un filtro de la pregunta entera, no de cada
        chunk: cuando pasa, igual se devuelven los top_k. relevance_gate=
        False lo salta (el eval lo usa para medir con y sin filtro).

        viewer_role: rol de quien pregunta. Solo se consideran los
        articulos cuyo access_level ese rol puede leer (ver
        app.rag.access). None = sin filtro, solo para procesos internos
        (evals, scripts)."""
        if hybrid is None:
            hybrid = settings.rag_hybrid_search_enabled
        levels = readable_levels(viewer_role)

        query_vector = await self.embedding_client.embed_one(query)

        threshold = settings.rag_max_cosine_distance
        gate_active = relevance_gate and threshold > 0

        if not hybrid:
            similar = await self.repository.search_similar_chunks(
                organization_id, query_vector, top_k=top_k, allowed_levels=levels
            )
            if gate_active and (not similar or similar[0][2] > threshold):
                return []
            return [(chunk, title, 1.0 - distance) for chunk, title, distance in similar]

        # Cada ranker trae mas candidatos que top_k: un chunk que queda
        # 7mo en la vectorial pero 1ro en palabras clave solo puede
        # subir si el 7mo llega a la fusion. Secuencial, no en
        # paralelo: ambas queries comparten la misma AsyncSession, que
        # no admite dos operaciones a la vez.
        candidates = max(top_k * _CANDIDATES_PER_TOP_K, _MIN_CANDIDATES)
        similar = await self.repository.search_similar_chunks(
            organization_id, query_vector, top_k=candidates, allowed_levels=levels
        )
        if gate_active and (not similar or similar[0][2] > threshold):
            # Lejos en el espacio vectorial: solo se salva si algun chunk
            # contiene todos los terminos (el caso de un codigo exacto).
            if not await self.repository.has_strict_keyword_match(
                organization_id, query, allowed_levels=levels
            ):
                return []
        keyword = await self.repository.search_keyword_chunks(
            organization_id, query, top_k=candidates, allowed_levels=levels
        )

        fused = reciprocal_rank_fusion(
            [similar, keyword], key=lambda row: row[0].id, k=DEFAULT_RRF_K
        )
        return [(chunk, title, score) for (chunk, title, _raw), score in fused[:top_k]]
