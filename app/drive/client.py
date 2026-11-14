"""Cliente minimo de la Google Drive REST API (v3) sobre httpx.

Solo lo que necesita la sincronizacion: quien es la cuenta, validar una
carpeta, listar una carpeta (con subcarpetas) y descargar un archivo.
httpx directo, igual que app.gmail.client: sin sumar el SDK de Google, que
es grande y sincrono, a un proyecto totalmente async.

Ningun mensaje de error incluye tokens ni contenido de documentos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

import httpx

from app.drive.errors import DriveAPIError, DriveFolderError, DriveNotConnectedError

API_BASE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

# Archivos nativos de Google: no tienen contenido descargable, se EXPORTAN
# a un formato que ya sabemos leer (app.rag.ingestion).
GOOGLE_EXPORTS: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "application/vnd.google-apps.spreadsheet": (
        ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "application/vnd.google-apps.presentation": (".pdf", "application/pdf"),
}
# Extensiones de archivos "normales" que sabemos leer.
READABLE_EXTENSIONS = (".md", ".pdf", ".xlsx", ".docx")

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_FILES = 500
MAX_DEPTH = 3
_PAGE_SIZE = 200

_FOLDER_ID = re.compile(r"[-\w]{15,}")


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: datetime
    size: int | None
    path: str  # carpeta relativa a la raiz sincronizada, para mostrar


@dataclass(frozen=True)
class Classification:
    """Que hacer con un archivo: `suffix` (extension con la que se lee) si
    se puede sincronizar, o `reason` si no."""

    suffix: str | None
    reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.suffix is not None


def parse_folder_reference(value: str) -> str:
    """El id de carpeta a partir de lo que el usuario pega: el id solo o
    la URL de Drive (drive.google.com/drive/folders/<id>?usp=sharing)."""
    value = value.strip()
    match = re.search(r"/folders/([-\w]+)", value)
    if match:
        return match.group(1)
    if _FOLDER_ID.fullmatch(value):
        return value
    raise DriveFolderError(
        "No reconoci la carpeta: pega el enlace de la carpeta de Drive o su id"
    )


def classify(file: DriveFile) -> Classification:
    if file.mime_type in GOOGLE_EXPORTS:
        return Classification(GOOGLE_EXPORTS[file.mime_type][0])
    if file.mime_type.startswith("application/vnd.google-apps."):
        return Classification(None, "tipo de archivo de Google sin exportacion soportada")
    if file.size is not None and file.size > MAX_FILE_BYTES:
        return Classification(None, f"supera {MAX_FILE_BYTES // (1024 * 1024)} MB")
    suffix = PurePosixPath(file.name).suffix.lower()
    if suffix in READABLE_EXTENSIONS:
        return Classification(suffix)
    return Classification(None, f"formato '{suffix or 'sin extension'}' no soportado")


def parse_time(value: str) -> datetime:
    # Drive entrega RFC 3339 con 'Z'.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class DriveClient:
    def __init__(self, access_token: str, *, timeout: float = 30.0) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._timeout = timeout

    async def get_account_email(self) -> str:
        data = await self._request_json("GET", "/about", params={"fields": "user(emailAddress)"})
        email = (data.get("user") or {}).get("emailAddress")
        if not isinstance(email, str) or not email:
            raise DriveAPIError("Drive no devolvio la direccion de la cuenta")
        return email.lower()

    async def get_folder(self, folder_id: str) -> tuple[str, str]:
        """(id, nombre) de una carpeta; DriveFolderError si no existe, no
        es una carpeta o la cuenta no tiene acceso."""
        try:
            data = await self._request_json(
                "GET",
                f"/files/{folder_id}",
                params={"fields": "id,name,mimeType", "supportsAllDrives": "true"},
            )
        except DriveAPIError as exc:
            if "404" in str(exc) or "403" in str(exc):
                raise DriveFolderError(
                    "No encuentro esa carpeta con la cuenta conectada; revisa el enlace "
                    "y que la cuenta tenga acceso"
                ) from exc
            raise
        if data.get("mimeType") != FOLDER_MIME:
            raise DriveFolderError("Ese enlace no es una carpeta de Drive")
        return data["id"], data.get("name", folder_id)

    async def list_folder(
        self, folder_id: str, *, max_depth: int = MAX_DEPTH, max_files: int = MAX_FILES
    ) -> tuple[list[DriveFile], bool]:
        """Todos los archivos de la carpeta y sus subcarpetas (hasta
        max_depth niveles). Devuelve (archivos, truncado): truncado=True si
        se alcanzo max_files -- en ese caso la lista esta INCOMPLETA y
        quien llama no debe concluir que lo que falta fue borrado."""
        files: list[DriveFile] = []
        queue: list[tuple[str, str, int]] = [(folder_id, "", 0)]
        while queue:
            current, prefix, depth = queue.pop(0)
            page_token: str | None = None
            while True:
                params = {
                    "q": f"'{current}' in parents and trashed = false",
                    "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
                    "pageSize": str(_PAGE_SIZE),
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                    "orderBy": "name",
                }
                if page_token:
                    params["pageToken"] = page_token
                data = await self._request_json("GET", "/files", params=params)
                for item in data.get("files", []):
                    if item["mimeType"] == FOLDER_MIME:
                        if depth < max_depth:
                            queue.append((item["id"], f"{prefix}{item['name']}/", depth + 1))
                        continue
                    if len(files) >= max_files:
                        return files, True
                    files.append(
                        DriveFile(
                            id=item["id"],
                            name=item["name"],
                            mime_type=item["mimeType"],
                            modified_time=parse_time(item["modifiedTime"]),
                            size=int(item["size"]) if item.get("size") else None,
                            path=prefix,
                        )
                    )
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return files, False

    async def download(self, file: DriveFile) -> bytes:
        """Contenido del archivo: exportado si es nativo de Google,
        descargado tal cual si no."""
        if file.mime_type in GOOGLE_EXPORTS:
            _suffix, export_mime = GOOGLE_EXPORTS[file.mime_type]
            return await self._request_bytes(
                f"/files/{file.id}/export", params={"mimeType": export_mime}
            )
        return await self._request_bytes(
            f"/files/{file.id}", params={"alt": "media", "supportsAllDrives": "true"}
        )

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._send(method, path, **kwargs)
        return response.json()

    async def _request_bytes(self, path: str, **kwargs: Any) -> bytes:
        return (await self._send("GET", path, **kwargs)).content

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{API_BASE}{path}", headers=self._headers, **kwargs
                )
        except httpx.HTTPError as exc:
            raise DriveAPIError(f"No se pudo contactar la Drive API ({method} {path})") from exc

        if response.status_code == 401:
            raise DriveNotConnectedError("Drive rechazo el access token (401)")
        if response.status_code >= 400:
            raise DriveAPIError(f"Drive API respondio {response.status_code} ({method} {path})")
        return response
