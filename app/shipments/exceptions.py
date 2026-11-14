"""Errores de negocio de envios y clientes. `status_code` es el HTTP que
corresponde: la API solo traduce, no decide (mismo patron que
app.claims.service.ClaimError)."""

from __future__ import annotations


class ShipmentError(Exception):
    status_code = 400


class CustomerNotFoundError(ShipmentError):
    status_code = 404


class ShipmentNotFoundError(ShipmentError):
    status_code = 404


class CustomerConflictError(ShipmentError):
    status_code = 409


class InvalidShipmentError(ShipmentError):
    status_code = 400


class NotificationNotPossibleError(ShipmentError):
    """El cliente no tiene correo, o Gmail no esta conectado."""

    status_code = 409


class NotificationLimitError(ShipmentError):
    status_code = 429
