"""LLMClient ya no reintiene errores tecnicos del proveedor -- eso es
responsabilidad de LiteLLM (router_settings.num_retries en
litellm_config.yaml). Ver el docstring de app.llm.client para el
incidente real (un 400 sin credito de Anthropic tardando ~170s en
fallar) que causo remover ese retry duplicado.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.llm.client import LLMClient
from app.llm.errors import InvalidRequestError, ProviderError, RateLimitError
from app.llm.schemas import LLMRequest, LLMResponse, Message, Role, TokenUsage

_MODEL = "gemini-3.6-flash"


def _request() -> LLMRequest:
    return LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model=_MODEL)


def _response() -> LLMResponse:
    return LLMResponse(
        content="ok",
        model=_MODEL,
        provider="litellm",
        tokens_used=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_generate_calls_provider_exactly_once_on_success():
    provider = AsyncMock()
    provider.generate.return_value = _response()
    client = LLMClient(provider)

    response = await client.generate(_request())

    assert response.content == "ok"
    assert provider.generate.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    InvalidRequestError("credit balance too low", provider="litellm"),
    ProviderError("500 upstream", provider="litellm"),
    RateLimitError("429", provider="litellm", retry_after=30.0),
])
async def test_generate_never_retries_any_provider_error(error):
    """Ni siquiera un RateLimitError con retry_after -- LiteLLM ya
    hizo (o va a hacer) su propio retry antes de devolvernos un error
    final; reintentar de nuevo acá era exactamente la duplicacion que
    causo el incidente de latencia."""
    provider = AsyncMock()
    provider.generate.side_effect = error

    client = LLMClient(provider)

    with pytest.raises(type(error)):
        await client.generate(_request())

    assert provider.generate.await_count == 1


@pytest.mark.asyncio
async def test_generate_stream_forwards_collector_to_provider():
    from app.llm.streaming import StreamUsageCollector

    async def fake_stream(request, collector=None):
        if collector is not None:
            collector.final_response = _response()
        yield "hola"

    provider = AsyncMock()
    provider.supports_streaming = MagicMock(return_value=True)
    provider.generate_stream = fake_stream
    client = LLMClient(provider)

    collector = StreamUsageCollector()
    deltas = [d async for d in client.generate_stream(_request(), collector=collector)]

    assert deltas == ["hola"]
    assert collector.final_response is not None
    assert collector.final_response.content == "ok"
