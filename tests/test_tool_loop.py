import json
from unittest.mock import AsyncMock

import pytest

from app.llm.schemas import LLMRequest, LLMResponse, Message, Role, TokenUsage, ToolCall, ToolDefinition
from app.llm.tools import Tool, ToolLoopError, run_streaming_tool_loop, run_tool_loop

_MODEL = "gemini-3.6-flash"


def _response(
    content: str = "",
    finish_reason: str = "stop",
    tool_calls: list[ToolCall] | None = None,
) -> LLMResponse:
    return LLMResponse(
        content=content,
        model=_MODEL,
        provider="litellm",
        tokens_used=TokenUsage(input_tokens=10, output_tokens=5),
        latency_ms=5.0,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def _base_request() -> LLMRequest:
    return LLMRequest(
        messages=[Message(role=Role.USER, content="Donde esta mi envio 854321?")],
        model=_MODEL,
    )


def _datetime_tool(handler=None) -> Tool:
    return Tool(
        definition=ToolDefinition(
            name="get_shipment_status",
            description="Consulta el estado de un envio por numero de guia",
            parameters={
                "type": "object",
                "properties": {"tracking_number": {"type": "string"}},
                "required": ["tracking_number"],
            },
        ),
        handler=handler or AsyncMock(return_value={"status": "en transito"}),
    )


@pytest.mark.asyncio
async def test_returns_directly_when_no_tool_call_needed():
    llm_client = AsyncMock()
    llm_client.generate.return_value = _response(content="No necesito ninguna tool para esto")

    response = await run_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()])

    assert response.content == "No necesito ninguna tool para esto"
    assert llm_client.generate.await_count == 1


@pytest.mark.asyncio
async def test_executes_tool_and_returns_final_answer():
    handler = AsyncMock(return_value={"status": "en transito", "last_event_city": "Medellin"})
    tool = _datetime_tool(handler)

    tool_call = ToolCall(
        id="call_1", name="get_shipment_status", arguments={"tracking_number": "854321"}
    )
    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[tool_call]),
        _response(content="Tu envio esta en transito, ultimo evento en Medellin"),
    ]

    response = await run_tool_loop(llm_client, _base_request(), tools=[tool])

    assert response.content == "Tu envio esta en transito, ultimo evento en Medellin"
    assert llm_client.generate.await_count == 2
    handler.assert_awaited_once_with(tracking_number="854321")

    # La segunda llamada debe llevar el mensaje ASSISTANT con el
    # tool_call y el mensaje TOOL con el resultado, casados por id.
    second_request = llm_client.generate.await_args_list[1].args[0]
    assistant_msg, tool_msg = second_request.messages[-2:]
    assert assistant_msg.role == Role.ASSISTANT
    assert assistant_msg.tool_calls == [tool_call]
    assert tool_msg.role == Role.TOOL
    assert tool_msg.tool_call_id == "call_1"
    assert json.loads(tool_msg.content) == {
        "status": "en transito",
        "last_event_city": "Medellin",
    }


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error_result_instead_of_crashing():
    tool_call = ToolCall(id="call_1", name="tool_que_no_existe", arguments={})
    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[tool_call]),
        _response(content="Parece que no tengo esa herramienta disponible"),
    ]

    response = await run_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()])

    assert response.content == "Parece que no tengo esa herramienta disponible"
    second_request = llm_client.generate.await_args_list[1].args[0]
    tool_msg = second_request.messages[-1]
    assert "no existe" in json.loads(tool_msg.content)["error"]


@pytest.mark.asyncio
async def test_tool_handler_exception_becomes_error_result_not_a_crash():
    failing_handler = AsyncMock(side_effect=RuntimeError("la base de datos no responde"))
    tool = _datetime_tool(failing_handler)
    tool_call = ToolCall(
        id="call_1", name="get_shipment_status", arguments={"tracking_number": "854321"}
    )
    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[tool_call]),
        _response(content="Algo fallo consultando tu envio, intenta de nuevo"),
    ]

    response = await run_tool_loop(llm_client, _base_request(), tools=[tool])

    assert response.content == "Algo fallo consultando tu envio, intenta de nuevo"
    second_request = llm_client.generate.await_args_list[1].args[0]
    tool_msg = second_request.messages[-1]
    assert "la base de datos no responde" in json.loads(tool_msg.content)["error"]


@pytest.mark.asyncio
async def test_raises_tool_loop_error_after_max_iterations():
    tool_call = ToolCall(
        id="call_1", name="get_shipment_status", arguments={"tracking_number": "854321"}
    )
    llm_client = AsyncMock()
    # El modelo pide la misma tool para siempre -- nunca converge.
    llm_client.generate.return_value = _response(
        finish_reason="tool_calls", tool_calls=[tool_call]
    )

    with pytest.raises(ToolLoopError):
        await run_tool_loop(
            llm_client, _base_request(), tools=[_datetime_tool()], max_iterations=3
        )

    assert llm_client.generate.await_count == 3


