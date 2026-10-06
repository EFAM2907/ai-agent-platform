"""Seed de datos de dominio para Inter Rapidisimo: clientes, envios y su
historial de eventos.

Dataset pequeno y realista a proposito (6 clientes, 15 envios) -- lo
suficiente para ejercitar get_shipment_status (paso 3) contra casos
variados: entregas normales en distintas etapas, un envio con novedad
declarada, y tres envios "atrasados" (uno urbano, uno nacional -- el
golden example 854321 -- y uno rural) que deberian disparar la
politica de retrasos de
kb_content/inter_rapidisimo/02-politica-retrasos-investigacion.md
cuando se implemente esa logica en un paso posterior.

Las fechas de los eventos se calculan en dias habiles relativos al
momento en que este script corre (no estan hardcodeadas a una fecha
fija) -- asi el golden example sigue siendo valido sin importar cuando
se ejecute el seed.

No es idempotente: correrlo dos veces sobre la misma organizacion
duplica clientes y envios normales, y para el golden example (tracking
"854321") directamente falla con un IntegrityError -- el indice unico
ix_shipments_tracking_number_unique_active existe justo para eso, asi
que un segundo run no pasa desapercibido en silencio. Si hace falta
re-sembrar, borrar las filas de esta organizacion primero.

Uso:
    python -m scripts.seed_inter_rapidisimo_shipments <organization_id>
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.organizations.repository import OrganizationRepository
from app.shipments.models import Customer, Shipment, ShipmentEvent, ShipmentStatus, ServiceType
# Organization.users es un relationship(back_populates=...) hacia User por
# nombre de clase -- sin este import, el registro de mappers de SQLAlchemy
# nunca ve la clase User y falla al resolverla al primer query.
from app.users import models as _users_models  # noqa: F401


def _business_days_ago(n: int, *, from_dt: datetime | None = None) -> datetime:
    """Resta n dias habiles (lunes a viernes) a from_dt (o a ahora, en
    UTC). n=0 devuelve from_dt tal cual -- se usa para eventos de hoy
    mismo, sin exigir que hoy sea dia habil."""
    current = from_dt or datetime.now(timezone.utc)
    remaining = n
    while remaining > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # 0=lunes ... 4=viernes
            remaining -= 1
    return current


_CUSTOMERS = [
    {"full_name": "Camila Restrepo", "document_id": "CC 1017234567", "phone": "+57 300 555 0101", "email": "camila.restrepo@example.com"},
    {"full_name": "Julián Gómez", "document_id": "CC 43876521", "phone": "+57 301 555 0102", "email": "julian.gomez@example.com"},
    {"full_name": "Laura Torres", "document_id": "CC 52901234", "phone": "+57 302 555 0103", "email": "laura.torres@example.com"},
    {"full_name": "Andrés Muñoz", "document_id": "CC 79456123", "phone": "+57 304 555 0104", "email": "andres.munoz@example.com"},
    {"full_name": "Valentina Ríos", "document_id": "CC 1010678234", "phone": "+57 313 555 0105", "email": "valentina.rios@example.com"},
    {"full_name": "Santiago Peña", "document_id": "CC 80234567", "phone": "+57 315 555 0106", "email": "santiago.pena@example.com"},
]

# customer_index referencia una posicion de _CUSTOMERS. Cada evento es
# (event_type, city, notes, dias_habiles_atras) -- el ultimo evento de
# la lista define Shipment.status.
_SHIPMENTS = [
    {
        # Golden example: nacional (Bogota -> Medellin), 5 dias
        # habiles sin movimiento desde el ultimo evento -- justo el
        # umbral de la politica de retrasos para envios nacionales.
        "customer_index": 0,
        "tracking_number": "854321",
        "origin_city": "Bogotá",
        "destination_city": "Medellín",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bogotá", "Paquete admitido en oficina Bogotá", 8),
            (ShipmentStatus.EN_BODEGA_ORIGEN, "Bogotá", "Ingreso a bodega de clasificación Bogotá", 7),
            (ShipmentStatus.EN_TRANSITO, "Honda (Tolima)", "Salió de bodega Bogotá rumbo a Medellín", 5),
        ],
    },
    {
        # Urbano (Medellin -> Medellin), 3 dias habiles sin
        # movimiento -- umbral urbano de la politica de retrasos.
        "customer_index": 1,
        "tracking_number": "112233",
        "origin_city": "Medellín",
        "destination_city": "Medellín",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Medellín", "Paquete admitido en oficina Medellín", 4),
            (ShipmentStatus.EN_BODEGA_ORIGEN, "Medellín", "Ingreso a bodega de clasificación Medellín", 4),
            (ShipmentStatus.EN_REPARTO, "Medellín", "Asignado a mensajero para entrega local", 3),
        ],
    },
    {
        # Rural (Bucaramanga -> zona rural de Santander), 8 dias
        # habiles -- supera el umbral nacional (5) + rural (+2) = 7.
        "customer_index": 4,
        "tracking_number": "998877",
        "origin_city": "Bucaramanga",
        "destination_city": "El Playón (Santander)",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bucaramanga", "Paquete admitido en oficina Bucaramanga", 10),
            (ShipmentStatus.EN_BODEGA_ORIGEN, "Bucaramanga", "Ingreso a bodega de clasificación Bucaramanga", 9),
            (ShipmentStatus.EN_TRANSITO, "Bucaramanga", "Salió hacia zona rural de Santander", 8),
        ],
    },
    {
        "customer_index": 1,
        "tracking_number": "550101",
        "origin_city": "Medellín",
        "destination_city": "Bogotá",
        "service_type": ServiceType.EXPRESS,
        "events": [
            (ShipmentStatus.ADMITIDO, "Medellín", "Paquete admitido en oficina Medellín", 1),
            (ShipmentStatus.EN_TRANSITO, "Medellín", "Salió en servicio express rumbo a Bogotá", 0),
        ],
    },
    {
        "customer_index": 2,
        "tracking_number": "550102",
        "origin_city": "Cali",
        "destination_city": "Cali",
        "service_type": ServiceType.SAME_DAY,
        "events": [
            (ShipmentStatus.ADMITIDO, "Cali", "Paquete admitido en oficina Cali", 0),
            (ShipmentStatus.EN_REPARTO, "Cali", "Asignado a mensajero same day", 0),
            (ShipmentStatus.ENTREGADO, "Cali", "Entregado en portería del edificio", 0),
        ],
    },
    {
        "customer_index": 3,
        "tracking_number": "550103",
        "origin_city": "Barranquilla",
        "destination_city": "Cartagena",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Barranquilla", "Paquete admitido en oficina Barranquilla", 6),
            (ShipmentStatus.EN_TRANSITO, "Barranquilla", "Salió rumbo a Cartagena", 5),
            (ShipmentStatus.EN_BODEGA_DESTINO, "Cartagena", "Llegó a bodega Cartagena", 3),
            (ShipmentStatus.EN_REPARTO, "Cartagena", "Asignado a mensajero para entrega local", 2),
            (ShipmentStatus.ENTREGADO, "Cartagena", "Recibido por el cliente", 2),
        ],
    },
    {
        "customer_index": 5,
        "tracking_number": "550104",
        "origin_city": "Bogotá",
        "destination_city": "Bogotá",
        "service_type": ServiceType.SAME_DAY,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bogotá", "Paquete admitido en oficina Bogotá", 1),
            (ShipmentStatus.EN_REPARTO, "Bogotá", "Asignado a mensajero same day", 1),
            (ShipmentStatus.ENTREGADO, "Bogotá", "Entregado al destinatario", 1),
        ],
    },
    {
        "customer_index": 0,
        "tracking_number": "550105",
        "origin_city": "Bogotá",
        "destination_city": "Cali",
        "service_type": ServiceType.EXPRESS,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bogotá", "Paquete admitido en oficina Bogotá", 3),
            (ShipmentStatus.EN_TRANSITO, "Bogotá", "Salió en servicio express rumbo a Cali", 2),
            (ShipmentStatus.EN_BODEGA_DESTINO, "Cali", "Llegó a bodega Cali, pendiente de reparto", 1),
        ],
    },
    {
        # Novedad declarada explicitamente -- distinto de un retraso
        # silencioso: aca ya hay un evento NOVEDAD registrado, no hace
        # falta calcular dias sin movimiento para saber que algo paso.
        "customer_index": 2,
        "tracking_number": "550106",
        "origin_city": "Cali",
        "destination_city": "Pasto",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Cali", "Paquete admitido en oficina Cali", 4),
            (ShipmentStatus.EN_TRANSITO, "Cali", "Salió rumbo a Pasto", 3),
            (ShipmentStatus.NOVEDAD, "Pasto", "Dirección de entrega incompleta, no se pudo contactar al destinatario", 1),
        ],
    },
    {
        "customer_index": 4,
        "tracking_number": "550107",
        "origin_city": "Bucaramanga",
        "destination_city": "Bogotá",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bucaramanga", "Paquete admitido en oficina Bucaramanga", 2),
            (ShipmentStatus.EN_TRANSITO, "Bucaramanga", "En ruta hacia Bogotá", 1),
        ],
    },
    {
        "customer_index": 3,
        "tracking_number": "550108",
        "origin_city": "Barranquilla",
        "destination_city": "Barranquilla",
        "service_type": ServiceType.SAME_DAY,
        "events": [
            (ShipmentStatus.ADMITIDO, "Barranquilla", "Paquete admitido en oficina Barranquilla", 0),
            (ShipmentStatus.EN_REPARTO, "Barranquilla", "Asignado a mensajero same day", 0),
            (ShipmentStatus.ENTREGADO, "Barranquilla", "Entregado al destinatario", 0),
        ],
    },
    {
        "customer_index": 5,
        "tracking_number": "550109",
        "origin_city": "Bogotá",
        "destination_city": "Medellín",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bogotá", "Paquete admitido en oficina Bogotá", 3),
            (ShipmentStatus.EN_BODEGA_ORIGEN, "Bogotá", "Ingreso a bodega de clasificación Bogotá", 3),
            (ShipmentStatus.EN_TRANSITO, "Bogotá", "Salió rumbo a Medellín", 2),
        ],
    },
    {
        "customer_index": 0,
        "tracking_number": "550110",
        "origin_city": "Bogotá",
        "destination_city": "Bucaramanga",
        "service_type": ServiceType.EXPRESS,
        "events": [
            (ShipmentStatus.ADMITIDO, "Bogotá", "Paquete admitido en oficina Bogotá", 1),
            (ShipmentStatus.EN_TRANSITO, "Bogotá", "Salió en servicio express rumbo a Bucaramanga", 1),
            (ShipmentStatus.EN_BODEGA_DESTINO, "Bucaramanga", "Llegó a bodega Bucaramanga", 0),
            (ShipmentStatus.EN_REPARTO, "Bucaramanga", "Asignado a mensajero para entrega local", 0),
        ],
    },
    {
        "customer_index": 1,
        "tracking_number": "550111",
        "origin_city": "Medellín",
        "destination_city": "Cali",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Medellín", "Paquete admitido, pendiente de despacho", 0),
        ],
    },
    {
        "customer_index": 2,
        "tracking_number": "550112",
        "origin_city": "Cali",
        "destination_city": "Cali",
        "service_type": ServiceType.ESTANDAR,
        "events": [
            (ShipmentStatus.ADMITIDO, "Cali", "Paquete admitido en oficina Cali", 7),
            (ShipmentStatus.EN_REPARTO, "Cali", "Asignado a mensajero para entrega local", 6),
            (ShipmentStatus.ENTREGADO, "Cali", "Entregado al destinatario", 6),
        ],
    },
]


async def seed_inter_rapidisimo_shipments(organization_id: uuid.UUID) -> tuple[int, int]:
    async with SessionLocal() as session:
        org_repository = OrganizationRepository(session)
        organization = await org_repository.get_by_id(organization_id)
        if organization is None:
            raise ValueError(f"Organizacion {organization_id} no encontrada")

        customers = [
            Customer(organization_id=organization_id, **data) for data in _CUSTOMERS
        ]
        session.add_all(customers)
        await session.flush()  # asigna los id antes de referenciarlos abajo

        shipment_count = 0
        for data in _SHIPMENTS:
            shipment = Shipment(
                organization_id=organization_id,
                customer_id=customers[data["customer_index"]].id,
                tracking_number=data["tracking_number"],
                origin_city=data["origin_city"],
                destination_city=data["destination_city"],
                service_type=data["service_type"],
                status=data["events"][-1][0],  # el ultimo evento define el estado actual
            )
            session.add(shipment)
            await session.flush()  # asigna shipment.id antes de crear sus eventos

            for event_type, city, notes, days_ago in data["events"]:
                occurred_at = _business_days_ago(days_ago)
                session.add(
                    ShipmentEvent(
                        shipment_id=shipment.id,
                        organization_id=organization_id,
                        event_type=event_type,
                        city=city,
                        notes=notes,
                        occurred_at=occurred_at,
                    )
                )
            shipment_count += 1

        await session.commit()
        return len(customers), shipment_count


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization_id", type=uuid.UUID)
    args = parser.parse_args()

    customer_count, shipment_count = await seed_inter_rapidisimo_shipments(args.organization_id)
    print(
        f"{customer_count} clientes y {shipment_count} envios (con su historial de "
        f"eventos) creados para la organizacion {args.organization_id}."
    )
    print(
        "Golden example: tracking_number=854321, envio nacional Bogota -> "
        "Medellin, sin movimiento desde hace 5 dias habiles."
    )


if __name__ == "__main__":
    asyncio.run(main())
