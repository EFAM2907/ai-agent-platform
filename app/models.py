"""Single registry of every ORM model.

Alembic's ``env.py`` (and anything else that needs the full schema) imports
this module so ``Base.metadata`` is complete. When you add ``app/<x>/models.py``,
import it here; ``tests/test_model_registry.py`` fails CI if you forget.
"""

from app.core.database import Base
from app.auth.models import RefreshToken
from app.chat.models import ChatMessage, ChatSession, ChatTurnTrace, PendingAction
from app.claims.models import Claim, ClaimEvent
from app.drive.models import DriveConnection
from app.gmail.models import GmailConnection, GmailProcessedMessage
from app.organizations.models import Organization
from app.rag.models import KBArticle, KBChunk
from app.shipments.models import Customer, Shipment, ShipmentEvent, ShipmentNotification
from app.users.models import User

__all__ = [
    "Base",
    "ChatMessage",
    "ChatSession",
    "ChatTurnTrace",
    "Claim",
    "ClaimEvent",
    "Customer",
    "DriveConnection",
    "GmailConnection",
    "GmailProcessedMessage",
    "KBArticle",
    "KBChunk",
    "Organization",
    "PendingAction",
    "RefreshToken",
    "Shipment",
    "ShipmentEvent",
    "ShipmentNotification",
    "User",
]
