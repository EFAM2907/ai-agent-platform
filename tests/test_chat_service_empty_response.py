"""Un LLM puede completar sin lanzar ninguna excepcion (finish_reason
distinto de "tool_calls", content vacio) -- pasa de verdad bajo
rate-limit en cascada (ver el incidente real: Gemini 429 -> fallback a
proveedores sin credito -> eventualmente una llamada "exitosa" con
texto vacio). Sin este chequeo, ChatService persistia un ChatMessage
vacio y devolvia "done"/200 como si la conversacion hubiera
funcionado -- indistinguible, para el usuario, de que el sistema
simplemente no respondio nada.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.chat.models import ChatSession
from app.chat.service import CHEAP_MODEL, ChatService, EmptyResponseError
from app.llm.schemas import LLMResponse, TokenUsage
from app.users.models import User, UserRole


@pytest.fixture(autouse=True)
def _fake_mcp_tools():
    """ChatService abre una sesion MCP real (app.chat.mcp_tools.open_mcp_tools)
    alrededor del tool loop -- estos tests ejercitan el tool loop en si
    (via run_tool_loop/run_streaming_tool_loop), no el transporte MCP,
    asi que se reemplaza por un context manager que entrega una lista
    de tools vacia sin abrir ninguna conexion real. tests/test_chat_mcp_tools.py
    cubre open_mcp_tools por separado."""

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _empty_tools(bearer_token: str):
        yield []

    with patch("app.chat.service.open_mcp_tools", _empty_tools):
        yield


def _current_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="owner@test.com",
        hashed_password="x",
        full_name="Test Owner",
        organization_id=uuid.uuid4(),
        role=UserRole.OWNER,
    )


def _empty_response(finish_reason: str = "stop") -> LLMResponse:
    return LLMResponse(
        content="",
        model=CHEAP_MODEL,
        provider="litellm",
        tokens_used=TokenUsage(input_tokens=5, output_tokens=0),
        latency_ms=1.0,
        finish_reason=finish_reason,
    )


def _build_service(llm_client):
    chat_session = ChatSession(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), user_id=uuid.uuid4()
    )
    repository = AsyncMock()
    repository.get_session.return_value = None
    repository.create_session.return_value = chat_session
    repository.recent_messages.return_value = []

    rag_service = AsyncMock()
    rag_service.retrieve.return_value = []

    model_router = AsyncMock()
    model_router.choose_model.return_value = CHEAP_MODEL

    service = ChatService(
        repository=repository,
        rag_service=rag_service,
        llm_client=llm_client,
        model_router=model_router,
        session=AsyncMock(),
    )
    return service, repository, chat_session


@pytest.mark.asyncio
async def test_send_message_raises_on_empty_response_and_does_not_persist_it():
    llm_client = AsyncMock()
    llm_client.generate.return_value = _empty_response()
    service, repository, chat_session = _build_service(llm_client)

    with pytest.raises(EmptyResponseError):
        await service.send_message(
            organization_id=chat_session.organization_id,
            current_user=_current_user(),
            message="hola",
            session_id=None,
            bearer_token="t",
        )

    # Solo el mensaje USER (de _prepare_turn) -- nunca se llega a
    # persistir un ChatMessage ASSISTANT vacio.
    assert repository.add_message.await_count == 1
    service.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_succeeds_with_real_content():
    llm_client = AsyncMock()
    llm_client.generate.return_value = LLMResponse(
        content="Hola, en que te puedo ayudar?",
        model=CHEAP_MODEL,
        provider="litellm",
        tokens_used=TokenUsage(input_tokens=5, output_tokens=5),
        latency_ms=1.0,
        finish_reason="stop",
    )
    service, repository, chat_session = _build_service(llm_client)
    repository.add_message.return_value = MagicMock(id=uuid.uuid4(), content="Hola, en que te puedo ayudar?")

    result = await service.send_message(
        organization_id=chat_session.organization_id,
        current_user=_current_user(),
        message="hola",
        session_id=None,
        bearer_token="t",
    )

    assert result.content == "Hola, en que te puedo ayudar?"
    assert repository.add_message.await_count == 2  # USER + ASSISTANT
    service.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_stream_yields_error_event_on_empty_response():
    async def fake_generate_stream(request, collector=None):
        if collector is not None:
            collector.final_response = _empty_response()
        return
        yield  # pragma: no cover -- nunca se llega, solo hace de esto un generador

    llm_client = AsyncMock()
    llm_client.generate_stream = fake_generate_stream
    service, repository, chat_session = _build_service(llm_client)

    events = [
        event
        async for event in service.send_message_stream(
            organization_id=chat_session.organization_id,
            current_user=_current_user(),
            message="hola",
            session_id=None,
            bearer_token="t",
        )
    ]

    assert [e["event"] for e in events] == ["start", "error"]
    # Solo el mensaje USER -- nunca se persiste un ChatMessage ASSISTANT vacio.
    assert repository.add_message.await_count == 1
    service.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_stream_succeeds_with_real_content():
    async def fake_generate_stream(request, collector=None):
        for piece in ["Hola", ", ", "en que te ayudo?"]:
            yield piece
        if collector is not None:
            collector.final_response = LLMResponse(
                content="Hola, en que te ayudo?",
                model=CHEAP_MODEL,
                provider="litellm",
                tokens_used=TokenUsage(input_tokens=5, output_tokens=5),
                latency_ms=1.0,
                finish_reason="stop",
            )

    llm_client = AsyncMock()
    llm_client.generate_stream = fake_generate_stream
    service, repository, chat_session = _build_service(llm_client)
    repository.add_message.return_value = MagicMock(
        id=uuid.uuid4(), created_at=datetime.now(timezone.utc)
    )

    events = [
        event
        async for event in service.send_message_stream(
            organization_id=chat_session.organization_id,
            current_user=_current_user(),
            message="hola",
            session_id=None,
            bearer_token="t",
        )
    ]

    assert [e["event"] for e in events] == ["start", "delta", "delta", "delta", "done"]
    service.session.commit.assert_awaited_once()
