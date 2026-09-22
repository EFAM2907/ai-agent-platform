"""El prompt del agente de chat debe responder en el idioma del
mensaje del usuario -- ver la version 5 de support_chat_agent. Cubre
solo que la regla este presente y sea la version que ChatService
carga de verdad; el comportamiento real del modelo (que efectivamente
conteste en ingles/español) es responsabilidad del LLM, no algo que un
test unitario pueda verificar sin llamarlo de verdad."""

from app.chat.service import _SYSTEM_PROMPT_NAME, _SYSTEM_PROMPT_VERSION
from app.llm.prompts.loader import PromptLoader


def test_chat_service_pins_the_language_aware_prompt_version():
    assert _SYSTEM_PROMPT_VERSION == 5


def test_v5_prompt_instructs_replying_in_the_users_language():
    prompt = PromptLoader().load_version(_SYSTEM_PROMPT_NAME, _SYSTEM_PROMPT_VERSION)

    # El YAML es un bloque literal con saltos de linea propios -- se
    # normaliza el espacio en blanco para no depender de donde cae el
    # wrap de una version a otra.
    rendered = " ".join(prompt.render(kb_context="(contexto de prueba)").split())

    assert "Always reply in the same language the user's current message is written in" in rendered
    assert "default to Spanish" in rendered


def test_load_latest_resolves_to_v5():
    prompt = PromptLoader().load_latest(_SYSTEM_PROMPT_NAME)

    assert prompt.version == 5
