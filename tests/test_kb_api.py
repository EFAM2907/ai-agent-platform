"""API REST de la base de conocimiento (app.rag.api). Los embeddings son
falsos (se sobreescribe get_tenant_embedding_client): ningun test llama
a un proveedor."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from app.rag.dependencies import get_tenant_embedding_client
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.users.models import UserRole
from tests.factories import create_organization, create_user


class FakeEmbeddings:
    async def embed(self, texts):
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    async def embed_one(self, text):
        return (await self.embed([text]))[0]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    monkeypatch.setattr(settings, "rag_max_cosine_distance", 0)

    async def _fake():
        return FakeEmbeddings()

    app.dependency_overrides[get_tenant_embedding_client] = _fake
    yield
    app.dependency_overrides.pop(get_tenant_embedding_client, None)


def _headers(user, org_id) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "organization_id": str(org_id)})
    return {"Authorization": f"Bearer {token}"}


async def _admin_and_org(db_session, role=UserRole.ADMIN):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, role=role)
    await db_session.commit()
    return org, user


def _md(text: str = "# Politica de prueba\n\nLa tarifa especial es 998877.") -> dict:
    return {"file": ("politica.md", text.encode(), "text/markdown")}


@pytest.mark.asyncio
async def test_admin_uploads_a_markdown_document_and_it_is_listed(db_session, client):
    org, admin = await _admin_and_org(db_session)

    response = await client.post("/kb/documents", files=_md(), headers=_headers(admin, org.id))

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "politica.md"
    [doc] = body["documents"]
    assert doc["title"] == "Politica de prueba"
    assert doc["status"] == "created"
    assert doc["chunk_count"] >= 1

    listing = await client.get("/kb/documents", headers=_headers(admin, org.id))
    [item] = listing.json()["documents"]
    assert item["source_type"] == "upload"
    assert item["source_ref"] == "politica.md"
    assert item["access_level"] == "viewer"


@pytest.mark.asyncio
async def test_uploading_the_same_file_twice_reports_unchanged(db_session, client):
    org, admin = await _admin_and_org(db_session)
    headers = _headers(admin, org.id)

    await client.post("/kb/documents", files=_md(), headers=headers)
    second = await client.post("/kb/documents", files=_md(), headers=headers)

    assert second.json()["documents"][0]["status"] == "unchanged"
    listing = await client.get("/kb/documents", headers=headers)
    assert listing.json()["total"] == 1  # no duplico


@pytest.mark.asyncio
async def test_changed_content_reports_updated(db_session, client):
    org, admin = await _admin_and_org(db_session)
    headers = _headers(admin, org.id)
    await client.post("/kb/documents", files=_md("# Doc\n\nversion uno"), headers=headers)

    second = await client.post("/kb/documents", files=_md("# Doc\n\nversion dos"), headers=headers)

    assert second.json()["documents"][0]["status"] == "updated"


@pytest.mark.asyncio
async def test_excel_upload_creates_one_document_per_sheet(db_session, client):
    org, admin = await _admin_and_org(db_session)
    workbook = Workbook()
    workbook.active.title = "Tarifas"
    workbook.active.append(["Ruta", "Precio"])
    workbook.active.append(["Bogota-Cali", 15000])
    workbook.active.append(["Bogota-Pasto", 17800])
    buffer = io.BytesIO()
    workbook.save(buffer)

    response = await client.post(
        "/kb/documents",
        files={"file": ("tarifas.xlsx", buffer.getvalue(), "application/octet-stream")},
        headers=_headers(admin, org.id),
    )

    assert response.status_code == 201
    [doc] = response.json()["documents"]
    assert doc["title"] == "tarifas - Tarifas"
    assert doc["chunk_count"] == 2  # una fila = un chunk


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_documents(db_session, client):
    org, member = await _admin_and_org(db_session, role=UserRole.MEMBER)
    headers = _headers(member, org.id)

    assert (await client.post("/kb/documents", files=_md(), headers=headers)).status_code == 403
    assert (await client.get("/kb/documents", headers=headers)).status_code == 403
    assert (await client.get("/kb/stats", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_rejects_unsupported_extension_and_empty_and_invalid_level(db_session, client):
    org, admin = await _admin_and_org(db_session)
    headers = _headers(admin, org.id)

    exe = await client.post(
        "/kb/documents", files={"file": ("virus.exe", b"MZ", "application/octet-stream")}, headers=headers
    )
    empty = await client.post(
        "/kb/documents", files={"file": ("vacio.md", b"", "text/markdown")}, headers=headers
    )
    bad_level = await client.post(
        "/kb/documents", files=_md(), data={"access_level": "dios"}, headers=headers
    )

    assert exe.status_code == 415
    assert empty.status_code == 400
    assert bad_level.status_code == 400


@pytest.mark.asyncio
async def test_unreadable_pdf_is_a_clear_422(db_session, client):
    org, admin = await _admin_and_org(db_session)

    response = await client.post(
        "/kb/documents",
        files={"file": ("roto.pdf", b"esto no es un pdf", "application/pdf")},
        headers=_headers(admin, org.id),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_upload_is_rejected(db_session, client, monkeypatch):
    org, admin = await _admin_and_org(db_session)
    monkeypatch.setattr("app.rag.api.MAX_UPLOAD_BYTES", 100)

    response = await client.post(
        "/kb/documents", files=_md("# T\n\n" + "x" * 500), headers=_headers(admin, org.id)
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_filename_cannot_escape_the_temp_directory(db_session, client):
    org, admin = await _admin_and_org(db_session)

    response = await client.post(
        "/kb/documents",
        files={"file": ("../../etc/evil.md", b"# Titulo\n\ncontenido", "text/markdown")},
        headers=_headers(admin, org.id),
    )

    assert response.status_code == 201
    assert response.json()["filename"] == "evil.md"


@pytest.mark.asyncio
async def test_delete_document_and_tenant_isolation(db_session, client):
    org_a, admin_a = await _admin_and_org(db_session)
    org_b, admin_b = await _admin_and_org(db_session)
    uploaded = await client.post("/kb/documents", files=_md(), headers=_headers(admin_a, org_a.id))
    doc_id = uploaded.json()["documents"][0]["id"]

    foreign = await client.delete(f"/kb/documents/{doc_id}", headers=_headers(admin_b, org_b.id))
    own = await client.delete(f"/kb/documents/{doc_id}", headers=_headers(admin_a, org_a.id))
    again = await client.delete(f"/kb/documents/{doc_id}", headers=_headers(admin_a, org_a.id))

    assert foreign.status_code == 404  # otra organizacion: no existe para ella
    assert own.status_code == 204
    assert again.status_code == 404
    listing = await client.get("/kb/documents", headers=_headers(admin_a, org_a.id))
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_stats_report_counts_sources_and_formats(db_session, client):
    org, admin = await _admin_and_org(db_session)
    headers = _headers(admin, org.id)
    await client.post("/kb/documents", files=_md(), headers=headers)

    body = (await client.get("/kb/stats", headers=headers)).json()

    assert body["articles"] == 1
    assert body["chunks"] >= 1
    assert body["by_source"] == {"upload": 1}
    assert set(body["supported_formats"]) == {".md", ".pdf", ".xlsx", ".docx"}


@pytest.mark.asyncio
async def test_search_returns_hits_and_respects_the_callers_role(db_session, client):
    org, admin = await _admin_and_org(db_session)
    viewer = await create_user(db_session, org.id, role=UserRole.VIEWER)
    await db_session.commit()
    await client.post(
        "/kb/documents", files=_md("# Publico\n\ninformacion general"), headers=_headers(admin, org.id)
    )
    await client.post(
        "/kb/documents",
        files={"file": ("rrhh.md", b"# Salarios\n\ninformacion salarial", "text/markdown")},
        data={"access_level": "admin"},
        headers=_headers(admin, org.id),
    )

    as_admin = await client.post(
        "/kb/search", json={"query": "informacion", "top_k": 5}, headers=_headers(admin, org.id)
    )
    as_viewer = await client.post(
        "/kb/search", json={"query": "informacion", "top_k": 5}, headers=_headers(viewer, org.id)
    )

    assert {h["title"] for h in as_admin.json()["results"]} == {"Publico", "Salarios"}
    assert {h["title"] for h in as_viewer.json()["results"]} == {"Publico"}


@pytest.mark.asyncio
async def test_search_validates_input_and_requires_password_change_done(db_session, client):
    org = await create_organization(db_session)
    pending = await create_user(db_session, org.id, must_change_password=True)
    ok = await create_user(db_session, org.id)
    await db_session.commit()

    too_short = await client.post("/kb/search", json={"query": "a"}, headers=_headers(ok, org.id))
    blocked = await client.post(
        "/kb/search", json={"query": "politica"}, headers=_headers(pending, org.id)
    )

    assert too_short.status_code == 422
    assert blocked.status_code == 403
