"""Tests del servidor MCP (mcp_server/) -- no necesitan la API real
corriendo ni Docker: mcp_server.client.request se prueba mockeando
httpx.AsyncClient, y las 10 tools de mcp_server/server.py se prueban
mockeando esa misma funcion request para verificar que cada una
reenvia el metodo/path/params/json correctos -- el mismo patron de
"no duplicar la API real, solo verificar que se llama bien" que ya
usan tests/test_shipment_tools.py y tests/test_user_invite_rest_email.py
para el resto del proyecto."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server import server as mcp_server_module
from mcp_server.client import BackendAPIError
from mcp_server.client import request as client_request


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_data: Any = None,
        text: str = "",
        content: bytes = b"{}",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.content = content

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("no es JSON valido")
        return self._json_data


class _FakeAsyncClient:
    """Reemplaza httpx.AsyncClient en los tests de mcp_server.client --
    graba la llamada (metodo/path/headers/params/json) para que el
    test la pueda inspeccionar, y devuelve la _FakeResponse que el
    test configuro de antemano."""

    last_call: dict[str, Any] | None = None

    def __init__(self, response: _FakeResponse, **_kwargs: Any) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        _FakeAsyncClient.last_call = {
            "method": method,
            "path": path,
            "headers": headers,
            "params": params,
            "json": json,
        }
        return self._response


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    def _factory(*_args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(response, **kwargs)

    monkeypatch.setattr("mcp_server.client.httpx.AsyncClient", _factory)


class TestClientRequest:
    @pytest.mark.asyncio
    async def test_respuesta_exitosa_devuelve_el_json(self, monkeypatch):
        _patch_async_client(monkeypatch, _FakeResponse(200, json_data={"id": "u1"}))

        result = await client_request(
            "GET", "/users/u1", headers={"authorization": "Bearer t"}
        )

        assert result == {"id": "u1"}
        assert _FakeAsyncClient.last_call == {
            "method": "GET",
            "path": "/users/u1",
            "headers": {"authorization": "Bearer t"},
            "params": None,
            "json": None,
        }

    @pytest.mark.asyncio
    async def test_204_devuelve_none(self, monkeypatch):
        _patch_async_client(monkeypatch, _FakeResponse(204, content=b""))

        result = await client_request("DELETE", "/users/u1", headers={})

        assert result is None

    @pytest.mark.asyncio
    async def test_error_con_detail_json_lanza_backend_api_error(self, monkeypatch):
        _patch_async_client(
            monkeypatch, _FakeResponse(404, json_data={"detail": "Usuario no encontrado"})
        )

        with pytest.raises(BackendAPIError) as exc_info:
            await client_request("GET", "/users/x", headers={})

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Usuario no encontrado"

    @pytest.mark.asyncio
    async def test_error_sin_json_valido_usa_el_texto_plano(self, monkeypatch):
        _patch_async_client(
            monkeypatch, _FakeResponse(500, json_data=None, text="Internal Server Error")
        )

        with pytest.raises(BackendAPIError) as exc_info:
            await client_request("GET", "/users/x", headers={})

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal Server Error"


class TestForwardedHeaders:
    def test_con_authorization_la_devuelve(self, monkeypatch):
        monkeypatch.setattr(
            mcp_server_module,
            "get_http_headers",
            lambda include: {"authorization": "Bearer t"},
        )

        assert mcp_server_module._forwarded_headers() == {"authorization": "Bearer t"}

    def test_sin_authorization_lanza_permission_error(self, monkeypatch):
        # Sin token no hay con que autenticar el request reenviado --
        # esto es un error de configuracion del cliente MCP, no un
        # resultado de negocio (ver el docstring de _call).
        monkeypatch.setattr(mcp_server_module, "get_http_headers", lambda include: {})

        with pytest.raises(PermissionError):
            mcp_server_module._forwarded_headers()


class TestCall:
    @pytest.mark.asyncio
    async def test_exito_devuelve_el_resultado_tal_cual(self, monkeypatch):
        monkeypatch.setattr(
            mcp_server_module, "_forwarded_headers", lambda: {"authorization": "Bearer t"}
        )
        monkeypatch.setattr(mcp_server_module, "request", AsyncMock(return_value={"ok": True}))

        result = await mcp_server_module._call("GET", "/users/")

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_backend_api_error_se_traduce_a_dict(self, monkeypatch):
        monkeypatch.setattr(
            mcp_server_module, "_forwarded_headers", lambda: {"authorization": "Bearer t"}
        )
        monkeypatch.setattr(
            mcp_server_module,
            "request",
            AsyncMock(side_effect=BackendAPIError(403, "No tienes permiso")),
        )

        result = await mcp_server_module._call("DELETE", "/users/u1")

        assert result == {"error": "No tienes permiso", "status_code": 403}

    @pytest.mark.asyncio
    async def test_header_faltante_propaga_permission_error(self, monkeypatch):
        # Distinto del caso anterior: este PermissionError NO lo debe
        # atrapar _call como hace con BackendAPIError, porque no es un
        # resultado de negocio que el LLM pueda leer y explicarle al
        # usuario -- es la tool call mal configurada.
        monkeypatch.setattr(mcp_server_module, "get_http_headers", lambda include: {})

        with pytest.raises(PermissionError):
            await mcp_server_module._call("GET", "/users/")


class TestTools:
    """Cada tool es un reenvio de una linea a la API REST -- lo unico
    que vale la pena probar aca es que cada una arma el metodo/path/
    params/json correctos (la logica de negocio real ya esta probada
    en tests/test_shipments_api.py y tests/test_user_*.py, del lado de
    la API)."""

    @pytest.fixture(autouse=True)
    def _auth(self, monkeypatch):
        monkeypatch.setattr(
            mcp_server_module, "_forwarded_headers", lambda: {"authorization": "Bearer t"}
        )

    @pytest.fixture
    def mock_request(self, monkeypatch):
        mock = AsyncMock(return_value={"ok": True})
        monkeypatch.setattr(mcp_server_module, "request", mock)
        return mock

    @pytest.mark.asyncio
    async def test_create_user(self, mock_request):
        await mcp_server_module.create_user(email="a@b.com", full_name="Ana", role="admin")

        mock_request.assert_awaited_once_with(
            "POST",
            "/users/",
            headers={"authorization": "Bearer t"},
            json={"email": "a@b.com", "full_name": "Ana", "role": "admin"},
        )

    @pytest.mark.asyncio
    async def test_list_users(self, mock_request):
        await mcp_server_module.list_users(skip=10, limit=20)

        mock_request.assert_awaited_once_with(
            "GET",
            "/users/",
            headers={"authorization": "Bearer t"},
            params={"skip": 10, "limit": 20},
        )

    @pytest.mark.asyncio
    async def test_get_user(self, mock_request):
        await mcp_server_module.get_user(user_id="u1")

        mock_request.assert_awaited_once_with(
            "GET", "/users/u1", headers={"authorization": "Bearer t"}
        )

    @pytest.mark.asyncio
    async def test_update_user_solo_manda_los_campos_no_nulos(self, mock_request):
        await mcp_server_module.update_user(user_id="u1", full_name="Nuevo Nombre")

        mock_request.assert_awaited_once_with(
            "PATCH",
            "/users/u1",
            headers={"authorization": "Bearer t"},
            json={"full_name": "Nuevo Nombre"},
        )

    @pytest.mark.asyncio
    async def test_delete_user(self, mock_request):
        await mcp_server_module.delete_user(user_id="u1")

        mock_request.assert_awaited_once_with(
            "DELETE", "/users/u1", headers={"authorization": "Bearer t"}
        )

    @pytest.mark.asyncio
    async def test_change_user_role(self, mock_request):
        await mcp_server_module.change_user_role(user_id="u1", role="admin")

        mock_request.assert_awaited_once_with(
            "PATCH",
            "/users/u1/role",
            headers={"authorization": "Bearer t"},
            json={"role": "admin"},
        )

    @pytest.mark.asyncio
    async def test_list_shipments(self, mock_request):
        await mcp_server_module.list_shipments(limit=5)

        mock_request.assert_awaited_once_with(
            "GET",
            "/shipments/",
            headers={"authorization": "Bearer t"},
            params={"limit": 5},
        )

    @pytest.mark.asyncio
    async def test_get_shipment_status(self, mock_request):
        await mcp_server_module.get_shipment_status(tracking_number="ABC123")

        mock_request.assert_awaited_once_with(
            "GET", "/shipments/ABC123/status", headers={"authorization": "Bearer t"}
        )

    @pytest.mark.asyncio
    async def test_list_customers(self, mock_request):
        await mcp_server_module.list_customers(limit=5)

        mock_request.assert_awaited_once_with(
            "GET",
            "/shipments/customers",
            headers={"authorization": "Bearer t"},
            params={"limit": 5},
        )

    @pytest.mark.asyncio
    async def test_update_shipment_status(self, mock_request):
        await mcp_server_module.update_shipment_status(
            tracking_number="ABC123", new_status="entregado", city="Bogota", notes=None
        )

        mock_request.assert_awaited_once_with(
            "PATCH",
            "/shipments/ABC123/status",
            headers={"authorization": "Bearer t"},
            json={"new_status": "entregado", "city": "Bogota", "notes": None},
        )

    @pytest.mark.asyncio
    async def test_un_error_de_negocio_llega_como_dict_al_llm(self, monkeypatch):
        # No duplica TestCall.test_backend_api_error_se_traduce_a_dict
        # -- esto confirma que una tool concreta (no solo _call en
        # abstracto) tambien devuelve el dict {"error", "status_code"}
        # en vez de dejar propagar la excepcion, que es lo que
        # realmente veria el LLM en un tool result.
        monkeypatch.setattr(
            mcp_server_module,
            "request",
            AsyncMock(side_effect=BackendAPIError(404, "Envio no encontrado")),
        )

        result = await mcp_server_module.get_shipment_status(tracking_number="NOEXISTE")

        assert result == {"error": "Envio no encontrado", "status_code": 404}
