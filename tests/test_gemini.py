import pytest

from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.schemas import LLMRequest, Message, Role


@pytest.mark.asyncio
async def test_gemini_generate():
    provider = GeminiProvider()

    request = LLMRequest(
        messages=[
            Message(
                role=Role.USER,
                content="Responde solamente: Hola desde Gemini",
            )
        ],
        model="gemini-3.6-flash",
        temperature=0.0,
    )

    response = await provider.generate(request)

    print("\nRESPUESTA:")
    print(response.content)

    print("\nPROVIDER:")
    print(response.provider)

    print("\nMODELO:")
    print(response.model)

    print("\nTOKENS:")
    print(response.tokens_used)

    assert response.content
    assert response.provider == "gemini"