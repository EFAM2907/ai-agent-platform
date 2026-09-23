from __future__ import annotations


class GmailError(Exception):
    """Base de los errores de la integracion con Gmail."""


class GmailNotConfiguredError(GmailError):
    """GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET no estan configurados."""


class GmailNotConnectedError(GmailError):
    """La organizacion no tiene una conexion de Gmail activa."""


class GmailAuthorizationError(GmailError):
    """El flujo OAuth no se pudo completar: state invalido/vencido,
    codigo rechazado por Google, o scopes concedidos insuficientes."""


class GmailAccessRevokedError(GmailError):
    """Google rechazo el refresh token (invalid_grant): el usuario
    revoco el acceso o el token vencio. Requiere reconectar."""


class GmailAPIError(GmailError):
    """Fallo de la Gmail API o de la red hacia Google. Nunca incluye
    tokens ni cuerpos de correo en el mensaje."""
