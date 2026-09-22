"""Tool-calling loop generico: dado un LLMRequest base y una lista de
Tools disponibles, corre el ciclo "el LLM pide llamar una tool -> el
backend la ejecuta -> el resultado vuelve al LLM -> sigue" hasta que
responda con texto en vez de tool_calls, o hasta un limite de vueltas.

Vive en app/llm/ (no en un dominio como app.chat) porque es
infraestructura generica sobre el protocolo del LLM, no logica de
negocio -- que tools existen y que hacen es responsabilidad de quien
llama a run_tool_loop(), este modulo no sabe nada de envios, clientes
ni reclamaciones.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.llm.client import LLMClient
from app.llm.schemas import LLMRequest, LLMResponse, Message, Role, ToolCall, ToolDefinition
from app.llm.streaming import StreamUsageCollector

logger = logging.getLogger(__name__)

# La funcion real que ejecuta una tool: recibe los argumentos que el
# LLM mando (ya parseados, como kwargs) y devuelve algo serializable a
# JSON. Async siempre -- una tool real va a tocar la base de datos o
# una API externa, nunca deberia bloquear el event loop.
ToolHandler = Callable[..., Awaitable[Any]]


class Tool(BaseModel):
    """Empareja lo que el LLM necesita ver (ToolDefinition: nombre,
    descripcion, JSON schema de argumentos) con la funcion Python real
    que la ejecuta -- son dos cosas distintas (protocolo vs. codigo)
    que el loop necesita juntas para poder despachar una llamada."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    definition: ToolDefinition
    handler: ToolHandler

    @property
    def name(self) -> str:
        return self.definition.name


class ToolLoopError(Exception):
    """El LLM siguio pidiendo tool_calls mas alla de max_iterations --
    nunca dejamos que esto se vuelva un loop infinito silencioso."""


async def run_tool_loop(
    llm_client: LLMClient,
    request: LLMRequest,
    tools: list[Tool],
    *,
    max_iterations: int = 3,
) -> LLMResponse:
    """Corre `request` contra `llm_client` con las `tools` dadas,
    ejecutando cada tool_call que el LLM pida y devolviendole el
    resultado, hasta que responda con una respuesta final
    (finish_reason != "tool_calls") o se agoten las vueltas.

    Mismo principio que el loop de reparacion de salida estructurada en
    LLMClient._ensure_structured_output: acotado a proposito, nunca
    reintenta indefinidamente. Si el LLM sigue pidiendo tools despues
    de max_iterations, se lanza ToolLoopError en vez de devolver una
    respuesta a medias -- el caller decide que hacer con eso (mostrarle
    un error al usuario, loguearlo), este modulo no lo esconde.

    request.tools se sobreescribe con las `tools` dadas en cada vuelta
    -- no hace falta que el caller lo setee de antemano en `request`."""
    tools_by_name = {tool.name: tool for tool in tools}
    tool_definitions = [tool.definition for tool in tools]
    messages = list(request.messages)

    for _iteration in range(max_iterations):
        current_request = request.model_copy(
            update={"messages": messages, "tools": tool_definitions}
        )
        response = await llm_client.generate(current_request)

        if response.finish_reason != "tool_calls" or not response.tool_calls:
            return response

        messages = await _append_tool_results(messages, tools_by_name, response)

    raise ToolLoopError(
        f"El modelo siguio pidiendo tool_calls despues de {max_iterations} vueltas"
    )


async def run_streaming_tool_loop(
    llm_client: LLMClient,
    request: LLMRequest,
    tools: list[Tool],
    *,
    max_iterations: int = 3,
) -> AsyncIterator[str]:
    """Como run_tool_loop, pero streameando el texto de cada vuelta a
    medida que el proveedor lo entrega en vez de esperar la respuesta
    completa -- para que una conversacion con tools disponibles no
    pierda el streaming token a token que sí tiene una sin tools.

    Cada vuelta usa llm_client.generate_stream() con un
    StreamUsageCollector para poder inspeccionar, una vez que ESA
    vuelta termina de streamear, si el LLM pidio tool_calls (ver
    LiteLLMProvider.generate_stream, que ensambla los tool_calls
    fragmentados del streaming). Una vuelta que solo pide tools
    tipicamente no trae texto que mostrar -- las tools se ejecutan
    entre vueltas, igual que en run_tool_loop, y recien la vuelta que
    responde con texto final se streamea de verdad al usuario.

    Si el ultimo chunk del proveedor no trajo usage utilizable,
    collector.final_response queda en None (ver
    LiteLLMProvider._build_stream_final_response) -- ahi no hay forma
    de saber si hubo tool_calls, así que esta vuelta se trata como la
    final: ya se le mostró al usuario el texto que haya llegado, y
    reintentar a ciegas podría duplicarlo."""
    tools_by_name = {tool.name: tool for tool in tools}
    tool_definitions = [tool.definition for tool in tools]
    messages = list(request.messages)

    for _iteration in range(max_iterations):
        current_request = request.model_copy(
            update={"messages": messages, "tools": tool_definitions}
        )
        collector = StreamUsageCollector()
        async for delta in llm_client.generate_stream(current_request, collector=collector):
            yield delta

        response = collector.final_response
        if response is None:
            return
        if response.finish_reason != "tool_calls" or not response.tool_calls:
            return

        messages = await _append_tool_results(messages, tools_by_name, response)

    raise ToolLoopError(
        f"El modelo siguio pidiendo tool_calls despues de {max_iterations} vueltas"
    )


async def _append_tool_results(
    messages: list[Message], tools_by_name: dict[str, Tool], response: LLMResponse
) -> list[Message]:
    """Ejecuta cada tool_call de `response` y devuelve `messages` con
    el mensaje ASSISTANT (con los tool_calls pedidos) y un mensaje TOOL
    por resultado agregados -- compartido entre run_tool_loop y
    run_streaming_tool_loop para que ninguno de los dos pueda
    divergir en como arma el turno de vuelta hacia el LLM."""
    assert response.tool_calls is not None
    messages = [
        *messages,
        Message(
            role=Role.ASSISTANT,
            content=response.content,
            tool_calls=response.tool_calls,
        ),
    ]

    for tool_call in response.tool_calls:
        result = await _execute_tool(tools_by_name, tool_call)
        messages.append(
            Message(
                role=Role.TOOL,
                content=json.dumps(result, ensure_ascii=False, default=str),
                tool_call_id=tool_call.id,
            )
        )

    return messages


async def _execute_tool(tools_by_name: dict[str, Tool], tool_call: ToolCall) -> Any:
    """Nunca deja que una excepcion de una tool tumbe el loop entero --
    un tool_call a una tool inexistente, o que falla al ejecutarse, se
    convierte en un resultado de error que vuelve al LLM (igual que una
    API real le devolveria un error 4xx/5xx a un caller, no un crash
    del proceso). El LLM decide que hacer con ese error: reintentar con
    otros argumentos, decirle al usuario que algo fallo, etc."""
    tool = tools_by_name.get(tool_call.name)
    if tool is None:
        logger.warning("El LLM pidio una tool desconocida: %s", tool_call.name)
        return {"error": f"Tool '{tool_call.name}' no existe"}

    try:
        return await tool.handler(**tool_call.arguments)
    except Exception as exc:  # noqa: BLE001 -- ver docstring, amplio a proposito
        logger.exception("Fallo ejecutando la tool '%s'", tool_call.name)
        return {"error": f"La tool '{tool_call.name}' fallo: {exc}"}
