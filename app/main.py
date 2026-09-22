from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.organizations.api import router as organizations_router
from app.users.api import router as users_router
from app.auth.api import router as auth_router
from app.chat.api import router as chat_router
from app.gmail.api import router as gmail_router

app = FastAPI(title="AI Agent Platform")

# El frontend de React corre en un puerto/origen distinto (Vite dev
# server) -- sin esto el navegador bloquea las llamadas al chat antes
# de que lleguen a la API. Restringido a localhost:5173 (default de
# Vite) en vez de "*" porque este endpoint ya requiere el Bearer token
# de auth igual, pero no hay razon para abrir CORS mas de lo que el
# dev server realmente necesita.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(organizations_router)
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(gmail_router)