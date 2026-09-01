import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from google.api_core import exceptions as google_exceptions
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.schemas import LLMRequest, Message, Role
from app.llm.errors import RateLimitError, ProviderError
from app.llm.errors import TimeoutError_ as LLMTimeoutError

@pytest.mark.asyncio
async def test_gemini_provider_mapping():
    # Mock de la respuesta del SDK de Gemini
    mock_response = MagicMock()
    mock_response.text = "Respuesta de Gemini"
    
    mock_response.usage_metadata.prompt_token_count = 12
    mock_response.usage_metadata.candidates_token_count = 22
    
    mock_candidate = MagicMock()
    mock_candidate.finish_reason = 1 # STOP
    mock_response.candidates = [mock_candidate]

    # Patch de genai.GenerativeModel y genai.configure
    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel") as mock_model_class:
        
        mock_model = mock_model_class.return_value
        mock_model.generate_content_async = AsyncMock(return_value=mock_response)
        
        provider = GeminiProvider()
        
        request = LLMRequest(
            messages=[
                Message(role=Role.SYSTEM, content="Eres un bot de Google"),
                Message(role=Role.USER, content="Hola")
            ],
            model="gemini-1.5-pro"
        )
        
        response = await provider.generate(request)
        
        # Verificar mapeo de salida
        assert response.content == "Respuesta de Gemini"
        assert response.provider == "gemini"
        assert response.tokens_used.input_tokens == 12
        assert response.tokens_used.output_tokens == 22
        assert response.finish_reason == "stop"
        
        # Verificar mapeo de entrada
        mock_model_class.assert_called_once_with(
            model_name="gemini-1.5-pro",
            system_instruction="Eres un bot de Google"
        )
        mock_model.generate_content_async.assert_called_once()
        args, kwargs = mock_model.generate_content_async.call_args
        assert kwargs["contents"] == [{"role": "user", "parts": ["Hola"]}]

@pytest.mark.asyncio
async def test_gemini_provider_rate_limit():
    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel") as mock_model_class:
        
        mock_model = mock_model_class.return_value
        exc = google_exceptions.ResourceExhausted("Quota exceeded")
        mock_model.generate_content_async = AsyncMock(side_effect=exc)
        
        provider = GeminiProvider()
        request = LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model="gemini-1.5")
        
        with pytest.raises(RateLimitError):
            await provider.generate(request)

@pytest.mark.asyncio
async def test_gemini_provider_timeout():
    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel") as mock_model_class:
        
        mock_model = mock_model_class.return_value
        exc = google_exceptions.DeadlineExceeded("Timeout")
        mock_model.generate_content_async = AsyncMock(side_effect=exc)
        
        provider = GeminiProvider()
        request = LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model="gemini-1.5")
        
        with pytest.raises(LLMTimeoutError):
            await provider.generate(request)

@pytest.mark.asyncio
async def test_gemini_provider_generic_error():
    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel") as mock_model_class:
        
        mock_model = mock_model_class.return_value
        exc = google_exceptions.InternalServerError("Internal Error")
        mock_model.generate_content_async = AsyncMock(side_effect=exc)
        
        provider = GeminiProvider()
        request = LLMRequest(messages=[Message(role=Role.USER, content="Hola")], model="gemini-1.5")
        
        with pytest.raises(ProviderError):
            await provider.generate(request)
