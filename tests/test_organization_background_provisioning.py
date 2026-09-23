"""Tests del aprovisionamiento automático en background al registrar
una organización (POST /organizations/ y POST /auth/register), y del
manejo de errores dentro de ese BackgroundTask."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.rate_limit import login_rate_limit, rate_limit
from app.llm.errors import TenantVirtualKeyError
from app.organizations.repository import OrganizationRepository
from app.organizations.service import OrganizationService, provision_llm_key_in_background


def _bootstrap_payload(**overrides) -> dict:
    payload = {
        "name": "Acme Inc",
        "tax_id": f"tax-{uuid4()}",
        "owner_email": f"owner-{uuid4()}@example.com",
        "owner_password": "supersecret123",
        "owner_full_name": "Owner Persona",
    }
    payload.update(overrides)
    return payload


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_create_organization_schedules_background_provisioning(client, db_session):
    from app.main import app

    payload = _bootstrap_payload()
    app.dependency_overrides[rate_limit] = _noop
    try:
        with patch(
            "app.organizations.service.provision_llm_key_in_background",
            new_callable=AsyncMock,
        ) as background_task:
            response = await client.post("/organizations/", json=payload)
    finally:
        app.dependency_overrides.pop(rate_limit, None)

    assert response.status_code == 201
    organization = await OrganizationRepository(db_session).get_by_tax_id(payload["tax_id"])
    assert organization is not None
    background_task.assert_called_once_with(organization.id)


@pytest.mark.asyncio
async def test_register_schedules_background_provisioning(client, db_session):
    from app.main import app

    payload = _bootstrap_payload()
    app.dependency_overrides[login_rate_limit] = _noop
    try:
        with patch(
            "app.auth.service.provision_llm_key_in_background",
            new_callable=AsyncMock,
        ) as background_task:
            response = await client.post("/auth/register", json=payload)
    finally:
        app.dependency_overrides.pop(login_rate_limit, None)

    assert response.status_code == 201
    organization = await OrganizationRepository(db_session).get_by_tax_id(payload["tax_id"])
    assert organization is not None
    background_task.assert_called_once_with(organization.id)


@pytest.mark.asyncio
async def test_background_task_swallows_tenant_virtual_key_error(caplog):
    organization_id = uuid4()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.organizations.service.SessionLocal", return_value=session_ctx), patch.object(
        OrganizationService,
        "provision_llm_key",
        new_callable=AsyncMock,
        side_effect=TenantVirtualKeyError("LiteLLM no respondio", provider="litellm"),
    ), caplog.at_level(logging.ERROR, logger="app.organizations.service"):
        await provision_llm_key_in_background(organization_id)

    assert str(organization_id) in caplog.text


@pytest.mark.asyncio
async def test_background_task_swallows_unexpected_errors_too(caplog):
    organization_id = uuid4()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.organizations.service.SessionLocal", return_value=session_ctx), patch.object(
        OrganizationService,
        "provision_llm_key",
        new_callable=AsyncMock,
        side_effect=RuntimeError("algo inesperado"),
    ), caplog.at_level(logging.ERROR, logger="app.organizations.service"):
        await provision_llm_key_in_background(organization_id)

    assert str(organization_id) in caplog.text