class _FakeStreamingClient:
    """rounds: una entrada por vuelta esperada, (deltas, final_response).
    Cada llamada a generate_stream() consume la siguiente entrada en
    orden y guarda el request recibido en self.requests, para poder
    verificar que la siguiente vuelta llevo el turno de tools bien
    armado."""

    def __init__(self, rounds: list[tuple[list[str], LLMResponse | None]]):
        self._rounds = rounds
        self.requests: list[LLMRequest] = []

    async def generate_stream(self, request, collector=None):
        self.requests.append(request)
        deltas, final_response = self._rounds[len(self.requests) - 1]
        for delta in deltas:
            yield delta
        if collector is not None:
            collector.final_response = final_response


@pytest.mark.asyncio
async def test_streaming_returns_directly_when_no_tool_call_needed():
    llm_client = _FakeStreamingClient([
        (["No ", "necesito ", "tools"], _response(content="No necesito tools", finish_reason="stop")),
    ])

    deltas = [
        d async for d in run_streaming_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()])
    ]

    assert "".join(deltas) == "No necesito tools"
    assert len(llm_client.requests) == 1


@pytest.mark.asyncio
async def test_streaming_executes_tool_then_streams_final_answer():
    handler = AsyncMock(return_value={"status": "en transito", "last_event_city": "Medellin"})
    tool = _datetime_tool(handler)
    tool_call = ToolCall(id="call_1", name="get_shipment_status", arguments={"tracking_number": "854321"})

    llm_client = _FakeStreamingClient([
        ([], _response(finish_reason="tool_calls", tool_calls=[tool_call])),
        (["Tu ", "envio ", "esta en transito"], _response(content="Tu envio esta en transito", finish_reason="stop")),
    ])

    deltas = [d async for d in run_streaming_tool_loop(llm_client, _base_request(), tools=[tool])]

    assert "".join(deltas) == "Tu envio esta en transito"
    assert len(llm_client.requests) == 2
    handler.assert_awaited_once_with(tracking_number="854321")

    second_request = llm_client.requests[1]
    assistant_msg, tool_msg = second_request.messages[-2:]
    assert assistant_msg.role == Role.ASSISTANT
    assert assistant_msg.tool_calls == [tool_call]
    assert tool_msg.role == Role.TOOL
    assert tool_msg.tool_call_id == "call_1"
    assert json.loads(tool_msg.content) == {
        "status": "en transito",
        "last_event_city": "Medellin",
    }


@pytest.mark.asyncio
async def test_streaming_unknown_tool_name_returns_error_result_instead_of_crashing():
    tool_call = ToolCall(id="call_1", name="tool_que_no_existe", arguments={})
    llm_client = _FakeStreamingClient([
        ([], _response(finish_reason="tool_calls", tool_calls=[tool_call])),
        (["listo"], _response(content="listo", finish_reason="stop")),
    ])

    deltas = [d async for d in run_streaming_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()])]

    assert "".join(deltas) == "listo"
    tool_msg = llm_client.requests[1].messages[-1]
    assert "no existe" in json.loads(tool_msg.content)["error"]


@pytest.mark.asyncio
async def test_streaming_stops_when_collector_has_no_final_response():
    """Sin usage utilizable en el ultimo chunk (ver
    LiteLLMProvider._build_stream_final_response), collector.final_response
    queda en None -- no hay forma de saber si hubo tool_calls, asi que
    la vuelta se trata como final en vez de reintentar a ciegas."""
    llm_client = _FakeStreamingClient([
        (["texto ", "parcial"], None),
    ])

    deltas = [d async for d in run_streaming_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()])]

    assert "".join(deltas) == "texto parcial"
    assert len(llm_client.requests) == 1


@pytest.mark.asyncio
async def test_streaming_raises_tool_loop_error_after_max_iterations():
    tool_call = ToolCall(id="call_1", name="get_shipment_status", arguments={"tracking_number": "854321"})
    llm_client = _FakeStreamingClient([
        ([], _response(finish_reason="tool_calls", tool_calls=[tool_call])),
        ([], _response(finish_reason="tool_calls", tool_calls=[tool_call])),
        ([], _response(finish_reason="tool_calls", tool_calls=[tool_call])),
    ])

    with pytest.raises(ToolLoopError):
        async for _ in run_streaming_tool_loop(
            llm_client, _base_request(), tools=[_datetime_tool()], max_iterations=3
        ):
            pass

    assert len(llm_client.requests) == 3
