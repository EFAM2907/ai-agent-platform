"""OAuth 2.0 (authorization code + refresh token) contra Google.

Scopes minimos: gmail.readonly (buscar/leer correos y el perfil) y
gmail.send (responder). Deliberadamente NO gmail.modify -- no marcamos
ni archivamos nada; la idempotencia vive en gmail_processed_messages.

`state` es un JWT firmado, de vida corta, que ata el callback al
usuario/organizacion que inicio el flujo (el callback lo llama el
navegador redirigido por Google, sin Bearer token). Se firma con una
clave DERIVADA de secret_key y con un claim `purpose`, nunca con
secret_key directa: asi un state (que viaja en la URL, o sea en el
historial del navegador) jamas podria aceptarse como access token de la
API, que se firma con la misma secret_key.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import settings
from app.gmail.errors import (
    GmailAccessRevokedError,
    GmailAPIError,
    GmailAuthorizationError,
    GmailNotConfiguredError,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
REQUIRED_SCOPES = (SCOPE_READONLY, SCOPE_SEND)

_STATE_PURPOSE = "gmail-oauth-state"
_STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class OAuthState:
    user_id: uuid.UUID
    organization_id: uuid.UUID


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    scopes: frozenset[str]


def _state_key() -> str:
    return hashlib.sha256(f"gmail-oauth:{settings.secret_key}".encode()).hexdigest()


def create_state(user_id: uuid.UUID, organization_id: uuid.UUID) -> str:
    payload = {
        "purpose": _STATE_PURPOSE,
        "uid": str(user_id),
        "org": str(organization_id),
        "exp": datetime.now(timezone.utc) + _STATE_TTL,
    }
    return jwt.encode(payload, _state_key(), algorithm="HS256")


def decode_state(state: str) -> OAuthState:
    try:
        payload = jwt.decode(state, _state_key(), algorithms=["HS256"])
        if payload.get("purpose") != _STATE_PURPOSE:
            raise ValueError("purpose")
        return OAuthState(
            user_id=uuid.UUID(payload["uid"]), organization_id=uuid.UUID(payload["org"])
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise GmailAuthorizationError("State de OAuth invalido o vencido") from exc


class GoogleOAuth:
    """Cliente del endpoint OAuth de Google. Inyectable en los servicios
    para poder sustituirlo en tests sin red."""

    def _require_credentials(self) -> tuple[str, str]:
        if not settings.google_client_id or not settings.google_client_secret:
            raise GmailNotConfiguredError(
                "Falta GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en la configuracion"
            )
        return settings.google_client_id, settings.google_client_secret

    def build_authorization_url(self, state: str) -> str:
        client_id, _ = self._require_credentials()
        params = {
            "client_id": client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_SCOPES),
            # offline + consent: sin prompt=consent Google solo entrega
            # refresh_token la PRIMERA vez que la cuenta autoriza la app,
            # y una reconexion quedaria sin token.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> OAuthTokens:
        client_id, client_secret = self._require_credentials()
        data = await self._post_token(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            error_cls=GmailAuthorizationError,
        )
        scopes = frozenset(str(data.get("scope", "")).split())
        missing = [s for s in REQUIRED_SCOPES if s not in scopes]
        if missing:
            # Consentimiento granular: el usuario puede desmarcar
            # permisos en la pantalla de Google.
            raise GmailAuthorizationError(
                "Faltan permisos de Gmail: es necesario aceptar leer y enviar correos"
            )
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            scopes=scopes,
        )

    async def refresh_access_token(self, refresh_token: str) -> str:
        client_id, client_secret = self._require_credentials()
        data = await self._post_token(
            {
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            error_cls=GmailAccessRevokedError,
        )
        return data["access_token"]

    async def revoke(self, token: str) -> None:
        """Mejor esfuerzo: si Google no responde, la conexion local se
        borra igual -- el token queda inutil para la app de todos modos."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            pass

    async def _post_token(self, form: dict, *, error_cls: type[Exception]) -> dict:
        """`error_cls` es lo que significa `invalid_grant` para quien
        llama: al canjear un codigo, un flujo de autorizacion fallido;
        al refrescar, acceso revocado. Cualquier otra falla (red, 5xx,
        invalid_client...) es GmailAPIError: un problema transitorio o
        de configuracion NO debe interpretarse como acceso revocado."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise GmailAPIError("No se pudo contactar a Google para obtener tokens") from exc

        if response.status_code == 200:
            return response.json()

        # El cuerpo de error de Google no trae secretos, pero solo
        # exponemos su codigo corto, no el texto completo.
        try:
            error_code = response.json().get("error", "unknown")
        except ValueError:
            error_code = "unknown"
        if error_code == "invalid_grant":
            raise error_cls("El codigo o el token de Google ya no es valido (invalid_grant)")
        raise GmailAPIError(f"Google rechazo la solicitud de token ({error_code})")
