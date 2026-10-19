"""Confirmacion humana tecnica de las tools destructivas: el agente solo
crea una accion pendiente; ejecutarla exige la aprobacion del usuario."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.chat.models import PendingActionStatus
from app.chat.repository import ChatRepository
from app.chat.service import ActionNotFoundError, ActionNotPendingError
from app.llm.schemas import ToolCall
from app.users.models import UserRole
from tests.factories import create_organization, create_user
from tests.test_chat_tracing import FakeStreamingLLM, _response, _service


async def _pending(db_session, service, org, user, args=None):
    """Corre un turno en el que el LLM pide delete_user y devuelve el id de la accion."""
    args = args or {"user_id": "u-123"}
    llm = FakeStreamingLLM([
        ([], _response(content="", finish_reason="tool_calls",
                       tool_calls=[ToolCall(id="c1", name="delete_user", arguments=args)])),
        (["Necesito tu aprobacion"], _response(content="Necesito tu aprobacion")),
    ])
    service.llm_client = llm

    from tests.test_chat_tracing import _tool

    @asynccontextmanager
    async def _open(u, *, confirmation_handler=None, on_delegation=None):
        tool = _tool("delete_user")

        async def handler(**kwargs):
            return await confirmation_handler("delete_user", kwargs)

        tool.handler = handler
        yield [tool]

    with patch("app.chat.service.open_mcp_tools", _open):
        events = [e async for e in service.send_message_stream(org.id, user, "borra a u-123", None)]
    return events


@pytest.mark.asyncio
async def test_a_destructive_tool_call_creates_a_pending_action_and_does_not_execute(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service, _ = _service(db_session, None)

    events = await _pending(db_session, service, org, admin)

    [announced] = [e for e in events if e["event"] == "confirmation_required"]
    assert announced["action"]["tool_name"] == "delete_user"
    assert announced["action"]["arguments"] == {"user_id": "u-123"}
    [action] = await ChatRepository(db_session).list_pending_actions(admin.id)
    assert action.status == PendingActionStatus.PENDING.value


@pytest.mark.asyncio
async def test_approve_executes_once_with_the_exact_arguments(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service, _ = _service(db_session, None)
    await _pending(db_session, service, org, admin)
    [action] = await ChatRepository(db_session).list_pending_actions(admin.id)

    with patch(
        "app.chat.service.call_approved_tool", new=AsyncMock(return_value={"deleted": True})
    ) as call:
        done = await service.approve_action(admin, action.id)

    call.assert_awaited_once_with(admin, "delete_user", {"user_id": "u-123"}, str(action.id))
    assert done.status == PendingActionStatus.EXECUTED.value
    assert done.result == {"deleted": True}

    with pytest.raises(ActionNotPendingError):
        await service.approve_action(admin, action.id)  # no se ejecuta dos veces


@pytest.mark.asyncio
async def test_reject_never_executes(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service, _ = _service(db_session, None)
    await _pending(db_session, service, org, admin)
    [action] = await ChatRepository(db_session).list_pending_actions(admin.id)

    rejected = await service.reject_action(admin, action.id)
    assert rejected.status == PendingActionStatus.REJECTED.value

    with patch("app.chat.service.call_approved_tool", new=AsyncMock()) as call:
        with pytest.raises(ActionNotPendingError):
            await service.approve_action(admin, action.id)
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_another_user_cannot_approve_my_action(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    other = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service, _ = _service(db_session, None)
    await _pending(db_session, service, org, admin)
    [action] = await ChatRepository(db_session).list_pending_actions(admin.id)

    with pytest.raises(ActionNotFoundError):
        await service.approve_action(other, action.id)


# --- la API como resource server de tokens delegados ----------------------------


@pytest.mark.asyncio
async def test_api_accepts_a_delegated_token_on_business_routes(client, db_session):
    from app.core.sts import issue_delegated_token

    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, role=UserRole.MEMBER)
    token = issue_delegated_token(user, {"profile:read"})

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)


@pytest.mark.asyncio
async def test_a_delegated_token_cannot_call_chat_or_approve_actions(client, db_session):
    import uuid

    from app.core.sts import issue_delegated_token

    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    headers = {"Authorization": f"Bearer {issue_delegated_token(admin, {'users:delete'})}"}

    assert (await client.get("/chat/sessions", headers=headers)).status_code == 403
    approve = await client.post(f"/chat/actions/{uuid.uuid4()}/approve", headers=headers)
    assert approve.status_code == 403


@pytest.mark.asyncio
async def test_jwks_is_public(client):
    response = await client.get("/.well-known/jwks.json")

    assert response.status_code == 200
    assert response.json()["keys"][0]["alg"] == "RS256"
