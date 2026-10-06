"""Cliente de embeddings contra el gateway LiteLLM.

Mismo patron que LiteLLMProvider (app.llm.litellmprovider.litellm_provider):
AsyncOpenAI apuntando al proxy, autenticado con la virtual key del
tenant. Deliberadamente NO se mete dentro de la interfaz LLMProvider
(pensada para generate/generate_stream de chat) -- /embeddings es un
endpoint OpenAI-compatible distinto de /chat/completions, con su
propia forma de request/response, y forzarlo dentro del contrato de
chat hubiera sido mas confuso que tener una clase chica separada.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.core.config import settings

# model_name tal como esta registrado en litellm_config.yaml.
EMBEDDING_MODEL = "gemini-embedding"

# Debe coincidir con EMBEDDING_DIM en app.rag.models y en la migracion
# e1a2b3c4d5f6_add_rag_kb_tables.
EMBEDDING_DIMENSIONS = 768


class EmbeddingClient:
    def __init__(self, virtual_key: str) -> None:
        """virtual_key: la misma virtual key de tenant (o de
        emergencia) que usa LiteLLMProvider para chat -- ver
        app.organizations.dependencies.resolve_tenant_virtual_key.
        Comparte presupuesto/aislamiento por tenant con el trafico de
        chat, a proposito: ambos pasan por el mismo gateway."""
        self.client = AsyncOpenAI(
            base_url=settings.litellm_base_url.rstrip("/"),
            api_key=virtual_key,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Un vector por texto de entrada, en el mismo orden que
        `texts`. gemini-embedding-001 (no gemini-embedding-2) da
        exactamente esta semantica 1 string -> 1 vector -- ver el
        comentario en litellm_config.yaml sobre por que no es el
        modelo de embeddings mas nuevo."""
        if not texts:
            return []

        response = await self.client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
            dimensions=EMBEDDING_DIMENSIONS,
        )
        # La API OpenAI-compatible preserva el orden input -> data,
        # pero no confiamos en eso a ciegas -- cada item trae su
        # propio .index, y ordenar explicitamente por el es gratis.
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]
