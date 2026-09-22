import logging
from unittest.mock import patch
from uuid import uuid4

import pydantic
import pytest

from app.core.config import Settings, settings
from app.core.crypto import encrypt_secret
from app.llm.client import LLMClient
from app.llm.litellmprovider.litellm_provider import LiteLLMProvider
from app.organizations.dependencies import get_tenant_llm_client
from app.organizations.models import Organization


@pytest.mark.asyncio
async def test_uses_decrypted_tenant_virtual_key_when_provisioned():
    organization = Organization(
        id=uuid4(),
        name="Tenant Org",
        litellm_virtual_key=encrypt_secret("sk-tenant-real"),
    )

    with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
        client = await get_tenant_llm_client(organization)

    assert isinstance(client, LLMClient)
    client_class.assert_called_once_with(
        base_url=settings.litellm_base_url.rstrip("/"),
        api_key="sk-tenant-real",
        timeout=LiteLLMProvider._REQUEST_TIMEOUT_SECONDS,
    )


@pytest.mark.asyncio
async def test_falls_back_to_emergency_key_and_warns_when_not_provisioned(caplog):
    organization = Organization(id=uuid4(), name="Unprovisioned Org", litellm_virtual_key=None)

    with caplog.at_level(logging.WARNING, logger="app.organizations.dependencies"):
        with patch("app.llm.litellmprovider.litellm_provider.AsyncOpenAI") as client_class:
            client = await get_tenant_llm_client(organization)

    assert isinstance(client, LLMClient)
    client_class.assert_called_once_with(
        base_url=settings.litellm_base_url.rstrip("/"),
        api_key=settings.litellm_emergency_fallback_key,
        timeout=LiteLLMProvider._REQUEST_TIMEOUT_SECONDS,
    )
    # Nunca la master key: no debe autenticar tráfico real de chat.
    assert client_class.call_args.kwargs["api_key"] != settings.litellm_master_key
    assert str(organization.id) in caplog.text
    assert "sin litellm_virtual_key aprovisionada" in caplog.text


def test_app_fails_to_start_without_emergency_fallback_key():
    """Fail-fast: falta la key de emergencia -> la app no debe arrancar
    (ni, mucho menos, caer en silencio a la master key en el primer
    request). Se construye un Settings nuevo sin leer .env, con todos
    los demás campos requeridos presentes, para aislar el efecto de
    omitir justo litellm_emergency_fallback_key."""
    with pytest.raises(pydantic.ValidationError, match="litellm_emergency_fallback_key"):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            secret_key="test-secret",
            encryption_key="test-encryption-key",
            algorithm="HS256",
            access_token_expire_minutes=30,
        )
