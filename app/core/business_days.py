"""Dias habiles (lunes a viernes).

NO incluye festivos: Colombia tiene un calendario de festivos propio y
cambiante, y modelarlo bien (Ley Emiliani, semana santa) es un trabajo
aparte. Con este modulo un lunes festivo cuenta como habil, lo que hace
que los plazos calculados se vean ligeramente mas cortos de lo real.
Cuando se agregue un calendario, este es el unico lugar a cambiar.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def business_days_between(start: datetime | date, end: datetime | date) -> int:
    """Dias habiles transcurridos DESPUES de `start` hasta `end`
    inclusive: de un lunes a un miercoles son 2. Si end <= start, 0."""
    first, last = _as_date(start), _as_date(end)
    if last <= first:
        return 0
    total = 0
    current = first
    while current < last:
        current += timedelta(days=1)
        if current.weekday() < 5:
            total += 1
    return total


def add_business_days(start: datetime, days: int) -> datetime:
    """`start` mas `days` dias habiles, conservando la hora. Un sabado o
    domingo de partida cuenta desde el siguiente lunes."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current
