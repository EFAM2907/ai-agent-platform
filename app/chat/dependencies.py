from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.repository import ChatRepository
from app.chat.service import CHEAP_MODEL, EXPENSIVE_MODEL, ChatService
from app.core.database import get_db
from app.llm.client import LLMClient
from app.llm.routing import ModelRouter
from app.organizations.dependencies import get_tenant_llm_client
from app.rag.dependencies import get_rag_service
from app.rag.service import RAGService


async def get_chat_service(
    session: AsyncSession = Depends(get_db),
    rag_service: RAGService = Depends(get_rag_service),
    llm_client: LLMClient = Depends(get_tenant_llm_client),
) -> ChatService:
    # Mismo llm_client que ya resuelve la virtual key del tenant -- el
    # router solo agrega la clasificacion barato/caro encima, no habla
    # con el gateway por su cuenta.
    #
    # Ya no construye UserService/ShipmentService/GmailConnectionService
    # -- ChatService dejo de llamarlas in-process (ver docs/roadmap.md
    # seccion 2): las tools de usuarios y envios ahora vienen del
    # servidor MCP (app.chat.mcp_tools.open_mcp_tools), que es quien
    # reenvia cada tool call a la API REST con el Bearer token del
    # usuario.
    model_router = ModelRouter(
        llm_client,
        cheap_model=CHEAP_MODEL,
        expensive_model=EXPENSIVE_MODEL,
    )
    return ChatService(
        repository=ChatRepository(session),
        rag_service=rag_service,
        llm_client=llm_client,
        model_router=model_router,
        session=session,
    )
