import logging

from fastapi import Depends, HTTPException, Request, status

from app.core.dependencies import get_current_user
from app.core.redis_client import redis_client
from app.users.models import User

logger = logging.getLogger(__name__)

ORG_RATE_LIMIT = 100
ORG_WINDOW_SECONDS = 60

LOGIN_RATE_LIMIT = 5
LOGIN_WINDOW_SECONDS = 60

REFRESH_RATE_LIMIT = 10
REFRESH_WINDOW_SECONDS = 60


async def _check_fixed_window(key: str, max_requests: int, window_seconds: int) -> None:

    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window_seconds)
    except Exception:
        logger.warning("Rate limiter", exc_info=True)
        return

    if count > max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
            headers={"Retry-After": str(window_seconds)},
        )


async def rate_limit(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    key = f"rate_limit:org:{current_user.organization_id}"
    await _check_fixed_window(key, ORG_RATE_LIMIT, ORG_WINDOW_SECONDS)


async def login_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    key = f"rate_limit:login:ip:{client_ip}"
    await _check_fixed_window(key, LOGIN_RATE_LIMIT, LOGIN_WINDOW_SECONDS)


async def refresh_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    key = f"rate_limit:refresh:ip:{client_ip}"
    await _check_fixed_window(key, REFRESH_RATE_LIMIT, REFRESH_WINDOW_SECONDS)