"""Reenvios de las herramientas MCP de clientes, envios, reclamos, base de
conocimiento e integraciones -- misma idea que tests/test_mcp_server.py:
verificar metodo/path/params/json; la logica de negocio esta probada del
lado de la API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_server import server as mcp_server_module

H = {"authorization": "Bearer t"}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setattr(mcp_server_module, "_forwarded_headers", lambda: H)


@pytest.fixture
def mock_request(monkeypatch):
    mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(mcp_server_module, "request", mock)
    return mock


def _sent(mock):
    return [
        (c.args[0], c.args[1], c.kwargs.get("params"), c.kwargs.get("json"))
        for c in mock.await_args_list
    ]


@pytest.mark.asyncio
async def test_whoami(mock_request):
    await mcp_server_module.whoami()

    mock_request.assert_awaited_once_with("GET", "/users/me", headers=H)


@pytest.mark.asyncio
async def test_list_shipments_with_filters_drops_the_empty_ones(mock_request):
    await mcp_server_module.list_shipments(limit=10, status="entregado", city="Cali")

    mock_request.assert_awaited_once_with(
        "GET", "/shipments/", headers=H, params={"limit": 10, "status": "entregado", "city": "Cali"}
    )


@pytest.mark.asyncio
async def test_history_stats_and_delayed(mock_request):
    await mcp_server_module.get_shipment_history(tracking_number="123456")
    await mcp_server_module.shipment_stats()
    await mcp_server_module.list_delayed_shipments(level="extreme", limit=5)

    assert _sent(mock_request) == [
        ("GET", "/shipments/123456/history", None, None),
        ("GET", "/shipments/stats", None, None),
        ("GET", "/shipments/delayed", {"level": "extreme", "limit": 5}, None),
    ]


@pytest.mark.asyncio
async def test_customers(mock_request):
    await mcp_server_module.list_customers(limit=5, search="camila")
    await mcp_server_module.get_customer(customer_id="c1")
    await mcp_server_module.create_customer(full_name="Ana", email="a@x.com")
    await mcp_server_module.update_customer(customer_id="c1", phone="300")

    assert _sent(mock_request) == [
        ("GET", "/shipments/customers", {"limit": 5, "search": "camila"}, None),
        ("GET", "/shipments/customers/c1", None, None),
        ("POST", "/shipments/customers", None, {"full_name": "Ana", "email": "a@x.com"}),
        ("PATCH", "/shipments/customers/c1", None, {"phone": "300"}),
    ]


@pytest.mark.asyncio
async def test_create_shipment_and_notify(mock_request):
    await mcp_server_module.create_shipment(
        customer_id="c1", origin_city="Bogota", destination_city="Cali"
    )
    await mcp_server_module.notify_shipment_customer(
        tracking_number="123456", subject="Hola", message="Tu envio salio"
    )

    assert _sent(mock_request) == [
        (
            "POST", "/shipments/", None,
            {"customer_id": "c1", "origin_city": "Bogota", "destination_city": "Cali", "service_type": "estandar"},
        ),
        ("POST", "/shipments/123456/notify", None, {"subject": "Hola", "message": "Tu envio salio"}),
    ]


@pytest.mark.asyncio
async def test_claims(mock_request):
    await mcp_server_module.open_claim(
        tracking_number="123456", claim_type="dano", description="Llego roto el paquete"
    )
    await mcp_server_module.get_claim(claim_number="CLM-2026-000001")
    await mcp_server_module.list_claims(status="abierto")
    await mcp_server_module.update_claim_status(
        claim_number="CLM-2026-000001", new_status="resuelto", note="Indemnizado"
    )

    assert _sent(mock_request) == [
        (
            "POST", "/claims/", None,
            {"tracking_number": "123456", "claim_type": "dano", "description": "Llego roto el paquete"},
        ),
        ("GET", "/claims/CLM-2026-000001", None, None),
        ("GET", "/claims/", {"limit": 50, "status": "abierto"}, None),
        ("PATCH", "/claims/CLM-2026-000001/status", None, {"new_status": "resuelto", "note": "Indemnizado"}),
    ]


@pytest.mark.asyncio
async def test_knowledge_base_and_integrations(mock_request):
    await mcp_server_module.search_knowledge_base(query="tarifa Leticia", top_k=3)
    await mcp_server_module.list_kb_documents(limit=10)
    await mcp_server_module.gmail_status()
    await mcp_server_module.list_unresolved_emails(limit=5)
    await mcp_server_module.poll_gmail_now()
    await mcp_server_module.drive_status()
    await mcp_server_module.sync_drive()

    assert [(m, p) for m, p, _q, _j in _sent(mock_request)] == [
        ("POST", "/kb/search"), ("GET", "/kb/documents"), ("GET", "/gmail/status"),
        ("GET", "/gmail/unresolved"), ("POST", "/gmail/poll"),
        ("GET", "/drive/status"), ("POST", "/drive/sync"),
    ]
    assert _sent(mock_request)[0][3] == {"query": "tarifa Leticia", "top_k": 3}


@pytest.mark.asyncio
async def test_a_business_error_from_a_new_tool_reaches_the_model_as_a_dict(monkeypatch):
    from mcp_server.client import BackendAPIError

    monkeypatch.setattr(
        mcp_server_module, "request", AsyncMock(side_effect=BackendAPIError(409, "Ya existe un reclamo activo"))
    )

    result = await mcp_server_module.open_claim(
        tracking_number="1", claim_type="dano", description="ya existe uno igual"
    )

    assert result == {"error": "Ya existe un reclamo activo", "status_code": 409}
