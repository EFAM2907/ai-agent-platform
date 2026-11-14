# Dependencias del Proyecto — AI Agent Platform

Documento de referencia con las librerías instaladas, para qué sirve cada una, y cómo se están usando concretamente en el proyecto. Cubre los dominios `organizations`, `users`, `auth`, `chat`, `llm`, `rag`, `shipments`, `claims`, `gmail` y `drive`, más el servidor MCP (`mcp_server/`) y el frontend (`frontend/`).

Última actualización: octubre de 2026, tras cerrar la etapa de RAG, trazabilidad, herramientas MCP e integraciones. Lo que cambió respecto a la versión anterior de este documento:

- **RAG**: búsqueda híbrida (vectorial + palabras clave de Postgres, fusionadas con RRF), filtro de relevancia, índice HNSW, metadatos de origen/versión/acceso por documento, sincronización de fuentes (carpeta local, subida por API, Google Drive) con poda de lo que desaparece, y soporte de Word (`.docx`) además de md/pdf/xlsx. Ver sección 7.
- **Trazabilidad**: una traza por turno de chat guardada en la base de datos, `trace_id`/sesión/usuario/tenant enviados al gateway (Langfuse), valoración 👍/👎 por respuesta y vistas de administración. Ver sección 6.3.
- **Dominio de negocio**: reclamos (`app/claims/`), alta de clientes y envíos, historial, estadísticas, envíos demorados según la política de la KB y aviso por correo al cliente. Ver sección 6.4.
- **MCP**: de 10 a 30 herramientas, filtradas por rol en el chat. Ver sección 6.2.
- **Integraciones**: Gmail (propio, multi-organización) y Google Drive (solo lectura), ambas expuestas por nuestro MCP. Ver sección 13.
- **Frontend**: navegación por vistas (chat, base de conocimiento, integraciones, trazabilidad), markdown real, estado de herramientas en vivo y detalle de cada respuesta. Ver sección 14.

---

## 1. Framework web

### `fastapi`
Framework principal de la API. Define los routers, valida requests/responses con Pydantic, maneja inyección de dependencias (`Depends`), respuestas en streaming (SSE) y genera la documentación interactiva en `/docs`.

**Uso en el proyecto:**
- `APIRouter` en cada dominio (`organizations/api.py`, `users/api.py`, `auth/api.py`, `chat/api.py`, `shipments/api.py`). `shipments` sí tiene router propio (4 endpoints — ver sección 6.1), construido para que el servidor MCP (sección 6.2) tuviera algo consistente que proxyear junto a `users`. `rag` sigue **sin router propio** — se accede únicamente desde el agente de chat vía tools, no como endpoints REST directos.
- `Depends()` para inyectar sesiones de BD, services, y el usuario autenticado (`get_db`, `get_user_service`, `get_current_user`, `get_chat_service`).
- `StreamingResponse` (`media_type="text/event-stream"`) para `POST /chat/messages/stream`: el turno completo del chat sale como Server-Sent Events (`start` → `delta`* → `done`/`error`), no como una respuesta JSON única.
- `CORSMiddleware` restringido a `http://localhost:5173` (dev server de Vite del frontend), no `"*"`.
- `HTTPException` para traducir excepciones internas (`UserAlreadyExistsError`, `InvalidCredentialsError`, `TenantVirtualKeyError`, etc.) a respuestas HTTP con el status code correcto.
- `Query` para parámetros de paginación (`skip`, `limit`) con validación (`le=100`).
- **Inyección y control de acceso robusto (`app/core/dependencies.py`):**
  - `require_owner(current_user)`: privilegios exclusivos de propietario.
  - `require_admin(current_user)`: operaciones de administración a usuarios `ADMIN` u `OWNER`.
  - `require_password_changed(current_user)`: bloquea con 403 las rutas de negocio (hoy: `POST /chat/messages` y `/chat/messages/stream`) mientras la cuenta tenga `must_change_password=True` — nunca se aplica a `/auth/*` (sería una trampa sin salida) ni a rutas self-service sobre la propia cuenta.
  - `verify_same_organization(admin, target_organization_id)`: restringe acciones a la misma organización, con bypass para administradores de plataforma globales (`is_platform_admin`).
  - `can_manage_other_user(actor, target)`: compara niveles de `ROLE_HIERARCHY` para autorizar modificaciones de usuarios.

### `uvicorn`
Servidor ASGI que corre la aplicación FastAPI. Se usa con `--reload` en desarrollo para recargar automáticamente al detectar cambios en el código.

---

## 2. Base de datos y ORM

### `sqlalchemy` (v2.0, sintaxis async)
ORM usado para modelar todas las tablas (`Organization`, `User`, `RefreshToken`, `ChatSession`/`ChatMessage`, `KBArticle`/`KBChunk`, `Customer`/`Shipment`/`ShipmentEvent`) y ejecutar queries con `select()` async.

**Uso en el proyecto:**
- Modelos con `Mapped`/`mapped_column`, UUID como primary key, `relationship()` bidireccional (`Organization`↔`User`, `Shipment`↔`Customer`↔`ShipmentEvent`), `TYPE_CHECKING` para evitar imports circulares.
- `create_async_engine`, `async_sessionmaker` con `expire_on_commit=False`.
- `selectinload()` para precargar relaciones (`Shipment.customer`, `Shipment.events`) — necesario en SQLAlchemy async: acceder a una relación no cargada fuera de un `await` explícito revienta con `MissingGreenlet`.
- Mutación de una relación ya cargada vía `shipment.events.append(event)` (no `ShipmentEvent(shipment_id=...)` + `session.add()`) para que el ORM setee el FK y actualice la colección en memoria a la vez.
- **Transacciones complejas y atómicas:**
  - Creación conjunta de organización y su primer `OWNER` en un único commit (`OrganizationService.create_with_owner`).
  - Actualizar `Shipment.status` y agregar su `ShipmentEvent` correspondiente en la misma transacción (`ShipmentService.update_shipment_status`) — nunca uno sin el otro, para no romper la garantía de que `shipment_events` es la fuente de verdad del historial.
  - Bloqueo de filas (`.with_for_update()`) en `UserRepository.get_by_id` durante la transferencia de propiedad.

