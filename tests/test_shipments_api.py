"""Tests REST de app.shipments.api -- hasta ahora envios/clientes solo
se probaban por function calling (tests/test_shipment_tools.py); estos
cubren el mismo comportamiento pero por HTTP, con los mismos permisos
(lectura abierta, escritura vetada solo para VIEWER) y el mismo
aislamiento multi-tenant."""

import pytest

from app.core.security import create_access_token
from app.shipments.models import ShipmentStatus
from app.users.models import UserRole
from tests.factories import create_customer, create_organization, create_shipment, create_user


def _auth_headers(user, organization_id) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "organization_id": str(organization_id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_shipments_returns_customer_name_and_real_total(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer_a = await create_customer(db_session, org.id, full_name="Camila Restrepo")
    customer_b = await create_customer(db_session, org.id, full_name="Julián Gómez")
    await create_shipment(db_session, org.id, customer_a.id, tracking_number="854321")
    await create_shipment(db_session, org.id, customer_b.id, tracking_number="112233")
    await db_session.commit()

    response = await client.get("/shipments/", headers=_auth_headers(actor, org.id))

    assert response.status_code == 200
    body = response.json()
    assert body["total_shipments"] == 2
    assert body["returned"] == 2
    names = {s["customer_name"] for s in body["shipments"]}
    assert names == {"Camila Restrepo", "Julián Gómez"}


@pytest.mark.asyncio
async def test_list_shipments_only_returns_own_organization(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    actor_a = await create_user(db_session, org_a.id, role=UserRole.MEMBER)
    customer_a = await create_customer(db_session, org_a.id)
    customer_b = await create_customer(db_session, org_b.id)
    await create_shipment(db_session, org_a.id, customer_a.id, tracking_number="111111")
    await create_shipment(db_session, org_b.id, customer_b.id, tracking_number="222222")
    await db_session.commit()

    response = await client.get("/shipments/", headers=_auth_headers(actor_a, org_a.id))

    assert response.status_code == 200
    body = response.json()
    assert body["total_shipments"] == 1
    assert body["shipments"][0]["tracking_number"] == "111111"


@pytest.mark.asyncio
async def test_get_shipment_status_reports_last_event_not_creation_time(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.VIEWER)
    customer = await create_customer(db_session, org.id, full_name="Camila Restrepo")
    await create_shipment(
        db_session,
        org.id,
        customer.id,
        tracking_number="854321",
        status=ShipmentStatus.EN_TRANSITO,
        origin_city="Honda (Tolima)",
    )
    await db_session.commit()

    response = await client.get("/shipments/854321/status", headers=_auth_headers(actor, org.id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "en_transito"
    assert body["customer"]["full_name"] == "Camila Restrepo"
    assert body["last_event_city"] == "Honda (Tolima)"
    assert body["last_event_at"] is not None


@pytest.mark.asyncio
async def test_get_shipment_status_unknown_tracking_number_is_404(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    response = await client.get("/shipments/no-existe/status", headers=_auth_headers(actor, org.id))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_shipment_status_from_another_organization_is_404(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    actor_a = await create_user(db_session, org_a.id, role=UserRole.MEMBER)
    customer_b = await create_customer(db_session, org_b.id)
    await create_shipment(db_session, org_b.id, customer_b.id, tracking_number="999999")
    await db_session.commit()

    response = await client.get("/shipments/999999/status", headers=_auth_headers(actor_a, org_a.id))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_customers_returns_real_total(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await create_customer(db_session, org.id, full_name="Camila Restrepo")
    await create_customer(db_session, org.id, full_name="Julián Gómez")
    await db_session.commit()

    response = await client.get("/shipments/customers", headers=_auth_headers(actor, org.id))

    assert response.status_code == 200
    body = response.json()
    assert body["total_customers"] == 2
    names = {c["full_name"] for c in body["customers"]}
    assert names == {"Camila Restrepo", "Julián Gómez"}


@pytest.mark.asyncio
async def test_update_shipment_status_updates_status_and_appends_event(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(
        db_session, org.id, customer.id, tracking_number="854321", status=ShipmentStatus.EN_TRANSITO
    )
    await db_session.commit()

    response = await client.patch(
        "/shipments/854321/status",
        json={"new_status": "entregado", "city": "Medellín", "notes": "Recibido por el destinatario"},
        headers=_auth_headers(actor, org.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "entregado"
    assert body["last_event_city"] == "Medellín"
    assert body["last_event_notes"] == "Recibido por el destinatario"


@pytest.mark.asyncio
async def test_update_shipment_status_denied_for_viewer(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.VIEWER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, customer.id, tracking_number="854321")
    await db_session.commit()

    response = await client.patch(
        "/shipments/854321/status",
        json={"new_status": "entregado"},
        headers=_auth_headers(actor, org.id),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_shipment_status_rejects_invalid_status_with_400(db_session, client):
    org = await create_organization(db_session)
    actor = await create_user(db_session, org.id, role=UserRole.MEMBER)
    customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, customer.id, tracking_number="854321")
    await db_session.commit()

    response = await client.patch(
        "/shipments/854321/status",
        json={"new_status": "volando"},
        headers=_auth_headers(actor, org.id),
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_shipment_status_from_another_organization_is_404(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    actor_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    customer_b = await create_customer(db_session, org_b.id)
    await create_shipment(db_session, org_b.id, customer_b.id, tracking_number="999999")
    await db_session.commit()

    response = await client.patch(
        "/shipments/999999/status",
        json={"new_status": "entregado"},
        headers=_auth_headers(actor_a, org_a.id),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_requires_authentication(client):
    response = await client.get("/shipments/")

    assert response.status_code in (401, 403)
