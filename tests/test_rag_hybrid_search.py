"""Busqueda hibrida (vectorial + palabras clave + RRF) contra Postgres
real. Los embeddings son falsos y deterministas: cada texto cae en un
"eje" segun un marcador que contenga, asi el test controla exactamente
que tan cerca esta cada chunk de la query en el espacio vectorial, sin
llamar a ningun modelo."""

from __future__ import annotations

import pytest

from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.repository import KBRepository
from app.rag.service import RAGService
from tests.factories import create_organization

# marcador -> eje. Lo que no matchee ningun marcador cae en el ultimo
# eje, ortogonal a todos los demas (distancia coseno 1.0 contra ellos).
_AXES = {"[ax-envios]": 0, "[ax-danos]": 1, "[ax-tarifas]": 2}
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


def _service(db_session) -> RAGService:
    return RAGService(
        repository=KBRepository(db_session),
        embedding_client=FakeEmbeddingClient(),
        session=db_session,
    )


@pytest.mark.asyncio
async def test_keyword_search_finds_an_exact_tracking_number_the_vector_search_cannot(
    db_session,
):
    org = await create_organization(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Envio puntual", "x", chunks=["[ax-envios] La guia 550201 esta en bodega"]
    )
    for i in range(6):
        await service.ingest_article(
            org.id, f"Relleno {i}", "x", chunks=[f"[ax-danos] texto de relleno numero {i}"]
        )

    # La query no cae en ningun eje: para la busqueda vectorial todos los
    # chunks estan igual de lejos. Solo la de palabras clave sabe cual es.
    [(chunk, title, _score)] = await service.retrieve(
        org.id, "550201", top_k=1, hybrid=True
    )

    assert title == "Envio puntual"
    assert "550201" in chunk.content


@pytest.mark.asyncio
async def test_chunk_ranked_well_by_both_searches_wins(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Danos", "x", chunks=["[ax-danos] paquete averiado reclamo por dano"]
    )
    await service.ingest_article(
        org.id, "Tarifas", "x", chunks=["[ax-tarifas] precio del servicio express"]
    )
    await service.ingest_article(
        org.id, "Envios", "x", chunks=["[ax-envios] estado del envio en ruta"]
    )

    results = await service.retrieve(
        org.id, "[ax-danos] reclamo por paquete averiado", top_k=3, hybrid=True
    )

    assert results[0][1] == "Danos"


@pytest.mark.asyncio
async def test_keyword_search_stems_spanish_words(db_session):
    org = await create_organization(db_session)
    repository = KBRepository(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Retrasos", "x", chunks=["El retraso de un envio se investiga"]
    )

    found = await repository.search_keyword_chunks(org.id, "retrasos")

    assert [title for _chunk, title, _rank in found] == ["Retrasos"]


@pytest.mark.asyncio
async def test_keyword_search_matches_on_any_term_not_all_of_them(db_session):
    org = await create_organization(db_session)
    repository = KBRepository(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Reclamaciones", "x", chunks=["Plazo para presentar una reclamacion"]
    )

    # "dinosaurio" y "volcan" no estan en ningun chunk: con AND no habria match.
    found = await repository.search_keyword_chunks(
        org.id, "reclamacion dinosaurio volcan"
    )

    assert [title for _chunk, title, _rank in found] == ["Reclamaciones"]


@pytest.mark.asyncio
async def test_keyword_search_never_returns_another_organizations_chunks(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    service = _service(db_session)
    await service.ingest_article(
        org_a.id, "Secreto de A", "x", chunks=["tarifa confidencial 998877"]
    )

    from_b = await KBRepository(db_session).search_keyword_chunks(org_b.id, "998877")
    hybrid_from_b = await service.retrieve(org_b.id, "998877", top_k=5, hybrid=True)

    assert from_b == []
    assert hybrid_from_b == []


@pytest.mark.asyncio
async def test_stopword_only_query_falls_back_to_vector_results(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Envios", "x", chunks=["[ax-envios] estado del envio en ruta"]
    )

    # "de la en el" son todas stopwords en español: el tsquery queda vacio.
    keyword = await KBRepository(db_session).search_keyword_chunks(org.id, "de la en el")
    results = await service.retrieve(
        org.id, "de la en el", top_k=3, hybrid=True, relevance_gate=False
    )

    assert keyword == []
    assert [title for _chunk, title, _score in results] == ["Envios"]


@pytest.mark.asyncio
async def test_vector_only_mode_returns_cosine_similarity_as_score(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    await service.ingest_article(
        org.id, "Envios", "x", chunks=["[ax-envios] estado del envio en ruta"]
    )

    [(_chunk, title, score)] = await service.retrieve(
        org.id, "[ax-envios] donde esta mi paquete", top_k=1, hybrid=False
    )

    assert title == "Envios"
    assert score == pytest.approx(1.0)  # mismo eje -> distancia 0 -> similitud 1


@pytest.mark.asyncio
async def test_hybrid_returns_at_most_top_k_without_duplicate_chunks(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)
    for i in range(8):
        await service.ingest_article(
            org.id, f"Art {i}", "x", chunks=[f"[ax-envios] envio numero {i}"]
        )

    results = await service.retrieve(org.id, "[ax-envios] envio", top_k=3, hybrid=True)

    assert len(results) == 3
    assert len({chunk.id for chunk, _title, _score in results}) == 3
