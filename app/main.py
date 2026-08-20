from fastapi import FastAPI

from app.organizations.api import router as organizations_router
from app.users.api import router as users_router
from app.auth.api import router as auth_router

app = FastAPI(title="AI Agent Platform")

app.include_router(organizations_router)
app.include_router(users_router)
app.include_router(auth_router)