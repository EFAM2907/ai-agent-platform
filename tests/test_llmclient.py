import pytest

from app.llm.client import LLMClient
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.schemas import LLMRequest, Message, Role


@pytest.mark.asyncio
async def test_llm_client_with_gemini():
    provider = GeminiProvider()

    client = LLMClient(
        provider=provider
    )

    request = LLMRequest(
        messages=[
            Message(
                role=Role.USER,
                content="Responde solamente: Hola desde LLMClient",
            )
        ],
        model="gemini-3.6-flash",
        temperature=0.0,
    )

    response = await client.generate(request)

    print("\nRESPUESTA:")
    print(response.content)

    print("\nPROVIDER:")
    print(response.provider)

    assert response.content
    assert response.provider == "gemini"