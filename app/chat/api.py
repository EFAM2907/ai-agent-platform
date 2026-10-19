from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.dependencies import get_chat_service
from app.chat.repository import ChatRepository
from app.chat.models import ChatTurnTrace, MessageRole
from app.chat.schemas import (
    ChatMessageIn,
    ChatMessageOut,
    ChatReplyOut,
    ChatSessionOut,
    ChatSessionRenameIn,
    ChatSessionSummaryOut,
    FeedbackIn,
    FeedbackOut,
    PendingActionOut,
    TraceDetailOut,
    TraceListOut,
    TraceStatsOut,
    TraceSummaryOut,
)
from app.chat.service import ActionNotFoundError, ActionNotPendingError, ChatService
from app.core.database import get_db
from app.core.dependencies import (
    get_current_user,
    require_admin,
    require_password_changed,
)
from app.organizations.dependencies import get_current_organization
from app.organizations.models import Organization
from app.users.models import User, UserRole

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/messages", response_model=ChatReplyOut)
async def send_message(
    payload: ChatMessageIn,
    current_user: User = Depends(require_password_changed),
    organization: Organization = Depends(get_current_organization),
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatReplyOut:
    assistant_message = await chat_service.send_message(
        organization_id=organization.id,
        current_user=current_user,
        message=payload.message,
        session_id=payload.session_id,
    )
    # Construido explicitamente, no via from_attributes=True sobre el
    # ORM directo: ChatMessage.sources es un string "|"-separado en la
    # DB, pero ChatMessageOut.sources es list[str] -- from_attributes
    # no sabe hacer esa conversion sola.
    message_out = ChatMessageOut(
        id=assistant_message.id,
        role=assistant_message.role,
        content=assistant_message.content,
        sources=assistant_message.sources.split("|") if assistant_message.sources else [],
        created_at=assistant_message.created_at,
    )
    return ChatReplyOut(
        session_id=assistant_message.session_id,
        message=message_out,
        trace_id=getattr(assistant_message, "trace_id", None),
        pending_actions=getattr(assistant_message, "pending_actions", []),
    )


@router.post("/messages/stream")
async def send_message_stream(
    payload: ChatMessageIn,
    current_user: User = Depends(require_password_changed),
    organization: Organization = Depends(get_current_organization),
    chat_service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    """Mismo turno que POST /messages, pero como Server-Sent Events en
    vez de esperar la respuesta completa -- ver
    ChatService.send_message_stream para el formato exacto de cada
    evento. Auth y resolucion de organizacion pasan por las mismas
    dependencies ANTES de que arranque el streaming (FastAPI resuelve
    Depends() antes de llamar al handler), asi que un 401 llega como un
    error HTTP normal, nunca a mitad de un stream ya empezado.

    No usa EventSource nativo del browser a proposito -- EventSource
    solo soporta GET y no permite mandar el header Authorization, y
    esta API es Bearer-token, no de cookies. El frontend consume esto
    con fetch() + response.body.getReader(), no con EventSource (ver
    frontend/src/api.ts streamChatMessage)."""

    async def event_source():
        async for event in chat_service.send_message_stream(
            organization_id=organization.id,
            current_user=current_user,
            message=payload.message,
            session_id=payload.session_id,
            ):
            event_type = event.pop("event")
            yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.get("/sessions", response_model=list[ChatSessionSummaryOut])
async def list_sessions(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
    session: AsyncSession = Depends(get_db),
) -> list[ChatSessionSummaryOut]:
    """Historial para el sidebar -- accesible aunque must_change_password
    este activo (no pasa por require_password_changed), mismo criterio
    que GET /users/me: ver tu propio historial no es una accion de
    negocio que deba bloquearse."""
    repository = ChatRepository(session)
    return await repository.list_sessions(organization.id, current_user.id, skip, limit)


@router.patch("/sessions/{session_id}", response_model=ChatSessionSummaryOut)
async def rename_session(
    session_id: uuid.UUID,
    payload: ChatSessionRenameIn,
    current_user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
    session: AsyncSession = Depends(get_db),
) -> ChatSessionSummaryOut:
    """Renombrar una conversacion propia -- mismo criterio que
    list_sessions: no pasa por require_password_changed porque
    organizar tu historial no es una accion de negocio nueva, es
    manejo de datos que ya son tuyos."""
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="El título no puede estar vacío")

    repository = ChatRepository(session)
    chat_session = await repository.rename_session(
        session_id, organization.id, current_user.id, title
    )
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return chat_session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Borra una conversacion propia (y en cascada sus mensajes, ver
    ChatRepository.delete_session). Mismo criterio de autorizacion que
    rename_session: filtra por organization_id Y user_id, nunca solo
    por id -- ni un ADMIN puede borrar el historial de otro usuario
    desde aqui."""
    repository = ChatRepository(session)
    deleted = await repository.delete_session(session_id, organization.id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found")


@router.get("/sessions/{session_id}", response_model=ChatSessionOut)
async def get_session_history(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
    session: AsyncSession = Depends(get_db),
) -> ChatSessionOut:
    """Trae una conversacion propia -- filtra por organization_id Y
    user_id, mismo criterio que rename_session/delete_session (antes
    esta ruta solo filtraba por organization_id: cualquier usuario de la
    organizacion podia leer el historial de otro con solo conocer su
    session_id, ej. uno guardado en el localStorage del navegador
    compartido de una cuenta anterior)."""
    repository = ChatRepository(session)
    chat_session = await repository.get_session(session_id, organization.id)
    if chat_session is None or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await repository.list_messages(session_id, organization.id)
    return ChatSessionOut(
        id=chat_session.id,
        title=chat_session.title,
        created_at=chat_session.created_at,
        messages=[
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources.split("|") if m.sources else [],
                "created_at": m.created_at,
                "feedback": m.feedback,
            }
            for m in messages
        ],
    )


# ---------------------------------------------------------------------
# Trazabilidad y feedback
# ---------------------------------------------------------------------
def _trace_summary(trace: ChatTurnTrace) -> TraceSummaryOut:
    return TraceSummaryOut(
        id=trace.id,
        trace_id=trace.trace_id,
        created_at=trace.created_at,
        user_id=trace.user_id,
        session_id=trace.session_id,
        status=trace.status,
        error_type=trace.error_type,
        model=trace.model,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        cost_usd=float(trace.cost_usd) if trace.cost_usd is not None else None,
        latency_ms=trace.latency_ms,
        tools_used=len(trace.tool_calls or []),
        kb_chunks=int((trace.retrieval or {}).get("returned", 0)),
        route_reason=(trace.route or {}).get("reason"),
    )


def _trace_detail(trace: ChatTurnTrace) -> TraceDetailOut:
    return TraceDetailOut(
        id=trace.id,
        trace_id=trace.trace_id,
        created_at=trace.created_at,
        user_id=trace.user_id,
        session_id=trace.session_id,
        user_message_id=trace.user_message_id,
        assistant_message_id=trace.assistant_message_id,
        status=trace.status,
        error_type=trace.error_type,
        error_message=trace.error_message,
        model=trace.model,
        route=trace.route or {},
        retrieval=trace.retrieval or {},
        llm_calls=trace.llm_calls or [],
        tool_calls=trace.tool_calls or [],
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        cost_usd=float(trace.cost_usd) if trace.cost_usd is not None else None,
        latency_ms=trace.latency_ms,
    )


@router.get("/messages/{message_id}/trace", response_model=TraceDetailOut)
async def get_message_trace(
    message_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
    session: AsyncSession = Depends(get_db),
) -> TraceDetailOut:
    """La traza de una respuesta del asistente: la ve quien la recibio y
    los ADMIN/OWNER de la organizacion (para auditar). Para cualquier otro
    usuario la traza no existe (404)."""
    repository = ChatRepository(session)
    trace = await repository.get_trace_for_message(message_id, organization.id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Traza no encontrada")
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.OWNER)
    if trace.user_id != current_user.id and not is_admin:
        raise HTTPException(status_code=404, detail="Traza no encontrada")
    return _trace_detail(trace)


@router.post("/messages/{message_id}/feedback", response_model=FeedbackOut)
async def set_message_feedback(
    message_id: uuid.UUID,
    payload: FeedbackIn,
    current_user: User = Depends(get_current_user),
    organization: Organization = Depends(get_current_organization),
    session: AsyncSession = Depends(get_db),
) -> FeedbackOut:
    """Valorar una respuesta del asistente (1 util, -1 no util, null
    retira la valoracion). Solo sobre respuestas de conversaciones
    propias."""
    if payload.rating not in (1, -1, None):
        raise HTTPException(status_code=422, detail="rating debe ser 1, -1 o null")
    repository = ChatRepository(session)
    message = await repository.get_message(message_id, organization.id)
    chat_session = (
        await repository.get_session(message.session_id, organization.id) if message else None
    )
    if (
        message is None
        or message.role != MessageRole.ASSISTANT
        or chat_session is None
        or chat_session.user_id != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")
    comment = (payload.comment or "").strip() or None
    await repository.set_feedback(message, payload.rating, comment)
    return FeedbackOut(
        message_id=message.id, rating=message.feedback, comment=message.feedback_comment
    )


@router.get("/traces", response_model=TraceListOut)
async def list_traces(
    skip: int = 0,
    limit: int = 50,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(ok|error)$"),
    session_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TraceListOut:
    """Turnos recientes de TODA la organizacion (solo ADMIN/OWNER)."""
    repository = ChatRepository(session)
    rows, total = await repository.list_traces(
        admin.organization_id,
        skip=max(skip, 0),
        limit=max(1, min(limit, 100)),
        status=status_filter,
        session_id=session_id,
        user_id=user_id,
    )
    return TraceListOut(total=total, returned=len(rows), traces=[_trace_summary(t) for t in rows])


@router.get("/traces/stats", response_model=TraceStatsOut)
async def trace_stats(
    days: int = Query(default=7, ge=1, le=90),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TraceStatsOut:
    # Declarado ANTES de /traces/{trace_row_id}: FastAPI matchea en orden de declaracion.
    repository = ChatRepository(session)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stats = await repository.trace_stats(admin.organization_id, since)
    return TraceStatsOut(days=days, **stats)


@router.get("/traces/{trace_row_id}", response_model=TraceDetailOut)
async def get_trace(
    trace_row_id: uuid.UUID,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TraceDetailOut:
    trace = await ChatRepository(session).get_trace(trace_row_id, admin.organization_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Traza no encontrada")
    return _trace_detail(trace)


@router.get("/actions/pending", response_model=list[PendingActionOut])
async def list_pending_actions(
    session_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service),
) -> list[PendingActionOut]:
    """Acciones destructivas que el agente pidio y esperan tu decision."""
    return await chat_service.list_pending_actions(current_user, session_id)


@router.post("/actions/{action_id}/approve", response_model=PendingActionOut)
async def approve_action(
    action_id: uuid.UUID,
    current_user: User = Depends(require_password_changed),
    chat_service: ChatService = Depends(get_chat_service),
) -> PendingActionOut:
    """Aprobar y ejecutar UNA accion pendiente. Exige tu sesion normal
    (no el token delegado del agente: ver get_current_user, que rechaza
    tokens delegados en /chat)."""
    try:
        return await chat_service.approve_action(current_user, action_id)
    except ActionNotFoundError:
        raise HTTPException(status_code=404, detail="Acción no encontrada")
    except ActionNotPendingError as exc:
        raise HTTPException(status_code=409, detail=f"La acción ya no está pendiente ({exc.status})")


@router.post("/actions/{action_id}/reject", response_model=PendingActionOut)
async def reject_action(
    action_id: uuid.UUID,
    current_user: User = Depends(require_password_changed),
    chat_service: ChatService = Depends(get_chat_service),
) -> PendingActionOut:
    try:
        return await chat_service.reject_action(current_user, action_id)
    except ActionNotFoundError:
        raise HTTPException(status_code=404, detail="Acción no encontrada")
    except ActionNotPendingError as exc:
        raise HTTPException(status_code=409, detail=f"La acción ya no está pendiente ({exc.status})")
