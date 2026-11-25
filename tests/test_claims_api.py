"""API de reclamos: apertura, duplicados, ciclo de vida, permisos y
aislamiento entre organizaciones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.claims.models import Claim
from app.core.security import create_access_token
from app.users.models import UserRole
from tests.factories import create_customer, create_organization, create_shipment, create_user

DESCRIPTION = "El paquete llego con la caja aplastada y el contenido roto"


def _headers(user, org_id):
    token = create_access_token({"sub": str(user.id), "organization_id": str(org_id)})
    return {"Authorization": f"Bearer {token}"}


async def _setup(db_session, role=UserRole.MEMBER, tracking="854321"):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, role=role)
    customer = await create_customer(db_session, org.id, full_name="Camila Restrepo")
    shipment = await create_shipment(db_session, org.id, customer.id, tracking_number=tracking)
    await db_session.commit()
    return org, user, shipment


def _open(tracking="854321", claim_type="dano", description=DESCRIPTION):
    return {"tracking_number": tracking, "claim_type": claim_type, "description": description}


@pytest.mark.asyncio
async def test_open_claim_assigns_a_number_an_sla_and_history(db_session, client):
    org, user, _ = await _setup(db_session)

    # Antes de la peticion, no despues: el servicio calcula el SLA con SU
    # `now`, que es posterior a este. Medir contra un `now` tomado despues de
    # responder daba "3 dias menos unos milisegundos" y el test fallaba los
    # lunes y martes, cuando 3 dias habiles no cruzan un fin de semana.
    before = datetime.now(timezone.utc)
    response = await client.post("/claims/", json=_open(), headers=_headers(user, org.id))

    assert response.status_code == 201
    body = response.json()
    year = datetime.now(timezone.utc).year
    assert body["claim_number"] == f"CLM-{year}-000001"
    assert body["status"] == "abierto"
    assert body["claim_type"] == "dano" and body["rec_code"] == "REC-01"
    assert body["tracking_number"] == "854321" and body["customer_name"] == "Camila Restrepo"
    assert body["sla_overdue"] is False
    assert [h["status"] for h in body["history"]] == ["abierto"]
    sla = datetime.fromisoformat(body["sla_due_at"])
    # REC-01 (dano) = 72 h = 3 dias habiles, pasado el momento de apertura.
    assert timedelta(days=3) <= sla - before <= timedelta(days=6)


@pytest.mark.asyncio
async def test_claim_numbers_are_consecutive_per_organization(db_session, client):
    org, user, _ = await _setup(db_session)
    other_customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, other_customer.id, tracking_number="111222")
    await db_session.commit()
    headers = _headers(user, org.id)

    first = await client.post("/claims/", json=_open(), headers=headers)
    second = await client.post("/claims/", json=_open(tracking="111222", claim_type="perdida"), headers=headers)

    year = datetime.now(timezone.utc).year
    assert first.json()["claim_number"] == f"CLM-{year}-000001"
    assert second.json()["claim_number"] == f"CLM-{year}-000002"


@pytest.mark.asyncio
async def test_a_second_active_claim_of_the_same_type_is_a_conflict_naming_the_first(db_session, client):
    org, user, _ = await _setup(db_session)
    headers = _headers(user, org.id)
    first = await client.post("/claims/", json=_open(), headers=headers)

    duplicate = await client.post("/claims/", json=_open(), headers=headers)
    other_type = await client.post("/claims/", json=_open(claim_type="retraso"), headers=headers)

    assert duplicate.status_code == 409
    assert first.json()["claim_number"] in duplicate.json()["detail"]
    assert other_type.status_code == 201  # otro tipo de reclamo sobre el mismo envio si se puede


@pytest.mark.asyncio
async def test_a_closed_claim_allows_opening_a_new_one_of_the_same_type(db_session, client):
    org, user, _ = await _setup(db_session)
    headers = _headers(user, org.id)
    number = (await client.post("/claims/", json=_open(), headers=headers)).json()["claim_number"]
    await client.patch(f"/claims/{number}/status", json={"new_status": "cerrado"}, headers=headers)

    again = await client.post("/claims/", json=_open(), headers=headers)

    assert again.status_code == 201


@pytest.mark.asyncio
async def test_open_claim_validation_errors(db_session, client):
    org, user, _ = await _setup(db_session)
    headers = _headers(user, org.id)

    bad_type = await client.post("/claims/", json=_open(claim_type="robo"), headers=headers)
    no_shipment = await client.post("/claims/", json=_open(tracking="000000"), headers=headers)
    short = await client.post("/claims/", json=_open(description="roto"), headers=headers)

    assert bad_type.status_code == 400 and "dano" in bad_type.json()["detail"]
    assert no_shipment.status_code == 404
    assert short.status_code == 422


@pytest.mark.asyncio
async def test_claim_lifecycle_and_resolution_rules(db_session, client):
    org, user, _ = await _setup(db_session)
    headers = _headers(user, org.id)
    number = (await client.post("/claims/", json=_open(), headers=headers)).json()["claim_number"]
    url = f"/claims/{number}/status"

    skipped = await client.patch(url, json={"new_status": "resuelto", "note": "ya"}, headers=headers)
    investigating = await client.patch(url, json={"new_status": "en_investigacion"}, headers=headers)
    no_note = await client.patch(url, json={"new_status": "resuelto"}, headers=headers)
    resolved = await client.patch(
        url, json={"new_status": "resuelto", "note": "Se indemnizo el 100% del valor declarado"}, headers=headers
    )
    reopened = await client.patch(url, json={"new_status": "en_investigacion", "note": "No esta conforme"}, headers=headers)
    closed = await client.patch(url, json={"new_status": "cerrado"}, headers=headers)
    after_close = await client.patch(url, json={"new_status": "en_investigacion"}, headers=headers)

    assert skipped.status_code == 400 and "en_investigacion" in skipped.json()["detail"]  # no se salta un paso
    assert investigating.status_code == 200
    assert no_note.status_code == 400  # resolver exige explicar la resolucion
    assert resolved.status_code == 200 and resolved.json()["resolution"].startswith("Se indemnizo")
    assert resolved.json()["resolved_at"] is not None
    assert reopened.json()["status"] == "en_investigacion" and reopened.json()["resolved_at"] is None
    assert closed.json()["closed_at"] is not None
    assert after_close.status_code == 400 and "cerrado" in after_close.json()["detail"]
    history = closed.json()["history"]
    assert [h["status"] for h in history] == [
        "abierto", "en_investigacion", "resuelto", "en_investigacion", "cerrado"
    ]


@pytest.mark.asyncio
async def test_unknown_status_and_unknown_claim(db_session, client):
    org, user, _ = await _setup(db_session)
    headers = _headers(user, org.id)
    number = (await client.post("/claims/", json=_open(), headers=headers)).json()["claim_number"]

    bad = await client.patch(f"/claims/{number}/status", json={"new_status": "volando"}, headers=headers)
    missing = await client.patch("/claims/CLM-2026-999999/status", json={"new_status": "cerrado"}, headers=headers)
    get_missing = await client.get("/claims/CLM-2026-999999", headers=headers)

    assert bad.status_code == 400
    assert missing.status_code == 404 and get_missing.status_code == 404


@pytest.mark.asyncio
async def test_sla_overdue_only_while_the_claim_is_alive(db_session, client):
    org, user, _ = await _setup(db_session)
    headers = _headers(user, org.id)
    number = (await client.post("/claims/", json=_open(), headers=headers)).json()["claim_number"]
    await db_session.execute(
        update(Claim).where(Claim.claim_number == number).values(
            sla_due_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
    )
    await db_session.commit()

    overdue = await client.get(f"/claims/{number}", headers=headers)
    await client.patch(f"/claims/{number}/status", json={"new_status": "cerrado"}, headers=headers)
    closed = await client.get(f"/claims/{number}", headers=headers)

    assert overdue.json()["sla_overdue"] is True
    assert closed.json()["sla_overdue"] is False


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_write(db_session, client):
    org, member, _ = await _setup(db_session)
    viewer = await create_user(db_session, org.id, role=UserRole.VIEWER)
    await db_session.commit()
    number = (await client.post("/claims/", json=_open(), headers=_headers(member, org.id))).json()["claim_number"]
    viewer_headers = _headers(viewer, org.id)

    read = await client.get(f"/claims/{number}", headers=viewer_headers)
    listing = await client.get("/claims/", headers=viewer_headers)
    open_ = await client.post("/claims/", json=_open(claim_type="retraso"), headers=viewer_headers)
    patch = await client.patch(f"/claims/{number}/status", json={"new_status": "cerrado"}, headers=viewer_headers)

    assert read.status_code == 200 and listing.json()["total"] == 1
    assert open_.status_code == 403 and patch.status_code == 403


@pytest.mark.asyncio
async def test_list_filters_by_status_and_tracking_number(db_session, client):
    org, user, _ = await _setup(db_session)
    other_customer = await create_customer(db_session, org.id)
    await create_shipment(db_session, org.id, other_customer.id, tracking_number="777888")
    await db_session.commit()
    headers = _headers(user, org.id)
    first = (await client.post("/claims/", json=_open(), headers=headers)).json()["claim_number"]
    await client.post("/claims/", json=_open(tracking="777888", claim_type="perdida"), headers=headers)
    await client.patch(f"/claims/{first}/status", json={"new_status": "en_investigacion"}, headers=headers)

    investigating = await client.get("/claims/?status=en_investigacion", headers=headers)
    by_tracking = await client.get("/claims/?tracking_number=777888", headers=headers)
    unknown_tracking = await client.get("/claims/?tracking_number=000000", headers=headers)
    bad_status = await client.get("/claims/?status=raro", headers=headers)

    assert [c["claim_number"] for c in investigating.json()["claims"]] == [first]
    assert [c["tracking_number"] for c in by_tracking.json()["claims"]] == ["777888"]
    assert unknown_tracking.json()["total"] == 0
    assert bad_status.status_code == 400


@pytest.mark.asyncio
async def test_claims_are_isolated_between_organizations(db_session, client):
    org_a, user_a, _ = await _setup(db_session)
    org_b, user_b, _ = await _setup(db_session, tracking="854321")  # misma guia en otro tenant
    number = (await client.post("/claims/", json=_open(), headers=_headers(user_a, org_a.id))).json()["claim_number"]

    foreign_get = await client.get(f"/claims/{number}", headers=_headers(user_b, org_b.id))
    foreign_patch = await client.patch(
        f"/claims/{number}/status", json={"new_status": "cerrado"}, headers=_headers(user_b, org_b.id)
    )
    foreign_list = await client.get("/claims/", headers=_headers(user_b, org_b.id))

    assert foreign_get.status_code == 404 and foreign_patch.status_code == 404
    assert foreign_list.json()["total"] == 0
    stored = (await db_session.execute(select(Claim))).scalars().all()
    assert len(stored) == 1 and stored[0].organization_id == org_a.id