### `asyncpg`
Driver de PostgreSQL para SQLAlchemy en modo async.

### `psycopg2-binary`
Driver síncrono de PostgreSQL, usado exclusivamente por Alembic (ver abajo).

### `pgvector`
Extensión de PostgreSQL + tipo `Vector` para SQLAlchemy. Almacena los embeddings de la knowledge base (`KBChunk.embedding`, 768 dimensiones) y permite búsqueda por similitud coseno. La imagen de base de datos del proyecto es `pgvector/pgvector:pg16` (drop-in de `postgres:16` con la extensión ya compilada), no `postgres:16` a secas.

### `alembic`
Herramienta de migraciones. Configurado con el driver síncrono (`psycopg2`) porque Alembic no soporta drivers async de forma nativa en su configuración estándar — `migrations/env.py` reescribe la URL de `postgresql+asyncpg://` a `postgresql+psycopg2://` en tiempo de ejecución.

**Cadena de migraciones actual** (`alembic heads` debe dar un único head): organizations → refresh_tokens → roles/platform admin → users → email único activo → LiteLLM virtual key → RAG (pgvector) → chat → shipments → `must_change_password` → tablas de Gmail (`c9d4e7a1b2f3`) → búsqueda de texto completo en `kb_chunks` (`d2e5f8a1b3c4`) → metadatos de origen/versión/acceso e índice HNSW (`e3f6a9b2c5d7`) → trazas y feedback de chat (`f5b8c1d4e7a9`) → reclamos y notificaciones (`a7d0e3f6b9c1`) → extensión `unaccent` (`b8e1f4a7c0d2`) → conexiones de Drive (`c0a3d6e9f2b4`, head actual).

- **Restricciones complejas aplicadas vía Alembic:**
  - `ix_users_email_unique_active` / `ix_shipments_tracking_number_unique_active`: índices únicos parciales que solo aplican a filas activas (`deleted_at IS NULL`) — permiten reutilizar un email o un tracking number si la fila anterior fue borrada lógicamente.
  - `ix_users_one_active_owner_per_organization`: máximo un `OWNER` activo por organización.
  - Enums de Postgres compartidos entre varias tablas (`shipmentstatus` en `shipments.status` y `shipment_events.event_type`) deben crearse con `postgresql.ENUM(..., create_type=False)`, no `sa.Enum` genérico — de lo contrario `create_table` intenta re-crear el tipo en la segunda tabla que lo usa y falla con `DuplicateObject` (bug real, ya corregido).

---

## 3. Validación de datos

### `pydantic` (v2)
Define los schemas de entrada/salida de cada endpoint.

**Uso en el proyecto:**
- `OrganizationBootstrap`, `OwnershipTransfer`, `UserCreate` (sin `password` — ver sección 6), `UserInviteResponse` (con `temporary_password` en texto plano, una sola vez), `ChatMessageIn`/`ChatMessageOut`/`ChatReplyOut`/`ChatSessionOut`, `TokenPair` (con `must_change_password`), `PasswordChangeRequest`.
- Separación estricta entre schemas de entrada y de salida para nunca exponer credenciales (`UserResponse` nunca incluye `hashed_password`).
- `model_dump(exclude_unset=True)` en updates parciales (PATCH).
- `EmailStr` para validación de formato de email.

### `email-validator`
Dependencia adicional requerida por Pydantic para que `EmailStr` funcione.

### `pydantic-settings`
Configuración vía variables de entorno tipada y validada en `core/config.py` — incluye credenciales de LiteLLM/proveedores, presupuesto por defecto de tenant, clave de cifrado, y falla al arrancar (`fail-fast`) si falta `litellm_emergency_fallback_key`.

### `jsonschema`
Valida la salida estructurada del LLM contra un schema (`LLMRequest.response_schema`) en el loop de reparación de `LLMClient` — si el modelo devuelve JSON inválido o que no cumple el schema, se le pide corregir con el error exacto antes de fallar definitivamente.

### `PyYAML`
Carga los prompts versionados del sistema (`app/llm/prompts/templates/**/v*.yaml`) — cada prompt (`support_chat_agent`, `route_model_complexity`) tiene versiones explícitas para poder comparar resultados de forma reproducible.

---

## 4. Seguridad

### `argon2-cffi`
Hasheo de contraseñas recomendado por OWASP.

**Uso en el proyecto:**
- `hash_password(password)`: hashea antes de guardar en `hashed_password`.
- `verify_password(plain_password, hashed_password)`: compara de forma segura, manejando `VerifyMismatchError`/`InvalidHashError`.
- `generate_temporary_password()`: genera una contraseña temporal aleatoria (`secrets.token_urlsafe(12)`) para cuentas invitadas — ver sección 6.

### `pyjwt`
Genera y decodifica los JWT de sesión.

**Uso en el proyecto:**
- `create_access_token(data)`: firma con `SECRET_KEY` y `HS256`.
- `decode_access_token(token)`: verifica firma y expiración; lanza `InvalidTokenError` si expiró o es inválido.
- Refresh tokens de larga duración (30 días) generados aparte (`secrets.token_urlsafe(64)`), hasheados con SHA-256 antes de persistirse (`app/auth/repository.py`) — nunca se guarda el refresh token en claro.

### `cryptography`
Cifrado simétrico (Fernet) para secretos que se persisten en la base de datos.

**Uso en el proyecto (`app/core/crypto.py`):**
- `encrypt_secret`/`decrypt_secret`: usados exclusivamente para `Organization.litellm_virtual_key` — una virtual key de LiteLLM equivale a una API key real (autentica y gasta contra el budget del tenant), así que nunca queda en texto plano en la base de datos.

---

## 5. Rate limiting

### `redis`
Cliente async de Redis. Backend de un rate limiter de ventana fija (`app/core/rate_limit.py`).

