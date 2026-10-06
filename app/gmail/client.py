"""Cliente minimo de la Gmail REST API (v1) sobre httpx.

Solo las cuatro operaciones que usa el flujo: perfil, listar, leer y
enviar. Se usa httpx directo en vez de google-api-python-client para no
sumar una dependencia grande y sincrona a un proyecto totalmente async.

Ningun mensaje de error incluye tokens ni contenido de correos.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.gmail.errors import GmailAccessRevokedError, GmailAPIError

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailClient:
    def __init__(self, access_token: str, *, timeout: float = 15.0) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._timeout = timeout

    async def get_profile_email(self) -> str:
        data = await self._request("GET", "/profile")
        email = data.get("emailAddress")
        if not isinstance(email, str) or not email:
            raise GmailAPIError("Gmail no devolvio la direccion de la cuenta")
        return email.lower()

    async def list_message_ids(self, query: str, max_results: int) -> list[str]:
        data = await self._request(
            "GET", "/messages", params={"q": query, "maxResults": max_results}
        )
        return [m["id"] for m in data.get("messages", []) if "id" in m]

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/messages/{message_id}", params={"format": "full"})

    async def send_message(self, raw_base64url: str, thread_id: str | None) -> str:
        body: dict[str, Any] = {"raw": raw_base64url}
        if thread_id:
            body["threadId"] = thread_id
        data = await self._request("POST", "/messages/send", json=body)
        return data.get("id", "")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{API_BASE}{path}", headers=self._headers, **kwargs
                )
        except httpx.HTTPError as exc:
            raise GmailAPIError(f"No se pudo contactar la Gmail API ({method} {path})") from exc

        if response.status_code == 401:
            raise GmailAccessRevokedError("Gmail rechazo el access token (401)")
        if response.status_code >= 400:
            raise GmailAPIError(
                f"Gmail API respondio {response.status_code} ({method} {path})"
            )
        return response.json()
