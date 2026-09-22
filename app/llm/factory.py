from app.llm.client import LLMClient
from app.llm.litellmprovider.litellm_provider import LiteLLMProvider

def get_default_llm_client(tenant_virtual_key: str) -> LLMClient:
    """La app siempre habla con el gateway LiteLLM, nunca directo
    con un provider. El orden de fallback y qué modelo usar vive en
    litellm_config.yaml, no acá.

    tenant_virtual_key: SIEMPRE requerida -- la del tenant, o la de
    emergencia si el tenant no tiene la suya. Esta función no sabe
    nada de organizaciones ni de cifrado -- resolver cuál usar (y
    descifrarla si corresponde) es responsabilidad del caller (ver
    app.organizations.dependencies.get_tenant_llm_client). No tiene
    default a propósito: no existe un caso legítimo de construir un
    LiteLLMProvider sin una virtual key concreta."""
    return LLMClient(LiteLLMProvider(virtual_key=tenant_virtual_key))
