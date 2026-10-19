"""Politica de envios demorados, aplicada a datos reales.

Los umbrales vienen de la politica de la base de conocimiento
(kb_content/inter_rapidisimo/02-politica-retrasos-investigacion.md):

- Zona urbana (origen y destino en la misma ciudad): seguimiento
  preventivo a los 2 dias habiles sin evento nuevo; demorado a los 3.
- Zona nacional / intermunicipal: preventivo a los 3; demorado a los 5.
- Zonas rurales o de dificil acceso: +2 dias habiles sobre lo anterior.
- Mas de 10 dias habiles sin movimiento: ver el procedimiento de envios
  perdidos ("extreme").

Si la politica cambia, se cambia aca Y en el articulo de la KB: son dos
copias de la misma regla (una para que el modelo la cite, otra para
poder calcularla), a proposito -- el codigo no interpreta texto.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.business_days import business_days_between
from app.shipments.models import Shipment, ShipmentStatus

LEVEL_OK = "ok"
LEVEL_PREVENTIVE = "preventive"
LEVEL_DELAYED = "delayed"
LEVEL_EXTREME = "extreme"

EXTREME_BUSINESS_DAYS = 10
RURAL_EXTRA_DAYS = 2

# (preventivo, demorado) en dias habiles sin evento nuevo.
URBAN_THRESHOLDS = (2, 3)
NATIONAL_THRESHOLDS = (3, 5)

# Destinos de dificil acceso: sin via carreteable permanente, dependen de
# transporte aereo/fluvial o se entregan por punto aliado/en oficina
# (ver la hoja de cobertura de municipios de la KB).
HARD_TO_REACH_CITIES = frozenset(
    {
        "leticia", "mitu", "inirida", "puerto carreno", "san andres", "quibdo",
        "san jose del guaviare", "mocoa", "arauca", "florencia", "yopal", "riohacha",
        "popayan",
    }
)


def normalize_city(value: str) -> str:
    """Minusculas, sin tildes y sin el departamento entre parentesis:
    'Mitú' y 'mitu (Vaupés)' son la misma ciudad."""
    base = value.split("(")[0].strip().lower()
    decomposed = unicodedata.normalize("NFKD", base)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def is_hard_to_reach(city: str) -> bool:
    return normalize_city(city) in HARD_TO_REACH_CITIES


@dataclass(frozen=True)
class DelayAssessment:
    level: str
    business_days_without_event: int
    preventive_after: int
    delayed_after: int
    zone: str  # "urban" | "national" | "rural"

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "business_days_without_event": self.business_days_without_event,
            "preventive_after": self.preventive_after,
            "delayed_after": self.delayed_after,
            "zone": self.zone,
        }


def zone_for(shipment: Shipment) -> str:
    if is_hard_to_reach(shipment.destination_city):
        return "rural"
    if normalize_city(shipment.origin_city) == normalize_city(shipment.destination_city):
        return "urban"
    return "national"


def assess_delay(shipment: Shipment, now: datetime | None = None) -> DelayAssessment:
    """Evalua un envio contra la politica. Requiere shipment.events
    cargado (selectinload). Un envio ENTREGADO nunca esta demorado."""
    now = now or datetime.now(timezone.utc)
    zone = zone_for(shipment)
    preventive, delayed = URBAN_THRESHOLDS if zone == "urban" else NATIONAL_THRESHOLDS
    if zone == "rural":
        preventive, delayed = NATIONAL_THRESHOLDS[0] + RURAL_EXTRA_DAYS, NATIONAL_THRESHOLDS[1] + RURAL_EXTRA_DAYS

    if shipment.status == ShipmentStatus.ENTREGADO:
        return DelayAssessment(LEVEL_OK, 0, preventive, delayed, zone)

    last_event_at = max(
        (event.occurred_at for event in shipment.events), default=shipment.created_at
    )
    waited = business_days_between(last_event_at, now)

    if waited > EXTREME_BUSINESS_DAYS:
        level = LEVEL_EXTREME
    elif waited >= delayed:
        level = LEVEL_DELAYED
    elif waited >= preventive:
        level = LEVEL_PREVENTIVE
    else:
        level = LEVEL_OK
    return DelayAssessment(level, waited, preventive, delayed, zone)
