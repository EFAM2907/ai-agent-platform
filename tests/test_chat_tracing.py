"""Trazabilidad de los turnos de chat: lo que TurnTracer junta, lo que
ChatService guarda en chat_turn_traces (incluso cuando el turno falla), y
los endpoints que lo exponen. Postgres real; el LLM, el RAG y el router
son falsos -- ningun test llama a un proveedor."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.chat.models import ChatMessage, ChatSession, ChatTurnTrace, MessageRole
from app.chat.repository import ChatRepository
from app.chat.service import CHEAP_MODEL, ChatService
from app.chat.tracing import TurnTracer, redact_arguments
from app.core.security import create_access_token
from app.llm.routing import RoutingDecision
from app.llm.schemas import LLMResponse, TokenUsage, ToolCall, ToolDefinition
from app.llm.tools import Tool
from app.users.models import UserRole
from tests.factories import create_organization, create_user


def _response(content="Hola", finish_reason="stop", tool_calls=None, cost=0.0002) -> LLMResponse:
    return LLMResponse(
        content=content, model=CHEAP_MODEL, provider="litellm",
        tokens_used=TokenUsage(input_tokens=100, output_tokens=20), latency_ms=12.0,
        finish_reason=finish_reason, tool_calls=tool_calls, estimated_cost_usd=cost,
    )


def _tool(name: str, result=None) -> Tool:
    return Tool(
        definition=ToolDefinition(name=name, description=name, parameters={"type": "object", "properties": {}}),
        handler=AsyncMock(return_value=result if result is not None else {"ok": True}),
    )


class FakeStreamingLLM:
    """rounds: lista de (deltas, LLMResponse) por vuelta; guarda los requests."""

    def __init__(self, rounds):
        self.rounds = rounds
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return self.rounds[len(self.requests) - 1][1]

    async def generate_stream(self, request, collector=None):
        self.requests.append(request)
        deltas, final = self.rounds[len(self.requests) - 1]
        for delta in deltas:
            yield delta
        if collector is not None:
            collector.final_response = final


def _chunk():
    return SimpleNamespace(id=uuid.uuid4(), article_id=uuid.uuid4(), content="texto del articulo")


def _service(db_session, llm, *, retrieved=None, retrieve_error=None):
    rag = AsyncMock()
    if retrieve_error:
        rag.retrieve.side_effect = retrieve_error
    else:
        rag.retrieve.return_value = retrieved if retrieved is not None else []
    router = AsyncMock()
    router.decide.return_value = RoutingDecision(CHEAP_MODEL, "classified_cheap", _response(cost=0.0001))
    return ChatService(
        repository=ChatRepository(db_session), rag_service=rag, llm_client=llm,
        model_router=router, session=db_session,
    ), rag


def _with_tools(tools):
    @asynccontextmanager
    async def _open(user, **_kwargs):
        yield tools

    return patch("app.chat.service.open_mcp_tools", _open)


async def _traces(db_session, org_id):
    return list((await db_session.execute(
        select(ChatTurnTrace).where(ChatTurnTrace.organization_id == org_id)
    )).scalars().all())


# --------------------------------------------------------------------------
# TurnTracer (sin base de datos)
# --------------------------------------------------------------------------
def test_redact_arguments_hides_secrets_at_any_depth_and_trims_long_text():
    redacted = redact_arguments(
        {"email": "a@b.com", "password": "x", "nested": {"api_key": "k", "ok": 1}, "note": "n" * 900}
    )

    assert redacted["password"] == "***"
    assert redacted["nested"] == {"api_key": "***", "ok": 1}
    assert redacted["email"] == "a@b.com"
    assert len(redacted["note"]) == 501


def test_tracer_totals_cover_router_and_answer_calls():
    tracer = TurnTracer()
    tracer.record_route(CHEAP_MODEL, "classified_cheap", _response(cost=0.0001))
    tracer.on_llm_response(_response(cost=0.0002))
    tracer.on_llm_response(_response(cost=0.0003))

    trace = tracer.build(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), session_id=uuid.uuid4(),
        user_message_id=None, assistant_message_id=None,
    )

    assert len(trace.llm_calls) == 3
    assert [c["purpose"] for c in trace.llm_calls] == ["router", "answer", "answer"]
    assert trace.input_tokens == 300 and trace.output_tokens == 60
    assert float(trace.cost_usd) == pytest.approx(0.0006)
    assert len(tracer.trace_id) == 32


def test_tracer_cost_is_none_when_no_call_reports_a_price():
    tracer = TurnTracer()
    tracer.on_llm_response(_response(cost=None))

    trace = tracer.build(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), session_id=uuid.uuid4(),
        user_message_id=None, assistant_message_id=None,
    )

    assert trace.cost_usd is None


def test_tracer_records_tool_calls_with_redacted_arguments_and_no_result_body():
    tracer = TurnTracer()
    tracer.on_tool_call(
        "create_user", {"email": "n@x.com", "password": "secreto"},
        ok=True, duration_ms=12.34, error=None, result_chars=80,
    )

    [call] = tracer.tool_calls
    assert call["arguments"]["password"] == "***"
    assert "result" not in call and call["result_chars"] == 80


# --------------------------------------------------------------------------
# ChatService con base de datos real
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_turn_persists_messages_and_a_complete_trace(db_session):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, role=UserRole.ADMIN)
    tool_call = ToolCall(id="c1", name="list_shipments", arguments={"limit": 5})
    llm = FakeStreamingLLM([
        ([], _response(content="", finish_reason="tool_calls", tool_calls=[tool_call])),
        (["Tienes ", "3 envios"], _response(content="Tienes 3 envios")),
    ])
    service, _rag = _service(db_session, llm, retrieved=[(_chunk(), "Politica de retrasos", 0.031)])

    with _with_tools([_tool("list_shipments", {"total_shipments": 3})]):
        events = [e async for e in service.send_message_stream(org.id, user, "cuantos envios hay?", None)]

    kinds = [e["event"] for e in events]
    assert kinds == ["start", "tool", "tool", "delta", "delta", "done"]
    assert events[1] == {"event": "tool", "name": "list_shipments", "phase": "start", "ok": None}
    assert events[2]["phase"] == "end" and events[2]["ok"] is True
    assert events[0]["sources"] == ["Politica de retrasos"]

    [trace] = await _traces(db_session, org.id)
    assert trace.status == "ok" and trace.trace_id == events[0]["trace_id"]
    assert trace.model == CHEAP_MODEL
    assert trace.route["reason"] == "classified_cheap"
    assert trace.retrieval["returned"] == 1
    assert trace.retrieval["chunks"][0]["title"] == "Politica de retrasos"
    assert [t["name"] for t in trace.tool_calls] == ["list_shipments"]
    assert trace.tool_calls[0]["arguments"] == {"limit": 5}
    assert [c["purpose"] for c in trace.llm_calls] == ["router", "answer", "answer"]
    assert trace.input_tokens == 300
    assert trace.assistant_message_id is not None and trace.user_message_id is not None


@pytest.mark.asyncio
async def test_trace_id_travels_to_the_gateway_on_every_request(db_session):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id)
    llm = FakeStreamingLLM([(["ok"], _response(content="ok"))])
    service, _rag = _service(db_session, llm)

    with _with_tools([]):
        events = [e async for e in service.send_message_stream(org.id, user, "hola", None)]

    request = llm.requests[0]
    assert request.trace_id == events[0]["trace_id"]
    assert request.session_id == events[0]["session_id"]
    assert request.user_id == str(user.id)
    assert request.tenant_id == str(org.id)
    assert request.request_tag == "chat_answer"


@pytest.mark.asyncio
async def test_failed_turn_keeps_the_user_message_and_records_an_error_trace(db_session):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id)
    llm = AsyncMock()
    llm.generate_stream = None  # fuerza una falla al usar el cliente
    service, _rag = _service(db_session, llm)

    with _with_tools([]):
        events = [e async for e in service.send_message_stream(org.id, user, "hola", None)]

    assert [e["event"] for e in events] == ["start", "error"]
    messages = (await db_session.execute(select(ChatMessage))).scalars().all()
    assert [(m.role, m.content) for m in messages] == [(MessageRole.USER, "hola")]
    [trace] = await _traces(db_session, org.id)
    assert trace.status == "error" and trace.error_type
    assert trace.assistant_message_id is None and trace.user_message_id == messages[0].id
    assert events[1]["trace_id"] == trace.trace_id


@pytest.mark.asyncio
async def test_a_knowledge_base_failure_does_not_break_the_turn(db_session):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id)
    llm = FakeStreamingLLM([(["Respuesta"], _response(content="Respuesta"))])
    service, _rag = _service(db_session, llm, retrieve_error=RuntimeError("cuota de embeddings"))

    with _with_tools([]):
        events = [e async for e in service.send_message_stream(org.id, user, "hola", None)]

    assert events[-1]["event"] == "done"
    [trace] = await _traces(db_session, org.id)
    assert trace.status == "ok"
    assert "cuota de embeddings" in trace.retrieval["error"]
    assert trace.retrieval["returned"] == 0
    system_prompt = llm.requests[0].messages[0].content
    assert "No knowledge base content was found" in system_prompt


@pytest.mark.asyncio
async def test_a_session_of_another_user_is_never_continued(db_session):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id)
    intruder = await create_user(db_session, org.id)
    foreign = ChatSession(organization_id=org.id, user_id=owner.id)
    db_session.add(foreign)
    await db_session.flush()
    llm = FakeStreamingLLM([(["ok"], _response(content="ok"))])
    service, _rag = _service(db_session, llm)

    with _with_tools([]):
        events = [e async for e in service.send_message_stream(org.id, intruder, "hola", foreign.id)]

    assert events[0]["session_id"] != str(foreign.id)  # se abrio una conversacion nueva
    foreign_messages = (await db_session.execute(
        select(ChatMessage).where(ChatMessage.session_id == foreign.id)
    )).scalars().all()
    assert foreign_messages == []  # nada se escribio en la conversacion ajena


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, expected",
    [
        (UserRole.VIEWER, {"get_shipment_status"}),
        (UserRole.MEMBER, {"get_shipment_status", "create_shipment"}),
        (UserRole.ADMIN, {"get_shipment_status", "create_shipment", "delete_user"}),
    ],
)
async def test_the_model_only_sees_the_tools_its_role_allows(db_session, role, expected):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, role=role)
    llm = FakeStreamingLLM([(["ok"], _response(content="ok"))])
    service, _rag = _service(db_session, llm)
    all_tools = [_tool("get_shipment_status"), _tool("create_shipment"), _tool("delete_user")]

    with _with_tools(all_tools):
        [e async for e in service.send_message_stream(org.id, user, "hola", None)]

    offered = {t.name for t in (llm.requests[0].tools or [])}
    assert offered == expected


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
def _headers(user, org_id):
    token = create_access_token({"sub": str(user.id), "organization_id": str(org_id)})
    return {"Authorization": f"Bearer {token}"}


async def _turn(db_session, org, user, text="hola"):
    llm = FakeStreamingLLM([(["ok"], _response(content="ok"))])
    service, _rag = _service(db_session, llm)
    with _with_tools([]):
        events = [e async for e in service.send_message_stream(org.id, user, text, None)]
    await db_session.commit()
    return events[-1]["message_id"], events[0]["session_id"]


@pytest.mark.asyncio
async def test_message_trace_is_visible_to_its_owner_and_admins_only(db_session, client):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.MEMBER)
    other_member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    message_id, _session = await _turn(db_session, org, owner)

    own = await client.get(f"/chat/messages/{message_id}/trace", headers=_headers(owner, org.id))
    by_admin = await client.get(f"/chat/messages/{message_id}/trace", headers=_headers(admin, org.id))
    by_peer = await client.get(f"/chat/messages/{message_id}/trace", headers=_headers(other_member, org.id))

    assert own.status_code == 200 and by_admin.status_code == 200
    assert by_peer.status_code == 404
    body = own.json()
    assert body["status"] == "ok" and body["model"] == CHEAP_MODEL
    assert body["llm_calls"][0]["purpose"] == "router"


@pytest.mark.asyncio
async def test_traces_listing_stats_and_detail_are_admin_only_and_tenant_scoped(db_session, client):
    org = await create_organization(db_session)
    other_org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    foreign_admin = await create_user(db_session, other_org.id, role=UserRole.ADMIN)
    await _turn(db_session, org, member)
    await _turn(db_session, org, member)

    forbidden = await client.get("/chat/traces", headers=_headers(member, org.id))
    listing = await client.get("/chat/traces", headers=_headers(admin, org.id))
    foreign = await client.get("/chat/traces", headers=_headers(foreign_admin, other_org.id))
    stats = await client.get("/chat/traces/stats?days=7", headers=_headers(admin, org.id))
    detail = await client.get(
        f"/chat/traces/{listing.json()['traces'][0]['id']}", headers=_headers(admin, org.id)
    )
    foreign_detail = await client.get(
        f"/chat/traces/{listing.json()['traces'][0]['id']}", headers=_headers(foreign_admin, other_org.id)
    )
    bad_filter = await client.get("/chat/traces?status=raro", headers=_headers(admin, org.id))

    assert forbidden.status_code == 403
    assert listing.json()["total"] == 2
    assert foreign.json()["total"] == 0
    assert stats.status_code == 200 and stats.json()["turns"] == 2 and stats.json()["errors"] == 0
    assert stats.json()["by_model"][0]["name"] == CHEAP_MODEL
    assert detail.status_code == 200
    assert foreign_detail.status_code == 404
    assert bad_filter.status_code == 422


@pytest.mark.asyncio
async def test_feedback_can_be_set_changed_and_removed_only_on_own_assistant_messages(db_session, client):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id)
    other = await create_user(db_session, org.id)
    message_id, session_id = await _turn(db_session, org, owner)
    url = f"/chat/messages/{message_id}/feedback"

    up = await client.post(url, json={"rating": 1}, headers=_headers(owner, org.id))
    down = await client.post(url, json={"rating": -1, "comment": " no era eso "}, headers=_headers(owner, org.id))
    history = await client.get(f"/chat/sessions/{session_id}", headers=_headers(owner, org.id))
    removed = await client.post(url, json={"rating": None}, headers=_headers(owner, org.id))
    foreign = await client.post(url, json={"rating": 1}, headers=_headers(other, org.id))
    invalid = await client.post(url, json={"rating": 5}, headers=_headers(owner, org.id))

    assert up.json()["rating"] == 1
    assert down.json() == {"message_id": message_id, "rating": -1, "comment": "no era eso"}
    assistant = [m for m in history.json()["messages"] if m["role"] == "assistant"][0]
    assert assistant["feedback"] == -1
    assert removed.json()["rating"] is None
    assert foreign.status_code == 404
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_feedback_is_rejected_on_user_messages(db_session, client):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id)
    _message_id, session_id = await _turn(db_session, org, owner)
    user_message = (await db_session.execute(
        select(ChatMessage).where(ChatMessage.session_id == uuid.UUID(session_id), ChatMessage.role == MessageRole.USER)
    )).scalar_one()

    response = await client.post(
        f"/chat/messages/{user_message.id}/feedback", json={"rating": 1}, headers=_headers(owner, org.id)
    )

    assert response.status_code == 404
