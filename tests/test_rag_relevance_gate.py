"""Filtro de relevancia de RAGService.retrieve: si el chunk mas cercano
esta mas lejos que settings.rag_max_cosine_distance y ningun chunk
contiene todos los terminos de la pregunta, no se devuelve nada.

Embeddings falsos: cada texto cae en un eje segun un marcador, asi que la
distancia coseno entre dos textos es 0.0 (mismo eje) o 1.0 (ejes
distintos) -- el umbral real (0.35) separa limpiamente ambos casos sin
depender de ningun modelo."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.repository import KBRepository
from app.rag.service import RAGService
from tests.factories import create_organization

_AXES = {"[ax-envios]": 0, "[ax-danos]": 1}
_FAR_AXIS = EMBEDDING_DIMENSIONS - 1


def _vector_for(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    axis = next((a for marker, a in _AXES.items() if marker in text), _FAR_AXIS)
    vector[axis] = 1.0
    return vector


class FakeEmbeddingClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector_for(text) for text in texts]

    async def embed_one(self, text: str) -> list[float]:
        return _vector_for(text)


@pytest.fixture(autouse=True)
def gate_threshold(monkeypatch):
    monkeypatch.setattr(settings, "rag_max_cosine_distance", 0.35)


def _service(db_session) -> RAGService:
    return RAGService(
        repository=KBRepository(db_session),
        embedding_client=FakeEmbeddingClient(),
        session=db_session,
    )


async def _seed(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Envios", "x", chunks=["[ax-envios] estado del envio en ruta"]
    )
    await service.ingest_article(
        org.id, "Oficinas", "x", chunks=["[ax-danos] la oficina OFC-MED-003 abre a las 8"]
    )
    return org, service


@pytest.mark.asyncio
@pytest.mark.parametrize("hybrid", [True, False])
async def test_close_question_passes_the_gate(db_session, hybrid):
    org, service = await _seed(db_session)

    results = await service.retrieve(
        org.id, "[ax-envios] donde esta mi paquete", top_k=3, hybrid=hybrid
    )

    assert results[0][1] == "Envios"


@pytest.mark.asyncio
@pytest.mark.parametrize("hybrid", [True, False])
async def test_far_question_with_no_keyword_match_returns_nothing(db_session, hybrid):
    org, service = await _seed(db_session)

    # Eje lejano y ninguna palabra de la KB: "no hay nada sobre esto".
    results = await service.retrieve(
        org.id, "capital de francia", top_k=3, hybrid=hybrid
    )

    assert results == []


@pytest.mark.asyncio
async def test_far_question_is_rescued_by_an_exact_keyword_match(db_session):
    org, service = await _seed(db_session)

    # El vector de la query esta lejos de todo, pero "OFC-MED-003" aparece
    # tal cual en un chunk: es justo el caso que la busqueda hibrida rescata.
    results = await service.retrieve(org.id, "OFC-MED-003", top_k=3, hybrid=True)

    assert [title for _c, title, _s in results][0] == "Oficinas"


@pytest.mark.asyncio
async def test_partial_keyword_overlap_does_not_rescue_a_far_question(db_session):
    org, service = await _seed(db_session)

    # "oficina" esta en la KB pero "tokio" no: no contiene TODOS los terminos.
    results = await service.retrieve(org.id, "oficina en tokio", top_k=3, hybrid=True)

    assert results == []


@pytest.mark.asyncio
async def test_vector_only_mode_has_no_keyword_rescue(db_session):
    org, service = await _seed(db_session)

    results = await service.retrieve(org.id, "OFC-MED-003", top_k=3, hybrid=False)

    assert results == []


@pytest.mark.asyncio
async def test_threshold_zero_disables_the_gate(db_session, monkeypatch):
    org, service = await _seed(db_session)
    monkeypatch.setattr(settings, "rag_max_cosine_distance", 0)

    results = await service.retrieve(org.id, "capital de francia", top_k=3, hybrid=True)

    assert len(results) > 0


@pytest.mark.asyncio
async def test_relevance_gate_false_skips_the_gate_for_one_call(db_session):
    org, service = await _seed(db_session)

    results = await service.retrieve(
        org.id, "capital de francia", top_k=3, hybrid=True, relevance_gate=False
    )

    assert len(results) > 0


@pytest.mark.asyncio
async def test_strict_keyword_match_never_crosses_organizations(db_session):
    org_a, _service_a = await _seed(db_session)
    org_b = await create_organization(db_session, name="Org B")

    assert await KBRepository(db_session).has_strict_keyword_match(org_a.id, "OFC-MED-003")
    assert not await KBRepository(db_session).has_strict_keyword_match(
        org_b.id, "OFC-MED-003"
    )
