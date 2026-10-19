"""Token exchange (app.core.sts), scopes por tool (app.core.scopes) y el
MCP server como resource server (mcp_server.auth)."""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest

from app.core import sts
from app.core.exceptions import InvalidTokenError
from app.core.scopes import (
    CONFIRMATION_REQUIRED_TOOLS,
    SCOPE_MIN_ROLE,
    TOOL_SCOPE,
    scopes_for_role,
)
from app.users.models import UserRole
from mcp_server import auth as mcp_auth
from mcp_server.server import mcp


def _user(role: UserRole = UserRole.ADMIN):
    return SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4(), role=role)


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


class _FakeJwksClient:
    """Resuelve la clave de firma como lo haria PyJWKClient con la JWKS real."""

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=sts._private_key().public_key())


@pytest.fixture(autouse=True)
def _mcp_uses_local_sts():
    with patch.object(mcp_auth, "_client", return_value=_FakeJwksClient()):
        yield


# --- STS -------------------------------------------------------------------


def test_delegated_token_claims():
    user = _user()
    claims = _claims(sts.issue_delegated_token(user, {"shipments:read"}))

    assert claims["aud"] == "mcp-server"
    assert claims["sub"] == str(user.id)
    assert claims["act"] == {"sub": "chat-service"}
    assert claims["scope"] == "shipments:read"
    assert claims["exp"] - claims["iat"] <= 300
    assert claims["jti"]


def test_scopes_are_clipped_to_the_role():
    viewer = _user(UserRole.VIEWER)
    claims = _claims(sts.issue_delegated_token(viewer, set(SCOPE_MIN_ROLE)))

    granted = set(claims["scope"].split())
    assert granted == scopes_for_role(UserRole.VIEWER)
    assert "users:delete" not in granted and "shipments:write" not in granted


def test_the_user_session_jwt_is_not_a_delegated_token():
    from app.core.security import create_access_token

    assert not sts.is_delegated_token(create_access_token({"sub": "x"}))
    assert sts.is_delegated_token(sts.issue_delegated_token(_user(), {"profile:read"}))


def test_decode_rejects_wrong_audience_and_tampering():
    token = sts.issue_delegated_token(_user(), {"profile:read"})
    assert sts.decode_delegated_token(token)["sub"]

    forged = jwt.encode({"sub": "x", "aud": "other"}, "k", algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        sts.decode_delegated_token(forged)


def test_jwks_exposes_only_the_public_key():
    key = sts.jwks()["keys"][0]
    assert key["kty"] == "RSA" and key["kid"] and "d" not in key


# --- scopes ----------------------------------------------------------------


def test_every_mcp_tool_declares_the_same_scope_as_app_scopes():
    announced = {t.name for t in asyncio.run(mcp.list_tools())}
    assert announced == set(TOOL_SCOPE)
    assert set(TOOL_SCOPE.values()) <= set(SCOPE_MIN_ROLE)


# --- MCP server como resource server ---------------------------------------


def _call(tool: str, args: dict, token: str | None):
    with (
        patch.object(mcp_auth, "_bearer_token", side_effect=_bearer(token)),
        patch("mcp_server.server.request", new=AsyncMock(return_value={"ok": True})) as fake,
    ):
        result = asyncio.run(mcp.call_tool(tool, args))
    return result, fake


def _bearer(token):
    def _get():
        if token is None:
            raise PermissionError("sin token")
        return token

    return _get


def test_mcp_executes_a_tool_when_the_scope_is_granted():
    token = sts.issue_delegated_token(_user(), {"shipments:read"})

    _result, fake = _call("shipment_stats", {}, token)

    fake.assert_awaited_once()
    # reenvia el token delegado a la API, no otro
    assert fake.await_args.kwargs["headers"]["authorization"] == f"Bearer {token}"


def test_mcp_rejects_a_tool_whose_scope_is_not_in_the_token():
    token = sts.issue_delegated_token(_user(), {"shipments:read"})

    with pytest.raises(Exception, match="scope"):
        _call("list_users", {}, token)


def test_mcp_rejects_a_missing_token():
    with pytest.raises(Exception, match="sin token"):
        _call("shipment_stats", {}, None)


def test_mcp_rejects_an_expired_token():
    token = sts.issue_delegated_token(_user(), {"shipments:read"}, ttl_seconds=1)
    time.sleep(2)

    with pytest.raises(Exception, match="invalido"):
        _call("shipment_stats", {}, token)


def test_mcp_rejects_a_token_signed_with_another_key():
    forged = jwt.encode(
        {"iss": "http://localhost:8000", "aud": "mcp-server", "sub": "x", "jti": "1",
         "exp": int(time.time()) + 60, "scope": "users:delete"},
        "not-the-sts-key-not-the-sts-key-1234", algorithm="HS256",
    )
    with pytest.raises(Exception, match="invalido"):
        _call("shipment_stats", {}, forged)


# --- confirmacion humana ---------------------------------------------------


def test_destructive_tools_are_marked_in_both_maps():
    for name in CONFIRMATION_REQUIRED_TOOLS:
        assert name in TOOL_SCOPE


def test_a_destructive_tool_is_rejected_without_approval_even_with_the_scope():
    token = sts.issue_delegated_token(_user(UserRole.OWNER), {"users:delete"})

    with pytest.raises(Exception, match="aprobacion"):
        _call("delete_user", {"user_id": "abc"}, token)


def test_an_approved_token_runs_exactly_the_approved_arguments():
    args = {"user_id": "abc"}
    token = sts.issue_delegated_token(
        _user(UserRole.OWNER), {"users:delete"},
        approved_action="act-1", approved_args_hash=sts.args_hash(args),
    )

    _result, fake = _call("delete_user", args, token)
    fake.assert_awaited_once()

    # mismo token, otros argumentos: la aprobacion no se transfiere
    with pytest.raises(Exception, match="aprobacion"):
        _call("delete_user", {"user_id": "otro"}, token)


def test_hash_helpers_agree_between_api_and_mcp():
    args = {"b": 1, "a": [1, 2], "c": None}
    assert sts.args_hash(args) == mcp_auth.args_hash(args)
