"""STS minimo (Security Token Service) de la plataforma: token exchange
RFC 8693 en proceso.

El JWT de sesion del usuario (HS256, ver app.core.security) NUNCA sale
de la API. Cuando ChatService necesita llamar al MCP server, intercambia
la identidad del usuario por un token delegado:

- RS256, firmado aca; el MCP server lo valida con la JWKS publica
  (GET /.well-known/jwks.json), sin compartir ningun secreto.
- `aud` = mcp_audience: no sirve en ningun otro resource server.
- `scope` = interseccion de lo pedido y lo que el rol del usuario
  permite (app.core.scopes).
- `sub` = el usuario; `act` = {"sub": "chat-service"}: el agente actua
  EN NOMBRE del usuario, y queda registrado asi.
- `exp` corto, `jti` unico.
- Para una accion destructiva aprobada por el humano, el token lleva
  ademas `approved_action` y `args_hash`: autoriza UNA llamada concreta.

Disenado para poder reemplazarse por un IdP real (Keycloak, Auth0...):
quien valida solo necesita iss/JWKS/aud/scope.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings
from app.core.exceptions import InvalidTokenError
from app.core.scopes import scopes_for_role

logger = logging.getLogger(__name__)

ACTOR_CHAT_SERVICE = "chat-service"
ALGORITHM = "RS256"


def args_hash(arguments: dict[str, Any]) -> str:
    """Hash canonico de los argumentos de una tool call -- el MCP server
    calcula exactamente lo mismo (mcp_server/auth.py)."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@lru_cache(maxsize=1)
def _private_key() -> rsa.RSAPrivateKey:
    if settings.sts_private_key:
        pem = settings.sts_private_key.replace("\\n", "\n").encode()
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RuntimeError("STS_PRIVATE_KEY debe ser una clave RSA")
        return key
    if settings.environment != "development":
        raise RuntimeError("STS_PRIVATE_KEY es obligatoria fuera de development")
    logger.warning(
        "STS_PRIVATE_KEY no esta configurada: se genera una clave RSA efimera "
        "(los tokens delegados dejan de validar al reiniciar la API)"
    )
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks() -> dict[str, Any]:
    public_key = _private_key().public_key()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": settings.sts_key_id, "use": "sig", "alg": ALGORITHM})
    return {"keys": [jwk]}


def issue_delegated_token(
    user: Any,
    requested_scopes: set[str] | frozenset[str],
    *,
    approved_action: str | None = None,
    approved_args_hash: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Token exchange: identidad de `user` -> token delegado para el MCP
    server. Los scopes se recortan al rol del usuario (principio de
    minimo privilegio: pedir de mas no concede de mas)."""
    granted = set(requested_scopes) & scopes_for_role(user.role)
    now = datetime.now(timezone.utc)
    ttl = ttl_seconds or settings.delegated_token_ttl_seconds
    claims: dict[str, Any] = {
        "iss": settings.sts_issuer,
        "aud": settings.mcp_audience,
        "sub": str(user.id),
        "organization_id": str(user.organization_id),
        "scope": " ".join(sorted(granted)),
        "act": {"sub": ACTOR_CHAT_SERVICE},
        "jti": uuid.uuid4().hex,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl),
    }
    if approved_action:
        claims["approved_action"] = approved_action
        claims["args_hash"] = approved_args_hash
    return jwt.encode(
        claims, _private_key(), algorithm=ALGORITHM, headers={"kid": settings.sts_key_id}
    )


def is_delegated_token(token: str) -> bool:
    """True si el token parece un token delegado (por `aud`, sin
    verificar nada todavia) -- solo para elegir con que clave validarlo."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return False
    aud = claims.get("aud")
    return aud == settings.mcp_audience or (
        isinstance(aud, list) and settings.mcp_audience in aud
    )


def decode_delegated_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _private_key().public_key(),
            algorithms=[ALGORITHM],
            audience=settings.mcp_audience,
            issuer=settings.sts_issuer,
            options={"require": ["exp", "iss", "aud", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("Token has expired")
    except jwt.InvalidTokenError:
        raise InvalidTokenError("Invalid token")
