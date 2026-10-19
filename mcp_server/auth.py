"""Autenticacion/autorizacion del MCP server como resource server OAuth.

Ya NO reenvia ciegamente el JWT del usuario. Cada tool call debe traer
un token delegado emitido por el STS de la API (app/core/sts.py,
RFC 8693 token exchange):

- firma RS256 validada con la JWKS publica de la API (sin secretos
  compartidos);
- `iss` y `aud` (= mcp-server) correctos, `exp` vigente;
- el `scope` del token debe incluir el scope de la tool llamada;
- las tools destructivas (`confirm=True`) exigen ademas la aprobacion
  humana de ESA llamada: claims `approved_action` y `args_hash` (hash de
  los argumentos exactos). Aunque el agente (o un atacante con un token
  normal) pida la tool, sin aprobacion no se ejecuta.

Cada llamada deja un log estructurado con actor (quien llama) y sujeto
(en nombre de quien).
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import json
import logging
import os
from typing import Any, Callable

import jwt
from fastmcp.server.dependencies import get_http_headers

logger = logging.getLogger("mcp_server.audit")

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
JWKS_URL = os.environ.get("STS_JWKS_URL", f"{API_BASE_URL}/.well-known/jwks.json")
ISSUER = os.environ.get("STS_ISSUER", "http://localhost:8000")
AUDIENCE = os.environ.get("MCP_AUDIENCE", "mcp-server")

# El token delegado ya validado de la llamada en curso: _call lo reenvia
# a la API REST (que lo vuelve a validar y resuelve al usuario).
_current_token: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_delegated_token")

_jwks_client: jwt.PyJWKClient | None = None


def _client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=300)
    return _jwks_client


def args_hash(arguments: dict[str, Any]) -> str:
    """Mismo calculo que app.core.sts.args_hash."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_token(token: str) -> dict[str, Any]:
    """Valida firma, iss, aud y exp. Lanza PermissionError si algo falla."""
    try:
        signing_key = _client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iss", "aud", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise PermissionError(f"Token invalido para este servidor MCP: {exc}") from exc


def _bearer_token() -> str:
    header = get_http_headers(include={"authorization"}).get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise PermissionError(
            "Falta el header Authorization: Bearer <token delegado> -- este servidor "
            "MCP no acepta llamadas anonimas"
        )
    return token


def current_token() -> str:
    try:
        return _current_token.get()
    except LookupError:
        raise PermissionError(
            "No hay un token delegado validado para esta llamada: la tool debe "
            "declararse con @tool(mcp, scope)"
        ) from None


def tool(mcp: Any, scope: str, *, confirm: bool = False) -> Callable[[Callable], Any]:
    """`@mcp.tool` que antes de ejecutar la funcion exige un token
    delegado valido con `scope` (y, si `confirm`, la aprobacion humana
    de esta llamada exacta)."""

    def decorator(fn: Callable) -> Any:
        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            token = _bearer_token()
            claims = await asyncio.to_thread(validate_token, token)
            actor = (claims.get("act") or {}).get("sub")
            audit = {
                "tool": fn.__name__,
                "scope": scope,
                "actor": actor,
                "sub": claims.get("sub"),
                "jti": claims.get("jti"),
                "approved_action": claims.get("approved_action"),
            }
            granted = set(str(claims.get("scope", "")).split())
            if scope not in granted:
                logger.warning("tool_denied reason=missing_scope %s", audit)
                raise PermissionError(f"El token no tiene el scope '{scope}' que exige {fn.__name__}")
            if confirm and (
                not claims.get("approved_action")
                or claims.get("args_hash") != args_hash(kwargs)
            ):
                logger.warning("tool_denied reason=not_approved %s", audit)
                raise PermissionError(
                    f"{fn.__name__} es una accion destructiva: requiere la aprobacion "
                    "explicita del usuario para estos argumentos exactos"
                )
            reset = _current_token.set(token)
            try:
                result = await fn(**kwargs)
            except Exception:
                logger.warning("tool_error %s", audit)
                raise
            finally:
                _current_token.reset(reset)
            logger.info("tool_call %s", audit)
            return result

        return mcp.tool(wrapper)

    return decorator
