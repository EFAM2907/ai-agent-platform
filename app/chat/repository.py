from __future__ import annotations

import uuid

from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.models import (
    ChatMessage,
    ChatSession,
    ChatTurnTrace,
    MessageRole,
    PendingAction,
    PendingActionStatus,
)


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

    async def get_message(
        self, message_id: uuid.UUID, organization_id: uuid.UUID
    ) -> ChatMessage | None:
        stmt = select(ChatMessage).where(
            ChatMessage.id == message_id, ChatMessage.organization_id == organization_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_feedback(
        self, message: ChatMessage, rating: int | None, comment: str | None
    ) -> ChatMessage:
        """rating None borra la valoracion (el usuario se arrepintio)."""
        message.feedback = rating
        message.feedback_comment = comment if rating is not None else None
        message.feedback_at = datetime.now(timezone.utc) if rating is not None else None
        await self.session.commit()
        return message

    # ------------------------------------------------------------------
    # Trazas por turno
    # ------------------------------------------------------------------
    async def add_trace(self, trace: ChatTurnTrace) -> ChatTurnTrace:
        self.session.add(trace)
        await self.session.flush()
        return trace

    async def get_trace_for_message(
        self, assistant_message_id: uuid.UUID, organization_id: uuid.UUID
    ) -> ChatTurnTrace | None:
        stmt = select(ChatTurnTrace).where(
            ChatTurnTrace.assistant_message_id == assistant_message_id,
            ChatTurnTrace.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_trace(
        self, trace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> ChatTurnTrace | None:
        stmt = select(ChatTurnTrace).where(
            ChatTurnTrace.id == trace_id, ChatTurnTrace.organization_id == organization_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_traces(
        self,
        organization_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
        status: str | None = None,
        session_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> tuple[list[ChatTurnTrace], int]:
        conditions = [ChatTurnTrace.organization_id == organization_id]
        if status:
            conditions.append(ChatTurnTrace.status == status)
        if session_id:
            conditions.append(ChatTurnTrace.session_id == session_id)
        if user_id:
            conditions.append(ChatTurnTrace.user_id == user_id)
        stmt = (
            select(ChatTurnTrace)
            .where(*conditions)
            .order_by(ChatTurnTrace.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        total = (
            await self.session.execute(select(func.count(ChatTurnTrace.id)).where(*conditions))
        ).scalar_one()
        return rows, total

    async def trace_stats(self, organization_id: uuid.UUID, since: datetime) -> dict:
        """Agregados de los turnos desde `since`: volumen, errores,
        latencia, tokens, costo, uso por modelo y por herramienta, y
        valoraciones de los usuarios."""
        params = {"org": organization_id, "since": since}
        totals = (
            await self.session.execute(
                text(
                    "SELECT count(*) AS turns, "
                    "count(*) FILTER (WHERE status = 'error') AS errors, "
                    "coalesce(avg(latency_ms), 0)::int AS avg_latency_ms, "
                    "coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0)::int AS p95_latency_ms, "
                    "coalesce(sum(input_tokens), 0) AS input_tokens, "
                    "coalesce(sum(output_tokens), 0) AS output_tokens, "
                    "sum(cost_usd) AS cost_usd "
                    "FROM chat_turn_traces WHERE organization_id = :org AND created_at >= :since"
                ),
                params,
            )
        ).mappings().one()
        by_model = (
            await self.session.execute(
                text(
                    "SELECT coalesce(model, 'desconocido') AS name, count(*) AS count "
                    "FROM chat_turn_traces WHERE organization_id = :org AND created_at >= :since "
                    "GROUP BY 1 ORDER BY 2 DESC"
                ),
                params,
            )
        ).mappings().all()
        by_tool = (
            await self.session.execute(
                text(
                    "SELECT t->>'name' AS name, count(*) AS count, "
                    "count(*) FILTER (WHERE (t->>'ok')::boolean = false) AS failed "
                    "FROM chat_turn_traces, jsonb_array_elements(tool_calls) AS t "
                    "WHERE organization_id = :org AND created_at >= :since "
                    "GROUP BY 1 ORDER BY 2 DESC"
                ),
                params,
            )
        ).mappings().all()
        feedback = (
            await self.session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE feedback = 1) AS positive, "
                    "count(*) FILTER (WHERE feedback = -1) AS negative "
                    "FROM chat_messages WHERE organization_id = :org AND feedback_at >= :since"
                ),
                params,
            )
        ).mappings().one()
        return {
            "turns": totals["turns"],
            "errors": totals["errors"],
            "avg_latency_ms": totals["avg_latency_ms"],
            "p95_latency_ms": totals["p95_latency_ms"],
            "input_tokens": int(totals["input_tokens"]),
            "output_tokens": int(totals["output_tokens"]),
            "cost_usd": float(totals["cost_usd"]) if totals["cost_usd"] is not None else None,
            "by_model": [dict(row) for row in by_model],
            "by_tool": [dict(row) for row in by_tool],
            "feedback_positive": feedback["positive"],
            "feedback_negative": feedback["negative"],
        }

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


    # -- acciones pendientes de aprobacion humana --------------------------
    async def create_pending_action(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None,
        trace_id: str | None,
        tool_name: str,
        arguments: dict,
        expires_at: datetime,
    ) -> PendingAction:
        action = PendingAction(
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            tool_name=tool_name,
            arguments=arguments,
            expires_at=expires_at,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def get_pending_action(
        self, action_id: uuid.UUID, user_id: uuid.UUID
    ) -> PendingAction | None:
        result = await self.session.execute(
            select(PendingAction).where(
                PendingAction.id == action_id, PendingAction.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_pending_actions(
        self, user_id: uuid.UUID, session_id: uuid.UUID | None = None
    ) -> list[PendingAction]:
        query = select(PendingAction).where(
            PendingAction.user_id == user_id,
            PendingAction.status == PendingActionStatus.PENDING.value,
            PendingAction.expires_at > datetime.now(timezone.utc),
        )
        if session_id is not None:
            query = query.where(PendingAction.session_id == session_id)
        result = await self.session.execute(query.order_by(PendingAction.created_at))
        return list(result.scalars().all())

    async def decide_pending_action(
        self, action_id: uuid.UUID, user_id: uuid.UUID, new_status: PendingActionStatus
    ) -> bool:
        """Transicion atomica pending -> new_status (solo si sigue
        pendiente, es del usuario y no vencio). Evita la doble ejecucion
        si el usuario aprueba dos veces o en dos pestanas a la vez."""
        result = await self.session.execute(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.status == PendingActionStatus.PENDING.value,
                PendingAction.expires_at > datetime.now(timezone.utc),
            )
            .values(status=new_status.value, decided_at=datetime.now(timezone.utc))
        )
        return result.rowcount == 1

    async def finish_pending_action(
        self, action_id: uuid.UUID, status: PendingActionStatus, result_payload: object
    ) -> None:
        await self.session.execute(
            update(PendingAction)
            .where(PendingAction.id == action_id)
            .values(status=status.value, result=result_payload)
        )
