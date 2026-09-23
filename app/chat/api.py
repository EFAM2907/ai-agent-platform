from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.dependencies import get_chat_service
from app.chat.repository import ChatRepository
from app.chat.schemas import (
    ChatMessageIn,
    ChatMessageOut,
    ChatReplyOut,
    ChatSessionOut,
    ChatSessionRenameIn,
    ChatSessionSummaryOut,
)
from app.chat.service import ChatService
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_password_changed
from app.organizations.dependencies import get_current_organization
from app.organizations.models import Organization
from app.users.models import User

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
    return ChatReplyOut(session_id=assistant_message.session_id, message=message_out)


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
            }
            for m in messages
        ],
    )
