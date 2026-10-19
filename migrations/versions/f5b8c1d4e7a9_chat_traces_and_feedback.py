"""chat: traza por turno y feedback por mensaje

Revision ID: f5b8c1d4e7a9
Revises: e3f6a9b2c5d7
Create Date: 2026-10-15 00:00:00.000000

chat_turn_traces: una fila por turno de chat (exitoso o fallido) con TODO
lo que hace falta para auditar una respuesta sin depender de un servicio
externo: que modelo se eligio y por que, que fragmentos de la KB entraron
al contexto, que herramientas se ejecutaron (y si salieron bien), cuantos
tokens y cuanto costo. El trace_id es el MISMO que se manda a Langfuse
via LiteLLM, asi se puede saltar de una traza a la otra.

chat_messages.feedback: pulgar arriba/abajo del usuario sobre una
respuesta, para saber que respuestas fallan de verdad.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5b8c1d4e7a9"
down_revision: Union[str, Sequence[str], None] = "e3f6a9b2c5d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_turn_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"), nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "assistant_message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("route", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("retrieval", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("llm_calls", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index(
        "ix_chat_turn_traces_org_created", "chat_turn_traces", ["organization_id", "created_at"]
    )
    op.create_index("ix_chat_turn_traces_session", "chat_turn_traces", ["session_id"])
    op.create_index("ix_chat_turn_traces_trace_id", "chat_turn_traces", ["trace_id"])
    op.create_index(
        "ix_chat_turn_traces_assistant_message", "chat_turn_traces", ["assistant_message_id"]
    )

    op.add_column("chat_messages", sa.Column("feedback", sa.SmallInteger(), nullable=True))
    op.add_column("chat_messages", sa.Column("feedback_comment", sa.Text(), nullable=True))
    op.add_column(
        "chat_messages", sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_chat_messages_feedback", "chat_messages", "feedback IN (-1, 1)"
    )
    # Historial de una sesion en orden: se pide en cada turno (ultimos N mensajes).
    op.create_index(
        "ix_chat_messages_session_created", "chat_messages", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_constraint("ck_chat_messages_feedback", "chat_messages", type_="check")
    op.drop_column("chat_messages", "feedback_at")
    op.drop_column("chat_messages", "feedback_comment")
    op.drop_column("chat_messages", "feedback")
    op.drop_index("ix_chat_turn_traces_assistant_message", table_name="chat_turn_traces")
    op.drop_index("ix_chat_turn_traces_trace_id", table_name="chat_turn_traces")
    op.drop_index("ix_chat_turn_traces_session", table_name="chat_turn_traces")
    op.drop_index("ix_chat_turn_traces_org_created", table_name="chat_turn_traces")
    op.drop_table("chat_turn_traces")
