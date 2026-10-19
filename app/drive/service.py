"""Conexion de Google Drive y sincronizacion de una carpeta con la base
de conocimiento.

La sincronizacion es INCREMENTAL a proposito: descargar y reindexar
(embeber) toda la carpeta en cada pasada gastaria cuota de la API de
embeddings y de Drive por nada. Un archivo cuya fecha de modificacion no
cambio desde la ultima vez ni se descarga; los que ya no estan en la
carpeta se retiran de la base (app.rag.sync).
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.drive.client import DriveClient, DriveFile, classify, parse_folder_reference
from app.drive.errors import (
    DriveAPIError,
    DriveError,
    DriveFolderNotSetError,
    DriveNotConnectedError,
)
from app.drive.models import DriveConnection
from app.drive.repository import DriveRepository
from app.gmail.errors import GmailAccessRevokedError, GmailAuthorizationError
from app.gmail.oauth import DRIVE_STATE_PURPOSE, GoogleOAuth, create_state, decode_state
from app.rag.ingestion import DocumentLoadError, UnsupportedSourceError, load_documents
from app.rag.models import SOURCE_DRIVE
from app.rag.service import RAGService
from app.rag.sync import sync_documents

logger = logging.getLogger(__name__)

SCOPE_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
SOURCE_REF_PREFIX = "drive:"

ClientFactory = Callable[[str], DriveClient]


def drive_oauth() -> GoogleOAuth:
    """El cliente OAuth de Google (mismas credenciales que Gmail) con el
    scope de SOLO LECTURA de Drive y su propia URI de retorno."""
    return GoogleOAuth(
        scopes=(SCOPE_DRIVE_READONLY,),
        redirect_uri=settings.google_drive_redirect_uri,
        scope_error="Falta el permiso para leer tus archivos de Google Drive; vuelve a conectar y acepta el acceso",
    )


def file_id_of(source_ref: str | None) -> str | None:
    """'drive:<id>' o 'drive:<id>#<titulo>' -> '<id>'."""
    if not source_ref or not source_ref.startswith(SOURCE_REF_PREFIX):
        return None
    return source_ref[len(SOURCE_REF_PREFIX):].split("#", 1)[0]


@dataclass
class FileOutcome:
    name: str
    path: str
    status: str  # "new" | "changed" | "unchanged" | "unsupported" | "failed"
    reason: str | None = None


@dataclass
class SyncResult:
    documents: int = 0
    unchanged: int = 0
    pruned: int = 0
    truncated: bool = False
    files: list[FileOutcome] = field(default_factory=list)

    @property
    def unsupported(self) -> list[FileOutcome]:
        return [f for f in self.files if f.status == "unsupported"]

    @property
    def failed(self) -> list[FileOutcome]:
        return [f for f in self.files if f.status == "failed"]

    def summary(self) -> dict:
        """Lo que se guarda en la conexion y se muestra en la interfaz."""
        return {
            "documents": self.documents,
            "unchanged": self.unchanged,
            "pruned": self.pruned,
            "truncated": self.truncated,
            "unsupported": [asdict(f) for f in self.unsupported],
            "failed": [asdict(f) for f in self.failed],
            "at": datetime.now(timezone.utc).isoformat(),
        }


class DriveService:
    def __init__(
        self,
        repository: DriveRepository,
        session: AsyncSession,
        oauth: GoogleOAuth | None = None,
        client_factory: ClientFactory = DriveClient,
    ) -> None:
        self.repository = repository
        self.session = session
        self.oauth = oauth or drive_oauth()
        self.client_factory = client_factory

    # ------------------------------------------------------------------
    # Conexion
    # ------------------------------------------------------------------
    def start_authorization(self, user_id: uuid.UUID, organization_id: uuid.UUID) -> str:
        return self.oauth.build_authorization_url(
            create_state(user_id, organization_id, DRIVE_STATE_PURPOSE)
        )

    async def complete_authorization(self, code: str, state: str) -> DriveConnection:
        oauth_state = decode_state(state, DRIVE_STATE_PURPOSE)
        tokens = await self.oauth.exchange_code(code)
        if not tokens.refresh_token:
            raise GmailAuthorizationError(
                "Google no entrego un refresh token. Revoca el acceso de la app en "
                "https://myaccount.google.com/permissions y vuelve a conectar"
            )
        email = await self.client_factory(tokens.access_token).get_account_email()
        connection = await self.repository.upsert_connection(
            organization_id=oauth_state.organization_id,
            user_id=oauth_state.user_id,
            google_email=email,
            encrypted_refresh_token=encrypt_secret(tokens.refresh_token),
        )
        await self.session.commit()
        logger.info("Drive conectado para la organizacion %s (%s)", oauth_state.organization_id, email)
        return connection

    async def get_active_connection(self, organization_id: uuid.UUID) -> DriveConnection | None:
        connection = await self.repository.get_connection(organization_id)
        if connection is None or connection.revoked_at is not None:
            return None
        return connection

    async def disconnect(
        self, organization_id: uuid.UUID, *, rag_service: RAGService | None = None, remove_documents: bool = False
    ) -> bool:
        """Revoca el token (mejor esfuerzo) y marca la conexion como
        revocada. Los documentos que ya se indexaron se CONSERVAN salvo
        remove_documents=True: desconectar Drive no tiene por que vaciar el
        conocimiento que el asistente ya usa."""
        connection = await self.get_active_connection(organization_id)
        if connection is None:
            return False
        await self.oauth.revoke(decrypt_secret(connection.refresh_token))
        await self.repository.mark_revoked(connection)
        if remove_documents and rag_service is not None:
            await rag_service.prune_source(organization_id, SOURCE_DRIVE, set())
        await self.session.commit()
        return True

    async def _client(self, connection: DriveConnection) -> DriveClient:
        """Cliente de Drive con un access token fresco. Si Google ya no
        acepta el refresh token, la conexion se marca revocada para que la
        interfaz pida reconectar."""
        try:
            access_token = await self.oauth.refresh_access_token(
                decrypt_secret(connection.refresh_token)
            )
        except GmailAccessRevokedError as exc:
            await self.repository.mark_revoked(connection)
            await self.session.commit()
            raise DriveNotConnectedError(
                "Google revoco el acceso a Drive; hay que volver a conectar la cuenta"
            ) from exc
        return self.client_factory(access_token)

    # ------------------------------------------------------------------
    # Carpeta
    # ------------------------------------------------------------------
    async def set_folder(self, organization_id: uuid.UUID, folder_reference: str) -> DriveConnection:
        connection = await self.get_active_connection(organization_id)
        if connection is None:
            raise DriveNotConnectedError("Drive no esta conectado para esta organizacion")
        folder_id = parse_folder_reference(folder_reference)
        client = await self._client(connection)
        resolved_id, name = await client.get_folder(folder_id)
        await self.repository.set_folder(connection, resolved_id, name)
        await self.session.commit()
        return connection

    # ------------------------------------------------------------------
    # Vista previa y sincronizacion
    # ------------------------------------------------------------------
    async def _require_syncable(self, organization_id: uuid.UUID) -> DriveConnection:
        connection = await self.get_active_connection(organization_id)
        if connection is None:
            raise DriveNotConnectedError("Drive no esta conectado para esta organizacion")
        if not connection.folder_id:
            raise DriveFolderNotSetError("Elige primero la carpeta de Drive que quieres sincronizar")
        return connection

    @staticmethod
    def _known_files(articles) -> dict[str, datetime | None]:
        """id de archivo de Drive -> fecha de modificacion con la que se
        indexo, a partir de los articulos que ya tiene la KB."""
        known: dict[str, datetime | None] = {}
        for article in articles:
            file_id = file_id_of(article.source_ref)
            if file_id:
                known[file_id] = article.source_modified_at
        return known

    async def preview(self, organization_id: uuid.UUID, rag_service: RAGService) -> SyncResult:
        """Que haria una sincronizacion AHORA, sin descargar ni indexar
        nada: por cada archivo, si es nuevo, cambio, esta igual o no se
        puede leer."""
        connection = await self._require_syncable(organization_id)
        client = await self._client(connection)
        files, truncated = await client.list_folder(connection.folder_id)
        known = self._known_files(
            await rag_service.repository.list_active_by_source(organization_id, SOURCE_DRIVE)
        )
        result = SyncResult(truncated=truncated)
        for file in files:
            classification = classify(file)
            if not classification.supported:
                result.files.append(FileOutcome(file.name, file.path, "unsupported", classification.reason))
            elif file.id not in known:
                result.files.append(FileOutcome(file.name, file.path, "new"))
            elif known[file.id] != file.modified_time:
                result.files.append(FileOutcome(file.name, file.path, "changed"))
            else:
                result.files.append(FileOutcome(file.name, file.path, "unchanged"))
        return result

    async def sync(
        self,
        organization_id: uuid.UUID,
        rag_service: RAGService,
        *,
        rate_limit_attempts: int = 1,
    ) -> SyncResult:
        connection = await self._require_syncable(organization_id)
        client = await self._client(connection)
        files, truncated = await client.list_folder(connection.folder_id)

        existing = await rag_service.repository.list_active_by_source(organization_id, SOURCE_DRIVE)
        known = self._known_files(existing)
        refs_by_file: dict[str, set[str]] = {}
        title_owner: dict[str, str | None] = {}
        for article in existing:
            file_id = file_id_of(article.source_ref)
            if file_id and article.source_ref:
                refs_by_file.setdefault(file_id, set()).add(article.source_ref)
            title_owner[article.title] = file_id

        result = SyncResult(truncated=truncated)
        documents = []
        keep_refs: set[str] = set()
        seen_titles: dict[str, str] = {}  # titulo -> archivo que lo trajo en esta pasada

        for file in files:
            classification = classify(file)
            if not classification.supported:
                result.files.append(FileOutcome(file.name, file.path, "unsupported", classification.reason))
                continue

            if known.get(file.id) == file.modified_time and file.id in known:
                result.unchanged += 1
                keep_refs |= refs_by_file.get(file.id, set())
                result.files.append(FileOutcome(file.name, file.path, "unchanged"))
                continue

            try:
                loaded = await self._load(client, file, classification.suffix)
            except (DocumentLoadError, UnsupportedSourceError, DriveAPIError) as exc:
                # Un archivo malo no frena a los demas, y lo que ya estaba
                # indexado de el NO se retira por un fallo transitorio.
                keep_refs |= refs_by_file.get(file.id, set())
                result.files.append(FileOutcome(file.name, file.path, "failed", str(exc)[:200]))
                continue

            duplicate = next(
                (
                    doc.title
                    for doc in loaded
                    if doc.title in seen_titles or title_owner.get(doc.title, file.id) != file.id
                ),
                None,
            )
            if duplicate is not None:
                # Dos archivos con el mismo titulo se pisarian en cada
                # pasada (y se reembeberian siempre): el segundo se rechaza.
                keep_refs |= refs_by_file.get(file.id, set())
                result.files.append(
                    FileOutcome(
                        file.name, file.path, "failed",
                        f"el titulo '{duplicate}' ya lo usa otro documento; renombra el archivo",
                    )
                )
                continue

            for doc in loaded:
                seen_titles[doc.title] = file.id
                doc.source_ref = (
                    f"{SOURCE_REF_PREFIX}{file.id}"
                    if len(loaded) == 1
                    else f"{SOURCE_REF_PREFIX}{file.id}#{doc.title}"
                )
                doc.source_modified_at = file.modified_time
            documents.extend(loaded)
            result.files.append(
                FileOutcome(file.name, file.path, "new" if file.id not in known else "changed")
            )

        report = await sync_documents(
            rag_service,
            organization_id,
            documents,
            source_type=SOURCE_DRIVE,
            ref_prefix=SOURCE_REF_PREFIX,
            # Con la lista truncada no se sabe que fue borrado de verdad.
            prune=not truncated,
            keep_refs=keep_refs,
            rate_limit_attempts=rate_limit_attempts,
        )
        result.documents = report.documents
        result.pruned = report.pruned

        await self.repository.record_sync(connection, result.summary())
        await self.session.commit()
        return result

    @staticmethod
    async def _load(client: DriveClient, file: DriveFile, suffix: str):
        """Descarga el archivo a un directorio temporal conservando su
        nombre (los loaders derivan el titulo del nombre) y lo normaliza."""
        content = await client.download(file)
        stem = PurePosixPath(file.name).stem if PurePosixPath(file.name).suffix.lower() == suffix else file.name
        safe_name = "".join(ch for ch in stem if ch not in '<>:"/\\|?*').strip() or "documento"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{safe_name}{suffix}"
            path.write_bytes(content)
            documents = load_documents(path)
        for document in documents:
            document.mime_type = document.mime_type or file.mime_type
        return documents
