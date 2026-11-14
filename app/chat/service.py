from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.mcp_tools import call_approved_tool, open_mcp_tools
from app.chat.models import ChatMessage, ChatSession, MessageRole, PendingAction, PendingActionStatus
from app.chat.repository import ChatRepository
from app.chat.tool_access import filter_tools_for_role
from app.chat.tracing import TurnTracer
from app.core.config import settings
from app.llm.client import LLMClient
from app.llm.prompts.loader import PromptLoader
from app.llm.routing import ModelRouter, RoutingDecision
from app.llm.schemas import LLMRequest, Message, Role
from app.llm.tools import ToolEvent, ToolLoopError, run_streaming_tool_loop, run_tool_loop
from app.rag.service import RAGService
from app.users.models import User

logger = logging.getLogger(__name__)


class ActionNotFoundError(Exception):
    """La accion pendiente no existe o no es de este usuario."""


class ActionNotPendingError(Exception):
    """La accion ya fue decidida o vencio."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


def _action_payload(action: PendingAction) -> dict[str, Any]:
    return {
        "id": str(action.id),
        "tool_name": action.tool_name,
        "arguments": action.arguments,
        "expires_at": action.expires_at.isoformat(),
    }


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
_SYSTEM_PROMPT_VERSION = 6
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
# "claude-sonnet-5" -- Anthropic y OpenAI siguen sin credito real, y
# gemini-3.6-flash por si solo tiene RPM insuficiente para ensayar el
# flujo completo (chat + tools) sin pisar el rate limit. Separar CHEAP
# y EXPENSIVE en dos modelos reales (cada uno con su propio cupo de RPM
# en la API de Gemini) alivia eso sin gastar credito real de ningun
# proveedor de pago. El clasificador de ModelRouter siempre llama al
# cheap_model, sin importar el resultado.
#
# REVERTIR ambos a sus modelos reales (CHEAP_MODEL="gemini-3.6-flash",
# EXPENSIVE_MODEL="claude-sonnet-5") en cuanto haya credito real.
# Dejar esto asi permanentemente pierde el sentido de tener tiers
# separados.
EXPENSIVE_MODEL = "gemini-3.5-flash-lite"


@dataclass
class PreparedTurn:
    """Todo lo que send_message()/send_message_stream() necesitan despues
    de _prepare_turn(). Los ids se guardan como valores simples (no solo
    los objetos ORM) para poder registrar una traza de error incluso si
    la sesion de base de datos tuvo que hacer rollback y expiro los
    objetos."""

    chat_session: ChatSession
    session_id: uuid.UUID
    user_message_id: uuid.UUID
    request: LLMRequest
    sources: list[str]
    tracer: TurnTracer
    organization_id: uuid.UUID
    user_id: uuid.UUID
    # Acciones destructivas que el agente pidio en este turno y esperan
    # aprobacion (ver ChatService._request_confirmation).
    pending_actions: list[dict[str, Any]] = field(default_factory=list)
    # Las que el stream ya le aviso al cliente.
    announced_actions: int = 0


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
    ) -> ChatMessage:
        """Guarda el mensaje del usuario, recupera contexto de la KB,
        genera la respuesta del asistente (con las herramientas que
        anuncie el servidor MCP y que el rol del usuario permita, ver
        app.chat.mcp_tools y app.chat.tool_access), la guarda junto con
        la traza del turno, y devuelve el ChatMessage del asistente ya
        persistido. Crea la sesion si session_id es None o no resuelve
        para este usuario -- nunca falla por un session_id invalido ni
        ajeno, simplemente empieza una conversacion nueva.

        El JWT del usuario no sale de la API: open_mcp_tools lo
        intercambia por un token delegado (aud=mcp-server, scope
        reducido, vida corta, actor=chat-service). Las tools
        destructivas solo crean una accion pendiente de aprobacion.

        El mensaje del usuario se confirma en la base ANTES de llamar al
        modelo: si el turno falla, la conversacion conserva lo que el
        usuario escribio y la traza de error (status="error") dice por
        que fallo."""
        turn = await self._prepare_turn(organization_id, current_user, message, session_id)

        try:
            async with open_mcp_tools(
                current_user,
                confirmation_handler=self._confirmation_handler(turn, current_user),
                on_delegation=turn.tracer.record_delegation,
            ) as tools:
                tools = filter_tools_for_role(tools, current_user.role)
                response = await run_tool_loop(
                    self.llm_client,
                    turn.request,
                    tools,
                    max_iterations=_MAX_TOOL_ITERATIONS,
                    observer=turn.tracer,
                )
            self._reject_empty_response(response.content, turn.session_id)
        except Exception as exc:
            await self._record_failed_turn(turn, exc)
            raise

        assistant_message = await self._finish_turn(turn, response.content)
        # Atributo transitorio (no es una columna): la API lo devuelve para
        # que el cliente pueda abrir la traza de esta respuesta.
        assistant_message.trace_id = turn.tracer.trace_id
        assistant_message.pending_actions = turn.pending_actions
        return assistant_message

    async def send_message_stream(
        self,
        organization_id: uuid.UUID,
        current_user: User,
        message: str,
        session_id: uuid.UUID | None,
    ) -> AsyncIterator[dict]:
        """Misma logica de turno que send_message(), pero entregando la
        respuesta como una secuencia de eventos en vez de esperar el
        texto completo -- app.chat.api es quien traduce estos dicts al
        formato de SSE ("event: ...\\ndata: ...\\n\\n"), este metodo no
        sabe nada de HTTP a proposito, misma separacion que el resto de
        la app mantiene entre service y api.

        Eventos que puede producir, en orden:
          - {"event": "start", "session_id", "sources", "trace_id"}
          - {"event": "tool", "name", "phase": "start"|"end", "ok"}
            (uno al empezar y otro al terminar cada herramienta)
          - {"event": "confirmation_required", "action": {...}} (el agente
            pidio una accion destructiva: no se ejecuto, espera tu
            aprobacion en POST /chat/actions/{id}/approve)
          - {"event": "delta", "content": "..."} (uno por cada pedazo de texto)
          - {"event": "done", "message_id", "created_at", "trace": {...}}
          - {"event": "error", "detail", "trace_id"} (en vez de "done", si
            algo falla a mitad del stream)

        Con herramientas habilitadas, el texto sigue saliendo token a
        token de verdad: run_streaming_tool_loop() resuelve cada llamada
        a tool con generate_stream() (no generate()), y solo ejecuta las
        tools entre vueltas -- la vuelta que responde con texto final se
        streamea en vivo.

        Si falla a mitad de turno, el mensaje del usuario ya esta
        confirmado (se guarda antes de llamar al modelo) y se registra una
        traza con status="error". Lo que no persiste es el mensaje
        ASSISTANT. Si una herramienta de escritura llego a ejecutarse, esa
        accion real ya ocurrio en la API REST y la traza lo muestra.

        abrir la sesion MCP (open_mcp_tools) esta DENTRO del try de
        abajo a proposito: si el servidor MCP no esta disponible, eso
        debe llegar como el mismo evento SSE "error" que cualquier otra
        falla a mitad de turno, no como un 500 crudo.
        """
        turn = await self._prepare_turn(organization_id, current_user, message, session_id)

        yield {
            "event": "start",
            "session_id": str(turn.session_id),
            "sources": turn.sources,
            "trace_id": turn.tracer.trace_id,
        }

        accumulated: list[str] = []
        failure: Exception | None = None
        error_detail = "No se pudo generar la respuesta. Intenta de nuevo."
        try:
            async with open_mcp_tools(
                current_user,
                confirmation_handler=self._confirmation_handler(turn, current_user),
                on_delegation=turn.tracer.record_delegation,
            ) as tools:
                tools = filter_tools_for_role(tools, current_user.role)
                async for item in run_streaming_tool_loop(
                    self.llm_client,
                    turn.request,
                    tools,
                    max_iterations=_MAX_TOOL_ITERATIONS,
                    observer=turn.tracer,
                    emit_tool_events=True,
                ):
                    if isinstance(item, ToolEvent):
                        yield {
                            "event": "tool",
                            "name": item.name,
                            "phase": item.phase,
                            "ok": item.ok,
                        }
                    else:
                        accumulated.append(item)
                        yield {"event": "delta", "content": item}
                    while turn.announced_actions < len(turn.pending_actions):
                        action = turn.pending_actions[turn.announced_actions]
                        turn.announced_actions += 1
                        yield {"event": "confirmation_required", "action": action}
            self._reject_empty_response("".join(accumulated), turn.session_id)
        except ToolLoopError as exc:
            logger.exception(
                "El modelo no convergio resolviendo tools para la sesion %s", turn.session_id
            )
            failure = exc
            error_detail = "No se pudo completar la acción solicitada. Intenta de nuevo."
        except Exception as exc:
            # Excepcion amplia a proposito: los errores del SDK subyacente
            # no siempre llegan envueltos en app.llm.errors.LLMError (gap
            # conocido) -- un except mas estrecho dejaria pasar un error
            # crudo del proveedor y tumbaria el request con un 500 sin
            # evento SSE "error" limpio.
            logger.exception(
                "Fallo generando la respuesta en streaming para la sesion %s", turn.session_id
            )
            failure = exc

        if failure is not None:
            await self._record_failed_turn(turn, failure)
            yield {"event": "error", "detail": error_detail, "trace_id": turn.tracer.trace_id}
            return

        assistant_message = await self._finish_turn(turn, "".join(accumulated))
        yield {
            "event": "done",
            "message_id": str(assistant_message.id),
            "created_at": assistant_message.created_at.isoformat(),
            "trace": turn.tracer.summary(),
        }

    def _confirmation_handler(self, turn: PreparedTurn, current_user: User):
        """Que hace el agente cuando pide una tool destructiva: NO se
        ejecuta -- se guarda como accion pendiente y el LLM recibe un
        resultado que le indica avisar al usuario y esperar."""

        async def handler(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            action = await self.repository.create_pending_action(
                organization_id=turn.organization_id,
                user_id=current_user.id,
                session_id=turn.session_id,
                trace_id=turn.tracer.trace_id,
                tool_name=tool_name,
                arguments=arguments,
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=settings.pending_action_ttl_minutes),
            )
            payload = _action_payload(action)
            # Commit inmediato: la accion debe existir cuando el usuario
            # pulse "Aprobar", aunque el turno siga o falle despues.
            await self.session.commit()
            turn.pending_actions.append(payload)
            return {
                "status": "pending_confirmation",
                "action_id": str(action.id),
                "message": (
                    "La accion NO se ejecuto. Quedo pendiente de la aprobacion explicita "
                    "del usuario en la interfaz. Explicale brevemente que debe aprobarla o "
                    "rechazarla ahi; no vuelvas a intentarla."
                ),
            }

        return handler

    async def list_pending_actions(
        self, current_user: User, session_id: uuid.UUID | None = None
    ) -> list[PendingAction]:
        return await self.repository.list_pending_actions(current_user.id, session_id)

    async def approve_action(self, current_user: User, action_id: uuid.UUID) -> PendingAction:
        """El humano aprueba una accion pendiente (con su propio JWT de
        sesion, no con el token delegado): se reclama de forma atomica
        (nunca se ejecuta dos veces) y se ejecuta con un token delegado
        de un solo uso atado a estos argumentos exactos."""
        action = await self.repository.get_pending_action(action_id, current_user.id)
        if action is None:
            raise ActionNotFoundError()
        # Valores simples ANTES de los commit: commit expira los objetos ORM.
        tool_name, arguments, previous_status = action.tool_name, dict(action.arguments), action.status
        if not await self.repository.decide_pending_action(
            action_id, current_user.id, PendingActionStatus.APPROVED
        ):
            await self.session.rollback()
            raise ActionNotPendingError(previous_status)
        await self.session.commit()

        try:
            result = await call_approved_tool(current_user, tool_name, arguments, str(action_id))
            status, payload = PendingActionStatus.EXECUTED, result
        except Exception as exc:  # noqa: BLE001 -- el resultado queda en la accion
            logger.exception("Fallo la accion aprobada %s (%s)", action_id, tool_name)
            status, payload = PendingActionStatus.FAILED, {"error": str(exc)[:500]}
        await self.repository.finish_pending_action(action_id, status, payload)
        await self.session.commit()
        await self.session.refresh(action)
        logger.info(
            "approved_action id=%s tool=%s sub=%s status=%s",
            action_id, tool_name, current_user.id, status.value,
        )
        return action

    async def reject_action(self, current_user: User, action_id: uuid.UUID) -> PendingAction:
        action = await self.repository.get_pending_action(action_id, current_user.id)
        if action is None:
            raise ActionNotFoundError()
        previous_status = action.status
        if not await self.repository.decide_pending_action(
            action_id, current_user.id, PendingActionStatus.REJECTED
        ):
            await self.session.rollback()
            raise ActionNotPendingError(previous_status)
        await self.session.commit()
        await self.session.refresh(action)
        return action

    async def _finish_turn(self, turn: PreparedTurn, content: str) -> ChatMessage:
        """Guarda la respuesta del asistente y la traza del turno en UNA
        sola transaccion: nunca hay una respuesta sin traza ni al reves."""
        assistant_message = await self.repository.add_message(
            turn.session_id,
            turn.organization_id,
            MessageRole.ASSISTANT,
            content,
            sources=turn.sources,
        )
        await self.repository.add_trace(
            turn.tracer.build(
                organization_id=turn.organization_id,
                user_id=turn.user_id,
                session_id=turn.session_id,
                user_message_id=turn.user_message_id,
                assistant_message_id=assistant_message.id,
            )
        )
        await self.session.commit()
        return assistant_message

    async def _record_failed_turn(self, turn: PreparedTurn, error: Exception) -> None:
        """Deja constancia de un turno fallido. Es de mejor esfuerzo: si
        ni siquiera se puede guardar la traza, se loguea y se sigue --
        nunca debe enmascarar el error original que se esta reportando."""
        try:
            if not self.session.is_active:
                await self.session.rollback()
            await self.repository.add_trace(
                turn.tracer.build(
                    organization_id=turn.organization_id,
                    user_id=turn.user_id,
                    session_id=turn.session_id,
                    user_message_id=turn.user_message_id,
                    assistant_message_id=None,
                    error=error,
                )
            )
            await self.session.commit()
        except Exception:
            logger.exception("No se pudo guardar la traza del turno fallido %s", turn.tracer.trace_id)

    async def _prepare_turn(
        self,
        organization_id: uuid.UUID,
        current_user: User,
        message: str,
        session_id: uuid.UUID | None,
    ) -> PreparedTurn:
        """Todo lo que send_message() y send_message_stream() necesitan
        antes de poder generar: resolver/crear la sesion, guardar (y
        confirmar) el mensaje del usuario, y armar el LLMRequest --
        compartido para que ambos caminos se beneficien igual de la
        eleccion de modelo (ModelRouter) y del control de
        reasoning_effort. Las tools NO se arman aca: dependen de una
        sesion MCP abierta (app.chat.mcp_tools.open_mcp_tools) que el
        caller abre el solo, envolviendo el tool loop completo."""
        chat_session = None
        if session_id is not None:
            chat_session = await self.repository.get_session(session_id, organization_id)
            # Una sesion de OTRO usuario de la misma organizacion se trata
            # como inexistente: escribir en ella (y mostrarle su historial
            # al modelo) seria leer la conversacion ajena.
            if chat_session is not None and chat_session.user_id != current_user.id:
                chat_session = None
        if chat_session is None:
            chat_session = await self.repository.create_session(organization_id, current_user.id)

        user_message = await self.repository.add_message(
            chat_session.id, organization_id, MessageRole.USER, message
        )
        # Se confirma ya: si el modelo o una herramienta falla, lo que el
        # usuario escribio no se pierde con el rollback.
        await self.session.commit()
        session_uuid, user_message_uuid = chat_session.id, user_message.id

        tracer = TurnTracer()
        trace_context = {
            "tenant_id": str(organization_id),
            "trace_id": tracer.trace_id,
            "session_id": str(session_uuid),
            "user_id": str(current_user.id),
        }

        # Recuperar contexto de la KB (RAG) y elegir el modelo
        # (ModelRouter) son dos llamadas independientes entre si -- las
        # dos parten unicamente del mensaje del usuario -- asi que
        # corren en paralelo. La latencia total pasa a ser el maximo de
        # las dos, no la suma. return_exceptions=True: que falle la KB
        # (cuota de embeddings, red) NO debe tumbar el chat -- el turno
        # sigue sin contexto de la KB y la traza lo registra.
        retrieval_started = time.perf_counter()
        retrieved_result, decision_result = await asyncio.gather(
            self.rag_service.retrieve(
                organization_id, message, top_k=_TOP_K_CHUNKS, viewer_role=current_user.role
            ),
            self.model_router.decide(message, trace=trace_context),
            return_exceptions=True,
        )
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000

        retrieval_error: str | None = None
        if isinstance(retrieved_result, Exception):
            logger.warning(
                "La busqueda en la KB fallo; el turno sigue sin contexto (%s: %s)",
                type(retrieved_result).__name__, retrieved_result,
            )
            retrieval_error = f"{type(retrieved_result).__name__}: {retrieved_result}"
            retrieved: list = []
        elif isinstance(retrieved_result, BaseException):
            raise retrieved_result
        else:
            retrieved = retrieved_result

        if isinstance(decision_result, RoutingDecision):
            decision = decision_result
        elif isinstance(decision_result, Exception):
            logger.warning("El router de modelo fallo; se usa el modelo caro (%s)", decision_result)
            decision = RoutingDecision(EXPENSIVE_MODEL, "router_error")
        else:
            raise decision_result  # CancelledError y similares

        tracer.record_route(decision.model, decision.reason, decision.classifier_response)
        tracer.record_retrieval(
            mode="hybrid" if settings.rag_hybrid_search_enabled else "vector",
            results=retrieved,
            latency_ms=retrieval_ms,
            error=retrieval_error,
        )

        sources = sorted({title for _chunk, title, _score in retrieved})
        kb_context = self._format_context(retrieved)

        prompt = self.prompt_loader.load_version(_SYSTEM_PROMPT_NAME, _SYSTEM_PROMPT_VERSION)
        system_prompt = prompt.render(kb_context=kb_context)

        history = await self.repository.recent_messages(
            session_uuid, organization_id, limit=_HISTORY_MESSAGES
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
            model=decision.model,
            request_tag="chat_answer",
            # El tier barato corre sin razonamiento extendido -- es el
            # que atiende preguntas simples de lectura/sintesis sobre la
            # KB, donde el "thinking" de Gemini es puro tiempo de espera
            # sin ganancia real. El tier caro NUNCA lo apaga: si el
            # router mando la pregunta ahi fue justamente porque
            # necesita razonar (evidencia cruzada, ambiguedad, una
            # accion sensible) -- apagarle el thinking ahi contradiria
            # la decision de routing que se acaba de tomar.
            reasoning_effort="none" if decision.model == CHEAP_MODEL else None,
            **trace_context,
        )
        return PreparedTurn(
            chat_session=chat_session,
            session_id=session_uuid,
            user_message_id=user_message_uuid,
            request=request,
            sources=sources,
            tracer=tracer,
            organization_id=organization_id,
            user_id=current_user.id,
        )

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
        blocks = [f"### {title}\n{chunk.content}" for chunk, title, _score in retrieved]
        return "\n\n".join(blocks)
