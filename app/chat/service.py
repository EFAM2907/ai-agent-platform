from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.mcp_tools import open_mcp_tools
from app.chat.models import ChatMessage, ChatSession, MessageRole
from app.chat.repository import ChatRepository
from app.llm.client import LLMClient
from app.llm.prompts.loader import PromptLoader
from app.llm.routing import ModelRouter
from app.llm.schemas import LLMRequest, Message, Role
from app.llm.tools import ToolLoopError, run_streaming_tool_loop, run_tool_loop
from app.rag.service import RAGService
from app.users.models import User

logger = logging.getLogger(__name__)


class EmptyResponseError(Exception):
    """El LLM devolvio finish_reason distinto de "tool_calls" pero con
    content vacio -- no es un error de transporte (el provider no
    lanzo nada, generate()/generate_stream() completaron "bien"), pero
    para un chat una respuesta invisible es indistinguible de que el
    sistema no respondio nada. Detectado y tratado como fallo real en
    vez de persistir un ChatMessage vacio y devolver "done" como si
    todo hubiera salido bien -- ver el incidente real donde un 429 de
    Gemini en cascada produjo exactamente esto."""


_SYSTEM_PROMPT_NAME = "support_chat_agent"
_SYSTEM_PROMPT_VERSION = 5
_TOP_K_CHUNKS = 5
_HISTORY_MESSAGES = 10
_MAX_TOOL_ITERATIONS = 5

# Mismos dos tiers que litellm_config.yaml y eval_harness/model_routing_eval.py
# -- si alguno cambia, cambian los tres juntos, no es un descuido tenerlos
# repetidos en vez de en un solo lugar central (ver el comentario en
# app.llm.factory sobre por que litellm_config.yaml es "el" lugar para
# routing/fallback; estos son solo los nombres que ChatService le pasa al
# ModelRouter, no una decision de infraestructura nueva).
CHEAP_MODEL = "gemini-3.1-flash-lite"
# TEMPORAL: dos flash-lite distintos en vez de "gemini-3.6-flash" /
# "claude-sonnet-5" -- Anthropic y OpenAI siguen sin credito real (ver
# la conversacion), y gemini-3.6-flash por si solo tiene RPM
# insuficiente para ensayar el flujo completo (chat + tools) sin pisar
# el rate limit. Antes CHEAP y EXPENSIVE apuntaban AMBOS a
# gemini-3.1-flash-lite -- eso significaba que cada turno competia dos
# veces (clasificador + respuesta) por la MISMA cuota, que es
# justamente lo que la satura mas rapido. Separarlos en dos modelos
# reales (cada uno con su propio cupo de RPM en la API de Gemini) alivia
# eso sin gastar credito real de ningun proveedor de pago.
#
# gemini-3.5-flash-lite va al tier caro (antes gemini-2.5-flash-lite,
# que Google ya no ofrece a cuentas nuevas: responde 404). Se deja
# gemini-3.1-flash-lite en el tier barato, que ademas es el que YA
# recibe trafico garantizado en cada turno (el clasificador de
# ModelRouter siempre llama al cheap_model, sin importar el resultado).
# Verificar los numeros reales de la cuenta en
# https://aistudio.google.com/rate-limit -- si resultan al reves,
# basta con intercambiar estas dos lineas.
#
# REVERTIR ambos a sus modelos reales (CHEAP_MODEL="gemini-3.6-flash",
# EXPENSIVE_MODEL="claude-sonnet-5") en cuanto haya credito real.
# Dejar esto asi permanentemente pierde el sentido de tener tiers
# separados.
EXPENSIVE_MODEL = "gemini-3.5-flash-lite"


