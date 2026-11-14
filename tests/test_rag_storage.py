"""Almacenamiento de la KB pensado para crecer: origen y version por
articulo, borrado logico, poda de una fuente y acceso por rol. Postgres
real, embeddings falsos (ningun test llama a un proveedor)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.rag import embeddings as embeddings_module
from app.rag.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from app.rag.models import SOURCE_DRIVE, SOURCE_FILE, SOURCE_UPLOAD, KBArticle, KBChunk
from app.rag.repository import KBRepository
from app.rag.service import RAGService, content_hash
from tests.factories import create_organization


class CountingEmbeddings:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += len(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    async def embed_one(self, text):
        return (await self.embed([text]))[0]


@pytest.fixture(autouse=True)
def no_relevance_gate(monkeypatch):
    # Estos tests prueban almacenamiento y acceso, no el filtro de relevancia.
    monkeypatch.setattr(settings, "rag_max_cosine_distance", 0)


def _service(db_session, embeddings=None):
    embeddings = embeddings or CountingEmbeddings()
    return (
        RAGService(
            repository=KBRepository(db_session), embedding_client=embeddings, session=db_session
        ),
        embeddings,
    )


@pytest.mark.asyncio
async def test_ingest_records_source_hash_and_embedding_model(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)

    article = await service.ingest_article(
        org.id, "Manual", "contenido", source_type=SOURCE_UPLOAD,
        source_ref="manual.pdf", mime_type="application/pdf", access_level="admin",
    )

    assert article.source_type == SOURCE_UPLOAD
    assert article.source_ref == "manual.pdf"
    assert article.mime_type == "application/pdf"
    assert article.access_level == "admin"
    assert article.content_hash == content_hash("contenido")
    assert article.embedding_model == EMBEDDING_MODEL
    assert article.indexed_at is not None


@pytest.mark.asyncio
async def test_metadata_only_change_does_not_reembed(db_session):
    org = await create_organization(db_session)
    service, embeddings = _service(db_session)
    await service.ingest_article(org.id, "Manual", "contenido", access_level="viewer")
    calls_after_first = embeddings.calls

    updated = await service.ingest_article(org.id, "Manual", "contenido", access_level="admin")

    assert embeddings.calls == calls_after_first  # sin tocar el proveedor
    assert updated.access_level == "admin"  # pero el metadato si cambio


@pytest.mark.asyncio
async def test_changed_embedding_model_triggers_reindex(db_session):
    org = await create_organization(db_session)
    service, embeddings = _service(db_session)
    article = await service.ingest_article(org.id, "Manual", "contenido")
    article.embedding_model = "modelo-viejo"  # simula un articulo indexado con otro modelo
    await db_session.flush()
    calls = embeddings.calls

    await service.ingest_article(org.id, "Manual", "contenido")

    assert embeddings.calls > calls
    assert article.embedding_model == EMBEDDING_MODEL


@pytest.mark.asyncio
async def test_reingest_without_source_date_keeps_the_existing_one(db_session):
    from datetime import datetime, timezone

    org = await create_organization(db_session)
    service, _ = _service(db_session)
    when = datetime(2026, 1, 2, tzinfo=timezone.utc)
    article = await service.ingest_article(
        org.id, "Doc", "contenido", source_type=SOURCE_DRIVE, source_modified_at=when
    )

    await service.ingest_article(org.id, "Doc", "contenido", source_type=SOURCE_DRIVE)

    assert article.source_modified_at == when


@pytest.mark.asyncio
async def test_delete_article_removes_chunks_and_hides_it_from_search(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    article = await service.ingest_article(org.id, "Politica", "tarifa especial 998877")

    assert await service.delete_article(org.id, article.id) is True

    chunks = (await db_session.execute(select(KBChunk).where(KBChunk.article_id == article.id))).all()
    assert chunks == []
    assert await service.retrieve(org.id, "tarifa especial 998877", hybrid=True) == []
    assert await service.delete_article(org.id, article.id) is False  # ya no existe


@pytest.mark.asyncio
async def test_a_deleted_title_can_be_ingested_again(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    first = await service.ingest_article(org.id, "Politica", "version uno")
    await service.delete_article(org.id, first.id)

    second = await service.ingest_article(org.id, "Politica", "version dos")

    assert second.id != first.id  # indice unico parcial: el borrado no bloquea el titulo
    assert second.content == "version dos"


@pytest.mark.asyncio
async def test_prune_source_retires_only_missing_articles_of_that_source(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    keep = await service.ingest_article(org.id, "Sigue", "a", source_type=SOURCE_DRIVE, source_ref="drive:1")
    gone = await service.ingest_article(org.id, "Se fue", "b", source_type=SOURCE_DRIVE, source_ref="drive:2")
    other = await service.ingest_article(org.id, "De otra fuente", "c", source_type=SOURCE_FILE, source_ref="x.md")

    removed = await service.prune_source(org.id, SOURCE_DRIVE, {"drive:1"})

    assert removed == 1
    active = {a.title for a in await KBRepository(db_session).list_active_by_source(org.id, SOURCE_DRIVE)}
    assert active == {"Sigue"}
    assert (await db_session.get(KBArticle, gone.id)).deleted_at is not None
    assert (await db_session.get(KBArticle, other.id)).deleted_at is None
    assert (await db_session.get(KBArticle, keep.id)).deleted_at is None


@pytest.mark.asyncio
async def test_prune_source_respects_the_ref_prefix(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    await service.ingest_article(org.id, "A", "a", source_type=SOURCE_DRIVE, source_ref="carpeta1/a")
    await service.ingest_article(org.id, "B", "b", source_type=SOURCE_DRIVE, source_ref="carpeta2/b")

    removed = await service.prune_source(org.id, SOURCE_DRIVE, set(), ref_prefix="carpeta1/")

    assert removed == 1  # solo la carpeta1; la carpeta2 no se toca


@pytest.mark.asyncio
async def test_retrieve_respects_access_level_by_role(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    await service.ingest_article(org.id, "Publico", "informacion general", access_level="viewer")
    await service.ingest_article(org.id, "Solo admins", "informacion salarial", access_level="admin")

    def titles(rows):
        return {title for _c, title, _s in rows}

    viewer = await service.retrieve(org.id, "informacion", top_k=5, hybrid=False, viewer_role="viewer")
    member = await service.retrieve(org.id, "informacion", top_k=5, hybrid=False, viewer_role="member")
    admin = await service.retrieve(org.id, "informacion", top_k=5, hybrid=False, viewer_role="admin")
    internal = await service.retrieve(org.id, "informacion", top_k=5, hybrid=False)

    assert titles(viewer) == {"Publico"}
    assert titles(member) == {"Publico"}
    assert titles(admin) == {"Publico", "Solo admins"}
    assert titles(internal) == {"Publico", "Solo admins"}  # None = proceso interno, sin filtro


@pytest.mark.asyncio
async def test_access_level_also_applies_to_keyword_search_and_strict_match(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    await service.ingest_article(org.id, "Secreto", "codigo reservado XK-7731", access_level="admin")
    repository = KBRepository(db_session)

    assert await repository.search_keyword_chunks(org.id, "XK-7731", allowed_levels=["viewer"]) == []
    assert not await repository.has_strict_keyword_match(org.id, "XK-7731", allowed_levels=["viewer"])
    assert await repository.has_strict_keyword_match(org.id, "XK-7731", allowed_levels=["viewer", "admin"])


@pytest.mark.asyncio
async def test_invalid_access_level_is_rejected(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)

    with pytest.raises(ValueError):
        await service.ingest_article(org.id, "Doc", "x", access_level="superusuario")


@pytest.mark.asyncio
async def test_list_articles_reports_chunk_counts_and_filters_by_source(db_session):
    org = await create_organization(db_session)
    service, _ = _service(db_session)
    await service.ingest_article(org.id, "Tabla", "x", chunks=["fila 1", "fila 2", "fila 3"], source_type=SOURCE_UPLOAD)
    await service.ingest_article(org.id, "Prosa", "una frase corta", source_type=SOURCE_FILE)
    repository = KBRepository(db_session)

    everything = {a.title: n for a, n in await repository.list_articles(org.id)}
    uploads = [a.title for a, _n in await repository.list_articles(org.id, source_type=SOURCE_UPLOAD)]

    assert everything == {"Tabla": 3, "Prosa": 1}
    assert uploads == ["Tabla"]
    assert await repository.count_articles(org.id) == 2
    assert await repository.count_chunks(org.id) == 4


def test_embedding_model_constant_matches_what_the_migration_backfilled():
    # La migracion e3f6a9b2c5d7 escribe 'gemini-embedding' para lo ya indexado;
    # si EMBEDDING_MODEL cambia, esos articulos se reindexan solos (diseño), pero
    # debe ser una decision consciente, no un descuido.
    assert embeddings_module.EMBEDDING_MODEL == "gemini-embedding"
