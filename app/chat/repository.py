from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.models import ChatMessage, ChatSession, MessageRole


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> ChatSession:
        chat_session = ChatSession(organization_id=organization_id, user_id=user_id)
        self.session.add(chat_session)
        await self.session.flush()
        return chat_session

    async def get_session(
        self, session_id: uuid.UUID, organization_id: uuid.UUID
    ) -> ChatSession | None:
        # Filtra por organization_id en el WHERE, no solo por id --
        # mismo principio que verify_same_organization en el resto
        # del proyecto: un session_id de otro tenant nunca debe
        # resolver aca, ni siquiera para devolver un 404 mas "honesto".
        stmt = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_sessions(
        self, organization_id: uuid.UUID, user_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> list[ChatSession]:
        """Historial de conversaciones para el sidebar -- filtrado por
        user_id ademas de organization_id: el historial es personal
        (cada quien ve sus propias conversaciones, no las de todo el
        tenant), mismo criterio que get_session ya aplica para
        organization_id."""
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.organization_id == organization_id,
                ChatSession.user_id == user_id,
            )
            .order_by(ChatSession.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def rename_session(
        self,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        title: str,
    ) -> ChatSession | None:
        """Filtra tambien por user_id, mismo criterio que list_sessions
        -- renombrar es organizar tu propio historial, nunca el de
        otro miembro del tenant. Devuelve None si no existe o no es
        del dueno, para que la API decida el 404."""
        chat_session = await self.get_session(session_id, organization_id)
        if chat_session is None or chat_session.user_id != user_id:
            return None
        chat_session.title = title
        await self.session.commit()
        await self.session.refresh(chat_session)
        return chat_session

    async def delete_session(
        self, session_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """DELETE a nivel SQL (no session.delete() sobre el objeto ORM)
        a proposito: session.delete() dispararia un lazy-load de
        `messages` para aplicar el cascade="all, delete-orphan" de
        Python, y en el mundo async eso revienta con MissingGreenlet
        si la relacion no esta pre-cargada. El ON DELETE CASCADE ya
        declarado en ChatMessage.session_id (ver models.py) se encarga
        de borrar los mensajes en la base de datos sin que el ORM
        tenga que saber nada de ellos. Devuelve False sin lanzar si no
        existe o no es del dueno."""
        stmt = delete(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.organization_id == organization_id,
            ChatSession.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def add_message(
        self,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        role: MessageRole,
        content: str,
        sources: list[str] | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            organization_id=organization_id,
            role=role,
            content=content,
            sources="|".join(sources) if sources else None,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages(
        self, session_id: uuid.UUID, organization_id: uuid.UUID
    ) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.organization_id == organization_id,
            )
            .order_by(ChatMessage.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def recent_messages(
        self, session_id: uuid.UUID, organization_id: uuid.UUID, limit: int = 10
    ) -> list[ChatMessage]:
        """Ultimos `limit` mensajes en orden cronologico -- usado como
        historial de conversacion para el LLM (ChatService), no para
        mostrar en UI (ahi list_messages, sin limite)."""
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.organization_id == organization_id,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(reversed(result.scalars().all()))