class ChatService:
    def __init__(
        self,
        repository: ChatRepository,
        rag_service: RAGService,
        llm_client: LLMClient,
        model_router: ModelRouter,
        session: AsyncSession,
        prompt_loader: PromptLoader | None = None,
    ) -> None:
        self.repository = repository
        self.rag_service = rag_service
        self.llm_client = llm_client
        self.model_router = model_router
        self.session = session
        self.prompt_loader = prompt_loader or PromptLoader()

    async def send_message(
        self,
        organization_id: uuid.UUID,
        current_user: User,
        message: str,
        session_id: uuid.UUID | None,
        bearer_token: str,
    ) -> ChatMessage:
        """Guarda el mensaje del usuario, recupera contexto de la KB,
        genera la respuesta del asistente (con las tools de gestion de
        usuarios y de consulta de envios/clientes que anuncie el
        servidor MCP, ver app.chat.mcp_tools), la guarda, y devuelve
        el ChatMessage del asistente ya persistido. Crea la sesion si
        session_id es None o no resuelve para este tenant --
        nunca falla por un session_id invalido, simplemente empieza una
        conversacion nueva.

        bearer_token es el JWT crudo de este request (ver
        app.core.dependencies.get_bearer_token) -- se reenvia tal cual
        al servidor MCP, que a su vez se lo reenvia a la API REST, para
        que cada tool call corra con los mismos permisos que
        current_user ya tiene, nunca con credenciales propias de este
        servicio."""
        chat_session, request, sources = await self._prepare_turn(
            organization_id, current_user, message, session_id
        )

        async with open_mcp_tools(bearer_token) as tools:
            response = await run_tool_loop(
                self.llm_client, request, tools, max_iterations=_MAX_TOOL_ITERATIONS
            )
        self._reject_empty_response(response.content, chat_session.id)

        assistant_message = await self.repository.add_message(
            chat_session.id,
            organization_id,
            MessageRole.ASSISTANT,
            response.content,
            sources=sources,
        )
        await self.session.commit()
        return assistant_message

    async def send_message_stream(
        self,
        organization_id: uuid.UUID,
        current_user: User,
        message: str,
        session_id: uuid.UUID | None,
        bearer_token: str,
    ) -> AsyncIterator[dict]:
        """Misma logica de turno que send_message(), pero entregando la
        respuesta como una secuencia de eventos en vez de esperar el
        texto completo -- app.chat.api es quien traduce estos dicts al
        formato de SSE ("event: ...\\ndata: ...\\n\\n"), este metodo no
        sabe nada de HTTP a proposito, misma separacion que el resto de
        la app mantiene entre service y api.

        Eventos que puede producir, en orden:
          - {"event": "start", "session_id": ..., "sources": [...]}
          - {"event": "delta", "content": "..."} (uno por cada pedazo de texto)
          - {"event": "done", "message_id": ..., "created_at": ...}
          - {"event": "error", "detail": "..."} (en vez de "done", si algo
            falla a mitad del stream)

        Con tools habilitadas (ver app.chat.mcp_tools), el texto sí sigue
        saliendo token a token de verdad: run_streaming_tool_loop()
        resuelve cada llamada a tool con generate_stream() (no
        generate()), y solo ejecuta las tools entre vueltas -- la
        vuelta que responde con texto final se streamea en vivo igual
        que antes de que existieran las tools.

        Si falla a mitad de turno, el mensaje del usuario ya guardado
        en _prepare_turn() queda persistido igual (a diferencia del
        comportamiento anterior) porque la API REST (del otro lado del
        servidor MCP) confirma sus propios cambios de inmediato si el
        LLM llego a ejecutar una tool de mutacion (delete_user,
        update_user, etc.) -- esa accion real ya ocurrio y no tendria
        sentido esconderla. Lo que sigue sin persistir si falla es el
        mensaje ASSISTANT de esta funcion, exactamente igual que antes.

        abrir la sesion MCP (open_mcp_tools) esta DENTRO del try de
        abajo a proposito: si el servidor MCP no esta disponible, eso
        debe llegar como el mismo evento SSE "error" que cualquier otra
        falla a mitad de turno, no como un 500 crudo.
        """
        chat_session, request, sources = await self._prepare_turn(
            organization_id, current_user, message, session_id
        )

        yield {
            "event": "start",
            "session_id": str(chat_session.id),
            "sources": sources,
        }

        accumulated: list[str] = []
        try:
            async with open_mcp_tools(bearer_token) as tools:
                async for delta in run_streaming_tool_loop(
                    self.llm_client, request, tools, max_iterations=_MAX_TOOL_ITERATIONS
                ):
                    accumulated.append(delta)
                    yield {"event": "delta", "content": delta}
        except ToolLoopError:
            logger.exception(
                "El modelo no convergio resolviendo tools para la sesion %s",
                chat_session.id,
            )
            yield {
                "event": "error",
                "detail": "No se pudo completar la acción solicitada. Intenta de nuevo.",
            }
            return
        except Exception:
            # Excepcion amplia a proposito, misma razon que antes: los
            # errores del SDK subyacente no siempre llegan envueltos en
            # app.llm.errors.LLMError (gap conocido, no cerrado en este
            # cambio) -- un except LLMError aca dejaria pasar de largo
            # un error crudo del proveedor y tumbaria el request con un
            # 500 sin SSE "error" limpio.
            logger.exception(
                "Fallo generando la respuesta en streaming para la sesion %s",
                chat_session.id,
            )
            yield {
                "event": "error",
                "detail": "No se pudo generar la respuesta. Intenta de nuevo.",
            }
            return

        full_content = "".join(accumulated)
        try:
            self._reject_empty_response(full_content, chat_session.id)
        except EmptyResponseError:
            yield {
                "event": "error",
                "detail": "No se pudo generar la respuesta. Intenta de nuevo.",
            }
            return

        assistant_message = await self.repository.add_message(
            chat_session.id,
            organization_id,
            MessageRole.ASSISTANT,
            full_content,
            sources=sources,
        )
        await self.session.commit()
        yield {
            "event": "done",
            "message_id": str(assistant_message.id),
            "created_at": assistant_message.created_at.isoformat(),
        }

    async def _prepare_turn(
        self,
        organization_id: uuid.UUID,
        current_user: User,
        message: str,
        session_id: uuid.UUID | None,
    ) -> tuple[ChatSession, LLMRequest, list[str]]:
        """Todo lo que send_message() y send_message_stream() necesitan
        antes de poder generar: resolver/crear la sesion, guardar el
        mensaje del usuario, y armar el LLMRequest -- compartido para
        que ambos caminos se beneficien igual de la eleccion de modelo
        (ModelRouter) y del control de reasoning_effort. Las tools NO
        se arman aca: dependen de una sesion MCP abierta
        (app.chat.mcp_tools.open_mcp_tools) que el caller abre el solo,
        envolviendo el tool loop completo -- no tendria sentido abrirla
        aca y cerrarla antes de que el loop la use."""
        chat_session = None
        if session_id is not None:
            chat_session = await self.repository.get_session(session_id, organization_id)
        if chat_session is None:
            chat_session = await self.repository.create_session(organization_id, current_user.id)

        await self.repository.add_message(
            chat_session.id, organization_id, MessageRole.USER, message
        )

        # Recuperar contexto de la KB (RAG) y elegir el modelo
        # (ModelRouter) son dos llamadas independientes entre si -- las
        # dos parten unicamente del mensaje del usuario -- asi que
        # corren en paralelo en vez de una despues de la otra. La
        # latencia total pasa a ser el maximo de las dos, no la suma.
        retrieved, chosen_model = await asyncio.gather(
            self.rag_service.retrieve(organization_id, message, top_k=_TOP_K_CHUNKS),
            self.model_router.choose_model(message),
        )
        sources = sorted({title for _chunk, title, _distance in retrieved})
        kb_context = self._format_context(retrieved)

        prompt = self.prompt_loader.load_version(
            _SYSTEM_PROMPT_NAME, _SYSTEM_PROMPT_VERSION
        )
        system_prompt = prompt.render(kb_context=kb_context)

        history = await self.repository.recent_messages(
            chat_session.id, organization_id, limit=_HISTORY_MESSAGES
        )
        conversation = [
            Message(
                role=Role.USER if entry.role == MessageRole.USER else Role.ASSISTANT,
                content=entry.content,
            )
            for entry in history
        ]

        request = LLMRequest(
            messages=[Message(role=Role.SYSTEM, content=system_prompt), *conversation],
            model=chosen_model,
            tenant_id=str(organization_id),
            # El tier barato corre sin razonamiento extendido -- es el
            # que atiende preguntas simples de lectura/sintesis sobre la
            # KB, donde el "thinking" de Gemini es puro tiempo de espera
            # sin ganancia real. El tier caro NUNCA lo apaga: si el
            # router mando la pregunta ahi fue justamente porque
            # necesita razonar (evidencia cruzada, ambiguedad, una
            # accion sensible) -- apagarle el thinking ahi contradiria
            # la decision de routing que se acaba de tomar.
            reasoning_effort="none" if chosen_model == CHEAP_MODEL else None,
        )
        return chat_session, request, sources

    @staticmethod
    def _reject_empty_response(content: str, session_id: uuid.UUID) -> None:
        if content.strip():
            return
        logger.error(
            "El LLM devolvio una respuesta vacia (sin tool_calls) para la sesion %s",
            session_id,
        )
        raise EmptyResponseError("El modelo devolvio una respuesta vacia")

    @staticmethod
    def _format_context(retrieved: list[tuple]) -> str:
        if not retrieved:
            return (
                "(No knowledge base content was found related to this "
                "question.)"
            )
        blocks = [f"### {title}\n{chunk.content}" for chunk, title, _distance in retrieved]
        return "\n\n".join(blocks)
