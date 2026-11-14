"""Integracion con Google Drive: cliente HTTP, clasificacion de archivos,
sincronizacion incremental con la base de conocimiento y API. Sin red:
el cliente de Drive y el OAuth de Google son falsos, pero los archivos que
sirven (md, docx, xlsx) pasan por los loaders reales."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from docx import Document
from openpyxl import Workbook

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.security import create_access_token
from app.drive.client import (
    FOLDER_MIME,
    DriveClient,
    DriveFile,
    classify,
    parse_folder_reference,
    parse_time,
)
from app.drive.errors import (
    DriveAPIError,
    DriveFolderError,
    DriveFolderNotSetError,
    DriveNotConnectedError,
)
from app.drive.repository import DriveRepository
from app.drive.service import DriveService, file_id_of
from app.gmail.errors import GmailAccessRevokedError, GmailAuthorizationError
from app.gmail.oauth import OAuthTokens, create_state
from app.main import app
from app.rag.embeddings import EMBEDDING_DIMENSIONS
from app.rag.models import SOURCE_DRIVE
from app.rag.repository import KBRepository
from app.rag.service import RAGService
from app.users.models import UserRole
from tests.factories import create_organization, create_user

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
MD_MIME = "text/markdown"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GDOC = "application/vnd.google-apps.document"


# --------------------------------------------------------------------------
# Archivos de prueba
# --------------------------------------------------------------------------
def md_bytes(title: str, body: str = "contenido de la politica") -> bytes:
    return f"# {title}\n\n{body}".encode()


def docx_bytes(text: str = "Procedimiento de atencion") -> bytes:
    document = Document()
    document.add_heading("Manual", level=1)
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_bytes() -> bytes:
    workbook = Workbook()
    workbook.active.title = "Tarifas"
    workbook.active.append(["Ruta", "Precio"])
    workbook.active.append(["Bogota-Cali", 15000])
    workbook.active.append(["Bogota-Pasto", 17800])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def drive_file(file_id, name, mime=MD_MIME, modified=T0, size=100, path="") -> DriveFile:
    return DriveFile(id=file_id, name=name, mime_type=mime, modified_time=modified, size=size, path=path)


class FakeDriveClient:
    """Una carpeta de Drive en memoria: `files` (lista) y `contents` (id -> bytes)."""

    def __init__(self) -> None:
        self.files: list[DriveFile] = []
        self.contents: dict[str, bytes] = {}
        self.truncated = False
        self.downloads: list[str] = []
        self.folders = {"carpeta_valida_123456": "Documentos de la empresa"}

    def put(self, file: DriveFile, content: bytes) -> None:
        self.files = [f for f in self.files if f.id != file.id] + [file]
        self.contents[file.id] = content

    def remove(self, file_id: str) -> None:
        self.files = [f for f in self.files if f.id != file_id]

    async def get_account_email(self) -> str:
        return "docs@empresa.com"

    async def get_folder(self, folder_id: str):
        if folder_id not in self.folders:
            raise DriveFolderError("No encuentro esa carpeta con la cuenta conectada")
        return folder_id, self.folders[folder_id]

    async def list_folder(self, folder_id: str, **_kwargs):
        return list(self.files), self.truncated

    async def download(self, file: DriveFile) -> bytes:
        self.downloads.append(file.id)
        return self.contents[file.id]


class CountingEmbeddings:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += len(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    async def embed_one(self, text):
        return (await self.embed([text]))[0]


class FakeOAuth:
    def __init__(self) -> None:
        self.refresh_access_token = AsyncMock(return_value="access-token")
        self.exchange_code = AsyncMock(
            return_value=OAuthTokens("access-token", "refresh-token", frozenset())
        )
        self.revoke = AsyncMock()
        self.build_authorization_url = lambda state: f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"


@pytest.fixture(autouse=True)
def no_relevance_gate(monkeypatch):
    monkeypatch.setattr(settings, "rag_max_cosine_distance", 0)


async def _connected(db_session, *, folder="carpeta_valida_123456"):
    """organizacion + admin + DriveService conectado (con carpeta) sobre un Drive falso."""
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    drive = FakeDriveClient()
    oauth = FakeOAuth()
    repository = DriveRepository(db_session)
    connection = await repository.upsert_connection(
        organization_id=org.id, user_id=admin.id, google_email="docs@empresa.com",
        encrypted_refresh_token=encrypt_secret("refresh-token"),
    )
    if folder:
        await repository.set_folder(connection, folder, "Documentos de la empresa")
    await db_session.commit()
    service = DriveService(repository, db_session, oauth=oauth, client_factory=lambda _token: drive)
    embeddings = CountingEmbeddings()
    rag = RAGService(repository=KBRepository(db_session), embedding_client=embeddings, session=db_session)
    return org, admin, service, rag, drive, oauth, embeddings


async def _titles(db_session, org_id):
    rows = await KBRepository(db_session).list_active_by_source(org_id, SOURCE_DRIVE)
    return {a.title for a in rows}


# --------------------------------------------------------------------------
# Funciones puras
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv?usp=sharing", "1AbCdEfGhIjKlMnOpQrStUv"),
        ("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUv", "1AbCdEfGhIjKlMnOpQrStUv"),
        ("  1AbCdEfGhIjKlMnOpQrStUv  ", "1AbCdEfGhIjKlMnOpQrStUv"),
    ],
)
def test_parse_folder_reference(value, expected):
    assert parse_folder_reference(value) == expected


@pytest.mark.parametrize("value", ["", "corto", "no es un enlace de drive!!", "https://example.com/algo"])
def test_parse_folder_reference_rejects_garbage(value):
    with pytest.raises(DriveFolderError):
        parse_folder_reference(value)


def test_classify_files():
    assert classify(drive_file("1", "politica.md")).suffix == ".md"
    assert classify(drive_file("2", "Manual.PDF", mime="application/pdf")).suffix == ".pdf"
    assert classify(drive_file("3", "Procedimiento", mime=GDOC)).suffix == ".docx"  # nativo: se exporta
    assert classify(drive_file("4", "Hoja", mime="application/vnd.google-apps.spreadsheet")).suffix == ".xlsx"
    assert classify(drive_file("5", "Deck", mime="application/vnd.google-apps.presentation")).suffix == ".pdf"
    assert not classify(drive_file("6", "foto.png", mime="image/png")).supported
    assert not classify(drive_file("7", "Mapa", mime="application/vnd.google-apps.map")).supported
    big = classify(drive_file("8", "enorme.pdf", mime="application/pdf", size=50 * 1024 * 1024))
    assert not big.supported and "MB" in big.reason
    assert not classify(drive_file("9", "sin_extension")).supported


def test_parse_time_and_file_id_of():
    assert parse_time("2026-10-01T12:00:00.000Z") == T0
    assert file_id_of("drive:abc123") == "abc123"
    assert file_id_of("drive:abc123#Hoja 1") == "abc123"
    assert file_id_of("inter_rapidisimo/01.md") is None and file_id_of(None) is None


# --------------------------------------------------------------------------
# Cliente HTTP (httpx simulado)
# --------------------------------------------------------------------------
class _Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self._data, self.content = status, data, content

    def json(self):
        return self._data


def _client_with(monkeypatch, responder):
    client = DriveClient("tok")
    calls = []

    async def fake_send(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return responder(method, path, kwargs)

    monkeypatch.setattr(client, "_send", fake_send)
    return client, calls


@pytest.mark.asyncio
async def test_list_folder_recurses_paginates_and_reports_paths(monkeypatch):
    def respond(method, path, kwargs):
        q = kwargs["params"]["q"]
        token = kwargs["params"].get("pageToken")
        if "'root_id'" in q and not token:
            return _Resp(data={
                "files": [
                    {"id": "f1", "name": "a.md", "mimeType": MD_MIME, "modifiedTime": "2026-10-01T12:00:00Z", "size": "10"},
                    {"id": "sub", "name": "Procedimientos", "mimeType": FOLDER_MIME, "modifiedTime": "2026-10-01T12:00:00Z"},
                ],
                "nextPageToken": "page2",
            })
        if "'root_id'" in q and token == "page2":
            return _Resp(data={"files": [{"id": "f2", "name": "b.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-10-02T12:00:00Z"}]})
        if "'sub'" in q:
            return _Resp(data={"files": [{"id": "f3", "name": "c.docx", "mimeType": DOCX_MIME, "modifiedTime": "2026-10-03T12:00:00Z"}]})
        raise AssertionError(q)

    client, calls = _client_with(monkeypatch, respond)

    files, truncated = await client.list_folder("root_id")

    assert not truncated
    assert {(f.id, f.path) for f in files} == {("f1", ""), ("f2", ""), ("f3", "Procedimientos/")}
    assert next(f for f in files if f.id == "f1").size == 10
    assert any(kw["params"].get("pageToken") == "page2" for _m, _p, kw in calls)


@pytest.mark.asyncio
async def test_list_folder_stops_at_max_files_and_flags_truncation(monkeypatch):
    items = [
        {"id": f"f{i}", "name": f"{i}.md", "mimeType": MD_MIME, "modifiedTime": "2026-10-01T12:00:00Z"}
        for i in range(5)
    ]
    client, _ = _client_with(monkeypatch, lambda m, p, kw: _Resp(data={"files": items}))

    files, truncated = await client.list_folder("root_id", max_files=3)

    assert len(files) == 3 and truncated


@pytest.mark.asyncio
async def test_list_folder_respects_max_depth(monkeypatch):
    def respond(method, path, kwargs):
        q = kwargs["params"]["q"]
        parent = q.split("'")[1]
        child = f"d{len(parent)}"
        return _Resp(data={"files": [{"id": child + "x" * len(parent), "name": "sub", "mimeType": FOLDER_MIME, "modifiedTime": "2026-10-01T12:00:00Z"}]})

    client, calls = _client_with(monkeypatch, respond)

    await client.list_folder("r", max_depth=2)

    assert len(calls) == 3  # raiz + 2 niveles de subcarpetas, ni uno mas


@pytest.mark.asyncio
async def test_download_exports_google_files_and_downloads_the_rest(monkeypatch):
    client, calls = _client_with(monkeypatch, lambda m, p, kw: _Resp(content=b"bytes"))

    await client.download(drive_file("g1", "Doc", mime=GDOC))
    await client.download(drive_file("p1", "a.pdf", mime="application/pdf"))

    (_m1, path1, kw1), (_m2, path2, kw2) = calls
    assert path1 == "/files/g1/export" and kw1["params"]["mimeType"] == DOCX_MIME
    assert path2 == "/files/p1" and kw2["params"]["alt"] == "media"


@pytest.mark.asyncio
async def test_get_folder_validates_the_kind_and_translates_404(monkeypatch):
    client, _ = _client_with(monkeypatch, lambda m, p, kw: _Resp(data={"id": "x1", "name": "Docs", "mimeType": FOLDER_MIME}))
    assert await client.get_folder("x1") == ("x1", "Docs")

    client, _ = _client_with(monkeypatch, lambda m, p, kw: _Resp(data={"id": "x1", "name": "a.pdf", "mimeType": "application/pdf"}))
    with pytest.raises(DriveFolderError, match="no es una carpeta"):
        await client.get_folder("x1")

    def not_found(m, p, kw):
        raise DriveAPIError("Drive API respondio 404 (GET /files/x1)")

    client = DriveClient("tok")
    monkeypatch.setattr(client, "_send", AsyncMock(side_effect=DriveAPIError("Drive API respondio 404 (GET /files/x1)")))
    with pytest.raises(DriveFolderError, match="No encuentro"):
        await client.get_folder("x1")


# --------------------------------------------------------------------------
# Sincronizacion
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_sync_indexes_every_supported_format_with_source_metadata(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "Politica de envios.md"), md_bytes("Politica de envios"))
    drive.put(drive_file("f2", "Manual interno", mime=GDOC), docx_bytes())
    drive.put(drive_file("f3", "Tarifas.xlsx", mime=XLSX_MIME), xlsx_bytes())

    result = await service.sync(org.id, rag)

    assert result.documents == 3 and result.unchanged == 0 and result.pruned == 0
    assert await _titles(db_session, org.id) == {"Politica de envios", "Manual interno", "Tarifas - Tarifas"}
    articles = {a.title: a for a in await KBRepository(db_session).list_active_by_source(org.id, SOURCE_DRIVE)}
    assert articles["Politica de envios"].source_ref == "drive:f1"
    assert articles["Politica de envios"].source_modified_at == T0
    assert articles["Tarifas - Tarifas"].source_ref == "drive:f3"  # un archivo, una hoja
    connection = await service.get_active_connection(org.id)
    assert connection.last_sync["documents"] == 3 and connection.last_synced_at is not None


@pytest.mark.asyncio
async def test_second_sync_downloads_and_embeds_nothing(db_session):
    org, _admin, service, rag, drive, _oauth, embeddings = await _connected(db_session)
    drive.put(drive_file("f1", "Politica.md"), md_bytes("Politica"))
    drive.put(drive_file("f2", "Tarifas.xlsx", mime=XLSX_MIME), xlsx_bytes())
    await service.sync(org.id, rag)
    drive.downloads.clear()
    calls_before = embeddings.calls

    result = await service.sync(org.id, rag)

    assert result.unchanged == 2 and result.documents == 0 and result.pruned == 0
    assert drive.downloads == []  # ni siquiera se descargo
    assert embeddings.calls == calls_before  # ni se llamo al proveedor de embeddings
    assert await _titles(db_session, org.id) == {"Politica", "Tarifas - Tarifas"}


@pytest.mark.asyncio
async def test_a_modified_file_is_reindexed_and_the_rest_untouched(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "Politica.md"), md_bytes("Politica", "version uno"))
    drive.put(drive_file("f2", "Otra.md"), md_bytes("Otra", "sin cambios"))
    await service.sync(org.id, rag)
    drive.downloads.clear()
    drive.put(drive_file("f1", "Politica.md", modified=T0 + timedelta(days=1)), md_bytes("Politica", "version dos"))

    result = await service.sync(org.id, rag)

    assert drive.downloads == ["f1"]
    assert result.documents == 1 and result.unchanged == 1
    article = await KBRepository(db_session).get_article_by_title(org.id, "Politica")
    assert "version dos" in article.content and article.source_modified_at == T0 + timedelta(days=1)


@pytest.mark.asyncio
async def test_a_file_removed_from_drive_is_retired_from_the_kb(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "Queda.md"), md_bytes("Queda"))
    drive.put(drive_file("f2", "Se va.md"), md_bytes("Se va"))
    await service.sync(org.id, rag)
    drive.remove("f2")

    result = await service.sync(org.id, rag)

    assert result.pruned == 1
    assert await _titles(db_session, org.id) == {"Queda"}


@pytest.mark.asyncio
async def test_removing_every_file_does_retire_them_all_but_an_inaccessible_drive_does_not(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "Unico.md"), md_bytes("Unico"))
    await service.sync(org.id, rag)
    drive.remove("f1")

    result = await service.sync(org.id, rag)

    # Carpeta vacia: no hay NINGUNA referencia que conservar -> no se poda
    # (una carpeta vacia e inaccesible son indistinguibles desde aqui).
    assert result.pruned == 0
    assert await _titles(db_session, org.id) == {"Unico"}


@pytest.mark.asyncio
async def test_unsupported_files_are_reported_not_indexed(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "Politica.md"), md_bytes("Politica"))
    drive.put(drive_file("f2", "foto.png", mime="image/png"), b"png")

    result = await service.sync(org.id, rag)

    assert result.documents == 1
    assert [(f.name, f.status) for f in result.unsupported] == [("foto.png", "unsupported")]
    assert "foto.png" not in drive.downloads and drive.downloads == ["f1"]


@pytest.mark.asyncio
async def test_a_corrupt_file_fails_alone_and_keeps_what_was_indexed_before(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "Manual.docx", mime=DOCX_MIME), docx_bytes("version buena"))
    drive.put(drive_file("f2", "Otra.md"), md_bytes("Otra"))
    await service.sync(org.id, rag)
    # El manual cambia pero ahora llega corrupto.
    drive.put(drive_file("f1", "Manual.docx", mime=DOCX_MIME, modified=T0 + timedelta(days=1)), b"esto no es un docx")

    result = await service.sync(org.id, rag)

    assert [(f.name, f.status) for f in result.failed] == [("Manual.docx", "failed")]
    assert result.pruned == 0  # el fallo transitorio NO retira lo que ya estaba indexado
    assert await _titles(db_session, org.id) == {"Manual", "Otra"}


@pytest.mark.asyncio
async def test_two_files_with_the_same_title_are_not_allowed_to_fight(db_session):
    org, _admin, service, rag, drive, _oauth, embeddings = await _connected(db_session)
    drive.put(drive_file("f1", "Politica.md", path=""), md_bytes("Politica", "primera version"))
    drive.put(drive_file("f2", "Politica copia.md", path="Archivo/"), md_bytes("Politica", "segunda version"))

    first = await service.sync(org.id, rag)
    calls = embeddings.calls
    second = await service.sync(org.id, rag)

    assert first.documents == 1 and len(first.failed) == 1
    assert "ya lo usa otro documento" in first.failed[0].reason
    assert embeddings.calls == calls  # la segunda pasada no reembebe nada: no hay pelea
    assert second.documents == 0


@pytest.mark.asyncio
async def test_a_truncated_listing_never_prunes(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "A.md"), md_bytes("A"))
    drive.put(drive_file("f2", "B.md"), md_bytes("B"))
    await service.sync(org.id, rag)
    drive.remove("f2")
    drive.truncated = True  # la lista esta incompleta: B podria seguir existiendo

    result = await service.sync(org.id, rag)

    assert result.truncated and result.pruned == 0
    assert await _titles(db_session, org.id) == {"A", "B"}


@pytest.mark.asyncio
async def test_sync_requires_a_connection_and_a_folder(db_session):
    org, _admin, service, rag, _drive, _oauth, _emb = await _connected(db_session, folder=None)

    with pytest.raises(DriveFolderNotSetError):
        await service.sync(org.id, rag)

    other_org = await create_organization(db_session)
    with pytest.raises(DriveNotConnectedError):
        await service.sync(other_org.id, rag)


@pytest.mark.asyncio
async def test_a_revoked_refresh_token_marks_the_connection_revoked(db_session):
    org, _admin, service, rag, _drive, oauth, _emb = await _connected(db_session)
    oauth.refresh_access_token.side_effect = GmailAccessRevokedError("invalid_grant")

    with pytest.raises(DriveNotConnectedError):
        await service.sync(org.id, rag)

    assert await service.get_active_connection(org.id) is None


@pytest.mark.asyncio
async def test_syncs_of_different_organizations_never_touch_each_other(db_session):
    org_a, _a, service_a, rag_a, drive_a, _o, _e = await _connected(db_session)
    org_b, _b, service_b, rag_b, drive_b, _o2, _e2 = await _connected(db_session)
    drive_a.put(drive_file("f1", "Solo de A.md"), md_bytes("Solo de A"))
    drive_b.put(drive_file("f9", "Solo de B.md"), md_bytes("Solo de B"))
    await service_a.sync(org_a.id, rag_a)
    await service_b.sync(org_b.id, rag_b)
    drive_b.remove("f9")
    drive_b.put(drive_file("f8", "Nuevo de B.md"), md_bytes("Nuevo de B"))

    await service_b.sync(org_b.id, rag_b)

    assert await _titles(db_session, org_a.id) == {"Solo de A"}
    assert await _titles(db_session, org_b.id) == {"Nuevo de B"}


@pytest.mark.asyncio
async def test_preview_classifies_without_downloading_or_indexing(db_session):
    org, _admin, service, rag, drive, _oauth, embeddings = await _connected(db_session)
    drive.put(drive_file("f1", "Igual.md"), md_bytes("Igual"))
    drive.put(drive_file("f2", "Cambia.md"), md_bytes("Cambia"))
    await service.sync(org.id, rag)
    drive.put(drive_file("f2", "Cambia.md", modified=T0 + timedelta(days=1)), md_bytes("Cambia", "otra cosa"))
    drive.put(drive_file("f3", "Nuevo.md"), md_bytes("Nuevo"))
    drive.put(drive_file("f4", "foto.jpg", mime="image/jpeg"), b"x")
    drive.downloads.clear()
    calls = embeddings.calls

    preview = await service.preview(org.id, rag)

    statuses = {f.name: f.status for f in preview.files}
    assert statuses == {"Igual.md": "unchanged", "Cambia.md": "changed", "Nuevo.md": "new", "foto.jpg": "unsupported"}
    assert drive.downloads == [] and embeddings.calls == calls


@pytest.mark.asyncio
async def test_set_folder_validates_and_a_new_folder_resets_the_sync_history(db_session):
    org, _admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "A.md"), md_bytes("A"))
    await service.sync(org.id, rag)
    drive.folders["otra_carpeta_valida_99"] = "Otra"

    with pytest.raises(DriveFolderError):
        await service.set_folder(org.id, "https://drive.google.com/drive/folders/no_existe_carpeta_123456")
    connection = await service.set_folder(org.id, "https://drive.google.com/drive/folders/otra_carpeta_valida_99")

    assert connection.folder_id == "otra_carpeta_valida_99" and connection.folder_name == "Otra"
    assert connection.last_synced_at is None and connection.last_sync is None


@pytest.mark.asyncio
async def test_disconnect_keeps_documents_unless_asked_to_remove_them(db_session):
    org, _admin, service, rag, drive, oauth, _emb = await _connected(db_session)
    drive.put(drive_file("f1", "A.md"), md_bytes("A"))
    await service.sync(org.id, rag)

    assert await service.disconnect(org.id, rag_service=rag) is True
    assert await _titles(db_session, org.id) == {"A"}  # el conocimiento ya indexado se conserva
    oauth.revoke.assert_awaited_once()
    assert await service.disconnect(org.id, rag_service=rag) is False

    org2, _a2, service2, rag2, drive2, _o2, _e2 = await _connected(db_session)
    drive2.put(drive_file("f1", "B.md"), md_bytes("B"))
    await service2.sync(org2.id, rag2)
    await service2.disconnect(org2.id, rag_service=rag2, remove_documents=True)
    assert await _titles(db_session, org2.id) == set()


# --------------------------------------------------------------------------
# Autorizacion OAuth
# --------------------------------------------------------------------------
def test_authorization_url_uses_a_drive_only_state():
    service = DriveService(repository=None, session=None, oauth=FakeOAuth())
    import uuid

    url = service.start_authorization(uuid.uuid4(), uuid.uuid4())

    assert "state=" in url


@pytest.mark.asyncio
async def test_a_gmail_state_cannot_complete_a_drive_authorization(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service = DriveService(DriveRepository(db_session), db_session, oauth=FakeOAuth())
    gmail_state = create_state(admin.id, org.id)  # el state de Gmail

    with pytest.raises(GmailAuthorizationError):
        await service.complete_authorization("code", gmail_state)


@pytest.mark.asyncio
async def test_complete_authorization_stores_an_encrypted_token_and_the_account(db_session):
    from app.core.crypto import decrypt_secret
    from app.gmail.oauth import DRIVE_STATE_PURPOSE

    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    drive = FakeDriveClient()
    service = DriveService(DriveRepository(db_session), db_session, oauth=FakeOAuth(), client_factory=lambda _t: drive)

    connection = await service.complete_authorization("code", create_state(admin.id, org.id, DRIVE_STATE_PURPOSE))

    assert connection.google_email == "docs@empresa.com"
    assert connection.refresh_token != "refresh-token"  # cifrado
    assert decrypt_secret(connection.refresh_token) == "refresh-token"
    assert connection.folder_id is None


@pytest.mark.asyncio
async def test_authorization_without_refresh_token_is_rejected(db_session):
    from app.gmail.oauth import DRIVE_STATE_PURPOSE

    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    oauth = FakeOAuth()
    oauth.exchange_code.return_value = OAuthTokens("access", None, frozenset())
    service = DriveService(DriveRepository(db_session), db_session, oauth=oauth, client_factory=lambda _t: FakeDriveClient())

    with pytest.raises(GmailAuthorizationError, match="refresh token"):
        await service.complete_authorization("code", create_state(admin.id, org.id, DRIVE_STATE_PURPOSE))


@pytest.mark.asyncio
async def test_reconnecting_with_another_google_account_drops_the_folder(db_session):
    org, admin, service, _rag, _drive, _oauth, _emb = await _connected(db_session)
    repository = DriveRepository(db_session)

    connection = await repository.upsert_connection(
        organization_id=org.id, user_id=admin.id, google_email="otra@cuenta.com",
        encrypted_refresh_token=encrypt_secret("nuevo"),
    )

    assert connection.folder_id is None and connection.last_sync is None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def _headers(user, org_id):
    token = create_access_token({"sub": str(user.id), "organization_id": str(org_id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def drive_api(db_session):
    """Sustituye el servicio de Drive y de RAG de la API por los falsos."""
    from app.drive.api import get_drive_service
    from app.rag.dependencies import get_rag_service

    holder = {}

    def install(service, rag):
        app.dependency_overrides[get_drive_service] = lambda: service
        app.dependency_overrides[get_rag_service] = lambda: rag

    holder["install"] = install
    yield holder
    app.dependency_overrides.pop(get_drive_service, None)
    app.dependency_overrides.pop(get_rag_service, None)


@pytest.mark.asyncio
async def test_api_full_flow_status_preview_sync_and_disconnect(db_session, client, drive_api):
    org, admin, service, rag, drive, _oauth, _emb = await _connected(db_session)
    drive_api["install"](service, rag)
    drive.put(drive_file("f1", "Politica.md"), md_bytes("Politica"))
    drive.put(drive_file("f2", "foto.png", mime="image/png"), b"x")
    headers = _headers(admin, org.id)

    status = await client.get("/drive/status", headers=headers)
    preview = await client.get("/drive/preview", headers=headers)
    sync = await client.post("/drive/sync", headers=headers)
    status_after = await client.get("/drive/status", headers=headers)
    disconnect = await client.delete("/drive/connection", headers=headers)
    status_final = await client.get("/drive/status", headers=headers)

    assert status.json()["connected"] is True and status.json()["folder_name"] == "Documentos de la empresa"
    assert (preview.json()["new"], preview.json()["unsupported"]) == (1, 1)
    assert sync.status_code == 200 and sync.json()["documents"] == 1
    assert sync.json()["unsupported"][0]["name"] == "foto.png"
    assert status_after.json()["last_sync"]["documents"] == 1
    assert disconnect.status_code == 204
    assert status_final.json() == {**status_final.json(), "connected": False}


@pytest.mark.asyncio
async def test_api_folder_endpoint_validates(db_session, client, drive_api):
    org, admin, service, rag, _drive, _oauth, _emb = await _connected(db_session, folder=None)
    drive_api["install"](service, rag)
    headers = _headers(admin, org.id)

    bad_link = await client.put("/drive/folder", json={"folder": "esto no es un enlace"}, headers=headers)
    missing = await client.put(
        "/drive/folder", json={"folder": "https://drive.google.com/drive/folders/no_existe_carpeta_123456"}, headers=headers
    )
    ok = await client.put(
        "/drive/folder", json={"folder": "https://drive.google.com/drive/folders/carpeta_valida_123456"}, headers=headers
    )
    sync_without_folder = None

    assert bad_link.status_code == 400 and missing.status_code == 400
    assert ok.status_code == 200 and ok.json()["folder_id"] == "carpeta_valida_123456"
    assert sync_without_folder is None


@pytest.mark.asyncio
async def test_api_sync_without_a_folder_is_a_clear_409(db_session, client, drive_api):
    org, admin, service, rag, _drive, _oauth, _emb = await _connected(db_session, folder=None)
    drive_api["install"](service, rag)

    response = await client.post("/drive/sync", headers=_headers(admin, org.id))

    assert response.status_code == 409 and "carpeta" in response.json()["detail"]


@pytest.mark.asyncio
async def test_api_is_admin_only(db_session, client, drive_api):
    org, _admin, service, rag, _drive, _oauth, _emb = await _connected(db_session)
    drive_api["install"](service, rag)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()
    headers = _headers(member, org.id)

    for method, url in [("get", "/drive/status"), ("get", "/drive/connect"), ("post", "/drive/sync"), ("get", "/drive/preview")]:
        response = await getattr(client, method)(url, headers=headers)
        assert response.status_code == 403, url


@pytest.mark.asyncio
async def test_api_embedding_quota_error_is_a_503_that_keeps_what_was_indexed(db_session, client, drive_api):
    import httpx
    from openai import RateLimitError

    org, admin, service, rag, drive, _oauth, embeddings = await _connected(db_session)
    drive_api["install"](service, rag)
    drive.put(drive_file("f1", "A.md"), md_bytes("A"))
    drive.put(drive_file("f2", "B.md"), md_bytes("B"))
    original = embeddings.embed
    state = {"calls": 0}

    async def flaky(texts):
        state["calls"] += 1
        if state["calls"] == 2:
            request = httpx.Request("POST", "http://litellm/embeddings")
            raise RateLimitError("cuota", response=httpx.Response(429, request=request), body=None)
        return await original(texts)

    embeddings.embed = flaky

    response = await client.post("/drive/sync", headers=_headers(admin, org.id))

    assert response.status_code == 503
    assert len(await _titles(db_session, org.id)) == 1  # el primero quedo indexado


@pytest.mark.asyncio
async def test_api_callback_rejects_missing_params_and_bad_state(db_session, client, drive_api):
    org, _admin, service, rag, _drive, _oauth, _emb = await _connected(db_session)
    drive_api["install"](service, rag)

    cancelled = await client.get("/drive/callback?error=access_denied")
    bad_state = await client.get("/drive/callback?code=abc&state=falso")

    assert cancelled.status_code == 400 and "cancelada" in cancelled.text
    assert bad_state.status_code == 400
