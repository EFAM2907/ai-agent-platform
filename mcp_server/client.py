"""Cliente HTTP delgado hacia la API REST principal -- el servidor MCP
no importa SQLAlchemy ni los services/models de la app: cada tool
reenvia la llamada como un request HTTP normal a la misma API que ya
usa el frontend, reusando el JWT que ya trae la llamada MCP (ver
server.py, _forwarded_headers()). Esto evita duplicar logica de acceso
a datos/autorizacion en dos procesos distintos -- ver docs/roadmap.md
seccion 2 para el porque de este diseno ("proxy delgado" en vez de
importar la logica de negocio directo)."""

from __future__ import annotations

import os
from typing import Any

import httpx

# En docker-compose, este servicio habla con el contenedor de la API
# por su nombre de servicio, no por localhost -- ver el nuevo servicio
# mcp-server en docker-compose.yml.
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


class BackendAPIError(Exception):
    """La API principal respondio con un error (4xx/5xx) -- se
    propaga el status code y el detail tal cual, para que la tool le
    devuelva al LLM un mensaje tan claro como el que ya recibe un
    cliente REST normal (los HTTPException de app.users.api /
    app.shipments.api terminan aca)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


async def request(
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    """Un cliente httpx nuevo por request a proposito (no uno
    compartido a nivel de modulo): este servidor corre con
    run_in_thread=True (default de FastMCP) para cada tool call, y un
    AsyncClient de httpx no esta pensado para compartirse entre threads
    distintos sin coordinacion propia -- el costo de abrir/cerrar la
    conexion por request es aceptable para el volumen de este
    proyecto, no para trafico de produccion a gran escala."""
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        response = await client.request(method, path, headers=headers, params=params, json=json)

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise BackendAPIError(response.status_code, str(detail))

    if response.status_code == 204 or not response.content:
        return None
    return response.json()
