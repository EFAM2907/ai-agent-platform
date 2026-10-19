from __future__ import annotations


class DriveError(Exception):
    """Base de los errores de la integracion con Google Drive."""


class DriveNotConnectedError(DriveError):
    """La organizacion no tiene Drive conectado (o Google revoco el acceso)."""


class DriveFolderNotSetError(DriveError):
    """Drive esta conectado pero todavia no se eligio la carpeta a sincronizar."""


class DriveFolderError(DriveError):
    """La carpeta indicada no existe, no es una carpeta o la cuenta conectada no la ve."""


class DriveAPIError(DriveError):
    """Fallo de la Drive API o de la red hacia Google. Nunca incluye
    tokens ni contenido de documentos en el mensaje."""
