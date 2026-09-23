"""Cifrado simetrico para secretos que se persisten en la base de datos.

Usado para columnas como Organization.litellm_virtual_key: una virtual
key de LiteLLM es equivalente a una API key real (autentica y gasta
contra el budget del tenant), por lo que no debe quedar en texto plano
en la base de datos.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "No se pudo descifrar el secreto: token invalido o "
            "ENCRYPTION_KEY incorrecta"
        ) from exc
