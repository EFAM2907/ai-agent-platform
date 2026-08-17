from fastapi import FastAPI

from app.organizations.api import router as organizations_router

app = FastAPI(title="AI Agent Platform")

app.include_router(organizations_router)