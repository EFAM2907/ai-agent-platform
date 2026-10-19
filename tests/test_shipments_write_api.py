"""Escritura y consultas nuevas de envios/clientes: altas con reglas de
negocio, filtros, historial, demoras segun la politica, estadisticas y
aviso al cliente por correo."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.gmail.errors import GmailAPIError, GmailNotConnectedError
from app.main import app
from app.shipments.api import get_gmail_service
from app.shipments.models import ServiceType, ShipmentEvent, ShipmentNotification, ShipmentStatus
from app.users.models import UserRole
from tests.factories import create_customer, create_organization, create_shipment, create_user


def _headers(user, org_id):
    token = create_access_token({"sub": str(user.id), "organization_id": str(org_id)})
    return {"Authorization": f"Bearer {token}"}


async def _org_with_user(db_session, role=UserRole.MEMBER):
    org = await create_organization(db_session, name="Inter Test")
    user = await create_user(db_session, org.id, role=role)
    await db_session.commit()
    return org, user


# --------------------------------------------------------------------------
# Clientes
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_customer_normalizes_and_rejects_duplicates(db_session, client):
    org, user = await _org_with_user(db_session)
    headers = _headers(user, org.id)

    created = await client.post(
        "/shipments/customers",
        json={"full_name": "  Camila Restrepo ", "email": "Camila@Example.com", "document_id": "1020304050"},
        headers=headers,
    )
    same_email = await client.post(
        "/shipments/customers", json={"full_name": "Otra Persona", "email": "camila@example.com"}, headers=headers
    )
    same_document = await client.post(
        "/shipments/customers", json={"full_name": "Otra Persona", "document_id": "1020304050"}, headers=headers
    )
    bad_email = await client.post(
        "/shipments/customers", json={"full_name": "Sin Correo", "email": "no-es-correo"}, headers=headers
    )

    assert created.status_code == 201
    assert created.json()["full_name"] == "Camila Restrepo"
    assert created.json()["email"] == "camila@example.com"  # el correo se guarda en minusculas
    assert same_email.status_code == 409 and "Camila Restrepo" in same_email.json()["detail"]
    assert same_document.status_code == 409
    assert bad_email.status_code == 422


@pytest.mark.asyncio
async def test_same_email_in_another_organization_is_allowed(db_session, client):
    org_a, user_a = await _org_with_user(db_session)
    org_b, user_b = await _org_with_user(db_session)
    payload = {"full_name": "Camila", "email": "camila@example.com"}

    first = await client.post("/shipments/customers", json=payload, headers=_headers(user_a, org_a.id))
    second = await client.post("/shipments/customers", json=payload, headers=_headers(user_b, org_b.id))

    assert first.status_code == 201 and second.status_code == 201


@pytest.mark.asyncio
async def test_update_customer_changes_only_what_is_sent_and_keeps_emails_unique(db_session, client):
    org, user = await _org_with_user(db_session)
    headers = _headers(user, org.id)
    a = (await client.post("/shipments/customers", json={"full_name": "Ana", "email": "ana@x.com", "phone": "300"}, headers=headers)).json()
    b = (await client.post("/shipments/customers", json={"full_name": "Beto", "email": "beto@x.com"}, headers=headers)).json()

    renamed = await client.patch(f"/shipments/customers/{a['id']}", json={"full_name": "Ana Maria"}, headers=headers)
    clash = await client.patch(f"/shipments/customers/{a['id']}", json={"email": "beto@x.com"}, headers=headers)
    keep_own = await client.patch(f"/shipments/customers/{b['id']}", json={"email": "BETO@x.com"}, headers=headers)
    empty = await client.patch(f"/shipments/customers/{a['id']}", json={}, headers=headers)
    missing = await client.patch(
        "/shipments/customers/00000000-0000-0000-0000-000000000000", json={"full_name": "Zed"}, headers=headers
    )

    assert renamed.json()["full_name"] == "Ana Maria"
    assert renamed.json()["email"] == "ana@x.com" and renamed.json()["phone"] == "300"  # lo no enviado no cambia
    assert clash.status_code == 409
    assert keep_own.status_code == 200  # reescribir su propio correo no choca consigo mismo
    assert empty.status_code == 400
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_customer_search_and_detail_with_shipments(db_session, client):
    org, user = await _org_with_user(db_session)
    camila = await create_customer(db_session, org.id, full_name="Camila Restrepo", email="camila@x.com")
    julian = await create_customer(db_session, org.id, full_name="Julián Gómez", email="julian@x.com")
    await create_shipment(db_session, org.id, camila.id, tracking_number="100001")
    await db_session.commit()
    headers = _headers(user, org.id)

    by_name = await client.get("/shipments/customers?search=camila", headers=headers)
    by_email = await client.get("/shipments/customers?search=julian@x", headers=headers)
    wildcard = await client.get("/shipments/customers?search=%25", headers=headers)  # '%' literal, no comodin
    detail = await client.get(f"/shipments/customers/{camila.id}", headers=headers)

    assert [c["full_name"] for c in by_name.json()["customers"]] == ["Camila Restrepo"]
    assert by_email.json()["customers"][0]["id"] == str(julian.id)
    assert wildcard.json()["total_customers"] == 0
    assert [s["tracking_number"] for s in detail.json()["shipments"]] == ["100001"]


@pytest.mark.asyncio
async def test_viewer_cannot_create_or_edit_customers(db_session, client):
    org, viewer = await _org_with_user(db_session, role=UserRole.VIEWER)
    customer = await create_customer(db_session, org.id)
    await db_session.commit()
    headers = _headers(viewer, org.id)

    create = await client.post("/shipments/customers", json={"full_name": "Nuevo Cliente"}, headers=headers)
    edit = await client.patch(f"/shipments/customers/{customer.id}", json={"full_name": "Cambio"}, headers=headers)

    assert create.status_code == 403 and edit.status_code == 403


# --------------------------------------------------------------------------
# Envios
# --------------------------------------------------------------------------
async def _customer(db_session, org):
    customer = await create_customer(db_session, org.id, full_name="Camila Restrepo", email="camila@x.com")
    await db_session.commit()
    return customer


@pytest.mark.asyncio
async def test_create_shipment_generates_a_tracking_number_and_the_first_event(db_session, client):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)

    response = await client.post(
        "/shipments/",
        json={"customer_id": str(customer.id), "origin_city": "Bogotá", "destination_city": "Cali"},
        headers=_headers(user, org.id),
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["tracking_number"]) == 8 and body["tracking_number"].isdigit()
    assert body["status"] == "admitido" and body["service_type"] == "estandar"
    assert body["last_event_city"] == "Bogotá"
    events = (await db_session.execute(select(ShipmentEvent))).scalars().all()
    assert [e.event_type for e in events] == [ShipmentStatus.ADMITIDO]


@pytest.mark.asyncio
async def test_create_shipment_with_an_explicit_number_rejects_duplicates_and_bad_formats(db_session, client):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    headers = _headers(user, org.id)
    base = {"customer_id": str(customer.id), "origin_city": "Bogotá", "destination_city": "Cali"}

    ok = await client.post("/shipments/", json={**base, "tracking_number": "AB-1234"}, headers=headers)
    duplicate = await client.post("/shipments/", json={**base, "tracking_number": "AB-1234"}, headers=headers)
    bad = await client.post("/shipments/", json={**base, "tracking_number": "a b!"}, headers=headers)

    assert ok.status_code == 201 and ok.json()["tracking_number"] == "AB-1234"
    assert duplicate.status_code == 409
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_service_rules_same_day_and_express(db_session, client):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    headers = _headers(user, org.id)

    def body(origin, destination, service):
        return {
            "customer_id": str(customer.id), "origin_city": origin,
            "destination_city": destination, "service_type": service,
        }

    same_day_ok = await client.post("/shipments/", json=body("Bogotá", "bogota", "same_day"), headers=headers)
    same_day_far = await client.post("/shipments/", json=body("Bogotá", "Cali", "same_day"), headers=headers)
    express_rural = await client.post("/shipments/", json=body("Bogotá", "Leticia", "express"), headers=headers)
    standard_rural = await client.post("/shipments/", json=body("Bogotá", "Leticia", "estandar"), headers=headers)
    bad_service = await client.post("/shipments/", json=body("Bogotá", "Cali", "teletransporte"), headers=headers)

    assert same_day_ok.status_code == 201  # ciudades iguales aunque cambie la capitalizacion
    assert same_day_far.status_code == 400 and "misma ciudad" in same_day_far.json()["detail"]
    assert express_rural.status_code == 400 and "Leticia" in express_rural.json()["detail"]
    assert standard_rural.status_code == 201
    assert bad_service.status_code == 400 and "estandar" in bad_service.json()["detail"]


@pytest.mark.asyncio
async def test_create_shipment_requires_a_customer_of_the_same_organization(db_session, client):
    org_a, user_a = await _org_with_user(db_session)
    org_b, _user_b = await _org_with_user(db_session)
    foreign_customer = await _customer(db_session, org_b)

    response = await client.post(
        "/shipments/",
        json={"customer_id": str(foreign_customer.id), "origin_city": "Bogotá", "destination_city": "Cali"},
        headers=_headers(user_a, org_a.id),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_create_shipments(db_session, client):
    org, viewer = await _org_with_user(db_session, role=UserRole.VIEWER)
    customer = await _customer(db_session, org)

    response = await client.post(
        "/shipments/",
        json={"customer_id": str(customer.id), "origin_city": "Bogotá", "destination_city": "Cali"},
        headers=_headers(viewer, org.id),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_shipments_filters(db_session, client):
    org, user = await _org_with_user(db_session)
    camila = await create_customer(db_session, org.id, full_name="Camila Restrepo")
    julian = await create_customer(db_session, org.id, full_name="Julián Gómez")
    await create_shipment(db_session, org.id, camila.id, tracking_number="100001", status=ShipmentStatus.EN_TRANSITO, destination_city="Cali")
    await create_shipment(db_session, org.id, julian.id, tracking_number="100002", status=ShipmentStatus.ENTREGADO, destination_city="Pasto")
    await create_shipment(db_session, org.id, julian.id, tracking_number="200003", status=ShipmentStatus.EN_TRANSITO, service_type=ServiceType.EXPRESS, destination_city="Cali")
    await db_session.commit()
    headers = _headers(user, org.id)

    async def tracking(query):
        body = (await client.get(f"/shipments/?{query}", headers=headers)).json()
        return sorted(s["tracking_number"] for s in body["shipments"]), body["total_shipments"]

    assert await tracking("status=entregado") == (["100002"], 1)
    assert await tracking("service_type=express") == (["200003"], 1)
    assert await tracking("city=cali") == (["100001", "200003"], 2)
    assert await tracking("q=julian") == (["100002", "200003"], 2)
    assert await tracking("q=1000") == (["100001", "100002"], 2)
    assert await tracking(f"customer_id={camila.id}") == (["100001"], 1)
    assert await tracking("status=en_transito&city=cali&q=camila") == (["100001"], 1)
    assert (await client.get("/shipments/?status=volando", headers=headers)).status_code == 400


@pytest.mark.asyncio
async def test_history_returns_the_full_timeline_in_order(db_session, client):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    created = await client.post(
        "/shipments/",
        json={"customer_id": str(customer.id), "origin_city": "Bogotá", "destination_city": "Cali"},
        headers=_headers(user, org.id),
    )
    number = created.json()["tracking_number"]
    await client.patch(f"/shipments/{number}/status", json={"new_status": "en_transito", "city": "Ibagué"}, headers=_headers(user, org.id))

    history = await client.get(f"/shipments/{number}/history", headers=_headers(user, org.id))

    assert history.status_code == 200
    assert [e["event_type"] for e in history.json()["events"]] == ["admitido", "en_transito"]
    assert history.json()["events"][1]["city"] == "Ibagué"
    assert (await client.get("/shipments/000/history", headers=_headers(user, org.id))).status_code == 404


# --------------------------------------------------------------------------
# Demoras y estadisticas
# --------------------------------------------------------------------------
def _days_ago(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest.mark.asyncio
async def test_delayed_endpoint_applies_the_kb_policy(db_session, client):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    # 30 dias de calendario sin eventos: extremo en cualquier zona.
    await create_shipment(db_session, org.id, customer.id, tracking_number="900001", destination_city="Cali", last_event_at=_days_ago(30))
    # Reciente: no aparece.
    await create_shipment(db_session, org.id, customer.id, tracking_number="900002", destination_city="Cali", last_event_at=_days_ago(0))
    # Viejo pero ya entregado: nunca esta demorado.
    await create_shipment(db_session, org.id, customer.id, tracking_number="900003", status=ShipmentStatus.ENTREGADO, destination_city="Cali", last_event_at=_days_ago(30))
    await db_session.commit()
    headers = _headers(user, org.id)

    delayed = await client.get("/shipments/delayed", headers=headers)
    extreme = await client.get("/shipments/delayed?level=extreme", headers=headers)
    bad_level = await client.get("/shipments/delayed?level=raro", headers=headers)

    assert [s["tracking_number"] for s in delayed.json()["shipments"]] == ["900001"]
    item = delayed.json()["shipments"][0]
    assert item["level"] == "extreme" and item["zone"] == "national" and item["delayed_after"] == 5
    assert item["business_days_without_event"] > 10
    assert extreme.json()["returned"] == 1
    assert bad_level.status_code == 422


@pytest.mark.asyncio
async def test_stats_summarize_status_service_and_attention(db_session, client):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    await create_shipment(db_session, org.id, customer.id, tracking_number="900001", status=ShipmentStatus.EN_TRANSITO, last_event_at=_days_ago(30))
    await create_shipment(db_session, org.id, customer.id, tracking_number="900002", status=ShipmentStatus.EN_TRANSITO)
    await create_shipment(db_session, org.id, customer.id, tracking_number="900003", status=ShipmentStatus.ENTREGADO, service_type=ServiceType.EXPRESS)
    await db_session.commit()

    body = (await client.get("/shipments/stats", headers=_headers(user, org.id))).json()

    assert body["total_shipments"] == 3 and body["total_customers"] == 1
    assert body["by_status"] == {"en_transito": 2, "entregado": 1}
    assert body["by_service"] == {"estandar": 2, "express": 1}
    assert body["attention"]["extreme_delay"] == 1
    assert body["attention"]["delayed"] == 0


@pytest.mark.asyncio
async def test_delays_and_stats_never_cross_organizations(db_session, client):
    org_a, user_a = await _org_with_user(db_session)
    org_b, user_b = await _org_with_user(db_session)
    customer = await _customer(db_session, org_a)
    await create_shipment(db_session, org_a.id, customer.id, tracking_number="900001", last_event_at=_days_ago(30))
    await db_session.commit()

    delayed_b = await client.get("/shipments/delayed", headers=_headers(user_b, org_b.id))
    stats_b = await client.get("/shipments/stats", headers=_headers(user_b, org_b.id))

    assert delayed_b.json()["returned"] == 0
    assert stats_b.json()["total_shipments"] == 0


# --------------------------------------------------------------------------
# Avisar al cliente
# --------------------------------------------------------------------------
@pytest.fixture
def fake_gmail():
    service = AsyncMock()
    app.dependency_overrides[get_gmail_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_gmail_service, None)


NOTIFY = {"subject": "Novedad con tu envio", "message": "Tu envio tuvo una novedad y lo reprogramamos."}


@pytest.mark.asyncio
async def test_notify_emails_only_the_customer_on_file_and_records_it(db_session, client, fake_gmail):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    shipment = await create_shipment(db_session, org.id, customer.id, tracking_number="900001")
    await db_session.commit()

    response = await client.post("/shipments/900001/notify", json=NOTIFY, headers=_headers(user, org.id))

    assert response.status_code == 200
    assert response.json()["sent_to"] == "camila@x.com"
    kwargs = fake_gmail.send_email.await_args.kwargs
    assert kwargs["to_email"] == "camila@x.com"
    assert "Guia: 900001" in kwargs["body"] and "Inter Test" in kwargs["body"]
    saved = (await db_session.execute(select(ShipmentNotification))).scalars().one()
    assert saved.shipment_id == shipment.id and saved.sent_by_user_id == user.id


@pytest.mark.asyncio
async def test_notify_has_no_way_to_choose_the_recipient(db_session, client, fake_gmail):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    await create_shipment(db_session, org.id, customer.id, tracking_number="900001")
    await db_session.commit()

    await client.post(
        "/shipments/900001/notify", json={**NOTIFY, "to_email": "victima@evil.com", "to": "x@y.com"},
        headers=_headers(user, org.id),
    )

    assert fake_gmail.send_email.await_args.kwargs["to_email"] == "camila@x.com"


@pytest.mark.asyncio
async def test_notify_failure_modes(db_session, client, fake_gmail):
    org, user = await _org_with_user(db_session)
    with_email = await _customer(db_session, org)
    no_email = await create_customer(db_session, org.id, full_name="Sin Correo")
    await create_shipment(db_session, org.id, with_email.id, tracking_number="900001")
    await create_shipment(db_session, org.id, no_email.id, tracking_number="900002")
    await db_session.commit()
    headers = _headers(user, org.id)

    missing_email = await client.post("/shipments/900002/notify", json=NOTIFY, headers=headers)
    unknown = await client.post("/shipments/000000/notify", json=NOTIFY, headers=headers)
    short = await client.post("/shipments/900001/notify", json={"subject": "ok", "message": "corto"}, headers=headers)
    fake_gmail.send_email.side_effect = GmailNotConnectedError("no conectado")
    not_connected = await client.post("/shipments/900001/notify", json=NOTIFY, headers=headers)
    fake_gmail.send_email.side_effect = GmailAPIError("google caido")
    api_down = await client.post("/shipments/900001/notify", json=NOTIFY, headers=headers)

    assert missing_email.status_code == 409 and "Sin Correo" in missing_email.json()["detail"]
    assert unknown.status_code == 404
    assert short.status_code == 422
    assert not_connected.status_code == 409
    assert api_down.status_code == 502
    # Los intentos fallidos no cuentan como avisos enviados.
    assert (await db_session.execute(select(ShipmentNotification))).scalars().all() == []


@pytest.mark.asyncio
async def test_notify_is_limited_to_three_per_shipment_per_day(db_session, client, fake_gmail):
    org, user = await _org_with_user(db_session)
    customer = await _customer(db_session, org)
    await create_shipment(db_session, org.id, customer.id, tracking_number="900001")
    await db_session.commit()
    headers = _headers(user, org.id)

    results = [
        (await client.post("/shipments/900001/notify", json=NOTIFY, headers=headers)).status_code
        for _ in range(4)
    ]

    assert results == [200, 200, 200, 429]
    assert fake_gmail.send_email.await_count == 3


@pytest.mark.asyncio
async def test_viewer_cannot_notify_customers(db_session, client, fake_gmail):
    org, viewer = await _org_with_user(db_session, role=UserRole.VIEWER)
    customer = await _customer(db_session, org)
    await create_shipment(db_session, org.id, customer.id, tracking_number="900001")
    await db_session.commit()

    response = await client.post("/shipments/900001/notify", json=NOTIFY, headers=_headers(viewer, org.id))

    assert response.status_code == 403
    fake_gmail.send_email.assert_not_awaited()
