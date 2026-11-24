"""Prueba de humo contra la API desplegada (Cloud Run u otra URL).

Recorre el flujo minimo de punta a punta:

    health -> crear organizacion + dueño -> login -> /users/me -> chat

Pensado para ejecutarse en TU terminal: la contrasena se pide de forma oculta
(getpass) y nunca se imprime ni se guarda. Los tokens tampoco se imprimen.
Solo usa la libreria estandar, no necesita instalar nada.

Uso:
    python scripts/smoke_test_cloud.py https://<url-de-la-api>

Crea una organizacion REAL en la base a la que apunta la URL. Al final ofrece
borrarla (soft delete, solo la puede borrar su dueño).
"""

from __future__ import annotations

import getpass
import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 180  # el primer request tras escalar a cero puede tardar


def call(method: str, base: str, path: str, body=None, token: str | None = None):
    """Devuelve (status, json_o_texto). No lanza por codigos 4xx/5xx."""
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        status = exc.code
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


def short(value, limit=600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print("Uso: python scripts/smoke_test_cloud.py https://<url-de-la-api>")
        return 2
    base = sys.argv[1].rstrip("/")

    step("1) Health")
    status, body = call("GET", base, "/health")
    print(f"HTTP {status}: {short(body)}")
    if status != 200:
        return 1

    step("2) Datos de la organizacion de prueba")
    name = input("Nombre de la organizacion [Prueba Cloud]: ").strip() or "Prueba Cloud"
    tax_id = input("NIT / tax_id [900000001-1]: ").strip() or "900000001-1"
    email = input("Email del dueño: ").strip()
    full_name = input("Nombre completo del dueño [Dueño Prueba]: ").strip() or "Dueño Prueba"
    password = getpass.getpass("Contraseña del dueño (no se muestra): ")
    if not email or not password:
        print("Faltan email o contraseña.")
        return 2

    step("3) Crear organizacion + dueño (POST /organizations/)")
    status, body = call(
        "POST",
        base,
        "/organizations/",
        {
            "name": name,
            "tax_id": tax_id,
            "owner_email": email,
            "owner_password": password,
            "owner_full_name": full_name,
        },
    )
    org_id = None
    if status == 201:
        org_id = body.get("id") if isinstance(body, dict) else None
        print(f"HTTP 201: organizacion creada (id={org_id})")
    elif status == 409:
        print(f"HTTP 409: ya existe ({short(body, 200)}). Se sigue con el login.")
    else:
        print(f"HTTP {status}: {short(body)}")
        return 1

    # La llave virtual de la organizacion se provisiona en segundo plano
    # (BackgroundTasks) tras responder; damos unos segundos.
    time.sleep(8)

    step("4) Login (POST /auth/login)")
    status, body = call("POST", base, "/auth/login", {"email": email, "password": password})
    password = None
    if status != 200 or not isinstance(body, dict):
        print(f"HTTP {status}: {short(body)}")
        return 1
    token = body["access_token"]
    print(f"HTTP 200: sesion iniciada (must_change_password={body.get('must_change_password')})")

    step("5) Quien soy (GET /users/me)")
    status, body = call("GET", base, "/users/me", token=token)
    print(f"HTTP {status}: {short(body, 400)}")

    step("6) Chat (POST /chat/messages) -- ejercita LiteLLM y, si la usa, el MCP")
    question = "Dime quien soy y a que organizacion pertenezco, usando tus herramientas."
    t0 = time.time()
    status, body = call("POST", base, "/chat/messages", {"message": question}, token=token)
    print(f"HTTP {status} en {time.time() - t0:.1f}s")
    if isinstance(body, dict) and "message" in body:
        print("Respuesta:", short(body["message"].get("content", ""), 700))
        print("trace_id:", body.get("trace_id"))
        print("acciones pendientes:", len(body.get("pending_actions") or []))
    else:
        print(short(body))

    step("7) Limpieza (opcional)")
    if org_id and input("¿Borrar la organizacion de prueba? [s/N]: ").strip().lower() == "s":
        status, _ = call("DELETE", base, f"/organizations/{org_id}", token=token)
        print(f"DELETE -> HTTP {status} (204 = borrada)")
    else:
        print("No se borra nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