**Uso en el proyecto:**
- `rate_limit`: 100 requests/60s por usuario autenticado, aplicado a nivel de router en `organizations` y `users`.
- `login_rate_limit`: 5 intentos/60s en `POST /auth/login` y `POST /auth/register`.
- `refresh_rate_limit`: 10/60s en `POST /auth/refresh`.
- Fail-open a propósito: si Redis no responde, se loguea un warning y se deja pasar el request — un rate limiter caído no debe tumbar la API entera.

---

## 6. Gateway LLM y agente de chat

### `openai` (SDK)
Cliente HTTP usado para hablar con el gateway **LiteLLM**, no directamente con OpenAI — la app nunca importa un SDK de proveedor (Anthropic, Google) fuera de `app/llm/legacy/` (código muerto, reemplazado). `AsyncOpenAI(base_url=litellm_base_url, api_key=<virtual_key>)` apunta al proxy; la superficie OpenAI-compatible (`/chat/completions`, `/embeddings`) es lo que permite que LiteLLM unifique Gemini, OpenAI y Anthropic detrás de un único contrato.

**Uso en el proyecto (`app/llm/litellmprovider/litellm_provider.py`):**
- `generate()`: no-streaming, con soporte de `tools` (function calling) y reasoning_effort.
- `generate_stream()`: streaming token a token, **también con soporte de `tools`** — los `tool_calls` llegan fragmentados por chunk (id/nombre en el primero, argumentos de a pedazos en los siguientes) y se reensamblan por índice antes de exponerse en `LLMResponse.tool_calls` vía un `StreamUsageCollector`.
- Timeout explícito de 60s en el cliente (antes no había ninguno — el SDK por defecto espera hasta 10 minutos, lo que permitía que un solo request colgara el turno de chat mucho más de lo tolerable si el gateway o el proveedor se colgaban).
- Taxonomía de errores propia (`app/llm/errors.py`): `RateLimitError`, `TimeoutError_`, `ProviderError` (5xx/transporte), `InvalidRequestError` (400 — nunca se reintenta, a diferencia de `ProviderError`), `ContentFilterError`, `InvalidResponseError`.

### LiteLLM (servicio Docker, no una librería Python)
Gateway self-hosted (`ghcr.io/berriai/litellm:v1.83.10-stable`, versión pineada tras un incidente de supply-chain) que centraliza el ruteo/fallback entre proveedores (`litellm_config.yaml`), aplica presupuesto y aislamiento por tenant vía virtual keys, y loguea trazas a Langfuse.

**Modelos configurados hoy:** `gemini-3.6-flash`, `claude-sonnet-5`, y para el chat los dos niveles *temporales* `CHEAP_MODEL = gemini-3.1-flash-lite` / `EXPENSIVE_MODEL = gemini-3.5-flash-lite` (`app/chat/service.py`, a revertir a modelos reales cuando Anthropic/OpenAI tengan crédito, ver sección 9), `claude-haiku-4-5`, `gpt-5.6-luna`, `gemini-embedding`, y `deepseek-chat` (proveedor DeepSeek vía su API, prepago sin free tier, agregado como segundo fallback barato para el par `gemini-3.1-flash-lite`/`gemini-3.5-flash-lite` — el problema que resuelve es el rate-limit por RPM de Gemini, no precio). Fallbacks configurados para `gemini-3.6-flash`, `claude-sonnet-5` (evitando caer primero en `claude-haiku-4-5`, que comparte la misma API key de Anthropic), y el par flash-lite (que cae en su hermano y luego en `deepseek-chat` antes de escalar a proveedores sin crédito).

- **Reintentos: responsabilidad exclusiva de LiteLLM** (`router_settings.num_retries`), no de la app — `LLMClient` ya no reintiene nada por su cuenta. Antes reintentaba también él mismo, lo que bajo rate-limit sostenido significaba `app_retries × litellm_retries` llamadas reales por una sola generación (incidente real: ~170s de espera para una consulta simple).
- Virtual keys por tenant (`app.llm.litellm_admin`): una por organización, aprovisionada en background al registrarse (`BackgroundTasks`), más una de emergencia (budget diario bajo, un solo modelo) para tenants sin la propia todavía, y una separada para `eval_harness/`.

### `app/llm/client.py` (`LLMClient`) — fachada propia, no una librería externa
Único punto de entrada al gateway para el resto de la app. Se ocupa solo de lo que LiteLLM no puede resolver por sí solo: el loop de reparación de salida estructurada (JSON que no cumple el schema pedido se reintenta con el error exacto, hasta 2 veces).

### `app/llm/tools.py` — tool-calling loop, no una librería externa
`run_tool_loop()` (no-streaming) y `run_streaming_tool_loop()` (streaming real, incluso con tools de por medio): ejecutan cada `tool_call` que el LLM pide, le devuelven el resultado, y repiten hasta que responde con texto final o se agota `max_iterations` (`ToolLoopError` si nunca converge). Una excepción de una tool nunca tumba el loop — se convierte en `{"error": ...}` y el LLM decide qué hacer con eso.

### `app/llm/routing.py` (`ModelRouter`) — sin librería externa
Clasifica cada mensaje como barato/caro con una llamada al modelo barato (`route_model_complexity` prompt, salida estructurada) antes de decidir qué modelo usa la respuesta real. Salta esa llamada solo para mensajes estructuralmente triviales (`_is_obviously_trivial`: saludos/cierres muy cortos) — nunca como sustituto del juicio del clasificador sobre contenido real. Ante fallo del clasificador, por defecto usa el modelo caro (fail-safe hacia "más capaz", no hacia "más barato").

### Tools expuestas al agente de chat hoy

Ya no son funciones in-process: las anuncia el servidor MCP (sección 6.2) y cada una reenvía a la API REST con el Bearer token del usuario, así que corre con exactamente las mismas reglas de autorización que llamar la API directo. El chat solo le ofrece al modelo las que el rol del usuario puede usar (`app/chat/tool_access.py`: un viewer ve ~11, un member ~18, un admin las 30 — unos 1.100, 2.200 y 3.100 tokens de esquema por llamada).

