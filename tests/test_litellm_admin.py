from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.llm.litellm_admin import (
    EMERGENCY_FALLBACK_KEY_ALIAS,
    EMERGENCY_FALLBACK_KEY_MODELS,
    create_emergency_fallback_key,
    generate_tenant_virtual_key,
)


def _mock_client_returning(key: str) -> tuple[MagicMock, MagicMock]:
    response = MagicMock()
    response.json.return_value = {"key": key}

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=client)
    context_manager.__aexit__ = AsyncMock(return_value=False)
    return client, context_manager


@pytest.mark.asyncio
async def test_generate_tenant_virtual_key_sends_tenant_metadata(monkeypatch):
    organization_id = uuid4()
    client, context_manager = _mock_client_returning("sk-tenant-secret")

    monkeypatch.setattr("app.llm.litellm_admin.settings.litellm_base_url", "http://proxy")
    monkeypatch.setattr("app.llm.litellm_admin.settings.litellm_master_key", "master-key")

    with patch("app.llm.litellm_admin.httpx.AsyncClient", return_value=context_manager):
        key = await generate_tenant_virtual_key(organization_id, 12.5)

    assert key == "sk-tenant-secret"
    client.post.assert_awaited_once_with(
        "/key/generate",
        headers={"Authorization": "Bearer master-key"},
        json={
            "metadata": {"tenant_id": str(organization_id)},
            "max_budget": 12.5,
        },
    )
    client.post.return_value.raise_for_status.assert_called_once_with()


@pytest.mark.asyncio
async def test_create_emergency_fallback_key_is_scoped_to_one_cheap_model(monkeypatch):
    client, context_manager = _mock_client_returning("sk-emergency-secret")

    monkeypatch.setattr("app.llm.litellm_admin.settings.litellm_base_url", "http://proxy")
    monkeypatch.setattr("app.llm.litellm_admin.settings.litellm_master_key", "master-key")
    monkeypatch.setattr("app.llm.litellm_admin.settings.emergency_fallback_daily_budget_usd", 2.0)

    with patch("app.llm.litellm_admin.httpx.AsyncClient", return_value=context_manager):
        key = await create_emergency_fallback_key()

    assert key == "sk-emergency-secret"
    client.post.assert_awaited_once_with(
        "/key/generate",
        headers={"Authorization": "Bearer master-key"},
        json={
            "key_alias": EMERGENCY_FALLBACK_KEY_ALIAS,
            "max_budget": 2.0,
            "budget_duration": "1d",
            "models": EMERGENCY_FALLBACK_KEY_MODELS,
        },
    )
    assert EMERGENCY_FALLBACK_KEY_MODELS == ["gemini-3.6-flash"]
