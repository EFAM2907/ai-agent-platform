from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.organizations.api import router as organizations_router
from app.users.api import router as users_router
from app.auth.api import router as auth_router
from app.chat.api import router as chat_router
from app.gmail.api import router as gmail_router
from app.shipments.api import router as shipments_router
from app.rag.api import router as kb_router
from app.claims.api import router as claims_router
from app.drive.api import router as drive_router
from app.core.config import settings
from app.core.sts import jwks as sts_jwks
from app.health.api import router as health_router

app = FastAPI(title="AI Agent Platform", version="0.1.0")

# The React frontend runs on a different origin, so the browser blocks
# its calls unless that origin is allowed. Origins come from CORS_ORIGINS
# (default: the Vite dev server) instead of being hardcoded; production
# requires explicit https origins (see Settings validation).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/.well-known/jwks.json", tags=["auth"], include_in_schema=False)
async def jwks_endpoint() -> dict:
    """Claves publicas con las que el MCP server (resource server) valida
    los tokens delegados que emite app.core.sts."""
    return sts_jwks()


app.include_router(health_router)
app.include_router(organizations_router)
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(gmail_router)
app.include_router(shipments_router)
app.include_router(kb_router)
app.include_router(claims_router)
app.include_router(drive_router)