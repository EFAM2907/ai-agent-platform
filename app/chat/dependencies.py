from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.repository import ChatRepository
from app.chat.service import CHEAP_MODEL, EXPENSIVE_MODEL, ChatService
from app.core.database import get_db
from app.gmail.repository import GmailRepository
from app.gmail.service import GmailConnectionService
from app.llm.client import LLMClient
from app.llm.routing import ModelRouter
from app.organizations.dependencies import get_tenant_llm_client
from app.rag.dependencies import get_rag_service
from app.rag.service import RAGService
from app.shipments.repository import ShipmentRepository
from app.shipments.service import ShipmentService
from app.users.repository import UserRepository
from app.users.service import UserService


async def get_chat_service(
    session: AsyncSession = Depends(get_db),
    rag_service: RAGService = Depends(get_rag_service),
    llm_client: LLMClient = Depends(get_tenant_llm_client),
) -> ChatService:
    # Mismo llm_client que ya resuelve la virtual key del tenant -- el
    # router solo agrega la clasificacion barato/caro encima, no habla
    # con el gateway por su cuenta.
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
        user_service=UserService(UserRepository(session), session),
        shipment_service=ShipmentService(ShipmentRepository(session), session),
        # create_user() solo lo usa si la organizacion realmente tiene
        # Gmail conectado (ver GmailConnectionService.get_active_connection);
        # instanciarlo aca no dispara ninguna llamada de red.
        gmail_service=GmailConnectionService(GmailRepository(session), session),
    )
