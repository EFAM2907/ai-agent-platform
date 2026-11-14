"""app.rag.sync: indexar una fuente y retirar lo que desaparecio de ella."""

from __future__ import annotations

import httpx
import pytest
from openai import RateLimitError

from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.ingestion.base import IngestedDocument
from app.rag.models import SOURCE_DRIVE, SOURCE_FILE
from app.rag.repository import KBRepository
from app.rag.service import RAGService
from app.rag.sync import sync_documents
from tests.factories import create_organization


class FakeEmbeddings:
    def __init__(self, fail_first: int = 0) -> None:
        self.fail_first = fail_first
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        if self.calls <= self.fail_first:
            request = httpx.Request("POST", "http://litellm/embeddings")
            raise RateLimitError(
                "cuota", response=httpx.Response(429, request=request), body=None
            )
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    async def embed_one(self, text):
        return (await self.embed([text]))[0]


def _doc(title: str, content: str = "contenido", ref: str | None = None) -> IngestedDocument:
    return IngestedDocument(title=title, content=content, source_ref=ref or f"{title}.md")


def _service(db_session, embeddings=None) -> RAGService:
    return RAGService(
        repository=KBRepository(db_session),
        embedding_client=embeddings or FakeEmbeddings(),
        session=db_session,
    )


async def _titles(db_session, org_id, source_type):
    rows = await KBRepository(db_session).list_active_by_source(org_id, source_type)
    return {a.title for a in rows}


@pytest.mark.asyncio
async def test_sync_indexes_documents_with_their_source_metadata(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)

    report = await sync_documents(
        service, org.id, [_doc("A", ref="carpeta/a.md"), _doc("B", ref="carpeta/b.md")],
        source_type=SOURCE_FILE, ref_prefix="carpeta/",
    )

    assert report.documents == 2 and report.pruned == 0
    refs = {a.source_ref for a in await KBRepository(db_session).list_active_by_source(org.id, SOURCE_FILE)}
    assert refs == {"carpeta/a.md", "carpeta/b.md"}


@pytest.mark.asyncio
async def test_sync_prunes_documents_that_left_the_source(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await sync_documents(
        service, org.id, [_doc("A", ref="c/a.md"), _doc("B", ref="c/b.md")],
        source_type=SOURCE_FILE, ref_prefix="c/",
    )

    report = await sync_documents(
        service, org.id, [_doc("A", ref="c/a.md")], source_type=SOURCE_FILE, ref_prefix="c/"
    )

    assert report.pruned == 1
    assert await _titles(db_session, org.id, SOURCE_FILE) == {"A"}


@pytest.mark.asyncio
async def test_sync_with_no_documents_never_empties_the_kb(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await sync_documents(service, org.id, [_doc("A", ref="c/a.md")], source_type=SOURCE_FILE, ref_prefix="c/")

    report = await sync_documents(service, org.id, [], source_type=SOURCE_FILE, ref_prefix="c/")

    assert report.pruned == 0  # una carpeta vacia/inaccesible no es "borraron todo"
    assert await _titles(db_session, org.id, SOURCE_FILE) == {"A"}


@pytest.mark.asyncio
async def test_prune_false_keeps_everything(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await sync_documents(
        service, org.id, [_doc("A", ref="c/a.md"), _doc("B", ref="c/b.md")],
        source_type=SOURCE_FILE, ref_prefix="c/",
    )

    await sync_documents(
        service, org.id, [_doc("A", ref="c/a.md")], source_type=SOURCE_FILE, ref_prefix="c/", prune=False
    )

    assert await _titles(db_session, org.id, SOURCE_FILE) == {"A", "B"}


@pytest.mark.asyncio
async def test_sync_of_one_source_never_touches_another(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await sync_documents(service, org.id, [_doc("Drive doc", ref="drive:1")], source_type=SOURCE_DRIVE)

    await sync_documents(service, org.id, [_doc("A", ref="c/a.md")], source_type=SOURCE_FILE, ref_prefix="c/")

    assert await _titles(db_session, org.id, SOURCE_DRIVE) == {"Drive doc"}


@pytest.mark.asyncio
async def test_sync_does_not_reset_an_access_level_set_by_an_admin(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await sync_documents(service, org.id, [_doc("A", ref="c/a.md")], source_type=SOURCE_FILE, ref_prefix="c/")
    [article] = await KBRepository(db_session).list_active_by_source(org.id, SOURCE_FILE)
    await service.set_access_level(org.id, article.id, "admin")

    await sync_documents(service, org.id, [_doc("A", ref="c/a.md")], source_type=SOURCE_FILE, ref_prefix="c/")

    assert article.access_level == "admin"


@pytest.mark.asyncio
async def test_rate_limit_waits_and_retries_the_same_document(db_session):
    org = await create_organization(db_session)
    embeddings = FakeEmbeddings(fail_first=2)
    service = _service(db_session, embeddings)

    report = await sync_documents(
        service, org.id, [_doc("A")], source_type=SOURCE_FILE,
        rate_limit_attempts=4, rate_limit_wait_seconds=0,
    )

    assert report.documents == 1
    assert embeddings.calls == 3  # 2 fallos + 1 exito
    assert await _titles(db_session, org.id, SOURCE_FILE) == {"A"}


@pytest.mark.asyncio
async def test_rate_limit_without_retries_propagates(db_session):
    org = await create_organization(db_session)
    service = _service(db_session, FakeEmbeddings(fail_first=1))

    with pytest.raises(RateLimitError):
        await sync_documents(service, org.id, [_doc("A")], source_type=SOURCE_FILE)
