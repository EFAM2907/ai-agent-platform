from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest

from app.llm.errors import ContentFilterError, InvalidRequestError, ProviderError
from app.llm.litellmprovider.litellm_provider import LiteLLMProvider
from app.llm.schemas import LLMRequest, Message, Role, ToolCall, ToolDefinition
from app.llm.streaming import StreamUsageCollector


def _bad_request_error(message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "http://litellm.test/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return openai.BadRequestError(message=message, response=response, body=None)


@pytest.mark.asyncio
async def test_litellm_provider_maps_chat_completion_response():
    mock_response = MagicMock()
    mock_response.model = "gemini-3.6-flash"
    mock_response.usage.prompt_tokens = 12
    mock_response.usage.completion_tokens = 34
    mock_choice = MagicMock()
    mock_choice.message.content = "Respuesta desde el gateway"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        response = await provider.generate(
            LLMRequest(
                messages=[Message(role=Role.SYSTEM, content="Ayuda"), Message(role=Role.USER, content="Hola")],
                model="gemini-3.6-flash",
                temperature=0.2,
                max_tokens=100,
            )
        )

    assert response.content == "Respuesta desde el gateway"
    assert response.model == "gemini-3.6-flash"
    assert response.provider == "litellm"
    assert response.tokens_used.input_tokens == 12
    assert response.tokens_used.output_tokens == 34
    assert response.finish_reason == "stop"
    client.chat.completions.create.assert_awaited_once_with(
        model="gemini-3.6-flash",
        messages=[
            {"role": "system", "content": "Ayuda"},
            {"role": "user", "content": "Hola"},
        ],
        temperature=0.2,
        max_tokens=100,
        tools=None,
        extra_body=None,
    )


@pytest.mark.asyncio
async def test_litellm_provider_sends_tools_payload_when_request_has_tools():
    mock_response = MagicMock()
    mock_response.model = "gemini-3.6-flash"
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 5
    mock_choice = MagicMock()
    mock_choice.message.content = "ok"
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    tool = ToolDefinition(
        name="get_current_datetime",
        description="Devuelve la hora actual",
        parameters={"type": "object", "properties": {}, "required": []},
    )

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        await provider.generate(
            LLMRequest(
                messages=[Message(role=Role.USER, content="Que hora es?")],
                model="gemini-3.6-flash",
                tools=[tool],
            )
        )

    sent_tools = client.chat.completions.create.call_args.kwargs["tools"]
    assert sent_tools == [
        {
            "type": "function",
            "function": {
                "name": "get_current_datetime",
                "description": "Devuelve la hora actual",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ]


@pytest.mark.asyncio
async def test_litellm_provider_parses_tool_calls_from_response():
    """finish_reason="tool_calls" con content vacio es una respuesta
    valida (el LLM no tiene nada que "decir" todavia) -- no debe
    lanzar InvalidResponseError, y ToolCall.arguments debe llegar ya
    parseado como dict, no como el string JSON crudo del SDK."""
    mock_response = MagicMock()
    mock_response.model = "gemini-3.6-flash"
    mock_response.usage.prompt_tokens = 20
    mock_response.usage.completion_tokens = 8
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_abc123"
    mock_tool_call.function.name = "get_shipment_status"
    mock_tool_call.function.arguments = '{"tracking_number": "854321"}'
    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_choice.message.tool_calls = [mock_tool_call]
    mock_choice.finish_reason = "tool_calls"
    mock_response.choices = [mock_choice]

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=mock_response)
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        response = await provider.generate(
            LLMRequest(
                messages=[Message(role=Role.USER, content="Donde esta mi envio 854321?")],
                model="gemini-3.6-flash",
            )
        )

    assert response.finish_reason == "tool_calls"
    assert response.content == ""
    assert response.tool_calls == [
        ToolCall(
            id="call_abc123",
            name="get_shipment_status",
            arguments={"tracking_number": "854321"},
        )
    ]


def test_litellm_provider_does_not_claim_portable_structured_output():
    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI"):
        assert LiteLLMProvider(virtual_key="sk-test-virtual-key").supports_structured_output() is False


def test_litellm_provider_claims_streaming_support():
    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI"):
        assert LiteLLMProvider(virtual_key="sk-test-virtual-key").supports_streaming() is True


def _make_delta_chunk(content: str | None, finish_reason: str | None = None):
    chunk = MagicMock()
    chunk.model = "gemini-3.6-flash"
    chunk.usage = None
    choice = MagicMock()
    choice.delta.content = content
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    return chunk


def _make_usage_chunk(prompt_tokens: int, completion_tokens: int, *, with_stale_choice: bool = False):
    """El bug conocido de LiteLLM: el chunk de usage a veces repite el
    ultimo choice (con delta vacio) en vez de llegar con choices=[]."""
    chunk = MagicMock()
    chunk.model = "gemini-3.6-flash"
    chunk.usage.prompt_tokens = prompt_tokens
    chunk.usage.completion_tokens = completion_tokens
    if with_stale_choice:
        choice = MagicMock()
        choice.delta.content = None
        choice.finish_reason = "stop"
        chunk.choices = [choice]
    else:
        chunk.choices = []
    return chunk


def _make_tool_call_delta_chunk(fragments: list[dict], finish_reason: str | None = None):
    """fragments: uno por tool_call delta en ESTE chunk, con las
    claves que traigan (index siempre, id/name/arguments opcionales
    segun si es el primer chunk de ese indice o uno posterior) -- asi
    de fragmentado llega function calling en streaming real."""
    chunk = MagicMock()
    chunk.model = "gemini-3.6-flash"
    chunk.usage = None
    choice = MagicMock()
    choice.delta.content = None
    tool_call_deltas = []
    for frag in fragments:
        tc_delta = MagicMock()
        tc_delta.index = frag["index"]
        tc_delta.id = frag.get("id")
        if "name" in frag or "arguments" in frag:
            tc_delta.function.name = frag.get("name")
            tc_delta.function.arguments = frag.get("arguments")
        else:
            tc_delta.function = None
        tool_call_deltas.append(tc_delta)
    choice.delta.tool_calls = tool_call_deltas
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    return chunk


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_litellm_provider_generate_stream_yields_deltas_and_fills_collector():
    chunks = [
        _make_delta_chunk("Hola"),
        _make_delta_chunk(" mundo", finish_reason="stop"),
        _make_usage_chunk(12, 34, with_stale_choice=True),
    ]

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        collector = StreamUsageCollector()
        deltas = [
            delta
            async for delta in provider.generate_stream(
                LLMRequest(
                    messages=[Message(role=Role.USER, content="Hola")],
                    model="gemini-3.6-flash",
                ),
                collector=collector,
            )
        ]

    assert deltas == ["Hola", " mundo"]
    assert collector.final_response is not None
    assert collector.final_response.content == "Hola mundo"
    assert collector.final_response.tokens_used.input_tokens == 12
    assert collector.final_response.tokens_used.output_tokens == 34
    assert collector.final_response.finish_reason == "stop"
    client.chat.completions.create.assert_awaited_once_with(
        model="gemini-3.6-flash",
        messages=[{"role": "user", "content": "Hola"}],
        temperature=0.0,
        max_tokens=None,
        tools=None,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=None,
    )


@pytest.mark.asyncio
async def test_litellm_provider_generate_stream_without_usage_chunk_leaves_collector_empty():
    chunks = [_make_delta_chunk("Hola", finish_reason="stop")]

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        collector = StreamUsageCollector()
        deltas = [
            delta
            async for delta in provider.generate_stream(
                LLMRequest(
                    messages=[Message(role=Role.USER, content="Hola")],
                    model="gemini-3.6-flash",
                ),
                collector=collector,
            )
        ]

    assert deltas == ["Hola"]
    assert collector.final_response is None


@pytest.mark.asyncio
async def test_generate_stream_reassembles_fragmented_tool_call_and_yields_no_text():
    """function calling en streaming llega fragmentado: id y function.name
    solo en el primer chunk de ese indice, function.arguments de a
    pedazos en los siguientes -- hay que acumularlos y ensamblar el
    ToolCall completo recien al final, sin yieldear nada como texto."""
    chunks = [
        _make_tool_call_delta_chunk([
            {"index": 0, "id": "call_abc", "name": "get_shipment_status", "arguments": '{"track'}
        ]),
        _make_tool_call_delta_chunk([
            {"index": 0, "arguments": 'ing_number": "854321"}'}
        ], finish_reason="tool_calls"),
        _make_usage_chunk(30, 12),
    ]

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        collector = StreamUsageCollector()
        tool = ToolDefinition(
            name="get_shipment_status",
            description="Consulta el estado de un envio",
            parameters={"type": "object", "properties": {"tracking_number": {"type": "string"}}},
        )
        deltas = [
            delta
            async for delta in provider.generate_stream(
                LLMRequest(
                    messages=[Message(role=Role.USER, content="Donde esta mi envio 854321?")],
                    model="gemini-3.6-flash",
                    tools=[tool],
                ),
                collector=collector,
            )
        ]

    assert deltas == []
    assert collector.final_response is not None
    assert collector.final_response.finish_reason == "tool_calls"
    assert collector.final_response.tool_calls == [
        ToolCall(
            id="call_abc",
            name="get_shipment_status",
            arguments={"tracking_number": "854321"},
        )
    ]


@pytest.mark.asyncio
async def test_generate_stream_sends_tools_payload_to_the_gateway():
    tool = ToolDefinition(
        name="list_users",
        description="Lista usuarios",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    chunks = [_make_delta_chunk("hola", finish_reason="stop")]

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        async for _ in provider.generate_stream(
            LLMRequest(
                messages=[Message(role=Role.USER, content="hola")],
                model="gemini-3.6-flash",
                tools=[tool],
            )
        ):
            pass

    sent_tools = client.chat.completions.create.call_args.kwargs["tools"]
    assert sent_tools == [
        {
            "type": "function",
            "function": {
                "name": "list_users",
                "description": "Lista usuarios",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ]


@pytest.mark.asyncio
async def test_bad_request_error_raises_invalid_request_error_not_provider_error():
    """Distincion real, no cosmetica: ProviderError es para 5xx/fallas
    de transporte, InvalidRequestError para 400s (parametros
    invalidos, modelo sin credito). Antes de que LLMClient dejara de
    reintentar por su cuenta (ver el docstring de app.llm.client),
    confundir las dos convertia un 400 -- que por definicion nunca
    cambia con un reintento -- en varios reintentos inutiles con
    backoff exponencial."""
    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(
            side_effect=_bad_request_error("Your credit balance is too low")
        )
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        with pytest.raises(InvalidRequestError) as exc_info:
            await provider.generate(
                LLMRequest(
                    messages=[Message(role=Role.USER, content="Hola")],
                    model="claude-sonnet-5",
                )
            )

    assert not isinstance(exc_info.value, ProviderError)
    assert "credit balance" in str(exc_info.value)


@pytest.mark.asyncio
async def test_bad_request_error_for_content_filter_still_raises_content_filter_error():
    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = client_class.return_value
        client.chat.completions.create = AsyncMock(
            side_effect=_bad_request_error("Blocked by content_filter policy")
        )
        provider = LiteLLMProvider(virtual_key="sk-test-virtual-key")

        with pytest.raises(ContentFilterError):
            await provider.generate(
                LLMRequest(
                    messages=[Message(role=Role.USER, content="Hola")],
                    model="gemini-3.6-flash",
                )
            )


def test_provider_configures_a_bounded_request_timeout():
    """Sin timeout explicito, el SDK de OpenAI espera hasta 10 minutos
    por defecto -- si el gateway o el proveedor detras se cuelgan, un
    solo request de chat podria colgar mucho mas de lo que cualquier
    usuario va a tolerar."""
    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        LiteLLMProvider(virtual_key="sk-test-virtual-key")

    assert client_class.call_args.kwargs["timeout"] == LiteLLMProvider._REQUEST_TIMEOUT_SECONDS
