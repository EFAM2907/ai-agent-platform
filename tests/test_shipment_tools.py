import pytest

from app.shipments.models import ShipmentStatus
from app.shipments.repository import ShipmentRepository
from app.shipments.service import ShipmentService
from app.shipments.tools import build_shipment_tools
from app.users.models import UserRole
from tests.factories import create_customer, create_organization, create_shipment, create_user


def _tools_by_name(actor, service):
    return {tool.name: tool.handler for tool in build_shipment_tools(actor, service)}


@pytest.mark.asyncio
async def test_list_shipments_returns_customer_name_and_real_total(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer_a = await create_customer(db_session, org.id, full_name="Camila Restrepo")
    customer_b = await create_customer(db_session, org.id, full_name="Julián Gómez")
    await create_shipment(db_session, org.id, customer_a.id, tracking_number="854321")
    await create_shipment(db_session, org.id, customer_b.id, tracking_number="112233")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["list_shipments"]()

    assert result["total_shipments"] == 2
    assert result["returned"] == 2
    customer_names = {s["customer_name"] for s in result["shipments"]}
    assert customer_names == {"Camila Restrepo", "Julián Gómez"}


@pytest.mark.asyncio
async def test_list_shipments_only_returns_own_organization(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    actor_a = await create_user(db_session, org_a.id, role=UserRole.MEMBER)
    customer_a = await create_customer(db_session, org_a.id)
    customer_b = await create_customer(db_session, org_b.id)
    await create_shipment(db_session, org_a.id, customer_a.id, tracking_number="111111")
    await create_shipment(db_session, org_b.id, customer_b.id, tracking_number="222222")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor_a, service)

    result = await tools["list_shipments"]()

    assert result["total_shipments"] == 1
    assert result["shipments"][0]["tracking_number"] == "111111"


@pytest.mark.asyncio
async def test_get_shipment_status_reports_last_event_not_creation_time(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.VIEWER)
    customer = await create_customer(db_session, org.id, full_name="Camila Restrepo")
    shipment = await create_shipment(
        db_session,
        org.id,
        customer.id,
        tracking_number="854321",
        status=ShipmentStatus.EN_TRANSITO,
        origin_city="Honda (Tolima)",
    )
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["get_shipment_status"](tracking_number="854321")

    assert result["status"] == "en_transito"
    assert result["customer"]["full_name"] == "Camila Restrepo"
    assert result["last_event_city"] == "Honda (Tolima)"
    assert result["last_event_at"] is not None


@pytest.mark.asyncio
async def test_get_shipment_status_unknown_tracking_number_returns_error_not_exception(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["get_shipment_status"](tracking_number="no-existe")

    assert "error" in result


@pytest.mark.asyncio
async def test_get_shipment_status_from_another_organization_is_not_found(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    actor_a = await create_user(db_session, org_a.id, role=UserRole.MEMBER)
    customer_b = await create_customer(db_session, org_b.id)
    await create_shipment(db_session, org_b.id, customer_b.id, tracking_number="999999")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor_a, service)

    result = await tools["get_shipment_status"](tracking_number="999999")

    assert "error" in result


@pytest.mark.asyncio
async def test_list_customers_returns_real_total_for_own_organization(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await create_customer(db_session, org.id, full_name="Camila Restrepo")
    await create_customer(db_session, org.id, full_name="Julián Gómez")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["list_customers"]()

    assert result["total_customers"] == 2
    names = {c["full_name"] for c in result["customers"]}
    assert names == {"Camila Restrepo", "Julián Gómez"}


@pytest.mark.asyncio
async def test_update_shipment_status_updates_status_and_appends_event(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(
        db_session,
        org.id,
        customer.id,
        tracking_number="854321",
        status=ShipmentStatus.EN_TRANSITO,
    )
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["update_shipment_status"](
        tracking_number="854321",
        new_status="entregado",
        city="Medellín",
        notes="Recibido por el destinatario",
    )

    assert result["status"] == "entregado"
    assert result["last_event_city"] == "Medellín"
    assert result["last_event_notes"] == "Recibido por el destinatario"

    # El historial queda como fuente de verdad -- ver el docstring de
    # ShipmentEvent -- asi que el evento nuevo debe existir de verdad
    # en la base, no solo reflejado en la respuesta de la tool.
    reloaded = await service.get_by_tracking_number(org.id, "854321")
    assert reloaded.status == ShipmentStatus.ENTREGADO
    assert len(reloaded.events) == 2  # el del seed + el nuevo
    assert reloaded.events[-1].event_type == ShipmentStatus.ENTREGADO


@pytest.mark.asyncio
async def test_update_shipment_status_denied_for_viewer(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.VIEWER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, customer.id, tracking_number="854321")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    with pytest.raises(PermissionError, match="Viewer"):
        await tools["update_shipment_status"](tracking_number="854321", new_status="entregado")


@pytest.mark.asyncio
async def test_update_shipment_status_allowed_for_member(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, customer.id, tracking_number="854321")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["update_shipment_status"](tracking_number="854321", new_status="entregado")

    assert result["status"] == "entregado"


@pytest.mark.asyncio
async def test_update_shipment_status_rejects_invalid_status_without_raising(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, customer.id, tracking_number="854321")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["update_shipment_status"](tracking_number="854321", new_status="volando")

    assert "error" in result


@pytest.mark.asyncio
async def test_update_shipment_status_unknown_tracking_number_returns_error(db_session):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor, service)

    result = await tools["update_shipment_status"](tracking_number="no-existe", new_status="entregado")

    assert "error" in result


@pytest.mark.asyncio
async def test_update_shipment_status_from_another_organization_is_not_found(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    actor_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    customer_b = await create_customer(db_session, org_b.id)
    await create_shipment(db_session, org_b.id, customer_b.id, tracking_number="999999")
    await db_session.commit()

    service = ShipmentService(ShipmentRepository(db_session), db_session)
    tools = _tools_by_name(actor_a, service)

    result = await tools["update_shipment_status"](tracking_number="999999", new_status="entregado")

    assert "error" in result
