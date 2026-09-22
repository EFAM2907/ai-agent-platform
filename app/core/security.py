from datetime import datetime, timedelta, timezone
import jwt
from app.core.config import settings
from app.core.exceptions import InvalidTokenError
import hashlib
import secrets

# --- Password hashing ---

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

ph = PasswordHasher()  # usa los defaults recomendados por OWASP

def hash_password(password: str) -> str:
    return ph.hash(password)


def generate_temporary_password() -> str:
    """Contraseña temporal para cuentas creadas por un admin/owner (o
    por la tool create_user del agente de chat) -- nunca una
    contraseña elegida a mano ni compartida entre usuarios. Se muestra
    en texto plano una sola vez, en la respuesta de esa misma llamada
    (nunca se persiste ni se loguea en claro), y el usuario queda
    obligado a cambiarla (User.must_change_password) antes de poder
    usar el chat -- ver require_password_changed."""
    return secrets.token_urlsafe(12)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# --- JWT ---

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update({
        "exp": expire,
        "iat": now,
    })

    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        print(f'esto es : {payload}')
        return payload
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("Token has expired")
    except jwt.InvalidTokenError:
        raise InvalidTokenError("Invalid token")
    
    
# --- Refresh Token ---


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(64)  # token aleatorio, criptográficamente seguro

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()