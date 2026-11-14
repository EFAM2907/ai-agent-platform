"""Operational health endpoints for the deployment platform.

- GET /health/live: liveness. Touches no dependency, so a hung database
  never gets the process restarted by the orchestrator.
- GET /health: readiness. Checks each dependency in parallel with its
  own timeout. Only the database decides the status code: Redis backs a
  fail-open rate limiter and LiteLLM outages are handled per request, so
  both only mark the response as "degraded".

These endpoints are intentionally unauthenticated and never expose error
messages, versions, hosts or URL fragments: failure details go to the
log only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Per-dependency budget. Kept well under typical orchestrator probe
# timeouts so a slow dependency cannot make the probe itself time out.
CHECK_TIMEOUT_SECONDS = 2.0

# LiteLLM's readiness endpoint verifies the proxy and its own database
# without calling any model provider (unlike /health, which needs the
# master key and sends real requests to every configured model).
LITELLM_READINESS_PATH = "/health/readiness"


async def _check_database() -> None:
    # Reuses the application's engine/pool: no new pool per request.
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _check_redis() -> None:
    # Reuses the shared client also used by the rate limiter.
    await redis_client.ping()


async def _check_litellm() -> None:
    url = settings.litellm_base_url.rstrip("/") + LITELLM_READINESS_PATH
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(url)
        response.raise_for_status()


async def _run_check(name: str, check: Callable[[], Awaitable[None]]) -> bool:
    """True when the dependency is healthy. Never raises."""
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        return True
    except asyncio.TimeoutError:
        logger.warning("Health check '%s' timed out after %.1fs", name, CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        # Class name + message only (no traceback, no connection URLs).
        logger.warning("Health check '%s' failed: %s: %s", name, type(exc).__name__, exc)
    return False


@router.get("/health/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    # Looked up at call time (not captured at import) so tests can
    # replace individual checks.
    database_ok, redis_ok, litellm_ok = await asyncio.gather(
        _run_check("database", _check_database),
        _run_check("redis", _check_redis),
        _run_check("litellm", _check_litellm),
    )

    if not database_ok:
        status, code = "unavailable", 503
    elif redis_ok and litellm_ok:
        status, code = "ok", 200
    else:
        status, code = "degraded", 200

    return JSONResponse(
        status_code=code,
        content={
            "status": status,
            "database": "ok" if database_ok else "error",
            "redis": "ok" if redis_ok else "degraded",
            "litellm": "ok" if litellm_ok else "degraded",
        },
    )
