from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.crypto import decrypt_secret
from app.core.dependencies import get_current_user
from app.llm.errors import TenantVirtualKeyError
from app.organizations.api import get_organization_service
from app.organizations.models import Organization
from app.organizations.service import OrganizationService
from app.users.models import UserRole


@pytest.mark.asyncio
async def test_service_does_not_regenerate_existing_virtual_key():
    organization = Organization(id=uuid4(), name="Existing", litellm_virtual_key="sk-existing")
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=organization)
    session = MagicMock()
    session.commit = AsyncMock()
    service = OrganizationService(repository, session)

    with patch(
        "app.organizations.service.generate_tenant_virtual_key", new_callable=AsyncMock
    ) as generate_key:
        result = await service.provision_llm_key(organization.id)

    assert result is organization
    generate_key.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_encrypts_virtual_key_before_persisting():
    organization = Organization(id=uuid4(), name="New Org", litellm_virtual_key=None)
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=organization)
    session = MagicMock()
    session.commit = AsyncMock()
    service = OrganizationService(repository, session)

    with patch(
        "app.organizations.service.generate_tenant_virtual_key",
        new_callable=AsyncMock,
        return_value="sk-tenant-secret",
    ):
        result = await service.provision_llm_key(organization.id)

    assert result.litellm_virtual_key != "sk-tenant-secret"
    assert decrypt_secret(result.litellm_virtual_key) == "sk-tenant-secret"
    session.commit.assert_awaited_once()


def _organization() -> Organization:
    return Organization(
        id=uuid4(),
        name="Provisioned Org",
        tax_id="test-tax-id",
        plan_type="free",
        created_at=datetime.now(timezone.utc),
        litellm_virtual_key="sk-never-returned",
    )


@pytest.mark.asyncio
async def test_provision_endpoint_allows_owner_and_hides_key(client):
    organization = _organization()
    service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=organization),
        provision_llm_key=AsyncMock(return_value=organization),
    )
    owner = SimpleNamespace(
        organization_id=organization.id,
        is_platform_admin=False,
        role=UserRole.OWNER,
    )

    async def current_user_override():
        return owner

    from app.main import app
    app.dependency_overrides[get_organization_service] = lambda: service
    app.dependency_overrides[get_current_user] = current_user_override
    try:
        response = await client.post(f"/organizations/{organization.id}/provision-llm-key")
    finally:
        app.dependency_overrides.pop(get_organization_service, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert "litellm_virtual_key" not in response.json()
    service.provision_llm_key.assert_awaited_once_with(organization.id)


@pytest.mark.asyncio
async def test_provision_endpoint_rejects_member_but_allows_platform_admin_bypass(client):
    organization = _organization()
    service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=organization),
        provision_llm_key=AsyncMock(return_value=organization),
    )
    member = SimpleNamespace(
        organization_id=organization.id,
        is_platform_admin=False,
        role=UserRole.MEMBER,
    )

    async def member_override():
        return member

    from app.main import app
    app.dependency_overrides[get_organization_service] = lambda: service
    app.dependency_overrides[get_current_user] = member_override
    try:
        forbidden = await client.post(f"/organizations/{organization.id}/provision-llm-key")
    finally:
        app.dependency_overrides.pop(get_organization_service, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert forbidden.status_code == 403
    service.provision_llm_key.assert_not_awaited()

    platform_admin = SimpleNamespace(
        organization_id=uuid4(),
        is_platform_admin=True,
        role=UserRole.MEMBER,
    )

    async def platform_admin_override():
        return platform_admin

    app.dependency_overrides[get_organization_service] = lambda: service
    app.dependency_overrides[get_current_user] = platform_admin_override
    try:
        bypass = await client.post(f"/organizations/{organization.id}/provision-llm-key")
    finally:
        app.dependency_overrides.pop(get_organization_service, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert bypass.status_code == 200
    service.provision_llm_key.assert_awaited_once_with(organization.id)


@pytest.mark.asyncio
async def test_provision_endpoint_returns_503_when_litellm_fails(client):
    organization = _organization()
    service = SimpleNamespace(
        get_by_id=AsyncMock(return_value=organization),
        provision_llm_key=AsyncMock(
            side_effect=TenantVirtualKeyError(
                "LiteLLM no respondio", provider="litellm"
            )
        ),
    )
    owner = SimpleNamespace(
        organization_id=organization.id,
        is_platform_admin=False,
        role=UserRole.OWNER,
    )

    async def current_user_override():
        return owner

    from app.main import app
    app.dependency_overrides[get_organization_service] = lambda: service
    app.dependency_overrides[get_current_user] = current_user_override
    try:
        response = await client.post(f"/organizations/{organization.id}/provision-llm-key")
    finally:
        app.dependency_overrides.pop(get_organization_service, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
