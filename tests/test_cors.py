"""CORS middleware configured from settings.cors_origins in app.main."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest_asyncio.fixture
async def anon_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _preflight_headers(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }


@pytest.mark.asyncio
async def test_preflight_allows_configured_origin(anon_client):
    origin = settings.cors_origins[0]

    response = await anon_client.options("/health/live", headers=_preflight_headers(origin))

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_preflight_rejects_unknown_origin(anon_client):
    response = await anon_client.options(
        "/health/live", headers=_preflight_headers("http://evil.example")
    )

    assert "access-control-allow-origin" not in response.headers
