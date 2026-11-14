"""Observer y eventos del tool loop: lo que alimenta la trazabilidad de
cada turno (tokens por llamada al modelo, duracion/exito de cada tool) y
el aviso en vivo "ejecutando herramienta" de la interfaz."""

from unittest.mock import AsyncMock

import pytest

from app.llm.schemas import ToolCall
from app.llm.tools import ToolEvent, run_streaming_tool_loop, run_tool_loop
from tests.test_tool_loop import (
    _base_request,
    _datetime_tool,
    _FakeStreamingClient,
    _response,
)


class RecordingObserver:
    def __init__(self) -> None:
        self.llm_responses = []
        self.tool_calls = []

    def on_llm_response(self, response) -> None:
        self.llm_responses.append(response)

    def on_tool_call(self, name, arguments, *, ok, duration_ms, error, result_chars) -> None:
        self.tool_calls.append(
            {"name": name, "arguments": arguments, "ok": ok, "error": error,
             "duration_ms": duration_ms, "result_chars": result_chars}
        )


def _tool_call(arguments=None) -> ToolCall:
    return ToolCall(
        id="call_1", name="get_shipment_status", arguments=arguments or {"tracking_number": "854321"}
    )


@pytest.mark.asyncio
async def test_observer_sees_every_llm_call_and_each_tool_execution():
    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[_tool_call()]),
        _response(content="Listo", finish_reason="stop"),
    ]
    observer = RecordingObserver()

    await run_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()], observer=observer)

    assert len(observer.llm_responses) == 2
    [call] = observer.tool_calls
    assert call["name"] == "get_shipment_status"
    assert call["arguments"] == {"tracking_number": "854321"}
    assert call["ok"] is True and call["error"] is None
    assert call["duration_ms"] >= 0 and call["result_chars"] > 0


@pytest.mark.asyncio
async def test_failed_tool_is_reported_as_not_ok_with_its_error():
    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[_tool_call()]),
        _response(content="No pude", finish_reason="stop"),
    ]
    boom = AsyncMock(side_effect=RuntimeError("la base de datos no responde"))
    observer = RecordingObserver()

    await run_tool_loop(llm_client, _base_request(), tools=[_datetime_tool(boom)], observer=observer)

    [call] = observer.tool_calls
    assert call["ok"] is False
    assert "la base de datos no responde" in call["error"]


@pytest.mark.asyncio
async def test_business_error_dict_counts_as_not_ok():
    # El contrato de las tools MCP: un 403/404 de la API llega como {"error", "status_code"}.
    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[_tool_call()]),
        _response(content="No tiene permiso", finish_reason="stop"),
    ]
    forbidden = AsyncMock(return_value={"error": "Admin privileges required", "status_code": 403})
    observer = RecordingObserver()

    await run_tool_loop(llm_client, _base_request(), tools=[_datetime_tool(forbidden)], observer=observer)

    [call] = observer.tool_calls
    assert call["ok"] is False and call["error"] == "Admin privileges required"


@pytest.mark.asyncio
async def test_a_failing_observer_never_breaks_the_turn():
    class BrokenObserver:
        def on_llm_response(self, response):
            raise RuntimeError("observer roto")

        def on_tool_call(self, *args, **kwargs):
            raise RuntimeError("observer roto")

    llm_client = AsyncMock()
    llm_client.generate.side_effect = [
        _response(finish_reason="tool_calls", tool_calls=[_tool_call()]),
        _response(content="Respuesta", finish_reason="stop"),
    ]

    response = await run_tool_loop(
        llm_client, _base_request(), tools=[_datetime_tool()], observer=BrokenObserver()
    )

    assert response.content == "Respuesta"


@pytest.mark.asyncio
async def test_streaming_emits_tool_events_between_text_when_enabled():
    llm_client = _FakeStreamingClient([
        ([], _response(finish_reason="tool_calls", tool_calls=[_tool_call()])),
        (["Esta ", "en transito"], _response(content="Esta en transito", finish_reason="stop")),
    ])
    observer = RecordingObserver()

    items = [
        item
        async for item in run_streaming_tool_loop(
            llm_client, _base_request(), tools=[_datetime_tool()],
            observer=observer, emit_tool_events=True,
        )
    ]

    events = [i for i in items if isinstance(i, ToolEvent)]
    text = "".join(i for i in items if isinstance(i, str))
    assert [(e.name, e.phase, e.ok) for e in events] == [
        ("get_shipment_status", "start", None),
        ("get_shipment_status", "end", True),
    ]
    assert text == "Esta en transito"
    assert items.index(events[1]) < items.index("Esta ")  # las tools terminan antes del texto final
    assert len(observer.llm_responses) == 2 and len(observer.tool_calls) == 1


@pytest.mark.asyncio
async def test_streaming_does_not_emit_tool_events_by_default():
    llm_client = _FakeStreamingClient([
        ([], _response(finish_reason="tool_calls", tool_calls=[_tool_call()])),
        (["Listo"], _response(content="Listo", finish_reason="stop")),
    ])

    items = [i async for i in run_streaming_tool_loop(llm_client, _base_request(), tools=[_datetime_tool()])]

    assert all(isinstance(i, str) for i in items)
