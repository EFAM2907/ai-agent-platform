import uuid
from datetime import datetime, timezone

from app.organizations.models import Organization
from app.shipments.models import Customer, Shipment, ShipmentEvent, ShipmentStatus, ServiceType
from app.users.models import User, UserRole
from app.core.security import hash_password


async def create_organization(session, name: str = "Test Org") -> Organization:
    org = Organization(name=name, tax_id=str(uuid.uuid4())[:10])
    session.add(org)
    await session.flush()
    return org


async def create_user(
    session,
    organization_id: uuid.UUID,
    role: UserRole = UserRole.MEMBER,
    email: str | None = None,
    must_change_password: bool = False,
) -> User:
    user = User(
        email=email or f"{uuid.uuid4()}@test.com",
        hashed_password=hash_password("Test1234!"),
        full_name="Test User",
        organization_id=organization_id,
        role=role,
        must_change_password=must_change_password,
    )
    session.add(user)
    await session.flush()
    return user


async def create_customer(
    session,
    organization_id: uuid.UUID,
    full_name: str = "Test Customer",
    email: str | None = None,
) -> Customer:
    customer = Customer(organization_id=organization_id, full_name=full_name, email=email)
    session.add(customer)
    await session.flush()
    return customer


async def create_shipment(
    session,
    organization_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    tracking_number: str | None = None,
    status: ShipmentStatus = ShipmentStatus.EN_TRANSITO,
    service_type: ServiceType = ServiceType.ESTANDAR,
    origin_city: str = "Bogotá",
    destination_city: str = "Medellín",
    with_event: bool = True,
) -> Shipment:
    shipment = Shipment(
        organization_id=organization_id,
        customer_id=customer_id,
        tracking_number=tracking_number or str(uuid.uuid4())[:8],
        status=status,
        service_type=service_type,
        origin_city=origin_city,
        destination_city=destination_city,
    )
    session.add(shipment)
    await session.flush()
    if with_event:
        session.add(
            ShipmentEvent(
                shipment_id=shipment.id,
                organization_id=organization_id,
                event_type=status,
                city=origin_city,
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
    return shipment