- **Usuarios** (ADMIN/OWNER): `create_user`, `list_users`, `get_user`, `update_user`, `delete_user`, `change_user_role`.
- **Envíos y clientes**: `list_shipments` (filtros por estado, servicio, cliente, ciudad y `q`), `get_shipment_status`, `get_shipment_history`, `shipment_stats`, `list_delayed_shipments`, `list_customers` (búsqueda sin tildes), `get_customer` (lectura, cualquier rol); `create_customer`, `update_customer`, `create_shipment`, `update_shipment_status`, `notify_shipment_customer` (cualquier rol excepto VIEWER).
- **Reclamos**: `list_claims`, `get_claim` (lectura); `open_claim`, `update_claim_status` (excepto VIEWER).
- **Base de conocimiento**: `search_knowledge_base` (cualquier rol, respeta el acceso por rol de cada documento); `list_kb_documents` (ADMIN/OWNER).
- **Integraciones** (ADMIN/OWNER): `gmail_status`, `list_unresolved_emails`, `poll_gmail_now`, `drive_status`, `sync_drive`.
- **Contexto**: `whoami`.

### 6.1. API REST de envíos (`app/shipments/api.py`)

Hasta hace poco `app/shipments/` solo existía como tools de chat, sin equivalente REST (a diferencia de `app/users/`, que siempre tuvo los dos caminos). Se agregaron 4 endpoints que reusan `ShipmentService` tal cual — ninguna lógica de negocio nueva, solo una superficie HTTP adicional sobre lo que ya existía:

- `GET /shipments/` y `GET /shipments/customers`: paginados (`skip`/`limit`, `limit` con `le=100`).
- `GET /shipments/{tracking_number}/status`: 404 si el tracking number no existe en la organización del usuario autenticado.
- `PATCH /shipments/{tracking_number}/status`: 403 si el usuario es `VIEWER`, 400 si `new_status` no es un `ShipmentStatus` válido, 404 igual que el anterior.

`/customers` se declara antes de `/{tracking_number}/status` en el router — mismo cuidado defensivo que ya existía en `users/api.py` con `/users/me` antes de `/users/{user_id}`, para que FastAPI no intente matchear `"customers"` como si fuera un tracking number. La serialización (`serialize_customer`, `serialize_shipment_summary`, `serialize_shipment_detail`) vive en `app/shipments/schemas.py` — único consumidor hoy, ver sección 6.2 para por qué el chat ya no necesita su propia copia. Tests en `tests/test_shipments_api.py`.

### 6.2. Servidor MCP propio (`mcp_server/`), y el chat como su cliente (`app/chat/mcp_tools.py`)

