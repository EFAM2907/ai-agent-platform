"""Health endpoints (app.health.api). No test needs a live Redis or
LiteLLM: individual checks are replaced; the database check runs against
the test database through a dedicated engine."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.health import api as health_api
from app.main import app
from tests.conftest import TEST_DATABASE_URL

SECRET_ERROR_TEXT = "password authentication failed for user secret-user@db.internal"


@pytest_asyncio.fixture
async def anon_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise RuntimeError(SECRET_ERROR_TEXT)


@pytest_asyncio.fixture
async def real_db_engine(monkeypatch):
    # The app's module-level engine is bound to the first event loop that
    # uses it; a per-test NullPool engine avoids cross-loop reuse.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(health_api, "engine", engine)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_returns_200_without_touching_dependencies(anon_client, monkeypatch):
    monkeypatch.setattr(health_api, "_check_database", _boom)
    monkeypatch.setattr(health_api, "_check_redis", _boom)
    monkeypatch.setattr(health_api, "_check_litellm", _boom)

    response = await anon_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ok_with_database_up(anon_client, real_db_engine, monkeypatch):
    monkeypatch.setattr(health_api, "_check_redis", _ok)
    monkeypatch.setattr(health_api, "_check_litellm", _ok)

    response = await anon_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "redis": "ok",
        "litellm": "ok",
    }


@pytest.mark.asyncio
async def test_health_503_when_database_fails_and_hides_error(anon_client, monkeypatch, caplog):
    monkeypatch.setattr(health_api, "_check_database", _boom)
    monkeypatch.setattr(health_api, "_check_redis", _ok)
    monkeypatch.setattr(health_api, "_check_litellm", _ok)

    with caplog.at_level("WARNING"):
        response = await anon_client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["database"] == "error"
    assert "password" not in response.text
    assert "secret-user" not in response.text
    # The detail is only in the log.
    assert SECRET_ERROR_TEXT in caplog.text


@pytest.mark.asyncio
async def test_health_503_when_database_times_out(anon_client, monkeypatch):
    async def slow() -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(health_api, "CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(health_api, "_check_database", slow)
    monkeypatch.setattr(health_api, "_check_redis", _ok)
    monkeypatch.setattr(health_api, "_check_litellm", _ok)

    response = await anon_client.get("/health")

    assert response.status_code == 503
    assert response.json()["database"] == "error"


@pytest.mark.asyncio
async def test_health_degraded_but_200_when_redis_fails(anon_client, monkeypatch):
    monkeypatch.setattr(health_api, "_check_database", _ok)
    monkeypatch.setattr(health_api, "_check_redis", _boom)
    monkeypatch.setattr(health_api, "_check_litellm", _ok)

    response = await anon_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["redis"] == "degraded"
    assert body["database"] == "ok"
    assert SECRET_ERROR_TEXT not in response.text


@pytest.mark.asyncio
async def test_health_degraded_but_200_when_litellm_fails(anon_client, monkeypatch):
    monkeypatch.setattr(health_api, "_check_database", _ok)
    monkeypatch.setattr(health_api, "_check_redis", _ok)
    monkeypatch.setattr(health_api, "_check_litellm", _boom)

    response = await anon_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["litellm"] == "degraded"
