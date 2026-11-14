"""Reglas puras: dias habiles y politica de envios demorados de la KB."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.business_days import add_business_days, business_days_between
from app.shipments.delays import (
    LEVEL_DELAYED,
    LEVEL_EXTREME,
    LEVEL_OK,
    LEVEL_PREVENTIVE,
    assess_delay,
    is_hard_to_reach,
    normalize_city,
    zone_for,
)
from app.shipments.models import ShipmentStatus


def dt(y, m, d, h=10):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


# 2026-10-05 es lunes.
def test_business_days_between_skips_weekends():
    assert business_days_between(dt(2026, 10, 5), dt(2026, 10, 7)) == 2  # lun -> mie
    assert business_days_between(dt(2026, 10, 9), dt(2026, 10, 12)) == 1  # vie -> lun
    assert business_days_between(dt(2026, 10, 10), dt(2026, 10, 11)) == 0  # sab -> dom
    assert business_days_between(dt(2026, 10, 5), dt(2026, 10, 19)) == 10  # dos semanas
    assert business_days_between(dt(2026, 10, 7), dt(2026, 10, 5)) == 0  # al reves


def test_add_business_days_skips_weekends_and_keeps_the_hour():
    assert add_business_days(dt(2026, 10, 8, 15), 1) == dt(2026, 10, 9, 15)  # jue -> vie
    assert add_business_days(dt(2026, 10, 9, 15), 1) == dt(2026, 10, 12, 15)  # vie -> lun
    assert add_business_days(dt(2026, 10, 10, 9), 3) == dt(2026, 10, 14, 9)  # sab -> mie
    assert add_business_days(dt(2026, 10, 5), 0) == dt(2026, 10, 5)


def test_city_normalization_ignores_accents_case_and_department():
    assert normalize_city("Mitú") == "mitu"
    assert normalize_city("Honda (Tolima)") == "honda"
    assert normalize_city("  BOGOTÁ ") == "bogota"
    assert is_hard_to_reach("Leticia") and is_hard_to_reach("inírida")
    assert not is_hard_to_reach("Medellín")


def _shipment(*, origin="Bogotá", destination="Medellín", status=ShipmentStatus.EN_TRANSITO, last_event=None):
    events = [SimpleNamespace(occurred_at=last_event)] if last_event else []
    return SimpleNamespace(
        origin_city=origin, destination_city=destination, status=status,
        events=events, created_at=dt(2026, 9, 1),
    )


def test_zones():
    assert zone_for(_shipment(origin="Bogotá", destination="Bogotá")) == "urban"
    assert zone_for(_shipment(origin="Bogotá", destination="Cali")) == "national"
    assert zone_for(_shipment(origin="Bogotá", destination="Mitú")) == "rural"


@pytest.mark.parametrize(
    "origin, destination, waited_days_from_monday, expected",
    [
        # Urbano: preventivo a 2, demorado a 3.
        ("Bogotá", "Bogotá", 1, LEVEL_OK),
        ("Bogotá", "Bogotá", 2, LEVEL_PREVENTIVE),
        ("Bogotá", "Bogotá", 3, LEVEL_DELAYED),
        # Nacional: preventivo a 3, demorado a 5.
        ("Bogotá", "Cali", 2, LEVEL_OK),
        ("Bogotá", "Cali", 3, LEVEL_PREVENTIVE),
        ("Bogotá", "Cali", 4, LEVEL_PREVENTIVE),
        ("Bogotá", "Cali", 5, LEVEL_DELAYED),
        # Rural: +2 sobre el nacional (preventivo 5, demorado 7).
        ("Bogotá", "Leticia", 5, LEVEL_PREVENTIVE),
        ("Bogotá", "Leticia", 6, LEVEL_PREVENTIVE),
        ("Bogotá", "Leticia", 7, LEVEL_DELAYED),
        # Mas de 10 dias habiles: extremo.
        ("Bogotá", "Cali", 10, LEVEL_DELAYED),
        ("Bogotá", "Cali", 11, LEVEL_EXTREME),
    ],
)
def test_delay_thresholds_follow_the_kb_policy(origin, destination, waited_days_from_monday, expected):
    monday = dt(2026, 10, 5)
    # N dias habiles despues del lunes (sin cruzar el fin de semana en los casos chicos).
    now = add_business_days(monday, waited_days_from_monday)

    assessment = assess_delay(_shipment(origin=origin, destination=destination, last_event=monday), now)

    assert assessment.business_days_without_event == waited_days_from_monday
    assert assessment.level == expected


def test_a_delivered_shipment_is_never_delayed():
    shipment = _shipment(status=ShipmentStatus.ENTREGADO, last_event=dt(2026, 9, 1))

    assert assess_delay(shipment, dt(2026, 10, 20)).level == LEVEL_OK


def test_a_shipment_without_events_counts_from_its_creation():
    shipment = _shipment()  # created_at = 2026-09-01, sin eventos

    assert assess_delay(shipment, dt(2026, 10, 20)).level == LEVEL_EXTREME