Servidor [FastMCP](https://github.com/jlowin/fastmcp) (`transport="streamable-http"`) que expone 30 operaciones (usuarios, clientes, envíos, reclamos, base de conocimiento, Gmail y Drive) por el protocolo MCP estándar — ver `docs/roadmap.md` sección 2 para el por qué. Cada herramienta nueva exige registrarse en `TOOL_MIN_ROLE` (`app/chat/tool_access.py`); `tests/test_tool_access.py` falla si el servidor anuncia una herramienta sin rol mínimo (y una no registrada solo se ofrece a ADMIN/OWNER).

Diseño de **proxy delgado**: cada `@mcp.tool` (`mcp_server/server.py`) reenvía la llamada como un request HTTP normal a la API REST que ya existe (`app.users.api`, `app.shipments.api`), reusando el mismo Bearer token que el cliente MCP ya trae (`get_http_headers()` de `fastmcp.server.dependencies`). Dos ganancias de esa decisión: el contenedor del servicio queda liviano (solo `fastmcp`+`httpx` — sin SQLAlchemy/asyncpg/cryptography, lo más pesado de la imagen de la API principal), y la identidad del usuario cruza el proceso gratis — la API principal valida el token exactamente igual que a cualquier otro cliente, sin lógica de autorización propia duplicada en este servidor. Un error de negocio (401/403/404 de la API) se traduce a `{"error": ..., "status_code": ...}` en vez de tumbar la tool call.

Limitación a propósito: solo soporta `streamable-http`, no `stdio` — `get_http_headers()` necesita un request HTTP real detrás para leer el token, algo que no existe con transporte stdio. Tiene su propio `Dockerfile` y requirements (`mcp_server/requirements.txt`) y servicio en `docker-compose.yml` (`mcp-server`, puerto 8100 — ver sección 9). Tests en `tests/test_mcp_server.py` (mockean `httpx.AsyncClient`/el `request` del cliente, no necesitan la API real corriendo).

**Ya no es un camino adicional opcional: el chat lo usa como su único cliente MCP.** `ChatService` dejó de llamar `app.users.tools`/`app.shipments.tools` in-process (ambos módulos se borraron) — `app.chat.mcp_tools.open_mcp_tools(bearer_token)` abre una sesión `fastmcp.client.Client` contra `settings.mcp_server_url` (env var `MCP_SERVER_URL`, default `http://localhost:8100/mcp`) reenviando el Bearer token del usuario, pide `list_tools()`, y traduce cada `mcp_types.Tool` a un `app.llm.tools.Tool` genérico sin volver a declarar el JSON schema a mano (lo anuncia el propio servidor MCP). `ChatService.send_message`/`send_message_stream` envuelven el `run_tool_loop`/`run_streaming_tool_loop` completo dentro de ese `async with` — una sesión por turno, viva mientras dure el loop de varias vueltas, no una conexión por tool call.

Consecuencia real de este cambio: `mcp-server` pasa de ser opcional a ser una dependencia dura del chat — si no está levantado, ninguna conversación con tools funciona (ni una que no termine llamando ninguna, porque la lista de tools se pide al abrir la sesión). También significa que el `requirements.txt` raíz **ya no está libre de `fastmcp`**: ahora instala `fastmcp-slim` (la variante sin extras de servidor — el cliente no necesita `cyclopts`/`uvicorn`-para-CLI/etc. que sí carga `mcp_server/requirements.txt` del lado del servidor) para poder importar `fastmcp.client`. Se verificó una instalación combinada real contra los pines existentes (`starlette==1.6.0`, `uvicorn==0.52.3`, `pydantic==2.13.4`, etc.): todos satisfacen las cotas mínimas que pide `mcp==2.2.0`/`fastmcp-slim==4.0.10`, así que ninguno de los pines pre-existentes subió de versión — solo se agregaron los paquetes nuevos (`mcp`, `mcp-types`, `Authlib`, `opentelemetry-api`, `py-key-value-aio` y sus dependencias, etc.).

### 6.3. Trazabilidad de cada turno (`app/chat/tracing.py`)

Hasta ahora Langfuse recibía llamadas sueltas del gateway: `tenant_id` y `request_tag` del `LLMRequest` nunca se enviaban. Ahora:

- **`TurnTracer`** junta, mientras el turno ocurre, qué modelo se eligió y por qué (`ModelRouter.decide()`), qué fragmentos de la KB entraron al contexto (id, título, score), cada llamada al modelo (tokens, latencia) y cada herramienta (nombre, argumentos **sin secretos**, éxito, duración, tamaño del resultado — nunca el resultado en sí). Implementa el protocolo `ToolLoopObserver` de `app/llm/tools.py`; un observer que falla nunca tumba el turno.
- Se guarda **una fila por turno** en `chat_turn_traces`, también cuando el turno falla (`status="error"`), junto con la respuesta en una sola transacción. El mensaje del usuario se confirma antes de llamar al modelo (ya no se pierde si el turno falla).
- `trace_id`, `session_id`, `user_id` y `tenant_id` viajan al gateway en `metadata` (`LiteLLMProvider._extra_body`), así Langfuse agrupa las llamadas del turno. Verificado en `LiteLLM_SpendLogs`: las llamadas de un turno comparten el `session_id` del chat.
- Una falla de la búsqueda en la KB (cuota de embeddings) ya no tumba el chat: el turno sigue sin contexto y la traza lo registra.
- API: `GET /chat/messages/{id}/trace` (quien recibió la respuesta y ADMIN/OWNER), `POST /chat/messages/{id}/feedback` (1 / -1 / null), y solo ADMIN/OWNER `GET /chat/traces`, `/chat/traces/stats` y `/chat/traces/{id}`.
- **Límite conocido**: el costo en dólares no se guarda en la traza local (`cost_usd` queda en `null`); LiteLLM lo calcula y se ve en Langfuse. La traza local guarda tokens y latencias.
- Si el cliente corta la conexión a mitad de una respuesta en streaming, el servidor cancela el turno y no guarda ni respuesta ni traza.

### 6.4. Dominio de negocio: clientes, envíos y reclamos

- **Reclamos** (`app/claims/`): número `CLM-AAAA-NNNNNN` consecutivo por organización, tipo (daño, pérdida, retraso, cobro incorrecto, entrega no autorizada — los códigos REC-01..05 de la KB), SLA en días hábiles según el tipo, ciclo `abierto → en_investigacion → resuelto → cerrado` (un resuelto puede reabrirse; cerrado es final; resolver exige explicar la resolución), historial de cada cambio y un solo reclamo activo por envío y tipo. Leer: cualquier rol; escribir: excepto VIEWER.
- **Clientes y envíos**: alta y edición de clientes (correo y documento únicos por organización — el correo es lo que autoriza ver envíos por Gmail), alta de envíos con guía generada, regla de la KB de Same Day solo en la misma ciudad y Express no a zonas rurales, historial completo, filtros, estadísticas y búsquedas sin tildes (`unaccent`).
- **Envíos demorados** (`app/shipments/delays.py`, `app/core/business_days.py`): la política de la KB (urbano 2/3 días hábiles, nacional 3/5, rural +2, más de 10 = extremo) aplicada a los datos reales. No incluye festivos.
- **Aviso al cliente** (`POST /shipments/{guía}/notify`): el destinatario no es un parámetro (siempre el correo del cliente de ese envío), máximo 3 avisos por envío cada 24 horas, cada uno queda guardado.

---

## 7. RAG (Retrieval-Augmented Generation)

Sin librería de orquestación externa (LangChain, LlamaIndex, etc.) — implementado directo sobre `pgvector` + el gateway de embeddings.

**Uso en el proyecto (`app/rag/`):**
- `chunking.py`: ventanas de 150 palabras con 30 de solape para prosa. El chunking adaptativo se evaluó y se pospuso: ninguno de los casos que fallan en la evaluación es un problema de chunking.
- `embeddings.py` (`EmbeddingClient`): un vector de 768 dimensiones por chunk, vía `gemini-embedding` en LiteLLM.
- **Almacenamiento** (`models.py`, migraciones `d2e5f8a1b3c4` y `e3f6a9b2c5d7`): `kb_articles` guarda de dónde salió cada documento (`source_type`: file/upload/drive/manual, `source_ref`), un hash SHA-256 del contenido, con qué modelo de embeddings se indexó, su nivel de acceso (`viewer`/`member`/`admin`/`owner`) y cuándo cambió en la fuente; `(organization_id, title)` es único entre los activos. `kb_chunks` guarda el vector con índice **HNSW** (con `hnsw.iterative_scan` para que el filtro por organización no recorte resultados) y una columna generada `tsvector` (español) con índice GIN. Requiere pgvector ≥ 0.8.
- **Búsqueda híbrida** (`RAGService.retrieve`): búsqueda vectorial + palabras clave de Postgres, fusionadas con Reciprocal Rank Fusion (`fusion.py`). Las palabras clave aportan en códigos, siglas y datos exactos (guías, `OFC-MED-003`, `N-07`) que los embeddings representan peor. Interruptor: `RAG_HYBRID_SEARCH_ENABLED`.
- **Filtro de relevancia** (`RAG_MAX_COSINE_DISTANCE`, 0,35): si el fragmento más cercano está más lejos y ningún fragmento contiene todos los términos de la pregunta, devuelve `[]` — el asistente dice que no tiene la información en vez de citar fuentes irrelevantes. Calibrado para `gemini-embedding-001`; recalibrar si cambia el modelo.
- **Acceso por rol**: cada artículo tiene un nivel mínimo; `retrieve(viewer_role=...)` solo considera los que el rol puede leer.
- **Ingesta idempotente** (`ingest_article`): upsert por título, salta lo que no cambió (hash + modelo de embeddings iguales), calcula los embeddings *antes* de tocar la base. **Sincronización de fuentes** (`sync.py`): indexa lo nuevo, salta lo igual y retira lo que desapareció de la fuente; nunca poda con una fuente vacía ni con un listado truncado.
- **Formatos** (`app/rag/ingestion/`): markdown, PDF (`pypdf`, sin OCR), Excel (`openpyxl`, un chunk por fila) y Word (`python-docx`, tablas como filas con su sección). Un archivo ilegible se salta con un warning.
- **API** (`app/rag/api.py`): `POST /kb/documents` (subida, máximo 10 MB), `GET/PATCH/DELETE /kb/documents`, `GET /kb/stats` (ADMIN/OWNER) y `POST /kb/search` (cualquier rol — es la herramienta MCP `search_knowledge_base`, RAG agéntico acotado: la recuperación automática del turno se mantiene y el modelo puede volver a buscar con otras palabras).
- **Procesos de fondo**: `scripts/ingest_kb_articles.py` (`--force`, `--no-prune`) y `scripts/run_kb_ingestion_scheduler.py`, que ahora también sincroniza la carpeta de Drive de cada organización. Esperan y reintentan ante el límite de cuota de embeddings.
- **Corpus de prueba**: `scripts/generate_sample_kb.py` genera 22 documentos sintéticos (md, pdf, xlsx, docx) en `kb_content/inter_rapidisimo/`, marcados como inventados. Dependencia de generación (`fpdf2`) en `requirements-dev.txt`.
- **Evaluación** (`eval_harness/rag_eval.py`, `datasets/rag_golden.yaml`, 51 preguntas en 4 grupos): `--compare` mide vectorial contra híbrido, con y sin filtro, sin gastar LLM de generación. Con la KB ampliada: recall por respuesta 40% → 100%, recall por título 67% → 94%; el filtro deja pasar 1 de 10 preguntas fuera de la KB sin bloquear ninguna legítima. El eval completo (con juez LLM) mide *fidelidad*, no corrección: un juez de la misma familia que el generador tiende a ser indulgente.
- **Decisión pendiente**: tarifas y oficinas se consultan hoy por RAG; una herramienta determinista (`cotizar_envio`) sería más fiable para datos tabulares exactos.

---

## 8. Testing

**Antes marcado como pendiente en este documento — ya instalado y en uso activo, no pendiente.**

### `pytest` / `pytest-asyncio`
Suite de tests async contra una base de datos Postgres real de pruebas (`ai_agent_platform_test`), no mocks de la capa de datos.

**Uso en el proyecto:**
- `tests/conftest.py`: fixture `db_session` (engine propio por test, transacción que se revierte al final — ningún test deja residuo) y fixture `client` (`httpx.AsyncClient` + `ASGITransport` contra la app real, con `get_db` sobreescrito para compartir la misma sesión/transacción del test).
- `tests/factories.py`: helpers (`create_organization`, `create_user`, `create_customer`, `create_shipment`) para armar estado de dominio sin pasar por HTTP.
- Más de 400 tests (ver `pytest -q`). Cobertura actual: RBAC y aislamiento multi-tenant end-to-end (`test_tenant_isolation.py`), los endpoints REST de usuarios y de envíos (permisos, jerarquía, cross-tenant — `test_shipments_api.py` y los de `users`), el tool-calling loop genérico (streaming y no-streaming), `ModelRouter` (clasificación + heurístico de mensajes triviales), `LiteLLMProvider` (mapeo de errores, reensamblado de tool_calls en streaming), `LLMClient` (ya no reintenta nada), `require_password_changed`, el flujo de invitación de usuarios (`UserService.create`), la app de LiteLLM (admin, provisioning), el pipeline de ingestión multi-formato del RAG (`test_rag_ingestion.py`, `test_rag_service_ingest.py`), el servidor MCP (`test_mcp_server.py`, `test_mcp_new_tools.py` — mockean `httpx.AsyncClient`, no necesitan la API real corriendo), el filtro de herramientas por rol (`test_tool_access.py`), la búsqueda híbrida, el filtro de relevancia y el almacenamiento del RAG (`test_rag_*.py`, `test_kb_api.py`), la trazabilidad (`test_chat_tracing.py`, `test_tool_loop_observer.py`), reclamos y envíos (`test_claims_api.py`, `test_shipments_write_api.py`, `test_business_rules.py`) y Drive (`test_drive.py`, con un Drive falso que sirve archivos reales por los loaders) y el cliente MCP del chat (`test_chat_mcp_tools.py` — mockea `fastmcp.client.Client`, verifica que `open_mcp_tools` traduce `mcp_types.Tool` a `app.llm.tools.Tool` y reenvía el Bearer token).
- `tests/test_user_management_tools.py` y `tests/test_shipment_tools.py` (las tools in-process `app.users.tools`/`app.shipments.tools`) se borraron junto con los módulos que probaban: el chat ya no las llama, quedaron huérfanos (ver sección 6.2).
- Los módulos de test que quedaron huérfanos tras el reemplazo de los providers directos por el gateway LiteLLM (`test_anthropic_provider.py`, `test_gemini*.py`, `test_llmclient.py`, `test_openai_provider.py` — importaban `app.llm.providers`, movido a `app.llm.legacy`) ya se borraron: quedan completamente cubiertos por `test_litellm_provider.py` y `test_llm_client_retries.py`.

### `httpx`
Cliente HTTP async — usado tanto por los tests (`AsyncClient` contra la app) como por `app.llm.litellm_admin` para hablar con el proxy LiteLLM.

---

## 9. Infraestructura (Docker Compose)

- **`db`** (`pgvector/pgvector:pg16`): Postgres de la app, puerto `5433`.
- **`redis`** (`redis:7-alpine`): rate limiting.
- **`litellm`** (`ghcr.io/berriai/litellm:v1.83.10-stable`): gateway LLM, puerto `4000`, config montada desde `litellm_config.yaml`.
- **`litellm-db`** (`postgres:16`): base de datos propia de LiteLLM (virtual keys, spend, teams) — separada de la de la app a propósito, distinto dominio de fallo.
- **`mcp-server`** (build propio, `mcp_server/Dockerfile`): el servidor MCP (sección 6.2), puerto `8100`. No está en el mismo `docker-compose.yml` que la API principal porque esa API no corre en Docker en este proyecto — se levanta aparte con `uvicorn` en el host — así que este contenedor alcanza la API vía `host.docker.internal` (funciona tal cual en Docker Desktop; en Linux nativo sin Docker Desktop hay que pasar `MCP_SERVER_API_BASE_URL` con la IP del host a mano).

**Estado operacional real, no solo de infraestructura:** Anthropic y OpenAI están sin crédito de facturación; Gemini tiene una cuota gratuita diaria muy baja (20 requests/día para `gemini-3.6-flash`) que se agota fácilmente. Por eso `CHEAP_MODEL`/`EXPENSIVE_MODEL` en `app/chat/service.py` son dos `flash-lite` distintos de Gemini (cada uno con su propio cupo) en vez de `gemini-3.6-flash`/`claude-sonnet-5` — marcado explícitamente como `# TEMPORAL`, a revertir cuando haya crédito real. El nivel gratuito de embeddings limita las peticiones por minuto (cada texto de un lote cuenta como una): por eso la ingesta salta lo que no cambió y espera y reintenta ante el límite. Tras cambiar `mcp_server/` hay que reconstruir el contenedor: `docker compose up -d --build mcp-server`.

---

## 10. Resumen de flujos y capacidades actuales

1. **Bootstrap de organización** (`POST /organizations/`): crea `Organization` + su primer `OWNER` de forma atómica, dispara aprovisionamiento de virtual key de LiteLLM en background.
2. **Invitación de usuarios** (`POST /users/`, o la tool `create_user` desde el chat): quien invita **nunca** elige la contraseña — se genera una temporal (`must_change_password=True`). Si la organización tiene Gmail conectado, esa contraseña se manda por correo al usuario nuevo y nunca aparece en la respuesta (`password_sent_by_email=true`, `temporary_password=null`); si no hay Gmail conectado, o el envío falla por lo que sea, se devuelve en texto plano una única vez, como antes. Ya no son "dos caminos que comparten lógica": la tool de chat reenvía a este mismo endpoint vía el servidor MCP (sección 6.2), así que es un solo camino real.
3. **Login / refresh / logout / cambio de contraseña** (`POST /auth/login|refresh|logout|change-password`): JWT de acceso + refresh token de 30 días hasheado; `change-password` es la única ruta que apaga `must_change_password`.
4. **RBAC y aislamiento multi-tenant** en cada operación sobre usuarios/organizaciones: jerarquía de roles (`OWNER > ADMIN > MEMBER > VIEWER`), aislamiento por `organization_id` con bypass explícito solo para `is_platform_admin`.
5. **Transferencia de propiedad** (`POST /organizations/{id}/transfer-ownership`): swap atómico de roles con bloqueo de filas.
6. **Chat con tool-calling, streaming real** (`POST /chat/messages` y `/messages/stream`): cada turno recupera contexto de la KB (RAG) y elige modelo barato/caro en paralelo, arma el prompt versionado, y corre el loop de tools (usuarios + envíos/clientes, servidas por el servidor MCP — sección 6.2) hasta obtener una respuesta final (el modelo solo ve las herramientas que su rol permite, y cada turno deja una traza) — bloqueado con 403 si la cuenta tiene un cambio de contraseña pendiente. Una respuesta vacía del LLM (pasa bajo rate-limit en cascada) se trata como error explícito, no como éxito silencioso.
7. **Gestión de usuarios desde el chat, en lenguaje natural**: invitar, listar, ver, actualizar, eliminar y cambiar de rol — vía el servidor MCP, que reenvía a la API REST con el Bearer token del usuario, así que corre con exactamente las mismas reglas de autorización que llamar la API REST directo.
8. **Consulta y actualización de envíos/clientes desde el chat**: cuántos envíos hay y de qué clientes, estado y último evento de un envío puntual, lista de clientes, y marcar un envío con un nuevo estado (registrando el evento correspondiente) — mismo camino vía MCP que el punto 7.
9. **Evaluación de la calidad del routing** (`eval_harness/model_routing_eval.py` + `datasets/model_routing_golden.yaml`): mide accuracy real del clasificador barato/caro contra un dataset etiquetado a mano, en vez de asumirla.
10. **Evaluación de la calidad del RAG** (`eval_harness/rag_eval.py` + `datasets/rag_golden.yaml`): mide precisión/recall de retrieval y faithfulness de la respuesta generada contra un dataset de preguntas con fuente esperada, mismo principio que el punto anterior aplicado a la KB en vez de al routing.
12. **Reclamos y operación desde el chat**: abrir y seguir reclamos con número y plazo, dar de alta clientes y envíos, ver demorados según la política, estadísticas y avisar al cliente por correo — con confirmación previa para toda acción que cambia datos o contacta a un tercero (regla del prompt v6).
13. **Base de conocimiento administrable**: subir documentos, definir quién puede leer cada uno, probar una búsqueda y sincronizar una carpeta de Google Drive desde la interfaz.
14. **Trazabilidad**: cada respuesta puede abrirse para ver modelo, fuentes, herramientas y tiempos; los administradores ven el listado, las estadísticas y los errores.
11. **Ingestión programada del RAG** (`scripts/run_kb_ingestion_scheduler.py`): corre la ingesta de `kb_content/` para cada organización configurada en `kb_content/ingestion_schedule.yaml`, sin depender de que alguien la invoque a mano — modo `--once` (cron del SO) o bucle continuo.

---

## 11. Scripts operacionales (`scripts/`)

- `seed_inter_rapidisimo_shipments.py`: siembra clientes/envíos/eventos de ejemplo para el tenant Inter Rapidísimo (no idempotente — re-correr duplica o falla por el índice único de tracking number).
- `ingest_kb_articles.py`: indexa `kb_content/` en la KB (RAG). Idempotente, reusable desde otros scripts (ver el siguiente).
- `run_kb_ingestion_scheduler.py`: corre `ingest_kb_articles` periódicamente para los pares `(organization_id, subcarpeta)` de `kb_content/ingestion_schedule.yaml` -- modo `--once` (cron del SO) o bucle continuo, mismo patrón que `run_gmail_poller.py`.
- `generate_sample_kb.py`: genera el corpus sintético de prueba (md, pdf, xlsx, docx) en `kb_content/inter_rapidisimo/`. Requiere `pip install -r requirements-dev.txt`.
- `run_gmail_poller.py`: atiende periódicamente los buzones de Gmail conectados (`--once` para cron).
- `provision_existing_tenant_llm_keys.py`: aprovisiona virtual keys de LiteLLM para tenants que no la tengan.
- `create_emergency_fallback_key.py` / `create_eval_virtual_key.py`: generan las virtual keys especiales (una vez, a mano).
- `reset_platform_data.py`: limpia datos de la plataforma para volver a empezar en desarrollo.
- `verify_tool_calling.py`: prueba manual del tool-calling end-to-end.

---

## 12. Pendientes conocidos

- **Crédito real en Anthropic/OpenAI**, o aceptar que `EXPENSIVE_MODEL` sigue en Gemini hasta entonces (sección 9).
- **Costo en la traza local**: `cost_usd` queda en `null`; el costo real está en Langfuse/LiteLLM. Cerrarlo exige leer el gasto de LiteLLM por `call id` después del turno.
- **Validar con documentos y preguntas reales**: todo el corpus y el dataset de evaluación son sintéticos y los escribimos nosotros. La ventaja medida de la búsqueda híbrida puede ser menor con tráfico real.
- **Tarifas y oficinas por herramienta** en lugar de RAG (ver sección 7).
- **Reranker**: no se justifica todavía (recall por respuesta ya en 100%); reconsiderar si tras ampliar la KB fallan preguntas con el documento entre los primeros 20 resultados.
- **Festivos** en el cálculo de días hábiles (`app/core/business_days.py`).
- **OCR** para PDF escaneados y formatos antiguos (`.doc`, `.xls`).
- **Google Drive en producción**: hay que habilitar la Drive API en el proyecto de Google Cloud, registrar `GOOGLE_DRIVE_REDIRECT_URI` y añadir el scope `drive.readonly` en la pantalla de consentimiento; el flujo OAuth real no se pudo probar sin esas credenciales (el resto está cubierto con un Drive falso).
- **Pagos**: sin integración todavía. Recomendación y alternativas en `docs/roadmap.md` sección 7.
- **Gap de streaming pre-existente**: algunos errores de proveedor en `generate_stream()` no siempre llegan envueltos en `app.llm.errors.LLMError`.
- `ruff`/`mypy`: linting y tipado estático, todavía no instalados.
- **`mcp_server/requirements.txt` sigue separado del `requirements.txt` raíz** (distinto Dockerfile, distinto contenedor) pero ya no es verdad que el raíz esté libre de `fastmcp` — ver sección 6.2: el raíz ahora instala `fastmcp-slim` (variante cliente, sin los extras de servidor que sí carga `mcp_server/requirements.txt` del lado del servidor). Para correr `tests/test_mcp_server.py` en local (que ejercita el lado *servidor*) sigue haciendo falta instalar también `mcp_server/requirements.txt`: `pip install -r requirements.txt -r mcp_server/requirements.txt`.

---

## 13. Integraciones de Google (`app/gmail/`, `app/drive/`)

**Decisión**: se mantiene el código propio en vez de un servidor MCP genérico de terceros. Esos servidores son de un solo usuario y no traen lo que ya construimos: verificación del remitente (SPF/DKIM/DMARC más correo del cliente), idempotencia por correo, topes por remitente y aislamiento por organización con el token cifrado. Se exponen por nuestro MCP (sección 6.2).

- **Gmail**: una conexión por organización, scopes mínimos (`gmail.readonly` y `gmail.send`), poller propio (`scripts/run_gmail_poller.py`), endpoint de correos sin resolver.
- **Google Drive** (`app/drive/`): mismo cliente OAuth de Google (`GoogleOAuth` parametrizado por scopes y URI de retorno), scope `drive.readonly` (la aplicación nunca escribe en Drive), `state` firmado con propósito propio (un `state` de Gmail no sirve en el callback de Drive). Flujo: conectar → elegir carpeta (enlace o id, validado contra Drive) → ver qué cambiaría → sincronizar. Los archivos nativos de Google se exportan (Docs → `.docx`, Sheets → `.xlsx`, Slides → `.pdf`) y pasan por los loaders de la sección 7. Sincronización **incremental**: un archivo con la misma fecha de modificación no se descarga ni se reindexa; un fallo en un archivo no frena a los demás ni retira lo ya indexado; dos archivos con el mismo título se rechazan para que no se pisen y reembeban en cada pasada. Desconectar conserva los documentos ya indexados salvo `remove_documents=true`.

---

## 14. Frontend (`frontend/`)

React 18 + Vite + TypeScript, sin librería de enrutado (una variable de vista en `App.tsx`).

- **`react-markdown` + `remark-gfm`**: respuestas del asistente con listas, tablas, código y enlaces. No interpreta HTML crudo (lo que llegue como `<script>` se muestra como texto) y los enlaces abren en pestaña nueva con `noopener`.
- **Chat**: fuentes como chips, copiar, 👍/👎, detalle de cómo se generó la respuesta, estado de la herramienta en curso ("Consultando envíos…"), botón de detener, campo multilínea y sugerencias según el rol.
- **Vistas de administración** (solo ADMIN/OWNER): base de conocimiento (subida con arrastrar y soltar, nivel de acceso por documento, prueba de búsqueda), integraciones (Gmail y Drive) y trazabilidad (estadísticas, listado, detalle).
- Los errores de validación de FastAPI (una lista de objetos) se muestran como texto legible.
