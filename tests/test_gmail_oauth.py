import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
import pytest

from app.core.config import settings
from app.core.exceptions import InvalidTokenError
from app.core.security import create_access_token, decode_access_token
from app.gmail.errors import (
    GmailAccessRevokedError,
    GmailAPIError,
    GmailAuthorizationError,
    GmailNotConfiguredError,
)
from app.gmail.oauth import (
    REQUIRED_SCOPES,
    GoogleOAuth,
    _state_key,
    create_state,
    decode_state,
)


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret")


def test_state_roundtrip():
    user_id, org_id = uuid.uuid4(), uuid.uuid4()

    state = decode_state(create_state(user_id, org_id))

    assert state.user_id == user_id
    assert state.organization_id == org_id


def test_state_rejects_tampered_and_garbage_values():
    good = create_state(uuid.uuid4(), uuid.uuid4())

    for bad in (good[:-2] + "xx", "not-a-jwt", ""):
        with pytest.raises(GmailAuthorizationError):
            decode_state(bad)


def test_state_rejects_expired_token():
    expired = jwt.encode(
        {
            "purpose": "gmail-oauth-state",
            "uid": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        _state_key(),
        algorithm="HS256",
    )

    with pytest.raises(GmailAuthorizationError):
        decode_state(expired)


def test_state_rejects_an_access_token():
    # Un access token de la API (misma secret_key) no debe servir de state.
    access = create_access_token({"sub": str(uuid.uuid4()), "organization_id": str(uuid.uuid4())})

    with pytest.raises(GmailAuthorizationError):
        decode_state(access)


def test_state_can_never_be_used_as_an_access_token():
    # El state viaja en la URL: si se pudiera aceptar como Bearer, filtrar
    # el historial del navegador seria filtrar una sesion.
    state = create_state(uuid.uuid4(), uuid.uuid4())

    with pytest.raises(InvalidTokenError):
        decode_access_token(state)


def test_authorization_url_requests_offline_access_and_minimal_scopes(google_configured):
    url = GoogleOAuth().build_authorization_url("the-state")
    query = parse_qs(urlparse(url).query)

    assert urlparse(url).netloc == "accounts.google.com"
    assert query["client_id"] == ["client-id"]
    assert query["state"] == ["the-state"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert set(query["scope"][0].split()) == set(REQUIRED_SCOPES)
    assert not any("modify" in scope for scope in query["scope"][0].split())


def test_authorization_url_requires_credentials(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)

    with pytest.raises(GmailNotConfiguredError):
        GoogleOAuth().build_authorization_url("s")


@pytest.mark.asyncio
async def test_exchange_code_rejects_missing_scopes(google_configured, monkeypatch):
    oauth = GoogleOAuth()

    async def fake_post(form, *, error_cls):
        return {"access_token": "a", "refresh_token": "r", "scope": REQUIRED_SCOPES[0]}

    monkeypatch.setattr(oauth, "_post_token", fake_post)

    with pytest.raises(GmailAuthorizationError):
        await oauth.exchange_code("code")


@pytest.mark.asyncio
async def test_exchange_code_returns_tokens_when_all_scopes_granted(google_configured, monkeypatch):
    oauth = GoogleOAuth()

    async def fake_post(form, *, error_cls):
        return {"access_token": "a", "refresh_token": "r", "scope": " ".join(REQUIRED_SCOPES)}

    monkeypatch.setattr(oauth, "_post_token", fake_post)

    tokens = await oauth.exchange_code("code")

    assert tokens.access_token == "a"
    assert tokens.refresh_token == "r"


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _patch_http(monkeypatch, response):
    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return response

    monkeypatch.setattr("app.gmail.oauth.httpx.AsyncClient", FakeClient)


@pytest.mark.asyncio
async def test_refresh_maps_invalid_grant_to_access_revoked(google_configured, monkeypatch):
    _patch_http(monkeypatch, _FakeResponse(400, {"error": "invalid_grant"}))

    with pytest.raises(GmailAccessRevokedError):
        await GoogleOAuth().refresh_access_token("refresh")


@pytest.mark.asyncio
async def test_refresh_does_not_treat_server_errors_as_revoked(google_configured, monkeypatch):
    # Un 5xx de Google es transitorio: marcar la conexion como revocada
    # obligaria a reconectar por nada.
    _patch_http(monkeypatch, _FakeResponse(503, {"error": "backend_error"}))

    with pytest.raises(GmailAPIError) as exc_info:
        await GoogleOAuth().refresh_access_token("refresh")

    assert not isinstance(exc_info.value, GmailAccessRevokedError)
