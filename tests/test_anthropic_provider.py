import pytest
import anthropic
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.schemas import LLMRequest, Message, Role
from app.llm.errors import RateLimitError, ProviderError
from app.llm.errors import TimeoutError_ as LLMTimeoutError

@pytest.mark.asyncio
async def test_anthropic_provider_mapping():
    # Mock de la respuesta del SDK de Anthropic
    mock_response = MagicMock()
    
    # Bloque de texto
    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = "Respuesta de Anthropic"
    mock_response.content = [mock_content_block]
    
    mock_response.model = "claude-3-opus"
    mock_response.usage.input_tokens = 10
    mock_response.usage.output_tokens = 20
    mock_response.stop_reason = "end_turn"

    # Patch para evitar instanciar el cliente real
    with patch("app.llm.providers.anthropic_provider.AsyncAnthropic") as mock_anthropic_class:
        mock_client = mock_anthropic_class.return_value
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        
        provider = AnthropicProvider()
        
        request = LLMRequest(
            messages=[
                Message(role=Role.SYSTEM, content="Eres un bot"),
                Message(role=Role.USER, content="Hola")
            ],
            model="claude-3-opus"
        )
        
        response = await provider.generate(request)
        
        # Verificar mapeo de salida
        assert response.content == "Respuesta de Anthropic"
        assert response.provider == "anthropic"
        assert response.tokens_used.input_tokens == 10
        assert response.tokens_used.output_tokens == 20
        assert response.finish_reason == "stop"
        
        # Verificar mapeo de entrada
        mock_client.messages.create.assert_called_once()
        args, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == "claude-3-opus"
        assert kwargs["system"] == "Eres un bot"
        assert kwargs["messages"] == [{"role": "user", "content": "Hola"}]

@pytest.mark.asyncio
async def test_anthropic_provider_rate_limit():
    with patch("app.llm.providers.anthropic_provider.AsyncAnthropic") as mock_anthropic_class:
        mock_client = mock_anthropic_class.return_value
        # Mock de la excepción
        exc = anthropic.RateLimitError(
            message="Rate limit",
            response=MagicMock(),
            body={}
        )
        mock_client.messages.create = AsyncMock(side_effect=exc)
        
        provider = AnthropicProvider()
        request = LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model="claude-3")
        
        with pytest.raises(RateLimitError):
            await provider.generate(request)

@pytest.mark.asyncio
async def test_anthropic_provider_timeout():
    with patch("app.llm.providers.anthropic_provider.AsyncAnthropic") as mock_anthropic_class:
        mock_client = mock_anthropic_class.return_value
        exc = anthropic.APITimeoutError(request=MagicMock())
        mock_client.messages.create = AsyncMock(side_effect=exc)
        
        provider = AnthropicProvider()
        request = LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model="claude-3")
        
        with pytest.raises(LLMTimeoutError):
            await provider.generate(request)

@pytest.mark.asyncio
async def test_anthropic_provider_generic_error():
    with patch("app.llm.providers.anthropic_provider.AsyncAnthropic") as mock_anthropic_class:
        mock_client = mock_anthropic_class.return_value
        exc = anthropic.APIStatusError(message="Error", response=MagicMock(), body={})
        mock_client.messages.create = AsyncMock(side_effect=exc)
        
        provider = AnthropicProvider()
        request = LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model="claude-3")
        
        with pytest.raises(ProviderError):
            await provider.generate(request)
