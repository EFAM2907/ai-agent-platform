import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.providers.openai_provider import OpenAIProvider
from app.llm.schemas import LLMRequest, Message, Role

@pytest.mark.asyncio
async def test_openai_provider_mapping():
    # Mock de la respuesta del SDK de OpenAI (basado en lo que espera OpenAIProvider)
    mock_response = MagicMock()
    mock_response.output_text = "Respuesta de OpenAI"
    mock_response.model = "gpt-4o"
    mock_response.usage.input_tokens = 15
    mock_response.usage.output_tokens = 25
    
    mock_output_item = MagicMock()
    mock_output_item.finish_reason = "stop"
    mock_response.output = [mock_output_item]

    # Patch para evitar instanciar el cliente real
    with patch("app.llm.providers.openai_provider.AsyncOpenAI") as mock_openai_class:
        mock_client = mock_openai_class.return_value
        mock_client.responses.create = AsyncMock(return_value=mock_response)
        
        provider = OpenAIProvider()
        
        request = LLMRequest(
            messages=[Message(role=Role.USER, content="Hola")],
            model="gpt-4o"
        )
        
        response = await provider.generate(request)
        
        # Verificar mapeo de salida
        assert response.content == "Respuesta de OpenAI"
        assert response.provider == "openai"
        assert response.tokens_used.input_tokens == 15
        assert response.tokens_used.output_tokens == 25
        assert response.finish_reason == "stop"
        
        # Verificar mapeo de entrada
        mock_client.responses.create.assert_called_once()
        args, kwargs = mock_client.responses.create.call_args
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["input"] == [{"role": "user", "content": "Hola"}]
